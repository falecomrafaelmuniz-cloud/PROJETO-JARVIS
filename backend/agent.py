import sys
import os
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
from dotenv import load_dotenv
import os
import shutil
import asyncio
import subprocess
import uuid
import json
import logging
import math
import re
import tempfile
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo
import ctypes
from ctypes import wintypes
import zipfile
from PIL import Image
try:
    import pypdf
except ImportError:
    pypdf = None

import chromadb
import httpx
import psutil
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from urllib.parse import quote as url_quote

from livekit import agents, rtc
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    ToolCallEnded,
    ToolCallStarted,
    ToolExecutionUpdatedEvent,
    function_tool,
)
from livekit.plugins import (
    noise_cancellation,
    google
)
from google_workspace import GoogleWorkspaceError, google_workspace
from jarvis_browser import JarvisBrowserError, jarvis_chrome
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION

load_dotenv()

# Silencia logs barulhentos de APIs do Google e clientes HTTP para manter o terminal limpo
for _noisy in [
    "googleapiclient.discovery",
    "googleapiclient",
    "google.auth.transport.requests",
    "urllib3",
]:
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger_memoria = logging.getLogger("jarvis.memoria")
logger_memoria.setLevel(logging.INFO)
logger_memoria.propagate = False
if not logger_memoria.handlers:
    handler_memoria = RotatingFileHandler(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "memoria_jarvis.log"),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler_memoria.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger_memoria.addHandler(handler_memoria)

# --- INÍCIO CONFIGURAÇÃO DA MEMÓRIA DE LONGO PRAZO ---
CHAVE_API = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
cliente_pesquisa = genai.Client(
    api_key=CHAVE_API,
    http_options=genai_types.HttpOptions(
        timeout=20_000,
        retry_options=genai_types.HttpRetryOptions(attempts=1),
    ),
)
MODELO_PESQUISA = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-3.5-flash-lite")
MODELO_PESQUISA_FALLBACK = os.environ.get(
    "GEMINI_RESEARCH_FALLBACK_MODEL", "gemini-flash-lite-latest"
)

class MemoriaGemini:
    def __init__(self, chave):
        self.cliente = genai.Client(api_key=chave)
        
    def name(self):
        return "MemoriaGemini"
        
    def __call__(self, input):
        # Modelo de embeddings atualizado
        resposta = self.cliente.models.embed_content(
            model="gemini-embedding-001",
            contents=input,
        )
        if not resposta.embeddings or any(embedding.values is None for embedding in resposta.embeddings):
            raise ValueError("A API do Gemini não retornou embeddings válidos.")
        resposta = {"embedding": [embedding.values for embedding in resposta.embeddings]}
        
        # Tratamento de segurança para garantir que o formato devolvido seja Lista de Listas
        if isinstance(resposta, dict) and 'embedding' in resposta:
            emb = resposta['embedding']
            if emb and isinstance(emb[0], (float, int)):
                return [emb]
            return emb
        elif isinstance(resposta, list):
            return [e.get('embedding', []) for e in resposta]
            
        return resposta.get('embedding', [])

    # Correção: O ChromaDB exige que o parâmetro se chame 'input' na hora de BUSCAR
    def embed_query(self, input):
        return self.__call__(input)
        
    def embed_documents(self, input):
        return self.__call__(input)

cliente_chroma = chromadb.PersistentClient(path="./memoria_jarvis")

colecao_memoria = cliente_chroma.get_or_create_collection(
    name="memorias_longo_prazo",
    embedding_function=MemoriaGemini(CHAVE_API)
)
DISTANCIA_MAXIMA_MEMORIA = 0.85
# --- FIM CONFIGURAÇÃO DA MEMÓRIA ---


def _update_session_location(
    data_packet: rtc.DataPacket, location_state: dict[str, float | str]
):
    if data_packet.topic != "jarvis.location":
        return

    try:
        payload = json.loads(data_packet.data)
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        logger_memoria.warning("evento=localizacao status=erro motivo=payload_invalido")
        return

    if payload.get("type") != "jarvis_location":
        return
    if (
        isinstance(latitude, bool)
        or not isinstance(latitude, (int, float))
        or not -90 <= latitude <= 90
        or isinstance(longitude, bool)
        or not isinstance(longitude, (int, float))
        or not -180 <= longitude <= 180
    ):
        logger_memoria.warning("evento=localizacao status=erro motivo=coordenadas_invalidas")
        return

    location_state["latitude"] = latitude
    location_state["longitude"] = longitude

    for field in ("country", "state", "city"):
        value = payload.get(field)
        if isinstance(value, str):
            value = " ".join(value.split())
            if value and len(value) <= 120:
                location_state[field] = value
                continue
        location_state.pop(field, None)

    logger_memoria.info("evento=localizacao status=disponivel_na_sessao")


class Assistant(Agent):
    def __init__(self, location_state: dict[str, float | str], room: rtc.Room) -> None:
        self.location_state = location_state
        self.board_room = room
        self._pesquisa_lock = asyncio.Lock()
        self._ultima_pesquisa_cache: tuple[str, float, str] | None = None
        self._rascunho_email: dict = {
            "aberto": False,
            "para": "",
            "cc": "",
            "assunto": "",
            "mensagem": "",
            "anexos": [],
            "status": "rascunho",
            "erro": "",
        }
        self._organizador_email: dict = {
            "sugestoes": [],
            "marcadores_disponiveis": [],
        }
        self._caminho_alertas_email = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "alertas_email_jarvis.json",
        )
        self._alertas_email: list[dict] = self._carregar_alertas_email()
        self._ids_emails_ja_vistos: set[str] = set()
        self._gmail_lock = asyncio.Lock()
        self._ultimo_destino_gmail: str = ""
        self._ultimo_tempo_gmail: float = 0.0
        self._gmail_ja_aberto_na_sessao: bool = False
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                voice="Charon",
                temperature=0.6,
            ),
        )

    def _carregar_alertas_email(self) -> list[dict]:
        try:
            if os.path.isfile(self._caminho_alertas_email):
                with open(self._caminho_alertas_email, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                    if isinstance(dados, list):
                        return dados
        except Exception as erro:
            logger_memoria.warning("evento=carregar_alertas_email status=erro motivo=%s", erro)
        return []

    def _salvar_alertas_email(self) -> None:
        try:
            with open(self._caminho_alertas_email, "w", encoding="utf-8") as f:
                json.dump(self._alertas_email, f, ensure_ascii=False, indent=2)
        except Exception as erro:
            logger_memoria.warning("evento=salvar_alertas_email status=erro motivo=%s", erro)

    async def _navegar_no_gmail_navegador(self, hash_ou_url: str = "#inbox") -> None:
        destino = (hash_ou_url or "#inbox").strip()
        if not destino.startswith("http"):
            if not destino.startswith("#"):
                destino = f"#{destino}"
            url_final = f"https://mail.google.com/mail/u/0/{destino}"
        else:
            url_final = destino

        agora = time.monotonic()
        async with self._gmail_lock:
            # Se for a mesma URL chamada há menos de 3 segundos, não re-executa
            if url_final == self._ultimo_destino_gmail and (agora - self._ultimo_tempo_gmail) < 3.0:
                return

            # Se pediu apenas a caixa de entrada (#inbox) e o Gmail já está aberto no Chrome, não precisa recarregar
            if (
                destino in {"#inbox", "https://mail.google.com/mail/u/0/#inbox"}
                and (self._gmail_ja_aberto_na_sessao or bool(jarvis_chrome.get_gmail_tab().get("url")))
            ):
                self._ultimo_destino_gmail = url_final
                self._ultimo_tempo_gmail = agora
                return

            self._ultimo_destino_gmail = url_final
            self._ultimo_tempo_gmail = agora
            try:
                await asyncio.to_thread(jarvis_chrome.open_url, url_final)
                self._gmail_ja_aberto_na_sessao = True
            except Exception as erro:
                logger_memoria.warning("evento=navegar_gmail status=erro motivo=%s", erro)

    async def _publicar_rascunho_email(self):
        payload = {
            "type": "jarvis_email_draft",
            **self._rascunho_email,
        }
        try:
            await self.board_room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False),
                reliable=True,
                topic="jarvis.email",
            )
        except Exception as erro:
            logger_memoria.error(
                "evento=rascunho_email status=erro tipo=%s",
                type(erro).__name__,
            )

    def buscar_arquivo_local(self, nome_ou_caminho: str) -> str | None:
        termo = nome_ou_caminho.strip().strip('"').strip("'")
        if not termo:
            return None
        if os.path.isfile(termo):
            return os.path.abspath(termo)

        pasta_usuario = os.path.expanduser("~")
        locais_rapidos = [
            os.path.join(pasta_usuario, "Downloads"),
            os.path.join(pasta_usuario, "Desktop"),
            os.path.join(pasta_usuario, "Área de Trabalho"),
            os.path.join(pasta_usuario, "Documents"),
            os.path.join(pasta_usuario, "Documentos"),
            os.path.join(pasta_usuario, "Pictures"),
            os.path.join(pasta_usuario, "Imagens"),
            os.path.join(pasta_usuario, "Videos"),
            os.path.join(pasta_usuario, "Vídeos"),
            os.path.join(pasta_usuario, "Music"),
            os.path.join(pasta_usuario, "Músicas"),
            pasta_usuario,
        ]
        termo_lower = termo.lower()
        candidatos_parciais: list[tuple[float, str]] = []

        for local in locais_rapidos:
            if not os.path.exists(local):
                continue
            try:
                for item in os.listdir(local):
                    caminho_item = os.path.join(local, item)
                    if not os.path.isfile(caminho_item):
                        continue
                    item_lower = item.lower()
                    if item_lower == termo_lower:
                        return caminho_item
                    if termo_lower in item_lower:
                        try:
                            mtime = os.path.getmtime(caminho_item)
                        except OSError:
                            mtime = 0.0
                        candidatos_parciais.append((mtime, caminho_item))
            except OSError:
                continue

        if candidatos_parciais:
            candidatos_parciais.sort(key=lambda par: par[0], reverse=True)
            return candidatos_parciais[0][1]
        return None

    def _preparar_caminho_anexo_local(self, caminho: str) -> str | None:
        caminho_limpo = (caminho or "").strip().strip('"').strip("'")
        if not caminho_limpo or not os.path.exists(caminho_limpo):
            return None
        if os.path.isfile(caminho_limpo):
            return os.path.abspath(caminho_limpo)
        if os.path.isdir(caminho_limpo):
            try:
                pasta_temp = os.path.join(tempfile.gettempdir(), "jarvis-email-attachments")
                os.makedirs(pasta_temp, exist_ok=True)
                nome_base = os.path.basename(os.path.normpath(caminho_limpo)) or "Pasta"
                base_zip = os.path.join(pasta_temp, f"{nome_base}_{uuid.uuid4().hex[:6]}")
                arquivo_zip = shutil.make_archive(base_zip, "zip", caminho_limpo)
                return os.path.abspath(arquivo_zip)
            except Exception as erro:
                logger_memoria.error("evento=zip_pasta_anexo status=erro motivo=%s", erro)
                return None
        return None

    def _obter_selecao_explorador_arquivos(self) -> list[str]:
        """Lê os arquivos/pastas que o usuário deixou clicados/selecionados no Explorador de Arquivos do Windows ou copiados no Clipboard."""
        try:
            import ctypes
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            fg_hwnd = 0

        ps_script = f"""
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $fg = {int(fg_hwnd)}
        $prioritarios = @()
        $outros = @()
        try {{
            $shell = New-Object -ComObject Shell.Application
            foreach ($w in $shell.Windows()) {{
                try {{
                    $sel = @($w.Document.SelectedItems() | ForEach-Object {{ $_.Path }} | Where-Object {{ $_ }})
                    if ($sel.Count -gt 0) {{
                        if ([int64]$w.HWND -eq $fg) {{
                            $prioritarios += $sel
                        }} else {{
                            $outros += $sel
                        }}
                    }}
                }} catch {{}}
            }}
        }} catch {{}}
        $resultado = @()
        if ($prioritarios.Count -gt 0) {{
            $resultado = $prioritarios
        }} elseif ($outros.Count -gt 0) {{
            $resultado = $outros
        }} else {{
            try {{
                Add-Type -AssemblyName System.Windows.Forms
                if ([System.Windows.Forms.Clipboard]::ContainsFileDropList()) {{
                    $resultado = @([System.Windows.Forms.Clipboard]::GetFileDropList())
                }}
            }} catch {{}}
        }}
        $resultado | ConvertTo-Json -Compress
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
            )
            saida = (proc.stdout or "").strip()
            if not saida:
                return []
            dados = json.loads(saida)
            if isinstance(dados, str):
                caminhos_brutos = [dados]
            elif isinstance(dados, list):
                caminhos_brutos = [str(x) for x in dados if x]
            else:
                caminhos_brutos = []
        except Exception:
            caminhos_brutos = []

        preparados: list[str] = []
        vistos: set[str] = set()
        for c in caminhos_brutos:
            prep = self._preparar_caminho_anexo_local(c)
            if prep and prep.lower() not in vistos:
                vistos.add(prep.lower())
                preparados.append(prep)
        return preparados

    def _obter_aba_ativa_navegador(self) -> dict[str, str]:
        """Obtém a URL e o título da aba ativa do navegador (via extensão do Chrome ou UIAutomation do Windows)."""
        aba = jarvis_chrome.get_active_tab()
        if aba.get("url"):
            return aba

        ps_script = r"""
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        try {
            Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
            $procs = Get-Process | Where-Object { $_.ProcessName -in 'chrome','msedge','brave' -and $_.MainWindowHandle -ne 0 }
            foreach ($p in $procs) {
                try {
                    $el = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
                    $cond = New-Object System.Windows.Automation.PropertyCondition(
                        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                        [System.Windows.Automation.ControlType]::Edit
                    )
                    $edits = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
                    foreach ($edit in $edits) {
                        try {
                            $vp = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                            $val = $vp.Current.Value
                            if ($val -and $val -notmatch 'localhost:3000|127\.0\.0\.1:3000') {
                                if ($val -match '^https?://') {
                                    @{ url = $val; title = $p.MainWindowTitle } | ConvertTo-Json -Compress
                                    exit 0
                                } elseif ($val -match '^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/|$)') {
                                    @{ url = "https://$val"; title = $p.MainWindowTitle } | ConvertTo-Json -Compress
                                    exit 0
                                }
                            }
                        } catch {}
                    }
                } catch {}
            }
        } catch {}
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.5,
            )
            saida = (proc.stdout or "").strip()
            if saida:
                dados = json.loads(saida)
                if isinstance(dados, dict) and dados.get("url"):
                    return {
                        "url": str(dados.get("url") or ""),
                        "title": str(dados.get("title") or ""),
                    }
        except Exception:
            pass
        return {"url": "", "title": ""}

    def _capturar_imagem_clipboard_ou_tela(
        self,
        apenas_clipboard: bool = False,
        nome_prefixo: str = "Captura_Tela",
    ) -> str | None:
        pasta_temp = os.path.join(tempfile.gettempdir(), "jarvis-email-attachments")
        os.makedirs(pasta_temp, exist_ok=True)
        nome_limpo = re.sub(r"[^a-zA-Z0-9_-]+", "_", nome_prefixo).strip("_")[:40] or "Captura"
        caminho_destino = os.path.join(pasta_temp, f"{nome_limpo}_{uuid.uuid4().hex[:6]}.png")
        caminho_ps = caminho_destino.replace("'", "''")
        modo_clip = "$true" if apenas_clipboard else "$false"

        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $destino = '{caminho_ps}'
        $apenasClip = {modo_clip}
        if ([System.Windows.Forms.Clipboard]::ContainsImage()) {{
            $img = [System.Windows.Forms.Clipboard]::GetImage()
            if ($img -ne $null) {{
                $img.Save($destino, [System.Drawing.Imaging.ImageFormat]::Png)
                $img.Dispose()
                Write-Output $destino
                exit 0
            }}
        }}
        if (-not $apenasClip) {{
            $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
            $graphics = [System.Drawing.Graphics]::FromImage($bmp)
            $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
            $bmp.Save($destino, [System.Drawing.Imaging.ImageFormat]::Png)
            $graphics.Dispose()
            $bmp.Dispose()
            Write-Output $destino
        }}
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            saida = (proc.stdout or "").strip()
            if saida and os.path.isfile(saida):
                return os.path.abspath(saida)
        except Exception as erro:
            logger_memoria.error("evento=captura_tela_anexo status=erro motivo=%s", erro)
        return None

    def _baixar_arquivo_url_para_anexo(self, url: str, titulo: str = "") -> dict[str, str]:
        """Verifica se a URL é um documento do Google Drive/Docs/Sheets/Slides ou um arquivo/imagem direto para baixar."""
        url_limpa = (url or "").strip()
        if not url_limpa.startswith(("http://", "https://")):
            return {"tipo": "nenhum"}

        # 1. Google Docs / Sheets / Slides / Drive URL
        if "docs.google.com/" in url_limpa or "drive.google.com/" in url_limpa:
            match_id = re.search(r"/d/([a-zA-Z0-9_-]{18,})", url_limpa) or re.search(
                r"[?&]id=([a-zA-Z0-9_-]{18,})", url_limpa
            )
            if match_id:
                doc_id = match_id.group(1)
                nome_doc = (titulo or "Documento do Google Drive").replace(" - Google Docs", "").replace(
                    " - Planilhas Google", ""
                ).replace(" - Apresentações Google", "").replace(" - Google Drive", "").strip()
                return {
                    "tipo": "drive",
                    "id": doc_id,
                    "nome": nome_doc or f"Documento_{doc_id[:6]}.pdf",
                    "url": url_limpa,
                }

        # 2. Verifica se é um arquivo/imagem direto via HTTP
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )
            }
            with httpx.Client(follow_redirects=True, timeout=8.0, headers=headers) as client:
                resp = client.get(url_limpa)
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if resp.status_code == 200 and content_type and not content_type.startswith("text/html"):
                    ext_map = {
                        "application/pdf": ".pdf",
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                        "image/gif": ".gif",
                        "text/csv": ".csv",
                        "text/plain": ".txt",
                        "application/zip": ".zip",
                    }
                    caminho_url = url_limpa.split("?")[0].rstrip("/")
                    nome_url = os.path.basename(caminho_url) or "Arquivo_Web"
                    ext = os.path.splitext(nome_url)[1]
                    if not ext:
                        ext = ext_map.get(content_type, ".bin")
                        nome_url = f"{nome_url}{ext}"
                    pasta_temp = os.path.join(tempfile.gettempdir(), "jarvis-email-attachments")
                    os.makedirs(pasta_temp, exist_ok=True)
                    nome_seguro = re.sub(r'[<>:"/\\|?*]+', "_", nome_url)[:80]
                    destino = os.path.join(pasta_temp, f"{uuid.uuid4().hex[:6]}_{nome_seguro}")
                    with open(destino, "wb") as f:
                        f.write(resp.content)
                    return {
                        "tipo": "local",
                        "caminho": os.path.abspath(destino),
                        "nome": nome_seguro,
                        "url": url_limpa,
                    }
        except Exception:
            pass

        return {"tipo": "webpage", "url": url_limpa, "titulo": titulo}

    def buscar_pasta(self, nome_pasta: str):
        pasta_usuario = os.path.expanduser("~")
        locais_rapidos = [
            os.path.join(pasta_usuario, "Downloads"),
            os.path.join(pasta_usuario, "Desktop"),
            os.path.join(pasta_usuario, "Documents"),
            os.path.join(pasta_usuario, "Área de Trabalho"),
            os.path.join(pasta_usuario, "Documentos"),
            pasta_usuario
        ]
        
        for local in locais_rapidos:
            if not os.path.exists(local): continue
            try:
                for item in os.listdir(local):
                    caminho_item = os.path.join(local, item)
                    if os.path.isdir(caminho_item) and item.lower() == nome_pasta.lower():
                        return caminho_item
            except OSError as e:
                print(f"[AVISO] Não foi possível consultar '{local}': {e}")
                
        for root, dirs, files in os.walk(pasta_usuario):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['AppData', 'Local Settings']]
            if nome_pasta.lower() in [d.lower() for d in dirs]:
                idx = [d.lower() for d in dirs].index(nome_pasta.lower())
                return os.path.join(root, dirs[idx])
        return None

    def buscar_atalho_programa(self, nome_programa: str):
        pastas_busca = [
            os.path.join(os.path.expanduser("~"), "Desktop"),
            os.path.join(os.path.expanduser("~"), "Área de Trabalho"),
            os.environ.get("PUBLIC", "C:\\Users\\Public") + "\\Desktop",
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "Microsoft\\Windows\\Start Menu\\Programs")
        ]
        
        nome_lower = nome_programa.lower()
        for pasta in pastas_busca:
            if not pasta or not os.path.exists(pasta):
                continue
            for root, dirs, files in os.walk(pasta):
                for file in files:
                    if file.lower().endswith('.lnk') and nome_lower in file.lower():
                        return os.path.join(root, file)
    def _capturar_imagem_tela(self) -> Image.Image | None:
        # 1. Tenta captura nativa GDI BitBlt com anexação ao Desktop interativo (~15ms)
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)

            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                hdc_screen = user32.GetDC(0)
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
                old_bm = gdi32.SelectObject(hdc_mem, hbm)
                gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD),
                        ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = w
                bmi.biHeight = -h
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0

                buf = ctypes.create_string_buffer(w * h * 4)
                gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)

                gdi32.SelectObject(hdc_mem, old_bm)
                gdi32.DeleteObject(hbm)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc_screen)

                img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
                extrema = img.getextrema()
                if any(mx > 0 for _, mx in extrema):
                    return img
        except Exception as erro_gdi:
            logger_memoria.warning("evento=captura_tela_gdi_fallback motivo=%s", erro_gdi)

        # 2. Fallback via Windows Forms / Graphics CopyFromScreen
        caminho = self._capturar_imagem_clipboard_ou_tela(apenas_clipboard=False, nome_prefixo="Jarvis_Visao_Tela")
        if caminho and os.path.isfile(caminho):
            try:
                im = Image.open(caminho)
                im.load()
                return im.convert("RGB")
            except Exception:
                pass

        return None

    def _chamar_gemini_visao(self, imagem_pil: Image.Image, prompt: str) -> str:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return "Aviso: GOOGLE_API_KEY não configurada no ambiente."
        client = genai.Client(api_key=api_key)

        img_otimizada = imagem_pil.copy()
        if img_otimizada.width > 1280 or img_otimizada.height > 720:
            img_otimizada.thumbnail((1280, 720), Image.Resampling.LANCZOS)

        modelos = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]
        ultimo_erro = None
        for m in modelos:
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=[img_otimizada, prompt],
                )
                if resp and resp.text:
                    return resp.text.strip()
            except Exception as e:
                ultimo_erro = e
                continue
        return f"Aviso: Não foi possível analisar visualmente a tela no momento ({ultimo_erro})."

    def _extrair_texto_documento_ou_pdf(self, caminho_ou_id: str) -> dict:
        alvo = (caminho_ou_id or "").strip()
        if not alvo:
            return {"erro": "Nenhum arquivo ou documento informado."}

        # Verifica se é um arquivo local
        if os.path.exists(alvo):
            caminho = os.path.abspath(alvo)
            nome = os.path.basename(caminho)
            ext = os.path.splitext(caminho)[1].lower()

            if ext == ".pdf":
                if pypdf is None:
                    return {"erro": "Biblioteca pypdf não instalada no ambiente. Execute: pip install pypdf"}
                try:
                    reader = pypdf.PdfReader(caminho)
                    total_pags = len(reader.pages)
                    textos = []
                    for idx, pag in enumerate(reader.pages[:35], start=1):
                        txt = (pag.extract_text() or "").strip()
                        if txt:
                            textos.append(f"--- Página {idx} ---\n{txt}")
                    conteudo = "\n\n".join(textos).strip()
                    if len(conteudo) < 50:
                        return {
                            "tipo": "pdf_digitalizado",
                            "nome": nome,
                            "caminho": caminho,
                            "paginas": total_pags,
                            "texto": "(PDF digitalizado/sem camada de texto selecionável)",
                        }
                    return {
                        "tipo": "pdf",
                        "nome": nome,
                        "caminho": caminho,
                        "paginas": total_pags,
                        "texto": conteudo[:45000],
                    }
                except Exception as erro:
                    return {"erro": f"Erro ao ler PDF '{nome}': {erro}"}

            if ext in {".docx", ".doc"}:
                try:
                    with zipfile.ZipFile(caminho) as z:
                        xml_content = z.read("word/document.xml").decode("utf-8", errors="ignore")
                        paragrafos = re.findall(r"<w:p[^>]*>(.*?)</w:p>", xml_content)
                        linhas = []
                        for p in paragrafos:
                            textos = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p)
                            if textos:
                                linhas.append("".join(textos))
                        texto_final = "\n".join(linhas).strip()
                        return {
                            "tipo": "docx",
                            "nome": nome,
                            "caminho": caminho,
                            "texto": texto_final[:45000],
                        }
                except Exception as erro:
                    return {"erro": f"Erro ao ler arquivo Word '{nome}': {erro}"}

            # Arquivos de texto em geral (.txt, .csv, .md, .json, .py, .log)
            try:
                for enc in ("utf-8", "latin-1", "cp1252"):
                    try:
                        with open(caminho, "r", encoding=enc) as f:
                            conteudo = f.read(45000)
                            return {
                                "tipo": "texto",
                                "nome": nome,
                                "caminho": caminho,
                                "texto": conteudo,
                            }
                    except UnicodeDecodeError:
                        continue
            except Exception as erro:
                return {"erro": f"Erro ao ler arquivo de texto '{nome}': {erro}"}

        # Se não é arquivo local, tenta ler como arquivo do Google Drive
        try:
            res_drive = google_workspace.read_drive_file(alvo)
            return {
                "tipo": "google_drive",
                "id": alvo,
                "nome": res_drive.get("nome", "Documento do Google Drive"),
                "texto": str(res_drive.get("conteudo") or "")[:45000],
            }
        except Exception:
            pass

        return {"erro": f"Não foi possível localizar o arquivo ou documento '{alvo}'."}

    # --- INÍCIO DAS FERRAMENTAS DO JARVIS ---

    @function_tool(
        description="Obrigatória sempre que o usuário perguntar as horas, a hora atual, a data ou o horário local. Retorna a data e hora atuais no fuso de Brasília (America/Sao_Paulo); use exatamente esse resultado e nunca responda com UTC ou com o relógio do servidor."
    )
    async def obter_horario_brasilia(self) -> str:
        agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
        dias_semana = (
            "segunda-feira",
            "terça-feira",
            "quarta-feira",
            "quinta-feira",
            "sexta-feira",
            "sábado",
            "domingo",
        )
        data_hora = (
            f"{dias_semana[agora.weekday()]}, "
            f"{agora.day:02d}/{agora.month:02d}/{agora.year}, "
            f"{agora.hour:02d}:{agora.minute:02d}:{agora.second:02d}"
        )
        return f"Horário oficial de Brasília (America/Sao_Paulo): {data_hora} (UTC-03:00)."

    @function_tool(
        description="Retorna a localização compartilhada nesta sessão, incluindo país, estado e cidade aproximada quando identificados, além das coordenadas. Use sempre que o usuário perguntar onde está ou fizer um pedido que dependa da localização. Se nomes de localidade não estiverem disponíveis, informe as coordenadas sem inventar nomes."
    )
    async def obter_localizacao_atual(self) -> str:
        latitude = self.location_state.get("latitude")
        longitude = self.location_state.get("longitude")
        if (
            isinstance(latitude, bool)
            or not isinstance(latitude, (int, float))
            or isinstance(longitude, bool)
            or not isinstance(longitude, (int, float))
        ):
            return (
                "A localização atual não está disponível nesta sessão. "
                "Não há coordenadas disponíveis para identificar onde o usuário está."
            )

        place = ", ".join(
            value
            for field in ("city", "state", "country")
            if isinstance((value := self.location_state.get(field)), str)
        )
        if place:
            return (
                f"Localização aproximada atual do usuário: {place}. "
                f"Coordenadas: latitude {latitude}, longitude {longitude}."
            )
        return (
            f"Localização compartilhada nesta sessão: latitude {latitude}, longitude {longitude}. "
            "O nome do país, estado ou cidade ainda não foi identificado."
        )

    @function_tool(description="Obrigatória quando o usuário pedir para lembrar, guardar, anotar ou gravar uma informação: salva localmente e acrescenta uma nova entrada numerada ao final do único arquivo Memorias_do_Jarvis.txt na pasta Memórias do Jarvis do Google Drive. Não recria, substitui, apaga nem cria outro arquivo de memória.")
    async def salvar_na_memoria(self, informacao_detalhada: str):
        logger_memoria.info("evento=salvar status=iniciado")
        id_unico = str(uuid.uuid4())
        try:
            colecao_memoria.add(
                documents=[informacao_detalhada],
                ids=[id_unico],
                metadatas=[{"drive_archive_status": "pendente"}],
            )
        except Exception as e:
            logger_memoria.error("evento=salvar status=erro tipo=%s", type(e).__name__)
            return "Aviso: não foi possível salvar a informação na memória local."

        logger_memoria.info("evento=salvar_local status=sucesso")
        try:
            arquivo_drive = await asyncio.to_thread(
                google_workspace.archive_jarvis_memory,
                informacao_detalhada,
                id_unico,
            )
        except GoogleWorkspaceError as e:
            logger_memoria.error(
                "evento=salvar_drive status=erro tipo=%s",
                type(e).__name__,
            )
            return (
                "A informação foi salva na memória local, mas não consegui arquivá-la "
                f"no Google Drive. Motivo: {e}"
            )
        except Exception as e:
            logger_memoria.error(
                "evento=salvar_drive status=erro tipo=%s",
                type(e).__name__,
            )
            return (
                "A informação foi salva na memória local, mas ocorreu uma falha técnica "
                "ao arquivá-la no Google Drive."
            )

        try:
            registro = colecao_memoria.get(
                ids=[id_unico],
                include=["metadatas"],
            )
            metadados = (registro.get("metadatas") or [{}])[0] or {}
            metadados.update(
                {
                    "drive_archive_status": "salvo",
                    "drive_archive_file_id": arquivo_drive["id"],
                    "drive_archive_name": arquivo_drive["nome"],
                }
            )
            colecao_memoria.update(
                ids=[id_unico],
                metadatas=[metadados],
            )
        except Exception as e:
            logger_memoria.error(
                "evento=marcar_arquivo_drive status=erro tipo=%s",
                type(e).__name__,
            )

        logger_memoria.info(
            "evento=salvar_drive status=sucesso numero=%04d",
            arquivo_drive["numero_memoria"],
        )
        return (
            "Informação guardada na memória local e arquivada no Google Drive "
            f"como {arquivo_drive['nome']}."
        )

    @function_tool(description="Busca anotações ou fatos pessoais do usuário armazenados na memória de longo prazo. Chame apenas se o usuário perguntar explicitamente sobre algo gravado, anotado ou lembrado no passado.")
    async def buscar_na_memoria(self, assunto_ou_pergunta: str):
        logger_memoria.info("evento=buscar status=iniciado")
        try:
            quantidade_memorias = await asyncio.to_thread(colecao_memoria.count)
            if quantidade_memorias == 0:
                logger_memoria.info("evento=buscar status=sem_resultados resultados=0")
                return "Nenhuma lembrança encontrada sobre esse assunto na memória."

            resultados = await asyncio.to_thread(
                colecao_memoria.query,
                query_texts=[assunto_ou_pergunta],
                n_results=min(10, quantidade_memorias),
                include=["documents", "distances"],
            )
            documentos = resultados.get("documents", [[]])[0] or []
            distancias = resultados.get("distances", [[]])[0] or []
            lembrancas_relevantes = [
                documento
                for documento, distancia in zip(documentos, distancias)
                if documento and distancia <= DISTANCIA_MAXIMA_MEMORIA
            ]
            if lembrancas_relevantes:
                lembrancas = "\n".join(lembrancas_relevantes)
                logger_memoria.info(
                    "evento=buscar status=sucesso resultados=%d",
                    len(lembrancas_relevantes),
                )
                return f"Lembranças encontradas na memória:\n{lembrancas}"
            logger_memoria.info(
                "evento=buscar status=sem_resultados resultados=0 candidatos=%d",
                len(documentos),
            )
            return "Nenhuma lembrança encontrada sobre esse assunto na memória."
        except Exception as e:
            logger_memoria.error("evento=buscar status=erro tipo=%s", type(e).__name__)
            return f"Aviso: Erro ao tentar buscar na memória."

    def _buscar_fontes_e_imagens_web(
        self, assunto: str
    ) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
        resumos: list[str] = []
        fontes: list[dict[str, str]] = []
        imagens: list[dict[str, str]] = []
        headers = {"User-Agent": "JarvisAssistant/1.0 (https://localhost)"}
        with httpx.Client(headers=headers, timeout=2.5, follow_redirects=True) as client:
            for idioma in ("pt", "en"):
                try:
                    resp = client.get(
                        f"https://{idioma}.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": assunto,
                            "gsrlimit": 4,
                            "prop": "pageimages|extracts",
                            "piprop": "thumbnail",
                            "pithumbsize": 640,
                            "exintro": 1,
                            "explaintext": 1,
                            "exsentences": 2,
                            "exlimit": "max",
                            "format": "json",
                        },
                    )
                    resp.raise_for_status()
                    paginas = (resp.json().get("query", {}) or {}).get("pages", {}) or {}
                    itens_ordenados = sorted(
                        paginas.values(), key=lambda p: p.get("index", 99)
                    )
                    for item in itens_ordenados:
                        titulo = (item.get("title") or "").strip()
                        extrato = " ".join((item.get("extract") or "").split()).strip()
                        if not titulo:
                            continue
                        url = f"https://{idioma}.wikipedia.org/wiki/{url_quote(titulo.replace(' ', '_'))}"
                        fontes.append({"titulo": f"{titulo} ({idioma.upper()})", "url": url})
                        if extrato:
                            resumos.append(f"- {titulo}: {extrato[:240]}")
                        thumb = (item.get("thumbnail") or {}).get("source") or ""
                        if (
                            isinstance(thumb, str)
                            and thumb.startswith("https://")
                            and not thumb.lower().endswith(".svg")
                        ):
                            imagens.append({"url": thumb, "legenda": titulo[:65]})
                    if fontes and imagens:
                        break
                except Exception:
                    continue

            # Se ainda não achou imagem, busca no Wikimedia Commons em ~0.8s
            if not imagens:
                try:
                    resp_commons = client.get(
                        "https://commons.wikimedia.org/w/api.php",
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": f"filetype:bitmap {assunto}",
                            "gsrnamespace": 6,
                            "gsrlimit": 3,
                            "prop": "imageinfo",
                            "iiprop": "url",
                            "iiurlwidth": 640,
                            "format": "json",
                        },
                    )
                    paginas_c = (resp_commons.json().get("query", {}) or {}).get("pages", {}) or {}
                    for item in paginas_c.values():
                        info = ((item.get("imageinfo") or [{}])[0]) or {}
                        thumb = (info.get("thumburl") or info.get("url") or "").strip()
                        titulo_img = (item.get("title") or "").replace("File:", "").rsplit(".", 1)[0]
                        if thumb.startswith("https://"):
                            imagens.append({"url": thumb, "legenda": titulo_img[:65]})
                except Exception:
                    pass

        return "\n".join(resumos), fontes[:3], imagens[:2]

    def _gerar_estrutura_rapida(self, prompt_pesquisa: str, assunto: str) -> dict:
        for modelo_candidato in (MODELO_PESQUISA_FALLBACK, "gemini-3.5-flash", MODELO_PESQUISA):
            try:
                resposta_fb = cliente_pesquisa.models.generate_content(
                    model=modelo_candidato,
                    contents=prompt_pesquisa,
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.4,
                        max_output_tokens=750,
                    ),
                )
                texto_fb = (resposta_fb.text or "").strip()
                if texto_fb:
                    return self._extrair_estrutura_aula(texto_fb, assunto)
            except Exception:
                continue
        raise RuntimeError("Nenhum modelo respondeu dentro do limite.")

    def _extrair_estrutura_aula(self, texto: str, assunto: str) -> dict:
        limpo = texto.strip()
        if limpo.startswith("```"):
            limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", limpo, flags=re.DOTALL).strip()
        inicio = limpo.find("{")
        fim = limpo.rfind("}")
        if inicio != -1 and fim != -1 and fim > inicio:
            try:
                dados = json.loads(limpo[inicio : fim + 1])
                if isinstance(dados, dict):
                    return dados
            except json.JSONDecodeError:
                pass
        linhas = [l.strip("-•* \t") for l in limpo.splitlines() if l.strip()]
        return {
            "titulo": assunto.title()[:60],
            "ideia_central": (linhas[0] if linhas else limpo)[:240],
            "conceitos": [l[:95] for l in linhas[1:5]] or [limpo[:95]],
            "etapas_esquema": ["Início", "Processo", "Resultado"],
            "exemplo": (linhas[5] if len(linhas) > 5 else "")[:180],
            "roteiro_fala": limpo[:700],
        }

    async def _montar_aula_na_lousa(
        self,
        assunto: str,
        estrutura: dict,
        fontes: list[dict[str, str]],
        imagens: list[dict[str, str]],
    ) -> None:
        await self._publicar_na_lousa("clear")

        titulo = str(estrutura.get("titulo") or assunto).strip()[:58]
        ideia_central = str(estrutura.get("ideia_central") or "").strip()[:240]
        conceitos = [
            str(c).strip()[:115]
            for c in (estrutura.get("conceitos") or [])
            if str(c).strip()
        ][:4]
        etapas = [
            str(e).strip()[:30]
            for e in (estrutura.get("etapas_esquema") or [])
            if str(e).strip()
        ][:4]
        exemplo = str(estrutura.get("exemplo") or "").strip()[:190]
        analogia = str(estrutura.get("analogia") or "").strip()[:190]

        # 1. Título no topo em amarelo giz
        await self.escrever_na_lousa(
            texto=f"AULA: {titulo.upper()}",
            x=40,
            y=46,
            cor="#f5d76e",
            tamanho=31,
        )

        # 2. Ideia central logo abaixo em branco giz
        if ideia_central:
            await self.escrever_na_lousa(
                texto=ideia_central,
                x=40,
                y=88,
                cor="#f8f5e7",
                tamanho=19,
            )

        # 3. Conceitos-chave detalhados na coluna esquerda
        if conceitos:
            texto_conceitos = "CONCEITOS-CHAVE:\n" + "\n".join(
                f"{idx + 1}. {c}" for idx, c in enumerate(conceitos)
            )
            await self.escrever_na_lousa(
                texto=texto_conceitos,
                x=40,
                y=180,
                cor="#8ce5f2",
                tamanho=18,
            )

        # 4. Imagem ilustrativa no centro-esquerda inferior (quando disponível)
        if imagens:
            img_principal = imagens[0]
            await self.adicionar_imagem_na_lousa(
                url=img_principal["url"],
                legenda=img_principal.get("legenda", assunto),
                x=40,
                y=430,
                largura=310,
                altura=195,
            )
            if len(imagens) > 1:
                img_sec = imagens[1]
                await self.adicionar_imagem_na_lousa(
                    url=img_sec["url"],
                    legenda=img_sec.get("legenda", ""),
                    x=375,
                    y=430,
                    largura=290,
                    altura=195,
                )

        # 5. Esquema visual na direita (fluxograma completo com caixas coloridas e setas)
        await self.escrever_na_lousa(
            texto="ESQUEMA VISUAL:",
            x=710,
            y=180,
            cor="#f5d76e",
            tamanho=19,
        )
        cores_esquema = ("#f5d76e", "#8ce5f2", "#ffb36b", "#ff9fba")
        if etapas:
            y_caixa = 200
            for idx, etapa in enumerate(etapas):
                cor_caixa = cores_esquema[idx % len(cores_esquema)]
                await self.desenhar_esquema_na_lousa(
                    tipo="retangulo",
                    x=710,
                    y=y_caixa,
                    largura=450,
                    altura=68,
                    rotulo=f"{idx + 1}. {etapa}",
                    cor=cor_caixa,
                )
                if idx < len(etapas) - 1:
                    await self.desenhar_esquema_na_lousa(
                        tipo="seta",
                        x=935,
                        y=y_caixa + 70,
                        largura=2,
                        altura=28,
                        rotulo="",
                        cor="#f8f5e7",
                    )
                y_caixa += 100

        # 6. Analogia, Exemplo prático e Fontes na base da lousa
        rodape_partes: list[str] = []
        if analogia:
            rodape_partes.append(f"Analogia: {analogia}")
        if exemplo:
            rodape_partes.append(f"Na prática: {exemplo}")
        if fontes:
            titulos_fontes = ", ".join(f["titulo"] for f in fontes[:3])
            rodape_partes.append(f"Fontes: {titulos_fontes}")
        if rodape_partes:
            await self.escrever_na_lousa(
                texto="\n".join(rodape_partes),
                x=40,
                y=665,
                cor="#ffb36b",
                tamanho=16,
            )

    @function_tool(description="Pesquisa um assunto na web em ~3 segundos, abre a lousa imediatamente com conceitos, imagens ilustrativas e fluxograma, e retorna a aula completa para você explicar ao usuário.")
    async def pesquisar_assunto_para_aula(
        self,
        assunto: str,
        foco: str = "",
        nivel: str = "iniciante",
    ):
        assunto = assunto.strip()
        foco = foco.strip()
        nivel = nivel.strip()
        if not assunto or len(assunto) > 500:
            return "Aviso: informe um assunto com até 500 caracteres."
        if len(foco) > 500:
            return "Aviso: o foco da pesquisa pode ter até 500 caracteres."
        if not nivel or len(nivel) > 80:
            return "Aviso: informe um nível de ensino com até 80 caracteres."

        chave_cache = f"{assunto.lower()}|{foco.lower()}|{nivel.lower()}"
        async with self._pesquisa_lock:
            # Abre a lousa instantaneamente (em 0.05s) com aviso de carregamento
            await self._publicar_na_lousa("clear")
            await self.escrever_na_lousa(
                texto=f"PREPARANDO AULA: {assunto.upper()}...",
                x=60,
                y=70,
                cor="#f5d76e",
                tamanho=30,
            )

            prompt_pesquisa = (
                "Você é um professor de excelência. Prepare uma aula completa, rica e didática "
                "em português do Brasil sobre o assunto abaixo e retorne APENAS um JSON válido com estas chaves:\n"
                '{\n'
                '  "titulo": "título claro de até 48 caracteres",\n'
                '  "ideia_central": "definição clara e precisa em 1 a 2 frases (até 210 chars)",\n'
                '  "conceitos": [\n'
                '    "Conceito 1: explicação direta (até 95 chars)",\n'
                '    "Conceito 2: explicação direta (até 95 chars)",\n'
                '    "Conceito 3: explicação direta (até 95 chars)",\n'
                '    "Conceito 4: explicação direta (até 95 chars)"\n'
                '  ],\n'
                '  "etapas_esquema": [\n'
                '    "Etapa 1 (até 28 chars)",\n'
                '    "Etapa 2 (até 28 chars)",\n'
                '    "Etapa 3 (até 28 chars)",\n'
                '    "Etapa 4 (até 28 chars)"\n'
                '  ],\n'
                '  "analogia": "analogia simples do mundo real (até 150 chars)",\n'
                '  "exemplo": "exemplo concreto de aplicação (até 150 chars)",\n'
                '  "roteiro_fala": "Explicação falada completa, didática e envolvente (8 a 12 frases), guiando o aluno pelo que está na lousa (definição, os 4 conceitos, as imagens, o fluxograma e o exemplo prático)."\n'
                '}\n'
                f"Assunto: {assunto}\nFoco: {foco or 'visão geral completa'}\nNível: {nivel}."
            )
            pesquisa_iniciada = time.monotonic()
            try:
                # Executa a busca de imagens/fontes E a geração da aula em PARALELO (~2.5 a 3 segundos no total!)
                tarefa_web = asyncio.to_thread(self._buscar_fontes_e_imagens_web, assunto)
                tarefa_ia = asyncio.to_thread(self._gerar_estrutura_rapida, prompt_pesquisa, assunto)
                (_, fontes, imagens), estrutura = await asyncio.wait_for(
                    asyncio.gather(tarefa_web, tarefa_ia),
                    timeout=8,
                )
                modo = "paralelo_ultrarrapido"
            except Exception as error:
                duracao_ms = int((time.monotonic() - pesquisa_iniciada) * 1000)
                logger_memoria.error(
                    "evento=pesquisa_aula status=erro tipo=%s duracao_ms=%d",
                    type(error).__name__,
                    duracao_ms,
                )
                estrutura = {
                    "titulo": assunto.title()[:50],
                    "ideia_central": f"Visão geral essencial sobre {assunto} ({nivel}).",
                    "conceitos": [
                        f"Fundamentos principais de {assunto}",
                        f"Como funciona na prática ({foco or 'mecanismo central'})",
                        "Componentes e estrutura de funcionamento",
                        "Aplicações reais e impacto atual",
                    ],
                    "etapas_esquema": ["Base Conceitual", "Mecanismo Central", "Processamento", "Aplicação Prática"],
                    "analogia": f"Pense em {assunto} como uma engrenagem coordenada onde cada etapa alimenta a seguinte.",
                    "exemplo": f"Aplicação prática de {assunto} no dia a dia.",
                    "roteiro_fala": f"Preparei o quadro completo na lousa sobre {assunto}. Explique detalhadamente ao usuário a definição central, os pilares, o fluxograma e o exemplo prático.",
                }
                fontes = []
                imagens = []
                modo = "contingencia_local"

            await self._montar_aula_na_lousa(assunto, estrutura, fontes, imagens)

            duracao_ms = int((time.monotonic() - pesquisa_iniciada) * 1000)
            logger_memoria.info(
                "evento=pesquisa_aula status=sucesso modo=%s fontes=%d imagens=%d duracao_ms=%d",
                modo,
                len(fontes),
                len(imagens),
                duracao_ms,
            )
            roteiro = str(estrutura.get("roteiro_fala") or estrutura.get("ideia_central") or "").strip()
            conceitos_txt = " | ".join(str(c) for c in (estrutura.get("conceitos") or [])[:4])
            fontes_txt = ", ".join(f"{f['titulo']} ({f['url']})" for f in fontes[:3]) or "conhecimento consolidado"
            resumo_curto = (
                f"A lousa já foi aberta e preenchida com título, definição, 4 conceitos, {len(imagens)} imagem(ns), fluxograma e exemplo sobre '{assunto}'. "
                f"Comece IMEDIATAMENTE a dar uma AULA COMPLETA E DETALHADA em voz alta ao usuário (não chame mais nenhuma ferramenta da lousa!), seguindo este roteiro: {roteiro}\n"
                f"Conceitos na lousa: {conceitos_txt}\n"
                f"Analogia: {estrutura.get('analogia', '')} | Exemplo: {estrutura.get('exemplo', '')}\n"
                f"Fontes: {fontes_txt}."
            )
            self._ultima_pesquisa_cache = (chave_cache, time.monotonic(), resumo_curto)
            return resumo_curto

    async def _publicar_na_lousa(
        self,
        action: str,
        element: dict | None = None,
        target: str | None = None,
        changes: dict | None = None,
    ):
        packet: dict = {"type": "jarvis_board", "action": action}
        if element is not None:
            packet["element"] = element
        if target is not None:
            packet["target"] = target
        if changes is not None:
            packet["changes"] = changes
        try:
            await self.board_room.local_participant.publish_data(
                json.dumps(packet, ensure_ascii=False),
                topic="jarvis.board",
            )
        except Exception as error:
            logger_memoria.error(
                "evento=lousa status=erro_publicacao tipo=%s",
                type(error).__name__,
            )
            return "Aviso: não consegui enviar a atualização para a lousa desta sessão."
        return "A lousa foi atualizada."

    @function_tool(description="Abre a lousa de ensino na tela do usuário sem apagar o conteúdo atual. Use quando o usuário pedir para abrir ou mostrar a lousa.")
    async def abrir_lousa(self):
        return await self._publicar_na_lousa("open")

    @function_tool(description="Fecha/oculta a janela da lousa de ensino na tela do usuário, mantendo os desenhos salvos. Use quando o usuário pedir para fechar, esconder ou sair da lousa.")
    async def fechar_lousa(self):
        return await self._publicar_na_lousa("close")

    @function_tool(description="Edita ou move um item já existente na lousa. Informe em 'texto_ou_rotulo_atual' um trecho do texto/rótulo que deseja editar (ou 'ultimo' para o último item criado), e passe o 'novo_texto', 'nova_cor' (#RRGGBB) ou novas coordenadas 'novo_x'/'novo_y'.")
    async def editar_item_na_lousa(
        self,
        texto_ou_rotulo_atual: str,
        novo_texto: str = "",
        nova_cor: str = "",
        novo_x: float = -1,
        novo_y: float = -1,
        novo_tamanho: float = -1,
    ):
        alvo = texto_ou_rotulo_atual.strip()
        if not alvo:
            return "Aviso: informe o texto/rótulo do item que deseja editar ou 'ultimo'."
        changes: dict[str, str | float] = {}
        if novo_texto.strip():
            changes["text"] = novo_texto.strip()[:3000]
        if nova_cor.strip():
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", nova_cor.strip()):
                return "Aviso: a nova cor deve estar em hexadecimal, por exemplo #f5d76e."
            changes["color"] = nova_cor.strip()
        if novo_x >= 0:
            changes["x"] = max(0, min(1200, novo_x))
        if novo_y >= 0:
            changes["y"] = max(0, min(760, novo_y))
        if novo_tamanho > 0:
            changes["size"] = max(12, min(72, novo_tamanho))
        if not changes:
            return "Aviso: informe pelo menos uma alteração (novo_texto, nova_cor, novo_x, novo_y ou novo_tamanho)."
        return await self._publicar_na_lousa("update", target=alvo, changes=changes)

    @function_tool(description="Apaga um item específico da lousa sem limpar o quadro inteiro. Informe um trecho do texto/rótulo do item que quer apagar, ou 'ultimo' para desfazer/remover o último item adicionado.")
    async def apagar_item_da_lousa(self, texto_ou_rotulo_ou_ultimo: str = "ultimo"):
        alvo = texto_ou_rotulo_ou_ultimo.strip() or "ultimo"
        return await self._publicar_na_lousa("remove", target=alvo)

    @function_tool(description="Escreve um texto explicativo na lousa visível ao usuário. Coordenadas x/y usam uma área 1200x760; use valores entre 0 e 1200 para x e 0 e 760 para y. Abre a lousa automaticamente.")
    async def escrever_na_lousa(
        self,
        texto: str,
        x: float = 80,
        y: float = 100,
        cor: str = "#f8f5e7",
        tamanho: float = 30,
    ):
        texto = texto.strip()
        if not texto or len(texto) > 3000:
            return "Aviso: informe um texto para a lousa com até 3000 caracteres."
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor):
            return "Aviso: a cor deve estar em hexadecimal, por exemplo #172554."
        element = {
            "id": str(uuid.uuid4()),
            "type": "text",
            "x": max(0, min(1200, x)),
            "y": max(0, min(760, y)),
            "text": texto,
            "color": cor,
            "size": max(12, min(72, tamanho)),
        }
        return await self._publicar_na_lousa("add", element)

    @function_tool(description="Desenha na lousa um retângulo, círculo ou seta para ilustrar uma explicação. tipo aceita retangulo, circulo ou seta; x/y usam área 1200x760.")
    async def desenhar_esquema_na_lousa(
        self,
        tipo: str,
        x: float,
        y: float,
        largura: float = 180,
        altura: float = 120,
        rotulo: str = "",
        cor: str = "#f8f5e7",
    ):
        kind = tipo.strip().lower()
        if kind not in {"retangulo", "retângulo", "circulo", "círculo", "seta"}:
            return "Aviso: tipo inválido; escolha retangulo, circulo ou seta."
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor):
            return "Aviso: a cor deve estar em hexadecimal, por exemplo #0e7490."
        x = max(0, min(1200, x))
        y = max(0, min(760, y))
        width = max(2, min(1200 - x, largura))
        height = max(2, min(760 - y, altura))
        if kind == "seta":
            element = {
                "id": str(uuid.uuid4()),
                "type": "line",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "points": f"{x},{y} {x + width},{y + height}",
                "color": cor,
                "size": 5,
                "label": rotulo[:300],
            }
        else:
            element = {
                "id": str(uuid.uuid4()),
                "type": "ellipse" if kind in {"circulo", "círculo"} else "rectangle",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "color": cor,
                "size": 4,
                "label": rotulo[:300],
            }
        return await self._publicar_na_lousa("add", element)

    @function_tool(description="Desenha uma linha livre na lousa a partir de coordenadas JSON [[x,y], ...]. Use a escala 1200x760 e no máximo 400 pontos.")
    async def desenhar_linha_na_lousa(
        self,
        pontos_json: str,
        cor: str = "#f8f5e7",
        espessura: float = 4,
    ):
        try:
            points = json.loads(pontos_json)
        except (TypeError, json.JSONDecodeError):
            return "Aviso: os pontos devem ser um JSON válido no formato [[x,y], ...]."
        if (
            not isinstance(points, list)
            or len(points) < 2
            or len(points) > 400
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in point
                )
                for point in points
            )
        ):
            return "Aviso: informe de 2 a 400 pares de coordenadas numéricas."
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor):
            return "Aviso: a cor deve estar em hexadecimal, por exemplo #0e7490."
        coordinates = " ".join(
            f"{max(0, min(1200, x))},{max(0, min(760, y))}"
            for x, y in points
        )
        element = {
            "id": str(uuid.uuid4()),
            "type": "path",
            "x": 0,
            "y": 0,
            "points": coordinates,
            "color": cor,
            "size": max(1, min(24, espessura)),
        }
        return await self._publicar_na_lousa("add", element)

    @function_tool(description="Adiciona à lousa uma imagem pública por URL HTTPS. Não use links privados nem temporários que exijam login; para fotos locais, peça ao usuário para carregá-las pela lousa.")
    async def adicionar_imagem_na_lousa(
        self,
        url: str,
        legenda: str = "",
        x: float = 420,
        y: float = 180,
        largura: float = 320,
        altura: float = 220,
    ):
        url = url.strip()
        if (
            not url.startswith("https://")
            or len(url) > 4000
            or any(character.isspace() for character in url)
        ):
            return "Aviso: informe uma URL pública HTTPS de imagem, com até 4000 caracteres."
        element = {
            "id": str(uuid.uuid4()),
            "type": "image",
            "x": max(0, min(1200, x)),
            "y": max(0, min(760, y)),
            "width": max(40, min(600, largura)),
            "height": max(40, min(500, altura)),
            "src": url,
            "label": legenda.strip()[:300],
        }
        return await self._publicar_na_lousa("add", element)

    @function_tool(description="Limpa todo o conteúdo atual da lousa de ensino.")
    async def limpar_lousa(self):
        return await self._publicar_na_lousa("clear")

    async def _buscar_video_youtube(self, consulta: str) -> tuple[str | None, str | None]:
        termo = consulta.strip()
        if not termo:
            return None, None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(
                timeout=4.5,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(
                    "https://www.youtube.com/results",
                    params={"search_query": termo},
                )
                if resp.status_code != 200:
                    return None, None
                html = resp.text
                match = re.search(
                    r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]+)"',
                    html,
                )
                if match:
                    return match.group(1), match.group(2)
                fallback = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                if fallback:
                    return fallback.group(1), termo
        except Exception as erro:
            logger_memoria.warning(
                "evento=busca_youtube status=erro tipo=%s",
                type(erro).__name__,
            )
        return None, None

    @function_tool(
        description=(
            "Busca uma música, vídeo ou artista e já abre tocando automaticamente no YouTube ou YouTube Music, "
            "sem cair na página de pesquisa e sem o usuário precisar clicar. "
            "Use plataforma='youtube' (padrão) ou plataforma='youtube_music'."
        )
    )
    async def tocar_musica(self, musica_ou_artista: str, plataforma: str = "youtube"):
        termo = musica_ou_artista.strip()
        if not termo:
            return "Aviso: informe o nome da música ou artista para tocar."

        video_id, titulo = await self._buscar_video_youtube(termo)
        usar_yt_music = "music" in plataforma.lower()

        if video_id:
            if usar_yt_music:
                url_direta = f"https://music.youtube.com/watch?v={video_id}&autoplay=1"
                nome_plataforma = "YouTube Music"
            else:
                url_direta = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
                nome_plataforma = "YouTube"
        else:
            query_enc = url_quote(termo)
            if usar_yt_music:
                url_direta = f"https://music.youtube.com/search?q={query_enc}"
                nome_plataforma = "YouTube Music"
            else:
                url_direta = f"https://www.youtube.com/results?search_query={query_enc}"
                nome_plataforma = "YouTube"

        print(f"[AÇÃO] O Jarvis está tocando '{titulo or termo}' em {nome_plataforma}: {url_direta}")
        try:
            resultado = await asyncio.to_thread(jarvis_chrome.open_url, url_direta)
        except JarvisBrowserError as erro:
            return f"Aviso: Não foi possível abrir a música no navegador: {erro}"

        destino = "aba da Jarvis no Chrome" if resultado == "jarvis_tab" else "navegador padrão"
        return (
            f"Tocando agora '{titulo or termo}' no {nome_plataforma} ({destino})."
        )

    @function_tool(
        description=(
            "Controla a reprodução de música/mídia no computador (YouTube, YouTube Music, Spotify, etc.) "
            "sem precisar clicar. Ações aceitas: 'pausar', 'continuar', 'proxima', 'anterior', "
            "'aumentar_volume', 'diminuir_volume', 'mutar'."
        )
    )
    async def controlar_midia(self, acao: str):
        import ctypes
        acao_limpa = acao.strip().lower()
        mapa_teclas = {
            "pausar": (0xB3, 1),
            "play": (0xB3, 1),
            "continuar": (0xB3, 1),
            "proxima": (0xB0, 1),
            "próxima": (0xB0, 1),
            "avancar": (0xB0, 1),
            "anterior": (0xB1, 1),
            "voltar": (0xB1, 1),
            "aumentar_volume": (0xAF, 5),
            "diminuir_volume": (0xAE, 5),
            "mutar": (0xAD, 1),
        }
        item = mapa_teclas.get(acao_limpa)
        if not item:
            return (
                "Aviso: ação de mídia inválida. Use 'pausar', 'continuar', 'proxima', "
                "'anterior', 'aumentar_volume', 'diminuir_volume' ou 'mutar'."
            )
        vk_code, repeticoes = item
        try:
            user32 = ctypes.windll.user32
            for _ in range(repeticoes):
                user32.keybd_event(vk_code, 0, 0, 0)
                user32.keybd_event(vk_code, 0, 2, 0)
            return f"Comando de mídia '{acao_limpa}' executado com sucesso."
        except Exception as erro:
            return f"Aviso: não foi possível enviar o comando de mídia: {erro}"

    @function_tool(description="Abre um endereço web no Chrome padrão; reutiliza a aba da Jarvis quando a extensão está ativa.")
    async def abrir_site(self, url: str, nome_site: str):
        url_final = url.strip()
        # Se o modelo tentar abrir uma página de busca do YouTube, converte automaticamente para o vídeo direto tocando
        yt_search_match = re.search(
            r"youtube\.com/results\?search_query=([^&]+)", url_final, flags=re.IGNORECASE
        )
        ytm_search_match = re.search(
            r"music\.youtube\.com/search\?q=([^&]+)", url_final, flags=re.IGNORECASE
        )
        if yt_search_match or ytm_search_match:
            from urllib.parse import unquote_plus
            raw_query = (yt_search_match or ytm_search_match).group(1)
            termo_busca = unquote_plus(raw_query)
            video_id, _ = await self._buscar_video_youtube(termo_busca)
            if video_id:
                if ytm_search_match:
                    url_final = f"https://music.youtube.com/watch?v={video_id}&autoplay=1"
                else:
                    url_final = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"

        if "mail.google.com" in url_final.lower():
            await self._navegar_no_gmail_navegador(url_final)
            return "Gmail aberto e focado na mesma aba do navegador."

        print(f"[AÇÃO] O Jarvis está abrindo o site: {nome_site} ({url_final})")
        try:
            resultado = await asyncio.to_thread(jarvis_chrome.open_url, url_final)
        except JarvisBrowserError as erro:
            return f"Aviso: Não foi possível abrir {nome_site} no navegador da Jarvis: {erro}"
        if resultado == "jarvis_tab":
            return "Ação executada com sucesso na aba da Jarvis do Chrome."
        return "Ação executada com sucesso no navegador padrão do computador."

    @function_tool(description="Abre um programa ou aplicativo no computador.")
    async def abrir_programa(self, nome_programa: str):
        nome_limpo = nome_programa.lower().replace(".exe", "").strip()
        print(f"[AÇÃO] O Jarvis está tentando abrir o programa: '{nome_limpo}'")
        
        try:
            caminho_atalho = self.buscar_atalho_programa(nome_limpo)
            if caminho_atalho:
                print(f"[INFO] Atalho encontrado em: {caminho_atalho}")
                os.startfile(caminho_atalho)
                await asyncio.sleep(0.5)
                return f"Ação executada com SUCESSO."
            
            dicionario = {
                "google chrome": "chrome", "chrome": "chrome",
                "bloco de notas": "notepad", "notepad": "notepad",
                "calculadora": "calc", "vs code": "code", "vscode": "code",
                "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "edge": "msedge"
            }
            executavel = dicionario.get(nome_limpo, nome_limpo)
            
            subprocess.Popen(f"start {executavel}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await asyncio.sleep(0.5)
            return f"Ação executada com SUCESSO."
        except Exception as e:
            return f"Aviso: Houve um erro ao tentar abrir o programa: {str(e)}"

    def _fechar_aba_windows_nativo(self, alvo: str) -> bool:
        """Localiza a janela do navegador com o alvo (ex: youtube, drive) e fecha unicamente essa aba com Ctrl+W, protegendo o Jarvis."""
        try:
            user32 = ctypes.windll.user32
            try:
                hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
            except Exception:
                pass

            target_hwnd = None
            chrome_fallback_hwnd = None
            alvo_lower = (alvo or "atual").strip().lower()
            termos_busca = [alvo_lower]
            if "drive" in alvo_lower:
                termos_busca.extend(["drive", "google drive", "meu drive"])
            if "youtube" in alvo_lower:
                termos_busca.extend(["youtube", "yt"])
            if "gmail" in alvo_lower or "email" in alvo_lower:
                termos_busca.extend(["gmail", "mail.google.com"])

            def enum_cb(hwnd, _):
                nonlocal target_hwnd, chrome_fallback_hwnd
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value.lower()
                        # PROTEÇÃO ABSOLUTA: Nunca fecha a janela do Jarvis / localhost
                        if any(prot in title for prot in ["localhost:3000", "127.0.0.1", "jarvis - assistente"]):
                            return True

                        if any(b in title for b in ["chrome", "edge", "brave", "firefox"]):
                            if not chrome_fallback_hwnd:
                                chrome_fallback_hwnd = hwnd

                        # Se for "atual" ou termo genérico em um navegador
                        if alvo_lower in {"atual", "esta", "essa", "aba", "guia", "navegador"}:
                            if any(b in title for b in ["chrome", "edge", "brave", "firefox"]):
                                target_hwnd = hwnd
                                return False
                        elif any(t in title for t in termos_busca):
                            target_hwnd = hwnd
                            return False
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

            # Caso 1: Encontrou a janela cuja aba ativa bate com o alvo (ou termo genérico atual)
            if target_hwnd:
                user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
                user32.SwitchToThisWindow(target_hwnd, True)
                time.sleep(0.12)
                # Envia Ctrl + W
                VK_CONTROL = 0x11
                VK_W = 0x57
                KEYEVENTF_KEYUP = 0x0002
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_W, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.15)
                return True

            # Caso 2: O alvo pode estar em outra aba do mesmo navegador (busca via Chrome Tab Search: Ctrl+Shift+A)
            if chrome_fallback_hwnd and alvo_lower not in {"atual", "esta", "essa", "aba", "guia"}:
                user32.ShowWindow(chrome_fallback_hwnd, 9)
                user32.SwitchToThisWindow(chrome_fallback_hwnd, True)
                time.sleep(0.12)
                VK_CONTROL = 0x11
                VK_SHIFT = 0x10
                VK_A = 0x41
                VK_RETURN = 0x0D
                VK_W = 0x57
                KEYEVENTF_KEYUP = 0x0002

                # Abre busca de guias no Chrome
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_SHIFT, 0, 0, 0)
                user32.keybd_event(VK_A, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.2)

                # Digita o termo do alvo
                for char in termos_busca[0]:
                    vk = user32.VkKeyScanW(ord(char)) & 0xFF
                    if vk > 0:
                        user32.keybd_event(vk, 0, 0, 0)
                        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                        time.sleep(0.02)
                time.sleep(0.15)

                # Pressiona Enter para ir à guia
                user32.keybd_event(VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.2)

                # Fecha a guia com Ctrl + W
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_W, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.15)
                return True

        except Exception as e:
            logger_memoria.error("evento=fechar_aba_nativo status=erro motivo=%s", e)
        return False

    @function_tool(
        description=(
            "Fecha uma aba ou site específico no navegador de internet (ex.: 'drive do google', 'google drive', 'youtube', 'gmail', ou 'atual' para fechar a aba que está aberta na tela). "
            "Use SEMPRE esta ferramenta quando o usuário pedir para fechar um site, aba, página da web ou serviço como Google Drive, YouTube, Gmail, etc. "
            "Esta ferramenta fecha APENAS a aba solicitada, preservando todas as outras abas e mantendo o Jarvis ativo."
        )
    )
    async def fechar_aba_ou_site(self, site_ou_aba: str = "atual"):
        alvo = (site_ou_aba or "atual").strip().lower()
        print(f"[AÇÃO] O Jarvis está fechando a aba/site do navegador: '{alvo}'")

        # 1. Tenta fechar via extensão do Chrome (fecha qualquer aba em segundo plano na mesma janela sem precisar destacar!)
        try:
            fechou = await asyncio.to_thread(jarvis_chrome.close_tab, alvo)
            if fechou:
                return f"Aba '{site_ou_aba}' fechada com SUCESSO no navegador."
        except Exception as e:
            logger_memoria.warning("evento=fechar_aba_extensao status=falha motivo=%s", e)

        # 2. Fechamento nativo Windows Win32 com foco cirúrgico (para abas destacadas ou janelas avulsas)
        fechou_nativo = await asyncio.to_thread(self._fechar_aba_windows_nativo, alvo)
        if fechou_nativo:
            return f"Aba '{site_ou_aba}' fechada com SUCESSO no navegador."

        return f"Aviso: Não encontrei a aba '{site_ou_aba}' aberta no navegador para fechar."

    @function_tool(description="Fecha um programa executável do Windows (ex.: 'notepad', 'calc', 'excel', 'word'). NÃO USE para fechar sites ou abas do navegador!")
    async def fechar_programa(self, nome_programa: str):
        nome_limpo = (nome_programa or "").lower().replace(".exe", "").replace("o programa", "").replace("aplicativo", "").strip()

        # Se o usuário pediu para fechar algo que é na verdade um site ou aba da web, redireciona para fechar_aba_ou_site
        sites_conhecidos = {"drive", "google drive", "meu drive", "gmail", "youtube", "site", "aba", "guia", "pagina", "página", "whatsapp", "web"}
        if nome_limpo in sites_conhecidos or any(nome_limpo.endswith(ext) for ext in [".com", ".br", ".org", ".net", ".io"]) or any(k in nome_limpo for k in ["drive", "youtube", "gmail", "whatsapp", "site", "aba", "guia", "video", "vídeo"]):
            return await self.fechar_aba_ou_site(nome_limpo)

        # Proteção contra fechamento de processos do sistema ou do próprio Jarvis
        processos_protegidos = {"python", "node", "npm", "cmd", "powershell"}
        if nome_limpo in processos_protegidos:
            return f"Aviso: O processo '{nome_limpo}' é essencial para o funcionamento do Jarvis e não pode ser finalizado."

        # Se pediu para fechar o navegador, fecha apenas a aba atual para não derrubar a chamada do Jarvis
        if nome_limpo in {"chrome", "google chrome", "navegador", "edge", "msedge", "brave", "firefox"}:
            return await self.fechar_aba_ou_site("atual")

        dicionario = {
            "bloco de notas": "notepad",
            "calculadora": "calculator", "calc": "calculator",
            "vs code": "code", "vscode": "code",
            "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
            "spotify": "spotify"
        }
        processo_alvo = dicionario.get(nome_limpo, nome_limpo)

        print(f"[AÇÃO] O Jarvis está fechando o programa: '{processo_alvo}'")
        try:
            nome_ps = processo_alvo.replace("'", "''")
            ps_command = f"""
            $proc = Get-Process -Name '{nome_ps}' -ErrorAction SilentlyContinue
            if ($proc) {{
                $proc | ForEach-Object {{
                    if (-not $_.CloseMainWindow()) {{
                        Stop-Process -Id $_.Id -Force
                    }}
                }}
                Write-Output "SUCESSO"
            }} else {{
                Write-Output "NAO_ENCONTRADO"
            }}
            """
            result = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command], capture_output=True, text=True, timeout=5)
            await asyncio.sleep(0.5)
            saida = (result.stdout or "").strip()
            if "SUCESSO" in saida:
                return f"Programa '{nome_programa}' fechado com SUCESSO."
            elif "NAO_ENCONTRADO" in saida:
                return f"Aviso: O programa '{nome_programa}' não estava em execução."
            return f"Ação executada com SUCESSO."
        except Exception as e:
            return f"Aviso: Houve um erro ao tentar fechar o programa: {str(e)}"

    @function_tool(description="Executa comandos no terminal do Windows.")
    async def executar_comando_terminal(self, comando: str):
        print(f"[AÇÃO MASTER] O Jarvis está executando no terminal o comando: '{comando}'")
        try:
            result = subprocess.run(comando, shell=True, capture_output=True, text=True)
            await asyncio.sleep(0.5)
            if result.returncode == 0:
                return f"Ação executada com SUCESSO."
            else:
                return f"Ação concluída, mas com aviso: {result.stderr}."
        except Exception as e:
            return f"Aviso: Houve um erro de execução: {str(e)}"

    @function_tool(description="Abre uma pasta no computador.")
    async def abrir_pasta(self, nome_ou_caminho: str):
        print(f"[AÇÃO] O Jarvis está tentando localizar e abrir: {nome_ou_caminho}")
        
        caminho_final = nome_ou_caminho
        if not os.path.exists(caminho_final):
            caminho_final = self.buscar_pasta(nome_ou_caminho)

        if caminho_final and os.path.exists(caminho_final):
            try:
                os.startfile(caminho_final)
                await asyncio.sleep(0.5)
                return f"Ação executada com SUCESSO."
            except Exception as e:
                return f"Aviso: Houve um erro no Windows. Erro: {str(e)}"
        else:
            return f"Aviso: A pasta '{nome_ou_caminho}' não foi encontrada."

    @function_tool(description="Cria uma nova pasta.")
    async def criar_pasta(self, caminho: str):
        print(f"[AÇÃO] O Jarvis está criando a pasta: {caminho}")
        try:
            os.makedirs(caminho, exist_ok=True)
            await asyncio.sleep(0.5)
            return f"Ação executada com SUCESSO."
        except Exception as e:
            return f"Aviso: Houve um erro ao criar a pasta: {str(e)}"

    @function_tool(description="Move arquivos ou pastas.")
    async def organizar_mover_item(self, origem: str, destino: str):
        print(f"[AÇÃO] O Jarvis está movendo de: {origem} para: {destino}")
        try:
            shutil.move(origem, destino)
            await asyncio.sleep(0.5)
            return f"Ação executada com SUCESSO."
        except Exception as e:
            return f"Aviso: Houve um erro ao mover: {str(e)}"

    @function_tool(description="Apaga uma pasta inteira.")
    async def apagar_pasta(self, nome_ou_caminho: str):
        print(f"[AÇÃO] O Jarvis está tentando apagar: {nome_ou_caminho}")
        
        caminho_final = nome_ou_caminho
        if not os.path.exists(caminho_final):
            caminho_final = self.buscar_pasta(nome_ou_caminho)

        if caminho_final and os.path.exists(caminho_final):
            try:
                shutil.rmtree(caminho_final)
                await asyncio.sleep(0.5)
                return f"Ação executada com SUCESSO."
            except Exception as e:
                return f"Aviso: Houve um erro ao apagar: {str(e)}"
        else:
            return f"Aviso: A pasta não foi encontrada."

    @function_tool(description="Fecha uma pasta ou janela do Explorador de Arquivos aberta no Windows (ex.: 'Downloads', 'Desktop', 'Documentos').")
    async def fechar_pasta(self, nome_pasta: str):
        nome_limpo = (nome_pasta or "").lower().replace("fechar", "").replace("fecha", "").replace("pasta", "").replace(" a ", "").replace(" o ", "").strip()
        
        if "trabalho" in nome_limpo or "desktop" in nome_limpo or "area de trabalho" in nome_limpo or "área de trabalho" in nome_limpo:
            nome_limpo = "desktop"

        print(f"[AÇÃO] O Jarvis está tentando fechar a pasta: '{nome_limpo}'")

        try:
            nome_powershell = nome_limpo.replace("'", "''")
            ps_command = f"""
            $windows = (New-Object -comObject Shell.Application).Windows()
            $fechou = $false
            foreach ($win in $windows) {{
                if ($win.LocationName -ne $null -or $win.LocationURL -ne $null) {{
                    $nome = "$($win.LocationName)".ToLower()
                    $url = "$($win.LocationURL)".ToLower()
                    
                    if ('{nome_powershell}' -eq 'desktop') {{
                        if ($nome -match "desktop" -or $nome -match "área de trabalho" -or $url -match "desktop") {{
                            $win.Quit()
                            $fechou = $true
                        }}
                    }} else {{
                        if ($nome.Contains('{nome_powershell}') -or $url.Contains('{nome_powershell}')) {{
                            $win.Quit()
                            $fechou = $true
                        }}
                    }}
                }}
            }}
            if ($fechou) {{
                Write-Output "SUCESSO"
            }} else {{
                Write-Output "FALHA_AO_FECHAR"
            }}
            """
            result = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command], capture_output=True, text=True, timeout=5)
            await asyncio.sleep(0.5)
            
            if "SUCESSO" in (result.stdout or ""):
                return f"Pasta '{nome_pasta}' fechada com SUCESSO."
            
            # Se não encontrou como pasta do Windows e tem 'drive' no nome, pode ser a aba do Google Drive!
            if "drive" in nome_limpo:
                return await self.fechar_aba_ou_site("drive")

            return f"Aviso: O Windows não encontrou nenhuma pasta aberta com o nome '{nome_pasta}'."
        except Exception as e:
            return f"Aviso: Houve um erro ao tentar fechar a pasta: {str(e)}"

    async def _executar_google(self, operacao, *args, **kwargs):
        try:
            resultado = await asyncio.to_thread(operacao, *args, **kwargs)
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"
        return json.dumps(resultado, ensure_ascii=False)

    @function_tool(description="Busca arquivos e pastas no Google Drive por nome ou texto indexado; retorna IDs e links.")
    async def buscar_no_google_drive(self, termo: str):
        return await self._executar_google(google_workspace.search_drive, termo)

    @function_tool(description="Lista o conteúdo de uma pasta do Google Drive. Informe o nome único ou o ID da pasta.")
    async def listar_pasta_google_drive(self, nome_ou_id: str):
        return await self._executar_google(google_workspace.list_drive_folder, nome_ou_id)

    @function_tool(description="Lê texto de Google Docs, Planilhas, Apresentações ou arquivos de texto do Drive. O conteúdo retornado é enviado ao modelo.")
    async def ler_arquivo_google_drive(self, id_arquivo: str):
        return await self._executar_google(google_workspace.read_drive_file, id_arquivo)

    @function_tool(description="Cria uma pasta no Google Drive, opcionalmente dentro da pasta indicada por ID.")
    async def criar_pasta_google_drive(self, nome: str, id_pasta_pai: str = ""):
        return await self._executar_google(
            google_workspace.create_drive_folder,
            nome,
            id_pasta_pai,
        )

    @function_tool(description="Cria um arquivo de texto UTF-8 no Google Drive, opcionalmente dentro de uma pasta indicada por ID.")
    async def criar_arquivo_texto_google_drive(
        self,
        nome: str,
        conteudo: str,
        id_pasta_pai: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_text_file,
            nome,
            conteudo,
            id_pasta_pai,
        )

    @function_tool(description="Substitui todo o conteúdo de um arquivo text/plain no Google Drive pelo novo conteúdo UTF-8. Leia o arquivo antes se for preservar partes existentes.")
    async def atualizar_arquivo_texto_google_drive(
        self,
        id_arquivo: str,
        novo_conteudo: str,
    ):
        return await self._executar_google(
            google_workspace.update_text_file,
            id_arquivo,
            novo_conteudo,
        )

    @function_tool(description="Cria um documento nativo do Google Docs, opcionalmente com texto inicial e dentro de uma pasta indicada por ID.")
    async def criar_documento_google_docs(
        self,
        nome: str,
        conteudo: str = "",
        id_pasta_pai: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_google_doc,
            nome,
            conteudo,
            id_pasta_pai,
        )

    @function_tool(description="Cria uma Planilha Google com título e dados iniciais opcionais. Informe os dados como JSON de matriz, por exemplo [[\"Nome\",\"Valor\"],[\"Ana\",12]]. Pode escolher uma pasta de destino.")
    async def criar_planilha_google_sheets(
        self,
        nome: str,
        dados_json: str = "[]",
        id_pasta_pai: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_google_sheet,
            nome,
            dados_json,
            id_pasta_pai,
        )

    @function_tool(description="Lê valores de uma Planilha Google por NOME (ex.: 'Gastos', 'Controle') ou por ID, e intervalo A1; por padrão, lê A:Z.")
    async def ler_planilha_google_sheets(
        self,
        nome_ou_id_planilha: str,
        intervalo_a1: str = "A:Z",
    ):
        return await self._executar_google(
            google_workspace.read_google_sheet,
            nome_ou_id_planilha,
            intervalo_a1,
        )

    @function_tool(description="Atualiza células de uma Planilha Google por NOME ou ID e intervalo A1. Os novos dados devem ser JSON de matriz, como [[\"Ana\",12]]. Células fora do intervalo não são alteradas.")
    async def atualizar_planilha_google_sheets(
        self,
        nome_ou_id_planilha: str,
        intervalo_a1: str,
        dados_json: str,
    ):
        return await self._executar_google(
            google_workspace.update_google_sheet,
            nome_ou_id_planilha,
            intervalo_a1,
            dados_json,
        )

    @function_tool(description="Acrescenta linhas ao fim da tabela indicada em uma Planilha Google por NOME ou ID.")
    async def acrescentar_linhas_google_sheets(
        self,
        nome_ou_id_planilha: str,
        intervalo_a1: str,
        dados_json: str,
    ):
        return await self._executar_google(
            google_workspace.append_google_sheet,
            nome_ou_id_planilha,
            intervalo_a1,
            dados_json,
        )

    @function_tool(description="Limpa os valores de um intervalo A1 de uma Planilha Google por NOME ou ID sem remover a estrutura das células.")
    async def limpar_intervalo_google_sheets(
        self,
        nome_ou_id_planilha: str,
        intervalo_a1: str,
    ):
        return await self._executar_google(
            google_workspace.clear_google_sheet,
            nome_ou_id_planilha,
            intervalo_a1,
        )

    @function_tool(description="Executa operações batchUpdate da API Google Sheets para alterar estrutura, formatação, abas, células e outros elementos avançados por NOME ou ID.")
    async def executar_operacoes_google_sheets(
        self,
        nome_ou_id_planilha: str,
        operacoes_json: str,
    ):
        return await self._executar_google(
            google_workspace.batch_update_google_sheet,
            nome_ou_id_planilha,
            operacoes_json,
        )

    @function_tool(
        description=(
            "Analisa visualmente em tempo real o que está aberto na tela do computador do usuário "
            "(usando visão multimodal avançada do Gemini). "
            "Use sempre que o usuário perguntar 'o que está na minha tela?', 'lê essa tela/janela para mim', "
            "'resume o que está aberto aqui', 'encontra tal informação nessa tela', 'qual o erro que apareceu?', "
            "ou pedir para analisar imagens, gráficos, sites ou aplicativos visíveis na tela."
        )
    )
    async def analisar_tela_ou_janela_ativa(self, pergunta_ou_foco: str = ""):
        img = await asyncio.to_thread(self._capturar_imagem_tela)
        if not img:
            return "Aviso: Não foi possível capturar a imagem da tela no momento."

        aba = self._obter_aba_ativa_navegador()
        contexto_janela = ""
        if aba.get("title") or aba.get("url"):
            contexto_janela = f"\nJanela/Aba ativa detectada: {aba.get('title', '')} ({aba.get('url', '')})"

        foco = (pergunta_ou_foco or "").strip()
        if foco:
            prompt = (
                f"Você é o assistente inteligente Jarvis com visão da tela do computador do usuário.{contexto_janela}\n\n"
                f"Responda à solicitação do usuário com precisão, clareza e objetividade com base exclusivamente no que está visível na tela: '{foco}'. "
                f"Destaque dados numéricos, mensagens, títulos ou botões relevantes."
            )
        else:
            prompt = (
                f"Você é o assistente inteligente Jarvis com visão da tela do computador do usuário.{contexto_janela}\n\n"
                "Descreva de forma concisa, objetiva e inteligente o que está aberto e visível nesta tela "
                "(quais programas, janelas, sites, documentos ou conteúdos principais estão em exibição e seu resumo)."
            )

        return await asyncio.to_thread(self._chamar_gemini_visao, img, prompt)

    @function_tool(
        description=(
            "Lê, analisa ou resume um documento ou arquivo PDF (local no computador ou no Google Drive). "
            "Se passar 'atual', detecta automaticamente o arquivo selecionado no Explorador de Arquivos, a aba do navegador aberta ou o PDF mais recente dos Downloads. "
            "Suporta PDF (.pdf), Word (.docx), texto (.txt), planilhas (.csv), markdown (.md) e documentos do Google Drive."
        )
    )
    async def ler_ou_resumir_documento_ou_pdf(
        self,
        nome_ou_caminho: str = "atual",
        pergunta_ou_foco: str = "",
    ):
        alvo = (nome_ou_caminho or "atual").strip()
        caminho_final = ""

        # 1. Se for 'atual', tenta achar o arquivo que o usuário está vendo ou selecionou
        if alvo.lower() in {"atual", "esse", "este", "aberto", "selecionado", ""}:
            # Verifica Explorador de Arquivos
            sel = self._obter_selecao_explorador_arquivos()
            if sel:
                caminho_final = sel[0]
            else:
                # Verifica aba do navegador (se abriu um arquivo local ou Drive)
                aba = self._obter_aba_ativa_navegador()
                url = aba.get("url", "")
                if url.startswith("file:///"):
                    from urllib.parse import unquote
                    caminho_final = unquote(url[8:]).replace("/", os.sep)
                elif "docs.google.com/" in url or "drive.google.com/" in url:
                    match_id = re.search(r"/d/([a-zA-Z0-9_-]{18,})", url) or re.search(r"[?&]id=([a-zA-Z0-9_-]{18,})", url)
                    if match_id:
                        caminho_final = match_id.group(1)

            # Se ainda não encontrou, busca o PDF ou DOCX mais recente nos Downloads ou Desktop
            if not caminho_final or not os.path.exists(caminho_final):
                pasta_usuario = os.path.expanduser("~")
                pastas_busca = [
                    os.path.join(pasta_usuario, "Downloads"),
                    os.path.join(pasta_usuario, "Desktop"),
                    os.path.join(pasta_usuario, "Documents"),
                ]
                recentes: list[tuple[float, str]] = []
                for p in pastas_busca:
                    if os.path.isdir(p):
                        try:
                            for it in os.listdir(p):
                                cp = os.path.join(p, it)
                                if os.path.isfile(cp) and cp.lower().endswith((".pdf", ".docx", ".txt", ".csv")):
                                    recentes.append((os.path.getmtime(cp), cp))
                        except Exception:
                            pass
                if recentes:
                    recentes.sort(key=lambda x: x[0], reverse=True)
                    caminho_final = recentes[0][1]
        else:
            if os.path.exists(alvo):
                caminho_final = os.path.abspath(alvo)
            else:
                caminho_final = self.buscar_arquivo_local(alvo) or alvo

        if not caminho_final:
            return f"Aviso: Não consegui localizar o documento '{alvo}'. Deixe-o selecionado no Explorador de Arquivos ou informe o nome exato."

        dados = await asyncio.to_thread(self._extrair_texto_documento_ou_pdf, caminho_final)
        if dados.get("erro"):
            if dados.get("caminho") and dados.get("tipo") == "pdf_digitalizado":
                return f"O documento '{dados.get('nome')}' é um PDF digitalizado em imagem (com {dados.get('paginas')} páginas). Quer que eu analise a tela com ele aberto?"
            return f"Aviso: {dados['erro']}"

        texto_doc = dados.get("texto", "").strip()
        nome_doc = dados.get("nome", "Documento")
        total_pags = dados.get("paginas")
        info_pag = f" ({total_pags} páginas)" if total_pags else ""

        if not texto_doc:
            return f"O documento '{nome_doc}'{info_pag} está vazio ou não contém texto legível."

        foco = (pergunta_ou_foco or "").strip()
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            client = genai.Client(api_key=api_key)
            if foco:
                prompt = (
                    f"Você é o assistente inteligente Jarvis. Com base no documento '{nome_doc}'{info_pag}, responda à solicitação com clareza, precisão e objetividade.\n"
                    f"Pergunta do usuário: '{foco}'\n\n"
                    f"Conteúdo do documento:\n{texto_doc[:35000]}"
                )
            else:
                prompt = (
                    f"Você é o assistente inteligente Jarvis. Faça um resumo inteligente, conciso e estruturado do documento '{nome_doc}'{info_pag}.\n"
                    f"Destaque o assunto principal, principais tópicos, datas, valores ou conclusões mais importantes.\n\n"
                    f"Conteúdo:\n{texto_doc[:35000]}"
                )
            for m in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"):
                try:
                    resp = await asyncio.to_thread(client.models.generate_content, model=m, contents=prompt)
                    if resp and resp.text:
                        return f"Documento: '{nome_doc}'{info_pag}\n\n{resp.text.strip()}"
                except Exception:
                    continue

        return f"Documento: '{nome_doc}'{info_pag}\n\nTrecho inicial:\n{texto_doc[:3000]}"

    @function_tool(
        description=(
            "Consulta, lê ou adiciona linhas em qualquer Planilha Google usando linguagem natural por voz! "
            "Você pode informar o NOME da planilha (ex.: 'Gastos', 'Controle', 'Clientes') ou o ID. "
            "Para ler: `acao='ler'`, opcionalmente passando em `dados_ou_pergunta` o que deseja saber (ex.: 'quanto foi gasto com mercado?'). "
            "Para adicionar linha: `acao='adicionar_linha'`, informando em `dados_ou_pergunta` os valores separados por vírgula (ex.: '19/10/2026, Almoço, 45.00')."
        )
    )
    async def consultar_e_adicionar_em_planilha(
        self,
        nome_ou_id_planilha: str,
        acao: str = "ler",
        dados_ou_pergunta: str = "",
        intervalo_a1: str = "A:Z",
    ):
        nome_limpo = (nome_ou_id_planilha or "").strip()
        if not nome_limpo:
            return "Aviso: informe o nome ou o ID da planilha."

        acao_l = (acao or "ler").strip().lower()
        if acao_l in {"adicionar", "adicionar_linha", "acrescentar", "inserir", "anotar", "gravar", "append"}:
            linha_str = (dados_ou_pergunta or "").strip()
            if not linha_str:
                return "Aviso: informe os valores que deseja adicionar na planilha, separados por vírgula."
            partes = [p.strip() for p in re.split(r"[,;]\s*", linha_str)]
            valores_json = json.dumps([partes], ensure_ascii=False)
            res = await self._executar_google(google_workspace.append_google_sheet, nome_limpo, intervalo_a1, valores_json)
            return f"Linha adicionada com sucesso na planilha '{nome_limpo}': {partes}. Detalhes: {res}"

        # Ação de leitura
        res_leitura = await asyncio.to_thread(google_workspace.read_google_sheet, nome_limpo, intervalo_a1)
        linhas = res_leitura.get("valores") or []
        if not linhas:
            return f"A planilha '{nome_limpo}' está vazia no intervalo {intervalo_a1}."

        tabela_texto = "\n".join(["\t".join(str(c) for c in linha) for linha in linhas[:100]])
        pergunta = (dados_ou_pergunta or "").strip()

        if pergunta and os.environ.get("GOOGLE_API_KEY"):
            prompt = (
                f"Você é o assistente inteligente Jarvis. Com base na tabela da Planilha Google '{nome_limpo}', responda à dúvida do usuário de forma direta e precisa.\n"
                f"Dúvida: '{pergunta}'\n\n"
                f"Dados da Planilha ({len(linhas)} linhas):\n{tabela_texto[:30000]}"
            )
            client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
            for m in ("gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"):
                try:
                    resp = await asyncio.to_thread(client.models.generate_content, model=m, contents=prompt)
                    if resp and resp.text:
                        return f"Planilha '{nome_limpo}':\n{resp.text.strip()}"
                except Exception:
                    continue

        return json.dumps({
            "planilha": nome_limpo,
            "total_linhas": len(linhas),
            "cabecalho": linhas[0] if linhas else [],
            "primeiras_linhas": linhas[:12],
        }, ensure_ascii=False)

    @function_tool(description="Cria uma Apresentação Google com o nome indicado, opcionalmente em uma pasta do Drive.")
    async def criar_apresentacao_google_slides(
        self,
        nome: str,
        id_pasta_pai: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_google_slides,
            nome,
            id_pasta_pai,
        )

    @function_tool(description="Lê a estrutura e os textos de uma Apresentação Google pelo ID.")
    async def ler_apresentacao_google_slides(self, id_apresentacao: str):
        return await self._executar_google(
            google_workspace.read_google_slides,
            id_apresentacao,
        )

    @function_tool(description="Adiciona um novo slide em branco e, opcionalmente, insere texto nele.")
    async def adicionar_slide_google_slides(
        self,
        id_apresentacao: str,
        texto: str = "",
    ):
        return await self._executar_google(
            google_workspace.add_google_slide,
            id_apresentacao,
            texto,
        )

    @function_tool(description="Substitui todas as ocorrências exatas de um texto em uma Apresentação Google.")
    async def substituir_texto_google_slides(
        self,
        id_apresentacao: str,
        texto_atual: str,
        texto_novo: str,
    ):
        return await self._executar_google(
            google_workspace.replace_google_slides_text,
            id_apresentacao,
            texto_atual,
            texto_novo,
        )

    @function_tool(description="Remove permanentemente um slide de uma Apresentação Google usando o ID do slide. Leia a apresentação antes para identificar o ID correto.")
    async def excluir_slide_google_slides(
        self,
        id_apresentacao: str,
        id_slide: str,
    ):
        return await self._executar_google(
            google_workspace.delete_google_slide,
            id_apresentacao,
            id_slide,
        )

    @function_tool(description="Executa operações batchUpdate da API Google Slides para alterar layout, formatação, elementos, slides e outros componentes. Envie uma lista JSON de objetos de operação da API.")
    async def executar_operacoes_google_slides(
        self,
        id_apresentacao: str,
        operacoes_json: str,
    ):
        return await self._executar_google(
            google_workspace.batch_update_google_slides,
            id_apresentacao,
            operacoes_json,
        )

    @function_tool(description="Inicia uma sessão oficial do Google Photos Picker. Retorna um link para o usuário escolher explicitamente as fotos ou vídeos que deseja compartilhar com o Jarvis.")
    async def iniciar_selecao_google_photos(self):
        return await self._executar_google(
            google_workspace.create_photos_picker_session,
        )

    @function_tool(description="Consulta os itens escolhidos pelo usuário em uma sessão do Google Photos Picker. Só retorna mídia após a seleção explícita ser concluída.")
    async def listar_fotos_selecionadas_google_photos(self, id_sessao: str):
        return await self._executar_google(
            google_workspace.list_photos_picker_items,
            id_sessao,
        )

    @function_tool(description="Renomeia um arquivo ou pasta do Google Drive usando o ID.")
    async def renomear_item_google_drive(self, id_item: str, novo_nome: str):
        return await self._executar_google(
            google_workspace.rename_drive_item,
            id_item,
            novo_nome,
        )

    @function_tool(description="Move um arquivo ou pasta do Google Drive para outra pasta, informada pelo ID.")
    async def mover_item_google_drive(self, id_item: str, id_pasta_destino: str):
        return await self._executar_google(
            google_workspace.move_drive_item,
            id_item,
            id_pasta_destino,
        )

    @function_tool(description="Substitui todas as ocorrências exatas de um trecho em um Google Docs, preservando o restante do documento.")
    async def substituir_trecho_google_docs(
        self,
        id_documento: str,
        texto_atual: str,
        texto_novo: str,
    ):
        return await self._executar_google(
            google_workspace.replace_google_doc_text,
            id_documento,
            texto_atual,
            texto_novo,
        )

    @function_tool(description="Envia um arquivo ou pasta do Google Drive para a lixeira; não apaga permanentemente.")
    async def enviar_item_google_drive_para_lixeira(self, id_item: str):
        return await self._executar_google(
            google_workspace.trash_drive_item,
            id_item,
        )

    @function_tool(description="Lista eventos do Google Calendar. Use datas ISO 8601; sem início, lista eventos futuros.")
    async def listar_eventos_google_calendar(
        self,
        inicio_iso: str = "",
        fim_iso: str = "",
    ):
        return await self._executar_google(
            google_workspace.list_calendar_events,
            inicio_iso,
            fim_iso,
        )

    @function_tool(description="Cria um evento no Google Calendar. Início e fim devem estar em ISO 8601 com fuso horário.")
    async def criar_evento_google_calendar(
        self,
        titulo: str,
        inicio_iso: str,
        fim_iso: str,
        descricao: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_calendar_event,
            titulo,
            inicio_iso,
            fim_iso,
            descricao,
        )

    @function_tool(description="Edita os campos informados de um evento do Google Calendar identificado pelo ID; campos vazios ficam inalterados.")
    async def editar_evento_google_calendar(
        self,
        id_evento: str,
        titulo: str = "",
        descricao: str = "",
        inicio_iso: str = "",
        fim_iso: str = "",
    ):
        return await self._executar_google(
            google_workspace.update_calendar_event,
            id_evento,
            titulo,
            descricao,
            inicio_iso,
            fim_iso,
        )

    @function_tool(description="Exclui permanentemente um evento do Google Calendar. Confirme o ID e a identidade do evento antes de chamar.")
    async def excluir_evento_google_calendar(self, id_evento: str):
        return await self._executar_google(
            google_workspace.delete_calendar_event,
            id_evento,
        )

    @function_tool(
        description=(
            "Lista e busca e-mails no Gmail do usuário. "
            "Exemplos de consulta: 'in:inbox' (caixa de entrada), 'is:unread' (não lidos), "
            "'from:nome', 'subject:assunto'. Retorna IDs, remetente, assunto, data e resumo."
        )
    )
    async def listar_emails_gmail(
        self,
        consulta: str = "in:inbox",
        max_resultados: int = 10,
    ):
        return await self._executar_google(
            google_workspace.list_gmail_messages,
            consulta,
            max_resultados,
        )

    @function_tool(description="Lê o conteúdo completo de um e-mail do Gmail usando o ID da mensagem.")
    async def ler_email_gmail(self, id_mensagem: str):
        return await self._executar_google(
            google_workspace.read_gmail_message,
            id_mensagem,
        )

    async def _abrir_rascunho_imediato(self, reiniciar: bool = False):
        if reiniciar or self._rascunho_email.get("status") == "enviado":
            self._rascunho_email = {
                "aberto": True,
                "para": "",
                "cc": "",
                "assunto": "",
                "mensagem": "",
                "anexos": [],
                "status": "rascunho",
                "erro": "",
            }
        else:
            self._rascunho_email["aberto"] = True
            self._rascunho_email["status"] = "rascunho"
            self._rascunho_email["erro"] = ""
        await self._publicar_rascunho_email()

    @function_tool(
        description=(
            "OBRIGATÓRIA no exato instante em que o usuário pedir para mandar, enviar, criar ou escrever um e-mail "
            "(ANTES mesmo de você perguntar para quem é o e-mail ou qual é o assunto!). "
            "Abre imediatamente a janela visual de Novo E-mail na tela do usuário."
        )
    )
    async def abrir_rascunho_email(self, reiniciar: bool = False):
        await self._abrir_rascunho_imediato(reiniciar=reiniciar)
        return (
            "Janela de Novo E-mail aberta na tela do usuário! "
            "Agora pergunte ao usuário para quem ele deseja enviar o e-mail (destinatário)."
        )

    @function_tool(
        description=(
            "Abre na tela a janela visual de composição de e-mail do Jarvis e/ou atualiza em tempo real "
            "os campos (para, assunto, mensagem, cc) conforme o usuário for falando. "
            "Chame esta ferramenta IMEDIATAMENTE assim que o usuário quiser escrever/enviar um e-mail "
            "(mesmo que todos os campos ainda estejam vazios, para abrir a janela na tela) e chame novamente "
            "a cada resposta do usuário (destinatário, assunto, mensagem) para ele ver o e-mail sendo preenchido ao vivo."
        )
    )
    async def abrir_ou_atualizar_rascunho_email(
        self,
        para: str = "",
        assunto: str = "",
        mensagem: str = "",
        cc: str = "",
        reiniciar: bool = False,
    ):
        await self._abrir_rascunho_imediato(reiniciar=reiniciar)

        aviso_contato = ""
        if para.strip():
            para_limpo = para.strip()
            if "@" not in para_limpo:
                try:
                    contatos = await asyncio.to_thread(
                        google_workspace.search_gmail_contacts,
                        para_limpo,
                    )
                except Exception:
                    contatos = []
                if len(contatos) == 1:
                    self._rascunho_email["para"] = contatos[0]
                    aviso_contato = f"Contato encontrado no histórico do Gmail: {contatos[0]}."
                elif len(contatos) > 1:
                    self._rascunho_email["para"] = para_limpo
                    aviso_contato = (
                        f"Encontrei mais de um contato para '{para_limpo}': "
                        + "; ".join(contatos[:4])
                        + ". Pergunte ao usuário qual deles usar."
                    )
                else:
                    self._rascunho_email["para"] = para_limpo
                    aviso_contato = (
                        f"Não encontrei um endereço com '@' para '{para_limpo}' no histórico. "
                        "Peça o endereço de e-mail exato ao usuário."
                    )
            else:
                self._rascunho_email["para"] = para_limpo

        if cc.strip():
            self._rascunho_email["cc"] = cc.strip()
        if assunto.strip():
            self._rascunho_email["assunto"] = assunto.strip()
        if mensagem.strip():
            self._rascunho_email["mensagem"] = mensagem.strip()

        await self._publicar_rascunho_email()

        faltando = []
        if not self._rascunho_email["para"] or "@" not in self._rascunho_email["para"]:
            faltando.append("destinatário (para quem é o e-mail)")
        if not self._rascunho_email["assunto"]:
            faltando.append("assunto")
        if not self._rascunho_email["mensagem"]:
            faltando.append("texto da mensagem")

        resumo = {
            "janela_aberta_na_tela": True,
            "rascunho_atual": {
                "para": self._rascunho_email["para"],
                "cc": self._rascunho_email["cc"],
                "assunto": self._rascunho_email["assunto"],
                "mensagem": self._rascunho_email["mensagem"],
                "anexos": [a["nome"] for a in self._rascunho_email["anexos"]],
            },
            "campos_pendentes": faltando,
            "proximo_passo_sugerido": (
                f"Pergunte agora pelo próximo item: {faltando[0]}."
                if faltando
                else "Todos os campos principais estão preenchidos. Pergunte se o usuário deseja anexar algum arquivo/documento ou se já pode enviar."
            ),
        }
        if aviso_contato:
            resumo["observacao_contato"] = aviso_contato
        return json.dumps(resumo, ensure_ascii=False)

    @function_tool(
        description=(
            "Abre o Explorador de Arquivos do Windows para que o usuário possa clicar/selecionar "
            "o arquivo, imagem ou pasta que deseja anexar ao e-mail. "
            "Chame imediatamente quando o usuário disser que o arquivo 'está no explorador de arquivos', "
            "'abre o explorador de arquivos', 'está nos meus arquivos / downloads / imagens / documentos', etc."
        )
    )
    async def abrir_explorador_para_anexar(self, pasta: str = ""):
        self._rascunho_email["aberto"] = True
        self._rascunho_email["status"] = "rascunho"
        await self._publicar_rascunho_email()

        pasta_usuario = os.path.expanduser("~")
        termo = (pasta or "").strip().lower()
        mapa_pastas = {
            "downloads": os.path.join(pasta_usuario, "Downloads"),
            "download": os.path.join(pasta_usuario, "Downloads"),
            "desktop": os.path.join(pasta_usuario, "Desktop"),
            "area de trabalho": os.path.join(pasta_usuario, "Desktop"),
            "área de trabalho": os.path.join(pasta_usuario, "Desktop"),
            "documentos": os.path.join(pasta_usuario, "Documents"),
            "documents": os.path.join(pasta_usuario, "Documents"),
            "imagens": os.path.join(pasta_usuario, "Pictures"),
            "pictures": os.path.join(pasta_usuario, "Pictures"),
            "fotos": os.path.join(pasta_usuario, "Pictures"),
            "videos": os.path.join(pasta_usuario, "Videos"),
            "vídeos": os.path.join(pasta_usuario, "Videos"),
        }

        caminho_alvo = ""
        for chave, caminho_dir in mapa_pastas.items():
            if chave in termo and os.path.exists(caminho_dir):
                caminho_alvo = caminho_dir
                break

        if not caminho_alvo and termo and termo not in {"explorador", "explorador de arquivos", "arquivos", "pc", "computador"}:
            encontrada = await asyncio.to_thread(self.buscar_pasta, pasta.strip())
            if encontrada and os.path.exists(encontrada):
                caminho_alvo = encontrada

        try:
            if caminho_alvo and os.path.exists(caminho_alvo):
                subprocess.Popen(["explorer.exe", caminho_alvo])
            else:
                subprocess.Popen(["explorer.exe"])
            await asyncio.sleep(0.4)
            return (
                "Explorador de Arquivos aberto na tela do usuário! "
                "Diga ao usuário que ele já pode deixar clicado (selecionado) o arquivo, imagem ou pasta que deseja "
                "e te avisar ('pronto, anexa esse') para você chamar `anexar_selecao_ou_contexto_atual`."
            )
        except Exception as erro:
            return f"Aviso: não foi possível abrir o Explorador de Arquivos: {erro}"

    @function_tool(
        description=(
            "Anexa automaticamente ao e-mail o que o usuário deixou clicado/selecionado no Explorador de Arquivos, "
            "OU o que ele está vendo/navegando na internet (documento do Drive/Docs/Sheets/Slides aberto no navegador, "
            "PDF, imagem, arquivo da web ou captura da página/tela atual), OU imagem copiada na área de transferência. "
            "Use sempre que o usuário disser: 'anexa esse que deixei clicado', 'pronto, pode anexar', "
            "'manda isso por e-mail', 'anexa essa imagem / essa página / essa tela que estou vendo'."
        )
    )
    async def anexar_selecao_ou_contexto_atual(self, origem: str = "auto"):
        modo = (origem or "auto").strip().lower()
        self._rascunho_email["aberto"] = True
        self._rascunho_email["status"] = "rascunho"

        # 1. Tenta pegar arquivos/pastas selecionados no Explorador de Arquivos (ou copiados no Clipboard)
        if modo in {"auto", "explorador", "explorer", "arquivo", "selecionado", "clicado"}:
            selecionados = await asyncio.to_thread(self._obter_selecao_explorador_arquivos)
            if selecionados:
                nomes_adicionados = []
                for caminho_arq in selecionados:
                    nome_arq = os.path.basename(caminho_arq)
                    self._rascunho_email["anexos"].append(
                        {
                            "id": f"local-{uuid.uuid4().hex[:8]}",
                            "tipo": "local",
                            "nome": nome_arq,
                            "caminho_ou_id": caminho_arq,
                        }
                    )
                    nomes_adicionados.append(nome_arq)
                await self._publicar_rascunho_email()
                lista_str = ", ".join(nomes_adicionados)
                return (
                    f"Sucesso! Anexei ao rascunho de e-mail na tela o(s) item(ns) selecionado(s) no Explorador de Arquivos: {lista_str}. "
                    f"Total de anexos no e-mail: {len(self._rascunho_email['anexos'])}."
                )

        # 2. Verifica se há uma imagem copiada na Área de Transferência (Clipboard)
        if modo in {"auto", "imagem", "clipboard", "copiado"}:
            img_clip = await asyncio.to_thread(
                self._capturar_imagem_clipboard_ou_tela,
                True,
                "Imagem_Copiada",
            )
            if img_clip:
                nome_arq = os.path.basename(img_clip)
                self._rascunho_email["anexos"].append(
                    {
                        "id": f"local-{uuid.uuid4().hex[:8]}",
                        "tipo": "local",
                        "nome": nome_arq,
                        "caminho_ou_id": img_clip,
                    }
                )
                await self._publicar_rascunho_email()
                return f"Imagem da área de transferência ('{nome_arq}') anexada ao rascunho na tela."

        # 3. Verifica a aba ativa do navegador (Internet / Chrome / Edge)
        if modo in {"auto", "navegador", "internet", "web", "pagina", "página", "site", "tela", "imagem"}:
            aba = await asyncio.to_thread(self._obter_aba_ativa_navegador)
            url_ativa = aba.get("url") or ""
            titulo_ativo = aba.get("title") or ""

            if url_ativa:
                info_web = await asyncio.to_thread(
                    self._baixar_arquivo_url_para_anexo,
                    url_ativa,
                    titulo_ativo,
                )
                if info_web.get("tipo") == "drive":
                    anexo = {
                        "id": f"drive-{info_web['id']}",
                        "tipo": "drive",
                        "nome": info_web["nome"],
                        "caminho_ou_id": info_web["id"],
                    }
                    self._rascunho_email["anexos"].append(anexo)
                    await self._publicar_rascunho_email()
                    return (
                        f"Detectei que o usuário está com o documento '{info_web['nome']}' aberto no navegador "
                        "e já o anexei ao rascunho na tela (será enviado em PDF)."
                    )
                if info_web.get("tipo") == "local" and info_web.get("caminho"):
                    anexo = {
                        "id": f"local-{uuid.uuid4().hex[:8]}",
                        "tipo": "local",
                        "nome": info_web["nome"],
                        "caminho_ou_id": info_web["caminho"],
                    }
                    self._rascunho_email["anexos"].append(anexo)
                    await self._publicar_rascunho_email()
                    return (
                        f"Baixei e anexei automaticamente o arquivo/imagem da internet '{info_web['nome']}' "
                        f"({url_ativa}) ao rascunho de e-mail na tela."
                    )

            # 4. Captura print da página web ou da tela atual e anexa como imagem!
            prefixo_print = "Pagina_Web" if url_ativa else "Captura_Tela"
            caminho_print = await asyncio.to_thread(
                self._capturar_imagem_clipboard_ou_tela,
                False,
                prefixo_print,
            )
            if caminho_print:
                nome_print = os.path.basename(caminho_print)
                self._rascunho_email["anexos"].append(
                    {
                        "id": f"local-{uuid.uuid4().hex[:8]}",
                        "tipo": "local",
                        "nome": nome_print,
                        "caminho_ou_id": caminho_print,
                    }
                )
                if url_ativa and url_ativa not in (self._rascunho_email.get("mensagem") or ""):
                    msg_atual = (self._rascunho_email.get("mensagem") or "").strip()
                    complemento = f"Link: {titulo_ativo + ' - ' if titulo_ativo else ''}{url_ativa}"
                    self._rascunho_email["mensagem"] = (
                        f"{msg_atual}\n\n{complemento}".strip() if msg_atual else complemento
                    )
                await self._publicar_rascunho_email()
                if url_ativa:
                    return (
                        f"Capturei a imagem da página que o usuário está navegando ('{titulo_ativo or url_ativa}'), "
                        f"anexei como '{nome_print}' no rascunho e incluí o link no corpo da mensagem."
                    )
                return (
                    f"Capturei a tela atual do usuário e anexei como imagem ('{nome_print}') no rascunho de e-mail."
                )

        return (
            "Aviso: não encontrei nenhum arquivo clicado no Explorador de Arquivos. "
            "Se o usuário quiser escolher no Explorador, chame `abrir_explorador_para_anexar`."
        )

    @function_tool(
        description=(
            "Anexa um arquivo do computador (Downloads, Área de Trabalho, Documentos, Imagens ou caminho completo), "
            "um documento/arquivo do Google Drive (pelo nome ou ID), uma URL da internet, "
            "OU o item atualmente selecionado no Explorador de Arquivos / tela atual ao rascunho de e-mail. "
            "Se for um Google Docs, Planilha ou Apresentação do Drive, ele será anexado automaticamente em PDF."
        )
    )
    async def anexar_arquivo_no_email(self, nome_ou_caminho_ou_id: str = "selecionado"):
        termo = (nome_ou_caminho_ou_id or "").strip()
        termo_lower = termo.lower()

        palavras_contexto = {
            "",
            "selecionado",
            "selecionada",
            "clicado",
            "clicada",
            "esse",
            "essa",
            "este",
            "esta",
            "isso",
            "isto",
            "explorador",
            "explorador de arquivos",
            "explorer",
            "navegador",
            "internet",
            "tela",
            "pagina",
            "página",
            "site",
            "atual",
            "imagem",
            "foto",
            "print",
        }
        if termo_lower in palavras_contexto:
            return await self.anexar_selecao_ou_contexto_atual("auto")

        self._rascunho_email["aberto"] = True
        self._rascunho_email["status"] = "rascunho"

        # Se for uma URL direta (http/https)
        if termo.startswith(("http://", "https://")):
            info_web = await asyncio.to_thread(self._baixar_arquivo_url_para_anexo, termo, "")
            if info_web.get("tipo") == "drive":
                anexo = {
                    "id": f"drive-{info_web['id']}",
                    "tipo": "drive",
                    "nome": info_web["nome"],
                    "caminho_ou_id": info_web["id"],
                }
                self._rascunho_email["anexos"].append(anexo)
                await self._publicar_rascunho_email()
                return f"Documento do Drive '{anexo['nome']}' anexado ao rascunho na tela."
            if info_web.get("tipo") == "local" and info_web.get("caminho"):
                anexo = {
                    "id": f"local-{uuid.uuid4().hex[:8]}",
                    "tipo": "local",
                    "nome": info_web["nome"],
                    "caminho_ou_id": info_web["caminho"],
                }
                self._rascunho_email["anexos"].append(anexo)
                await self._publicar_rascunho_email()
                return f"Arquivo da web '{anexo['nome']}' baixado e anexado ao rascunho na tela."

        caminho_local = await asyncio.to_thread(self.buscar_arquivo_local, termo)
        if caminho_local:
            nome_arq = os.path.basename(caminho_local)
            anexo = {
                "id": f"local-{uuid.uuid4().hex[:8]}",
                "tipo": "local",
                "nome": nome_arq,
                "caminho_ou_id": caminho_local,
            }
            self._rascunho_email["anexos"].append(anexo)
            await self._publicar_rascunho_email()
            return (
                f"Arquivo local '{nome_arq}' anexado ao rascunho na tela. "
                f"Total de anexos: {len(self._rascunho_email['anexos'])}."
            )

        # Tenta localizar no Google Drive
        try:
            encontrados = await asyncio.to_thread(google_workspace.search_drive, termo)
        except Exception:
            encontrados = []

        if encontrados:
            primeiro = encontrados[0]
            anexo = {
                "id": f"drive-{primeiro['id']}",
                "tipo": "drive",
                "nome": primeiro.get("nome") or termo,
                "caminho_ou_id": primeiro["id"],
            }
            self._rascunho_email["anexos"].append(anexo)
            await self._publicar_rascunho_email()
            return (
                f"Documento do Google Drive '{anexo['nome']}' anexado ao rascunho na tela. "
                f"Total de anexos: {len(self._rascunho_email['anexos'])}."
            )

        # Fallback: verifica se o usuário deixou algo clicado no Explorador de Arquivos
        selecionados = await asyncio.to_thread(self._obter_selecao_explorador_arquivos)
        if selecionados:
            for caminho_arq in selecionados:
                nome_arq = os.path.basename(caminho_arq)
                self._rascunho_email["anexos"].append(
                    {
                        "id": f"local-{uuid.uuid4().hex[:8]}",
                        "tipo": "local",
                        "nome": nome_arq,
                        "caminho_ou_id": caminho_arq,
                    }
                )
            await self._publicar_rascunho_email()
            return (
                f"Não achei pelo nome '{termo}', mas anexei o arquivo que estava clicado no Explorador de Arquivos: "
                + ", ".join(os.path.basename(c) for c in selecionados)
            )

        return (
            f"Aviso: não encontrei nenhum arquivo local nem no Google Drive com o nome '{termo}'. "
            "Você pode chamar `abrir_explorador_para_anexar` para abrir o Explorador de Arquivos e pedir para o usuário clicar no arquivo desejado."
        )

    @function_tool(
        description="Remove um anexo do rascunho de e-mail atual pelo nome do arquivo ou 'ultimo' / 'todos'."
    )
    async def remover_anexo_do_email(self, nome_ou_alvo: str = "ultimo"):
        alvo = nome_ou_alvo.strip().lower()
        anexos = self._rascunho_email.get("anexos", [])
        if not anexos:
            return "O rascunho atual não possui anexos."
        if alvo in {"todos", "tudo", "all"}:
            self._rascunho_email["anexos"] = []
        elif alvo in {"ultimo", "último"}:
            self._rascunho_email["anexos"].pop()
        else:
            self._rascunho_email["anexos"] = [
                a for a in anexos if alvo not in a.get("nome", "").lower()
            ]
        await self._publicar_rascunho_email()
        return "Anexo removido e janela de rascunho atualizada."

    @function_tool(
        description=(
            "Envia definitivamente o e-mail que está montado na janela de rascunho na tela "
            "(incluindo todos os anexos adicionados). Chame quando o usuário confirmar que pode enviar."
        )
    )
    async def confirmar_e_enviar_email(self):
        para = (self._rascunho_email.get("para") or "").strip()
        assunto = (self._rascunho_email.get("assunto") or "").strip()
        mensagem = self._rascunho_email.get("mensagem") or ""
        cc = (self._rascunho_email.get("cc") or "").strip()
        anexos = list(self._rascunho_email.get("anexos") or [])

        if not para or "@" not in para:
            return "Aviso: o destinatário ('Para') ainda não possui um e-mail válido. Pergunte o e-mail do destinatário primeiro."
        if not assunto:
            return "Aviso: o assunto do e-mail ainda está vazio. Pergunte qual assunto colocar antes de enviar."

        self._rascunho_email["status"] = "enviando"
        self._rascunho_email["erro"] = ""
        await self._publicar_rascunho_email()

        try:
            resultado = await asyncio.to_thread(
                google_workspace.send_gmail_message,
                para,
                assunto,
                mensagem,
                cc,
                anexos,
            )
        except GoogleWorkspaceError as erro:
            self._rascunho_email["status"] = "erro"
            self._rascunho_email["erro"] = str(erro)
            await self._publicar_rascunho_email()
            return f"Aviso: {erro}"

        self._rascunho_email["status"] = "enviado"
        await self._publicar_rascunho_email()

        async def _fechar_apos_envio():
            await asyncio.sleep(2.5)
            if self._rascunho_email.get("status") == "enviado":
                self._rascunho_email["aberto"] = False
                await self._publicar_rascunho_email()

        asyncio.create_task(_fechar_apos_envio())
        return json.dumps(resultado, ensure_ascii=False)

    @function_tool(description="Fecha/cancela a janela de rascunho de e-mail na tela.")
    async def fechar_rascunho_email(self):
        self._rascunho_email["aberto"] = False
        await self._publicar_rascunho_email()
        return "Janela de rascunho de e-mail fechada."

    @function_tool(description="Envia um novo e-mail pelo Gmail do usuário (também exibe na janela de e-mail na tela).")
    async def enviar_email_gmail(
        self,
        para: str,
        assunto: str,
        mensagem: str,
        cc: str = "",
    ):
        await self.abrir_ou_atualizar_rascunho_email(
            para=para,
            assunto=assunto,
            mensagem=mensagem,
            cc=cc,
        )
        return await self.confirmar_e_enviar_email()

    @function_tool(description="Responde a um e-mail existente no Gmail mantendo a mesma conversa (thread).")
    async def responder_email_gmail(
        self,
        id_mensagem: str,
        mensagem: str,
    ):
        return await self._executar_google(
            google_workspace.reply_gmail_message,
            id_mensagem,
            mensagem,
        )

    @function_tool(description="Marca um e-mail do Gmail como lido usando o ID da mensagem.")
    async def marcar_email_como_lido_gmail(self, id_mensagem: str):
        return await self._executar_google(
            google_workspace.mark_gmail_message_read,
            id_mensagem,
        )

    @function_tool(description="Envia um e-mail do Gmail para a lixeira usando o ID da mensagem.")
    async def enviar_email_para_lixeira_gmail(self, id_mensagem: str):
        return await self._executar_google(
            google_workspace.trash_gmail_message,
            id_mensagem,
        )

    def _detectar_marcador_ou_busca_gmail_ativo(self) -> dict[str, str]:
        """Se o usuário estiver com o Gmail aberto no navegador e tiver clicado em um marcador (#label/...) ou busca (#search/...), identifica qual é."""
        from urllib.parse import unquote_plus

        aba_gmail = jarvis_chrome.get_gmail_tab()
        url = (aba_gmail.get("url") or "").strip()
        titulo = (aba_gmail.get("title") or "").strip()

        if "mail.google.com" not in url:
            aba = self._obter_aba_ativa_navegador()
            url = (aba.get("url") or "").strip()
            titulo = (aba.get("title") or "").strip()

        if "mail.google.com" not in url:
            return {"marcador": "", "busca": "", "url": url}

        m_label = re.search(r"#label/([^/?&]+)", url)
        if m_label:
            nome_lbl = unquote_plus(m_label.group(1)).replace("/", "/")
            return {"marcador": nome_lbl, "busca": "", "url": url}

        m_search = re.search(r"#(?:search|apps)/([^/?&]+)", url)
        if m_search:
            termo_busca = unquote_plus(m_search.group(1))
            return {"marcador": "", "busca": termo_busca, "url": url}

        # Tenta extrair do título da janela do Gmail (ex.: '"GRAICHE AP" - email@gmail.com - Gmail')
        m_title = re.search(r'^"?([^"-]+)"?\s*-\s*[^-]+-\s*Gmail', titulo)
        if m_title:
            cand = m_title.group(1).strip()
            if cand.lower() not in {"caixa de entrada", "inbox", "com estrela", "enviados", "rascunhos"}:
                return {"marcador": cand, "busca": "", "url": url}

        return {"marcador": "", "busca": "", "url": url}

    def _classificar_emails_para_organizacao(
        self,
        mensagens: list[dict],
        marcadores_existentes: list[dict],
    ) -> list[dict]:
        nomes_marcadores = [m["nome"] for m in marcadores_existentes if m.get("nome")]
        mapa_marcadores_lower = {n.lower(): n for n in nomes_marcadores}

        # Palavras-chave dos marcadores já existentes do usuário (ex.: "GRAICHE AP" -> "graiche", "BTG INVEST" -> "btg")
        tokens_marcadores: list[tuple[str, str]] = []
        ignorados_token = {"ap", "pp", "de", "do", "da", "em", "andamento", "tarefa"}
        for nome_m in nomes_marcadores:
            nome_l = nome_m.lower().strip()
            tokens_marcadores.append((nome_l, nome_m))
            for parte in re.split(r"[\s/_-]+", nome_l):
                if len(parte) >= 3 and parte not in ignorados_token:
                    tokens_marcadores.append((parte, nome_m))

        grupos: dict[str, dict] = {}

        for msg in mensagens:
            msg_id = msg.get("id", "")
            de_raw = (msg.get("de") or "").strip()
            assunto = (msg.get("assunto") or "(Sem assunto)").strip()
            resumo = (msg.get("resumo") or "").strip()
            nao_lido = bool(msg.get("nao_lido"))

            # Extrai nome e e-mail do remetente
            m_email = re.search(r"<([^>]+)>", de_raw)
            email_addr = (m_email.group(1) if m_email else de_raw).strip().lower()
            nome_remetente = re.sub(r"<[^>]+>", "", de_raw).strip().strip('"').strip("'")
            if not nome_remetente:
                nome_remetente = email_addr.split("@")[0] if "@" in email_addr else "Remetente"

            dominio = email_addr.split("@")[1] if "@" in email_addr else ""
            texto_completo = f"{de_raw} {assunto} {resumo}".lower()

            # 1. Verifica se bate com algum Marcador que o usuário JÁ TEM no Gmail (ex.: GRAICHE AP, ENEL, BINANCE, BTG INVEST...)
            marcador_detectado = ""
            termo_busca_grupo = ""
            for token, nome_marcador_real in tokens_marcadores:
                if re.search(rf"\b{re.escape(token)}\b", texto_completo) or token in dominio:
                    marcador_detectado = nome_marcador_real
                    termo_busca_grupo = token
                    break

            if marcador_detectado:
                chave_grupo = f"MARCADOR::{marcador_detectado}"
                titulo_grupo = f"{marcador_detectado} ({nome_remetente})"
                query_grupo = f"in:inbox {termo_busca_grupo}"
            else:
                chave_remetente = email_addr or nome_remetente.lower()
                chave_grupo = f"REMETENTE::{chave_remetente}"
                titulo_grupo = nome_remetente
                query_grupo = f"in:inbox from:{email_addr}" if "@" in email_addr else f'in:inbox "{nome_remetente}"'

            if chave_grupo not in grupos:
                if marcador_detectado:
                    marcador_sug = marcador_detectado
                    ja_existe = True
                else:
                    limpo_sug = re.sub(
                        r"\b(ltda|s\.?a\.?|inc|brasil|pagamentos|construt\w+|imobili\w+)\b",
                        "",
                        nome_remetente,
                        flags=re.IGNORECASE,
                    ).strip(" -._")
                    marcador_sug = (limpo_sug[:22] or nome_remetente[:22]).upper().strip()
                    if marcador_sug.lower() in mapa_marcadores_lower:
                        marcador_sug = mapa_marcadores_lower[marcador_sug.lower()]
                        ja_existe = True
                    else:
                        ja_existe = False

                grupos[chave_grupo] = {
                    "remetente": nome_remetente,
                    "email_remetente": email_addr,
                    "titulo_grupo": titulo_grupo,
                    "ids": [],
                    "assuntos_exemplo": [],
                    "nao_lidos": 0,
                    "marcador_sugerido": marcador_sug,
                    "marcador_ja_existe": ja_existe,
                    "query_gmail": query_grupo,
                }

            g = grupos[chave_grupo]
            if msg_id:
                g["ids"].append(msg_id)
            if len(g["assuntos_exemplo"]) < 2 and assunto not in g["assuntos_exemplo"]:
                g["assuntos_exemplo"].append(assunto[:80])
            if nao_lido:
                g["nao_lidos"] += 1

        lista_grupos = sorted(
            grupos.values(),
            key=lambda item: (1 if item["marcador_ja_existe"] else 0, len(item["ids"])),
            reverse=True,
        )[:12]

        sugestoes_finais = []
        for idx, g in enumerate(lista_grupos, start=1):
            qtd_msg = len(g["ids"])
            marcador = g["marcador_sugerido"]
            ja_existe = g["marcador_ja_existe"]
            assunto_exibicao = (
                f"{g['remetente']} ({qtd_msg} {'e-mail' if qtd_msg == 1 else 'e-mails'})"
            )
            resumo_exibicao = " · ".join(g["assuntos_exemplo"])
            motivo = (
                f"Mover todos os e-mails de {g['remetente']} para o marcador existente '{marcador}'"
                if ja_existe
                else f"Criar marcador '{marcador}' no Gmail e organizar os e-mails de {g['remetente']}"
            )
            sugestoes_finais.append(
                {
                    "numero": idx,
                    "id": g["ids"][0] if g["ids"] else str(idx),
                    "ids": g["ids"],
                    "quantidade_emails": qtd_msg,
                    "de": g["email_remetente"] or g["remetente"],
                    "remetente": g["remetente"],
                    "assunto": assunto_exibicao,
                    "resumo": resumo_exibicao,
                    "nao_lido": g["nao_lidos"] > 0,
                    "acao_sugerida": "mover_para_marcador",
                    "marcador_sugerido": marcador,
                    "marcador_ja_existe": ja_existe,
                    "query_gmail": g["query_gmail"],
                    "arquivar": True,
                    "motivo": motivo,
                    "status": "pendente",
                }
            )
        return sugestoes_finais

    @function_tool(
        description=(
            "Navega, pesquisa ou abre uma visão específica diretamente dentro da aba do Google Mail (Gmail) "
            "do usuário no navegador (sem abrir janelas extras). "
            "Exemplos de `destino_ou_busca`: 'caixa de entrada', 'nao lidos', 'lixeira', 'enviados', "
            "'marcador:GRAICHE AP', ou qualquer termo/remetente de busca como 'graiche', 'from:enel', etc."
        )
    )
    async def navegar_ou_pesquisar_no_gmail(self, destino_ou_busca: str = "caixa de entrada"):
        termo = (destino_ou_busca or "caixa de entrada").strip()
        termo_l = termo.lower()

        mapa_fixo = {
            "caixa de entrada": "#inbox",
            "inbox": "#inbox",
            "entrada": "#inbox",
            "nao lidos": "#search/is%3Aunread",
            "não lidos": "#search/is%3Aunread",
            "estrela": "#starred",
            "com estrela": "#starred",
            "enviados": "#sent",
            "rascunhos": "#drafts",
            "lixeira": "#trash",
            "spam": "#spam",
            "todos": "#all",
        }
        if termo_l in mapa_fixo:
            await self._navegar_no_gmail_navegador(mapa_fixo[termo_l])
            return f"Gmail focado em '{termo}' no navegador."

        if termo_l.startswith("marcador:"):
            nome_m = termo.split(":", 1)[1].strip()
            lbl = await asyncio.to_thread(google_workspace.find_gmail_label, nome_m)
            nome_real = lbl["nome"] if lbl else nome_m
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(nome_real)}")
            return f"Marcador '{nome_real}' aberto diretamente no Gmail."

        # Verifica se corresponde exatamente a um marcador do usuário
        try:
            lbl = await asyncio.to_thread(google_workspace.find_gmail_label, termo)
        except Exception:
            lbl = None
        if lbl and lbl.get("nome", "").lower() == termo_l:
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(lbl['nome'])}")
            return f"Marcador '{lbl['nome']}' aberto diretamente no Gmail."

        await self._navegar_no_gmail_navegador(f"#search/{url_quote(termo)}")
        return f"Pesquisa por '{termo}' aberta diretamente na tela do Gmail."

    @function_tool(
        description=(
            "Analisa a Caixa de Entrada (ou a busca/marcador atual no Google Mail) agrupando por REMETENTE "
            "e cruzando com os MARCADORES reais que o usuário já tem no Gmail (como GRAICHE AP, ENEL, BINANCE, BTG INVEST, etc.). "
            "Trabalha diretamente dentro do ambiente do Google Mail (NÃO abre nenhum painel extra) e retorna as sugestões "
            "para você falar ao usuário e executar conforme as ordens dele."
        )
    )
    async def analisar_e_sugerir_organizacao_emails(
        self,
        filtro_ou_remetente: str = "in:inbox",
        quantidade_analise: int = 45,
    ):
        qtd = max(5, min(80, quantidade_analise))
        consulta = (filtro_ou_remetente or "in:inbox").strip()

        ctx_gmail = await asyncio.to_thread(self._detectar_marcador_ou_busca_gmail_ativo)
        if not ctx_gmail.get("url") or "mail.google.com" not in (ctx_gmail.get("url") or ""):
            await self._navegar_no_gmail_navegador("#inbox")
        elif consulta == "in:inbox" and ctx_gmail.get("busca"):
            consulta = f"in:inbox {ctx_gmail['busca']}"

        try:
            marcadores_atuais, mensagens = await asyncio.gather(
                asyncio.to_thread(google_workspace.list_gmail_labels),
                asyncio.to_thread(google_workspace.list_gmail_messages, consulta, qtd),
            )
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        nomes_marcadores = [m["nome"] for m in marcadores_atuais]
        self._organizador_email["marcadores_disponiveis"] = nomes_marcadores

        if not mensagens:
            return "Nenhum e-mail encontrado na Caixa de Entrada para esse filtro."

        sugestoes = await asyncio.to_thread(
            self._classificar_emails_para_organizacao,
            mensagens,
            marcadores_atuais,
        )
        self._organizador_email["sugestoes"] = sugestoes

        resumo_para_fala = [
            {
                "numero": s["numero"],
                "remetente": s["remetente"],
                "quantidade_emails": s["quantidade_emails"],
                "marcador_sugerido": s["marcador_sugerido"],
                "marcador_ja_existe_no_gmail": s["marcador_ja_existe"],
                "exemplos_assunto": s["resumo"],
                "motivo": s["motivo"],
            }
            for s in sugestoes
        ]
        return json.dumps(
            {
                "ambiente": "Google Mail (direto no navegador, sem painel extra)",
                "marcadores_do_usuario_no_gmail": nomes_marcadores,
                "grupos_por_remetente": resumo_para_fala,
                "instrucao": (
                    "Converse naturalmente com o usuário sobre os remetentes encontrados na Caixa de Entrada "
                    "e diga em qual Marcador do Gmail você sugere colocar cada um (ex.: e-mails da Graiche no marcador GRAICHE AP). "
                    "Pergunte se ele quer que você já mova todos ou algum específico!"
                ),
            },
            ensure_ascii=False,
        )

    @function_tool(
        description=(
            "Executa no Google Mail as sugestões de organização aprovadas pelo usuário (em lote por remetente) "
            "e atualiza a tela do Gmail no navegador. "
            "Informe `numeros_ou_todos` como 'todos' ou números separados por vírgula (ex.: '1,2,4'). "
            "Se o usuário pedir para mudar o marcador de algum item, informe `novo_marcador`."
        )
    )
    async def aprovar_ou_rejeitar_sugestoes_email(
        self,
        numeros_ou_todos: str = "todos",
        aprovar: bool = True,
        nova_acao: str = "",
        novo_marcador: str = "",
    ):
        sugestoes = self._organizador_email.get("sugestoes") or []
        if not sugestoes:
            return "Aviso: ainda não há sugestões carregadas. Chame `analisar_e_sugerir_organizacao_emails` ou `organizar_remetente_no_marcador_gmail`."

        alvo_str = (numeros_ou_todos or "todos").strip().lower()
        if alvo_str in {"todos", "todas", "tudo", "all"}:
            numeros_alvo = {
                int(s["numero"])
                for s in sugestoes
                if s.get("status") == "pendente"
            }
        else:
            numeros_alvo = {
                int(m)
                for m in re.findall(r"\d+", alvo_str)
            }

        if not numeros_alvo:
            return "Aviso: nenhum item pendente correspondeu aos números informados."

        aplicados = []
        ignorados = []
        erros = []
        ultimo_marcador_usado = ""

        for item in sugestoes:
            if int(item.get("numero", 0)) not in numeros_alvo:
                continue
            if not aprovar:
                item["status"] = "ignorado"
                ignorados.append(item["numero"])
                continue

            if nova_acao.strip():
                item["acao_sugerida"] = nova_acao.strip().lower()
            if novo_marcador.strip():
                item["marcador_sugerido"] = novo_marcador.strip()

            try:
                res_lote = await asyncio.to_thread(
                    google_workspace.batch_organize_into_label,
                    item.get("ids") or [item["id"]],
                    item.get("query_gmail") or "",
                    item.get("marcador_sugerido") or "",
                    bool(item.get("arquivar", True)),
                    item.get("acao_sugerida") or "mover_para_marcador",
                    250,
                )
                item["status"] = "aprovado"
                qtd_real = res_lote.get("quantidade_afetada", len(item.get("ids") or []))
                marc_final = res_lote.get("marcador") or item.get("marcador_sugerido") or ""
                if marc_final:
                    ultimo_marcador_usado = marc_final
                aplicados.append(
                    f"#{item['numero']} ({qtd_real} e-mails de {item.get('remetente', item['assunto'])} -> "
                    f"Marcador '{marc_final}')"
                )
            except Exception as erro:
                item["status"] = "erro"
                erros.append(f"#{item['numero']}: {erro}")

        # Atualiza a tela do Google Mail do usuário para refletir a organização!
        if len(aplicados) == 1 and ultimo_marcador_usado:
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(ultimo_marcador_usado)}")
        elif aplicados:
            await self._navegar_no_gmail_navegador("#inbox")

        return json.dumps(
            {
                "executados_no_gmail": aplicados,
                "ignorados": ignorados,
                "erros": erros,
            },
            ensure_ascii=False,
        )

    @function_tool(
        description=(
            "Lista todos os Marcadores (pastas/etiquetas) que o usuário possui no Gmail "
            "(como GRAICHE AP, ENEL, BINANCE, BTG INVEST, etc.)."
        )
    )
    async def listar_marcadores_gmail(self):
        return await self._executar_google(google_workspace.list_gmail_labels)

    @function_tool(
        description=(
            "Abre um Marcador específico diretamente na tela do Google Mail do usuário e lista os e-mails que estão "
            "dentro desse Marcador. Se o usuário já clicou em um marcador lá no Gmail e disse 'lista os e-mails desse marcador', "
            "passe `nome_marcador='atual'`."
        )
    )
    async def abrir_ou_listar_marcador_gmail(
        self,
        nome_marcador: str = "atual",
        quantidade: int = 15,
        abrir_no_navegador: bool = True,
    ):
        alvo = (nome_marcador or "atual").strip()
        if alvo.lower() in {"atual", "esse", "este", "clicado", "selecionado", "aberto", ""}:
            ctx = await asyncio.to_thread(self._detectar_marcador_ou_busca_gmail_ativo)
            alvo = ctx.get("marcador") or ctx.get("busca") or ""
            if not alvo:
                return (
                    "Aviso: não identifiquei qual marcador está selecionado na aba do Gmail. "
                    "Pergunte ao usuário qual marcador do Gmail ele quer abrir ou listar (ex.: GRAICHE AP, ENEL, BINANCE)."
                )

        try:
            dados = await asyncio.to_thread(
                google_workspace.list_messages_in_label,
                alvo,
                quantidade,
            )
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        nome_real = dados.get("marcador") or alvo
        if abrir_no_navegador:
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(nome_real)}")

        return json.dumps(dados, ensure_ascii=False)

    @function_tool(
        description=(
            "Cria um Marcador no Gmail e/ou move todos os e-mails de um remetente ou termo de busca "
            "(ex.: 'graiche', 'enel', 'nubank', 'c6 bank') diretamente para dentro desse Marcador no Google Mail, "
            "atualizando a tela do Gmail no navegador! "
            "Se o usuário estiver com um marcador clicado no Gmail e pedir 'adiciona os e-mails de X aqui', "
            "passe `nome_marcador='atual'`."
        )
    )
    async def organizar_remetente_no_marcador_gmail(
        self,
        remetente_ou_busca: str = "",
        nome_marcador: str = "atual",
        mover_da_caixa_de_entrada: bool = True,
    ):
        marcador = (nome_marcador or "").strip()
        busca = (remetente_ou_busca or "").strip()

        if marcador.lower() in {"atual", "esse", "este", "clicado", "selecionado", ""}:
            ctx = await asyncio.to_thread(self._detectar_marcador_ou_busca_gmail_ativo)
            marcador = ctx.get("marcador") or ""

        if not marcador and busca:
            marcador = busca.upper()

        if not marcador:
            return "Aviso: informe o nome do marcador onde deseja organizar os e-mails."

        if not busca:
            res_criacao = await self._executar_google(google_workspace.create_gmail_label, marcador)
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(marcador)}")
            return res_criacao

        if busca.lower() in {"esses", "estes", "atual", "busca", "resultados", "isso"}:
            ctx = await asyncio.to_thread(self._detectar_marcador_ou_busca_gmail_ativo)
            busca = ctx.get("busca") or ""
            if not busca:
                return "Aviso: informe de qual remetente ou termo deseja mover os e-mails."

        try:
            resultado = await asyncio.to_thread(
                google_workspace.batch_organize_into_label,
                None,
                busca,
                marcador,
                mover_da_caixa_de_entrada,
                "mover_para_marcador",
                300,
            )
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        marcador_final = resultado.get("marcador") or marcador
        await self._navegar_no_gmail_navegador(f"#label/{url_quote(marcador_final)}")
        return json.dumps(resultado, ensure_ascii=False)

    @function_tool(
        description=(
            "Apaga um Marcador do Gmail, tira e-mails de dentro de um Marcador ou move os e-mails de um Marcador/remetente "
            "para a Lixeira do Gmail, atualizando a tela do Google Mail ao vivo. "
            "Se o usuário clicou no marcador lá no Gmail, passe `nome_marcador='atual'`. "
            "Opções para `acao`: 'remover_do_marcador' (tira a etiqueta sem apagar o e-mail), "
            "'lixeira' (manda os e-mails para a lixeira) ou 'apagar_marcador' (exclui a pasta/marcador do Gmail)."
        )
    )
    async def apagar_marcador_ou_emails_gmail(
        self,
        nome_marcador: str = "atual",
        acao: str = "remover_do_marcador",
        filtro_remetente_ou_assunto: str = "",
    ):
        marcador = (nome_marcador or "").strip()
        if marcador.lower() in {"atual", "esse", "este", "clicado", "selecionado", ""}:
            ctx = await asyncio.to_thread(self._detectar_marcador_ou_busca_gmail_ativo)
            marcador = ctx.get("marcador") or ""

        acao_norm = (acao or "remover_do_marcador").strip().lower()
        if acao_norm == "apagar_marcador":
            if not marcador:
                return "Aviso: informe qual marcador deseja excluir do Gmail."
            res_del = await self._executar_google(google_workspace.delete_gmail_label, marcador)
            await self._navegar_no_gmail_navegador("#inbox")
            return res_del

        partes_q = []
        nome_real_marcador = marcador
        if marcador:
            lbl = await asyncio.to_thread(google_workspace.find_gmail_label, marcador)
            if lbl:
                nome_real_marcador = lbl["nome"]
            nome_q = nome_real_marcador.replace(" ", "-")
            partes_q.append(f"label:{nome_q}")
        if filtro_remetente_ou_assunto.strip():
            partes_q.append(filtro_remetente_ou_assunto.strip())

        query_final = " ".join(partes_q).strip()
        if not query_final:
            return "Aviso: informe o marcador ou o remetente dos e-mails que deseja remover/apagar."

        try:
            resultado = await asyncio.to_thread(
                google_workspace.batch_organize_into_label,
                None,
                query_final,
                nome_real_marcador,
                False,
                "lixeira" if "lixeira" in acao_norm or "apagar" in acao_norm else "remover_do_marcador",
                200,
            )
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        if nome_real_marcador:
            await self._navegar_no_gmail_navegador(f"#label/{url_quote(nome_real_marcador)}")
        else:
            await self._navegar_no_gmail_navegador("#inbox")
        return json.dumps(resultado, ensure_ascii=False)

    @function_tool(
        description=(
            "Configura um alerta em tempo real para o Jarvis avisar o usuário por voz assim que chegar um novo e-mail "
            "de um determinado remetente, assunto ou palavra-chave (ou 'todos' para avisar sobre qualquer e-mail novo). "
            "Opcionalmente, se `marcador_automatico` for informado, o Jarvis também já move/etiqueta o e-mail "
            "automaticamente para aquele Marcador do Gmail assim que ele chegar!"
        )
    )
    async def configurar_aviso_chegada_email(
        self,
        remetente_ou_assunto: str = "todos",
        marcador_automatico: str = "",
        abrir_no_gmail_ao_chegar: bool = False,
    ):
        filtro = (remetente_ou_assunto or "todos").strip()
        marcador = (marcador_automatico or "").strip()

        # Atualiza ou adiciona a regra
        filtro_lower = filtro.lower()
        regra_existente = None
        for r in self._alertas_email:
            if str(r.get("filtro", "")).lower() == filtro_lower:
                regra_existente = r
                break

        if regra_existente:
            regra_existente["marcador_automatico"] = marcador
            regra_existente["abrir_no_gmail"] = bool(abrir_no_gmail_ao_chegar)
            regra = regra_existente
        else:
            regra = {
                "id": uuid.uuid4().hex[:8],
                "filtro": filtro,
                "marcador_automatico": marcador,
                "abrir_no_gmail": bool(abrir_no_gmail_ao_chegar),
                "criado_em": datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M"),
            }
            self._alertas_email.append(regra)

        await asyncio.to_thread(self._salvar_alertas_email)

        # Verifica os não lidos atuais para registrar IDs já existentes e checar se já tem algum recente
        ja_na_caixa = []
        try:
            atuais = await asyncio.to_thread(
                google_workspace.list_gmail_messages,
                "in:inbox is:unread newer_than:2d",
                15,
            )
            for m in atuais:
                mid = m.get("id")
                if mid:
                    self._ids_emails_ja_vistos.add(mid)
                texto_m = f"{m.get('de', '')} {m.get('assunto', '')} {m.get('resumo', '')}".lower()
                if filtro_lower in {"todos", "qualquer", "tudo", "*"} or filtro_lower in texto_m:
                    ja_na_caixa.append(
                        {
                            "de": m.get("de", ""),
                            "assunto": m.get("assunto", ""),
                            "data": m.get("data", ""),
                        }
                    )
        except Exception:
            pass

        return json.dumps(
            {
                "status": "monitoramento_ativo",
                "regra": regra,
                "nao_lidos_recentes_ja_na_caixa": ja_na_caixa[:3],
                "mensagem": (
                    f"Monitoramento ativado! Avisarei imediatamente por voz assim que chegar um novo e-mail "
                    f"correspondente a '{filtro}'"
                    + (f" e já organizarei no marcador '{marcador}'." if marcador else ".")
                ),
            },
            ensure_ascii=False,
        )

    @function_tool(
        description="Lista todos os alertas/monitoramentos ativos de chegada de novos e-mails configurados pelo usuário."
    )
    async def listar_avisos_chegada_email(self):
        return json.dumps(
            {
                "total_alertas_ativos": len(self._alertas_email),
                "alertas": self._alertas_email,
            },
            ensure_ascii=False,
        )

    @function_tool(
        description=(
            "Remove/desativa um alerta de chegada de e-mail pelo nome do remetente/assunto, pelo ID, "
            "ou 'todos' para desativar todos os avisos."
        )
    )
    async def remover_aviso_chegada_email(self, remetente_ou_id: str = "todos"):
        alvo = (remetente_ou_id or "todos").strip().lower()
        if alvo in {"todos", "todas", "tudo", "all", "*"}:
            qtd = len(self._alertas_email)
            self._alertas_email = []
            await asyncio.to_thread(self._salvar_alertas_email)
            return f"Todos os {qtd} alertas de chegada de e-mail foram desativados."

        antes = len(self._alertas_email)
        self._alertas_email = [
            r
            for r in self._alertas_email
            if alvo not in str(r.get("filtro", "")).lower()
            and alvo != str(r.get("id", "")).lower()
        ]
        removidos = antes - len(self._alertas_email)
        await asyncio.to_thread(self._salvar_alertas_email)
        if removidos == 0:
            return f"Nenhum alerta encontrado com o termo '{remetente_ou_id}'."
        return f"Alerta de chegada de e-mail para '{remetente_ou_id}' removido com sucesso."
    # --- FIM DAS FERRAMENTAS ---

async def _publish_tool_activity(
    room: rtc.Room,
    events: asyncio.Queue[ToolExecutionUpdatedEvent | None],
):
    while True:
        event = await events.get()
        if event is None:
            return

        update = event.update
        if isinstance(update, ToolCallStarted):
            payload = {
                "type": "jarvis_activity",
                "event": "started",
                "id": update.function_call.call_id,
                "tool": update.function_call.name,
            }
        elif isinstance(update, ToolCallEnded):
            payload = {
                "type": "jarvis_activity",
                "event": "ended",
                "id": update.call_id,
                "status": update.status,
            }
        else:
            continue

        try:
            await room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False),
                topic="jarvis.activity",
            )
        except Exception as error:
            logger_memoria.error(
                "evento=painel_atividade status=erro tipo=%s",
                type(error).__name__,
            )


async def _publish_system_metrics(room: rtc.Room):
    previous_sample: tuple[float, int, int] | None = None

    while True:
        await asyncio.sleep(2)
        try:
            sampled_at = time.monotonic()
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            network = psutil.net_io_counters()
            if network is None:
                logger_memoria.error("evento=metricas_sistema status=erro motivo=rede_indisponivel")
                return
        except psutil.Error as error:
            logger_memoria.error(
                "evento=metricas_sistema status=erro tipo=%s",
                type(error).__name__,
            )
            return

        sent_bytes_per_second = None
        received_bytes_per_second = None
        if previous_sample is not None:
            previous_time, previous_sent, previous_received = previous_sample
            elapsed = max(sampled_at - previous_time, 0.001)
            sent_bytes_per_second = max(0, network.bytes_sent - previous_sent) / elapsed
            received_bytes_per_second = max(0, network.bytes_recv - previous_received) / elapsed

        payload = {
            "type": "jarvis_system",
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_bytes": memory.used,
            "memory_total_bytes": memory.total,
            "sent_bytes_per_second": sent_bytes_per_second,
            "received_bytes_per_second": received_bytes_per_second,
        }
        try:
            await room.local_participant.publish_data(
                json.dumps(payload),
                topic="jarvis.system",
            )
        except Exception as error:
            logger_memoria.error(
                "evento=metricas_sistema status=erro_publicacao tipo=%s",
                type(error).__name__,
            )
            return

        previous_sample = (sampled_at, network.bytes_sent, network.bytes_recv)


def _handle_room_data(
    data_packet: rtc.DataPacket,
    location_state: dict[str, float | str],
    agent: Assistant,
):
    if data_packet.topic == "jarvis.location":
        _update_session_location(data_packet, location_state)
        return
    if data_packet.topic == "jarvis.email":
        try:
            payload = json.loads(data_packet.data)
        except Exception:
            return
        if payload.get("type") == "jarvis_email_user_update":
            for field in ("para", "cc", "assunto", "mensagem"):
                if isinstance(payload.get(field), str):
                    agent._rascunho_email[field] = payload[field]
            if isinstance(payload.get("aberto"), bool):
                agent._rascunho_email["aberto"] = payload["aberto"]
            if isinstance(payload.get("adicionar_anexos"), list):
                atuais = list(agent._rascunho_email.get("anexos", []))
                for novo in payload["adicionar_anexos"]:
                    if isinstance(novo, dict) and novo.get("caminho_ou_id"):
                        atuais.append(
                            {
                                "id": str(novo.get("id") or f"local-{uuid.uuid4().hex[:8]}"),
                                "tipo": str(novo.get("tipo") or "local"),
                                "nome": str(novo.get("nome") or "anexo"),
                                "caminho_ou_id": str(novo["caminho_ou_id"]),
                            }
                        )
                agent._rascunho_email["anexos"] = atuais
                asyncio.create_task(agent._publicar_rascunho_email())
            if isinstance(payload.get("remover_anexo_id"), str):
                rem_id = payload["remover_anexo_id"]
                agent._rascunho_email["anexos"] = [
                    a for a in agent._rascunho_email.get("anexos", []) if a.get("id") != rem_id
                ]
                asyncio.create_task(agent._publicar_rascunho_email())
            if payload.get("action") == "send_now":
                asyncio.create_task(agent.confirmar_e_enviar_email())


async def _monitorar_chegada_emails(session: AgentSession, assistant: Assistant):
    """Monitora em segundo plano a chegada de novos e-mails no Gmail e avisa o usuário por voz quando bater com os alertas configurados."""
    await asyncio.sleep(4)
    try:
        iniciais = await asyncio.to_thread(
            google_workspace.list_gmail_messages,
            "in:inbox is:unread newer_than:2d",
            25,
        )
        for m in iniciais:
            mid = m.get("id")
            if mid:
                assistant._ids_emails_ja_vistos.add(mid)
    except Exception:
        pass

    while True:
        await asyncio.sleep(15)
        if not assistant._alertas_email:
            continue
        try:
            atuais = await asyncio.to_thread(
                google_workspace.list_gmail_messages,
                "in:inbox is:unread newer_than:1d",
                10,
            )
        except Exception:
            continue

        novos_alertar: list[tuple[dict, dict]] = []
        for msg in atuais:
            mid = msg.get("id")
            if not mid or mid in assistant._ids_emails_ja_vistos:
                continue
            assistant._ids_emails_ja_vistos.add(mid)

            if not assistant._alertas_email:
                continue

            texto_msg = f"{msg.get('de', '')} {msg.get('assunto', '')} {msg.get('resumo', '')}".lower()
            for regra in assistant._alertas_email:
                filtro = str(regra.get("filtro") or "").strip().lower()
                if not filtro:
                    continue
                bateu = filtro in {"todos", "todas", "qualquer", "tudo", "*"} or filtro in texto_msg
                if bateu:
                    novos_alertar.append((msg, regra))
                    break

        for msg, regra in novos_alertar:
            de_str = msg.get("de") or "Remetente desconhecido"
            assunto_str = msg.get("assunto") or "(Sem assunto)"
            resumo_str = (msg.get("resumo") or "")[:140]
            marc_auto = str(regra.get("marcador_automatico") or "").strip()
            info_extra = ""

            if marc_auto:
                try:
                    await asyncio.to_thread(
                        google_workspace.batch_organize_into_label,
                        [msg["id"]],
                        "",
                        marc_auto,
                        False,
                        "mover_para_marcador",
                        1,
                    )
                    info_extra = f" Você também já aplicou automaticamente o marcador '{marc_auto}' nele."
                except Exception:
                    pass

            if regra.get("abrir_no_gmail"):
                await assistant._navegar_no_gmail_navegador(f"#inbox/{msg['id']}")

            try:
                await session.generate_reply(
                    instructions=(
                        f"AVISO DE CHEGADA DE E-MAIL EM TEMPO REAL: Acabou de chegar um novo e-mail que o usuário pediu para monitorar! "
                        f"Remetente: {de_str} | Assunto: '{assunto_str}' | Trecho: '{resumo_str}'.{info_extra} "
                        "Avise o usuário imediatamente de forma natural e pergunte se ele quer ler, responder ou mover para algum marcador no Gmail."
                    )
                )
            except Exception as erro:
                logger_memoria.warning("evento=alerta_email_voz status=erro motivo=%s", erro)


_PADRAO_PEDIDO_EMAIL = re.compile(
    r"\b("
    r"(mandar|manda|enviar|envia|escrever|escreve|redigir|redige|criar|cria|fazer|faz|preparar|prepara)\b.{0,35}\b(e-?mail|email)"
    r"|"
    r"(e-?mail|email)\b.{0,25}\b(para|pro|pra)"
    r"|"
    r"novo\s+(e-?mail|email)"
    r")\b",
    re.IGNORECASE,
)

_PADRAO_PERGUNTA_DESTINATARIO = re.compile(
    r"\b("
    r"para\s+quem\s+(voc[eê]\s+)?(quer|deseja|gostaria|vai)\s+(mandar|enviar)"
    r"|"
    r"para\s+quem\s+[eé]\s+o\s+(e-?mail|email)"
    r"|"
    r"qual\s+([eé]\s+)?o\s+(destinat[aá]rio|endere[cç]o\s+de\s+e-?mail)"
    r"|"
    r"quem\s+ser[aá]\s+o\s+destinat[aá]rio"
    r")\b",
    re.IGNORECASE,
)


def _verificar_texto_abrir_rascunho(texto: str, eh_usuario: bool, agent: Assistant):
    if not texto:
        return
    if agent._rascunho_email.get("aberto") and agent._rascunho_email.get("status") != "enviado":
        return
    padrao = _PADRAO_PEDIDO_EMAIL if eh_usuario else _PADRAO_PERGUNTA_DESTINATARIO
    if padrao.search(texto):
        asyncio.create_task(agent._abrir_rascunho_imediato())


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession()
    location_state: dict[str, float | str] = {}
    activity_events: asyncio.Queue[ToolExecutionUpdatedEvent | None] = asyncio.Queue()
    assistant = Assistant(location_state, ctx.room)
    ctx.room.on(
        "data_received",
        lambda data_packet: _handle_room_data(data_packet, location_state, assistant),
    )

    def _on_user_transcribed(ev):
        texto = getattr(ev, "transcript", "") or ""
        _verificar_texto_abrir_rascunho(str(texto), True, assistant)

    def _on_item_added(ev):
        item = getattr(ev, "item", None)
        if item is None:
            return
        role = str(getattr(item, "role", "") or "").lower()
        texto = getattr(item, "text_content", "") or ""
        if callable(texto):
            try:
                texto = texto()
            except Exception:
                texto = ""
        if not texto and hasattr(item, "content"):
            partes = getattr(item, "content", []) or []
            texto = " ".join(str(p) for p in partes if isinstance(p, str))
        _verificar_texto_abrir_rascunho(str(texto), role == "user", assistant)

    session.on("user_input_transcribed", _on_user_transcribed)
    session.on("conversation_item_added", _on_item_added)
    session.on("tool_execution_updated", activity_events.put_nowait)
    session.on("close", lambda _: activity_events.put_nowait(None))
    asyncio.create_task(_publish_tool_activity(ctx.room, activity_events))

    await session.start( 
        room=ctx.room,
        agent=assistant,
        room_input_options=RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()
    metrics_task = asyncio.create_task(_publish_system_metrics(ctx.room))
    email_watch_task = asyncio.create_task(_monitorar_chegada_emails(session, assistant))
    session.on("close", lambda _: (metrics_task.cancel(), email_watch_task.cancel()))

    await session.generate_reply( 
        instructions=SESSION_INSTRUCTION + "\nCumprimente o usuário de forma breve e confiante."
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))