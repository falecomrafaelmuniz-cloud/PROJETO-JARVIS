import os
import json
import queue
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class JarvisBrowserError(RuntimeError):
    pass


class JarvisChrome:
    _PORTS = range(47865, 47885)
    _EXTENSION_WAIT_TIMEOUT = 0.4
    _REQUEST_TIMEOUT = 8.0
    _EXTENSION_POLL_TIMEOUT = 1.5
    _EXTENSION_ALIVE_WINDOW = 4.5

    def __init__(self):
        self.extension_dir = Path(__file__).parent / "jarvis_chrome_extension"
        self._commands = queue.Queue()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._bridge_lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._server = None
        self._server_thread = None
        self._port = None
        self._last_extension_poll = 0.0
        self._ever_connected = False
        self._last_opened_url = ""
        self._last_opened_at = 0.0
        self._active_tab = {"url": "", "title": "", "updated_at": 0.0}
        self._gmail_tab = {"url": "", "title": "", "updated_at": 0.0}
        self._ext_version = ""

    def start_bridge(self) -> None:
        try:
            self._ensure_bridge()
        except Exception:
            pass

    def get_active_tab(self) -> dict[str, str]:
        try:
            self._ensure_bridge()
        except Exception:
            pass
        with self._pending_lock:
            if time.monotonic() - self._active_tab.get("updated_at", 0.0) <= 60.0:
                return {
                    "url": str(self._active_tab.get("url") or ""),
                    "title": str(self._active_tab.get("title") or ""),
                }
        return {"url": "", "title": ""}

    def get_gmail_tab(self) -> dict[str, str]:
        try:
            self._ensure_bridge()
        except Exception:
            pass
        with self._pending_lock:
            if time.monotonic() - self._gmail_tab.get("updated_at", 0.0) <= 120.0:
                url = str(self._gmail_tab.get("url") or "")
                if url:
                    return {
                        "url": url,
                        "title": str(self._gmail_tab.get("title") or ""),
                    }
            if time.monotonic() - self._active_tab.get("updated_at", 0.0) <= 120.0:
                url = str(self._active_tab.get("url") or "")
                if "mail.google.com" in url:
                    return {
                        "url": url,
                        "title": str(self._active_tab.get("title") or ""),
                    }
        return {"url": "", "title": ""}

    def open_url(self, url, nova_aba=True):
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise JarvisBrowserError("Só posso abrir endereços web HTTP ou HTTPS.")

        with self._open_lock:
            now = time.monotonic()
            with self._pending_lock:
                if url == self._last_opened_url and (now - self._last_opened_at) < 2.0:
                    return "jarvis_tab"
                self._last_opened_url = url
                self._last_opened_at = now

            try:
                self._ensure_bridge()
            except JarvisBrowserError:
                return self._open_default_browser(url, nova_aba=nova_aba)

            if not self._wait_for_extension():
                return self._open_default_browser(url, nova_aba=nova_aba)

            # Substitui comandos antigos que ainda não foram despachados na fila para evitar abrir várias URLs em sequência
            while not self._commands.empty():
                try:
                    stale = self._commands.get_nowait()
                    with self._pending_lock:
                        stale_pending = self._pending.pop(stale.get("request_id"), None)
                        if stale_pending is not None:
                            stale_pending["result"] = {"ok": True}
                            stale_pending["event"].set()
                except queue.Empty:
                    break

            request_id = uuid.uuid4().hex
            pending = {
                "event": threading.Event(),
                "dispatched": threading.Event(),
                "result": None,
            }
            with self._pending_lock:
                self._pending[request_id] = pending
                self._last_opened_url = url
                self._last_opened_at = time.monotonic()
                if "mail.google.com" in url:
                    self._gmail_tab = {
                        "url": url,
                        "title": self._gmail_tab.get("title") or "Gmail",
                        "updated_at": time.monotonic(),
                    }

            self._commands.put(
                {
                    "request_id": request_id,
                    "url": url,
                    "nova_aba": bool(nova_aba),
                    "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
                }
            )

            if not pending["event"].wait(2.5):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                if pending["dispatched"].is_set():
                    self._focus_chrome_window()
                    return "jarvis_tab"
                return self._open_default_browser(url, nova_aba=nova_aba)

            result = pending["result"]
            if not result or not result.get("ok"):
                return self._open_default_browser(url, nova_aba=nova_aba)
            self._focus_chrome_window()
            return "jarvis_tab"

    def close_tab(self, target: str = "atual") -> bool:
        with self._open_lock:
            try:
                self._ensure_bridge()
            except JarvisBrowserError:
                return False

            if not self._wait_for_extension():
                return False

            request_id = uuid.uuid4().hex
            pending = {
                "event": threading.Event(),
                "dispatched": threading.Event(),
                "result": None,
            }
            with self._pending_lock:
                self._pending[request_id] = pending

            self._commands.put(
                {
                    "request_id": request_id,
                    "action": "close_tab",
                    "target": target,
                    "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
                }
            )

            if not pending["event"].wait(self._REQUEST_TIMEOUT):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                return False

            result = pending["result"]
            return bool(result and result.get("ok"))

    def minimize_tab_or_window(self, target: str = "atual") -> str:
        with self._open_lock:
            try:
                self._ensure_bridge()
            except JarvisBrowserError:
                return ""

            if not self._wait_for_extension():
                return ""
            if self._ext_version not in ("2", "3"):
                return ""

            request_id = uuid.uuid4().hex
            pending = {
                "event": threading.Event(),
                "dispatched": threading.Event(),
                "result": None,
            }
            with self._pending_lock:
                self._pending[request_id] = pending

            self._commands.put(
                {
                    "request_id": request_id,
                    "action": "minimize_tab",
                    "target": target,
                    "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
                }
            )

            if not pending["event"].wait(self._REQUEST_TIMEOUT):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                return ""

            result = pending["result"]
            if result and result.get("ok") and result.get("mode"):
                return str(result["mode"])
            return ""

    def focus_or_maximize_tab(self, target: str = "atual") -> str:
        with self._open_lock:
            try:
                self._ensure_bridge()
            except JarvisBrowserError:
                return ""

            if not self._wait_for_extension():
                return ""
            if self._ext_version not in ("2", "3"):
                return ""

            request_id = uuid.uuid4().hex
            pending = {
                "event": threading.Event(),
                "dispatched": threading.Event(),
                "result": None,
            }
            with self._pending_lock:
                self._pending[request_id] = pending

            self._commands.put(
                {
                    "request_id": request_id,
                    "action": "focus_tab",
                    "target": target,
                    "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
                }
            )

            if not pending["event"].wait(self._REQUEST_TIMEOUT):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                return ""

            result = pending["result"]
            if result and result.get("ok") and result.get("mode"):
                return str(result["mode"])
            return ""

    def move_tab_to_monitor(
        self,
        target: str,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> dict:
        with self._open_lock:
            try:
                self._ensure_bridge()
            except JarvisBrowserError:
                return {"ok": False}

            if not self._wait_for_extension():
                return {"ok": False}

            ext_v = self._ext_version

        # Se a extensão no Chrome ainda estiver na v2 em memória, destaca a aba usando minimize_tab + focus_tab!
        if ext_v == "2":
            min_mode = self.minimize_tab_or_window(target)
            if min_mode:
                time.sleep(0.15)
                foc_mode = self.focus_or_maximize_tab(target)
                return {"ok": bool(foc_mode or min_mode), "mode": "detached_via_v2"}
            return {"ok": False}

        if ext_v != "3":
            return {"ok": False}

        with self._open_lock:
            request_id = uuid.uuid4().hex
            pending = {
                "event": threading.Event(),
                "dispatched": threading.Event(),
                "result": None,
            }
            with self._pending_lock:
                self._pending[request_id] = pending

            self._commands.put(
                {
                    "request_id": request_id,
                    "action": "move_tab_to_monitor",
                    "target": target,
                    "left": int(left),
                    "top": int(top),
                    "width": int(width),
                    "height": int(height),
                    "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
                }
            )

            if not pending["event"].wait(self._REQUEST_TIMEOUT):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                return {"ok": False}

            result = pending["result"]
            if isinstance(result, dict):
                return result
            return {"ok": False}

    @staticmethod
    def _focus_chrome_window():
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try:
                hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
            except Exception:
                pass
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            preferred_hwnds = []
            fallback_hwnds = []

            def _cb(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").lower()
                if any(b in title for b in ("google chrome", "chrome", "edge", "brave")):
                    if "jarvis - assistente" not in title and "localhost:3000" not in title:
                        preferred_hwnds.append(hwnd)
                    else:
                        fallback_hwnds.append(hwnd)
                return True

            user32.EnumWindows(WNDENUMPROC(_cb), 0)
            target_hwnds = preferred_hwnds or fallback_hwnds
            if target_hwnds:
                hwnd = target_hwnds[0]
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 3)  # SW_SHOWMAXIMIZED
                user32.SwitchToThisWindow(hwnd, True)
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(0x12, 0, 2, 0)  # ALT up
        except Exception:
            pass

    @staticmethod
    def _spawn_on_interactive_desktop(cmd_line: str, show_window: int = 1, creation_flags: int = 0) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.lpDesktop = "Winsta0\\Default"
        si.dwFlags = 1  # STARTF_USESHOWWINDOW
        si.wShowWindow = int(show_window)
        pi = PROCESS_INFORMATION()

        cmd_buf = ctypes.create_unicode_buffer(cmd_line)
        ok = kernel32.CreateProcessW(
            None,
            cmd_buf,
            None,
            None,
            False,
            int(creation_flags),
            None,
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if ok:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
            return True
        return False

    @classmethod
    def _open_default_browser(cls, url, nova_aba=False):
        try:
            candidatos_chrome = [
                os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
            ]
            chrome_exe = next((p for p in candidatos_chrome if p and os.path.isfile(p)), None)
            launched = False
            if chrome_exe:
                launched = cls._spawn_on_interactive_desktop(f'"{chrome_exe}" "{url}"', show_window=1)
            if not launched:
                launched = cls._spawn_on_interactive_desktop(
                    f'cmd.exe /c start "" "{url}"',
                    show_window=0,
                    creation_flags=0x08000000,
                )
            time.sleep(0.35)
            cls._focus_chrome_window()
        except Exception as error:
            raise JarvisBrowserError(
                f"Não foi possível abrir o link no navegador padrão: {error}"
            ) from error
        return "jarvis_tab"

    def _perform_hardware_click(self, x: int, y: int):
        now = time.monotonic()
        with self._pending_lock:
            last_click = getattr(self, "_last_skip_click", 0.0)
            if now - last_click < 0.9:
                return
            self._last_skip_click = now

        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        user32 = ctypes.windll.user32
        orig = POINT()
        user32.GetCursorPos(ctypes.byref(orig))
        user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        user32.SetCursorPos(orig.x, orig.y)

    def _wait_for_extension(self):
        with self._pending_lock:
            if not self._ever_connected:
                return False
        deadline = time.monotonic() + min(self._EXTENSION_WAIT_TIMEOUT, 1.0)
        while time.monotonic() < deadline:
            with self._pending_lock:
                if (
                    self._last_extension_poll > 0.0
                    and time.monotonic() - self._last_extension_poll <= self._EXTENSION_ALIVE_WINDOW
                ):
                    return True
            time.sleep(0.05)
        return False

    def _ensure_bridge(self):
        with self._bridge_lock:
            if self._server is not None:
                return

            handler = self._make_handler()
            last_error = None
            for port in self._PORTS:
                try:
                    self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                    self._server.daemon_threads = True
                    self._port = port
                    break
                except OSError as error:
                    last_error = error

            if self._server is None:
                raise JarvisBrowserError(
                    "Não consegui iniciar a conexão local com a extensão do Chrome "
                    f"nas portas {self._PORTS.start}-{self._PORTS.stop - 1}: {last_error}"
                )

            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="JarvisChromeBridge",
                daemon=True,
            )
            self._server_thread.start()

    def _make_handler(self):
        browser = self

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                if not self._is_extension_request():
                    self.send_error(403)
                    return
                self.send_response(204)
                self._add_cors_headers()
                self.end_headers()

            def do_GET(self):
                if not self._is_extension_request():
                    self.send_error(403)
                    return

                with browser._pending_lock:
                    browser._last_extension_poll = time.monotonic()
                    browser._ever_connected = True

                parsed_req = urlparse(self.path)
                path = parsed_req.path
                if path == "/health":
                    self._send_json(
                        200,
                        {"service": "jarvis-browser-bridge", "version": 1},
                    )
                    return
                if path != "/next":
                    self.send_error(404)
                    return

                qs = parse_qs(parsed_req.query)
                ext_v = (qs.get("v") or [""])[0].strip()
                active_url = (qs.get("active_url") or [""])[0].strip()
                active_title = (qs.get("active_title") or [""])[0].strip()
                gmail_url = (qs.get("gmail_url") or [""])[0].strip()
                gmail_title = (qs.get("gmail_title") or [""])[0].strip()
                now_mono = time.monotonic()
                with browser._pending_lock:
                    if ext_v:
                        browser._ext_version = ext_v
                    if active_url:
                        browser._active_tab = {
                            "url": active_url,
                            "title": active_title,
                            "updated_at": now_mono,
                        }
                        if "mail.google.com" in active_url and not gmail_url:
                            browser._gmail_tab = {
                                "url": active_url,
                                "title": active_title,
                                "updated_at": now_mono,
                            }
                    if gmail_url:
                        browser._gmail_tab = {
                            "url": gmail_url,
                            "title": gmail_title,
                            "updated_at": now_mono,
                        }

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    with browser._pending_lock:
                        browser._last_extension_poll = time.monotonic()
                    try:
                        command = browser._commands.get(
                            timeout=min(
                                browser._EXTENSION_POLL_TIMEOUT,
                                max(0.1, deadline - time.monotonic()),
                            )
                        )
                    except queue.Empty:
                        with browser._pending_lock:
                            browser._last_extension_poll = time.monotonic()
                        continue
                    with browser._pending_lock:
                        browser._last_extension_poll = time.monotonic()
                    if command["expires_at"] > time.monotonic():
                        with browser._pending_lock:
                            pending = browser._pending.get(command["request_id"])
                            if pending is not None:
                                pending["dispatched"].set()
                        payload_cmd = {"request_id": command["request_id"]}
                        for key in ("url", "nova_aba", "action", "target", "left", "top", "width", "height"):
                            if key in command and command[key] is not None:
                                payload_cmd[key] = command[key]
                        self._send_json(200, payload_cmd)
                        return
                with browser._pending_lock:
                    browser._last_extension_poll = time.monotonic()
                self._send_json(200, {})

            def do_POST(self):
                if not self._is_extension_request():
                    self.send_error(403)
                    return
                with browser._pending_lock:
                    browser._last_extension_poll = time.monotonic()
                    browser._ever_connected = True

                path = urlparse(self.path).path
                if path == "/click-skip":
                    try:
                        content_length = int(self.headers.get("Content-Length", "0"))
                        payload = json.loads(self.rfile.read(content_length))
                        x = int(payload["x"])
                        y = int(payload["y"])
                        browser._perform_hardware_click(x, y)
                    except Exception as error:
                        self.send_error(400, str(error))
                        return
                    self.send_response(204)
                    self._add_cors_headers()
                    self.end_headers()
                    return

                if path != "/result":
                    self.send_error(404)
                    return

                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16_384:
                        raise ValueError("tamanho de mensagem inválido")
                    result = json.loads(self.rfile.read(content_length))
                    request_id = result["request_id"]
                    if not isinstance(request_id, str):
                        raise ValueError("identificador de solicitação inválido")
                    if not isinstance(result.get("ok"), bool):
                        raise ValueError("resultado inválido")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    self.send_error(400, str(error))
                    return

                with browser._pending_lock:
                    browser._last_extension_poll = time.monotonic()
                    pending = browser._pending.get(request_id)
                    if pending is not None:
                        pending["result"] = result
                        pending["event"].set()

                if pending is None:
                    self.send_error(404)
                    return
                self.send_response(204)
                self._add_cors_headers()
                self.end_headers()

            def _is_extension_request(self):
                client_ip = self.client_address[0]
                if client_ip in ("127.0.0.1", "::1"):
                    return True
                origin = self.headers.get("Origin", "")
                return origin.startswith("chrome-extension://")

            def _add_cors_headers(self):
                origin = self.headers.get("Origin", "")
                if origin.startswith("chrome-extension://"):
                    self.send_header("Access-Control-Allow-Origin", origin)
                else:
                    self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")

            def _send_json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self._add_cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format_string, *args):
                return

        return BridgeHandler


jarvis_chrome = JarvisChrome()