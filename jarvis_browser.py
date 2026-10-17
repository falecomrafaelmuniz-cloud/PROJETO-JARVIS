import os
import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class JarvisBrowserError(RuntimeError):
    pass


class JarvisChrome:
    _PORTS = range(47865, 47885)
    _EXTENSION_WAIT_TIMEOUT = 0.8
    _REQUEST_TIMEOUT = 3
    _EXTENSION_POLL_TIMEOUT = 1.5

    def __init__(self):
        self.extension_dir = Path(__file__).parent / "jarvis_chrome_extension"
        self._commands = queue.Queue()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._bridge_lock = threading.Lock()
        self._server = None
        self._server_thread = None
        self._port = None
        self._last_extension_poll = 0.0

    def open_url(self, url):
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise JarvisBrowserError("Só posso abrir endereços web HTTP ou HTTPS.")

        try:
            self._ensure_bridge()
        except JarvisBrowserError:
            return self._open_default_browser(url)

        if not self._wait_for_extension():
            return self._open_default_browser(url)

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
                "url": url,
                "expires_at": time.monotonic() + self._REQUEST_TIMEOUT,
            }
        )

        if not pending["event"].wait(self._REQUEST_TIMEOUT):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if pending["dispatched"].is_set():
                raise JarvisBrowserError(
                    "A extensão recebeu o link, mas não confirmou se conseguiu abrir. "
                    "Não abri outra aba para evitar duplicar a solicitação."
                )
            return self._open_default_browser(url)

        result = pending["result"]
        if not result or not result.get("ok"):
            reason = result.get("error") if result else "resposta vazia"
            raise JarvisBrowserError(f"A extensão não conseguiu abrir o link: {reason}")
        return "jarvis_tab"

    @staticmethod
    def _open_default_browser(url):
        try:
            os.startfile(url)
        except OSError as error:
            raise JarvisBrowserError(
                f"Não foi possível abrir o link no navegador padrão: {error}"
            ) from error
        return "default_browser"

    def _wait_for_extension(self):
        deadline = time.monotonic() + self._EXTENSION_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            with self._pending_lock:
                if time.monotonic() - self._last_extension_poll <= self._EXTENSION_POLL_TIMEOUT:
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

                path = urlparse(self.path).path
                if path == "/health":
                    self._send_json(
                        200,
                        {"service": "jarvis-browser-bridge", "version": 1},
                    )
                    return
                if path != "/next":
                    self.send_error(404)
                    return

                deadline = time.monotonic() + browser._EXTENSION_WAIT_TIMEOUT * 2
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
                        continue
                    if command["expires_at"] > time.monotonic():
                        with browser._pending_lock:
                            pending = browser._pending.get(command["request_id"])
                            if pending is not None:
                                pending["dispatched"].set()
                        self._send_json(
                            200,
                            {
                                "request_id": command["request_id"],
                                "url": command["url"],
                            },
                        )
                        return
                self._send_json(200, {})

            def do_POST(self):
                if not self._is_extension_request():
                    self.send_error(403)
                    return
                if urlparse(self.path).path != "/result":
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
                origin = self.headers.get("Origin", "")
                return origin.startswith("chrome-extension://")

            def _add_cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
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