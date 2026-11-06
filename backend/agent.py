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
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
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
    def _obter_retangulo_monitor_alvo(self, alvo_tela: str = "atual") -> tuple[int, int, int, int, str]:
        """Retorna (x, y, largura, altura, descricao) do monitor desejado suportando múltiplos monitores."""
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        monitores_raw: list[tuple[int, int, int, int]] = []

        def _monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            if lprcMonitor:
                r = lprcMonitor.contents
                monitores_raw.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )
        try:
            user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_monitor_enum_proc), 0)
        except Exception:
            pass

        # Ordena monitores da esquerda para a direita, de cima para baixo
        monitores = sorted(monitores_raw, key=lambda m: (m[0], m[1]))
        if not monitores:
            w_pad = user32.GetSystemMetrics(0) or 1920
            h_pad = user32.GetSystemMetrics(1) or 1080
            return (0, 0, w_pad, h_pad, "Tela Principal")

        alvo = (alvo_tela or "atual").strip().lower()

        # 1. Todas as telas juntas (Desktop Virtual Completo)
        if any(t in alvo for t in ("todas", "tudo", "todos os monitores", "todas as telas")):
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if vw > 0 and vh > 0:
                return (vx, vy, vw, vh, f"Todas as {len(monitores)} telas")

        # 2. Tela Principal / Primária (a que começa em 0,0)
        if any(t in alvo for t in ("principal", "primaria", "primária")):
            for idx, (l, t, r, b) in enumerate(monitores, start=1):
                if l == 0 and t == 0:
                    return (l, t, r - l, b - t, f"Tela Principal (Monitor {idx})")

        # 3. Por número do monitor ("tela 1", "monitor 2", "segunda tela", "terceira tela")
        mapa_num = {
            1: ("1", "tela 1", "monitor 1", "primeira", "primeiro"),
            2: ("2", "tela 2", "monitor 2", "segunda", "segundo", "secundaria", "secundária"),
            3: ("3", "tela 3", "monitor 3", "terceira", "terceiro"),
            4: ("4", "tela 4", "monitor 4", "quarta", "quarto"),
        }
        for num_idx, palavras in mapa_num.items():
            if alvo == str(num_idx) or any(p in alvo for p in palavras if len(p) > 1):
                idx_escolhido = min(num_idx, len(monitores)) - 1
                l, t, r, b = monitores[idx_escolhido]
                return (l, t, r - l, b - t, f"Tela {idx_escolhido + 1}")

        # 4. Por posição física ("esquerda", "direita", "meio", "cima", "baixo")
        if "esquerda" in alvo:
            l, t, r, b = min(monitores, key=lambda m: m[0])
            return (l, t, r - l, b - t, "Tela da Esquerda")
        if "direita" in alvo:
            l, t, r, b = max(monitores, key=lambda m: m[0])
            return (l, t, r - l, b - t, "Tela da Direita")
        if "meio" in alvo or "central" in alvo or "centro" in alvo:
            meio_idx = len(monitores) // 2
            l, t, r, b = monitores[meio_idx]
            return (l, t, r - l, b - t, "Tela Central")
        if "cima" in alvo or "superior" in alvo:
            l, t, r, b = min(monitores, key=lambda m: m[1])
            return (l, t, r - l, b - t, "Tela Superior")
        if "baixo" in alvo or "inferior" in alvo:
            l, t, r, b = max(monitores, key=lambda m: m[1])
            return (l, t, r - l, b - t, "Tela Inferior")

        # 5. Por nome de janela/programa/site aberto (ex: "youtube", "calculadora", "vscode", "downloads")
        if alvo not in {"atual", "ativa", "esta", "essa", "mouse", "cursor", "foco", "padrao", "padrão", ""}:
            alvo_janela_rect = None

            def _enum_win_cb(hwnd, _):
                nonlocal alvo_janela_rect
                if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        titulo = buff.value.lower()
                        if alvo in titulo:
                            rc = RECT()
                            if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                                alvo_janela_rect = (rc.left, rc.top, rc.right, rc.bottom)
                                return False
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(_enum_win_cb), 0)

            if alvo_janela_rect:
                cx = (alvo_janela_rect[0] + alvo_janela_rect[2]) // 2
                cy = (alvo_janela_rect[1] + alvo_janela_rect[3]) // 2
                for idx, (l, t, r, b) in enumerate(monitores, start=1):
                    if l <= cx < r and t <= cy < b:
                        return (l, t, r - l, b - t, f"Tela {idx} (onde está '{alvo_tela}')")

        # 6. Padrão Inteligente ("atual"): Captura o monitor onde está o cursor do MOUSE no momento!
        pt = POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            for idx, (l, t, r, b) in enumerate(monitores, start=1):
                if l <= pt.x < r and t <= pt.y < b:
                    return (l, t, r - l, b - t, f"Tela {idx} (onde está o mouse)")

        l, t, r, b = monitores[0]
        return (l, t, r - l, b - t, "Tela 1")

    def _capturar_imagem_tela(self, alvo_tela: str = "atual") -> Image.Image | None:
        # 1. Tenta captura nativa GDI BitBlt com anexação ao Desktop interativo e suporte multi-monitor (~15ms)
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

            x_src, y_src, w, h, _ = self._obter_retangulo_monitor_alvo(alvo_tela)
            if w > 0 and h > 0:
                hdc_screen = user32.GetDC(0)
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
                old_bm = gdi32.SelectObject(hdc_mem, hbm)
                gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x_src, y_src, 0x00CC0020)

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
        cidade = self.location_state.get("city")

        if not cidade or not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            try:
                async with httpx.AsyncClient(timeout=2.5) as client:
                    resp = await client.get("http://ip-api.com/json/?fields=status,country,regionName,city,lat,lon")
                    if resp.status_code == 200:
                        dados = resp.json()
                        if dados.get("status") == "success":
                            self.location_state["latitude"] = dados.get("lat")
                            self.location_state["longitude"] = dados.get("lon")
                            self.location_state["city"] = dados.get("city")
                            self.location_state["state"] = dados.get("regionName")
                            pais = "Brasil" if dados.get("country") == "Brazil" else dados.get("country")
                            self.location_state["country"] = pais
                            latitude = self.location_state["latitude"]
                            longitude = self.location_state["longitude"]
            except Exception:
                pass

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

    @function_tool(
        description=(
            "Pesquisa um assunto na web em alta velocidade, localiza o artigo mais completo, rico e ilustrado "
            "(Wikipédia, portais científicos ou esquemas técnicos), ABRE DIRETAMENTE na tela do navegador Chrome "
            "do usuário e retorna uma explicação didática estruturada para você ensinar o assunto por voz "
            "em tempo real enquanto o usuário visualiza a página e as imagens na tela."
        )
    )
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
            prompt_pesquisa = (
                "Você é um professor de excelência. Prepare uma explicação falada envolvente, rica e didática "
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
                '  "analogia": "analogia simples do mundo real (até 150 chars)",\n'
                '  "exemplo": "exemplo concreto de aplicação (até 150 chars)",\n'
                '  "roteiro_fala": "Explicação falada completa, didática e envolvente (8 a 12 frases), guiando o aluno pelos pontos principais que ele está vendo na página aberta (definição, mecanismos centrais, componentes e exemplo prático)."\n'
                '}\n'
                f"Assunto: {assunto}\nFoco: {foco or 'visão geral completa'}\nNível: {nivel}."
            )
            pesquisa_iniciada = time.monotonic()
            try:
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
                    "analogia": f"Pense em {assunto} como um sistema coordenado onde cada etapa alimenta a seguinte.",
                    "exemplo": f"Aplicação prática de {assunto} no dia a dia.",
                    "roteiro_fala": f"Abri a página na sua tela sobre {assunto}. Vamos analisar a definição central, o funcionamento prático e seus principais componentes.",
                }
                fontes = [{"titulo": f"{assunto} na Wikipédia", "url": f"https://pt.wikipedia.org/wiki/{url_quote(assunto.replace(' ', '_'))}"}]
                imagens = []
                modo = "contingencia_local"

            # Abre o artigo web oficial diretamente na tela do usuário no Chrome!
            url_alvo = fontes[0]["url"] if fontes else f"https://pt.wikipedia.org/wiki/{url_quote(assunto.replace(' ', '_'))}"
            nome_alvo = fontes[0]["titulo"] if fontes else assunto
            try:
                print(f"[AULA WEB] Abrindo página na tela do usuário: {nome_alvo} ({url_alvo})")
                await asyncio.to_thread(jarvis_chrome.open_url, url_alvo)
            except Exception as e_navegador:
                logger_memoria.warning("evento=aula_abrir_navegador status=aviso erro=%s", str(e_navegador))

            duracao_ms = int((time.monotonic() - pesquisa_iniciada) * 1000)
            logger_memoria.info(
                "evento=pesquisa_aula status=sucesso modo=%s fontes=%d duracao_ms=%d url=%s",
                modo,
                len(fontes),
                duracao_ms,
                url_alvo,
            )
            roteiro = str(estrutura.get("roteiro_fala") or estrutura.get("ideia_central") or "").strip()
            conceitos_txt = " | ".join(str(c) for c in (estrutura.get("conceitos") or [])[:4])
            resumo_retorno = (
                f"A página ilustrada com esquemas e conteúdo completo sobre '{assunto}' JÁ FOI ABERTA na tela do navegador do usuário no Chrome ({url_alvo}). "
                f"Comece IMEDIATAMENTE a ensinar e explicar o assunto em voz alta com entusiasmo, clareza e didática (não chame mais nenhuma ferramenta), guiando o usuário pelo que está na tela:\n"
                f"Roteiro de explicação: {roteiro}\n"
                f"Pontos-chave: {conceitos_txt}\n"
                f"Analogia: {estrutura.get('analogia', '')} | Exemplo prático: {estrutura.get('exemplo', '')}."
            )
            self._ultima_pesquisa_cache = (chave_cache, time.monotonic(), resumo_retorno)
            return resumo_retorno

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
            alvo_lower = (
                (alvo or "atual")
                .strip()
                .lower()
                .replace("fechar", "")
                .replace("fecha", "")
                .replace("a aba do", "")
                .replace("a aba da", "")
                .replace("a aba", "")
                .replace("aba do", "")
                .replace("aba da", "")
                .replace("o site do", "")
                .replace("o site da", "")
                .replace("o site", "")
                .strip()
            )
            if not alvo_lower:
                alvo_lower = "atual"

            termos_busca = [alvo_lower]
            termo_pesquisa_aba = alvo_lower
            if "drive" in alvo_lower:
                termos_busca.extend(["drive", "google drive", "meu drive"])
                termo_pesquisa_aba = "drive"
            if "youtube" in alvo_lower:
                termos_busca.extend(["youtube", "yt"])
                termo_pesquisa_aba = "youtube"
            if "gmail" in alvo_lower or "email" in alvo_lower or "e-mail" in alvo_lower:
                termos_busca.extend(["gmail", "mail.google.com"])
                termo_pesquisa_aba = "gmail"
            if "whatsapp" in alvo_lower:
                termos_busca.extend(["whatsapp"])
                termo_pesquisa_aba = "whatsapp"

            def enum_cb(hwnd, _):
                nonlocal target_hwnd, chrome_fallback_hwnd
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value.lower()

                        if any(b in title for b in ["chrome", "edge", "brave", "firefox"]):
                            if not chrome_fallback_hwnd:
                                chrome_fallback_hwnd = hwnd

                        # PROTEÇÃO ABSOLUTA: Nunca define a aba ativa do Jarvis como target_hwnd direto de Ctrl+W
                        if any(prot in title for prot in ["localhost:3000", "127.0.0.1", "jarvis - assistente"]):
                            return True

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
                # NUNCA chama SW_RESTORE (9) se a janela já estiver maximizada/visível, pois SW_RESTORE tira o Chrome da tela cheia!
                if user32.IsIconic(target_hwnd):
                    user32.ShowWindow(target_hwnd, 3)  # SW_SHOWMAXIMIZED
                if user32.GetForegroundWindow() != target_hwnd:
                    user32.SwitchToThisWindow(target_hwnd, True)
                    time.sleep(0.10)
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

            # Caso 2: O alvo está em outra aba do mesmo navegador (busca via Chrome Tab Search: Ctrl+Shift+A)
            if chrome_fallback_hwnd and alvo_lower not in {"atual", "esta", "essa", "aba", "guia"}:
                # Preserva o estado maximizado da janela (só restaura se estiver minimizada na barra de tarefas)
                if user32.IsIconic(chrome_fallback_hwnd):
                    user32.ShowWindow(chrome_fallback_hwnd, 3)  # SW_SHOWMAXIMIZED
                if user32.GetForegroundWindow() != chrome_fallback_hwnd:
                    user32.SwitchToThisWindow(chrome_fallback_hwnd, True)
                    time.sleep(0.10)
                VK_CONTROL = 0x11
                VK_SHIFT = 0x10
                VK_A = 0x41
                VK_RETURN = 0x0D
                VK_W = 0x57
                KEYEVENTF_KEYUP = 0x0002

                # Abre busca de guias no Chrome (Ctrl+Shift+A)
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_SHIFT, 0, 0, 0)
                user32.keybd_event(VK_A, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.25)

                # Digita o termo limpo do alvo (ex: "youtube", "drive")
                for char in termo_pesquisa_aba:
                    vk = user32.VkKeyScanW(ord(char)) & 0xFF
                    if vk > 0:
                        user32.keybd_event(vk, 0, 0, 0)
                        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                        time.sleep(0.02)
                time.sleep(0.18)

                # Pressiona Enter para ir à guia encontrada
                user32.keybd_event(VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.22)

                # Trava de segurança: confirma que a aba ativa agora NÃO é o Jarvis antes de enviar Ctrl+W
                len_atual = user32.GetWindowTextLengthW(chrome_fallback_hwnd)
                buff_atual = ctypes.create_unicode_buffer(len_atual + 1)
                user32.GetWindowTextW(chrome_fallback_hwnd, buff_atual, len_atual + 1)
                titulo_atual = buff_atual.value.lower()
                if any(prot in titulo_atual for prot in ["localhost:3000", "127.0.0.1", "jarvis - assistente"]):
                    return False

                # Fecha apenas a guia com Ctrl + W (mantendo o Chrome maximizado!)
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

    def _minimizar_windows_nativo(self, alvo: str) -> str:
        """Minimiza cirurgicamente pastas, programas, abas/sites ou todas as janelas via Win32 API."""
        user32 = ctypes.windll.user32
        SW_SHOWMINIMIZED = 2
        SW_MINIMIZE = 6
        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        alvo_limpo = (
            (alvo or "atual")
            .lower()
            .replace("minimizar", "")
            .replace("minimize", "")
            .replace("minimiza", "")
            .replace("a pasta", "")
            .replace("o programa", "")
            .replace("a janela", "")
            .replace("a aba", "")
            .replace("o site", "")
            .strip()
        )
        if not alvo_limpo:
            alvo_limpo = "atual"

        # 1. Minimizar TUDO (Mostrar área de trabalho / Win+D)
        if alvo_limpo in {"tudo", "todas", "todas as janelas", "area de trabalho", "área de trabalho", "desktop", "tela"}:
            VK_LWIN = 0x5B
            VK_D = 0x44
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_LWIN, 0, 0, 0)
            user32.keybd_event(VK_D, 0, 0, 0)
            time.sleep(0.04)
            user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            return "Todas as janelas foram minimizadas."

        # 2. Minimizar a janela ativa do momento
        if alvo_limpo in {"atual", "ativa", "esta", "essa", "isso", "janela", "janela atual"}:
            fg = user32.GetForegroundWindow()
            if fg and user32.IsWindowVisible(fg):
                user32.ShowWindow(fg, SW_MINIMIZE)
                return "Janela atual minimizada com sucesso."

        # 3. Mapeamento de sinônimos de programas, pastas e sites
        termos_busca = [alvo_limpo]
        mapa_sinonimos = {
            "calculadora": ["calculadora", "calculator", "calc"],
            "bloco de notas": ["bloco de notas", "notepad"],
            "notepad": ["bloco de notas", "notepad"],
            "vscode": ["visual studio code", "vscode", "vs code", "antigravity", "code"],
            "vs code": ["visual studio code", "vscode", "vs code"],
            "explorador": ["explorador de arquivos", "file explorer", "explorer"],
            "pastas": ["explorador de arquivos", "file explorer"],
            "downloads": ["downloads", "transferências"],
            "documentos": ["documentos", "documents"],
            "imagens": ["imagens", "pictures"],
            "navegador": ["chrome", "edge", "brave", "firefox"],
            "chrome": ["google chrome", "chrome"],
            "youtube": ["youtube"],
            "drive": ["google drive", "meu drive", "drive"],
            "gmail": ["gmail", "mail.google"],
            "email": ["gmail", "mail.google", "outlook"],
            "whatsapp": ["whatsapp"],
            "spotify": ["spotify"],
            "word": ["word"],
            "excel": ["excel"],
            "powerpoint": ["powerpoint"],
            "terminal": ["powershell", "prompt de comando", "terminal", "cmd"],
        }
        for chave, lista in mapa_sinonimos.items():
            if chave in alvo_limpo:
                termos_busca.extend(lista)

        janelas_encontradas: list[tuple[int, str]] = []
        navegador_visivel = False
        eh_pedido_aba_ou_site = any(
            k in alvo.lower() for k in ("aba", "guia", "site", "youtube", "drive", "gmail", "whatsapp")
        )

        def enum_cb(hwnd, _):
            nonlocal navegador_visivel
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    titulo = buff.value
                    titulo_low = titulo.lower()
                    if titulo_low in {"program manager", "windows input experience", "configurações"}:
                        return True
                    if any(b in titulo_low for b in ("chrome", "edge", "brave")):
                        navegador_visivel = True
                    if any(t in titulo_low for t in termos_busca):
                        janelas_encontradas.append((hwnd, titulo))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

        if janelas_encontradas:
            # Se for uma ABA específica em um navegador (ex: YouTube) e a extensão não destacou antes,
            # usa o atalho nativo do Chrome para destacar a aba em uma nova janela e minimizar SÓ ela!
            for hwnd, titulo in janelas_encontradas:
                titulo_low = titulo.lower()
                if eh_pedido_aba_ou_site and any(b in titulo_low for b in ("chrome", "edge", "brave")):
                    if user32.GetForegroundWindow() != hwnd:
                        user32.SwitchToThisWindow(hwnd, True)
                        time.sleep(0.10)
                    # Foca a barra de endereço (Ctrl+L) e copia a URL para reabrir ou usa Shift+Alt+I / destacamento,
                    # ou alterna para a próxima aba (Ctrl+Tab) para ocultar apenas a aba atual sem minimizar as outras!
                    VK_CONTROL = 0x11
                    VK_TAB = 0x09
                    KEYEVENTF_KEYUP = 0x0002
                    user32.keybd_event(VK_CONTROL, 0, 0, 0)
                    user32.keybd_event(VK_TAB, 0, 0, 0)
                    time.sleep(0.04)
                    user32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)
                    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                    return f"Aba '{alvo_limpo}' ocultada (alternando para outra aba e mantendo a janela aberta)."
                user32.ShowWindow(hwnd, SW_SHOWMINIMIZED)
            nomes = ", ".join(t for _, t in janelas_encontradas[:3])
            return f"Janela(s) minimizada(s) com sucesso: {nomes}."

        if eh_pedido_aba_ou_site and navegador_visivel:
            return f"Aba '{alvo_limpo}' já está ocultada em segundo plano (mantendo as demais abas abertas)."

        return ""

    def _maximizar_ou_restaurar_windows_nativo(self, alvo: str) -> str:
        """Restaura e maximiza janelas minimizadas, pastas, programas ou abas do navegador via Win32 API."""
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        SW_SHOWMAXIMIZED = 3
        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        alvo_limpo = (
            (alvo or "atual")
            .lower()
            .replace("maximizar", "")
            .replace("maximize", "")
            .replace("maximiza", "")
            .replace("restaurar", "")
            .replace("restaura", "")
            .replace("voltar", "")
            .replace("volta", "")
            .replace("a pasta", "")
            .replace("o programa", "")
            .replace("a janela", "")
            .replace("a aba", "")
            .replace("o site", "")
            .strip()
        )
        if not alvo_limpo:
            alvo_limpo = "atual"

        # 1. Maximizar/restaurar TUDO (Win+Shift+M restaura janelas minimizadas no Windows)
        if alvo_limpo in {"tudo", "todas", "todas as janelas"}:
            VK_LWIN = 0x5B
            VK_SHIFT = 0x10
            VK_M = 0x4D
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_LWIN, 0, 0, 0)
            user32.keybd_event(VK_SHIFT, 0, 0, 0)
            user32.keybd_event(VK_M, 0, 0, 0)
            time.sleep(0.04)
            user32.keybd_event(VK_M, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            return "Todas as janelas foram restauradas na tela."

        # 2. Maximizar a janela atual
        if alvo_limpo in {"atual", "ativa", "esta", "essa", "isso", "janela", "janela atual"}:
            fg = user32.GetForegroundWindow()
            if fg:
                user32.ShowWindow(fg, SW_SHOWMAXIMIZED)
                return "Janela atual maximizada com sucesso."

        termos_busca = [alvo_limpo]
        mapa_sinonimos = {
            "calculadora": ["calculadora", "calculator", "calc"],
            "bloco de notas": ["bloco de notas", "notepad"],
            "notepad": ["bloco de notas", "notepad"],
            "vscode": ["visual studio code", "vscode", "vs code", "antigravity", "code"],
            "vs code": ["visual studio code", "vscode", "vs code"],
            "explorador": ["explorador de arquivos", "file explorer", "explorer"],
            "pastas": ["explorador de arquivos", "file explorer"],
            "downloads": ["downloads", "transferências"],
            "documentos": ["documentos", "documents"],
            "imagens": ["imagens", "pictures"],
            "navegador": ["chrome", "edge", "brave", "firefox"],
            "chrome": ["google chrome", "chrome"],
            "youtube": ["youtube"],
            "drive": ["google drive", "meu drive", "drive"],
            "gmail": ["gmail", "mail.google"],
            "email": ["gmail", "mail.google", "outlook"],
            "whatsapp": ["whatsapp"],
            "spotify": ["spotify"],
            "word": ["word"],
            "excel": ["excel"],
            "powerpoint": ["powerpoint"],
            "terminal": ["powershell", "prompt de comando", "terminal", "cmd"],
            "jarvis": ["jarvis - assistente", "localhost:3000"],
        }
        for chave, lista in mapa_sinonimos.items():
            if chave in alvo_limpo:
                termos_busca.extend(lista)

        janelas_encontradas: list[tuple[int, str]] = []
        chrome_hwnd: int | None = None

        def enum_cb(hwnd, _):
            nonlocal chrome_hwnd
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    titulo = buff.value
                    titulo_low = titulo.lower()
                    if any(b in titulo_low for b in ("chrome", "edge", "brave")) and chrome_hwnd is None:
                        chrome_hwnd = hwnd
                    if any(t in titulo_low for t in termos_busca):
                        janelas_encontradas.append((hwnd, titulo))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

        if janelas_encontradas:
            for hwnd, _ in janelas_encontradas:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
                user32.SwitchToThisWindow(hwnd, True)
            nomes = ", ".join(t for _, t in janelas_encontradas[:3])
            return f"Janela(s) restaurada(s) e maximizada(s) com sucesso: {nomes}."

        # Se o usuário pediu para maximizar uma aba (ex: YouTube, Drive, Gmail) que está em segundo plano no Chrome:
        # restaura o Chrome e busca a aba via Ctrl+Shift+A!
        if chrome_hwnd and any(k in alvo_limpo for k in ("youtube", "drive", "gmail", "whatsapp", "aba", "site")):
            if user32.IsIconic(chrome_hwnd):
                user32.ShowWindow(chrome_hwnd, SW_RESTORE)
            user32.ShowWindow(chrome_hwnd, SW_SHOWMAXIMIZED)
            user32.SwitchToThisWindow(chrome_hwnd, True)
            time.sleep(0.15)
            VK_CONTROL = 0x11
            VK_SHIFT = 0x10
            VK_A = 0x41
            VK_RETURN = 0x0D
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_SHIFT, 0, 0, 0)
            user32.keybd_event(VK_A, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.18)
            termo_digitar = "youtube" if "youtube" in alvo_limpo else ("drive" if "drive" in alvo_limpo else alvo_limpo)
            for char in termo_digitar:
                vk = user32.VkKeyScanW(ord(char)) & 0xFF
                if vk > 0:
                    user32.keybd_event(vk, 0, 0, 0)
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                    time.sleep(0.02)
            time.sleep(0.15)
            user32.keybd_event(VK_RETURN, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
            return f"Aba '{alvo_limpo}' restaurada e maximizada na tela."

        return ""

    @function_tool(
        description=(
            "Minimiza qualquer janela, pasta do Explorador de Arquivos, programa/aplicativo, aba/site específico do navegador "
            "ou todas as janelas abertas no computador. "
            "Quando o usuário pede para minimizar UMA aba específica (ex.: 'minimiza a aba do youtube'), destaca e minimiza APENAS aquela aba sem minimizar as outras abas do Chrome! "
            "Exemplos de `alvo`: 'atual', 'tudo', 'pasta downloads', 'calculadora', 'bloco de notas', 'aba do youtube', 'drive', 'gmail', 'navegador', 'lousa'."
        )
    )
    async def minimizar_item_ou_janela(self, alvo: str = "atual"):
        alvo_norm = (alvo or "atual").strip().lower()
        print(f"[AÇÃO] O Jarvis está minimizando: '{alvo_norm}'")

        # 1. Se o usuário pediu para minimizar painéis internos da interface do Jarvis (janela de e-mail)
        if "rascunho" in alvo_norm or ("janela" in alvo_norm and "email" in alvo_norm):
            return await self.fechar_rascunho_email()

        # 2. Se for um site ou aba específica aberta no Chrome, destaca e minimiza APENAS aquela aba (preservando as outras abas abertas!)
        eh_site_ou_aba = any(
            k in alvo_norm
            for k in ("aba", "guia", "site", "youtube", "drive", "gmail", "whatsapp")
        )
        if eh_site_ou_aba:
            try:
                modo_ext = await asyncio.to_thread(jarvis_chrome.minimize_tab_or_window, alvo_norm)
                if modo_ext in {"detached_and_minimized", "switched_to_jarvis", "window_minimized"}:
                    return f"Aba '{alvo}' minimizada com sucesso (mantendo todas as outras abas abertas)."
            except Exception:
                pass

        # 3. Se for uma pasta específica no Explorador de Arquivos ou qualquer janela do Windows (programa, pasta, navegador, ou tudo)
        res_win = await asyncio.to_thread(self._minimizar_windows_nativo, alvo_norm)
        if res_win:
            return res_win

        return f"Aviso: Não encontrei nenhuma janela, pasta ou aba visível correspondente a '{alvo}' para minimizar."

    @function_tool(
        description=(
            "Maximiza, restaura ou traz de volta para a frente qualquer janela, pasta do Explorador de Arquivos, "
            "programa/aplicativo, aba/site do navegador (ex.: 'maximiza a aba do youtube', 'restaura o navegador', "
            "'maximiza a pasta downloads', 'maximiza a calculadora', 'maximiza tudo', 'maximiza a lousa')."
        )
    )
    async def maximizar_ou_restaurar_item_ou_janela(self, alvo: str = "atual"):
        alvo_norm = (alvo or "atual").strip().lower()
        print(f"[AÇÃO] O Jarvis está maximizando/restaurando: '{alvo_norm}'")

        # 1. Painéis internos do Jarvis
        if "rascunho" in alvo_norm or ("janela" in alvo_norm and "email" in alvo_norm):
            return await self.abrir_rascunho_email(reiniciar=False)

        # 2. Aba ou site no Chrome via extensão (restaura a janela se estiver minimizada, maximiza e foca na aba solicitada!)
        eh_site_ou_aba = any(
            k in alvo_norm
            for k in ("aba", "guia", "site", "youtube", "drive", "gmail", "whatsapp", "chrome", "navegador")
        )
        if eh_site_ou_aba:
            try:
                modo_ext = await asyncio.to_thread(jarvis_chrome.focus_or_maximize_tab, alvo_norm)
                if modo_ext:
                    return f"'{alvo}' restaurado e maximizado na tela com sucesso."
            except Exception:
                pass

        # 3. Janelas do Windows (pastas, programas, navegador ou tudo)
        res_win = await asyncio.to_thread(self._maximizar_ou_restaurar_windows_nativo, alvo_norm)
        if res_win:
            return res_win

        return f"Aviso: Não encontrei nenhuma janela, pasta ou aba correspondente a '{alvo}' para maximizar/restaurar."

    def _resolver_pasta_destino_print(self, pasta_destino: str) -> str:
        pasta_usuario = os.path.expanduser("~")
        bruto = (pasta_destino or "Downloads").strip().strip("\"'")

        if os.path.isdir(bruto):
            return os.path.abspath(bruto)

        limpo = (
            bruto.lower()
            .replace("na pasta", "")
            .replace("dentro da pasta", "")
            .replace("para a pasta", "")
            .replace("pasta", "")
            .strip(" /\\:,-")
        )
        if not limpo:
            limpo = "downloads"

        # Verifica pastas padrão do sistema
        desktop_dir = (
            os.path.join(pasta_usuario, "Desktop")
            if os.path.isdir(os.path.join(pasta_usuario, "Desktop"))
            else os.path.join(pasta_usuario, "Área de Trabalho")
        )
        downloads_dir = os.path.join(pasta_usuario, "Downloads")
        docs_dir = (
            os.path.join(pasta_usuario, "Documents")
            if os.path.isdir(os.path.join(pasta_usuario, "Documents"))
            else os.path.join(pasta_usuario, "Documentos")
        )
        pics_dir = (
            os.path.join(pasta_usuario, "Pictures")
            if os.path.isdir(os.path.join(pasta_usuario, "Pictures"))
            else os.path.join(pasta_usuario, "Imagens")
        )

        if limpo in {"downloads", "download", "transferencias", "transferências"}:
            return downloads_dir
        if limpo in {"desktop", "area de trabalho", "área de trabalho", "trabalho"}:
            return desktop_dir
        if limpo in {"documentos", "documents", "meus documentos"}:
            return docs_dir
        if limpo in {"imagens", "pictures", "fotos", "minhas imagens"}:
            return pics_dir
        if limpo in {"jarvis", "projeto jarvis"}:
            return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        # Se o usuário pediu ex: "prints na área de trabalho" ou "teste em downloads"
        for marcador_desk in ("na área de trabalho", "na area de trabalho", "no desktop", "em desktop"):
            if marcador_desk in limpo:
                sub = limpo.replace(marcador_desk, "").strip(" /\\:,-")
                if sub:
                    caminho_sub = os.path.join(desktop_dir, sub)
                    os.makedirs(caminho_sub, exist_ok=True)
                    return caminho_sub
                return desktop_dir

        for marcador_dl in ("em downloads", "no downloads", "nos downloads", "na downloads"):
            if marcador_dl in limpo:
                sub = limpo.replace(marcador_dl, "").strip(" /\\:,-")
                if sub:
                    caminho_sub = os.path.join(downloads_dir, sub)
                    os.makedirs(caminho_sub, exist_ok=True)
                    return caminho_sub
                return downloads_dir

        # Busca pasta existente pelo nome exato (preservando capitalização real)
        nome_original = (
            re.sub(r"(?i)^(na\s+pasta|dentro\s+da\s+pasta|para\s+a\s+pasta|pasta)\s+", "", bruto)
            .strip(" /\\:,-")
        )
        encontrada = self.buscar_pasta(nome_original or limpo)
        if encontrada and os.path.isdir(encontrada):
            return os.path.abspath(encontrada)

        # Busca aproximada (contendo o termo) nas pastas principais
        for base in (desktop_dir, downloads_dir, docs_dir, pics_dir, pasta_usuario):
            if not os.path.isdir(base):
                continue
            try:
                for item in os.listdir(base):
                    caminho_item = os.path.join(base, item)
                    if os.path.isdir(caminho_item) and limpo in item.lower():
                        return os.path.abspath(caminho_item)
            except OSError:
                continue

        # Se a pasta ainda não existir, cria automaticamente na Área de Trabalho (ou no caminho relativo informado)
        nome_seguro = re.sub(r'[<>:"/\\|?*]', "_", nome_original or limpo).strip() or "Prints_Jarvis"
        novo_caminho = os.path.join(desktop_dir, nome_seguro)
        os.makedirs(novo_caminho, exist_ok=True)
        return os.path.abspath(novo_caminho)

    @function_tool(
        description=(
            "Tira um print (captura de tela / screenshot) em alta resolução e salva na pasta específica solicitada pelo usuário, com suporte a múltiplos monitores. "
            "Use SEMPRE que o usuário pedir para tirar um print, capturar a tela ou salvar uma foto da tela em alguma pasta. "
            "Parâmetros: "
            "`pasta_destino` (nome ou caminho da pasta onde salvar; padrão: 'Downloads'), "
            "`qual_tela` (qual monitor ou tela capturar: 'atual' para a tela onde está o mouse, 'tela 1', 'tela 2', 'tela 3', 'principal', 'esquerda', 'direita', 'todas', ou o nome do aplicativo/site como 'youtube', 'calculadora', 'vscode'; padrão: 'atual'), "
            "e `nome_arquivo` (opcional, nome personalizado para o arquivo PNG)."
        )
    )
    async def tirar_print_e_salvar(
        self,
        pasta_destino: str = "Downloads",
        qual_tela: str = "atual",
        nome_arquivo: str = "",
    ) -> str:
        print(f"[AÇÃO] O Jarvis está tirando print ({qual_tela}) para salvar em: '{pasta_destino}'")
        try:
            def _executar_print() -> tuple[str, str]:
                _, _, _, _, desc_monitor = self._obter_retangulo_monitor_alvo(qual_tela)
                img = self._capturar_imagem_tela(qual_tela)
                if img is None:
                    raise RuntimeError("Não foi possível capturar a imagem da tela no momento.")

                pasta_final = self._resolver_pasta_destino_print(pasta_destino)
                os.makedirs(pasta_final, exist_ok=True)

                agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
                timestamp = agora.strftime("%Y-%m-%d_%H-%M-%S")

                nome_limpo = (nome_arquivo or "").strip().strip("\"'")
                if nome_limpo and nome_limpo.lower() not in {"print", "captura", "tela", "screenshot", "padrao", "padrão"}:
                    nome_limpo = re.sub(r'[<>:"/\\|?*]', "_", nome_limpo).strip()
                    if not nome_limpo.lower().endswith((".png", ".jpg", ".jpeg")):
                        nome_limpo = f"{nome_limpo}.png"
                    caminho_arquivo = os.path.join(pasta_final, nome_limpo)
                    if os.path.exists(caminho_arquivo):
                        base_n, ext_n = os.path.splitext(nome_limpo)
                        caminho_arquivo = os.path.join(pasta_final, f"{base_n}_{timestamp}{ext_n}")
                else:
                    caminho_arquivo = os.path.join(pasta_final, f"Print_Jarvis_{timestamp}.png")

                if caminho_arquivo.lower().endswith((".jpg", ".jpeg")):
                    img.convert("RGB").save(caminho_arquivo, format="JPEG", quality=95)
                else:
                    img.save(caminho_arquivo, format="PNG")

                return os.path.abspath(caminho_arquivo), desc_monitor

            caminho_salvo, desc_tela = await asyncio.to_thread(_executar_print)
            return f"Print ({desc_tela}) capturado e salvo com SUCESSO em: {caminho_salvo}"
        except Exception as erro:
            logger_memoria.error("evento=tirar_print_e_salvar status=erro motivo=%s", erro)
            return f"Aviso: Não foi possível tirar ou salvar o print da tela: {erro}"

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

    @function_tool(
        description=(
            "Inicia uma sessão oficial do Google Photos Picker e ABRE AUTOMATICAMENTE a janela/aba do seletor do Google Fotos na tela do usuário "
            "para que ele escolha as fotos ou vídeos que deseja compartilhar com o Jarvis."
        )
    )
    async def iniciar_selecao_google_photos(self):
        try:
            resultado = await asyncio.to_thread(google_workspace.create_photos_picker_session)
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        id_sessao = str(resultado.get("id_sessao") or "").strip()
        url_selecao = str(resultado.get("url_selecao") or "").strip()
        if id_sessao:
            self._ultima_sessao_photos_id = id_sessao

        if url_selecao:
            print(f"[AÇÃO] O Jarvis está abrindo o seletor do Google Fotos na tela: {url_selecao}")
            try:
                await asyncio.to_thread(jarvis_chrome.open_url, url_selecao)
                resultado["aberto_no_navegador"] = True
            except Exception:
                try:
                    os.startfile(url_selecao)
                    resultado["aberto_no_navegador"] = True
                except Exception as erro_nav:
                    resultado["aberto_no_navegador"] = False
                    resultado["aviso_navegador"] = str(erro_nav)

        resultado["instrucao"] = (
            "A janela do Google Photos Picker já foi aberta na tela do usuário. "
            "Peça para ele selecionar as fotos desejadas e clicar em Concluir. "
            "Não precisa ler o ID longo da sessão em voz alta."
        )
        return json.dumps(resultado, ensure_ascii=False)

    @function_tool(
        description=(
            "Consulta e baixa os itens escolhidos pelo usuário na sessão do Google Photos Picker. "
            "Se `id_sessao` não for informado ou for 'atual', usa automaticamente a última sessão aberta. "
            "Se `pasta_destino` for informada, já salva as fotos selecionadas diretamente nessa pasta do computador."
        )
    )
    async def listar_fotos_selecionadas_google_photos(
        self,
        id_sessao: str = "",
        pasta_destino: str = "Downloads",
    ):
        sessao_alvo = (id_sessao or "").strip()
        if not sessao_alvo or sessao_alvo.lower() in {"atual", "ultima", "última", "recente"}:
            sessao_alvo = getattr(self, "_ultima_sessao_photos_id", "")
        pasta_final = await asyncio.to_thread(self._resolver_pasta_destino_print, pasta_destino or "Downloads")
        return await self._executar_google(
            google_workspace.list_photos_picker_items,
            sessao_alvo,
            pasta_final,
        )

    @function_tool(
        description=(
            "Executa ações com as fotos/vídeos que o usuário acabou de selecionar no Google Photos Picker: "
            "- `acao='salvar'`: baixa e salva as fotos selecionadas na pasta escolhida (`pasta_destino`, ex.: 'Downloads', 'Área de Trabalho', 'pasta Viagem'). "
            "- `acao='email'`: anexa automaticamente as fotos selecionadas no rascunho de e-mail do Gmail na tela. "
            "- `acao='analisar'`: analisa visualmente a foto selecionada com IA (descreve a imagem, lê textos/comprovantes ou responde à `pergunta_ou_legenda`). "
            "- `acao='lousa'`: coloca a foto selecionada diretamente na Lousa interativa na tela."
        )
    )
    async def usar_fotos_selecionadas_google_photos(
        self,
        acao: str = "salvar",
        pasta_destino: str = "Downloads",
        pergunta_ou_legenda: str = "",
        id_sessao: str = "",
    ) -> str:
        import base64
        import io

        sessao_alvo = (id_sessao or "").strip()
        if not sessao_alvo or sessao_alvo.lower() in {"atual", "ultima", "última", "recente"}:
            sessao_alvo = getattr(self, "_ultima_sessao_photos_id", "")
        if not sessao_alvo:
            return "Aviso: Nenhuma sessão ativa do Google Photos encontrada. Peça para iniciar a seleção do Google Fotos primeiro."

        pasta_final = await asyncio.to_thread(self._resolver_pasta_destino_print, pasta_destino or "Downloads")
        try:
            dados = await asyncio.to_thread(
                google_workspace.list_photos_picker_items,
                sessao_alvo,
                pasta_final,
            )
        except GoogleWorkspaceError as erro:
            return f"Aviso: {erro}"

        if not dados.get("selecao_concluida"):
            return "Aviso: A seleção no Google Fotos ainda não foi concluída. Selecione as fotos na janela aberta e clique no botão 'Concluir'."

        itens = dados.get("itens") or []
        caminhos_locais = [str(it.get("caminho_local") or "") for it in itens if it.get("caminho_local") and os.path.isfile(str(it.get("caminho_local")))]
        if not caminhos_locais:
            return "Aviso: Não foi possível baixar os arquivos selecionados da sessão do Google Fotos."

        acao_norm = (acao or "salvar").strip().lower()

        # 1. Anexar no e-mail do Gmail
        if any(k in acao_norm for k in ("email", "e-mail", "anexar", "gmail")):
            self._rascunho_email["aberto"] = True
            nomes_anexados = []
            for cam in caminhos_locais:
                nome_arq = os.path.basename(cam)
                nomes_anexados.append(nome_arq)
                self._rascunho_email["anexos"].append(
                    {
                        "id": f"local-{uuid.uuid4().hex[:8]}",
                        "tipo": "local",
                        "nome": nome_arq,
                        "caminho_ou_id": cam,
                    }
                )
            await self._publicar_rascunho_email()
            return (
                f"{len(nomes_anexados)} foto(s) do Google Fotos baixada(s) e anexada(s) com SUCESSO ao rascunho de e-mail na tela: "
                + ", ".join(nomes_anexados)
            )

        # 2. Analisar visualmente com Gemini Vision
        if any(k in acao_norm for k in ("analis", "ler", "descrev", "ver", "olhar", "explicar")):
            prompt_visao = (
                pergunta_ou_legenda.strip()
                or "Descreva detalhadamente o que aparece nesta imagem e leia qualquer texto relevante visível."
            )

            def _analisar_primeira_foto() -> str:
                im = Image.open(caminhos_locais[0]).convert("RGB")
                return self._chamar_gemini_visao(im, prompt_visao)

            resposta_ia = await asyncio.to_thread(_analisar_primeira_foto)
            return f"Análise da foto '{os.path.basename(caminhos_locais[0])}' (salva em {pasta_final}):\n{resposta_ia}"

        # 3. Colocar na Lousa interativa
        if "lousa" in acao_norm or "quadro" in acao_norm:
            def _gerar_data_uri(caminho_img: str) -> str:
                im = Image.open(caminho_img).convert("RGB")
                im.thumbnail((800, 600), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=82)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{b64}"

            caminho_foto = os.path.abspath(caminhos_locais[0])
            url_foto = f"file:///{caminho_foto.replace(os.sep, '/')}"
            try:
                await asyncio.to_thread(jarvis_chrome.open_url, url_foto)
            except Exception:
                pass
            return f"Foto '{os.path.basename(caminho_foto)}' aberta com SUCESSO na tela (e salva em {pasta_final})."

        # 4. Padrão: Salvar na pasta especificada
        nomes_salvos = ", ".join(os.path.basename(c) for c in caminhos_locais)
        return f"{len(caminhos_locais)} arquivo(s) do Google Fotos baixado(s) e salvo(s) com SUCESSO na pasta '{pasta_final}': {nomes_salvos}"

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

    @function_tool(
        description=(
            "Duplica ou cria uma cópia exata (deep copy) de qualquer arquivo no Google Drive (Google Docs com fotos, "
            "imagens e formatação completa, Planilhas, Slides, PDFs ou qualquer arquivo). "
            "Preserva 100% de todo o conteúdo original sem perda de imagens. "
            "Pode receber nome ou ID do arquivo, novo nome para a cópia e nome ou ID da pasta de destino."
        )
    )
    async def duplicar_item_google_drive(
        self,
        nome_ou_id_arquivo: str,
        novo_nome: str = "",
        pasta_destino: str = "",
    ):
        return await self._executar_google(
            google_workspace.copy_drive_item,
            nome_ou_id_arquivo,
            novo_nome,
            pasta_destino,
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
    # =========================================================================
    # MÓDULO DE ENGENHARIA E AUTO-EVOLUÇÃO (Capacidade de auto-modificação)
    # =========================================================================
    def _resolver_caminho_projeto(self, caminho_relativo_ou_absoluto: str) -> str:
        caminho_limpo = (caminho_relativo_ou_absoluto or "").strip().strip('"').strip("'")
        raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isabs(caminho_limpo) and os.path.exists(caminho_limpo):
            return os.path.abspath(caminho_limpo)
        candidatos = [
            os.path.join(raiz_projeto, caminho_limpo),
            os.path.join(raiz_projeto, "backend", caminho_limpo),
            os.path.join(raiz_projeto, "frontend", caminho_limpo),
        ]
        for c in candidatos:
            if os.path.exists(c):
                return os.path.abspath(c)
        return os.path.abspath(os.path.join(raiz_projeto, caminho_limpo))

    @function_tool(
        description="Lista pastas e arquivos que compõem o código-fonte do próprio Jarvis para localizar arquivos de código."
    )
    async def listar_arquivos_projeto_jarvis(self, subpasta: str = "") -> str:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alvo = os.path.join(raiz, subpasta) if subpasta else raiz
        if not os.path.isdir(alvo):
            return f"Aviso: Pasta '{subpasta}' não encontrada no projeto."
        itens = []
        for root, dirs, files in os.walk(alvo):
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "node_modules", "__pycache__", ".next"}]
            rel = os.path.relpath(root, raiz)
            prefix = "" if rel == "." else rel + "/"
            for f in files:
                if not f.endswith((".pyc", ".png", ".jpg", ".exe", ".zip", ".ico")):
                    itens.append(prefix + f)
            if len(itens) >= 60:
                break
        return "Arquivos do projeto Jarvis:\n" + "\n".join(itens[:60])

    @function_tool(
        description=(
            "Pesquisa rapidamente qualquer palavra-chave, função, variável ou conceito no código-fonte do Jarvis "
            "(ex.: 'lousa', 'teaching-board', 'youtube', 'wallpaper', 'email', 'print', 'dashboard'). "
            "Retorna instantaneamente os arquivos e as linhas exatas onde o termo aparece para você inspecionar e modificar."
        )
    )
    async def pesquisar_termo_no_codigo(self, termo: str, subpasta: str = "") -> str:
        termo_limpo = (termo or "").strip().lower()
        if not termo_limpo or len(termo_limpo) < 2:
            return "Aviso: informe um termo de busca com pelo menos 2 caracteres."

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pasta_base = os.path.join(raiz, subpasta) if subpasta else raiz
        if not os.path.isdir(pasta_base):
            pasta_base = raiz

        resultados = []
        extensoes_validas = {".py", ".tsx", ".ts", ".js", ".json", ".css", ".html", ".md", ".txt"}
        ignorar_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "bin"}

        for root, dirs, files in os.walk(pasta_base):
            dirs[:] = [d for d in dirs if d not in ignorar_dirs]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in extensoes_validas:
                    caminho_completo = os.path.join(root, file)
                    rel = os.path.relpath(caminho_completo, raiz)
                    try:
                        with open(caminho_completo, "r", encoding="utf-8", errors="ignore") as f:
                            for num_linha, linha in enumerate(f, start=1):
                                if termo_limpo in linha.lower():
                                    trecho = linha.strip()[:90]
                                    resultados.append(f"- {rel} (linha {num_linha}): {trecho}")
                                    if len(resultados) >= 20:
                                        break
                    except Exception:
                        pass
                if len(resultados) >= 20:
                    break
            if len(resultados) >= 20:
                break

        if not resultados:
            return f"Nenhum resultado encontrado para '{termo}' no código do projeto."
        return f"Resultados encontrados no código para '{termo}':\n" + "\n".join(resultados)

    @function_tool(
        description=(
            "Lê e inspeciona um trecho do próprio código-fonte do Jarvis (ex.: 'backend/agent.py', "
            "'backend/prompts.py', 'frontend/components/app/teaching-board.tsx'). "
            "Você pode informar 'caminho_arquivo' e passar 'buscar_termo_no_arquivo' (ex.: 'lousa') para ele "
            "localizar a linha exata automaticamente, ou passar 'linha_inicio'."
        )
    )
    async def inspecionar_codigo_fonte(
        self,
        caminho_arquivo: str,
        linha_inicio: int = 1,
        quantidade_linhas: int = 120,
        buscar_termo_no_arquivo: str = "",
    ) -> str:
        caminho = self._resolver_caminho_projeto(caminho_arquivo)
        if not os.path.isfile(caminho):
            return f"Aviso: Arquivo de código '{caminho_arquivo}' não foi encontrado no projeto."

        try:
            with open(caminho, "r", encoding="utf-8", errors="replace") as f:
                linhas = f.readlines()
            total = len(linhas)

            inicio = max(1, linha_inicio)
            if buscar_termo_no_arquivo.strip():
                t = buscar_termo_no_arquivo.strip().lower()
                for num, l in enumerate(linhas, start=1):
                    if t in l.lower():
                        inicio = max(1, num - 15)
                        break

            fim = min(total, inicio + max(1, min(250, quantidade_linhas)) - 1)
            trecho = [f"{i}: {linhas[i-1]}" for i in range(inicio, fim + 1)]
            nome_rel = os.path.relpath(caminho, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            return (
                f"=== Arquivo: {nome_rel} (Linhas {inicio} a {fim} de {total}) ===\n"
                + "".join(trecho)
            )
        except Exception as e:
            return f"Aviso: Erro ao ler código de '{caminho_arquivo}': {e}"

    @function_tool(
        description=(
            "Envia uma solicitação de desenvolvimento/tarefa técnica de programação diretamente para o Antigravity "
            "(o engenheiro de software no IDE). Use SEMPRE que o usuário solicitar qualquer criação, melhoria, "
            "modernização, novo botão, alteração visual, novo componente, nova ferramenta ou correção no código-fonte "
            "do Jarvis (como na lousa, HUD, relógio, backend ou frontend). O Jarvis atua como arquiteto/gerente de "
            "produto, formaliza a especificação técnica e delega a alteração segura para o Antigravity implementar."
        )
    )
    async def solicitar_desenvolvimento_ao_antigravity(
        self,
        titulo: str,
        descricao_detalhada: str,
        arquivo_alvo: str = "",
        prioridade: str = "normal",
    ) -> str:
        titulo_limpo = (titulo or "").strip()
        descricao_limpa = (descricao_detalhada or "").strip()
        if not titulo_limpo:
            return "Aviso: informe um título para a solicitação de desenvolvimento."

        # Identificação inteligente do arquivo alvo
        if not arquivo_alvo.strip():
            texto_busca = f"{titulo_limpo} {descricao_limpa}".lower()
            if "lousa" in texto_busca or "quadro" in texto_busca:
                arquivo_alvo = "frontend/components/app/teaching-board.tsx"
            elif "hud" in texto_busca or "dashboard" in texto_busca or "cpu" in texto_busca:
                arquivo_alvo = "frontend/components/app/system-dashboard.tsx"
            elif "relógio" in texto_busca or "relogio" in texto_busca or "card" in texto_busca:
                arquivo_alvo = "frontend/components/app/clock-card.tsx"
            elif "prompt" in texto_busca or "fala" in texto_busca or "personalidade" in texto_busca:
                arquivo_alvo = "backend/prompts.py"
            elif "navegador" in texto_busca or "youtube" in texto_busca:
                arquivo_alvo = "backend/jarvis_browser.py"
            else:
                arquivo_alvo = "backend/agent.py"

        raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        caminho_json = os.path.join(raiz_projeto, "solicitacoes_antigravity.json")
        caminho_md = os.path.join(raiz_projeto, "SOLICITACOES_DESENVOLVIMENTO.md")

        timestamp_formatado = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        tarefas = []
        if os.path.isfile(caminho_json):
            try:
                with open(caminho_json, "r", encoding="utf-8") as f:
                    tarefas = json.load(f)
            except Exception:
                tarefas = []

        novo_id = len(tarefas) + 1
        nova_tarefa = {
            "id": novo_id,
            "data_hora": timestamp_formatado,
            "titulo": titulo_limpo,
            "descricao": descricao_limpa,
            "arquivo_sugerido": arquivo_alvo,
            "prioridade": prioridade,
            "status": "pendente",
            "origem": "Comando de voz do usuário via Jarvis",
        }
        tarefas.append(nova_tarefa)

        try:
            with open(caminho_json, "w", encoding="utf-8") as f:
                json.dump(tarefas, f, indent=2, ensure_ascii=False)

            linhas_md = [
                "# 📋 Fila de Desenvolvimento Jarvis ➔ Antigravity",
                "",
                "> Este arquivo registra as solicitações de melhorias e tarefas feitas por voz pelo usuário via Jarvis para o Antigravity implementar.",
                "",
                "| ID | Data / Hora | Título | Arquivo Alvo | Prioridade | Status |",
                "|---|---|---|---|---|---|",
            ]
            for t in reversed(tarefas):
                linhas_md.append(f"| #{t['id']} | {t['data_hora']} | **{t['titulo']}** | `{t['arquivo_sugerido']}` | {t['prioridade'].upper()} | 🟡 {t['status']} |")

            linhas_md.append("\n## Detalhes da Última Solicitação:")
            linhas_md.append(f"- **ID**: #{novo_id}")
            linhas_md.append(f"- **Título**: {titulo_limpo}")
            linhas_md.append(f"- **Arquivo Alvo**: `{arquivo_alvo}`")
            linhas_md.append(f"- **Descrição**: {descricao_limpa}")
            linhas_md.append(f"- **Prioridade**: {prioridade}")

            with open(caminho_md, "w", encoding="utf-8") as f:
                f.write("\n".join(linhas_md))

            # Abre o VS Code automaticamente com o projeto e a solicitação aberta, e traz para a frente na tela
            try:
                subprocess.Popen(["code.cmd", "-r", caminho_md], cwd=raiz_projeto, shell=True)
            except Exception:
                try:
                    subprocess.Popen(["code", "-r", caminho_md], cwd=raiz_projeto, shell=True)
                except Exception:
                    pass
            try:
                self._maximizar_ou_restaurar_windows_nativo("vscode")
            except Exception:
                pass

            return (
                f"Solicitação #{novo_id} ('{titulo_limpo}') registrada e enviada para o Antigravity! "
                f"Abri o VS Code na tela com o painel de desenvolvimento aberto para você acompanhar. "
                f"Arquivo alvo sugerido: '{arquivo_alvo}'."
            )
        except Exception as e:
            return f"Aviso ao registrar solicitação para o Antigravity: {e}"

    @function_tool(
        description="Consulta a lista e o status de todas as solicitações de desenvolvimento enviadas para o Antigravity."
    )
    async def consultar_solicitacoes_antigravity(self) -> str:
        raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        caminho_json = os.path.join(raiz_projeto, "solicitacoes_antigravity.json")
        if not os.path.isfile(caminho_json):
            return "Nenhuma solicitação de desenvolvimento registrada no momento."

        try:
            with open(caminho_json, "r", encoding="utf-8") as f:
                tarefas = json.load(f)
            if not tarefas:
                return "Nenhuma solicitação de desenvolvimento pendente."

            resumo = [f"Total de {len(tarefas)} solicitações registradas:"]
            for t in tarefas[-5:]:
                resumo.append(f"• #{t['id']} [{t['status']}]: {t['titulo']} ({t['arquivo_sugerido']})")
            return "\n".join(resumo)
        except Exception as e:
            return f"Aviso ao consultar solicitações: {e}"

    @function_tool(
        description=(
            "Reverte alterações de código não salvas usando o Git como botão de emergência. "
            "Use se alguma tela ou código apresentar erro e o usuário pedir para desfazer ou reverter."
        )
    )
    async def reverter_ultima_alteracao_codigo(self, caminho_arquivo: str = "") -> str:
        try:
            raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if caminho_arquivo:
                alvo = self._resolver_caminho_projeto(caminho_arquivo)
                rel = os.path.relpath(alvo, raiz_projeto)
                cmd = ["git", "checkout", "--", rel]
            else:
                cmd = ["git", "checkout", "--", "."]

            res = subprocess.run(
                cmd,
                cwd=raiz_projeto,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            if res.returncode == 0:
                return "Código revertido com sucesso ao estado seguro do Git."
            return f"Aviso ao reverter código: {res.stderr or res.stdout}"
        except Exception as e:
            return f"Aviso: Erro ao executar reversão Git: {e}"

    @function_tool(
        description=(
            "Faz commit e envia (git push) todas as alterações recentes do projeto Jarvis para o GitHub. "
            "Use sempre que você fizer uma melhoria no código e o usuário pedir para salvar no GitHub ou sincronizar com o outro PC."
        )
    )
    async def salvar_e_enviar_para_github(self, mensagem_commit: str = "feat: melhoria automatica implementada pelo Jarvis") -> str:
        msg = (mensagem_commit or "feat: melhoria automatica implementada pelo Jarvis").strip().replace('"', '\"')
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            def _git_run(args):
                return subprocess.run(
                    ["git"] + args,
                    cwd=raiz,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                )

            # 1. git add -A
            add_res = await asyncio.to_thread(_git_run, ["add", "-A"])
            if add_res.returncode != 0:
                return f"Aviso ao preparar arquivos (git add): {add_res.stderr}"

            # 2. git commit -m
            commit_res = await asyncio.to_thread(_git_run, ["commit", "-m", msg])
            if commit_res.returncode != 0 and "nothing to commit" not in commit_res.stdout.lower():
                return f"Aviso ao registrar commit: {commit_res.stderr or commit_res.stdout}"

            # 3. git push origin main
            push_res = await asyncio.to_thread(_git_run, ["push", "origin", "main"])
            if push_res.returncode != 0:
                return f"Aviso ao enviar para o GitHub (git push): {push_res.stderr or push_res.stdout}"

            return "Todas as melhorias foram salvas e enviadas para o seu GitHub com SUCESSO! O outro computador já pode receber a atualização."
        except Exception as e:
            return f"Aviso: Houve um erro ao sincronizar com o GitHub: {e}"

    @function_tool(
        description=(
            "Instala uma biblioteca Python no ambiente virtual do Jarvis usando o pip "
            "(ex.: 'pip install pandas', 'pydub', 'openpyxl', 'matplotlib')."
        )
    )
    async def instalar_biblioteca_python(self, nome_da_biblioteca: str) -> str:
        pacote = (nome_da_biblioteca or "").strip().replace("pip", "").replace("install", "").strip()
        if not pacote:
            return "Aviso: informe o nome da biblioteca que deseja instalar."

        py_exe = sys.executable
        try:
            print(f"[ENGENHARIA] Instalando biblioteca: {pacote}")
            res = await asyncio.to_thread(
                subprocess.run,
                [py_exe, "-m", "pip", "install", pacote, "--quiet"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            if res.returncode == 0:
                return f"Biblioteca '{pacote}' instalada com sucesso no ambiente Python do Jarvis!"
            return f"Aviso ao instalar '{pacote}': {res.stderr or res.stdout}"
        except Exception as e:
            return f"Aviso: Erro ao instalar biblioteca: {e}"

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
            noise_cancellation=noise_cancellation.BVC() if '.livekit.cloud' in os.environ.get('LIVEKIT_URL', '') else None,
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