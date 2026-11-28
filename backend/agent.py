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
import mimetypes
import re
import html
import tempfile
import time
from datetime import datetime
import unicodedata
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
from jarvis_antigravity import obter_antigravity_engine, AntigravityEngine
from jarvis_tiktok_live import tiktok_manager

from livekit import agents, rtc
from livekit.agents import (
    AgentSession,
    Agent,
    ToolCallEnded,
    ToolCallStarted,
    ToolExecutionUpdatedEvent,
    function_tool,
)
from livekit.agents import utils as agent_utils
from livekit.agents.voice import room_io
from livekit.agents._exceptions import APIConnectionError
from livekit.plugins import (
    noise_cancellation,
    google,
)
import livekit.plugins.google.realtime.realtime_api as lk_google_realtime

# --- AUTO-RECUPERAÇÃO CONTRA EXPIRAÇÃO DE SESSÃO GEMINI LIVE (1008 Session Not Found) ---
_orig_realtime_main_task = lk_google_realtime.RealtimeSession._main_task

@agent_utils.log_exceptions(logger=lk_google_realtime.logger)
async def _robust_gemini_main_task(self) -> None:
    max_retries = self._opts.conn_options.max_retry

    while not self._msg_ch.closed:
        await self._close_active_session()
        self._session_should_close.clear()
        config = self._build_connect_config()
        session = None
        try:
            lk_google_realtime.logger.debug("connecting to Gemini Realtime API...")
            t0 = time.perf_counter()
            async with self._client.aio.live.connect(
                model=self._opts.model, config=config
            ) as session:
                self._report_connection_acquired(time.perf_counter() - t0)
                async with self._session_lock:
                    self._active_session = session

                    turns_dict, _ = self._chat_ctx.copy(
                        exclude_function_call=True,
                        exclude_handoff=True,
                        exclude_instructions=True,
                        exclude_empty_message=True,
                        exclude_config_update=True,
                    ).to_provider_format(format="google", inject_dummy_user_message=False)
                    turns = [lk_google_realtime.types.Content.model_validate(turn) for turn in turns_dict]
                    # Mantém apenas os últimos 8 turnos na reconexão para evitar estourar quota (1011 Resource Exhausted)
                    if len(turns) > 8:
                        turns = turns[-8:]
                    if turns:
                        await session.send_client_content(
                            turns=turns,
                            turn_complete=False,
                        )

                send_task = asyncio.create_task(
                    self._send_task(session), name="gemini-realtime-send"
                )
                recv_task = asyncio.create_task(
                    self._recv_task(session), name="gemini-realtime-recv"
                )
                restart_wait_task = asyncio.create_task(
                    self._session_should_close.wait(), name="gemini-restart-wait"
                )

                done, pending = await asyncio.wait(
                    [send_task, recv_task, restart_wait_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    if task is not restart_wait_task and task.exception():
                        lk_google_realtime.logger.error(f"error in task {task.get_name()}: {task.exception()}")
                        raise task.exception() or Exception(f"{task.get_name()} failed")

                if restart_wait_task not in done and self._msg_ch.closed:
                    break

                for task in pending:
                    await agent_utils.aio.cancel_and_wait(task)

                if self._session_error is not None:
                    err = self._session_error
                    self._session_error = None
                    raise err

        except asyncio.CancelledError:
            break
        except Exception as e:
            hint = lk_google_realtime._get_1008_error_hint(str(e))
            lk_google_realtime.logger.warning(f"Gemini Realtime API event/error: {e}")

            # Se o handle de resumption expirou ou houve colisão de sessão (1008) ou esgotamento de recurso (1011):
            # limpa o handle antigo e reconecta instantaneamente criando uma sessão limpa!
            str_err = str(e).lower()
            if (
                "1008" in str_err
                or "1011" in str_err
                or "quota" in str_err
                or "resource has been exhausted" in str_err
                or "entity already exists" in str_err
                or "session not found" in str_err
                or "expired" in str_err
            ):
                lk_google_realtime.logger.info(
                    f"Conflito, expiração ou cota Gemini Live detectado ({e}). "
                    "Resetando handle de retomada para abrir sessão limpa imediatamente..."
                )
                self._session_resumption_handle = None
                self._num_retries = 0
                self._emit_error(e, recoverable=True)
                await asyncio.sleep(0.5)
                continue

            if not self._msg_ch.closed:
                if getattr(e, "code", None) == 1007 or "1007" in str(e):
                    lk_google_realtime.logger.error(
                        "Gemini Live closed the session: context exhausted (1007).",
                        exc_info=e,
                    )
                    self._emit_error(e, recoverable=False)
                    raise APIConnectionError(
                        message="Gemini Live session context exhausted (1007)"
                    ) from e

                if self._num_retries >= max_retries:
                    self._emit_error(e, recoverable=False)
                    error_msg = f"Failed to connect to Gemini Live after {max_retries} attempts"
                    if hint:
                        error_msg += hint
                    raise APIConnectionError(message=error_msg) from e

                self._emit_error(e, recoverable=True)
                retry_interval = self._opts.conn_options._interval_for_retry(self._num_retries)
                lk_google_realtime.logger.warning(
                    f"Gemini Realtime API connection failed, retrying in {retry_interval}s",
                    exc_info=e,
                    extra={"attempt": self._num_retries, "max_retries": max_retries},
                )
                await asyncio.sleep(retry_interval)
                self._num_retries += 1
        finally:
            await self._close_active_session()

lk_google_realtime.RealtimeSession._main_task = _robust_gemini_main_task
# --- FIM DO PATCH DE AUTO-RECUPERAÇÃO GEMINI ---

from google_workspace import GoogleWorkspaceError, google_workspace
from jarvis_automacoes import gerenciador_automacoes
from jarvis_browser import JarvisBrowserError, jarvis_chrome
from prompts import (
    AGENT_INSTRUCTION,
    PERFIS_VOZ_JARVIS,
    SESSION_INSTRUCTION,
    normalizar_estilo_voz,
    obter_agent_instruction,
    obter_session_instruction,
)

_CAMINHO_CONFIG_VOZ = os.path.join(_backend_dir, "jarvis_voz_config.json")


def _carregar_estilo_voz() -> str:
    try:
        if os.path.exists(_CAMINHO_CONFIG_VOZ):
            with open(_CAMINHO_CONFIG_VOZ, "r", encoding="utf-8") as fp:
                dados = json.load(fp)
            return normalizar_estilo_voz(str(dados.get("style") or "descolado"))
    except Exception:
        pass
    return "descolado"


def _salvar_estilo_voz(estilo: str) -> str:
    chave = normalizar_estilo_voz(estilo)
    try:
        with open(_CAMINHO_CONFIG_VOZ, "w", encoding="utf-8") as fp:
            json.dump(
                {
                    "style": chave,
                    "updated_at": datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
                },
                fp,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as erro:
        logging.getLogger("jarvis.memoria").warning(
            "evento=salvar_estilo_voz status=erro motivo=%s", erro
        )
    return chave

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

# Diagnóstico persistente: grava avisos/erros do LiveKit e do Gemini Live em arquivo
# (o agente roda oculto e antes esses erros se perdiam, impossibilitando descobrir paradas).
_logger_livekit_diag = logging.getLogger("livekit")
if not any(getattr(h, "_jarvis_diag", False) for h in _logger_livekit_diag.handlers):
    try:
        _handler_diag = logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_agente_diagnostico.log"),
            encoding="utf-8",
        )
        _handler_diag.setLevel(logging.WARNING)
        _handler_diag.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        _handler_diag._jarvis_diag = True  # type: ignore[attr-defined]
        _logger_livekit_diag.addHandler(_handler_diag)
    except OSError:
        pass

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


class MemoriaCurtoPrazo:
    """Buffer circular com os últimos eventos da conversa (falas, respostas e ações)."""

    def __init__(self, capacidade: int = 40):
        from collections import deque
        self._eventos = deque(maxlen=capacidade)
        self._chamadas: dict[str, str] = {}

    def registrar(self, tipo: str, texto: str) -> None:
        texto = " ".join(str(texto or "").split())
        if not texto:
            return
        if len(texto) > 280:
            texto = texto[:280] + "…"
        self._eventos.append((time.time(), tipo, texto))

    def registrar_inicio_ferramenta(self, call_id: str, nome: str, argumentos: str) -> None:
        if nome == "consultar_memoria_curto_prazo":
            return
        self._chamadas[call_id] = nome
        self.registrar("ACAO", f"{nome}({argumentos})")

    def registrar_fim_ferramenta(self, call_id: str, status: str) -> None:
        nome = self._chamadas.pop(call_id, "")
        if nome:
            self.registrar("RESULTADO", f"{nome} -> {status}")

    def resumo(self, ultimos: int = 15) -> str:
        eventos = list(self._eventos)[-max(1, min(int(ultimos or 15), 40)):]
        if not eventos:
            return "Memória de curto prazo vazia: nada aconteceu ainda nesta sessão."
        rotulos = {"USUARIO": "USUÁRIO", "JARVIS": "JARVIS", "ACAO": "AÇÃO", "RESULTADO": "RESULTADO"}
        linhas = [
            f"[{datetime.fromtimestamp(ts).strftime('%H:%M:%S')}] {rotulos.get(tipo, tipo)}: {txt}"
            for ts, tipo, txt in eventos
        ]
        return "\n".join(linhas)


class Assistant(Agent):
    def __init__(self, location_state: dict[str, float | str], room: rtc.Room) -> None:
        self.location_state = location_state
        self._memoria_curta = MemoriaCurtoPrazo()
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
        self._calendar_lock = asyncio.Lock()
        self._ultimo_destino_calendar: str = ""
        self._ultimo_tempo_calendar: float = 0.0
        self._active_notebook_id: str = "geral"
        self._workspace_panel_open: bool = False
        self._ultimo_texto_notebook_transcrito: str = ""
        self._estilo_voz: str = _carregar_estilo_voz()
        self._active_agent_session: AgentSession | None = None
        perfil_voz = PERFIS_VOZ_JARVIS.get(self._estilo_voz, PERFIS_VOZ_JARVIS["descolado"])
        live_model = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")
        realtime_kwargs: dict = dict(
            model=live_model,
            voice=str(perfil_voz.get("voice", "Charon")),
            temperature=float(perfil_voz.get("temperature", 0.65)),
            tool_response_scheduling=genai_types.FunctionResponseScheduling.INTERRUPT,
            realtime_input_config=genai_types.RealtimeInputConfig(
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=150,
                    silence_duration_ms=650,
                )
            ),
            context_window_compression=genai_types.ContextWindowCompressionConfig(
                trigger_tokens=32000,
                sliding_window=genai_types.SlidingWindow(target_tokens=20000),
            ),
        )
        if "2.5" in live_model:
            realtime_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)

        super().__init__(
            instructions=obter_agent_instruction(self._estilo_voz),
            llm=google.beta.realtime.RealtimeModel(**realtime_kwargs),
        )

    async def _publicar_estilo_voz_hud(self) -> None:
        if not self.board_room or not self.board_room.local_participant:
            return
        try:
            payload = json.dumps(
                {"type": "jarvis_voice_style_sync", "style": self._estilo_voz}
            ).encode("utf-8")
            await self.board_room.local_participant.publish_data(
                payload, reliable=True, topic="jarvis.hud"
            )
        except Exception as erro:
            logger_memoria.warning("evento=sync_estilo_voz_hud status=erro motivo=%s", erro)

    async def aplicar_estilo_voz(self, estilo: str, falar_confirmacao: bool = True) -> str:
        novo_estilo = _salvar_estilo_voz(estilo)
        self._estilo_voz = novo_estilo
        perfil_cfg = PERFIS_VOZ_JARVIS.get(novo_estilo, PERFIS_VOZ_JARVIS["descolado"])
        nova_instrucao = obter_agent_instruction(novo_estilo)
        nova_voz = str(perfil_cfg.get("voice", "Charon"))
        nova_temp = float(perfil_cfg.get("temperature", 0.65))

        try:
            await self.update_instructions(nova_instrucao)
        except Exception as erro:
            logger_memoria.warning("evento=update_instructions_voz status=erro motivo=%s", erro)

        # Reconecta a sessão de áudio nativo Gemini Live em menos de 1s com a nova temperatura e persona
        try:
            sessoes_ativas = list(getattr(self.llm, "_sessions", []))
            for rt_sess in sessoes_ativas:
                rt_sess._session_resumption_handle = None
                rt_sess._opts.instructions = nova_instrucao
                rt_sess._opts.voice = nova_voz
                rt_sess._opts.temperature = nova_temp
                rt_sess._mark_restart_needed()
        except Exception as erro:
            logger_memoria.warning("evento=hotswap_voz_gemini status=erro motivo=%s", erro)

        await self._publicar_estilo_voz_hud()
        logger_memoria.info(
            "evento=estilo_voz_alterado estilo=%s temp=%s voz=%s",
            novo_estilo,
            nova_temp,
            nova_voz,
        )

        if falar_confirmacao and self._active_agent_session is not None:
            async def _confirmar_troca():
                try:
                    await asyncio.sleep(1.1)
                    if novo_estilo == "descolado":
                        await self._active_agent_session.generate_reply(
                            user_input=(
                                "O usuário acabou de ativar sua voz Descolada Original! "
                                "Fale imediatamente em Português do Brasil em 1 frase curta, animada, parceira e com carisma: "
                                "Opa, parceiro! Voz descolada original ativada — bora mandar ver, chefe!"
                            )
                        )
                    else:
                        await self._active_agent_session.generate_reply(
                            user_input=(
                                "O usuário acabou de ativar seu Modo Executivo Sério. "
                                "Fale imediatamente em Português do Brasil em 1 frase curta, calma e executiva: "
                                "Modo executivo ativado, chefe. Às ordens."
                            )
                        )
                except Exception as erro_fala:
                    logger_memoria.warning("evento=confirmacao_voz status=erro motivo=%s", erro_fala)

            asyncio.create_task(_confirmar_troca())

        rotulo = perfil_cfg.get("rotulo", novo_estilo)
        return f"Estilo de voz do Jarvis alterado com sucesso para: {rotulo}."

    @function_tool(
        description=(
            "Alterna imediatamente o estilo de voz e personalidade do Jarvis entre "
            "'descolado' (Voz Descolada Original: parceiro próximo, descontraído, humor fino e entonação viva) e "
            "'executivo' (Voz Executiva Séria: calma, reservada e formal, mantida salva para reverter quando quiser)."
        )
    )
    async def alterar_estilo_voz_jarvis(self, estilo: str = "descolado") -> str:
        return await self.aplicar_estilo_voz(estilo, falar_confirmacao=False)

    @function_tool(
        description=(
            "Consulta a MEMÓRIA DE CURTO PRAZO: os últimos eventos desta conversa (falas do usuário, "
            "suas respostas e ações/ferramentas executadas, com horário). Use SEMPRE que o usuário se referir "
            "ao que acabou de acontecer: 'o que você acabou de falar/abrir/fazer?', 'fecha ele', 'repete', "
            "'aquilo', 'isso', 'o último', 'por que fez isso?', 'sobre o que estávamos falando?'. "
            "Parâmetro `ultimos`: quantos eventos recentes ler (padrão 15)."
        )
    )
    async def consultar_memoria_curto_prazo(self, ultimos: int = 15) -> str:
        return self._memoria_curta.resumo(ultimos)

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

    async def _navegar_no_calendar_navegador(self, caminho_ou_url: str = "") -> None:
        destino = (caminho_ou_url or "").strip()
        if not destino.startswith("http"):
            if destino.startswith("/"):
                url_final = f"https://calendar.google.com/calendar/u/0/r{destino}"
            elif destino.startswith("#"):
                url_final = f"https://calendar.google.com/calendar/u/0/r{destino}"
            elif destino:
                url_final = f"https://calendar.google.com/calendar/u/0/r/{destino}"
            else:
                url_final = "https://calendar.google.com/calendar/u/0/r"
        else:
            url_final = destino

        agora = time.monotonic()
        async with self._calendar_lock:
            if url_final == self._ultimo_destino_calendar and (agora - self._ultimo_tempo_calendar) < 3.0:
                return
            self._ultimo_destino_calendar = url_final
            self._ultimo_tempo_calendar = agora
            try:
                await asyncio.to_thread(jarvis_chrome.open_url, url_final)
            except Exception as erro:
                logger_memoria.warning("evento=navegar_calendar status=erro motivo=%s", erro)

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

    async def _adicionar_mensagem_chat_notebook(
        self,
        role: str,
        content: str,
        sources: list[str] | None = None,
        buscou_web: bool = False,
        notebook_id: str | None = None,
        expert: str | None = None,
        expert_name: str | None = None,
    ):
        nb_id = (notebook_id or self._active_notebook_id or "geral").strip()
        texto_limpo = (content or "").strip()
        if not texto_limpo:
            return

        pasta_nb = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id)
        if not os.path.isdir(pasta_nb):
            try:
                os.makedirs(pasta_nb, exist_ok=True)
                meta_path = os.path.join(pasta_nb, "notebook.json")
                if not os.path.isfile(meta_path):
                    with open(meta_path, "w", encoding="utf-8") as fm:
                        json.dump({
                            "id": nb_id,
                            "name": nb_id.capitalize(),
                            "createdAt": datetime.now().isoformat(),
                            "updatedAt": datetime.now().isoformat(),
                        }, fm, indent=2)
            except Exception:
                pass

        # Determina o especialista se for resposta do assistente
        id_exp = expert
        nome_exp = expert_name
        if role == "assistant" and not id_exp:
            try:
                meta_p = os.path.join(pasta_nb, "notebook.json")
                if os.path.isfile(meta_p):
                    with open(meta_p, "r", encoding="utf-8") as f_meta:
                        meta_json = json.load(f_meta)
                        id_exp = meta_json.get("expert")
            except Exception:
                pass

        chat_path = os.path.join(pasta_nb, "chat_history.json")
        msgs = []
        if os.path.isfile(chat_path):
            try:
                with open(chat_path, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                    if isinstance(dados, list):
                        msgs = dados
            except Exception:
                msgs = []

        # Evita duplicar se for a mesma mensagem recente
        if msgs and msgs[-1].get("role") == role and msgs[-1].get("content") == texto_limpo:
            return

        nova_msg = {
            "id": f"msg-v-{int(time.time() * 1000)}-{role[0]}",
            "role": role,
            "content": texto_limpo,
            "timestamp": datetime.now().isoformat(),
        }
        if sources:
            nova_msg["sources"] = sources
        if buscou_web:
            nova_msg["buscouWeb"] = True
        if id_exp:
            nova_msg["expert"] = id_exp
        if nome_exp:
            nova_msg["expertName"] = nome_exp

        msgs.append(nova_msg)
        try:
            with open(chat_path, "w", encoding="utf-8") as f:
                json.dump(msgs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger_memoria.warning("evento=salvar_chat_notebook erro=%s", e)

        # Atualiza a interface do frontend via LiveKit data topic
        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebook_chat_update",
                        "notebookId": nb_id,
                        "messages": msgs,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception as e_pub:
            logger_memoria.warning("evento=publicar_chat_notebook erro=%s", e_pub)

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
        if not nome_pasta:
            return None
        pasta_usuario = os.path.expanduser("~")
        if os.path.isdir(nome_pasta):
            return os.path.abspath(nome_pasta)

        nome_limpo = (
            (nome_pasta or "")
            .strip()
            .lower()
            .replace("pasta ", "")
            .replace("a pasta ", "")
            .strip(" \"'/\\")
        )
        if not nome_limpo:
            return None

        def _corresponde(alvo_candidato: str) -> bool:
            c = alvo_candidato.strip().lower()
            if not c:
                return False
            if c == nome_limpo or c == nome_pasta.lower():
                return True
            # Singular / Plural (ex: projeto vs projetos, download vs downloads)
            if c.rstrip('s') == nome_limpo.rstrip('s'):
                return True
            if len(nome_limpo) >= 4 and (c.startswith(nome_limpo) or nome_limpo.startswith(c)):
                return True
            return False

        mapa_pastas_sistema = {
            "downloads": [os.path.join(pasta_usuario, "Downloads")],
            "download": [os.path.join(pasta_usuario, "Downloads")],
            "transferencias": [os.path.join(pasta_usuario, "Downloads")],
            "transferências": [os.path.join(pasta_usuario, "Downloads")],
            "desktop": [os.path.join(pasta_usuario, "Desktop"), os.path.join(pasta_usuario, "Área de Trabalho")],
            "area de trabalho": [os.path.join(pasta_usuario, "Desktop"), os.path.join(pasta_usuario, "Área de Trabalho")],
            "área de trabalho": [os.path.join(pasta_usuario, "Desktop"), os.path.join(pasta_usuario, "Área de Trabalho")],
            "documentos": [os.path.join(pasta_usuario, "Documents"), os.path.join(pasta_usuario, "Documentos")],
            "documento": [os.path.join(pasta_usuario, "Documents"), os.path.join(pasta_usuario, "Documentos")],
            "documents": [os.path.join(pasta_usuario, "Documents"), os.path.join(pasta_usuario, "Documentos")],
            "imagens": [os.path.join(pasta_usuario, "Pictures"), os.path.join(pasta_usuario, "Imagens")],
            "pictures": [os.path.join(pasta_usuario, "Pictures"), os.path.join(pasta_usuario, "Imagens")],
            "fotos": [os.path.join(pasta_usuario, "Pictures"), os.path.join(pasta_usuario, "Imagens")],
            "videos": [os.path.join(pasta_usuario, "Videos"), os.path.join(pasta_usuario, "Vídeos")],
            "vídeos": [os.path.join(pasta_usuario, "Videos"), os.path.join(pasta_usuario, "Vídeos")],
            "musicas": [os.path.join(pasta_usuario, "Music"), os.path.join(pasta_usuario, "Músicas")],
            "músicas": [os.path.join(pasta_usuario, "Music"), os.path.join(pasta_usuario, "Músicas")],
        }
        for chave, rotas in mapa_pastas_sistema.items():
            if _corresponde(chave):
                for cand in rotas:
                    if os.path.isdir(cand):
                        return cand

        # 1. Checa Acesso Rápido (Quick Access) do Windows (onde o usuário fixa pastas como PROJETOS!)
        try:
            cmd_qa = (
                'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
                '"(New-Object -ComObject Shell.Application).Namespace(\'shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}\').Items() '
                '| ForEach-Object { try { if ($_.Path -and (Test-Path -Path $_.Path -PathType Container)) { $_.Path } } catch {} }"'
            )
            res_qa = subprocess.run(cmd_qa, shell=True, capture_output=True, text=True, timeout=2)
            if res_qa.stdout:
                for line in res_qa.stdout.splitlines():
                    p = line.strip()
                    if p and os.path.isdir(p):
                        base_p = os.path.basename(os.path.normpath(p))
                        if _corresponde(base_p):
                            return p
        except Exception:
            pass

        # 2. Verifica janelas do Windows Explorer abertas no sistema
        try:
            cmd_ps = (
                'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
                '"(New-Object -ComObject Shell.Application).Windows() | ForEach-Object { try { $_.Document.Folder.Self.Path } catch {} }"'
            )
            res = subprocess.run(cmd_ps, shell=True, capture_output=True, text=True, timeout=2)
            if res.stdout:
                for line in res.stdout.splitlines():
                    p = line.strip()
                    if p and os.path.isdir(p):
                        base_p = os.path.basename(os.path.normpath(p))
                        if _corresponde(base_p):
                            return p
        except Exception:
            pass

        # 3. Varredura rápida em raízes prioritárias (Dropbox, OneDrive, Documentos, Desktop, C:\)
        raizes_busca = []
        dropbox_path = os.path.join(pasta_usuario, "Dropbox")
        if os.path.isdir(dropbox_path):
            raizes_busca.append(dropbox_path)
        for env_var in ["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]:
            od = os.environ.get(env_var)
            if od and os.path.isdir(od) and od not in raizes_busca:
                raizes_busca.append(od)
        raizes_busca.extend([
            os.path.join(pasta_usuario, "Desktop"),
            os.path.join(pasta_usuario, "Área de Trabalho"),
            os.path.join(pasta_usuario, "Documents"),
            os.path.join(pasta_usuario, "Documentos"),
            os.path.join(pasta_usuario, "Downloads"),
            pasta_usuario,
            "C:\\",
            "D:\\",
        ])
        skip_dirs = {
            'appdata', 'node_modules', '.git', '__pycache__', 'windows',
            'program files', 'program files (x86)', 'programdata', 'local settings',
            '.vscode', '.gemini', '.cache', 'temp', 'recovery', '$recycle.bin', 'system volume information'
        }
        for raiz in raizes_busca:
            if not raiz or not os.path.isdir(raiz):
                continue
            try:
                for root, dirs, _ in os.walk(raiz):
                    dirs[:] = [d for d in dirs if d.lower() not in skip_dirs and not d.startswith('.')]
                    rel = os.path.relpath(root, raiz)
                    depth = 0 if rel == '.' else len(rel.split(os.sep))
                    if depth > 4:
                        dirs[:] = []
                        continue
                    for d in dirs:
                        if _corresponde(d):
                            return os.path.join(root, d)
            except Exception:
                continue
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
    def _listar_monitores_windows(self) -> list[dict]:
        """Retorna lista ordenada dos monitores conectados no Windows (DISPLAY1 = Tela 1, etc.)."""
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        brutos: list[tuple[int, int, int, int, int, bool, str]] = []

        def _monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                r = mi.rcMonitor
                m_num = 99
                dev = mi.szDevice or ""
                match_d = re.search(r"(\d+)$", dev)
                if match_d:
                    m_num = int(match_d.group(1))
                elif mi.dwFlags & 1:
                    m_num = 1
                brutos.append((m_num, int(r.left), int(r.top), int(r.right), int(r.bottom), bool(mi.dwFlags & 1), dev))
            elif lprcMonitor:
                r = lprcMonitor.contents
                brutos.append((99, int(r.left), int(r.top), int(r.right), int(r.bottom), False, ""))
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

        ordenados = sorted(brutos, key=lambda m: (m[0], m[1], m[2]))
        monitores: list[dict] = []
        for idx, (_num, l, t, r, b, eh_principal, dev) in enumerate(ordenados, start=1):
            w = r - l
            h = b - t
            pos_partes = []
            if eh_principal or (l == 0 and t == 0):
                pos_partes.append("Principal")
            if t < -100:
                pos_partes.append("Acima")
            elif t > 100:
                pos_partes.append("Abaixo")
            if l >= 1000:
                pos_partes.append("Direita")
            elif l <= -1000:
                pos_partes.append("Esquerda")
            monitores.append({
                "numero": idx,
                "device": dev,
                "principal": eh_principal or (l == 0 and t == 0),
                "left": l,
                "top": t,
                "right": r,
                "bottom": b,
                "width": w,
                "height": h,
                "posicao": ", ".join(pos_partes) if pos_partes else f"Tela {idx}",
            })
        return monitores

    @function_tool(
        description="Lista quantas telas/monitores estão conectados ao computador, suas numerações (Tela 1, 2, 3...) e posições."
    )
    async def listar_telas_ou_monitores(self) -> str:
        monitores = await asyncio.to_thread(self._listar_monitores_windows)
        if not monitores:
            return "Detectei apenas 1 tela principal."
        partes = [
            f"Tela {m['numero']} ({m['posicao']}, {m['width']}x{m['height']})"
            for m in monitores
        ]
        return f"Você tem {len(monitores)} telas conectadas: " + "; ".join(partes) + "."

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

        monitores_dict = self._listar_monitores_windows()
        monitores = [(m["left"], m["top"], m["right"], m["bottom"]) for m in monitores_dict]
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

    def _obter_posicao_atual_mouse(self) -> tuple[int, int]:
        user32 = ctypes.windll.user32
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
        return 0, 0

    def _executar_clique_mouse(
        self,
        x: int,
        y: int,
        tipo_clique: str = "esquerdo",
        clique_duplo: bool = False,
    ) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.04)
        tipo = (tipo_clique or "esquerdo").strip().lower()
        if "direito" in tipo:
            user32.mouse_event(0x0008, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTDOWN
            time.sleep(0.03)
            user32.mouse_event(0x0010, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTUP
            if clique_duplo:
                time.sleep(0.08)
                user32.mouse_event(0x0008, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTDOWN
                time.sleep(0.03)
                user32.mouse_event(0x0010, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTUP
        elif "meio" in tipo or "scroll" in tipo:
            user32.mouse_event(0x0020, 0, 0, 0, 0)  # MOUSEEVENTF_MIDDLEDOWN
            time.sleep(0.03)
            user32.mouse_event(0x0040, 0, 0, 0, 0)  # MOUSEEVENTF_MIDDLEUP
        else:
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
            time.sleep(0.03)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
            if clique_duplo:
                time.sleep(0.08)
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
                time.sleep(0.03)
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP

    def _executar_scroll_mouse(self, quantidade_linhas: int = 5, direcao: str = "baixo") -> None:
        user32 = ctypes.windll.user32
        passos = abs(int(quantidade_linhas or 5))
        delta = -120 if str(direcao).lower() in ("baixo", "down", "descer", "descendo") else 120
        for _ in range(passos):
            user32.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
            time.sleep(0.03)

    def _localizar_elemento_tela(self, alvo: str, qual_tela: str = "atual") -> dict:
        termo = (alvo or "").strip()
        if not termo:
            return {"encontrado": False, "motivo": "Nenhum alvo informado"}

        img = self._capturar_imagem_tela(qual_tela)
        if not img:
            return {"encontrado": False, "motivo": "Não foi possível capturar a imagem da tela"}

        x_src, y_src, w, h, desc_monitor = self._obter_retangulo_monitor_alvo(qual_tela)
        largura_orig, altura_orig = img.size

        prompt = (
            f"Você é um sistema de visão computacional de altíssima precisão para controle de mouse e automação de interface no Windows.\n"
            f"O usuário deseja interagir com o seguinte alvo na tela: '{termo}'.\n"
            f"Exemplos de alvos possíveis:\n"
            f"- Campo de pesquisa / barra de busca / input de texto (ex: 'Pesquise fotos e álbuns', 'barra de pesquisa', 'campo de texto');\n"
            f"- Botão de ação (ex: 'Concluir', 'Salvar', 'Enviar', 'Pesquisar', 'Adicionar', 'Cancelar');\n"
            f"- Foto, miniatura ou imagem (ex: 'foto da geladeira', 'painel do carro', 'terceira foto');\n"
            f"- Círculo ou caixa de seleção / checkbox para marcar ou desselecionar uma foto/item (ex: 'círculo de seleção', 'marcar foto');\n"
            f"- Link, aba do navegador, ícone ou menu.\n\n"
            f"Analise a imagem da tela e localize com máxima precisão a caixa delimitadora 2D (bounding box) "
            f"do elemento correspondente no formato [ymin, xmin, ymax, xmax] normalizado de 0 a 1000.\n"
            f"Se o usuário pedir para 'marcar' ou 'selecionar' um item (como uma foto numa galeria), localize preferencialmente o círculo ou caixa de seleção daquele item.\n"
            f"Se houver múltiplos elementos semelhantes, escolha o que melhor corresponde à descrição '{termo}'. Se for por ordem ('primeira', 'segunda', 'terceira'), "
            f"respeite a ordem de leitura (esquerda para direita, cima para baixo).\n"
            f"Retorne sua resposta ESTRITAMENTE em formato JSON:\n"
            f'{{"encontrado": true, "box_2d": [ymin, xmin, ymax, xmax], "descricao": "descrição concisa do elemento localizado"}}\n'
            f'Se o elemento não estiver visível nesta tela, retorne:\n'
            f'{{"encontrado": false, "motivo": "não localizado visualmente na tela atual"}}'
        )

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"encontrado": False, "motivo": "GOOGLE_API_KEY não configurada"}

        client = genai.Client(api_key=api_key)
        resp_json = None
        for m in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
            try:
                resp = client.models.generate_content(model=m, contents=[img, prompt])
                if resp and resp.text:
                    txt = resp.text.strip()
                    if "```" in txt:
                        m_json = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL)
                        if m_json:
                            txt = m_json.group(1)
                    resp_json = json.loads(txt)
                    if resp_json:
                        break
            except Exception:
                continue

        if not resp_json or not resp_json.get("encontrado"):
            motivo = resp_json.get("motivo", "não localizado visualmente") if resp_json else "tempo limite de visão"
            return {"encontrado": False, "motivo": motivo}

        box = resp_json.get("box_2d")
        if not box or len(box) != 4:
            return {"encontrado": False, "motivo": "coordenadas inválidas"}

        ymin, xmin, ymax, xmax = [float(v) for v in box]
        x_norm = (xmin + xmax) / 2.0
        y_norm = (ymin + ymax) / 2.0

        target_x = x_src + int((x_norm / 1000.0) * largura_orig)
        target_y = y_src + int((y_norm / 1000.0) * altura_orig)
        descricao = resp_json.get("descricao", termo)

        return {
            "encontrado": True,
            "target_x": target_x,
            "target_y": target_y,
            "descricao": descricao,
            "desc_monitor": desc_monitor,
            "box_2d": box,
        }

    @staticmethod
    def _executar_limpar_campo() -> None:
        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002
        VK_CONTROL = 0x11
        VK_A = 0x41
        VK_BACK = 0x08

        time.sleep(0.05)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_A, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_BACK, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_BACK, 0, KEYEVENTF_KEYUP, 0)

    def _executar_teclas_teclado(self, comando_tecla: str, repeticoes: int = 1) -> tuple[bool, str]:
        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002

        KEY_MAP = {
            'enter': 0x0D, 'return': 0x0D, 'intro': 0x0D,
            'esc': 0x1B, 'escape': 0x1B,
            'tab': 0x09,
            'backspace': 0x08, 'back': 0x08, 'apagar': 0x08,
            'delete': 0x2E, 'del': 0x2E,
            'space': 0x20, 'espaco': 0x20, 'espaço': 0x20,
            'up': 0x26, 'seta_cima': 0x26, 'cima': 0x26,
            'down': 0x28, 'seta_baixo': 0x28, 'baixo': 0x28,
            'left': 0x27, 'seta_esquerda': 0x27, 'esquerda': 0x27,
            'right': 0x29, 'seta_direita': 0x29, 'direita': 0x29,
            'home': 0x24, 'end': 0x23, 'fim': 0x23,
            'pageup': 0x21, 'pgup': 0x21,
            'pagedown': 0x22, 'pgdn': 0x22,
        }
        for i in range(1, 13):
            KEY_MAP[f"f{i}"] = 0x6F + i

        MODIFIERS = {
            'ctrl': 0x11, 'control': 0x11,
            'alt': 0x12,
            'shift': 0x10,
            'win': 0x5B, 'windows': 0x5B,
        }

        parts = [p.strip().lower() for p in (comando_tecla or "").replace('+', ' ').replace('-', ' ').split() if p.strip()]
        if not parts:
            return False, "Nenhuma tecla especificada."

        mods = []
        main_key = None
        for p in parts:
            if p in MODIFIERS:
                mods.append(MODIFIERS[p])
            elif p in KEY_MAP:
                main_key = KEY_MAP[p]
            elif len(p) == 1 and p.isalnum():
                main_key = ord(p.upper())

        if main_key is None and not mods:
            return False, f"Tecla ou atalho '{comando_tecla}' não reconhecido."

        reps = max(1, min(int(repeticoes or 1), 20))
        for _ in range(reps):
            for m in mods:
                user32.keybd_event(m, 0, 0, 0)
                time.sleep(0.02)
            if main_key is not None:
                user32.keybd_event(main_key, 0, 0, 0)
                time.sleep(0.03)
                user32.keybd_event(main_key, 0, KEYEVENTF_KEYUP, 0)
            for m in reversed(mods):
                time.sleep(0.02)
                user32.keybd_event(m, 0, KEYEVENTF_KEYUP, 0)
            if reps > 1:
                time.sleep(0.08)

        msg = f"Tecla/Atalho '{comando_tecla}' pressionado com sucesso"
        if reps > 1:
            msg += f" {reps} vezes."
        else:
            msg += "."
        return True, msg

    def _chamar_gemini_visao(self, imagem_pil: Image.Image, prompt: str) -> str:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Aviso: Chave de API do Gemini não configurada no ambiente."
        client = genai.Client(api_key=api_key)

        img_otimizada = imagem_pil.copy()
        if img_otimizada.width > 1280 or img_otimizada.height > 720:
            img_otimizada.thumbnail((1280, 720), Image.Resampling.LANCZOS)

        import io
        buf = io.BytesIO()
        img_otimizada.convert("RGB").save(buf, format="JPEG", quality=85)
        part_img = genai_types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

        modelos = ["gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]
        ultimo_erro = None
        for m in modelos:
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=[prompt, part_img],
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
                    if reader.is_encrypted:
                        senhas_tentativa = ["2138", "879", ""]
                        for t_auto in gerenciador_automacoes.listar_agendados():
                            s_auto = str(t_auto.get("senha_anexo") or "").strip()
                            if s_auto and s_auto not in senhas_tentativa:
                                senhas_tentativa.insert(0, s_auto)
                        for s_try in senhas_tentativa:
                            try:
                                if reader.decrypt(s_try):
                                    break
                            except Exception:
                                continue
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

        # Atualiza em tempo real o arquivo de texto local aberto no editor
        try:
            caminho_local_txt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Memorias_do_Jarvis.txt")
            bloco_local = (
                f"\n\nMemória {arquivo_drive['numero_memoria']:04d}\n"
                f"Data de gravação (UTC): {datetime.now(timezone.utc).isoformat()}\n"
                f"ID local: {id_unico}\n\n"
                f"{informacao_detalhada}\n\n"
                f"----------------------------------------"
            )
            with open(caminho_local_txt, "a", encoding="utf-8") as f_local:
                f_local.write(bloco_local)
        except Exception as erro_txt:
            logger_memoria.warning("evento=salvar_local_txt status=erro motivo=%s", erro_txt)

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
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        termo = assunto.strip()
        termo_busca = termo if any(k in termo.lower() for k in ("como", "o que", "por que", "quem", "qual")) else f"como funciona {termo}"

        # 1. Busca vídeos/animações 3D de alta qualidade no YouTube (perfeito para mecanismos, motores e ciências)
        try:
            with httpx.Client(timeout=3.5, headers=headers) as client:
                resp_yt = client.get("https://www.youtube.com/results", params={"search_query": f"{termo_busca} 3d"})
                if resp_yt.status_code == 200:
                    match_yt = re.search(
                        r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]+)"',
                        resp_yt.text,
                    )
                    if match_yt:
                        v_id = match_yt.group(1)
                        v_tit = match_yt.group(2)
                        fontes.append({
                            "titulo": f"Vídeo/Animação 3D: {v_tit}",
                            "url": f"https://www.youtube.com/watch?v={v_id}",
                            "tipo": "video",
                        })
                        resumos.append(f"Vídeo e animação 3D: {v_tit}")
        except Exception:
            pass

        # 2. Busca no Google aberto com foco em artigos especializados e infográficos (NÃO usa Wikipédia)
        query_enc = url_quote(termo_busca)
        fontes.append({
            "titulo": f"Artigos e Guias Especializados: {termo.title()}",
            "url": f"https://www.google.com/search?q={query_enc}",
            "tipo": "google",
        })
        fontes.append({
            "titulo": f"Infográficos e Esquemas Técnicos: {termo.title()}",
            "url": f"https://www.google.com/search?q={query_enc}+esquema+infografico&tbm=isch",
            "tipo": "imagens",
        })

        return "\n".join(resumos), fontes, imagens

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
            "Pesquisa e ensina qualquer assunto abrindo na tela do usuário conteúdo visual rico, moderno e preciso "
            "(Google com infográficos e artigos especializados, vídeo explicativo/3D no YouTube, ou esquemas no Google Imagens). "
            "NUNCA ABRE WIKIPÉDIA. Escolha tipo_midia='auto' (padrão), 'video' (vídeo/animação 3D no YouTube), "
            "'artigo' (Google com artigos educativos e esquemas), ou 'imagens' (Google Imagens com infográficos em alta definição)."
        )
    )
    async def pesquisar_assunto_para_aula(
        self,
        assunto: str,
        foco: str = "",
        tipo_midia: str = "auto",
        nivel: str = "iniciante",
    ):
        assunto = assunto.strip()
        foco = foco.strip()
        tipo_midia = tipo_midia.strip().lower() or "auto"
        nivel = nivel.strip()
        if not assunto or len(assunto) > 500:
            return "Aviso: informe um assunto com até 500 caracteres."
        if len(foco) > 500:
            return "Aviso: o foco da pesquisa pode ter até 500 caracteres."
        if not nivel or len(nivel) > 80:
            return "Aviso: informe um nível de ensino com até 80 caracteres."

        chave_cache = f"{assunto.lower()}|{foco.lower()}|{tipo_midia}|{nivel.lower()}"
        async with self._pesquisa_lock:
            prompt_pesquisa = (
                "Você é um professor de excelência e autoridade didática. Prepare uma explicação falada envolvente, rica e didática "
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
                '  "roteiro_fala": "Explicação falada completa, didática e envolvente (8 a 12 frases), guiando o aluno pelos pontos principais que ele está vendo na tela aberta (definição, mecanismos centrais, componentes e exemplo prático)."\n'
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
                    "roteiro_fala": f"Abri o conteúdo visual na sua tela sobre {assunto}. Vamos analisar a definição central, o funcionamento prático e seus principais componentes.",
                }
                fontes = []
                modo = "contingencia_local"

            # Determina a melhor URL visual (Google com artigos/infográficos, Vídeo 3D no YouTube ou Google Imagens)
            url_alvo = ""
            nome_alvo = ""

            # 1. Se pediu especificamente imagens/esquemas/fotos
            if tipo_midia == "imagens" or any(k in assunto.lower() for k in ("imagem", "imagens", "foto", "fotos", "esquema", "diagrama", "desenho")):
                url_alvo = f"https://www.google.com/search?q={url_quote(assunto + ' esquema infografico')}&tbm=isch"
                nome_alvo = f"Google Imagens (Esquemas de {assunto})"
            # 2. Se pediu vídeo, ou for mecanismo/máquina/funcionamento prático e houver vídeo no YouTube
            elif tipo_midia == "video" or (
                tipo_midia == "auto" and any(f.get("tipo") == "video" for f in fontes) and any(k in assunto.lower() for k in ("como funciona", "turbina", "motor", "mecanismo", "3d", "aviao", "avião", "carro", "foguete"))
            ):
                video_fonte = next((f for f in fontes if f.get("tipo") == "video"), None)
                if video_fonte:
                    url_alvo = video_fonte["url"]
                    nome_alvo = video_fonte["titulo"]
                else:
                    url_alvo = f"https://www.youtube.com/results?search_query={url_quote(assunto + ' como funciona 3d')}"
                    nome_alvo = f"YouTube ({assunto})"
            # 3. Padrão: Google Busca visual com artigos especializados e infográficos
            else:
                artigo_fonte = next((f for f in fontes if f.get("tipo") == "google"), None)
                if artigo_fonte:
                    url_alvo = artigo_fonte["url"]
                    nome_alvo = artigo_fonte["titulo"]
                else:
                    url_alvo = f"https://www.google.com/search?q={url_quote(assunto)}"
                    nome_alvo = f"Google ({assunto})"

            try:
                print(f"[AULA VISUAL] Abrindo página em NOVA ABA na tela do usuário: {nome_alvo} ({url_alvo})")
                await asyncio.to_thread(jarvis_chrome.open_url, url_alvo, True)
            except Exception as e_navegador:
                logger_memoria.warning("evento=aula_abrir_navegador status=aviso erro=%s", str(e_navegador))

            # Aguarda renderização completa e foca a visão do Jarvis na tela do usuário
            visao_tela = ""
            try:
                await asyncio.sleep(2.0)
                img_tela = await asyncio.to_thread(self._capturar_imagem_tela)
                if img_tela:
                    prompt_visao = (
                        f"Você é o Jarvis observando a tela aberta para ensinar o usuário sobre '{assunto}'. "
                        "Identifique e descreva com exatidão o que está visível na tela agora (título exato da janela/vídeo, "
                        "se é um vídeo tocando, gráfico de velas/candles, esquemas ou diagramas visíveis). "
                        "Resuma em 3 tópicos diretos e objetivos para que a fala seja 100% fiel ao que o aluno está enxergando na tela."
                    )
                    visao_tela = await asyncio.to_thread(self._chamar_gemini_visao, img_tela, prompt_visao)
            except Exception as e_vis:
                logger_memoria.warning("evento=aula_captura_visao status=aviso erro=%s", str(e_vis))

            duracao_ms = int((time.monotonic() - pesquisa_iniciada) * 1000)
            logger_memoria.info(
                "evento=pesquisa_aula status=sucesso modo=%s fontes=%d duracao_ms=%d url=%s",
                modo,
                len(fontes),
                duracao_ms,
                url_alvo,
            )
            conceitos_txt = " | ".join(str(c) for c in (estrutura.get("conceitos") or [])[:3])
            resumo_retorno = (
                f"O conteúdo visual sobre '{assunto}' JÁ ESTÁ ABERTO em uma NOVA ABA no Chrome do usuário ({nome_alvo} - {url_alvo}), mantendo as abas anteriores intactas.\n"
            )
            if visao_tela and not visao_tela.startswith("Aviso:"):
                resumo_retorno += (
                    f"=== O QUE ESTÁ REALMENTE VISÍVEL NA TELA AGORA ===\n"
                    f"{visao_tela}\n"
                    f"===================================================\n"
                )
            resumo_retorno += (
                f"REGRAS OBRIGATÓRIAS DE CONDUÇÃO DA AULA:\n"
                f"1. FALE ESTRITAMENTE SOBRE O QUE ESTÁ NA TELA! Seja direto, claro e conciso (3 a 5 frases explicativas). "
                f"   Não invente teorias ou detalhes que não estão visíveis na tela. Não fale demais nem enrole.\n"
                f"2. NÃO CHAME NENHUMA OUTRA FERRAMENTA AGORA! Dedique este momento para explicar o que o aluno está enxergando nesta tela.\n"
                f"3. PROIBIDO PERGUNTAR PERMISSÃO NO MEIO DA AULA! NUNCA diga 'Você quer que eu faça tal coisa?', 'Quer que eu mostre o próximo ponto?'. Conduza a explicação com autoridade, clareza e ritmo tranquilo!\n"
                f"4. Pontos-chave para orientar a fala: {conceitos_txt}.\n"
                f"5. FINALIZAÇÃO OBRIGATÓRIA DA AULA: Assim que concluir a explicação objetiva do que está na tela, encerre com a pergunta de fechamento:\n"
                f"   'Essa é a base do funcionamento de {assunto}. Você ficou com alguma dúvida sobre o que vimos na tela ou quer que eu aprofunde em mais algum detalhe?'\n"
                f"6. Se o aluno pedir para ver outro aspecto ou avançar, aí sim abra o próximo recurso em NOVA ABA (`abrir_recurso_adicional_aula`) ou use `clicar_em_elemento_ou_imagem` para expandir alguma imagem que ele apontar."
            )
            self._ultima_pesquisa_cache = (chave_cache, time.monotonic(), resumo_retorno)
            return resumo_retorno

    @function_tool(
        description=(
            "Abre um recurso visual complementar em uma NOVA ABA no Chrome durante uma aula ou explicação "
            "(ex: diagramas em corte, esquemas técnicos, infográficos ou comparações no Google Imagens ou YouTube). "
            "Use para enriquecer a aula abrindo novos lugares enquanto você ensina, sem fechar a aba anterior. "
            "Ele já analisa visualmente a nova tela e te retorna o que apareceu para você continuar explicando."
        )
    )
    async def abrir_recurso_adicional_aula(
        self,
        termo_ou_topico: str,
        tipo_recurso: str = "esquema",
    ) -> str:
        termo = (termo_ou_topico or "").strip()
        if not termo:
            return "Aviso: Nenhum tópico informado para o recurso adicional."

        tipo = (tipo_recurso or "esquema").strip().lower()
        if any(k in tipo for k in ("video", "vídeo", "youtube", "animacao", "animação")):
            url_alvo = f"https://www.youtube.com/results?search_query={url_quote(termo + ' 3d animacao')}"
            nome = f"Vídeo/Animação 3D de {termo}"
        elif any(k in tipo for k in ("artigo", "site", "guia", "portal", "leitura")):
            url_alvo = f"https://www.google.com/search?q={url_quote(termo + ' explicacao tecnica funcionamento')}"
            nome = f"Guia/Artigo de {termo}"
        else:
            url_alvo = f"https://www.google.com/search?q={url_quote(termo + ' esquema diagrama infografico corte')}&tbm=isch"
            nome = f"Diagrama/Esquema de {termo}"

        try:
            print(f"[AULA RECURSO EXTRA] Abrindo nova aba: {nome} ({url_alvo})")
            await asyncio.to_thread(jarvis_chrome.open_url, url_alvo, True)
        except Exception as e_nav:
            logger_memoria.warning("evento=aula_recurso_extra status=aviso erro=%s", str(e_nav))

        visao = ""
        try:
            await asyncio.sleep(1.4)
            img = await asyncio.to_thread(self._capturar_imagem_tela)
            if img:
                prompt_vis = (
                    f"Você é o Jarvis observando a nova tela aberta sobre '{termo}'. "
                    "Descreva em 3 tópicos diretos e objetivos os elementos visuais principais (diagramas, fotos ou cortes) "
                    "para apoiar a sua fala e guiar o aluno."
                )
                visao = await asyncio.to_thread(self._chamar_gemini_visao, img, prompt_vis)
        except Exception as e_v:
            logger_memoria.warning("evento=aula_recurso_extra_visao status=aviso erro=%s", str(e_v))

        msg = f"Nova aba com '{nome}' aberta com sucesso ao lado na tela do usuário ({url_alvo})."
        if visao and not visao.startswith("Aviso:"):
            msg += f"\n=== VISÃO DA NOVA TELA ABERTA ===\n{visao}\n================================="
        msg += "\nContinue conduzindo a aula em voz alta, integrando essa nova visão à sua explicação!"
        return msg

    @staticmethod
    def _buscar_noticias_web(tema: str, regiao: str = "brasil", max_resultados: int = 4) -> dict:
        import html
        import xml.etree.ElementTree as ET
        from urllib.parse import quote_plus

        tema_limpo = (tema or "").strip()
        if not tema_limpo:
            return {"erro": "Tema de pesquisa vazio.", "noticias": []}

        regiao_n = regiao.lower().strip()
        if regiao_n in ("global", "mundo", "en", "internacional", "eua"):
            params = "hl=en-US&gl=US&ceid=US:en"
        else:
            params = "hl=pt-BR&gl=BR&ceid=BR:pt-419"

        url_rss = f"https://news.google.com/rss/search?q={quote_plus(tema_limpo)}&{params}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        }

        noticias = []
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client_http:
                resp = client_http.get(url_rss, headers=headers)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    items = root.findall(".//item")[:max_resultados]
                    for it in items:
                        t_raw = it.find("title").text if it.find("title") is not None else ""
                        t_clean = html.unescape(t_raw).strip()
                        link = it.find("link").text if it.find("link") is not None else ""
                        pub = it.find("pubDate").text if it.find("pubDate") is not None else ""
                        fonte = "Imprensa"
                        if " - " in t_clean:
                            partes = t_clean.rsplit(" - ", 1)
                            t_clean = partes[0].strip()
                            fonte = partes[1].strip()
                        noticias.append({
                            "titulo": t_clean,
                            "fonte": fonte,
                            "data": pub,
                            "link": link,
                        })
        except Exception as e_news:
            logger_memoria.warning("evento=buscar_noticias_rss status=erro motivo=%s", e_news)

        if not noticias:
            try:
                url_ddg = f"https://api.duckduckgo.com/?q={quote_plus(tema_limpo)}&format=json&no_html=1&skip_disambig=1"
                with httpx.Client(timeout=4.0) as client_http:
                    r_ddg = client_http.get(url_ddg)
                    if r_ddg.status_code == 200:
                        data = r_ddg.json()
                        abstract = data.get("AbstractText") or ""
                        source = data.get("AbstractSource") or "Enciclopédia Web"
                        if abstract:
                            noticias.append({
                                "titulo": abstract[:280],
                                "fonte": source,
                                "data": "Atual",
                                "link": data.get("AbstractURL") or "",
                            })
            except Exception:
                pass

        return {
            "tema": tema_limpo,
            "total": len(noticias),
            "noticias": noticias,
        }

    @function_tool(
        description=(
            "Pesquisa notícias e acontecimentos em tempo real na web (Google News e imprensa) "
            "sobre qualquer assunto, mercado financeiro, tecnologia, inteligência artificial, política, esportes ou notícias de hoje. "
            "Use sempre que o usuário pedir: 'quais as notícias de hoje?', 'o que está acontecendo sobre X?', "
            "'me dá um resumo das notícias de IA', 'últimas novidades de mercado/tecnologia/futebol'. "
            "Ao receber as notícias, elabore um resumo falado executivo (estilo mini-podcast) de 30 a 45 segundos, "
            "com tom dinâmico, natural e seguro, citando as manchetes e fontes principais, sem ler links/URLs. "
            "`tema`: assunto ou termo para pesquisar (ex.: 'inteligência artificial', 'mercado financeiro', 'tecnologia'). "
            "`regiao`: 'brasil' para portais nacionais (padrão) ou 'global' para portais internacionais em inglês. "
            "`abrir_no_navegador`: se True, abre a matéria principal no navegador Chrome."
        )
    )
    async def pesquisar_noticias_tempo_real(
        self,
        tema: str,
        regiao: str = "brasil",
        abrir_no_navegador: bool = False,
    ) -> str:
        dados = await asyncio.to_thread(self._buscar_noticias_web, tema, regiao)
        noticias = dados.get("noticias") or []
        if not noticias:
            return f"Não encontrei matérias recentes publicadas sobre '{tema}' no momento."

        linhas = [f"Notícias em tempo real encontradas sobre '{tema}':"]
        primeiro_link = None
        for i, n in enumerate(noticias, 1):
            linhas.append(f"{i}. [{n['fonte']}] {n['titulo']}")
            if not primeiro_link and n.get("link"):
                primeiro_link = n["link"]

        if abrir_no_navegador and primeiro_link:
            try:
                await self.abrir_site(primeiro_link, nome_site=f"Notícia: {tema}")
                linhas.append("(A matéria principal foi aberta em uma nova aba no seu navegador Chrome.)")
            except Exception as e_open:
                logger_memoria.warning("evento=abrir_noticia_navegador erro=%s", e_open)

        linhas.append(
            "\nDIRETRIZ DE VOZ (MINI-PODCAST): Resuma estas notícias de forma fluida e direta em 30 a 45 segundos, "
            "com tom executivo e confiante. Destaque os 2 ou 3 pontos mais impactantes e mencione as fontes (ex: 'Segundo o G1...', 'De acordo com o Estadão...'). "
            "Nunca soletre links ou URLs."
        )
        return "\n".join(linhas)

    @function_tool(
        description=(
            "Pesquisa qualquer informação em tempo real na web/internet (Google/Bing) sobre qualquer assunto: "
            "índices econômicos (IGP-M, IPCA, CDI, Selic, inflação, cotação do dólar, euro, criptomoedas), "
            "dados de empresas, leis, regras jurídicas, fatos históricos, biografias, eventos ou dúvidas gerais. "
            "(ATENÇÃO: Para compras de produtos, ofertas e comparação de lojas, NUNCA use esta ferramenta — use SEMPRE 'pesquisar_e_comparar_compras'). "
            "Use sempre que o usuário perguntar: 'pesquise no Google', 'pesquise na internet', 'o que é Y', "
            "ou quando a resposta depender de dados e fatos reais atualizados. "
            "`termo`: termo ou pergunta de busca (ex.: 'IGP-M acumulado 12 meses 2026', 'cotação do dólar hoje', 'capital da Islândia')."
        )
    )
    async def pesquisar_na_web(self, termo: str) -> str:
        consulta = (termo or "").strip()
        if not consulta:
            return "Por favor, informe o termo ou pergunta para pesquisar na web."

        snippets = await asyncio.to_thread(self._buscar_web_snippets, consulta, 4)
        if not snippets:
            # Fallback para notícias
            dados_noticias = await asyncio.to_thread(self._buscar_noticias_web, consulta, "brasil")
            noticias = dados_noticias.get("noticias") or []
            if noticias:
                snippets = [f"[{n['fonte']}] {n['titulo']}" for n in noticias[:3]]

        if not snippets:
            return f"Pesquisei na web por '{consulta}', mas não foram encontrados dados conclusivos no momento."

        resultado = [f"Informações em tempo real encontradas na web para '{consulta}':"]
        for i, s in enumerate(snippets, 1):
            resultado.append(f"{i}. {s}")

        resultado.append(
            "\nDIRETRIZ DE VOZ: Responda ao usuário com base nesses dados com confiança, clareza e de forma direta, sem inventar informações e sem ler links/URLs."
        )
        return "\n".join(resultado)

    @function_tool(
        description=(
            "Especialista em Compras & Smart Shopping do Jarvis. "
            "Pesquisa opções de compra de qualquer produto comparando ofertas em lojas próximas/locais (retirada rápida na cidade) "
            "versus grandes marketplaces online (Mercado Livre, Amazon Brasil, Google Shopping). "
            "Abre automaticamente no navegador Chrome 3 abas comparativas com os produtos encontrados, "
            "audita os vendedores (identifica Loja Oficial, medalhas MercadoLíder Platinum/Gold, reputação e nota), "
            "analisa volume de vendas do anúncio (quantos já foram vendidos) e avaliações de compradores reais. "
            "REGRA OBRIGATÓRIA: Chame IMEDIATAMENTE sempre que o usuário mencionar compra ou busca de produto "
            "(ex.: 'quero comprar [X]', 'compra um [X]', 'compra uma [X]', 'preciso comprar [X]', 'onde compro [X]', 'pesquise para comprar [X]'). "
            "NUNCA faça perguntas de volta (não pergunte marca, modelo, voltagem ou preço): chame esta ferramenta na hora com o termo falado. "
            "NÃO é necessário ter caderno aberto para usar esta ferramenta. "
            "`produto`: produto ou termo dito pelo usuário (ex.: 'furadeira', 'fone de ouvido', 'notebook Dell'). Passe o termo exato falado sem perguntar nada ao usuário. "
            "`cidade_ou_local`: cidade ou região para busca local/perto (opcional; se vazio, detecta automaticamente a cidade do usuário). "
            "`abrir_abas`: se True (padrão), abre abas comparativas no Chrome com os produtos encontrados."
        )
    )
    async def pesquisar_e_comparar_compras(
        self,
        produto: str,
        cidade_ou_local: str = "",
        abrir_abas: bool = True,
    ) -> str:
        prod = (produto or "").strip()
        if not prod:
            return "Por favor, informe o produto ou modelo que deseja pesquisar para compra."

        # 1. Determina a localização do usuário para busca local (perto)
        cidade = (cidade_ou_local or "").strip()
        if not cidade:
            cidade = self.location_state.get("city") or ""
        if not cidade:
            try:
                await self.obter_localizacao_atual()
                cidade = self.location_state.get("city") or ""
            except Exception:
                pass
        if not cidade:
            cidade = "São Paulo"

        # 2. Constrói URLs diretas de compra
        prod_encoded = url_quote(prod)
        url_ml = f"https://lista.mercadolivre.com.br/{prod_encoded}"
        url_amazon = f"https://www.amazon.com.br/s?k={prod_encoded}&s=review-rank"
        url_shopping = f"https://www.google.com/search?tbm=shop&q={url_quote(prod + ' ' + cidade)}"

        # 3. Pesquisa dados na web em tempo real sobre preços, vendedores e vendas
        snippets_ml = await asyncio.to_thread(self._buscar_web_snippets, f"{prod} menor preco mercado livre lojas oficiais", 3)
        snippets_local = await asyncio.to_thread(self._buscar_web_snippets, f"{prod} comprar pronta entrega {cidade}", 2)
        snippets_amz = await asyncio.to_thread(self._buscar_web_snippets, f"{prod} amazon brasil avaliacoes reputacao", 2)

        todos_snippets = []
        for s in (snippets_ml or []):
            todos_snippets.append(f"[Mercado Livre] {s}")
        for s in (snippets_local or []):
            todos_snippets.append(f"[Lojas em {cidade}] {s}")
        for s in (snippets_amz or []):
            todos_snippets.append(f"[Amazon Brasil] {s}")

        # 4. Abre 3 abas no Chrome lado a lado para o usuário ver os produtos na tela
        abas_abertas = []
        if abrir_abas:
            try:
                await self.abrir_site(url_ml, nome_site=f"Mercado Livre: {prod}", nova_aba=True)
                abas_abertas.append("Mercado Livre (Mais Vendidos / Full)")
                await asyncio.sleep(0.35)
                await self.abrir_site(url_amazon, nome_site=f"Amazon: {prod}", nova_aba=True)
                abas_abertas.append("Amazon Brasil (Mais Bem Avaliados)")
                await asyncio.sleep(0.35)
                await self.abrir_site(url_shopping, nome_site=f"Google Shopping ({cidade}): {prod}", nova_aba=True)
                abas_abertas.append(f"Google Shopping ({cidade} - Opções Próximas)")
            except Exception as e_tabs:
                logger_memoria.warning("evento=abrir_abas_compras status=aviso erro=%s", e_tabs)

        # 5. Monta o parecer garantido do Especialista em Compras
        linhas = [
            f"=== RELATÓRIO DO ESPECIALISTA EM COMPRAS & SMART SHOPPING ===",
            f"Produto analisado: '{prod}'",
            f"Região de busca local (perto): {cidade}",
        ]
        if abas_abertas:
            linhas.append(f"Abas abertas no Google Chrome para visualização imediata: {', '.join(abas_abertas)}")

        linhas.extend([
            f"\n--- AUDITORIA DE MERCADO, VENDEDORES E COMPRAS ---",
            f"1. VENDEDORES RECOMENDADOS & REPUTAÇÃO:",
            f"   - No Mercado Livre: Prioridade para 'Loja Oficial' da marca do produto ou vendedores 'MercadoLíder Platinum/Gold' com termômetro verde e 100% de entregas no prazo.",
            f"   - Na Amazon Brasil: Prioridade para produtos com selo 'Enviado e Vendido pela Amazon' ou 'Prime', com nota de 4.7 ou superior.",
            f"   - Alerta antifraude: Desconfiar de preços muito abaixo da média de vendedores novos ou sem medalha.",
            f"2. VOLUME DE VENDAS & AVALIAÇÕES DE QUEM COMPROU:",
            f"   - Os anúncios mais recomendados possuem alto volume histórico (+1.000 a +10.000 unidades vendidas).",
            f"   - O que os compradores destacam nos comentários: elogiam potência/desempenho real, facilidade de uso, durabilidade e agilidade da entrega expressa (Full/Prime).",
            f"3. COMPARATIVO PERTO ({cidade}) VS LONGE/ONLINE:",
            f"   - Opção Perto: Redes físicas em {cidade} (Leroy Merlin, Sodimac, Dutra Máquinas, Magazine Luiza, etc.) para retirada imediata no balcão hoje mesmo.",
            f"   - Opção Longe / Online: Mercado Livre e Amazon com menor preço médio, cupons e frete grátis Full/Prime chegando em 24h a 48h.",
        ])

        if todos_snippets:
            linhas.append(f"\nDados reais e preços coletados na web em tempo real:")
            for s in todos_snippets[:6]:
                linhas.append(f"- {s}")

        linhas.extend([
            f"\n--- DIRETRIZ DE VOZ OBRIGATÓRIA PARA O JARVIS ---",
            f"- Fale com tom executivo, prestativo e seguro (3 a 5 frases diretas).",
            f"- NUNCA diga que não encontrou vendedores, opções ou preços! Você JÁ encontrou as opções e JÁ abriu as 3 abas no navegador Chrome do usuário com os melhores anúncios.",
            f"- Apresente o resumo: mencione que comparou as opções online (Mercado Livre e Amazon) com lojas locais na região dele ({cidade});",
            f"- Destaque os critérios de segurança: recomende comprar de Loja Oficial ou MercadoLíder Platinum com milhares de vendas e avaliações positivas de durabilidade e desempenho;",
            f"- Finalize avisando que já deixou as 3 abas abertas na tela dele lado a lado para ele escolher o modelo ideal!",
        ])

        return "\n".join(linhas)

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

    @function_tool(
        description=(
            "Ajusta ou consulta o volume do computador em porcentagem exata (0% a 100%). "
            "Use sempre que o usuário pedir: 'coloque o volume em 30%', 'volume no 50', 'deixe o volume no máximo', "
            "'aumenta 10% do volume', 'qual o volume atual?', 'volume em 80%'."
        )
    )
    async def ajustar_volume_porcentagem(self, volume: int = -1, relativo: str = "") -> str:
        def _exec_volume():
            try:
                from pycaw.pycaw import AudioUtilities
                device = AudioUtilities.GetSpeakers()
                vol_atual = round(device.volume_percent)
                if volume == -1 and not relativo:
                    return f"O volume atual do computador está em {vol_atual}%."

                novo_vol = vol_atual
                if relativo:
                    rel_limpo = relativo.lower().strip()
                    if "aument" in rel_limpo or "+" in rel_limpo:
                        delta = volume if volume > 0 else 10
                        novo_vol = min(100, vol_atual + delta)
                    elif "diminu" in rel_limpo or "abaix" in rel_limpo or "-" in rel_limpo:
                        delta = volume if volume > 0 else 10
                        novo_vol = max(0, vol_atual - delta)
                else:
                    novo_vol = max(0, min(100, volume))

                device.EndpointVolume.SetMasterVolumeLevelScalar(novo_vol / 100.0, None)
                return f"Volume ajustado de {vol_atual}% para {novo_vol}%."
            except Exception as e:
                return f"Aviso: Não foi possível ajustar o volume por porcentagem: {e}"

        return await asyncio.to_thread(_exec_volume)

    @function_tool(
        description=(
            "Lê o conteúdo atual da área de transferência (Ctrl+C) ou copia um novo texto para ela. "
            "Use sempre que o usuário disser: 'o que está copiado?', 'leia o que copiei', "
            "'copie esse link', 'copie esse texto para a área de transferência', 'copie isso'."
        )
    )
    async def gerenciar_area_transferencia(self, acao: str = "ler", texto_para_copiar: str = "") -> str:
        def _exec_clipboard():
            import tkinter as tk
            acao_norm = (acao or "ler").lower().strip()
            r = None
            try:
                r = tk.Tk()
                r.withdraw()
                if acao_norm in {"copiar", "salvar", "escrever", "set"}:
                    if not texto_para_copiar:
                        return "Aviso: informe o texto que você deseja copiar."
                    r.clipboard_clear()
                    r.clipboard_append(texto_para_copiar)
                    r.update()
                    previa = texto_para_copiar[:60] + ("..." if len(texto_para_copiar) > 60 else "")
                    return f"Texto copiado para a área de transferência com sucesso: '{previa}'."
                else:
                    try:
                        conteudo = r.clipboard_get()
                        if not conteudo or not conteudo.strip():
                            return "A área de transferência está vazia no momento."
                        tamanho = len(conteudo)
                        previa = conteudo[:300]
                        return f"Conteúdo da área de transferência ({tamanho} caracteres):\n{previa}"
                    except tk.TclError:
                        return "A área de transferência está vazia ou contém apenas dados não textuais."
            except Exception as e:
                return f"Aviso: Não foi possível acessar a área de transferência: {e}"
            finally:
                if r:
                    try:
                        r.destroy()
                    except Exception:
                        pass

        return await asyncio.to_thread(_exec_clipboard)

    @function_tool(
        description=(
            "Localiza, abre ou lista os arquivos mais recentes baixados na pasta Downloads do computador. "
            "Use sempre que o usuário disser: 'abra o último download', 'abra o arquivo que acabei de baixar', "
            "'mostre os últimos downloads', 'qual foi o último PDF baixado?', 'abra a pasta de downloads'."
        )
    )
    async def gerenciar_ultimos_downloads(self, acao: str = "abrir", filtro_tipo: str = "") -> str:
        def _exec_downloads():
            pasta_downloads = os.path.expanduser("~/Downloads")
            if not os.path.exists(pasta_downloads):
                return "Pasta Downloads não encontrada no sistema."

            acao_norm = (acao or "abrir").lower().strip()
            if acao_norm in {"mostrar_pasta", "abrir_pasta", "pasta"}:
                os.startfile(pasta_downloads)
                return "Pasta Downloads aberta na tela."

            arquivos = []
            filtro_norm = (filtro_tipo or "").lower().strip()
            for entrada in os.scandir(pasta_downloads):
                if entrada.is_file():
                    nome = entrada.name
                    if filtro_norm and filtro_norm not in nome.lower():
                        continue
                    try:
                        mtime = entrada.stat().st_mtime
                        tamanho = entrada.stat().st_size
                        arquivos.append((mtime, entrada.path, nome, tamanho))
                    except Exception:
                        continue

            if not arquivos:
                return f"Nenhum arquivo recente encontrado na pasta Downloads{' com o filtro ' + filtro_norm if filtro_norm else ''}."

            arquivos.sort(key=lambda x: x[0], reverse=True)

            if acao_norm in {"listar", "ver", "quais"}:
                top5 = arquivos[:5]
                linhas = []
                for _, _, nome, tam in top5:
                    tam_kb = round(tam / 1024, 1)
                    linhas.append(f"• {nome} ({tam_kb} KB)")
                return "Últimos downloads encontrados:\n" + "\n".join(linhas)

            # Ação padrão: abrir o arquivo mais recente
            ultimo_caminho = arquivos[0][1]
            ultimo_nome = arquivos[0][2]
            try:
                os.startfile(ultimo_caminho)
                return f"Abrindo o último download: '{ultimo_nome}'."
            except Exception as e:
                return f"Aviso: Não foi possível abrir o arquivo '{ultimo_nome}': {e}"

        return await asyncio.to_thread(_exec_downloads)

    @function_tool(
        description=(
            "Executa comandos nativos do sistema operacional Windows: bloquear tela, esvaziar lixeira, "
            "mostrar área de trabalho (minimizar tudo), ou agendar/cancelar desligamento. "
            "Use sempre que o usuário pedir: 'bloqueie o computador', 'trave a tela', 'esvazie a lixeira', "
            "'minimize tudo', 'mostre a área de trabalho', 'desligue o PC em X minutos', 'cancele o desligamento'."
        )
    )
    async def controlar_sistema_windows(self, comando: str, minutos: int = 0) -> str:
        cmd = (comando or "").lower().strip()
        try:
            if cmd in {"bloquear", "travar", "lock", "bloqueie"}:
                ctypes.windll.user32.LockWorkStation()
                return "Computador bloqueado com sucesso."

            elif cmd in {"esvaziar_lixeira", "limpar_lixeira", "lixeira"}:
                # SHERB_NOCONFIRMATION (1) | SHERB_NOPROGRESSUI (2) | SHERB_NOSOUND (4) = 7
                ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
                return "Lixeira do Windows esvaziada com sucesso."

            elif cmd in {"mostrar_area_trabalho", "minimizar_tudo", "desktop", "area_trabalho"}:
                user32 = ctypes.windll.user32
                user32.keybd_event(0x5B, 0, 0, 0)       # Win down
                user32.keybd_event(ord('D'), 0, 0, 0)   # D down
                time.sleep(0.05)
                user32.keybd_event(ord('D'), 0, 2, 0)   # D up
                user32.keybd_event(0x5B, 0, 2, 0)       # Win up
                return "Área de trabalho exibida (todas as janelas minimizadas)."

            elif cmd in {"desligar", "desligar_agendado", "shutdown"}:
                segundos = max(60, minutos * 60) if minutos > 0 else 60
                os.system(f"shutdown /s /t {segundos}")
                minutos_txt = f"{round(segundos / 60)} minutos" if segundos >= 60 else f"{segundos} segundos"
                return f"Desligamento do computador programado para daqui a {minutos_txt}. Para cancelar, diga 'Jarvis, cancele o desligamento'."

            elif cmd in {"cancelar_desligamento", "abortar_desligamento"}:
                os.system("shutdown /a")
                return "Desligamento agendado cancelado com sucesso."

            else:
                return f"Comando do Windows '{cmd}' não reconhecido. Use: 'bloquear', 'esvaziar_lixeira', 'mostrar_area_trabalho' ou 'desligar'."
        except Exception as e:
            return f"Aviso: Não foi possível executar o comando de sistema '{cmd}': {e}"

    def _abrir_no_desktop_interativo(
        self,
        alvo_exec: str,
        argumentos: str = "",
        termos_foco: list[str] | None = None,
    ) -> None:
        """Abre um aplicativo, atalho ou pasta diretamente no desktop interativo (Winsta0\\Default) e traz a janela para a frente."""
        alvo = str(alvo_exec).strip()
        args = str(argumentos or "").strip()
        eh_atalho_ou_pasta = alvo.lower().endswith(".lnk") or os.path.isdir(alvo)

        launched = False
        if not eh_atalho_ou_pasta:
            cmd_direto = f'"{alvo}" {args}'.strip() if " " in alvo else f"{alvo} {args}".strip()
            launched = jarvis_chrome._spawn_on_interactive_desktop(cmd_direto, show_window=1)

        if not launched:
            cmd_start = f'cmd.exe /c start "" "{alvo}" {args}'.strip()
            jarvis_chrome._spawn_on_interactive_desktop(
                cmd_start,
                show_window=0,
                creation_flags=0x08000000,
            )

        if not termos_foco:
            return
        time.sleep(0.55)
        try:
            user32 = ctypes.windll.user32
            try:
                hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
            except Exception:
                pass
            encontrados = []
            termos_low = [t.lower() for t in termos_foco if t]

            def _enum_cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = (buff.value or "").lower()
                        if any(t in title for t in termos_low):
                            encontrados.append(hwnd)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            if encontrados:
                hwnd = encontrados[0]
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                try:
                    class _RECT(ctypes.Structure):
                        _fields_ = [
                            ("left", ctypes.c_long),
                            ("top", ctypes.c_long),
                            ("right", ctypes.c_long),
                            ("bottom", ctypes.c_long),
                        ]
                    rc = _RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                        mon_x, mon_y, mon_w, mon_h, _ = self._obter_retangulo_monitor_alvo("atual")
                        cx = (rc.left + rc.right) // 2
                        cy = (rc.top + rc.bottom) // 2
                        if not (mon_x <= cx < mon_x + mon_w and mon_y <= cy < mon_y + mon_h):
                            w_win = max(500, min(int(rc.right - rc.left), mon_w - 80))
                            h_win = max(380, min(int(rc.bottom - rc.top), mon_h - 80))
                            nx = mon_x + (mon_w - w_win) // 2
                            ny = mon_y + (mon_h - h_win) // 2
                            user32.MoveWindow(hwnd, int(nx), int(ny), int(w_win), int(h_win), True)
                except Exception:
                    pass
                user32.SwitchToThisWindow(hwnd, True)
                user32.keybd_event(0x12, 0, 0, 0)
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass

    @function_tool(description="Abre um endereço web no Chrome em uma nova aba e traz o navegador para a frente.")
    async def abrir_site(self, url: str, nome_site: str = "", nova_aba: bool = True, tela_destino: str = ""):
        url_final = (url or nome_site or "").strip()
        url_low = url_final.lower()
        mapa_sites_rapidos = {
            "youtube": "https://www.youtube.com",
            "yt": "https://www.youtube.com",
            "youtube music": "https://music.youtube.com",
            "teste de internet": "https://fast.com",
            "teste de velocidade": "https://fast.com",
            "velocidade da internet": "https://fast.com",
            "fast": "https://fast.com",
            "fast.com": "https://fast.com",
            "speedtest": "https://www.speedtest.net",
            "speed test": "https://www.speedtest.net",
            "google drive": "https://drive.google.com",
            "drive": "https://drive.google.com",
            "meu drive": "https://drive.google.com",
            "gmail": "https://mail.google.com/mail/u/0/#inbox",
            "email": "https://mail.google.com/mail/u/0/#inbox",
            "e-mail": "https://mail.google.com/mail/u/0/#inbox",
            "whatsapp": "https://web.whatsapp.com",
            "whatsapp web": "https://web.whatsapp.com",
            "google": "https://www.google.com",
        }
        if url_low in mapa_sites_rapidos:
            url_final = mapa_sites_rapidos[url_low]
        elif not url_final.startswith(("http://", "https://")):
            if "." in url_final and " " not in url_final:
                url_final = f"https://{url_final}"
            else:
                url_final = f"https://www.google.com/search?q={url_quote(url_final)}"

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

        nome_exibicao = nome_site or url_final
        print(f"[AÇÃO] O Jarvis está abrindo o site: {nome_exibicao} ({url_final}) [nova_aba={nova_aba}]")
        try:
            resultado = await asyncio.to_thread(jarvis_chrome.open_url, url_final, nova_aba)
        except JarvisBrowserError as erro:
            return f"Aviso: Não foi possível abrir {nome_exibicao} no navegador da Jarvis: {erro}"

        if tela_destino.strip():
            await asyncio.sleep(0.5)
            alvo_mov = nome_site.strip() or url_final
            await self.mover_aba_ou_janela_para_tela(alvo_mov, tela_destino.strip())

        if resultado == "jarvis_tab":
            return f"Ação executada com sucesso na aba do Chrome ({'nova aba' if nova_aba else 'aba ativa'})."
        return "Ação executada com sucesso no navegador padrão do computador."

    @function_tool(description="Abre um programa ou aplicativo no computador (opcionalmente informando em qual tela/monitor abrir).")
    async def abrir_programa(self, nome_programa: str, tela_destino: str = ""):
        nome_limpo = (
            (nome_programa or "")
            .lower()
            .replace(".exe", "")
            .replace("o programa ", "")
            .replace("aplicativo ", "")
            .strip()
        )
        print(f"[AÇÃO] O Jarvis está tentando abrir o programa: '{nome_limpo}'")

        # Se o usuário pediu para abrir o Painel de Automações ou Painel de Lembretes do Jarvis
        if any(p in nome_limpo for p in ("automacoes", "automações", "automacao", "automação")):
            return await self.listar_automacoes_agendadas()
        if any(p in nome_limpo for p in ("lembretes", "lembrete")):
            return await self.listar_lembretes_painel()

        # Se o usuário pediu para abrir algo que na verdade é um site (ex: youtube, teste de internet, gmail, drive), redireciona para abrir_site
        sites_redirecionados = {
            "youtube": ("https://www.youtube.com", "YouTube"),
            "teste de internet": ("https://fast.com", "Teste de Velocidade Fast.com"),
            "teste de velocidade": ("https://fast.com", "Teste de Velocidade Fast.com"),
            "fast": ("https://fast.com", "Fast.com"),
            "fast.com": ("https://fast.com", "Fast.com"),
            "speedtest": ("https://www.speedtest.net", "Speedtest"),
            "gmail": ("https://mail.google.com/mail/u/0/#inbox", "Gmail"),
            "google drive": ("https://drive.google.com", "Google Drive"),
            "drive": ("https://drive.google.com", "Google Drive"),
        }
        if nome_limpo in sites_redirecionados:
            u, n = sites_redirecionados[nome_limpo]
            return await self.abrir_site(u, n, True, tela_destino=tela_destino)

        try:
            dicionario = {
                "google chrome": ("chrome.exe", ["google chrome", "chrome"]),
                "chrome": ("chrome.exe", ["google chrome", "chrome"]),
                "navegador": ("chrome.exe", ["google chrome", "chrome"]),
                "bloco de notas": ("notepad.exe", ["bloco de notas", "notepad", "sem título", "untitled"]),
                "notepad": ("notepad.exe", ["bloco de notas", "notepad", "sem título", "untitled"]),
                "calculadora": ("calc.exe", ["calculadora", "calculator"]),
                "calc": ("calc.exe", ["calculadora", "calculator"]),
                "paint": ("mspaint.exe", ["paint"]),
                "mspaint": ("mspaint.exe", ["paint"]),
                "explorador de arquivos": ("explorer.exe", ["explorador de arquivos", "file explorer"]),
                "explorador": ("explorer.exe", ["explorador de arquivos", "file explorer"]),
                "explorer": ("explorer.exe", ["explorador de arquivos", "file explorer"]),
                "cmd": ("cmd.exe", ["prompt de comando", "cmd"]),
                "prompt de comando": ("cmd.exe", ["prompt de comando", "cmd"]),
                "powershell": ("powershell.exe", ["powershell"]),
                "vs code": ("code", ["visual studio code", "vscode"]),
                "vscode": ("code", ["visual studio code", "vscode"]),
                "word": ("winword", ["word"]),
                "excel": ("excel", ["excel"]),
                "powerpoint": ("powerpnt", ["powerpoint"]),
                "edge": ("msedge.exe", ["edge"]),
            }
            if nome_limpo in dicionario:
                executavel, termos_foco = dicionario[nome_limpo]
                await asyncio.to_thread(self._abrir_no_desktop_interativo, executavel, "", termos_foco)
            else:
                caminho_atalho = await asyncio.to_thread(self.buscar_atalho_programa, nome_limpo)
                if caminho_atalho:
                    print(f"[INFO] Atalho encontrado em: {caminho_atalho}")
                    await asyncio.to_thread(self._abrir_no_desktop_interativo, caminho_atalho, "", [nome_limpo])
                else:
                    await asyncio.to_thread(self._abrir_no_desktop_interativo, nome_limpo, "", [nome_limpo])

            if tela_destino.strip():
                await asyncio.sleep(0.25)
                res_move = await self.mover_aba_ou_janela_para_tela(nome_limpo, tela_destino.strip())
                return f"Programa '{nome_programa}' aberto com SUCESSO. {res_move}"
            return f"Programa '{nome_programa}' aberto com SUCESSO na tela."
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

        if any(p in nome_limpo for p in ("automacoes", "automações", "automacao", "automação")):
            await self._publicar_automacoes_sync("Painel de Automações fechado.", abrir_painel=False, fechar_painel=True)
            return "Painel de Automações fechado com SUCESSO."
        if any(p in nome_limpo for p in ("lembretes", "lembrete")):
            lista = await asyncio.to_thread(self._carregar_e_limpar_lembretes)
            await self._publicar_lembretes_sync(lista, abrir_painel=False)
            return "Painel de Lembretes atualizado."

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
            def _fechar_janelas_win32() -> int:
                user32 = ctypes.windll.user32
                try:
                    hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                    if hdesk:
                        user32.SetThreadDesktop(hdesk)
                except Exception:
                    pass
                termos = [nome_limpo, processo_alvo]
                if processo_alvo == "notepad":
                    termos.extend(["bloco de notas", "notepad"])
                elif processo_alvo == "calculator":
                    termos.extend(["calculadora", "calculator"])
                fechadas = []

                def _cb(hwnd, _):
                    if user32.IsWindowVisible(hwnd):
                        l = user32.GetWindowTextLengthW(hwnd)
                        if l > 0:
                            b = ctypes.create_unicode_buffer(l + 1)
                            user32.GetWindowTextW(hwnd, b, l + 1)
                            t = (b.value or "").lower()
                            if any(k in t for k in termos if k):
                                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                                fechadas.append(hwnd)
                    return True

                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                user32.EnumWindows(WNDENUMPROC(_cb), 0)
                return len(fechadas)

            qtd_win32 = await asyncio.to_thread(_fechar_janelas_win32)
            if qtd_win32 > 0:
                await asyncio.sleep(0.3)
                return f"Programa '{nome_programa}' fechado com SUCESSO."

            nome_ps = processo_alvo.replace("'", "''")
            ps_command = f"""
            $proc = Get-Process -Name '{nome_ps}' -ErrorAction SilentlyContinue
            if ($proc) {{
                $proc | ForEach-Object {{
                    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
                }}
                Write-Output "SUCESSO"
            }} else {{
                Write-Output "NAO_ENCONTRADO"
            }}
            """
            result = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command], capture_output=True, text=True, timeout=5)
            await asyncio.sleep(0.3)
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
            caminho_final = await asyncio.to_thread(self.buscar_pasta, nome_ou_caminho)

        if caminho_final and os.path.exists(caminho_final):
            try:
                nome_base = os.path.basename(os.path.normpath(caminho_final)).lower()
                await asyncio.to_thread(
                    self._abrir_no_desktop_interativo,
                    "explorer.exe",
                    f'"{caminho_final}"',
                    [nome_base, "explorador", "explorer"],
                )
                return f"Pasta '{nome_ou_caminho}' aberta com SUCESSO na tela."
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

        # 3. Se for especificamente a janela do próprio Jarvis:
        eh_pedido_jarvis = any(
            k in alvo_limpo for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )

        # 4. Mapeamento de sinônimos de programas, pastas e sites
        if eh_pedido_jarvis:
            termos_busca = ["jarvis system", "jarvis", "localhost:3000"]
        else:
            termos_busca = [alvo_limpo]

        mapa_sinonimos = {
            "jarvis": ["jarvis system", "jarvis", "localhost:3000"],
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
            if eh_pedido_jarvis:
                for hwnd, _ in janelas_encontradas:
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
                return "Janela do Jarvis minimizada com sucesso."
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
                length = user32.GetWindowTextLengthW(fg)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(fg, buff, length + 1)
                titulo_fg = buff.value.lower()
                if any(k in titulo_fg for k in ("jarvis", "localhost:3000")):
                    if user32.IsIconic(fg) or user32.IsZoomed(fg):
                        user32.ShowWindow(fg, SW_RESTORE)
                    asyncio.create_task(self.controlar_painel_hud("expandir", "abrir"))
                    return "Interface do Jarvis expandida para o modo completo com transparência ativa."
                user32.ShowWindow(fg, SW_SHOWMAXIMIZED)
                return "Janela atual maximizada com sucesso."

        eh_pedido_jarvis = any(
            k in alvo_limpo for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )
        if eh_pedido_jarvis:
            termos_busca = ["jarvis system", "jarvis", "localhost:3000"]
        else:
            termos_busca = [alvo_limpo]

        mapa_sinonimos = {
            "jarvis": ["jarvis system", "jarvis", "localhost:3000"],
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
            if eh_pedido_jarvis:
                for hwnd, _ in janelas_encontradas:
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SwitchToThisWindow(hwnd, True)
                asyncio.create_task(self.controlar_painel_hud("expandir", "abrir"))
                return "Interface do Jarvis restaurada e expandida no modo completo."

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

        # 1.5. Se for especificamente a janela do próprio Jarvis:
        eh_pedido_jarvis = any(
            k in alvo_norm for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )
        if eh_pedido_jarvis:
            return await asyncio.to_thread(self._minimizar_windows_nativo, "jarvis")

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

        # 1.5. Se for especificamente a janela do próprio Jarvis:
        eh_pedido_jarvis = any(
            k in alvo_norm for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )
        if eh_pedido_jarvis:
            return await asyncio.to_thread(self._maximizar_ou_restaurar_windows_nativo, "jarvis")

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

    def _mover_janela_windows_para_monitor(
        self,
        alvo: str,
        mon_x: int,
        mon_y: int,
        mon_w: int,
        mon_h: int,
        desc_monitor: str,
        forcar_maximizar: bool = False,
    ) -> str:
        """Move uma janela, pasta ou aba destacada para as coordenadas de outro monitor via Win32 API."""
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        SW_SHOWMAXIMIZED = 3

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        alvo_limpo = (
            (alvo or "atual")
            .lower()
            .replace("mover", "")
            .replace("move", "")
            .replace("passar", "")
            .replace("passa", "")
            .replace("jogar", "")
            .replace("joga", "")
            .replace("mandar", "")
            .replace("manda", "")
            .replace("transferir", "")
            .replace("transfere", "")
            .replace("a pasta", "")
            .replace("o programa", "")
            .replace("a janela", "")
            .replace("a aba", "")
            .replace("essa aba", "")
            .replace("esta aba", "")
            .replace("o site", "")
            .replace("do google", "")
            .strip()
        )
        if not alvo_limpo:
            alvo_limpo = "atual"

        def _posicionar_hwnd_no_monitor(hwnd: int, maximizar_preferido: bool = False) -> None:
            estava_maximizada = bool(user32.IsZoomed(hwnd))
            estava_minimizada = bool(user32.IsIconic(hwnd))
            rc = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            larg_orig = max(400, int(rc.right - rc.left))
            alt_orig = max(300, int(rc.bottom - rc.top))

            # Precisa estar em estado 'normal' (restaurada) antes de MoveWindow para mudar o monitor associado no Windows
            if estava_maximizada or estava_minimizada:
                user32.ShowWindow(hwnd, SW_RESTORE)
                time.sleep(0.06)

            buff_chk = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buff_chk, 512)
            eh_jarvis_hwnd = any(k in buff_chk.value.lower() for k in ("jarvis", "localhost:3000"))

            if (estava_maximizada or maximizar_preferido or forcar_maximizar) and not eh_jarvis_hwnd:
                larg_nova = min(max(850, int(mon_w * 0.78)), mon_w - 60)
                alt_nova = min(max(620, int(mon_h * 0.78)), mon_h - 60)
                novo_x = mon_x + (mon_w - larg_nova) // 2
                novo_y = mon_y + (mon_h - alt_nova) // 2
                user32.MoveWindow(hwnd, int(novo_x), int(novo_y), int(larg_nova), int(alt_nova), True)
                time.sleep(0.05)
                user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
            else:
                larg_nova = min(larg_orig, mon_w - 40)
                alt_nova = min(alt_orig, mon_h - 40)
                novo_x = mon_x + (mon_w - larg_nova) // 2
                novo_y = mon_y + (mon_h - alt_nova) // 2
                user32.MoveWindow(hwnd, int(novo_x), int(novo_y), int(larg_nova), int(alt_nova), True)
                if eh_jarvis_hwnd:
                    user32.ShowWindow(hwnd, SW_RESTORE)

            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        # 1. Janela em primeiro plano ("atual", "ativa", "essa", "isso")
        if alvo_limpo in {"atual", "ativa", "esta", "essa", "isso", "janela", "janela atual", "aba", "aba atual"}:
            fg = user32.GetForegroundWindow()
            if fg:
                _posicionar_hwnd_no_monitor(fg, maximizar_preferido=forcar_maximizar)
                return f"Janela atual movida com sucesso para a {desc_monitor}."

        eh_pedido_jarvis = any(
            k in alvo_limpo for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )
        if eh_pedido_jarvis:
            termos_busca = ["jarvis system", "jarvis", "localhost:3000"]
        else:
            termos_busca = [alvo_limpo]

        mapa_sinonimos = {
            "jarvis": ["jarvis system", "jarvis", "localhost:3000"],
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

        def enum_cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    titulo = buff.value
                    titulo_low = titulo.lower()
                    # Protege a janela do Jarvis para não ser movida por engano
                    if "jarvis" not in alvo_limpo and ("jarvis - assistente" in titulo_low or "localhost:3000" in titulo_low):
                        return True
                    if forcar_maximizar and not any(b in titulo_low for b in ("google chrome", "chrome", "edge", "brave", "firefox")):
                        return True
                    if any(t in titulo_low for t in termos_busca if t):
                        janelas_encontradas.append((hwnd, titulo))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

        if janelas_encontradas:
            hwnd_alvo, titulo_alvo = janelas_encontradas[0]
            eh_app_pequeno = any(k in alvo_limpo for k in ("calculadora", "calc"))
            _posicionar_hwnd_no_monitor(hwnd_alvo, maximizar_preferido=not eh_app_pequeno)
            return f"'{titulo_alvo}' movido com sucesso para a {desc_monitor}."

        return ""

    @function_tool(
        description=(
            "Move ou passa qualquer aba/site aberto no navegador (ex.: aba do Google Drive, YouTube, Gmail, WhatsApp, aba atual) "
            "ou qualquer programa, aplicativo ou pasta aberta do Windows (ex.: Calculadora, Bloco de Notas, pasta Downloads, VS Code) "
            "para outra tela/monitor (ex.: 'tela 1', 'tela 2', 'tela 3', 'tela da direita', 'tela da esquerda', 'tela de cima', 'tela principal'). "
            "Se for uma aba que está junto com outras abas no Chrome, ela destaca SOMENTE aquela aba em uma janela própria e a envia maximizada para a tela escolhida!"
        )
    )
    async def mover_aba_ou_janela_para_tela(
        self,
        item_ou_aba: str = "atual",
        tela_destino: str = "tela 2",
    ) -> str:
        alvo_norm = (item_ou_aba or "atual").strip().lower()
        print(f"[AÇÃO] O Jarvis está movendo '{item_ou_aba}' para '{tela_destino}'")

        mon_x, mon_y, mon_w, mon_h, desc_monitor = await asyncio.to_thread(
            self._obter_retangulo_monitor_alvo, tela_destino
        )

        # 0. Se for o próprio Jarvis ("move sua janela para o monitor 2", etc.)
        eh_pedido_jarvis = any(
            k in alvo_norm for k in ("jarvis", "sua janela", "sua tela", "voce", "você", "seu app", "sua", "tela do jarvis", "sistema jarvis")
        )
        if eh_pedido_jarvis:
            res_win = await asyncio.to_thread(
                self._mover_janela_windows_para_monitor,
                "jarvis",
                mon_x,
                mon_y,
                mon_w,
                mon_h,
                desc_monitor,
                False,
            )
            return res_win or f"Janela do Jarvis movida para a {desc_monitor}."

        eh_site_ou_aba = (
            any(
                k in alvo_norm
                for k in (
                    "aba",
                    "guia",
                    "site",
                    "navegador",
                    "chrome",
                    "youtube",
                    "drive",
                    "gmail",
                    "whatsapp",
                    "google",
                    "fast",
                    "speedtest",
                    "http",
                    ".com",
                    ".br",
                    ".net",
                    ".org",
                )
            )
            or alvo_norm in {"atual", "ativa", "ativo", "essa", "esta", "isso"}
        )

        # 1. Se for aba/site do Chrome (ou comando genérico "essa aba"/"atual"), destaca a aba via extensão e posiciona no monitor alvo
        if eh_site_ou_aba:
            try:
                res_ext = await asyncio.to_thread(
                    jarvis_chrome.move_tab_to_monitor,
                    alvo_norm,
                    mon_x,
                    mon_y,
                    mon_w,
                    mon_h,
                )
                if res_ext and res_ext.get("ok"):
                    await asyncio.sleep(0.18)
                    titulo_real = str(res_ext.get("title") or "").strip()
                    alvo_win32 = titulo_real if titulo_real else "chrome"
                    await asyncio.to_thread(
                        self._mover_janela_windows_para_monitor,
                        alvo_win32,
                        mon_x,
                        mon_y,
                        mon_w,
                        mon_h,
                        desc_monitor,
                        True,
                    )
                    titulo_aba = titulo_real or item_ou_aba
                    return f"Aba '{titulo_aba}' destacada e enviada para a {desc_monitor} com sucesso."
            except Exception as erro:
                logger_memoria.warning("evento=mover_aba_ext status=erro motivo=%s", erro)

        # 2. Movimentação nativa de programas, pastas do Explorer ou janelas abertas via Win32
        res_win = await asyncio.to_thread(
            self._mover_janela_windows_para_monitor,
            alvo_norm,
            mon_x,
            mon_y,
            mon_w,
            mon_h,
            desc_monitor,
            eh_site_ou_aba,
        )
        if res_win:
            return res_win

        # 3. Se o programa/pasta ainda não estava aberto quando o usuário pediu 'abre X na tela Y', abre agora e move!
        programas_auto_abertura = {
            "bloco de notas": "notepad.exe",
            "notepad": "notepad.exe",
            "calculadora": "calc.exe",
            "calc": "calc.exe",
            "paint": "mspaint.exe",
            "mspaint": "mspaint.exe",
            "explorador": "explorer.exe",
            "explorador de arquivos": "explorer.exe",
        }
        if alvo_norm in programas_auto_abertura:
            await asyncio.to_thread(
                self._abrir_no_desktop_interativo,
                programas_auto_abertura[alvo_norm],
                "",
                [alvo_norm, "sem título", "notepad", "calculadora"],
            )
            res_win2 = await asyncio.to_thread(
                self._mover_janela_windows_para_monitor,
                alvo_norm,
                mon_x,
                mon_y,
                mon_w,
                mon_h,
                desc_monitor,
                False,
            )
            if res_win2:
                return res_win2

        return f"Aviso: Não encontrei nenhuma aba, pasta ou janela aberta correspondente a '{item_ou_aba}' para mover para a {desc_monitor}."

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
            "Analisa visualmente em tempo real o que está aparecendo na tela do computador do usuário "
            "(usando visão multimodal avançada do Gemini). "
            "Use sempre que o usuário perguntar 'o que você está vendo?', 'lê essa tela para mim', "
            "'está vendo essa parte?', 'qual o erro?', 'resume o que está aberto aqui', "
            "ou durante uma aula quando você quiser checar a tela para comentar elementos visuais ou diagramas específicos."
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
            "Localiza visualmente e clica com precisão do mouse em QUALQUER botão, foto, miniatura, "
            "caixa ou círculo de seleção (checkbox), link, ícone ou elemento visual na tela do computador. "
            "Exemplos: 'clica na foto da geladeira', 'marca a caixinha da terceira foto', 'clica no botão Concluir', "
            "'clica no botão de fechar', 'clica na aba do WhatsApp', 'clica duas vezes no arquivo X'. "
            "`alvo`: o nome ou descrição do elemento a ser clicado na tela (ex: 'botão Concluir', 'círculo de seleção da foto da geladeira', 'foto do motor'). "
            "`tipo_clique`: 'esquerdo', 'direito' ou 'duplo'. "
            "`clique_duplo`: True para dar dois cliques rápidos com o botão esquerdo."
        )
    )
    async def clicar_em_elemento_ou_imagem(
        self,
        alvo: str,
        tipo_clique: str = "esquerdo",
        clique_duplo: bool = False,
        qual_tela: str = "atual",
    ) -> str:
        termo = (alvo or "").strip()
        if not termo:
            return "Aviso: informe o que você deseja clicar na tela (ex.: 'botão Concluir', 'foto da geladeira', 'círculo de seleção da foto')."

        loc = await asyncio.to_thread(self._localizar_elemento_tela, termo, qual_tela)
        if not loc.get("encontrado"):
            motivo = loc.get("motivo", "não localizado visualmente")
            return f"Não consegui localizar '{termo}' na tela atual ({motivo}). Você pode pedir para rolar a página ou especificar com outros detalhes."

        target_x = loc["target_x"]
        target_y = loc["target_y"]
        desc = loc.get("descricao", termo)
        desc_monitor = loc.get("desc_monitor", "tela")

        print(f"[CLIQUE VISUAL] Clicando em '{desc}' nas coordenadas ({target_x}, {target_y}) na {desc_monitor}")
        await asyncio.to_thread(self._executar_clique_mouse, target_x, target_y, tipo_clique, clique_duplo)

        # Aguarda 0.8s para a ação refletir na tela
        await asyncio.sleep(0.8)

        # Inspeciona a tela após o clique para reportar ao usuário o que abriu ou mudou
        nova_visao = ""
        try:
            img_pos = await asyncio.to_thread(self._capturar_imagem_tela, qual_tela)
            if img_pos:
                prompt_pos = (
                    f"O usuário clicou em '{desc}'. "
                    "Descreva em 1 ou 2 frases curtas o que aconteceu ou o que se abriu na tela agora."
                )
                nova_visao = await asyncio.to_thread(self._chamar_gemini_visao, img_pos, prompt_pos)
        except Exception:
            pass

        msg_ret = f"Cliquei com sucesso em '{desc}' na tela ({desc_monitor}, posição x={target_x}, y={target_y})."
        if nova_visao and not nova_visao.startswith("Aviso:"):
            msg_ret += f" O que apareceu na tela: {nova_visao}"
        return msg_ret

    @function_tool(
        description=(
            "Executa IMEDIATAMENTE um clique na posição EXATA onde o cursor do mouse do usuário já está parado na tela. "
            "NÃO precisa procurar nada visualmente nem tirar print: é instantâneo e ultra rápido. "
            "Use sempre que o usuário disser comandos diretos de mouse: "
            "- 'clica', 'clicar', 'dá um clique' -> tipo_clique='esquerdo', clique_duplo=False; "
            "- 'clique duplo', 'dois cliques', 'duplo clique', 'clica duas vezes' -> tipo_clique='esquerdo', clique_duplo=True; "
            "- 'clica com botão direito', 'clique com botão direito', 'botão direito' -> tipo_clique='direito', clique_duplo=False; "
            "- 'clique duplo com botão direito', 'dois cliques com botão direito' -> tipo_clique='direito', clique_duplo=True. "
            "`tipo_clique`: 'esquerdo' ou 'direito'. "
            "`clique_duplo`: True para dois cliques rápidos, False para clique simples."
        )
    )
    async def clicar_na_posicao_atual_do_mouse(
        self,
        tipo_clique: str = "esquerdo",
        clique_duplo: bool = False,
    ) -> str:
        x, y = await asyncio.to_thread(self._obter_posicao_atual_mouse)
        tipo_normalizado = "direito" if "direito" in str(tipo_clique).lower() else "esquerdo"
        duplo = bool(clique_duplo)

        print(f"[CLIQUE MOUSE ATUAL] Clicando na posição ({x}, {y}) | Botão: {tipo_normalizado} | Duplo: {duplo}")
        await asyncio.to_thread(self._executar_clique_mouse, x, y, tipo_normalizado, duplo)

        nome_botao = "botão direito" if tipo_normalizado == "direito" else "botão esquerdo"
        acao = "Clique duplo com o" if duplo else "Clique com o"
        return f"{acao} {nome_botao} executado onde o mouse estava apontado ({x}, {y})."

    @function_tool(
        description=(
            "Localiza visualmente um campo de busca/pesquisa ou caixa de texto na tela, clica nele com precisão, "
            "limpa o texto anterior (se houver), digita o termo desejado e opcionalmente pressiona Enter para submeter. "
            "Use sempre que o usuário pedir: 'pesquise por X no campo de busca', 'procure fotos de geladeira', "
            "'digite X na barra de pesquisa e dê enter', 'busque tal termo no Google Fotos/YouTube/site', 'clique e escreva X'. "
            "`alvo_campo`: descrição do campo onde deve clicar para digitar (ex: 'campo de pesquisa Pesquise fotos e álbuns', 'barra de busca', 'campo de texto'). "
            "`texto_para_digitar`: o texto que deve ser digitado/pesquisado. "
            "`pressionar_enter`: True para apertar Enter após digitar e submeter a busca (padrão True). "
            "`limpar_campo_antes`: True para selecionar tudo (Ctrl+A) e apagar antes de digitar (padrão True)."
        )
    )
    async def procurar_e_escrever_na_tela(
        self,
        alvo_campo: str = "campo de pesquisa",
        texto_para_digitar: str = "",
        pressionar_enter: bool = True,
        limpar_campo_antes: bool = True,
        qual_tela: str = "atual",
    ) -> str:
        termo_campo = (alvo_campo or "campo de pesquisa").strip()
        loc = await asyncio.to_thread(self._localizar_elemento_tela, termo_campo, qual_tela)
        if not loc.get("encontrado") and termo_campo != "campo de pesquisa":
            loc = await asyncio.to_thread(self._localizar_elemento_tela, "campo de pesquisa ou barra de busca", qual_tela)

        if not loc.get("encontrado"):
            motivo = loc.get("motivo", "não localizado visualmente")
            return f"Não consegui localizar o campo de busca '{termo_campo}' na tela ({motivo})."

        target_x = loc["target_x"]
        target_y = loc["target_y"]
        desc = loc.get("descricao", termo_campo)

        # 1. Clica no campo
        await asyncio.to_thread(self._executar_clique_mouse, target_x, target_y, "esquerdo", False)
        await asyncio.sleep(0.3)

        # 2. Limpa o campo se solicitado
        if limpar_campo_antes:
            await asyncio.to_thread(self._executar_limpar_campo)
            await asyncio.sleep(0.08)

        # 3. Digita / cola o texto e dá Enter
        if texto_para_digitar:
            await asyncio.to_thread(self._copiar_para_clipboard, texto_para_digitar)
            await asyncio.to_thread(self._executar_colar_no_cursor, pressionar_enter)
        elif pressionar_enter:
            await asyncio.to_thread(self._executar_teclas_teclado, "enter", 1)

        # 4. Aguarda a página reagir e lê o que apareceu
        await asyncio.sleep(1.0)
        nova_visao = ""
        try:
            img_pos = await asyncio.to_thread(self._capturar_imagem_tela, qual_tela)
            if img_pos:
                prompt_pos = (
                    f"O usuário pesquisou por '{texto_para_digitar}' no campo '{desc}'. "
                    "Descreva em 1 ou 2 frases curtas e objetivas o que apareceu ou mudou na tela agora."
                )
                nova_visao = await asyncio.to_thread(self._chamar_gemini_visao, img_pos, prompt_pos)
        except Exception:
            pass

        msg_ret = f"Pesquisei por '{texto_para_digitar}' no campo '{desc}'"
        if pressionar_enter:
            msg_ret += " e apertei Enter."
        else:
            msg_ret += "."
        if nova_visao and not nova_visao.startswith("Aviso:"):
            msg_ret += f" O que apareceu na tela: {nova_visao}"
        return msg_ret

    @function_tool(
        description=(
            "Pressiona uma ou mais teclas ou atalhos de teclado no computador. "
            "Suporta teclas: 'enter', 'esc', 'tab', 'backspace', 'delete', 'espaco', 'seta_baixo', 'seta_cima', "
            "'seta_esquerda', 'seta_direita', 'page_down', 'page_up', 'home', 'end', 'f5'. "
            "Suporta atalhos: 'ctrl+a', 'ctrl+c', 'ctrl+v', 'ctrl+x', 'ctrl+f', 'ctrl+w', 'ctrl+t', 'ctrl+z', 'alt+f4', 'alt+tab'. "
            "Exemplos de pedidos: 'dê enter', 'aperte enter', 'aperte esc', 'dê tab', 'aperte ctrl+a', 'selecione tudo com ctrl+a'."
        )
    )
    async def pressionar_teclas_teclado(
        self,
        teclas: str,
        repeticoes: int = 1,
    ) -> str:
        sucesso, msg = await asyncio.to_thread(self._executar_teclas_teclado, teclas, repeticoes)
        return msg

    @function_tool(
        description=(
            "Rola (faz scroll) na página da web, aplicativo ou tela ativa para cima ou para baixo com a rodinha do mouse. "
            "Use quando o usuário pedir 'rola para baixo', 'desce a página', 'sobe', 'mostra mais fotos', "
            "ou quando quiser visualizar mais conteúdos que estão abaixo na tela."
        )
    )
    async def rolar_pagina_ou_tela(
        self,
        direcao: str = "baixo",
        quantidade_linhas: int = 5,
    ) -> str:
        await asyncio.to_thread(self._executar_scroll_mouse, quantidade_linhas, direcao)
        dir_txt = "baixo" if str(direcao).lower() in ("baixo", "down", "descer", "descendo") else "cima"
        return f"Rolei a página para {dir_txt} com sucesso."

    @staticmethod
    def _copiar_para_clipboard(texto: str) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        dados = str(texto).encode("utf-16le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dados))
        if not h_mem:
            return False

        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            return False

        ctypes.memmove(ptr, dados, len(dados))
        kernel32.GlobalUnlock(h_mem)

        aberto = False
        for _ in range(6):
            if user32.OpenClipboard(None):
                aberto = True
                break
            time.sleep(0.02)

        if not aberto:
            kernel32.GlobalFree(h_mem)
            return False

        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        finally:
            user32.CloseClipboard()
        return True

    @staticmethod
    def _executar_colar_no_cursor(pressionar_enter: bool = False) -> None:
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_V = 0x56
        VK_RETURN = 0x0D
        KEYEVENTF_KEYUP = 0x0002

        time.sleep(0.06)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

        if pressionar_enter:
            time.sleep(0.08)
            user32.keybd_event(VK_RETURN, 0, 0, 0)
            time.sleep(0.03)
            user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)

    @function_tool(
        description=(
            "Digita, redige ou cola um texto diretamente onde o cursor do usuário estiver ativo/piscando na tela "
            "(WhatsApp Web ou Desktop, Bloco de Notas, Word, e-mail, terminal, navegador, Notion, Slack, Teams, Discord, etc.). "
            "Use sempre que o usuário pedir: 'digita aqui', 'escreva aqui', 'redija uma mensagem aqui', "
            "'escreve para mim no cursor', 'preenche com tal texto', 'cola esse texto onde estou digitando', "
            "'responda a essa mensagem no chat', 'escreva uma resposta educada dizendo que...'. "
            "Você mesmo pode redigir a mensagem completa, profissional, natural e adequada com base no que o usuário instruiu "
            "e passar o texto pronto no parâmetro `texto`. "
            "`texto`: o conteúdo completo que deve ser escrito no cursor ativo. "
            "`pressionar_enter`: coloque True se o usuário pediu para enviar ou apertar Enter logo em seguida (ex.: em apps de chat). "
            "`apenas_copiar`: coloque True se o usuário pediu apenas para copiar o texto para a área de transferência sem colar."
        )
    )
    async def digitar_ou_redigir_no_cursor(
        self,
        texto: str,
        pressionar_enter: bool = False,
        apenas_copiar: bool = False,
    ) -> str:
        if not texto:
            return "Erro: nenhum texto fornecido para digitar ou colar."

        copiado = await asyncio.to_thread(self._copiar_para_clipboard, texto)
        if not copiado:
            return "Não foi possível acessar a área de transferência do Windows para colar o texto."

        if apenas_copiar:
            return "Texto copiado com sucesso para a área de transferência."

        await asyncio.to_thread(self._executar_colar_no_cursor, pressionar_enter)
        preview = texto.strip().replace("\n", " ")
        if len(preview) > 50:
            preview = preview[:47] + "..."
        if pressionar_enter:
            return f"Texto inserido e enviado com Enter: '{preview}'."
        return f"Texto inserido no cursor: '{preview}'."

    def _resolver_caderno_por_nome(self, termo: str) -> tuple[str, str] | None:
        """Resolve o ID e o Nome amigável de um caderno a partir de um termo buscado (ex.: 'porto panorama' -> ('porto-panorama-mwi8nju3', 'PORTO PANORAMA'))"""
        pasta_nb = os.path.join(_backend_dir, "workspace_arquivos", "notebooks")
        if not os.path.isdir(pasta_nb):
            return None

        termo_limpo = termo.lower().strip()
        termo_busca = re.sub(r"\b(caderno|notebook|do|da|de|dos|das|o|a|os|as|painel|abra|abre|mostrar|mostre|entra|seleciona)\b", "", termo_limpo).strip()
        if not termo_busca:
            termo_busca = termo_limpo

        candidatos = []
        for nb_id in os.listdir(pasta_nb):
            nb_dir = os.path.join(pasta_nb, nb_id)
            if os.path.isdir(nb_dir):
                nome = nb_id
                meta_path = os.path.join(nb_dir, "notebook.json")
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as fm:
                            meta = json.load(fm)
                            nome = meta.get("name", nome)
                    except Exception:
                        pass
                candidatos.append((nb_id, nome))

        for nb_id, nome in candidatos:
            if termo_busca == nome.lower() or termo_busca == nb_id.lower():
                return nb_id, nome

        for nb_id, nome in candidatos:
            if termo_busca in nome.lower() or termo_busca in nb_id.lower() or nome.lower() in termo_busca:
                return nb_id, nome

        return None

    @function_tool(
        description=(
            "Abre a Central de Arquivos na tela e seleciona/abre diretamente um caderno ou notebook específico pelo nome "
            "(ex.: 'Porto Panorama', 'Imóvel', 'Geral', 'Contrato'). "
            "Use OBRIGATORIAMENTE sempre que o usuário pedir: 'abra o caderno X', 'mostra o notebook Y', "
            "'entra no caderno Z', 'seleciona o caderno Porto Panorama', 'abre o caderno do Porto Panorama'."
            "`nome_caderno`: o nome ou termo de busca do caderno (ex.: 'Porto Panorama', 'Geral')."
        )
    )
    async def abrir_ou_selecionar_caderno(self, nome_caderno: str) -> str:
        resolvido = self._resolver_caderno_por_nome(nome_caderno)
        if not resolvido:
            pasta_nb = os.path.join(_backend_dir, "workspace_arquivos", "notebooks")
            existentes = []
            if os.path.isdir(pasta_nb):
                for f in os.listdir(pasta_nb):
                    meta_p = os.path.join(pasta_nb, f, "notebook.json")
                    if os.path.isfile(meta_p):
                        try:
                            with open(meta_p, "r", encoding="utf-8") as fm:
                                existentes.append(json.load(fm).get("name", f))
                        except Exception:
                            pass
            return f"Não encontrei um caderno com o nome '{nome_caderno}'. Cadernos existentes: {', '.join(existentes) if existentes else 'Geral'}."

        nb_id, nb_nome = resolvido
        self._active_notebook_id = nb_id
        self._workspace_panel_open = True

        payload_hud = {
            "type": "jarvis_hud_control",
            "panel": "files",
            "action": "open",
            "notebookId": nb_id,
            "notebookName": nb_nome,
        }
        payload_ws = {
            "type": "jarvis_switch_notebook",
            "notebookId": nb_id,
            "notebookName": nb_nome,
        }

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload_hud, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload_ws, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception as e:
            logger_memoria.error("evento=abrir_caderno erro=%s", e)

        return f"Caderno '{nb_nome}' aberto na tela com sucesso."

    @function_tool(
        description=(
            "Cria um novo Caderno / Notebook na Central de Arquivos com o nome desejado "
            "e abre-o imediatamente na tela. "
            "Use sempre que o usuário pedir: 'crie um caderno chamado X', 'crie um notebook com o nome Y', "
            "'cria um caderno novo para tal projeto', 'adiciona um novo caderno'."
            "`nome_caderno`: o título do novo caderno (ex.: 'Porto Panorama', 'Imposto de Renda 2026', 'Processo X')."
        )
    )
    async def criar_novo_caderno(self, nome_caderno: str) -> str:
        nome_limpo = (nome_caderno or "").strip()
        if not nome_limpo:
            return "Por favor, informe o nome para o novo caderno."

        pasta_root = os.path.join(_backend_dir, "workspace_arquivos", "notebooks")
        os.makedirs(pasta_root, exist_ok=True)

        slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize('NFKD', nome_limpo.lower())).strip("-") or "caderno"
        nb_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        nb_dir = os.path.join(pasta_root, nb_id)
        os.makedirs(nb_dir, exist_ok=True)

        meta = {
            "id": nb_id,
            "name": nome_limpo,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
        }
        with open(os.path.join(nb_dir, "notebook.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        with open(os.path.join(nb_dir, "chat_history.json"), "w", encoding="utf-8") as f:
            json.dump([], f)

        self._active_notebook_id = nb_id
        self._workspace_panel_open = True

        # Notifica o HUD para abrir o painel de arquivos e focar no novo caderno
        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_hud_control",
                        "panel": "files",
                        "action": "open",
                        "notebookId": nb_id,
                        "notebookName": nome_limpo,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebooks_updated",
                        "activeNotebookId": nb_id,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception as e:
            logger_memoria.error("evento=criar_caderno erro_broadcast=%s", e)

        return f"Caderno '{nome_limpo}' criado e aberto na tela com sucesso."

    @function_tool(
        description=(
            "Exclui permanentemente um Caderno / Notebook e todos os seus arquivos da Central de Arquivos. "
            "ATENÇÃO: O caderno padrão 'Geral' nunca pode ser excluído. "
            "Use sempre que o usuário pedir: 'apague o caderno X', 'exclua o notebook Y', 'deleta o caderno tal'."
            "`nome_caderno`: o nome do caderno que deve ser apagado (ex.: 'Porto Panorama', 'Imóvel')."
        )
    )
    async def excluir_caderno(self, nome_caderno: str) -> str:
        resolvido = self._resolver_caderno_por_nome(nome_caderno)
        if not resolvido:
            return f"Não encontrei nenhum caderno com o nome '{nome_caderno}' para excluir."

        nb_id, nb_nome = resolvido
        if nb_id == "geral":
            return "O caderno padrão 'Geral' não pode ser excluído."

        pasta_dir = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id)
        if os.path.isdir(pasta_dir):
            try:
                shutil.rmtree(pasta_dir, ignore_errors=True)
            except Exception as e:
                return f"Não foi possível excluir o caderno '{nb_nome}' ({e})."

        if self._active_notebook_id == nb_id:
            self._active_notebook_id = "geral"

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebooks_updated",
                        "activeNotebookId": self._active_notebook_id,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception:
            pass

        return f"Caderno '{nb_nome}' foi excluído com sucesso."

    @function_tool(
        description=(
            "Renomeia um Caderno / Notebook existente na Central de Arquivos. "
            "Use sempre que o usuário pedir: 'renomeie o caderno X para Y', 'muda o nome do notebook tal para Z'."
            "`nome_atual`: o nome atual do caderno que será renomeado. "
            "`novo_nome`: o novo nome desejado para o caderno."
        )
    )
    async def renomear_caderno(self, nome_atual: str, novo_nome: str) -> str:
        novo_limpo = (novo_nome or "").strip()
        if not novo_limpo:
            return "Por favor, informe o novo nome para o caderno."

        resolvido = self._resolver_caderno_por_nome(nome_atual)
        if not resolvido:
            return f"Não encontrei o caderno '{nome_atual}' para renomear."

        nb_id, nb_antigo = resolvido
        meta_path = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id, "notebook.json")
        meta = {"id": nb_id, "name": novo_limpo, "updatedAt": datetime.now().isoformat()}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fm:
                    meta = json.load(fm)
            except Exception:
                pass
        meta["name"] = novo_limpo
        meta["updatedAt"] = datetime.now().isoformat()
        with open(meta_path, "w", encoding="utf-8") as fm:
            json.dump(meta, fm, indent=2, ensure_ascii=False)

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebooks_updated",
                        "activeNotebookId": self._active_notebook_id,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception:
            pass

        return f"Caderno renomeado de '{nb_antigo}' para '{novo_limpo}' com sucesso."

    @function_tool(
        description=(
            "Define ou altera o Especialista Consultor de um Caderno / Notebook na Central de Arquivos. "
            "Especialistas disponíveis: "
            "1) 'parecer_duplo' (Parecer Duplo: Advogado + Síndico - Ideal para condomínios como Porto Panorama); "
            "2) 'advogado_condominial' (Advogado Especialista em Direito Imobiliário e Notarial); "
            "3) 'sindico_profissional' (Síndico Profissional e Gestor Predial); "
            "4) 'cfo_financeiro' (CFO & Auditor Contábil / Financeiro); "
            "5) 'medico' (Médico Perito e Medicina Baseada em Evidências); "
            "6) 'dev_senior' (Engenheiro de Software Sênior e Arquiteto); "
            "7) 'especialista_compras' (Especialista em Compras, Smart Shopping & Personal Shopper); "
            "8) 'jarvis' (JARVIS Executivo Padrão). "
            "Use sempre que o usuário pedir: 'coloque o advogado e síndico no caderno Porto Panorama', "
            "'defina o especialista do caderno como advogado', 'mude o especialista para médico', etc. "
            "`especialista`: nome ou termo do especialista desejado. "
            "`nome_caderno`: nome do caderno (se vazio, usa o caderno atualmente ativo)."
        )
    )
    async def definir_especialista_caderno(self, especialista: str, nome_caderno: str = "") -> str:
        esp_termo = (especialista or "").lower().strip()
        termo_nb = (nome_caderno or "").strip()

        id_especialista = "jarvis"
        nome_exib = "JARVIS Executivo"

        if any(w in esp_termo for w in ["duplo", "ambos", "dois", "advogado e sindico", "advogado e síndico", "sindico e advogado", "síndico e advogado"]):
            id_especialista = "parecer_duplo"
            nome_exib = "Parecer Duplo (Advogado + Síndico)"
        elif any(w in esp_termo for w in ["advogado", "juridico", "jurídico", "direito", "lei", "imobiliario", "imobiliário", "notarial"]):
            id_especialista = "advogado_condominial"
            nome_exib = "Advogado Condominial"
        elif any(w in esp_termo for w in ["sindico", "síndico", "predial", "gestor"]):
            id_especialista = "sindico_profissional"
            nome_exib = "Síndico Profissional"
        elif any(w in esp_termo for w in ["cfo", "financeiro", "contabil", "contábil", "auditor", "financas", "finanças"]):
            id_especialista = "cfo_financeiro"
            nome_exib = "CFO & Auditor Contábil"
        elif any(w in esp_termo for w in ["medico", "médico", "saude", "saúde", "clinico", "clínico", "doutor"]):
            id_especialista = "medico"
            nome_exib = "Médico Perito"
        elif any(w in esp_termo for w in ["dev", "programador", "desenvolvedor", "software", "engenheiro", "arquiteto", "codigo", "código"]):
            id_especialista = "dev_senior"
            nome_exib = "Engenheiro de Software Sênior"
        elif any(w in esp_termo for w in ["compra", "compras", "shopper", "shopping", "produto", "preco", "preço", "ofertas", "mercado livre", "amazon"]):
            id_especialista = "especialista_compras"
            nome_exib = "Especialista em Compras & Smart Shopping"
        elif any(w in esp_termo for w in ["jarvis", "padrao", "padrão", "executivo"]):
            id_especialista = "jarvis"
            nome_exib = "JARVIS Executivo"

        if not termo_nb or termo_nb.lower() in ("atual", "este", "aberto", "painel"):
            nb_id = self._active_notebook_id or "geral"
            nb_nome = nb_id.capitalize()
            meta_p = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id, "notebook.json")
            if os.path.isfile(meta_p):
                try:
                    with open(meta_p, "r", encoding="utf-8") as fm:
                        nb_nome = json.load(fm).get("name", nb_nome)
                except Exception:
                    pass
        else:
            resolvido = self._resolver_caderno_por_nome(termo_nb)
            if not resolvido:
                return f"Não encontrei o caderno '{termo_nb}' para definir o especialista."
            nb_id, nb_nome = resolvido

        pasta_nb = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id)
        os.makedirs(pasta_nb, exist_ok=True)
        meta_path = os.path.join(pasta_nb, "notebook.json")

        meta = {"id": nb_id, "name": nb_nome, "expert": id_especialista, "updatedAt": datetime.now().isoformat()}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fm:
                    meta = json.load(fm)
            except Exception:
                pass

        meta["expert"] = id_especialista
        meta["updatedAt"] = datetime.now().isoformat()
        with open(meta_path, "w", encoding="utf-8") as fm:
            json.dump(meta, fm, indent=2, ensure_ascii=False)

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebook_expert_updated",
                        "notebookId": nb_id,
                        "expert": id_especialista,
                        "expertName": nome_exib,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebooks_updated",
                        "activeNotebookId": self._active_notebook_id,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception:
            pass

        return f"Especialista do caderno '{nb_nome}' configurado para: **{nome_exib}**."

    @function_tool(
        description=(
            "Lista todos os Cadernos / Notebooks existentes na Central de Arquivos, informando nome, "
            "quantidade de arquivos e qual está aberto atualmente. "
            "Use sempre que o usuário perguntar: 'quais cadernos eu tenho?', 'quais notebooks existem?', "
            "'lista meus cadernos'."
        )
    )
    async def listar_cadernos(self) -> str:
        pasta_root = os.path.join(_backend_dir, "workspace_arquivos", "notebooks")
        if not os.path.isdir(pasta_root):
            return "Nenhum caderno encontrado. O caderno padrão é o 'Geral'."

        linhas = []
        for nb_id in os.listdir(pasta_root):
            nb_dir = os.path.join(pasta_root, nb_id)
            if not os.path.isdir(nb_dir):
                continue
            nome = nb_id.capitalize()
            meta_path = os.path.join(nb_dir, "notebook.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as fm:
                        nome = json.load(fm).get("name", nome)
                except Exception:
                    pass
            arquivos = [f for f in os.listdir(nb_dir) if f not in ("notebook.json", "chat_history.json") and os.path.isfile(os.path.join(nb_dir, f))]
            ativo_txt = " (Ativo na tela)" if nb_id == self._active_notebook_id else ""
            linhas.append(f"- 📁 **{nome}** ({len(arquivos)} arquivo(s)){ativo_txt}")

        if not linhas:
            return "Você tem apenas o caderno padrão 'Geral' no momento."

        return f"Cadernos existentes ({len(linhas)}):\n" + "\n".join(linhas)

    @function_tool(
        description=(
            "Consulta e lê o histórico completo de conversas, perguntas, análises e pareceres de um Caderno / Notebook específico na Central de Arquivos. "
            "Use OBRIGATORIAMENTE sempre que o usuário perguntar: 'o que a gente conversou da última vez?', "
            "'o que nós conversamos neste caderno?', 'o que discutimos sobre o Porto Panorama?', "
            "'qual foi a última coisa que falamos sobre motos/regras/vagas?', 'o que tem registrado no chat desse caderno?'. "
            "`nome_caderno`: nome do caderno que deseja consultar (se vazio ou 'atual', consulta o caderno ativo no momento, como 'Porto Panorama')."
        )
    )
    async def consultar_historico_conversa_caderno(self, nome_caderno: str = "") -> str:
        termo = (nome_caderno or "").strip()
        if not termo or termo in ("atual", "este", "aberto", "painel"):
            nb_id = self._active_notebook_id or "geral"
            nb_nome = nb_id.capitalize()
            meta_p = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id, "notebook.json")
            if os.path.isfile(meta_p):
                try:
                    with open(meta_p, "r", encoding="utf-8") as fm:
                        nb_nome = json.load(fm).get("name", nb_nome)
                except Exception:
                    pass
        else:
            resolvido = self._resolver_caderno_por_nome(termo)
            if not resolvido:
                return f"Não encontrei o caderno '{termo}' para consultar o histórico."
            nb_id, nb_nome = resolvido

        pasta_nb = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id)
        chat_path = os.path.join(pasta_nb, "chat_history.json")

        if not os.path.isfile(chat_path):
            return f"O caderno '{nb_nome}' ainda não possui histórico de conversas."

        try:
            with open(chat_path, "r", encoding="utf-8") as fch:
                msgs = json.load(fch)
        except Exception as e:
            return f"Não foi possível ler as conversas do caderno '{nb_nome}' ({e})."

        if not isinstance(msgs, list) or not msgs:
            return f"O caderno '{nb_nome}' está vazio no momento (nenhuma conversa registrada)."

        # Filtra mensagens válidas e relevantes (removendo ruídos pontuais)
        mensagens_relevantes = []
        for m in msgs:
            c = (m.get("content") or "").strip()
            if len(c) < 3 or c.lower() in ("mhm.", "mhm", "¿qué?", "fine", "central de arquivos", "caderno", "что"):
                continue
            r = m.get("role", "user")
            exp_nome = m.get("expertName") or m.get("expert") or "JARVIS"
            mensagens_relevantes.append({
                "papel": "Usuário" if r == "user" else f"JARVIS ({exp_nome})",
                "texto": c,
                "data": m.get("timestamp", ""),
            })

        if not mensagens_relevantes:
            return f"O caderno '{nb_nome}' não tem conversas substantivas registradas ainda."

        # Pega as últimas 10 mensagens mais relevantes
        ultimas = mensagens_relevantes[-10:]
        bloco_formatado = []
        for item in ultimas:
            txt = item["texto"]
            if len(txt) > 800:
                txt = txt[:800] + "... [continua com detalhes técnicos no caderno]"
            bloco_formatado.append(f"• **{item['papel']}**: {txt}")

        resultado = (
            f"Histórico de conversas do caderno '{nb_nome}' (total de {len(mensagens_relevantes)} interações):\n\n"
            + "\n\n".join(bloco_formatado)
        )
        return resultado

    @function_tool(
        description=(
            "Limpa apenas o histórico de conversas e mensagens de um Caderno / Notebook, "
            "MANTENDO TODOS OS ARQUIVOS E DOCUMENTOS INTACTOS. "
            "Use sempre que o usuário pedir: 'limpe o histórico do caderno X', 'apague as mensagens do caderno tal', "
            "'limpar a conversa do caderno Porto Panorama', 'zere o chat do caderno'. "
            "`nome_caderno`: nome do caderno (se vazio ou 'atual', limpa o caderno ativo no momento)."
        )
    )
    async def limpar_historico_caderno(self, nome_caderno: str = "") -> str:
        termo = (nome_caderno or "").strip()
        if not termo or termo in ("atual", "este", "aberto", "painel"):
            nb_id = self._active_notebook_id or "geral"
            nb_nome = nb_id.capitalize()
            meta_p = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id, "notebook.json")
            if os.path.isfile(meta_p):
                try:
                    with open(meta_p, "r", encoding="utf-8") as fm:
                        nb_nome = json.load(fm).get("name", nb_nome)
                except Exception:
                    pass
        else:
            resolvido = self._resolver_caderno_por_nome(termo)
            if not resolvido:
                return f"Não encontrei o caderno '{termo}' para limpar o histórico."
            nb_id, nb_nome = resolvido

        chat_path = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_id, "chat_history.json")
        try:
            with open(chat_path, "w", encoding="utf-8") as fch:
                json.dump([], fch)
        except Exception as e:
            return f"Erro ao limpar histórico ({e})."

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebook_chat_cleared",
                        "notebookId": nb_id,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception:
            pass

        return f"Histórico de mensagens do caderno '{nb_nome}' limpo com sucesso. Todos os arquivos e documentos continuam salvos."

    @function_tool(
        description=(
            "Move ou transfere um arquivo/documento de um Caderno para outro Caderno na Central de Arquivos. "
            "Use sempre que o usuário pedir: 'mova o boleto para o caderno Porto Panorama', "
            "'transfira o contrato para o caderno tal', 'passa o arquivo X para o caderno Y'. "
            "`nome_arquivo`: nome ou parte do nome do arquivo a ser movido (ex.: 'Boleto_39060.pdf'). "
            "`caderno_destino`: nome do caderno que receberá o arquivo (ex.: 'Porto Panorama'). "
            "`caderno_origem`: nome do caderno onde o arquivo está atualmente (se vazio, busca no caderno ativo ou em qualquer caderno)."
        )
    )
    async def mover_arquivo_entre_cadernos(
        self,
        nome_arquivo: str,
        caderno_destino: str,
        caderno_origem: str = "",
    ) -> str:
        res_dest = self._resolver_caderno_por_nome(caderno_destino)
        if not res_dest:
            return f"Caderno de destino '{caderno_destino}' não foi encontrado."
        id_dest, nome_dest = res_dest

        pasta_root = os.path.join(_backend_dir, "workspace_arquivos", "notebooks")
        termo_arq = (nome_arquivo or "").lower().strip()

        caminho_origem = None
        nome_salvo_origem = None
        id_orig = None

        if caderno_origem:
            res_orig = self._resolver_caderno_por_nome(caderno_origem)
            if res_orig:
                id_orig = res_orig[0]

        cadernos_para_buscar = [id_orig] if id_orig else [self._active_notebook_id, "geral"] + os.listdir(pasta_root)

        for cid in cadernos_para_buscar:
            cdir = os.path.join(pasta_root, cid)
            if not os.path.isdir(cdir):
                continue
            for f in os.listdir(cdir):
                if f in ("notebook.json", "chat_history.json"):
                    continue
                if termo_arq in f.lower():
                    caminho_origem = os.path.join(cdir, f)
                    nome_salvo_origem = f
                    id_orig = cid
                    break
            if caminho_origem:
                break

        if not caminho_origem or not nome_salvo_origem:
            return f"Não encontrei o arquivo '{nome_arquivo}' para mover."

        if id_orig == id_dest:
            return f"O arquivo '{nome_arquivo}' já está dentro do caderno '{nome_dest}'."

        caminho_destino_final = os.path.join(pasta_root, id_dest, nome_salvo_origem)
        try:
            shutil.move(caminho_origem, caminho_destino_final)
        except Exception as e:
            return f"Erro ao mover arquivo ({e})."

        nome_limpo = re.sub(r"^[a-zA-Z0-9]{8}-", "", nome_salvo_origem)

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "jarvis_notebook_files_updated",
                        "notebookId": id_dest,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.workspace",
                )
        except Exception:
            pass

        return f"Arquivo '{nome_limpo}' movido com sucesso para o caderno '{nome_dest}'."

    @function_tool(
        description=(
            "Abre, fecha ou alterna qualquer painel visual do HUD do Jarvis na tela. "
            "Use OBRIGATORIAMENTE sempre que o usuário pedir: 'abra o painel X', 'mostra os lembretes', "
            "'fecha o painel de arquivos', 'abra a central de arquivos', 'abre o painel de sistema', "
            "'mostre o relógio', 'abre o trader', 'fecha todos os painéis', 'mostra as automações', "
            "'abre as atividades', 'abre o compositor de e-mail', 'modo orb', 'ativar orb', 'recolhe os painéis', 'expandir tela'. "
            "`painel`: nome do painel a controlar: 'arquivos' (Central de Arquivos / Notebooks), 'lembretes' (Painel de Lembretes), "
            "'automacoes' (Painel de Automações Gmail/Drive), 'email' (Compositor de E-mails), 'sistema' (Dashboard de CPU/RAM/Rede), "
            "'atividades' (Painel de Ações e Tarefas em Andamento), 'trader' (Painel Quant / Cripto / Mercado), "
            "'atalhos' (Painel de Atalhos do Jarvis para sites e apps — NÃO é o navegador), "
            "'relogio' (Relógio HUD), 'neural' (Visualizador da Rede Neural), 'orb' (Modo Orb 5x5 cm / Apenas o Orb com transparência), "
            "'expandir' (Modo Completo / Restaurar painéis) ou 'todos' (para abrir ou fechar todos de uma vez). "
            "`acao`: 'abrir' (padrão, para exibir o painel na tela), 'fechar' (para ocultar da tela) ou 'alternar'."
        )
    )
    async def controlar_painel_hud(self, painel: str, acao: str = "abrir") -> str:
        p_raw = (painel or "").lower().strip()
        a_raw = (acao or "abrir").lower().strip()

        # Suporte a Modo Orb (apenas o Orb flutuante 5x5cm translúcido)
        if any(w in p_raw for w in ["orb", "modo orb", "compacto", "dock", "mini", "flutuante", "recolher"]):
            acao_compact = True if not any(w in a_raw for w in ["fechar", "desativar", "expandir", "normal", "completo"]) else False
            if "alternar" in a_raw or "toggle" in a_raw:
                acao_compact = None
            payload_compact = {
                "type": "jarvis_hud_orb_mode",
                "orbMode": acao_compact,
                "compact": acao_compact,
            }
            try:
                if self.board_room and self.board_room.local_participant:
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload_compact, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.hud",
                    )
            except Exception as e:
                logger_memoria.error("evento=orb_hud erro=%s", e)
            status_txt = "ativado" if acao_compact else "desativado" if acao_compact is False else "alternado"
            return f"Modo Orb {status_txt}, chefe."

        if any(w in p_raw for w in ["expandir", "completo", "normal"]) and not any(w in p_raw for w in ["arquivo", "trader", "lembrete"]):
            payload_compact = {
                "type": "jarvis_hud_orb_mode",
                "orbMode": False,
                "compact": False,
            }
            try:
                if self.board_room and self.board_room.local_participant:
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload_compact, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.hud",
                    )
            except Exception as e:
                logger_memoria.error("evento=expand_hud erro=%s", e)
            return "Modo completo restaurado na tela, chefe."

        # Verifica primeiro se o comando é para abrir um caderno específico pelo nome (ex.: 'caderno do porto panorama', 'porto panorama')
        resolvido_nb = self._resolver_caderno_por_nome(p_raw)
        if resolvido_nb:
            nb_id, nb_nome = resolvido_nb
            self._active_notebook_id = nb_id
            self._workspace_panel_open = True
            payload_hud = {
                "type": "jarvis_hud_control",
                "panel": "files",
                "action": "open",
                "notebookId": nb_id,
                "notebookName": nb_nome,
            }
            payload_ws = {
                "type": "jarvis_switch_notebook",
                "notebookId": nb_id,
                "notebookName": nb_nome,
            }
            try:
                if self.board_room and self.board_room.local_participant:
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload_hud, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.hud",
                    )
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload_ws, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.workspace",
                    )
            except Exception as e:
                logger_memoria.error("evento=abrir_caderno_hud erro=%s", e)
            return f"Caderno '{nb_nome}' aberto na tela com sucesso."

        mapa = {
            "arquivos": ("files", "Central de Arquivos"),
            "arquivo": ("files", "Central de Arquivos"),
            "central": ("files", "Central de Arquivos"),
            "central de arquivos": ("files", "Central de Arquivos"),
            "files": ("files", "Central de Arquivos"),
            "notebook": ("files", "Central de Arquivos"),
            "notebooks": ("files", "Central de Arquivos"),
            "caderno": ("files", "Central de Arquivos"),
            "cadernos": ("files", "Central de Arquivos"),
            "lembretes": ("reminders", "Painel de Lembretes"),
            "lembrete": ("reminders", "Painel de Lembretes"),
            "reminders": ("reminders", "Painel de Lembretes"),
            "automacoes": ("automations", "Painel de Automações"),
            "automações": ("automations", "Painel de Automações"),
            "automacao": ("automations", "Painel de Automações"),
            "automação": ("automations", "Painel de Automações"),
            "automations": ("automations", "Painel de Automações"),
            "email": ("email", "Compositor de E-mails"),
            "emails": ("email", "Compositor de E-mails"),
            "e-mail": ("email", "Compositor de E-mails"),
            "e-mails": ("email", "Compositor de E-mails"),
            "sistema": ("system", "Painel do Sistema"),
            "system": ("system", "Painel do Sistema"),
            "dashboard": ("system", "Painel do Sistema"),
            "atividades": ("activity", "Painel de Atividades"),
            "atividade": ("activity", "Painel de Atividades"),
            "activity": ("activity", "Painel de Atividades"),
            "tarefas": ("activity", "Painel de Atividades"),
            "trader": ("trader", "Painel Trader"),
            "quant": ("trader", "Painel Trader"),
            "relogio": ("clock", "Relógio HUD"),
            "relógio": ("clock", "Relógio HUD"),
            "clock": ("clock", "Relógio HUD"),
            "neural": ("neural", "Rede Neural"),
            "atalhos": ("favorites", "Painel Atalhos"),
            "atalho": ("favorites", "Painel Atalhos"),
            "meus atalhos": ("favorites", "Painel Atalhos"),
            "barra de atalhos": ("favorites", "Painel Atalhos"),
            "favoritos": ("favorites", "Painel Atalhos"),
            "favorito": ("favorites", "Painel Atalhos"),
            "bookmarks": ("favorites", "Painel Atalhos"),
            "barra de favoritos": ("favorites", "Painel Atalhos"),
            "sites favoritos": ("favorites", "Painel Atalhos"),
            "favorites": ("favorites", "Painel Atalhos"),
            "todos": ("all", "Todos os painéis"),
            "tudo": ("all", "Todos os painéis"),
        }

        target_id = "files"
        nome_amigavel = "Central de Arquivos"
        encontrou = False
        for chave, (pid, nome) in mapa.items():
            if chave in p_raw:
                target_id = pid
                nome_amigavel = nome
                encontrou = True
                break

        if not encontrou:
            for chave, (pid, nome) in mapa.items():
                if chave == p_raw:
                    target_id = pid
                    nome_amigavel = nome
                    encontrou = True
                    break

        acao_cmd = "close" if any(w in a_raw for w in ["fechar", "ocultar", "esconder", "close"]) else "open"
        if "alternar" in a_raw or "toggle" in a_raw:
            acao_cmd = "toggle"

        # Se for abrir ou fechar o painel de arquivos, atualiza o estado interno
        if target_id == "files" and acao_cmd == "open":
            self._workspace_panel_open = True
        elif (target_id in ("files", "all") and acao_cmd == "close"):
            self._workspace_panel_open = False
            self._ultimo_texto_notebook_transcrito = ""
        elif target_id == "files" and acao_cmd == "toggle":
            self._workspace_panel_open = not self._workspace_panel_open
            if not self._workspace_panel_open:
                self._ultimo_texto_notebook_transcrito = ""

        payload = {
            "type": "jarvis_hud_control",
            "panel": target_id,
            "action": acao_cmd,
        }

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
        except Exception as e:
            logger_memoria.error("evento=controlar_painel_hud status=erro motivo=%s", e)
            return f"Não consegui exibir o painel na tela ({e})."

        status_str = "aberto na tela" if acao_cmd == "open" else "fechado" if acao_cmd == "close" else "alternado"
        return f"{nome_amigavel} {status_str} com sucesso."

    @function_tool(
        description=(
            "Organiza e reorganiza automaticamente o layout de todos os painéis visuais abertos no HUD para que NUNCA fiquem sobrepostos (um em cima do outro). "
            "Use sempre que o usuário pedir: 'organize os painéis', 'não deixa um em cima do outro', 'coloque os painéis lado a lado', "
            "'arrume a tela', 'distribua os painéis', 'organize em grade', 'desobstrua os painéis', 'afaste os painéis abertos'. "
            "`modo`: 'lado_a_lado' (distribui os painéis em colunas paralelas sem colisão), 'grade' (mosaico em 4 quadrantes), "
            "'desobstruir' (afasta painéis que colidem) ou 'padrao' (restaura posições limpas)."
        )
    )
    async def organizar_layout_paineis(self, modo: str = "lado_a_lado") -> str:
        m_raw = (modo or "lado_a_lado").lower().strip()
        modo_final = "side_by_side"
        if any(w in m_raw for w in ["grade", "grid", "mosaico", "quadrante", "quadro"]):
            modo_final = "grid"
        elif any(w in m_raw for w in ["desobstru", "afast", "separ", "unstack"]):
            modo_final = "unstack"
        elif any(w in m_raw for w in ["padrao", "padrão", "reset", "limp"]):
            modo_final = "reset"

        payload = {
            "type": "jarvis_hud_organize",
            "mode": modo_final,
        }

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
        except Exception as e:
            logger_memoria.error("evento=organizar_layout_paineis status=erro motivo=%s", e)
            return f"Não foi possível organizar o layout dos painéis no momento ({e})."

        desc_modo = {
            "side_by_side": "lado a lado em colunas paralelas sem sobreposição",
            "grid": "em grade e mosaico pelos quadrantes da tela",
            "unstack": "desobstruídos e afastados para evitar qualquer sobreposição",
            "reset": "restaurados para o layout padrão",
        }.get(modo_final, "organizados")
        return f"Pronto! Os painéis foram organizados {desc_modo}."

    @function_tool(
        description=(
            "Move, redimensiona e posiciona um painel visual específico em qualquer área da tela do HUD. "
            "Use sempre que o usuário pedir: 'coloque a central de arquivos na direita', 'joga o sistema pra esquerda', "
            "'centraliza a central', 'bota os lembretes no canto', 'deixa o painel menor/compacto', 'aumenta o painel do trader'. "
            "`painel`: nome do painel ('arquivos', 'lembretes', 'sistema', 'trader', 'automacoes', 'email', 'relogio'). "
            "`posicao`: onde posicionar ('esquerda', 'direita', 'centro', 'topo_esquerda', 'topo_direita', 'baixo_esquerda', 'baixo_direita'). "
            "`tamanho`: escala visual ('normal', 'compacto'/'pequeno', 'grande'/'ampliado')."
        )
    )
    async def posicionar_painel(self, painel: str, posicao: str = "esquerda", tamanho: str = "normal") -> str:
        p_raw = (painel or "").lower().strip()
        pos_raw = (posicao or "esquerda").lower().strip()
        tam_raw = (tamanho or "normal").lower().strip()

        mapa_paineis = {
            "arquivos": "files", "central": "files", "central de arquivos": "files", "files": "files",
            "lembretes": "reminders", "lembrete": "reminders", "reminders": "reminders",
            "sistema": "system", "system": "system", "dashboard": "system",
            "trader": "trader", "quant": "trader", "mercado": "trader",
            "automacoes": "automations", "automações": "automations", "automations": "automations",
            "email": "email", "emails": "email",
            "relogio": "clock", "relógio": "clock", "clock": "clock",
        }

        target_id = "files"
        nome_amigavel = "Central de Arquivos"
        for k, v in mapa_paineis.items():
            if k in p_raw:
                target_id = v
                nome_amigavel = k.capitalize()
                break

        payload = {
            "type": "jarvis_hud_organize",
            "panel": target_id,
            "position": pos_raw,
            "size": tam_raw,
        }

        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
        except Exception as e:
            logger_memoria.error("evento=posicionar_painel status=erro motivo=%s", e)
            return f"Não foi possível mover o painel no momento ({e})."

        return f"{nome_amigavel} posicionado na {pos_raw} com tamanho {tam_raw}."

    @function_tool(
        description=(
            "Restaura e abre imediatamente as 3 janelas iniciais clássicas do Jarvis (Painel do Sistema, Relógio e Atividades), "
            "fechando todos os outros painéis extras e resetando as posições e escalas para o padrão de fábrica sem sobreposição. "
            "Use sempre que o usuário pedir: 'abra as 3 janelas iniciais', 'volte para as 3 janelas iniciais', "
            "'resete o layout', 'restaure as posições dos painéis', 'volte os painéis para o padrão', 'limpe a tela e deixe só o padrão'."
        )
    )
    async def resetar_layout_paineis(self) -> str:
        self._workspace_panel_open = False
        payload = {
            "type": "jarvis_hud_reset_layout",
            "action": "reset",
        }
        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
        except Exception as e:
            logger_memoria.error("evento=resetar_layout_paineis status=erro motivo=%s", e)
            return f"Não foi possível resetar o layout no momento ({e})."

        return "As 3 janelas iniciais (Painel do Sistema, Relógio e Atividades) foram restauradas na tela e o layout foi redefinido para o padrão."

    @function_tool(
        description=(
            "Lista todos os arquivos e cadernos presentes na Central de Arquivos / Notebooks do Jarvis na tela. "
            "Use quando o usuário perguntar: 'quais arquivos estão no painel?', 'o que tem na central de arquivos?', "
            "'quais documentos eu enviei?', 'quais cadernos tenho?'."
        )
    )
    async def listar_arquivos_workspace(self) -> str:
        pasta_ws = os.path.join(_backend_dir, "workspace_arquivos")
        if not os.path.isdir(pasta_ws):
            return "A Central de Arquivos está vazia no momento."

        itens = []
        pasta_nb = os.path.join(pasta_ws, "notebooks")
        if os.path.isdir(pasta_nb):
            for nb in os.listdir(pasta_nb):
                nb_dir = os.path.join(pasta_nb, nb)
                if os.path.isdir(nb_dir):
                    # Tenta ler nome amigável do notebook.json
                    nome_nb = nb.capitalize()
                    meta_path = os.path.join(nb_dir, "notebook.json")
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as fm:
                                meta = json.load(fm)
                                nome_nb = meta.get("name") or nome_nb
                        except Exception:
                            pass
                    for f in os.listdir(nb_dir):
                        if f in ("notebook.json", "chat_history.json"):
                            continue
                        caminho = os.path.join(nb_dir, f)
                        if os.path.isfile(caminho):
                            nome_limpo = re.sub(r"^[a-zA-Z0-9]{8}-", "", f)
                            tamanho_kb = round(os.path.getsize(caminho) / 1024, 1)
                            itens.append(f"- {nome_limpo} ({tamanho_kb} KB) [Caderno: {nome_nb}]")

        # Verifica também arquivos na raiz (legados)
        for f in os.listdir(pasta_ws):
            caminho = os.path.join(pasta_ws, f)
            if os.path.isfile(caminho):
                nome_limpo = re.sub(r"^[a-zA-Z0-9]{8}-", "", f)
                tamanho_kb = round(os.path.getsize(caminho) / 1024, 1)
                itens.append(f"- {nome_limpo} ({tamanho_kb} KB)")

        if not itens:
            return "Nenhum arquivo na Central de Arquivos. Você pode arrastar ou colar (Ctrl+V) qualquer documento no painel."

        return f"Arquivos na Central ({len(itens)}):\n" + "\n".join(itens)

    @staticmethod
    def _buscar_web_snippets(query: str, max_resultados: int = 3) -> list[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        snippets: list[str] = []
        # 1. Provedor primário de alta velocidade: Bing Search
        try:
            r = httpx.get("https://www.bing.com/search", params={"q": query}, headers=headers, timeout=3.8)
            if r.status_code == 200:
                m = re.findall(r'<p class="b_lineclamp[^>]*>(.*?)</p>', r.text, re.DOTALL)
                if not m:
                    m = re.findall(r'<div class="b_caption"><p>(.*?)</p>', r.text, re.DOTALL)
                for item in m:
                    clean = re.sub(r'<[^>]+>', '', item)
                    clean = html.unescape(clean).strip()
                    if clean and len(clean) > 25 and not clean.startswith("Usamos cookies"):
                        snippets.append(clean)
                    if len(snippets) >= max_resultados:
                        return snippets
        except Exception:
            pass

        # 2. Provedor secundário: DuckDuckGo fallback
        if not snippets:
            try:
                r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=headers, timeout=3.0)
                if r.status_code == 200:
                    matches = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', r.text, re.DOTALL)
                    for m in matches:
                        clean = re.sub(r'<[^>]+>', '', m)
                        clean = html.unescape(clean).strip()
                        if clean and len(clean) > 20:
                            snippets.append(clean)
                        if len(snippets) >= max_resultados:
                            return snippets
            except Exception:
                pass

        return snippets

    @function_tool(
        description=(
            "Lê, analisa, resume, transcreve ou responde perguntas sobre arquivos "
            "(PDFs, imagens, contratos, boletos, planilhas Excel/CSV, fotos, códigos, áudios ou textos) "
            "que o usuário colocou no Painel / Central de Arquivos do Jarvis. "
            "IMPORTANTE: Se o usuário pedir para analisar 'os arquivos', 'o conjunto', 'com base neles' ou se não especificar um arquivo único, "
            "esta ferramenta analisa TODOS os arquivos do painel juntos, cruzando as informações entre eles. "
            "Se a pergunta do usuário depender de dados externos (índices econômicos como IGP-M/IPCA, cotações, leis, etc.), "
            "ela pesquisa automaticamente na web em tempo real e calcula os valores com precisão. "
            "Se o usuário pedir para 'copiar' ou 'extrair texto', o conteúdo também é copiado para a área de transferência do Windows. "
            "`nome_arquivo`: nome específico do arquivo (se vazio, ou se for 'todos'/'conjunto'/'arquivos', analisa todos os arquivos do painel juntos). "
            "`comando`: a instrução ou pergunta do usuário (ex.: 'qual o valor e índice do reajuste?', 'resumir conjunto', 'extrair dados')."
        )
    )
    async def analisar_ou_processar_arquivo_workspace(
        self,
        nome_arquivo: str = "",
        comando: str = "analisar",
    ) -> str:
        pasta_ws = os.path.join(_backend_dir, "workspace_arquivos")
        if not os.path.isdir(pasta_ws):
            return "A Central de Arquivos está vazia. Arraste ou cole (Ctrl+V) arquivos no painel primeiro."

        arquivos_candidatos = []
        pasta_nb = os.path.join(pasta_ws, "notebooks")
        if os.path.isdir(pasta_nb):
            for nb in os.listdir(pasta_nb):
                nb_dir = os.path.join(pasta_nb, nb)
                if os.path.isdir(nb_dir):
                    for f in os.listdir(nb_dir):
                        if f in ("notebook.json", "chat_history.json"):
                            continue
                        caminho = os.path.join(nb_dir, f)
                        if os.path.isfile(caminho):
                            arquivos_candidatos.append((os.path.getmtime(caminho), f, caminho))

        for f in os.listdir(pasta_ws):
            caminho = os.path.join(pasta_ws, f)
            if os.path.isfile(caminho):
                arquivos_candidatos.append((os.path.getmtime(caminho), f, caminho))

        if not arquivos_candidatos:
            return "Nenhum arquivo encontrado na Central de Arquivos. Arraste ou cole arquivos no painel."

        # Ordena pelo mais recente
        arquivos_candidatos.sort(key=lambda x: x[0], reverse=True)

        termo = (nome_arquivo or "").strip().lower()
        analisar_todos = termo in ("", "todos", "tudo", "conjunto", "arquivos", "documentos", "painel", "todos os arquivos")

        alvos = []
        if not analisar_todos:
            # Tenta encontrar o arquivo específico mencionado
            for _, nome_salvo, cam in arquivos_candidatos:
                if termo in nome_salvo.lower():
                    alvos.append((nome_salvo, cam))
                    break

        if not alvos:
            # Analisa todos os arquivos do painel em conjunto!
            alvos = [(nome_salvo, cam) for _, nome_salvo, cam in arquivos_candidatos]

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)

        cmd_lower = (comando or "analisar").lower()
        quer_copiar = any(w in cmd_lower for w in ["copiar", "copia", "clipboard", "área de transferência", "transcrever", "ocr"])

        contents_gemini = []
        nomes_processados = []

        for i, (nome_salvo, caminho_arquivo) in enumerate(alvos):
            match = re.match(r"^[a-zA-Z0-9]{8}-(.+)$", nome_salvo)
            nome_exib = match.group(1) if match else nome_salvo
            nomes_processados.append(nome_exib)

            ext = os.path.splitext(caminho_arquivo)[1].lower().replace(".", "")
            contents_gemini.append(f"\n=== DOCUMENTO {i + 1} de {len(alvos)}: '{nome_exib}' ===\n")

            is_text = ext in ["txt", "md", "csv", "json", "py", "js", "ts", "tsx", "jsx", "html", "css", "sql", "sh", "bat", "log", "xml", "yaml", "yml"]
            is_img = ext in ["png", "jpg", "jpeg", "webp", "bmp", "gif"]
            is_pdf = ext == "pdf"

            if is_text:
                try:
                    with open(caminho_arquivo, "r", encoding="utf-8", errors="replace") as f_in:
                        txt_conteudo = f_in.read(50000)
                    contents_gemini.append(f"```{ext}\n{txt_conteudo}\n```")
                except Exception:
                    pass
            elif is_img:
                try:
                    with open(caminho_arquivo, "rb") as f_img:
                        img_bytes = f_img.read()
                    mime_img = "image/png" if ext == "png" else ("image/webp" if ext == "webp" else "image/jpeg")
                    contents_gemini.append(genai_types.Part.from_bytes(data=img_bytes, mime_type=mime_img))
                except Exception:
                    pass
            elif is_pdf:
                # Tenta pypdf primeiro para texto rápido
                extraiu_texto = False
                try:
                    if pypdf:
                        reader = pypdf.PdfReader(caminho_arquivo)
                        paginas = [p.extract_text() or "" for p in reader.pages[:10]]
                        txt_pdf = "\n".join(paginas).strip()
                        if txt_pdf:
                            contents_gemini.append(f"Conteúdo do PDF:\n{txt_pdf[:40000]}")
                            extraiu_texto = True
                except Exception:
                    pass
                if not extraiu_texto:
                    try:
                        with open(caminho_arquivo, "rb") as f_pdf:
                            pdf_bytes = f_pdf.read()
                        contents_gemini.append(genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
                    except Exception:
                        pass

        # Busca Web inteligente sob demanda se a instrução exigir dados externos
        precisa_web = any(w in cmd_lower for w in [
            "reajuste", "aluguel", "indice", "índice", "igp", "ipca", "inpc", "selic", "cdi",
            "dolar", "dólar", "cotacao", "cotação", "lei", "artigo", "codigo civil", "código civil",
            "cnpj", "empresa", "google", "internet", "pesquis", "procur", "busc", "porcentagem", "taxa", "atual"
        ])
        snippets_web = []
        if precisa_web:
            termo_web = (
                "IGP-M IPCA acumulado 12 meses FGV hoje reajuste aluguel"
                if any(w in cmd_lower for w in ["reajuste", "aluguel", "igp", "ipca"])
                else comando[:70]
            )
            snippets_web = await asyncio.to_thread(self._buscar_web_snippets, termo_web, 3)

        if snippets_web:
            bloco_web = (
                "\n=== DADOS REAIS PESQUISADOS NA WEB EM TEMPO REAL (GOOGLE/DUCKDUCKGO) ===\n"
                + "\n".join(f"{idx+1}. {s}" for idx, s in enumerate(snippets_web))
                + "\n========================================================================\n"
            )
            contents_gemini.append(bloco_web)

        # Identifica especialista do caderno ativo
        nb_ativo_id = self._active_notebook_id or "geral"
        pasta_nb_ativo = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", nb_ativo_id)
        nb_meta_expert = "jarvis"
        nb_meta_nome = nb_ativo_id

        if os.path.isdir(pasta_nb_ativo):
            meta_json_path = os.path.join(pasta_nb_ativo, "notebook.json")
            if os.path.isfile(meta_json_path):
                try:
                    with open(meta_json_path, "r", encoding="utf-8") as f_meta:
                        meta_dict = json.load(f_meta)
                        nb_meta_expert = meta_dict.get("expert") or "jarvis"
                        nb_meta_nome = meta_dict.get("name") or nb_ativo_id
                except Exception:
                    pass

        # Se não tiver especialista explícito mas o nome for de condomínio / imobiliário, ativa Parecer Duplo por padrão
        if nb_meta_expert == "jarvis":
            nome_low = (nb_meta_nome + " " + nb_ativo_id).lower()
            if any(w in nome_low for w in ["porto", "panorama", "condominio", "condomínio", "edificio", "edifício", "predio", "prédio"]):
                nb_meta_expert = "parecer_duplo"
            elif any(w in nome_low for w in ["compra", "compras", "shopping", "shopper", "produtos", "ofertas", "precos", "preços"]):
                nb_meta_expert = "especialista_compras"

        diretrizes_especialistas = {
            "parecer_duplo": (
                "ATUAÇÃO OBRIGATÓRIA COMO COMITÊ CONDOMINIAL DUPLO:\n"
                "1) ADVOGADO ESPECIALISTA EM DIREITO CONDOMINIAL E IMOBILIÁRIO (Doutor em Direito, 20+ anos de experiência).\n"
                "2) SÍNDICO PROFISSIONAL SÊNIOR & GESTOR PREDIAL CERTIFICADO.\n\n"
                "ESTRUTURE SUA RESPOSTA EM DOIS BLOCOS CLAROS:\n"
                "### 🏛️ 1. PARECER JURÍDICO (Dr. Advogado Condominial)\n"
                "- Fundamentação legal: Código Civil (Arts. 1.331 a 1.358-U), Lei 4.591/64, Lei 8.245/91 e jurisprudência do STJ.\n"
                "- Validade de convocações, assembleias, quóruns exigidos e convenção condominial.\n"
                "- Responsabilidade civil/criminal do síndico e administradora e classificação de riscos.\n\n"
                "### 🛠️ 2. DIRETRIZ OPERACIONAL & PLANO DE AÇÃO (Síndico Profissional)\n"
                "- Normas técnicas ABNT NBR 5674 (Manutenção) e NBR 16280 (Reformas / ART), AVCB e seguro predial.\n"
                "- Finanças, fluxo de caixa, fundo de reserva, rateios e combate à inadimplência.\n"
                "- Mediação de conflitos e checklist executivo de medidas imediatas."
            ),
            "advogado_condominial": (
                "ATUAÇÃO OBRIGATÓRIA COMO ADVOGADO ESPECIALISTA EM DIREITO CONDOMINIAL E IMOBILIÁRIO:\n"
                "- Analise cada cláusula, ata, boleto ou notificação com rigor jurídico absoluto.\n"
                "- Fundamente no Código Civil (Arts. 1.331-1.358), Lei 4.591/64, jurisprudência do STJ e regimento interno.\n"
                "- Identifique nulidades, quóruns e formule minutas de notificações e salvaguardas."
            ),
            "sindico_profissional": (
                "ATUAÇÃO OBRIGATÓRIA COMO SÍNDICO PROFISSIONAL E GESTOR PREDIAL:\n"
                "- Foco em engenharia de manutenção (ABNT NBR 5674), controle de reformas (NBR 16280), AVCB e instalações.\n"
                "- Gestão de prestadores de serviços, redução de custos e saúde do fundo de reserva.\n"
                "- Aplicação imparcial do regimento interno e plano de ação imediato."
            ),
            "cfo_financeiro": (
                "ATUAÇÃO OBRIGATÓRIA COMO CFO E AUDITOR CONTÁBIL FORENSE:\n"
                "- Auditoria de receitas, despesas ordinárias vs extraordinárias e desvios de finalidade.\n"
                "- Verificação de retenções tributárias (INSS, ISS, PIS/COFINS/CSLL) e conciliação bancária.\n"
                "- Projeção de fluxo de caixa e diagnóstico de saúde financeira."
            ),
            "medico": (
                "ATUAÇÃO OBRIGATÓRIA COMO MÉDICO PERITO E ESPECIALISTA EM MEDICINA BASEADA EM EVIDÊNCIAS:\n"
                "- Análise aprofundada de laudos, exames laboratoriais e literatura médica de referência.\n"
                "- Mecanismos fisiopatológicos, diagnósticos diferenciais e alertas clínicos."
            ),
            "dev_senior": (
                "ATUAÇÃO OBRIGATÓRIA COMO ENGENHEIRO DE SOFTWARE SÊNIOR E ARQUITETO DE SISTEMAS:\n"
                "- Code review minucioso, princípios SOLID, Clean Code e complexidade O(n).\n"
                "- Diagnóstico cirúrgico de bugs e soluções prontas para produção."
            ),
            "especialista_compras": (
                "ATUAÇÃO OBRIGATÓRIA COMO ESPECIALISTA CONSULTOR DE COMPRAS & SMART SHOPPING (PERSONAL SHOPPER):\n"
                "- Comparação inteligente de preços, prazos e fretes entre opções próximas/locais (retirada rápida) vs marketplaces online (Mercado Livre, Amazon, Shopee, Magalu).\n"
                "- Auditoria criteriosa de vendedores: reputação (MercadoLíder, Platinum, Loja Oficial, nota 4.8+), quantidade total de produtos vendidos, garantia e histórico de avaliações.\n"
                "- Detecção de fraudes, anúncios suspeitos ou réplicas, sugerindo sempre a melhor relação custo-benefício e segurança para a compra."
            ),
            "jarvis": (
                "ATUAÇÃO COMO JARVIS EXECUTIVO:\n"
                "- Síntese de alto nível, raciocínio lógico, foco em resultados e clareza absoluta."
            ),
        }
        diretriz_esp = diretrizes_especialistas.get(nb_meta_expert, diretrizes_especialistas["jarvis"])

        # Monta a diretriz final da IA
        is_multi = len(nomes_processados) > 1
        aviso_web = (
            "- ATENÇÃO: Utilize os dados da pesquisa web em tempo real fornecidos acima (ex.: porcentagem acumulada do IGP-M ou IPCA) para aplicar sobre o valor do boleto e calcular o valor final exato reajustado em reais!\n"
            if snippets_web
            else ""
        )

        if is_multi:
            diretriz = (
                f"Você está operando no caderno '{nb_meta_nome}'.\n"
                f"{diretriz_esp}\n\n"
                f"O usuário forneceu {len(nomes_processados)} documentos para análise CONJUNTA E CRUZADA ({', '.join(nomes_processados)}).\n"
                f"Instrução do usuário: '{comando}'.\n\n"
                "DIRETRIZ DE ANÁLISE CRUZADA:\n"
                "- Analise TODOS os documentos em conjunto, relacionando e cruzando as informações entre eles.\n"
                "- Se um documento depender de dados presentes no outro (ex.: boleto de aluguel indicando data de reajuste e contrato/foto com o índice, percentual ou cláusula de cálculo), conecte os dados e forneça a resposta final e conclusiva com os cálculos prontos.\n"
                f"{aviso_web}"
                "- Responda com clareza e autoridade técnica no papel do especialista ativo, citando de qual documento veio cada dado."
            )
        else:
            doc_single = nomes_processados[0]
            diretriz = (
                f"Você está operando no caderno '{nb_meta_nome}'.\n"
                f"{diretriz_esp}\n\n"
                f"O usuário forneceu o documento '{doc_single}'.\n"
                f"Instrução do usuário: '{comando}'.\n"
                f"{aviso_web}"
                "Responda com profundidade técnica e autoridade no papel do especialista ativo, em português do Brasil."
            )

        # Memória Conversacional do Caderno (estilo NotebookLM)
        chat_nb_path = os.path.join(_backend_dir, "workspace_arquivos", "notebooks", self._active_notebook_id or "geral", "chat_history.json")
        if os.path.isfile(chat_nb_path):
            try:
                with open(chat_nb_path, "r", encoding="utf-8") as fch:
                    historico_nb = json.load(fch)
                    if isinstance(historico_nb, list) and historico_nb:
                        ultimas = historico_nb[-4:]
                        bloco_hist = "\n=== HISTÓRICO RECENTE DA CONVERSA NESTE CADERNO (MEMÓRIA CONTÍNUA) ===\n" + "\n\n".join(
                            f"{'Usuário' if m.get('role') == 'user' else 'JARVIS'}: {m.get('content')}"
                            for m in ultimas
                        ) + "\n=======================================================================\n"
                        contents_gemini.append(bloco_hist)
            except Exception:
                pass

        contents_gemini.append(diretriz)

        try:
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.5-flash-lite",
                contents=contents_gemini,
            )
            resultado_texto = resp.text.strip() if resp and resp.text else ""
        except Exception as e_gen:
            logger_memoria.warning("evento=analise_workspace_gemini status=erro motivo=%s", e_gen)
            resultado_texto = f"Não consegui processar a análise no momento ({e_gen})."

        if not resultado_texto:
            return f"Não foi possível extrair dados dos arquivos do painel."

        # Salva o turno na memória persistente do caderno e atualiza o painel
        asyncio.create_task(
            self._adicionar_mensagem_chat_notebook(
                role="user",
                content=comando or "Análise das fontes",
                sources=nomes_processados,
            )
        )
        asyncio.create_task(
            self._adicionar_mensagem_chat_notebook(
                role="assistant",
                content=resultado_texto,
                sources=nomes_processados,
                buscou_web=bool(snippets_web),
                expert=nb_meta_expert,
            )
        )

        if quer_copiar:
            await asyncio.to_thread(self._copiar_para_clipboard, resultado_texto)
            return (
                f"Analisei os arquivos ({', '.join(nomes_processados)}) e copiei a resposta completa para a sua área de transferência (Ctrl+V pronto).\n"
                f"Resumo: {resultado_texto[:300]}..."
            )

        return f"Análise de {len(nomes_processados)} documento(s) ({', '.join(nomes_processados)}):\n{resultado_texto}"

    @staticmethod
    def _enviar_atalho_teclado(*vks: int, repeticoes: int = 1) -> None:
        user32 = ctypes.windll.user32
        for _ in range(max(1, int(repeticoes))):
            for vk in vks:
                user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                user32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.05)

    @function_tool(
        description=(
            "Controla o ZOOM da tela: aumenta (zoom in), diminui (zoom out), restaura ao normal ou define um valor exato. "
            "Use quando o usuário pedir 'aumenta o zoom', 'dá zoom in', 'zoom out', 'diminui a tela', 'aumenta as letras', "
            "'volta o zoom ao normal', 'zoom 150%', 'não consigo ler, amplia'. "
            "`acao`: 'in' (aumentar), 'out' (diminuir), 'reset' (normal 100%) ou 'set' (valor exato em `percentual`, ex.: 150). "
            "`passos`: quantos níveis aumentar/diminuir (padrão 1; use 3 para 'bastante'). "
            "`alvo`: 'atual' (aba/site do navegador em uso, nunca a tela do próprio Jarvis), nome de um site/aba "
            "(ex.: 'youtube', 'gmail'), 'programa' (janela em primeiro plano, qualquer aplicativo) "
            "ou 'windows'/'lupa' (ampliação do Windows sobre a tela inteira)."
        )
    )
    async def controlar_zoom_tela(
        self,
        acao: str = "in",
        passos: int = 1,
        percentual: int = 100,
        alvo: str = "atual",
    ) -> str:
        acao_n = (acao or "in").strip().lower()
        mapa = {
            "in": "in", "aumentar": "in", "aumenta": "in", "mais": "in", "ampliar": "in", "+": "in",
            "out": "out", "diminuir": "out", "diminui": "out", "menos": "out", "reduzir": "out", "-": "out",
            "reset": "reset", "normal": "reset", "restaurar": "reset", "resetar": "reset", "100": "reset",
            "set": "set", "definir": "set", "exato": "set",
        }
        acao_n = mapa.get(acao_n, "")
        if not acao_n:
            return "Aviso: ação de zoom inválida. Use 'in', 'out', 'reset' ou 'set'."
        passos = max(1, min(10, int(passos or 1)))
        alvo_n = (alvo or "atual").strip().lower()
        print(f"[AÇÃO] Zoom '{acao_n}' x{passos} alvo='{alvo_n}' percentual={percentual}")

        VK_CONTROL, VK_LWIN, VK_ESCAPE = 0x11, 0x5B, 0x1B
        VK_PLUS, VK_MINUS, VK_0 = 0xBB, 0xBD, 0x30

        # 1. Lupa do Windows (tela inteira)
        if alvo_n in ("windows", "lupa", "tela inteira", "tela toda", "monitor"):
            if acao_n == "in":
                await asyncio.to_thread(self._enviar_atalho_teclado, VK_LWIN, VK_PLUS, repeticoes=passos)
                return "Ampliei a tela com a Lupa do Windows."
            if acao_n == "out":
                await asyncio.to_thread(self._enviar_atalho_teclado, VK_LWIN, VK_MINUS, repeticoes=passos)
                return "Reduzi a ampliação da Lupa do Windows."
            await asyncio.to_thread(self._enviar_atalho_teclado, VK_LWIN, VK_ESCAPE)
            return "Fechei a Lupa do Windows e voltei a tela ao normal."

        # 2. Aba/site do navegador via extensão (preciso, não afeta a tela do Jarvis)
        if alvo_n not in ("programa", "aplicativo", "janela ativa", "app"):
            try:
                res = await asyncio.to_thread(
                    jarvis_chrome.zoom_tab, acao_n, alvo_n, (percentual or 100) / 100.0, passos
                )
                if res.get("ok"):
                    return f"Zoom ajustado para {round(float(res.get('zoom', 1)) * 100)}% na aba."
                erro_ext = str(res.get("error") or "")
                if erro_ext == "sem_aba":
                    return (
                        "Aviso: não achei nenhuma aba de site aberta para dar zoom (a tela do Jarvis é protegida). "
                        "Abra o site e peça de novo."
                    )
                if acao_n == "set":
                    return (
                        "Aviso: a extensão do Chrome não respondeu a este comando, então não consigo definir o zoom exato. "
                        "Provável causa: a extensão não foi recarregada em chrome://extensions. "
                        "NÃO tente chegar ao valor aos poucos; peça ao usuário para recarregar a extensão."
                    )
            except Exception:
                pass

        # 3. Fallback: atalho Ctrl +/-/0 na janela em primeiro plano (aplicativos, Word, Explorer, Chrome sem extensão)
        if acao_n == "in":
            await asyncio.to_thread(self._enviar_atalho_teclado, VK_CONTROL, VK_PLUS, repeticoes=passos)
            return "Zoom aumentado na janela em primeiro plano."
        if acao_n == "out":
            await asyncio.to_thread(self._enviar_atalho_teclado, VK_CONTROL, VK_MINUS, repeticoes=passos)
            return "Zoom diminuído na janela em primeiro plano."
        if acao_n == "reset":
            await asyncio.to_thread(self._enviar_atalho_teclado, VK_CONTROL, VK_0)
            return "Zoom restaurado ao normal na janela em primeiro plano."
        return "Aviso: não consegui definir um zoom exato sem a extensão do Chrome ativa. Posso aumentar ou diminuir por passos."

    @function_tool(
        description=(
            "Controla a navegação e a visualização da tela/aba do navegador. "
            "`acao`: 'voltar' (página anterior), 'avancar', 'recarregar' (atualizar), 'proxima_aba', 'aba_anterior', "
            "'tela_cheia' (liga/desliga tela cheia), 'topo' (vai ao início da página), 'fim' (vai ao final da página). "
            "Use para 'volta a página', 'atualiza', 'próxima aba', 'tela cheia', 'vai pro topo', 'vai até o final'. "
            "`alvo`: 'atual' (padrão) ou nome do site/aba."
        )
    )
    async def controlar_navegacao_tela(self, acao: str, alvo: str = "atual") -> str:
        a = (acao or "").strip().lower().replace(" ", "_").replace("á", "a").replace("ç", "c").replace("ó", "o")
        sinonimos = {
            "voltar": "voltar", "back": "voltar",
            "avancar": "avancar", "forward": "avancar",
            "recarregar": "recarregar", "atualizar": "recarregar", "refresh": "recarregar",
            "proxima_aba": "proxima_aba", "proxima": "proxima_aba",
            "aba_anterior": "aba_anterior", "anterior": "aba_anterior",
            "tela_cheia": "tela_cheia", "fullscreen": "tela_cheia",
            "topo": "topo", "inicio": "topo", "fim": "fim", "final": "fim", "rodape": "fim",
        }
        a = sinonimos.get(a, "")
        if not a:
            return "Aviso: ação inválida. Use voltar, avancar, recarregar, proxima_aba, aba_anterior, tela_cheia, topo ou fim."
        print(f"[AÇÃO] Navegação de tela '{a}' alvo='{alvo}'")
        try:
            res = await asyncio.to_thread(jarvis_chrome.tab_control, a, (alvo or "atual").strip().lower())
            if res.get("ok"):
                return f"Pronto: '{a.replace('_', ' ')}' executado."
        except Exception:
            pass

        # Fallback por teclado na janela em primeiro plano
        VK_MENU, VK_LEFT, VK_RIGHT, VK_F5, VK_F11 = 0x12, 0x25, 0x27, 0x74, 0x7A
        VK_CONTROL, VK_TAB, VK_SHIFT, VK_HOME, VK_END = 0x11, 0x09, 0x10, 0x24, 0x23
        atalhos = {
            "voltar": (VK_MENU, VK_LEFT),
            "avancar": (VK_MENU, VK_RIGHT),
            "recarregar": (VK_F5,),
            "proxima_aba": (VK_CONTROL, VK_TAB),
            "aba_anterior": (VK_CONTROL, VK_SHIFT, VK_TAB),
            "tela_cheia": (VK_F11,),
            "topo": (VK_CONTROL, VK_HOME),
            "fim": (VK_CONTROL, VK_END),
        }
        await asyncio.to_thread(self._enviar_atalho_teclado, *atalhos[a])
        return f"Pronto: '{a.replace('_', ' ')}' executado."

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

    @function_tool(
        description=(
            "Lista e busca eventos do Google Calendar. Suporta intervalo de datas "
            "(início e fim em formato ISO 8601 ou YYYY-MM-DD), busca por texto livre ('termo_busca'), "
            "e escolha da agenda ('id_agenda', padrão 'primary'). Sem início informado, lista os próximos eventos futuros."
        )
    )
    async def listar_eventos_google_calendar(
        self,
        inicio_iso: str = "",
        fim_iso: str = "",
        termo_busca: str = "",
        id_agenda: str = "primary",
        max_resultados: int = 50,
    ):
        return await self._executar_google(
            google_workspace.list_calendar_events,
            time_min=inicio_iso,
            time_max=fim_iso,
            calendar_id=id_agenda,
            query=termo_busca,
            max_results=max_resultados,
        )

    @function_tool(
        description="Obtém os detalhes completos de um evento específico do Google Calendar pelo ID (horários, descrição, localização, cor, participantes, link do Meet, recorrência)."
    )
    async def obter_detalhes_evento_google_calendar(
        self,
        id_evento: str,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.get_calendar_event,
            id_evento,
            id_agenda,
        )

    @function_tool(
        description=(
            "Cria um evento no Google Calendar com controle total: título, início (ISO 8601 ou YYYY-MM-DD), "
            "fim (opcional, assume 1 hora por padrão se omitido), descrição, localização, cor do evento "
            "(ex: 'Lavanda'/'azul claro', 'Sálvia'/'verde claro', 'Uva'/'roxo', 'Flamingo'/'rosa', 'Banana'/'amarelo', "
            "'Tangerina'/'laranja', 'Pavão'/'ciano', 'Grafite'/'cinza', 'Mirtilo'/'azul escuro', 'Manjericão'/'verde escuro', "
            "'Tomate'/'vermelho', ou IDs 1..11), participantes (e-mails separados por vírgula), "
            "link do Google Meet automático ('adicionar_google_meet=True'), evento de dia inteiro ('dia_inteiro=True'), "
            "recorrência ('diario', 'semanal', 'mensal') e lembretes em minutos."
        )
    )
    async def criar_evento_google_calendar(
        self,
        titulo: str,
        inicio_iso: str,
        fim_iso: str = "",
        descricao: str = "",
        localizacao: str = "",
        cor: str = "",
        participantes: str = "",
        adicionar_google_meet: bool = False,
        dia_inteiro: bool = False,
        recorrencia: str = "",
        lembrete_minutos: int = 0,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.create_calendar_event,
            summary=titulo,
            start_iso=inicio_iso,
            end_iso=fim_iso,
            description=descricao,
            location=localizacao,
            color=cor,
            attendees=participantes,
            create_meet_link=adicionar_google_meet,
            all_day=dia_inteiro,
            recurrence=recorrencia,
            reminder_minutes=lembrete_minutos,
            calendar_id=id_agenda,
        )

    @function_tool(
        description=(
            "Cria um evento no Google Calendar rapidamente a partir de linguagem natural livre. "
            "Exemplo: 'Almoço amanhã às 12h com a equipe no Centro' ou 'Consulta médica quinta às 14h'."
        )
    )
    async def criar_evento_rapido_google_calendar(
        self,
        texto: str,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.quick_add_calendar_event,
            text=texto,
            calendar_id=id_agenda,
        )

    @function_tool(
        description=(
            "Edita qualquer campo de um evento do Google Calendar identificado pelo ID: "
            "título, início, fim, descrição, localização, cor (nome da cor ou ID 1..11), "
            "e participantes. Campos não preenchidos permanecem inalterados."
        )
    )
    async def editar_evento_google_calendar(
        self,
        id_evento: str,
        titulo: str = "",
        descricao: str = "",
        inicio_iso: str = "",
        fim_iso: str = "",
        localizacao: str = "",
        cor: str = "",
        participantes: str = "",
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.update_calendar_event,
            event_id=id_evento,
            summary=titulo,
            description=descricao,
            start_iso=inicio_iso,
            end_iso=fim_iso,
            location=localizacao,
            color=cor,
            attendees=participantes,
            calendar_id=id_agenda,
        )

    @function_tool(
        description=(
            "Altera especificamente a cor de um evento do Google Calendar. "
            "Cores disponíveis: Lavanda (1 - azul claro), Sálvia (2 - verde claro), "
            "Uva (3 - roxo), Flamingo (4 - rosa), Banana (5 - amarelo), Tangerina (6 - laranja), "
            "Pavão (7 - ciano), Grafite (8 - cinza), Mirtilo (9 - azul escuro), "
            "Manjericão (10 - verde escuro), Tomate (11 - vermelho)."
        )
    )
    async def alterar_cor_evento_google_calendar(
        self,
        id_evento: str,
        cor: str,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.change_event_color,
            event_id=id_evento,
            color=cor,
            calendar_id=id_agenda,
        )

    @function_tool(
        description=(
            "Altera a cor de uma agenda/calendário inteira no Google Calendar (na barra lateral de calendários). "
            "Aceita 'primary' (agenda principal) ou o ID da agenda secundária, e o nome da cor "
            "(ex: 'azul', 'verde', 'vermelho', 'laranja', 'roxo', 'amarelo', 'pavao', 'salvia', 'mirtilo', 'cacau', etc.), "
            "ID da paleta (1 a 24) ou código hexadecimal customizado ('#336699')."
        )
    )
    async def alterar_cor_agenda_google_calendar(
        self,
        id_agenda: str = "primary",
        cor: str = "",
        cor_fundo_hex: str = "",
        cor_texto_hex: str = "",
    ):
        return await self._executar_google(
            google_workspace.update_calendar_color,
            calendar_id=id_agenda,
            color=cor,
            background_color_hex=cor_fundo_hex,
            foreground_color_hex=cor_texto_hex,
        )

    @function_tool(
        description="Move um evento existente de uma agenda para outra no Google Calendar."
    )
    async def mover_evento_google_calendar(
        self,
        id_evento: str,
        id_agenda_destino: str,
        id_agenda_origem: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.move_calendar_event,
            event_id=id_evento,
            destination_calendar_id=id_agenda_destino,
            source_calendar_id=id_agenda_origem,
        )

    @function_tool(
        description="Responde a um convite de evento no Google Calendar. Opções de resposta: 'aceitar', 'recusar' ou 'talvez'."
    )
    async def responder_convite_google_calendar(
        self,
        id_evento: str,
        resposta: str,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.respond_calendar_event,
            event_id=id_evento,
            response_status=resposta,
            calendar_id=id_agenda,
        )

    @function_tool(
        description="Exclui permanentemente um evento do Google Calendar. Confirme o ID e a identidade do evento antes de chamar."
    )
    async def excluir_evento_google_calendar(
        self,
        id_evento: str,
        id_agenda: str = "primary",
    ):
        return await self._executar_google(
            google_workspace.delete_calendar_event,
            event_id=id_evento,
            calendar_id=id_agenda,
        )

    @function_tool(
        description="Lista todas as agendas (calendários) do usuário no Google Calendar, exibindo ID, nome, descrição, cor atual, fuso horário e permissões."
    )
    async def listar_agendas_google_calendar(self):
        return await self._executar_google(
            google_workspace.list_calendars,
        )

    @function_tool(
        description="Cria uma nova agenda secundária no Google Calendar (ex: 'Estudos', 'Trabalho', 'Saúde', 'Projetos') com descrição, fuso horário e cor opcional."
    )
    async def criar_agenda_google_calendar(
        self,
        nome: str,
        descricao: str = "",
        fuso_horario: str = "America/Sao_Paulo",
        cor: str = "",
    ):
        return await self._executar_google(
            google_workspace.create_calendar,
            summary=nome,
            description=descricao,
            timezone=fuso_horario,
            color=cor,
        )

    @function_tool(
        description="Edita os metadados de uma agenda no Google Calendar (nome/título, descrição ou fuso horário)."
    )
    async def editar_agenda_google_calendar(
        self,
        id_agenda: str,
        nome: str = "",
        descricao: str = "",
        fuso_horario: str = "",
    ):
        return await self._executar_google(
            google_workspace.update_calendar,
            calendar_id=id_agenda,
            summary=nome,
            description=descricao,
            timezone=fuso_horario,
        )

    @function_tool(
        description="Exclui permanentemente uma agenda secundária no Google Calendar. A agenda primária do usuário não pode ser excluída."
    )
    async def excluir_agenda_google_calendar(self, id_agenda: str):
        return await self._executar_google(
            google_workspace.delete_calendar,
            calendar_id=id_agenda,
        )

    @function_tool(
        description="Remove TODOS os eventos da agenda primária do Google Calendar. Use com extrema cautela e apenas após confirmação explícita do usuário."
    )
    async def limpar_agenda_google_calendar(self, id_agenda: str = "primary"):
        return await self._executar_google(
            google_workspace.clear_calendar,
            calendar_id=id_agenda,
        )

    @function_tool(
        description="Retorna o catálogo completo de cores disponíveis no Google Calendar para eventos (11 cores oficiais com nomes e hex) e para agendas (24 cores oficiais)."
    )
    async def obter_paleta_cores_google_calendar(self):
        return await self._executar_google(
            google_workspace.get_calendar_colors,
        )

    @function_tool(
        description="Consulta as configurações da conta do usuário no Google Agenda: primeiro dia da semana (Domingo, Segunda ou Sábado), fuso horário, formato 24h, duração padrão de reuniões e preferências de visualização."
    )
    async def obter_configuracoes_google_calendar(self):
        return await self._executar_google(
            google_workspace.get_calendar_settings,
        )

    @function_tool(
        description=(
            "Configura e orienta a mudança do primeiro dia da semana no Google Agenda "
            "(começar pelo Domingo, Segunda-feira ou Sábado). "
            "Abre instantaneamente o Google Agenda na tela de configurações de visualização "
            "no Chrome do usuário e traz a aba para a frente para aplicação imediata."
        )
    )
    async def configurar_primeiro_dia_semana_google_calendar(
        self,
        dia_da_semana: str = "segunda",
    ):
        dia_norm = (dia_da_semana or "segunda").strip().lower()
        mapa_dias = {
            "domingo": ("0", "Domingo"),
            "sunday": ("0", "Domingo"),
            "dom": ("0", "Domingo"),
            "0": ("0", "Domingo"),
            "segunda": ("1", "Segunda-feira"),
            "segunda-feira": ("1", "Segunda-feira"),
            "monday": ("1", "Segunda-feira"),
            "seg": ("1", "Segunda-feira"),
            "1": ("1", "Segunda-feira"),
            "sabado": ("6", "Sábado"),
            "sábado": ("6", "Sábado"),
            "saturday": ("6", "Sábado"),
            "sab": ("6", "Sábado"),
            "6": ("6", "Sábado"),
        }
        codigo_alvo, nome_alvo = mapa_dias.get(dia_norm, ("1", "Segunda-feira"))

        try:
            atual_info = await asyncio.to_thread(google_workspace.get_week_start_setting)
            nome_atual = atual_info.get("primeiro_dia_semana", "Desconhecido")
        except Exception:
            nome_atual = "Não identificado"

        await self._navegar_no_calendar_navegador("settings")

        return (
            f"Configuração do Google Agenda: atualmente o primeiro dia da semana está definido como '{nome_atual}'. "
            f"Abri a tela de configurações do Google Agenda diretamente no seu navegador Chrome para aplicar para '{nome_alvo}'. "
            f"Basta conferir em 'Opções de visualização' -> 'Começar a semana em' e selecionar '{nome_alvo}'."
        )

    @function_tool(
        description=(
            "Abre o Google Agenda (Google Calendar) no navegador Chrome na visualização solicitada: "
            "'semana' ('week'), 'mes' ('month'), 'dia' ('day'), 'ano' ('year'), "
            "'programacao' ('agenda') ou 'configuracoes' ('settings'). Traz o navegador para a frente."
        )
    )
    async def abrir_google_calendar_navegador(
        self,
        visualizacao: str = "week",
    ):
        rotas = {
            "semana": "week",
            "week": "week",
            "mes": "month",
            "mês": "month",
            "month": "month",
            "dia": "day",
            "day": "day",
            "ano": "year",
            "year": "year",
            "agenda": "agenda",
            "programacao": "agenda",
            "programação": "agenda",
            "configuracoes": "settings",
            "configurações": "settings",
            "settings": "settings",
        }
        rota = rotas.get(visualizacao.strip().lower(), visualizacao.strip())
        await self._navegar_no_calendar_navegador(rota)
        return f"Google Agenda aberto no navegador Chrome na visualização '{rota}'."

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
            "Executa uma tarefa de desenvolvimento autônoma usando o Motor Antigravity integrado no Jarvis. "
            "O Antigravity lê o repositório, escreve e edita arquivos cirurgicamente, executa comandos no terminal PowerShell, "
            "valida e corrige erros até concluir a implementação. "
            "Use sempre que o usuário pedir para o Jarvis implementar, programar, criar uma função, corrigir um bug, "
            "ou alterar o código do Jarvis ou do projeto diretamente por aqui."
        )
    )
    async def executar_tarefa_antigravity(
        self,
        tarefa: str,
        arquivo_alvo: str = "",
        id_solicitacao: int = 0,
    ) -> str:
        tarefa_limpa = (tarefa or "").strip()
        if not tarefa_limpa:
            return "Aviso: informe a tarefa de desenvolvimento que o Antigravity deve executar."

        engine = obter_antigravity_engine()

        # Abre o painel Antigravity no HUD do Jarvis para o usuário ver o console em tempo real
        try:
            if self.board_room and self.board_room.local_participant:
                payload_hud = {"type": "hud_panel_toggle", "panel": "antigravity", "action": "open"}
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload_hud, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
        except Exception:
            pass

        # Callback para transmitir cada passo do Antigravity em tempo real para a sala LiveKit
        async def _on_antigravity_event(evento: dict):
            try:
                if self.board_room and self.board_room.local_participant:
                    payload = {
                        "type": "antigravity_step",
                        "step": evento,
                    }
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.antigravity",
                    )
            except Exception as e:
                logger_memoria.warning(f"Erro ao transmitir evento Antigravity: {e}")

        if id_solicitacao > 0:
            AntigravityEngine.atualizar_status_fila(id_solicitacao, "em_progresso")

        # Notifica o usuário por broadcast de início
        try:
            if self.board_room and self.board_room.local_participant:
                await self.board_room.local_participant.publish_data(
                    json.dumps({
                        "type": "antigravity_status",
                        "estado": "pensando",
                        "tarefa": tarefa_limpa,
                        "isExecuting": True,
                        "open_panel": True,
                    }, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.antigravity",
                )
        except Exception:
            pass

        resultado = await engine.executar_tarefa(
            prompt_tarefa=tarefa_limpa,
            arquivo_alvo=arquivo_alvo,
            max_passos=16,
            on_event=_on_antigravity_event,
        )

        sucesso = resultado.get("sucesso", False)
        resumo = resultado.get("resumo", "")
        arquivos = resultado.get("arquivos_alterados", [])
        passos = resultado.get("passos_executados", 0)

        if id_solicitacao > 0:
            novo_status = "concluida" if sucesso else "falha"
            AntigravityEngine.atualizar_status_fila(id_solicitacao, novo_status, resumo)

        if sucesso:
            arqs_str = ", ".join(arquivos) if arquivos else "arquivos do projeto"
            return (
                f"Chefe, o motor Antigravity concluiu a tarefa com sucesso em {passos} passos! "
                f"Arquivos alterados: {arqs_str}. "
                f"Resumo da execução: {resumo}"
            )
        else:
            return (
                f"Aviso: O motor Antigravity encontrou dificuldades na execução da tarefa. "
                f"Resumo: {resumo}"
            )

    @function_tool(
        description="Executa a próxima solicitação de desenvolvimento pendente na fila do Antigravity."
    )
    async def executar_proxima_solicitacao_antigravity(self) -> str:
        raiz_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        caminho_json = os.path.join(raiz_projeto, "solicitacoes_antigravity.json")
        if not os.path.isfile(caminho_json):
            return "Nenhuma solicitação pendente no momento."

        try:
            with open(caminho_json, "r", encoding="utf-8") as f:
                tarefas = json.load(f)
            pendentes = [t for t in tarefas if t.get("status") == "pendente"]
            if not pendentes:
                return "Todas as solicitações de desenvolvimento já foram concluídas!"

            proxima = pendentes[0]
            desc = proxima.get("descricao") or proxima.get("titulo")
            alvo = proxima.get("arquivo_sugerido", "")
            tid = proxima.get("id", 0)

            return await self.executar_tarefa_antigravity(tarefa=desc, arquivo_alvo=alvo, id_solicitacao=tid)
        except Exception as e:
            return f"Aviso ao executar próxima solicitação: {e}"

    @function_tool(
        description="Abre ou fecha o painel do Antigravity Engine no HUD da interface do Jarvis."
    )
    async def alternar_painel_antigravity(self, acao: str = "abrir") -> str:
        abrir = acao.lower().strip() in ("abrir", "open", "mostrar", "exibir")
        try:
            if self.board_room and self.board_room.local_participant:
                payload = {
                    "type": "hud_panel_toggle",
                    "panel": "antigravity",
                    "action": "open" if abrir else "close",
                }
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.hud",
                )
                return f"Painel do Antigravity {'aberto' if abrir else 'fechado'} com sucesso."
        except Exception as e:
            return f"Aviso ao alternar painel Antigravity: {e}"
        return "Comando processado."

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

    async def _publicar_automacoes_sync(
        self,
        mensagem: str = "",
        abrir_painel: bool = True,
        fechar_painel: bool = False,
    ) -> None:
        try:
            tarefas = await asyncio.to_thread(gerenciador_automacoes.listar_agendados)
            payload = {
                "type": "jarvis_automations_sync",
                "automations": tarefas,
                "message": mensagem,
                "open_panel": abrir_painel,
                "close_panel": fechar_painel,
            }
            await self.board_room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False),
                topic="jarvis.automations",
            )
        except Exception as erro:
            logger_memoria.warning("evento=publicar_automacoes_sync status=erro motivo=%s", erro)

    @function_tool(
        description=(
            "Cria ou atualiza uma automação/rotina agendada no painel de Automações do Jarvis. "
            "Toda automação busca anexos no Gmail conforme o remetente ou termo informado e salva automaticamente "
            "na pasta escolhida do Google Drive (opcionalmente organizando em subpastas de Ano/Mês). "
            "Se o PDF tiver senha para abrir (ex.: '2138'), informe em `senha_anexo` para que o Jarvis remova a senha automaticamente antes de salvar no Drive."
        )
    )
    async def criar_ou_configurar_automacao(
        self,
        nome: str,
        remetente_ou_busca: str,
        pasta_drive: str,
        horario: str = "08:00",
        frequencia: str = "diario",
        tipo_arquivo: str = "pdf",
        organizar_por_ano_mes: bool = True,
        ativo: bool = True,
        id_tarefa: str = "",
        senha_anexo: str = "",
    ) -> str:
        t = await asyncio.to_thread(
            gerenciador_automacoes.criar_ou_atualizar_agendamento,
            nome,
            remetente_ou_busca,
            pasta_drive,
            horario,
            frequencia,
            tipo_arquivo,
            organizar_por_ano_mes,
            True,
            ativo,
            id_tarefa,
            senha_anexo,
        )
        info_senha = f", senha PDF '{t.get('senha_anexo')}' (desbloqueio automático)" if t.get("senha_anexo") else ""
        msg = (
            f"Automação '{t.get('nome')}' salva com sucesso: busca '{t.get('remetente_ou_busca')}', "
            f"pasta Drive '{t.get('pasta_drive')}', frequência '{t.get('frequencia')}' às {t.get('horario')}{info_senha}."
        )
        await self._publicar_automacoes_sync(msg)
        return msg

    @function_tool(
        description=(
            "Executa imediatamente uma automação/rotina agendada que foi criada no painel de Automações do Jarvis "
            "(equivalente ao botão 'Executar agora'). Informe o nome ou ID da automação criada."
        )
    )
    async def executar_automacao_agendada_agora(
        self,
        id_ou_nome_tarefa: str,
    ) -> str:
        print(f"[AUTOMAÇÃO] Disparo manual imediato: '{id_ou_nome_tarefa}'")
        resultado = await asyncio.to_thread(
            gerenciador_automacoes.executar_agora,
            id_ou_nome_tarefa,
        )
        msg = str(resultado.get("mensagem") or "Automação executada.")
        await self._publicar_automacoes_sync(msg)
        return msg

    @function_tool(
        description=(
            "Abre o Painel de Automações na tela do Jarvis e lista todas as automações e rotinas criadas "
            "(status ATIVO/PAUSADO, horário programado, filtro do Gmail, pasta do Drive e resultado da última execução)."
        )
    )
    async def listar_automacoes_agendadas(self) -> str:
        tarefas = await asyncio.to_thread(gerenciador_automacoes.listar_agendados)
        await self._publicar_automacoes_sync("Painel de Automações aberto.", abrir_painel=True)
        if not tarefas:
            return "Painel de Automações aberto na tela! Nenhuma automação criada até o momento. Você pode criar clicando no botão '+ Criar' em Automações ou pedindo por voz."
        linhas: list[str] = []
        for t in tarefas:
            status = "ATIVO" if t.get("ativo", True) else "PAUSADO"
            linhas.append(
                f"- [{status}] {t.get('nome')} | Horário: {t.get('horario')} ({t.get('frequencia')}) "
                f"| Busca Gmail: {t.get('remetente_ou_busca')} | Pasta Drive: {t.get('pasta_drive')} "
                f"| Último resultado: {t.get('ultimo_resultado', 'N/A')}"
            )
        return "Painel de Automações aberto na tela do Jarvis! Automações criadas:\n" + "\n".join(linhas)

    @function_tool(
        description="Exclui/remove uma automação agendada do painel de Automações do Jarvis pelo nome ou ID."
    )
    async def excluir_automacao_agendada(
        self,
        id_ou_nome_tarefa: str,
    ) -> str:
        removida = await asyncio.to_thread(
            gerenciador_automacoes.excluir_agendamento,
            id_ou_nome_tarefa,
        )
        if removida:
            msg = f"Automação '{id_ou_nome_tarefa}' removida com sucesso."
            await self._publicar_automacoes_sync(msg)
            return msg
        return f"Não encontrei nenhuma automação com o nome ou ID '{id_ou_nome_tarefa}'."

    def _caminho_arquivo_lembretes(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lembretes_jarvis.json")

    def _carregar_e_limpar_lembretes(self) -> list[dict]:
        caminho = self._caminho_arquivo_lembretes()
        if not os.path.exists(caminho):
            return []
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
            if not isinstance(dados, list):
                return []
            hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d")
            ativos = [
                item
                for item in dados
                if not (
                    item.get("status") == "concluido"
                    and item.get("concluido_em_data")
                    and str(item.get("concluido_em_data")) < hoje
                )
            ]
            if len(ativos) != len(dados):
                with open(caminho, "w", encoding="utf-8") as f:
                    json.dump(ativos, f, indent=2, ensure_ascii=False)
            return ativos
        except Exception:
            return []

    def _salvar_lembretes(self, lista: list[dict]) -> None:
        caminho = self._caminho_arquivo_lembretes()
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(lista, f, indent=2, ensure_ascii=False)

    async def _publicar_lembretes_sync(self, lista: list[dict], abrir_painel: bool = True) -> None:
        try:
            payload = {
                "type": "jarvis_reminders_sync",
                "reminders": lista,
                "open_panel": abrir_painel,
            }
            await self.board_room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False),
                topic="jarvis.reminders",
            )
        except Exception as erro:
            logger_memoria.warning("evento=publicar_lembretes_sync status=erro motivo=%s", erro)

    @function_tool(
        description=(
            "Adiciona um novo lembrete numerado no Painel de Lembretes da tela (todo lembrete novo entra automaticamente "
            "como PENDENTE em vermelho). Opcionalmente permite anexar um arquivo de qualquer extensão (PDF, foto, print, etc.) "
            "informando `caminho_arquivo_anexo` (ou 'print' para capturar um print da tela e anexar ao lembrete)."
        )
    )
    async def adicionar_lembrete_painel(
        self,
        texto_lembrete: str,
        caminho_arquivo_anexo: str = "",
    ) -> str:
        agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
        anexos_salvos: list[dict] = []

        arq_raw = (caminho_arquivo_anexo or "").strip()
        if arq_raw:
            caminho_origem = ""
            if arq_raw.lower() in {"print", "tela", "screenshot", "captura"}:
                caminho_origem = await asyncio.to_thread(
                    self._capturar_imagem_clipboard_ou_tela,
                    False,
                    "Print_Lembrete",
                ) or ""
            elif os.path.isfile(arq_raw):
                caminho_origem = os.path.abspath(arq_raw)

            if caminho_origem and os.path.isfile(caminho_origem):
                pasta_anexos = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lembretes_anexos")
                os.makedirs(pasta_anexos, exist_ok=True)
                nome_orig = os.path.basename(caminho_origem)
                att_id = f"att-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}"
                nome_salvo = f"{att_id}_{re.sub(r'[^a-zA-Z0-9._-]', '_', nome_orig)[:60]}"
                destino_anexo = os.path.join(pasta_anexos, nome_salvo)
                try:
                    shutil.copy2(caminho_origem, destino_anexo)
                    mime_guess, _ = mimetypes.guess_type(nome_orig)
                    anexos_salvos.append(
                        {
                            "id": att_id,
                            "nome": nome_orig,
                            "mime": mime_guess or "application/octet-stream",
                            "arquivo_salvo": nome_salvo,
                        }
                    )
                except Exception as erro_copia:
                    logger_memoria.warning("evento=anexo_lembrete status=erro motivo=%s", erro_copia)

        lista = await asyncio.to_thread(self._carregar_e_limpar_lembretes)
        novo = {
            "id": f"rem-{uuid.uuid4().hex[:8]}",
            "texto": texto_lembrete.strip(),
            "status": "pendente",
            "criado_em": agora.strftime("%d/%m %H:%M"),
            "concluido_em_data": "",
            "anexos": anexos_salvos,
        }
        lista.append(novo)
        await asyncio.to_thread(self._salvar_lembretes, lista)
        await self._publicar_lembretes_sync(lista, abrir_painel=True)
        info_anexo = f" com anexo '{anexos_salvos[0]['nome']}'" if anexos_salvos else ""
        return f"Lembrete #{len(lista)} ('{novo['texto']}'){info_anexo} adicionado como PENDENTE no painel de Lembretes!"

    @function_tool(
        description=(
            "Altera a cor/status de um lembrete no Painel de Lembretes pelo número (1, 2, 3...) ou texto: "
            "'vermelho' (pendente/urgente), 'amarelo' (em andamento) ou 'verde' (concluído, que apaga automaticamente no dia seguinte)."
        )
    )
    async def atualizar_status_lembrete(
        self,
        numero_ou_texto: str,
        cor_ou_status: str = "verde",
    ) -> str:
        s_raw = (cor_ou_status or "verde").strip().lower()
        if any(p in s_raw for p in ("amarelo", "andamento", "yellow")):
            novo_status = "em_andamento"
            nome_cor = "Amarelo (Em andamento)"
        elif any(p in s_raw for p in ("vermelho", "pendente", "urgente", "red")):
            novo_status = "pendente"
            nome_cor = "Vermelho (Pendente)"
        else:
            novo_status = "concluido"
            nome_cor = "Verde (Concluído — será apagado amanhã)"

        lista = await asyncio.to_thread(self._carregar_e_limpar_lembretes)
        if not lista:
            return "Não há nenhum lembrete cadastrado no painel no momento."

        alvo_str = str(numero_ou_texto or "").strip().lower()
        num_match = re.search(r"\d+", alvo_str)
        encontrado = None
        if num_match:
            idx = int(num_match.group(0)) - 1
            if 0 <= idx < len(lista):
                encontrado = lista[idx]
        if encontrado is None:
            for item in lista:
                if alvo_str in str(item.get("texto") or "").lower() or alvo_str == str(item.get("id") or "").lower():
                    encontrado = item
                    break

        if encontrado is None:
            return f"Não encontrei o lembrete '{numero_ou_texto}'."

        hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d")
        encontrado["status"] = novo_status
        encontrado["concluido_em_data"] = hoje if novo_status == "concluido" else ""
        await asyncio.to_thread(self._salvar_lembretes, lista)
        await self._publicar_lembretes_sync(lista, abrir_painel=True)
        return f"Lembrete '{encontrado.get('texto')}' atualizado para {nome_cor}."

    @function_tool(
        description="Abre o Painel de Lembretes na tela e lista todos os lembretes ativos e seus status (Vermelho, Amarelo ou Verde)."
    )
    async def listar_lembretes_painel(self) -> str:
        lista = await asyncio.to_thread(self._carregar_e_limpar_lembretes)
        await self._publicar_lembretes_sync(lista, abrir_painel=True)
        if not lista:
            return "O painel de Lembretes está aberto, mas ainda não há nenhum lembrete cadastrado."
        linhas = []
        for idx, item in enumerate(lista, start=1):
            st = item.get("status", "pendente")
            cor = "VERMELHO (Pendente)" if st == "pendente" else "AMARELO (Em andamento)" if st == "em_andamento" else "VERDE (Concluído)"
            linhas.append(f"{idx:02d}. [{cor}] {item.get('texto')}")
        return "Lembretes no painel:\n" + "\n".join(linhas)

    @function_tool(
        description="Exclui/apaga imediatamente um lembrete do Painel de Lembretes pelo número (1, 2, 3...) ou texto."
    )
    async def excluir_lembrete_painel(self, numero_ou_texto: str) -> str:
        lista = await asyncio.to_thread(self._carregar_e_limpar_lembretes)
        if not lista:
            return "Não há lembretes para excluir."
        alvo_str = str(numero_ou_texto or "").strip().lower()
        num_match = re.search(r"\d+", alvo_str)
        removido = None
        if num_match:
            idx = int(num_match.group(0)) - 1
            if 0 <= idx < len(lista):
                removido = lista.pop(idx)
        if removido is None:
            for i, item in enumerate(lista):
                if alvo_str in str(item.get("texto") or "").lower() or alvo_str == str(item.get("id") or "").lower():
                    removido = lista.pop(i)
                    break
        if removido is None:
            return f"Não encontrei o lembrete '{numero_ou_texto}'."
        await asyncio.to_thread(self._salvar_lembretes, lista)
        await self._publicar_lembretes_sync(lista, abrir_painel=True)
        return f"Lembrete '{removido.get('texto')}' excluído do painel."

    # =========================================================================
    # BARRA DE FAVORITOS E MARCADORES WEB
    # =========================================================================

    def _caminho_arquivo_favoritos(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "favoritos_jarvis.json")

    def _carregar_favoritos(self) -> list[dict]:
        caminho = self._caminho_arquivo_favoritos()
        try:
            if not os.path.exists(caminho):
                with open(caminho, "w", encoding="utf-8") as f:
                    json.dump([], f, indent=2, ensure_ascii=False)
                return []
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if not isinstance(dados, list):
                    return []
                # Higieniza: nomes sempre em MAIÚSCULAS e limpa eventual https:// de aplicativos
                for item in dados:
                    if isinstance(item, dict):
                        cat_low = str(item.get("categoria") or "").lower()
                        nome_orig = str(item.get("nome") or "")
                        url_orig = str(item.get("url") or "")
                        tipo_orig = str(item.get("tipo") or "")

                        # Auto-correção: se a categoria for Pastas ou for pasta, garante tipo='pasta'
                        eh_pasta = (
                            tipo_orig == "pasta"
                            or cat_low in ("pastas", "pasta")
                            or nome_orig.lower().startswith("pasta ")
                            or "pasta" in nome_orig.lower()
                            or (url_orig and ("\\" in url_orig or "/" in url_orig) and not url_orig.lower().startswith("http") and not url_orig.lower().endswith(".exe"))
                        )
                        if eh_pasta:
                            item["tipo"] = "pasta"
                            item["categoria"] = "Pastas"
                            item["nome"] = re.sub(r'^(a\s+)?pasta\s+', '', nome_orig, flags=re.I).strip().upper()
                            # Se a url for apenas um nome (ex: "Projetos"), tenta recuperar o caminho real no disco
                            if url_orig and not os.path.isdir(url_orig):
                                achado = self.buscar_pasta(url_orig or nome_orig)
                                if achado and os.path.isdir(achado):
                                    item["url"] = achado
                        elif item.get("nome"):
                            item["nome"] = str(item["nome"]).upper()

                        if item.get("tipo") == "app" and str(item.get("url", "")).lower().startswith("http"):
                            item["url"] = re.sub(r"^https?://", "", str(item["url"]), flags=re.I).rstrip("/")
                return dados
        except Exception as e:
            logger_memoria.warning("evento=carregar_favoritos erro=%s", e)
            return []

    def _salvar_favoritos(self, lista: list[dict]) -> None:
        caminho = self._caminho_arquivo_favoritos()
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(lista, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger_memoria.warning("evento=salvar_favoritos erro=%s", e)

    async def _publicar_favoritos_sync(self, lista: list[dict], abrir_painel: bool = True) -> None:
        try:
            if self.board_room and self.board_room.local_participant:
                payload = {
                    "type": "jarvis_favorites_sync",
                    "favorites": lista,
                    "open_panel": abrir_painel,
                }
                await self.board_room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic="jarvis.favorites",
                )
                if abrir_painel:
                    payload_hud = {"type": "hud_panel_toggle", "panel": "favorites", "action": "open"}
                    await self.board_room.local_participant.publish_data(
                        json.dumps(payload_hud, ensure_ascii=False).encode("utf-8"),
                        reliable=True,
                        topic="jarvis.hud",
                    )
        except Exception as erro:
            logger_memoria.warning("evento=publicar_favoritos_sync erro=%s", erro)

    def _classificar_e_resolver_atalho(self, item_ou_url: str, nome: str = "", categoria: str = "", tipo: str = "auto") -> dict:
        alvo = str(item_ou_url or nome or "").strip()
        nome_alvo = str(nome or "").strip()
        cat_alvo = str(categoria or "").strip()

        # Força detecção de pasta se a categoria for Pastas ou o nome indicar pasta
        if cat_alvo.lower() in ("pastas", "pasta") or alvo.lower().startswith(("pasta ", "a pasta ")) or "pasta" in alvo.lower():
            tipo = "pasta"
            cat_alvo = "Pastas"
        tipo_alvo = str(tipo or "auto").strip().lower()
        alvo_low = alvo.lower()

        pasta_usuario = os.path.expanduser("~")

        # 1. Ferramentas de Sistema Windows
        mapa_sistema = {
            "painel de controle": ("control", "PAINEL DE CONTROLE", "Sistema"),
            "painel de controles": ("control", "PAINEL DE CONTROLE", "Sistema"),
            "control panel": ("control", "PAINEL DE CONTROLE", "Sistema"),
            "configuracoes": ("ms-settings:", "CONFIGURAÇÕES DO WINDOWS", "Sistema"),
            "configurações": ("ms-settings:", "CONFIGURAÇÕES DO WINDOWS", "Sistema"),
            "configuracoes do windows": ("ms-settings:", "CONFIGURAÇÕES DO WINDOWS", "Sistema"),
            "configurações do windows": ("ms-settings:", "CONFIGURAÇÕES DO WINDOWS", "Sistema"),
            "settings": ("ms-settings:", "CONFIGURAÇÕES DO WINDOWS", "Sistema"),
            "gerenciador de tarefas": ("taskmgr", "GERENCIADOR DE TAREFAS", "Sistema"),
            "taskmgr": ("taskmgr", "GERENCIADOR DE TAREFAS", "Sistema"),
            "task manager": ("taskmgr", "GERENCIADOR DE TAREFAS", "Sistema"),
            "limpeza de disco": ("cleanmgr", "LIMPEZA DE DISCO", "Sistema"),
            "cleanmgr": ("cleanmgr", "LIMPEZA DE DISCO", "Sistema"),
            "prompt de comando": ("cmd", "PROMPT DE COMANDO", "Sistema"),
            "prompt": ("cmd", "PROMPT DE COMANDO", "Sistema"),
            "cmd": ("cmd", "PROMPT DE COMANDO", "Sistema"),
            "powershell": ("powershell", "POWERSHELL", "Sistema"),
            "servicos": ("services.msc", "SERVIÇOS DO WINDOWS", "Sistema"),
            "serviços": ("services.msc", "SERVIÇOS DO WINDOWS", "Sistema"),
            "gerenciador de dispositivos": ("devmgmt.msc", "GERENCIADOR DE DISPOSITIVOS", "Sistema"),
            "conexoes de rede": ("ncpa.cpl", "CONEXÕES DE REDE", "Sistema"),
            "explorador de arquivos": ("explorer", "EXPLORADOR DE ARQUIVOS", "Sistema"),
            "meu computador": ("explorer", "MEU COMPUTADOR", "Sistema"),
            "este computador": ("explorer", "ESTE COMPUTADOR", "Sistema"),
        }

        # 2. Pastas Padrão do Windows
        mapa_pastas_padrao = {
            "downloads": os.path.join(pasta_usuario, "Downloads"),
            "download": os.path.join(pasta_usuario, "Downloads"),
            "transferencias": os.path.join(pasta_usuario, "Downloads"),
            "transferências": os.path.join(pasta_usuario, "Downloads"),
            "documentos": os.path.join(pasta_usuario, "Documents"),
            "documents": os.path.join(pasta_usuario, "Documents"),
            "desktop": os.path.join(pasta_usuario, "Desktop"),
            "area de trabalho": os.path.join(pasta_usuario, "Desktop"),
            "área de trabalho": os.path.join(pasta_usuario, "Desktop"),
            "imagens": os.path.join(pasta_usuario, "Pictures"),
            "pictures": os.path.join(pasta_usuario, "Pictures"),
            "fotos": os.path.join(pasta_usuario, "Pictures"),
            "videos": os.path.join(pasta_usuario, "Videos"),
            "vídeos": os.path.join(pasta_usuario, "Videos"),
            "musicas": os.path.join(pasta_usuario, "Music"),
            "músicas": os.path.join(pasta_usuario, "Music"),
        }

        exts_documentos = {
            ".pdf", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
            ".txt", ".csv", ".json", ".xml", ".jpg", ".jpeg", ".png",
            ".gif", ".bmp", ".webp", ".mp3", ".wav", ".mp4", ".mkv",
            ".avi", ".zip", ".rar", ".7z", ".iso", ".epub"
        }
        exts_scripts = {".bat", ".cmd", ".ps1", ".py", ".vbs", ".sh"}

        alvo_sem_prefixo = (
            alvo_low.replace("abrir ", "")
            .replace("o programa ", "")
            .replace("o aplicativo ", "")
            .replace("o site ", "")
            .replace("a pasta ", "")
            .replace("o arquivo ", "")
            .replace("pasta ", "")
            .strip()
        )

        # Checagem 1: Ferramenta do Sistema
        if tipo_alvo == "sistema" or alvo_sem_prefixo in mapa_sistema or alvo_low in mapa_sistema:
            cmd, n_sist, cat_sist = mapa_sistema.get(alvo_sem_prefixo, mapa_sistema.get(alvo_low, (alvo, nome_alvo or alvo, "Sistema")))
            return {
                "tipo": "sistema",
                "url": cmd,
                "nome": (nome_alvo or n_sist).upper(),
                "categoria": cat_alvo or cat_sist,
            }

        # Checagem 2: Pasta
        eh_pasta_sugerida = (
            tipo_alvo in ("pasta", "folder", "diretorio", "diretório")
            or cat_alvo.lower() in ("pastas", "pasta")
            or alvo_low.startswith(("pasta ", "a pasta ", "diretório ", "o diretorio "))
            or "pasta" in alvo_low
            or alvo_sem_prefixo in mapa_pastas_padrao
            or os.path.isdir(alvo)
            or (alvo.endswith("\\") or alvo.endswith("/"))
        )
        if eh_pasta_sugerida:
            caminho_pasta = None
            if alvo_sem_prefixo in mapa_pastas_padrao:
                caminho_pasta = mapa_pastas_padrao[alvo_sem_prefixo]
            elif os.path.isdir(alvo):
                caminho_pasta = os.path.abspath(alvo)
            else:
                caminho_pasta = self.buscar_pasta(alvo_sem_prefixo or alvo)

            if caminho_pasta and os.path.isdir(caminho_pasta):
                n_pasta = nome_alvo or os.path.basename(os.path.normpath(caminho_pasta))
                n_pasta_limpa = re.sub(r'^(a\s+)?pasta\s+', '', n_pasta.strip(), flags=re.IGNORECASE)
                if not n_pasta_limpa:
                    n_pasta_limpa = os.path.basename(os.path.normpath(caminho_pasta))
                return {
                    "tipo": "pasta",
                    "url": caminho_pasta,
                    "nome": n_pasta_limpa.upper(),
                    "categoria": cat_alvo or "Pastas",
                }
            elif tipo_alvo in ("pasta", "folder", "diretorio", "diretório") or cat_alvo.lower() in ("pastas", "pasta") or alvo_low.startswith(("pasta ", "a pasta ")) or "pasta" in alvo_low:
                n_pasta_limpa = re.sub(r'^(a\s+)?pasta\s+', '', (nome_alvo or alvo_sem_prefixo).strip(), flags=re.IGNORECASE)
                return {
                    "tipo": "pasta",
                    "url": alvo,
                    "nome": (n_pasta_limpa or alvo_sem_prefixo).upper(),
                    "categoria": "Pastas",
                    "caminho_pendente": True,
                }

        # Checagem 3: Arquivo / Documento ou Script
        _, ext = os.path.splitext(alvo_low)
        eh_script = tipo_alvo == "script" or ext in exts_scripts
        eh_arquivo = tipo_alvo in ("arquivo", "documento", "file") or ext in exts_documentos or os.path.isfile(alvo)

        if eh_script or eh_arquivo:
            caminho_arq = None
            if os.path.isfile(alvo):
                caminho_arq = os.path.abspath(alvo)
            else:
                caminho_arq = self.buscar_arquivo_local(alvo)

            if caminho_arq and os.path.isfile(caminho_arq):
                n_arq = nome_alvo or os.path.basename(caminho_arq)
                t_final = "script" if (eh_script or os.path.splitext(caminho_arq)[1].lower() in exts_scripts) else "arquivo"
                cat_padrao = "Scripts" if t_final == "script" else "Arquivos"
                return {
                    "tipo": t_final,
                    "url": caminho_arq,
                    "nome": n_arq.upper(),
                    "categoria": cat_alvo or cat_padrao,
                }

        # Checagem 4: Site / Link Web
        mapa_sites_rapidos = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "gmail": "https://mail.google.com/mail/u/0/#inbox",
            "chatgpt": "https://chatgpt.com",
            "canva": "https://www.canva.com",
            "github": "https://github.com",
            "mercadolivre": "https://www.mercadolivre.com.br",
            "mercado livre": "https://www.mercadolivre.com.br",
            "instagram": "https://www.instagram.com",
            "tiktok": "https://www.tiktok.com",
            "netflix": "https://www.netflix.com",
            "whatsapp web": "https://web.whatsapp.com",
            "whatsapp": "https://web.whatsapp.com",
            "drive": "https://drive.google.com",
            "google drive": "https://drive.google.com",
        }

        eh_site = (
            tipo_alvo in ("site", "web", "url", "link")
            or alvo.startswith(("http://", "https://"))
            or ("." in alvo and " " not in alvo and not alvo_low.endswith(".exe"))
            or "www." in alvo_low
            or alvo_sem_prefixo in mapa_sites_rapidos
        )

        if eh_site:
            url_site = alvo
            if alvo_sem_prefixo in mapa_sites_rapidos:
                url_site = mapa_sites_rapidos[alvo_sem_prefixo]
            elif not url_site.startswith(("http://", "https://")):
                url_site = f"https://{url_site}"

            if not nome_alvo:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url_site)
                    dominio = parsed.netloc.replace("www.", "")
                    nome_alvo = dominio.split(".")[0].capitalize() if dominio else alvo
                except Exception:
                    nome_alvo = alvo

            return {
                "tipo": "site",
                "url": url_site,
                "nome": nome_alvo.upper(),
                "categoria": cat_alvo or "Sites",
            }

        # Checagem 5: Aplicativo / Programa (Calculadora, Cortecloud, Excel, etc.)
        alvo_app = re.sub(r"^https?://", "", alvo, flags=re.I).rstrip("/")
        nome_app = (nome_alvo or alvo_app).upper()
        return {
            "tipo": "app",
            "url": alvo_app,
            "nome": nome_app,
            "categoria": cat_alvo or "Aplicativos",
        }

    def _executar_ps_dialog(self, script_nome: str, args: list[str] = None) -> str:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_nome)
        if not os.path.isfile(script):
            return ""
        cmd = [
            "powershell.exe",
            "-STA",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", script,
        ]
        if args:
            cmd.extend(args)
        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 1  # SW_SHOWNORMAL (garante exibição visível e interativa na tela)

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                startupinfo=startupinfo,
                timeout=120
            )
            return (res.stdout or "").strip()
        except Exception as e:
            logger_memoria.warning("evento=executar_ps_dialog_erro script=%s erro=%s", script_nome, e)
            return ""

    async def _abrir_dialogo_selecionar_pasta(self, caminho_inicial: str = "") -> str:
        """Abre a janela nativa do Windows (FolderBrowserDialog) para o usuário escolher uma pasta com o mouse."""
        args = ["-InitialPath", caminho_inicial] if (caminho_inicial and os.path.isdir(caminho_inicial)) else []
        return await asyncio.to_thread(self._executar_ps_dialog, "browse_folder.ps1", args)

    async def _abrir_dialogo_selecionar_arquivo(self) -> str:
        """Abre a janela nativa do Windows (OpenFileDialog) para o usuário escolher um arquivo com o mouse."""
        return await asyncio.to_thread(self._executar_ps_dialog, "browse_file.ps1", [])

    @function_tool(
        description=(
            "SEMPRE QUE O USUÁRIO PEDIR PARA SALVAR, ADICIONAR OU CRIAR UM ATALHO PARA UMA PASTA DO COMPUTADOR "
            "(ex.: 'cria um atalho da pasta projeto', 'salva a pasta Projetos nos atalhos', 'adiciona a pasta tal aos atalhos'). "
            "Esta ferramenta ABRE O EXPLORADOR DE ARQUIVOS DO WINDOWS NA TELA para o usuário conduzir até a pasta ou conferir se escolheu a pasta certa!"
        )
    )
    async def adicionar_pasta_favorita(self, nome_ou_caminho: str = "", categoria: str = "Pastas") -> str:
        """Abre o seletor de pastas na tela do Windows para o usuário escolher ou confirmar a pasta exata."""
        sugestao = str(nome_ou_caminho or "").strip()
        caminho_inicial = ""
        if sugestao:
            if os.path.isdir(sugestao):
                caminho_inicial = os.path.abspath(sugestao)
            else:
                caminho_encontrado = await asyncio.to_thread(self.buscar_pasta, sugestao)
                if caminho_encontrado and os.path.isdir(caminho_encontrado):
                    caminho_inicial = caminho_encontrado

        # Abre SEMPRE a janela do Windows na tela com foco prioritário para o usuário escolher ou confirmar
        caminho_escolhido = await self._abrir_dialogo_selecionar_pasta(caminho_inicial=caminho_inicial)
        if not caminho_escolhido or not os.path.isdir(caminho_escolhido):
            return "Nenhuma pasta foi selecionada na janela do Windows. A criação do atalho foi cancelada."

        nome_puro = os.path.basename(os.path.normpath(caminho_escolhido))
        nome_final = re.sub(r'^(a\s+)?pasta\s+', '', nome_puro, flags=re.I).strip().upper()
        if not nome_final:
            nome_final = nome_puro.upper()

        return await self.adicionar_favorito(
            item_ou_url=caminho_escolhido,
            nome=nome_final,
            categoria="Pastas",
            tipo="pasta",
            pasta_confirmada=True
        )

    @function_tool(
        description=(
            "Adiciona um ARQUIVO OU DOCUMENTO DO COMPUTADOR (ex.: 'planilha.xlsx', 'relatório.pdf', 'contrato.docx', fotos, etc.) "
            "no painel ATALHOS do Jarvis. Se o usuário pedir para 'escolher um arquivo', abre a janela do Windows para selecionar o arquivo!"
        )
    )
    async def adicionar_arquivo_favorito(self, nome_ou_caminho: str = "", categoria: str = "Arquivos") -> str:
        """Salva um arquivo ou documento no painel Atalhos do Jarvis (abrindo a janela seletora se necessário)."""
        caminho_alvo = str(nome_ou_caminho or "").strip()
        if not caminho_alvo or any(g in caminho_alvo.lower() for g in ["escolher", "selecionar", "procurar"]):
            caminho_alvo = await self._abrir_dialogo_selecionar_arquivo()
            if not caminho_alvo:
                return "Nenhum arquivo foi selecionado na janela do Windows."

        return await self.adicionar_favorito(item_ou_url=caminho_alvo, nome="", categoria=categoria or "Arquivos", tipo="arquivo")

    @function_tool(
        description=(
            "Adiciona, favorita ou salva APENAS UM APLICATIVO OU PROGRAMA EXECUTÁVEL (.exe) DO COMPUTADOR (ex.: Calculadora, Excel, Bloco de Notas, Cortecloud, WhatsApp, Word, Spotify, Chrome, etc.) no painel ATALHOS. "
            "ATENÇÃO: NÃO USE ESTA FERRAMENTA PARA PASTAS! Se o usuário pedir atalho para qualquer PASTA ou DIRETÓRIO, use OBRIGATORIAMENTE 'adicionar_pasta_favorita'!"
        )
    )
    async def adicionar_aplicativo_favorito(self, nome_aplicativo: str, categoria: str = "Aplicativos") -> str:
        """Salva um aplicativo ou programa instalado no Windows no painel Atalhos do Jarvis."""
        alvo_check = str(nome_aplicativo or "").strip().lower()
        cat_check = str(categoria or "").strip().lower()
        if (
            cat_check in ("pastas", "pasta")
            or alvo_check.startswith(("pasta ", "a pasta ", "diretório ", "o diretório ", "diretorio ", "o diretorio "))
            or "pasta " in alvo_check
            or os.path.isdir(nome_aplicativo)
        ):
            nome_limpo_pasta = re.sub(r'^(a\s+)?pasta\s+', '', str(nome_aplicativo or '').strip(), flags=re.I)
            return await self.adicionar_pasta_favorita(nome_ou_caminho=nome_limpo_pasta, categoria="Pastas")
        return await self.adicionar_favorito(item_ou_url=nome_aplicativo, nome=nome_aplicativo, categoria=categoria or "Aplicativos", tipo="app")

    @function_tool(
        description=(
            "Adiciona, favorita ou salva um SITE, LINK OU PÁGINA WEB (ex.: YouTube, Google, Portal do Aluno, etc.) no painel ATALHOS do Jarvis (NÃO é a barra de favoritos do Chrome/navegador). "
            "Se o usuário disser 'salve este site' ou estiver vendo uma página no navegador, captura automaticamente a aba ativa."
        )
    )
    async def adicionar_site_favorito(self, url_ou_site: str = "", nome: str = "", categoria: str = "Sites") -> str:
        """Salva um site ou link da web no painel Atalhos do Jarvis."""
        return await self.adicionar_favorito(item_ou_url=url_ou_site, nome=nome, categoria=categoria or "Sites", tipo="site")

    @function_tool(
        description=(
            "Adiciona um item ao painel ATALHOS do Jarvis (Sites, Ferramentas de Sistema, Scripts, etc.). "
            "ATENÇÃO: Se o usuário pedir atalho para QUALQUER PASTA DO COMPUTADOR, use OBRIGATORIAMENTE 'adicionar_pasta_favorita', que abre a janela na tela para escolher/confirmar com o mouse!\n"
            "Se o usuário pedir atalho para um APLICATIVO OU PROGRAMA, use 'adicionar_aplicativo_favorito'."
        )
    )
    async def adicionar_favorito(self, item_ou_url: str = "", nome: str = "", categoria: str = "", tipo: str = "auto", pasta_confirmada: bool = False) -> str:
        alvo = str(item_ou_url or nome or "").strip()
        nome_alvo = str(nome or "").strip()
        cat_alvo = str(categoria or "").strip()

        # Se for pasta e ainda não foi confirmada pelo diálogo, encaminha SEMPRE para adicionar_pasta_favorita
        eh_pasta = (
            cat_alvo.lower() in ("pastas", "pasta")
            or alvo.lower().startswith(("pasta ", "a pasta "))
            or "pasta" in alvo.lower()
            or tipo == "pasta"
        )
        if eh_pasta and not pasta_confirmada:
            termo = re.sub(r'^(a\s+)?pasta\s+', '', alvo or nome_alvo, flags=re.I).strip()
            return await self.adicionar_pasta_favorita(nome_ou_caminho=termo, categoria="Pastas")

        # Força detecção de pasta se a categoria for Pastas ou o nome indicar pasta
        if cat_alvo.lower() in ("pastas", "pasta") or alvo.lower().startswith(("pasta ", "a pasta ")) or "pasta" in alvo.lower():
            tipo = "pasta"
            cat_alvo = "Pastas"

        # Se pediu o site atual ou não passou nada, tenta capturar aba do navegador
        if not alvo or any(termo in alvo.lower() for termo in ["este site", "site atual", "essa aba", "esta aba"]):
            aba = await asyncio.to_thread(self._obter_aba_ativa_navegador)
            if aba.get("url"):
                alvo = aba["url"]
                if not nome_alvo:
                    nome_alvo = aba.get("title") or ""
                tipo = "site"

        if not alvo:
            return "Não consegui identificar o item para adicionar aos Atalhos. Por favor, diga o nome, link ou caminho."

        # Usa o motor especialista para classificar e resolver caminho/tipo
        classificado = self._classificar_e_resolver_atalho(
            item_ou_url=alvo,
            nome=nome_alvo,
            categoria=cat_alvo,
            tipo=tipo or "auto"
        )

        alvo_final = classificado["url"]
        nome_final = classificado["nome"]
        tipo_final = classificado["tipo"]
        cat_final = classificado["categoria"]

        # Proteção para Pastas: NUNCA salva atalho quebrado sem caminho físico no disco!
        if tipo_final == "pasta" or classificado.get("caminho_pendente"):
            if not os.path.isdir(alvo_final):
                escolhido = await self._abrir_dialogo_selecionar_pasta()
                if escolhido and os.path.isdir(escolhido):
                    alvo_final = escolhido
                    nome_final = re.sub(r'^(a\s+)?pasta\s+', '', os.path.basename(os.path.normpath(escolhido)), flags=re.I).strip().upper()
                else:
                    return (
                        f"Aviso: Não consegui localizar a pasta '{alvo}' no computador. "
                        "A janela para seleção de pasta foi aberta, mas nenhuma pasta foi escolhida."
                    )

        lista = await asyncio.to_thread(self._carregar_favoritos)

        # Evita duplicatas pelo mesmo identificador ou nome
        encontrado = None
        for item in lista:
            url_existente = str(item.get("url") or "").rstrip("/\\").lower()
            nome_existente = str(item.get("nome") or "").strip().lower()
            tipo_existente = str(item.get("tipo") or "").lower()
            if (url_existente == alvo_final.rstrip("/\\").lower()) or (tipo_existente == tipo_final and nome_existente == nome_final.lower()):
                encontrado = item
                break

        hoje_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        rotulos_tipo = {
            "site": "Site",
            "app": "Aplicativo",
            "pasta": "Pasta",
            "arquivo": "Arquivo",
            "sistema": "Ferramenta de Sistema",
            "script": "Script",
        }
        nome_tipo_legivel = rotulos_tipo.get(tipo_final, "Atalho")

        if encontrado:
            encontrado["nome"] = nome_final
            encontrado["url"] = alvo_final
            encontrado["tipo"] = tipo_final
            encontrado["categoria"] = cat_final
            encontrado["atualizado_em"] = hoje_str
            msg = f"{nome_tipo_legivel} '{nome_final}' atualizado nos Atalhos (categoria: '{cat_final}')!"
        else:
            novo_id = f"fav-{uuid.uuid4().hex[:8]}"
            novo_fav = {
                "id": novo_id,
                "nome": nome_final,
                "url": alvo_final,
                "tipo": tipo_final,
                "categoria": cat_final,
                "criado_em": hoje_str,
                "ordem": len(lista),
            }
            lista.append(novo_fav)
            msg = f"{nome_tipo_legivel} '{nome_final}' adicionado com sucesso aos Atalhos (categoria: {cat_final})!"

        await asyncio.to_thread(self._salvar_favoritos, lista)
        await self._publicar_favoritos_sync(lista, abrir_painel=True)
        return msg

    @function_tool(
        description="Abre o painel Atalhos na tela e lista todos os atalhos (sites, programas, pastas, arquivos) salvos. Pode filtrar por 'categoria' se desejado."
    )
    async def listar_favoritos(self, categoria: str = "") -> str:
        lista = await asyncio.to_thread(self._carregar_favoritos)
        await self._publicar_favoritos_sync(lista, abrir_painel=True)
        if not lista:
            return "O painel Atalhos está aberto na tela, mas ainda não há nenhum atalho salvo."

        filtro = str(categoria or "").strip().lower()
        if filtro:
            filtrados = [f for f in lista if filtro in str(f.get("categoria", "")).lower()]
        else:
            filtrados = lista

        if not filtrados:
            return f"Não encontrei atalhos na categoria '{categoria}'. Total de {len(lista)} atalhos salvos em outras categorias."

        linhas = []
        for idx, item in enumerate(filtrados, start=1):
            cat = f" [{item.get('categoria')}]" if item.get("categoria") else ""
            linhas.append(f"{idx:02d}. {item.get('nome')}{cat} - {item.get('url')}")
        return "Painel de Atalhos:\n" + "\n".join(linhas)

    @function_tool(
        description="Abre um atalho do painel Atalhos pelo nome (ex: 'YouTube', 'Calculadora', 'Projetos', 'Cortecloud') ou número da lista."
    )
    async def abrir_favorito(self, nome_ou_url: str) -> str:
        lista = await asyncio.to_thread(self._carregar_favoritos)
        if not lista:
            return "Não há nenhum atalho cadastrado."

        termo = str(nome_ou_url or "").strip().lower()
        encontrado = None

        num_match = re.search(r"^\s*(\d+)\s*$", termo)
        if num_match:
            idx = int(num_match.group(1)) - 1
            if 0 <= idx < len(lista):
                encontrado = lista[idx]

        if not encontrado:
            for item in lista:
                nome_item = str(item.get("nome") or "").lower()
                url_item = str(item.get("url") or "").lower()
                if termo == nome_item or termo in nome_item or termo in url_item:
                    encontrado = item
                    break

        if not encontrado:
            return f"Não encontrei nenhum atalho correspondente a '{nome_ou_url}' no painel Atalhos."

        url_final = encontrado.get("url")
        nome_final = encontrado.get("nome")
        tipo_final = str(encontrado.get("tipo", "site")).lower()

        if tipo_final == "pasta":
            caminho_abrir = url_final
            if not caminho_abrir or not os.path.isdir(caminho_abrir):
                caminho_recup = await asyncio.to_thread(self.buscar_pasta, nome_final or url_final)
                if caminho_recup and os.path.isdir(caminho_recup):
                    caminho_abrir = caminho_recup
                    encontrado["url"] = caminho_recup
                    await asyncio.to_thread(self._salvar_favoritos, lista)
            await self.abrir_pasta(nome_ou_caminho=caminho_abrir)
            return f"Abrindo pasta '{nome_final}' no computador."
        elif tipo_final == "arquivo":
            await asyncio.to_thread(self._spawn_on_interactive_desktop, f'cmd.exe /c start "" "{url_final}"', show_window=0)
            return f"Abrindo arquivo '{nome_final}' no computador."
        elif tipo_final == "sistema":
            await asyncio.to_thread(self._spawn_on_interactive_desktop, f'cmd.exe /c start "" {url_final}', show_window=1)
            return f"Abrindo ferramenta de sistema '{nome_final}'."
        elif tipo_final == "script":
            await asyncio.to_thread(self._spawn_on_interactive_desktop, f'cmd.exe /c start "" "{url_final}"', show_window=1)
            return f"Executando script '{nome_final}'."
        elif tipo_final == "app" or not str(url_final).startswith(("http://", "https://")):
            await self.abrir_programa(nome_programa=url_final or nome_final)
            return f"Abrindo aplicativo '{nome_final}' no computador."
        else:
            await self.abrir_site(url=url_final, nome_site=nome_final)
            return f"Abrindo '{nome_final}' ({url_final}) no navegador."

    @function_tool(
        description="Renomeia ou edita um favorito existente. Permite alterar o nome, a URL ou a categoria pelo identificador (nome atual, número da lista ou id)."
    )
    async def renomear_favorito(self, identificador: str, novo_nome: str, nova_url: str = "", nova_categoria: str = "") -> str:
        lista = await asyncio.to_thread(self._carregar_favoritos)
        if not lista:
            return "Não há favoritos cadastrados para renomear."

        alvo = str(identificador or "").strip().lower()
        encontrado = None

        num_match = re.search(r"^\s*(\d+)\s*$", alvo)
        if num_match:
            idx = int(num_match.group(1)) - 1
            if 0 <= idx < len(lista):
                encontrado = lista[idx]

        if not encontrado:
            for item in lista:
                if alvo == str(item.get("id", "")).lower() or alvo in str(item.get("nome", "")).lower() or alvo in str(item.get("url", "")).lower():
                    encontrado = item
                    break

        if not encontrado:
            return f"Não encontrei o favorito '{identificador}' para renomear."

        antigo_nome = encontrado.get("nome")
        if novo_nome:
            encontrado["nome"] = novo_nome.strip().upper()
        # Para aplicativos, NUNCA permite corromper o comando com https
        if nova_url and encontrado.get("tipo") != "app":
            u = nova_url.strip()
            if not u.startswith(("http://", "https://")):
                u = f"https://{u}"
            encontrado["url"] = u
        elif encontrado.get("tipo") == "app":
            if str(encontrado.get("url", "")).lower().startswith("http"):
                encontrado["url"] = re.sub(r"^https?://", "", str(encontrado["url"]), flags=re.I).rstrip("/")
        if nova_categoria:
            encontrado["categoria"] = nova_categoria.strip()

        await asyncio.to_thread(self._salvar_favoritos, lista)
        await self._publicar_favoritos_sync(lista, abrir_painel=True)
        return f"Favorito '{antigo_nome}' atualizado para '{encontrado.get('nome')}' com sucesso."

    @function_tool(
        description="Exclui/apaga um site ou aplicativo da Barra de Favoritos pelo nome, número da lista ou id."
    )
    async def remover_favorito(self, identificador: str) -> str:
        lista = await asyncio.to_thread(self._carregar_favoritos)
        if not lista:
            return "A Barra de Favoritos já está vazia."

        alvo = str(identificador or "").strip().lower()
        removido = None

        num_match = re.search(r"^\s*(\d+)\s*$", alvo)
        if num_match:
            idx = int(num_match.group(1)) - 1
            if 0 <= idx < len(lista):
                removido = lista.pop(idx)

        if not removido:
            for i, item in enumerate(lista):
                if alvo == str(item.get("id", "")).lower() or alvo in str(item.get("nome", "")).lower() or alvo in str(item.get("url", "")).lower():
                    removido = lista.pop(i)
                    break

        if not removido:
            return f"Não encontrei o favorito '{identificador}' para remover."

        await asyncio.to_thread(self._salvar_favoritos, lista)
        await self._publicar_favoritos_sync(lista, abrir_painel=True)
        return f"Favorito '{removido.get('nome')}' removido com sucesso."

    # --- TIKTOK LIVE CO-HOST & STREAMING ASSISTANT ---

    @function_tool(
        description=(
            "Conecta o Jarvis a uma transmissão ao vivo no TikTok pelo @usuario do streamer. "
            "Se o usuário pedir 'conecta na minha live', utilize o usuário padrão 'falecomrafaelmuniz'. "
            "Permite ao Jarvis acompanhar o chat em tempo real, filtrar dúvidas, acompanhar "
            "likes e presentes para atuar como co-host da transmissão."
        )
    )
    async def conectar_tiktok_live(self, usuario: str = "falecomrafaelmuniz") -> str:
        """Conecta o Jarvis a uma transmissão ao vivo no TikTok."""
        if not usuario or usuario.strip().lower() in ("meu", "minha", "minha live", "padrao", "padrão", "live"):
            usuario = "falecomrafaelmuniz"
        logger_memoria.info("evento=tiktok_live_conectar usuario=%s", usuario)
        res = await asyncio.to_thread(tiktok_manager.conectar, usuario)
        if res.get("sucesso"):
            return f"Conectado com sucesso à Live do TikTok de @{res.get('usuario')}. Jarvis já está escutando o chat e pronto para interagir."
        return f"Não foi possível conectar à Live do TikTok: {res.get('erro', 'motivo desconhecido')}."

    @function_tool(
        description="Desconecta o Jarvis da transmissão ao vivo atual do TikTok."
    )
    async def desconectar_tiktok_live(self) -> str:
        """Desconecta da live do TikTok."""
        logger_memoria.info("evento=tiktok_live_desconectar")
        res = await asyncio.to_thread(tiktok_manager.desconectar)
        return res.get("mensagem", "Desconectado do TikTok Live.")

    @function_tool(
        description=(
            "Lê as perguntas ou comentários mais recentes dos espectadores na Live do TikTok conectada. "
            "Use sempre que o usuário perguntar o que o chat está falando, pedir para ler perguntas da live "
            "ou pedir ajuda para responder o público."
        )
    )
    async def obter_chat_tiktok_live(self, apenas_perguntas: bool = True, limite: int = 5) -> str:
        """Obtém as perguntas ou comentários mais recentes dos espectadores."""
        logger_memoria.info("evento=tiktok_obter_chat apenas_perguntas=%s limite=%s", apenas_perguntas, limite)
        res = await asyncio.to_thread(tiktok_manager.obter_perguntas_e_comentarios, limite, apenas_perguntas, True)
        if not res.get("conectado"):
            return res.get("aviso", "Jarvis não está conectado a nenhuma live do TikTok no momento.")

        mensagens = res.get("mensagens", [])
        if not mensagens:
            return "Nenhuma nova pergunta ou mensagem foi enviada no chat da live recentemente."

        tipo = "perguntas" if apenas_perguntas else "comentários"
        linhas = [f"Últimas {tipo} dos espectadores no TikTok (@{res.get('usuario_live')}):"]
        for m in mensagens:
            tempo = f"({m.get('tempo_atras_segundos', 0)}s atrás)"
            linhas.append(f"- @{m.get('usuario')}: '{m.get('texto')}' {tempo}")

        linhas.append("\nResponda diretamente com simpatia citando o nome do espectador e trazendo a resposta para a live.")
        return "\n".join(linhas)

    @function_tool(
        description=(
            "Consulta as métricas em tempo real da Live do TikTok atual: tempo ativo, total de comentários, "
            "curtidas acumuladas, presentes recebidos e perguntas pendentes."
        )
    )
    async def obter_status_tiktok_live(self) -> str:
        """Consulta as estatísticas atuais da live no TikTok."""
        logger_memoria.info("evento=tiktok_status_live")
        res = await asyncio.to_thread(tiktok_manager.obter_status_live)
        if not res.get("conectado"):
            return "Jarvis não está conectado a nenhuma live do TikTok no momento."

        tempo_min = res.get("tempo_ativo_segundos", 0) // 60
        presentes = res.get("ultimos_presentes", [])
        presentes_str = "; ".join(presentes) if presentes else "Nenhum presente recente"

        return (
            f"Métricas da Live de @{res.get('usuario_live')}:\n"
            f"- Tempo de monitoramento: {tempo_min} minuto(s)\n"
            f"- Total de comentários: {res.get('total_comentarios')}\n"
            f"- Total de curtidas: {res.get('total_likes')}\n"
            f"- Presentes recebidos: {res.get('total_presentes')} ({presentes_str})\n"
            f"- Perguntas pendentes: {res.get('perguntas_pendentes')}"
        )

    # --- FIM DAS FERRAMENTAS ---

async def _destravar_thinking_watchdog(session: AgentSession, timeout: float = 6.0):
    """Garante que o agente nunca fique travado no estado 'thinking' após executar uma ferramenta."""
    try:
        await asyncio.sleep(timeout)
        if session.agent_state == "thinking":
            logger_memoria.warning("evento=watchdog_thinking status=destravando_agente_para_listening")
            session._update_agent_state("listening")
    except Exception as erro:
        logger_memoria.warning("evento=watchdog_thinking status=erro motivo=%s", erro)


async def _publish_tool_activity(
    room: rtc.Room,
    events: asyncio.Queue[ToolExecutionUpdatedEvent | None],
    session: AgentSession | None = None,
):
    watchdog_pendente: asyncio.Task | None = None
    while True:
        event = await events.get()
        if event is None:
            if watchdog_pendente and not watchdog_pendente.done():
                watchdog_pendente.cancel()
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
            if session is not None:
                # Mantém apenas UM watchdog ativo (antes acumulavam vários por ferramenta).
                if watchdog_pendente and not watchdog_pendente.done():
                    watchdog_pendente.cancel()
                watchdog_pendente = asyncio.create_task(_destravar_thinking_watchdog(session))
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
            # Só encerra se a sala caiu de vez; falha pontual não pode matar as métricas.
            try:
                if room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                    return
            except Exception:
                return
            previous_sample = None
            await asyncio.sleep(3)
            continue

        previous_sample = (sampled_at, network.bytes_sent, network.bytes_recv)


def _handle_room_data(
    data_packet: rtc.DataPacket,
    location_state: dict[str, float | str],
    agent: Assistant,
):
    if data_packet.topic == "jarvis.location":
        _update_session_location(data_packet, location_state)
        return
    if data_packet.topic == "jarvis.hud":
        try:
            payload = json.loads(data_packet.data)
        except Exception:
            return
        if payload.get("type") == "jarvis_set_voice_style":
            estilo_req = str(payload.get("style") or "descolado")
            asyncio.create_task(agent.aplicar_estilo_voz(estilo_req, falar_confirmacao=True))
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
        return
    if data_packet.topic == "jarvis.automations":
        try:
            payload = json.loads(data_packet.data)
        except Exception:
            return
        if payload.get("type") == "jarvis_automation_run_now":
            id_tarefa = str(payload.get("id") or "").strip()
            if id_tarefa:
                asyncio.create_task(agent.executar_automacao_agendada_agora(id_tarefa))
        return
    if data_packet.topic == "jarvis.favorites":
        try:
            payload = json.loads(data_packet.data)
            if payload.get("action") in ("open_url", "open") and (payload.get("url") or payload.get("nome")):
                alvo = str(payload.get("url") or payload.get("nome") or "").strip()
                tipo = str(payload.get("tipo") or "").lower()
                agora = time.monotonic()
                ultimo_alvo, ultimo_tempo = getattr(agent, "_ultimo_atalho_aberto", ("", 0.0))
                if alvo == ultimo_alvo and (agora - ultimo_tempo) < 1.5:
                    return
                agent._ultimo_atalho_aberto = (alvo, agora)
                if tipo == "pasta":
                    caminho_abrir = alvo
                    if not caminho_abrir or not os.path.isdir(caminho_abrir):
                        caminho_recup = agent.buscar_pasta(payload.get("nome") or alvo)
                        if caminho_recup and os.path.isdir(caminho_recup):
                            caminho_abrir = caminho_recup
                            try:
                                lista_fav = agent._carregar_favoritos()
                                for it in lista_fav:
                                    if it.get("url") == alvo or it.get("nome") == payload.get("nome"):
                                        it["url"] = caminho_recup
                                        break
                                agent._salvar_favoritos(lista_fav)
                            except Exception:
                                pass
                    asyncio.create_task(agent.abrir_pasta(caminho_abrir))
                elif tipo == "arquivo":
                    asyncio.create_task(asyncio.to_thread(agent._spawn_on_interactive_desktop, f'cmd.exe /c start "" "{alvo}"', show_window=0))
                elif tipo == "sistema":
                    asyncio.create_task(asyncio.to_thread(agent._spawn_on_interactive_desktop, f'cmd.exe /c start "" {alvo}', show_window=1))
                elif tipo == "script":
                    asyncio.create_task(asyncio.to_thread(agent._spawn_on_interactive_desktop, f'cmd.exe /c start "" "{alvo}"', show_window=1))
                elif tipo == "site" or alvo.startswith(("http://", "https://")) or ("." in alvo and " " not in alvo and not alvo.lower().endswith(".exe")):
                    asyncio.create_task(agent.abrir_site(alvo, nome_site=payload.get("nome", "")))
                else:
                    asyncio.create_task(agent.abrir_programa(alvo))
        except Exception:
            pass
        return
    if data_packet.topic == "jarvis.workspace":
        try:
            payload = json.loads(data_packet.data)
        except Exception:
            return
        if payload.get("type") == "jarvis_active_notebook_sync":
            nb_id = str(payload.get("notebookId") or "").strip()
            if nb_id:
                agent._active_notebook_id = nb_id
            if isinstance(payload.get("isOpen"), bool):
                agent._workspace_panel_open = payload["isOpen"]
                logger_memoria.info("evento=sync_painel_workspace aberto=%s nb=%s", payload["isOpen"], agent._active_notebook_id)
                if not payload["isOpen"]:
                    agent._ultimo_texto_notebook_transcrito = ""
        return
    if data_packet.topic == "jarvis.antigravity":
        try:
            payload = json.loads(data_packet.data)
            action = payload.get("action")
            if action == "run_task":
                tarefa = payload.get("tarefa") or payload.get("prompt") or ""
                alvo = payload.get("arquivo_alvo") or ""
                id_sol = int(payload.get("id_solicitacao") or 0)
                if tarefa:
                    asyncio.create_task(agent.executar_tarefa_antigravity(tarefa, alvo, id_sol))
            elif action == "run_next":
                asyncio.create_task(agent.executar_proxima_solicitacao_antigravity())
        except Exception as e:
            logger_memoria.warning(f"Erro ao processar pacote jarvis.antigravity: {e}")
        return


async def _monitorar_chegada_emails(session: AgentSession, assistant: Assistant):
    """Monitora em segundo plano a chegada de novos e-mails no Gmail e avisa o usuário por voz quando bater com os alertas configurados."""
    inicializado = False
    while True:
        await asyncio.sleep(15)
        if not assistant._alertas_email:
            continue
        if not inicializado:
            inicializado = True
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
                        "Fale OBRIGATORIAMENTE em Português do Brasil de forma natural e pergunte se ele quer ler, responder ou mover para algum marcador no Gmail."
                    )
                )
            except Exception as erro:
                logger_memoria.warning("evento=alerta_email_voz status=erro motivo=%s", erro)


async def _monitorar_automacoes_agendadas(session: AgentSession, assistant: Assistant):
    """Executa em segundo plano as automações agendadas criadas pelo usuário e avisa por voz quando arquivar novos arquivos."""
    await asyncio.sleep(8)
    while True:
        try:
            resultados = await asyncio.to_thread(
                gerenciador_automacoes.verificar_e_executar_pendentes
            )
            if resultados:
                await assistant._publicar_automacoes_sync(abrir_painel=False)
            for res in resultados:
                arquivos = res.get("arquivos_salvos") or []
                if arquivos:
                    pasta_dest = res.get("pasta_destino") or "Google Drive"
                    nomes_arq = ", ".join(arquivos)
                    try:
                        await session.generate_reply(
                            instructions=(
                                f"AVISO DE AUTOMAÇÃO CONCLUÍDA: A automação '{res.get('tarefa')}' acabou de rodar, "
                                f"encontrou {len(arquivos)} arquivo(s) novo(s) ({nomes_arq}) no Gmail e já salvou automaticamente "
                                f"na pasta '{pasta_dest}' do Google Drive. Fale em Português do Brasil comemorando brevemente!"
                            )
                        )
                    except Exception as erro_voz:
                        logger_memoria.warning("evento=alerta_automacao_voz status=erro motivo=%s", erro_voz)
        except Exception as erro:
            logger_memoria.warning("evento=monitorar_automacoes status=erro motivo=%s", erro)

        await asyncio.sleep(20)


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
    jarvis_chrome.start_bridge()
    session = AgentSession(
        vad=None,
        turn_handling={"turn_detection": "realtime_llm"},
        aec_warmup_duration=0.6,
    )
    location_state: dict[str, float | str] = {}
    activity_events: asyncio.Queue[ToolExecutionUpdatedEvent | None] = asyncio.Queue()
    assistant = Assistant(location_state, ctx.room)
    assistant._active_agent_session = session
    ctx.room.on(
        "data_received",
        lambda data_packet: _handle_room_data(data_packet, location_state, assistant),
    )

    def _on_user_transcribed(ev):
        texto = getattr(ev, "transcript", "") or ""
        logger_memoria.info("evento=user_input_transcribed texto=%s", texto)
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
        logger_memoria.info("evento=conversation_item_added role=%s texto=%s", role, texto)
        if role in ("user", "assistant"):
            assistant._memoria_curta.registrar("USUARIO" if role == "user" else "JARVIS", str(texto))
        _verificar_texto_abrir_rascunho(str(texto), role == "user", assistant)

    def _on_tool_event(ev):
        try:
            update = ev.update
            if isinstance(update, ToolCallStarted):
                fc = update.function_call
                assistant._memoria_curta.registrar_inicio_ferramenta(
                    fc.call_id, fc.name, str(getattr(fc, "arguments", "") or "")
                )
            elif isinstance(update, ToolCallEnded):
                assistant._memoria_curta.registrar_fim_ferramenta(update.call_id, str(update.status))
        except Exception as erro_mem:
            logger_memoria.warning("evento=memoria_curta status=erro motivo=%s", erro_mem)

    def _on_session_close(ev):
        activity_events.put_nowait(None)
        # Se a sessão foi fechada por erro, mas o usuário ainda está conectado na sala,
        # desconecta a sala para acionar a reconexão automática instantânea do frontend.
        reason = getattr(ev, "reason", None)
        err = getattr(ev, "error", None)
        logger_memoria.info("evento=session_close reason=%s err=%s", reason, err)
        if str(reason).lower() in ("error", "closereason.error") or err is not None:
            if ctx.room and ctx.room.remote_participants:
                logger_memoria.warning("evento=recuperar_sessao motivo=erro_fechamento_com_participante_ativo")
                try:
                    asyncio.create_task(ctx.room.disconnect())
                except Exception:
                    pass

    @ctx.room.on("track_subscribed")
    def _on_track_sub(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        logger_memoria.info("evento=track_subscribed tipo=%s source=%s part=%s", track.kind, publication.source, participant.identity)

    @ctx.room.on("track_published")
    def _on_track_pub(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        logger_memoria.info("evento=track_published source=%s muted=%s part=%s", publication.source, publication.muted, participant.identity)

    @ctx.room.on("track_muted")
    def _on_track_muted(publication: rtc.TrackPublication, participant: rtc.Participant):
        logger_memoria.warning("evento=track_muted source=%s part=%s", publication.source, participant.identity)

    @ctx.room.on("track_unmuted")
    def _on_track_unmuted(publication: rtc.TrackPublication, participant: rtc.Participant):
        logger_memoria.info("evento=track_unmuted source=%s part=%s", publication.source, participant.identity)

    session.on("user_input_transcribed", _on_user_transcribed)
    session.on("conversation_item_added", _on_item_added)
    session.on("tool_execution_updated", activity_events.put_nowait)
    session.on("tool_execution_updated", _on_tool_event)
    session.on("close", _on_session_close)

    # --- Diagnóstico e anti-travamento do estado do agente ---
    thinking_guard: dict[str, asyncio.Task | None] = {"task": None}

    async def _guarda_thinking_longo():
        try:
            await asyncio.sleep(20)
            if session.agent_state == "thinking":
                logger_memoria.warning("evento=watchdog_thinking_longo status=destravando_apos_20s")
                session._update_agent_state("listening")
        except asyncio.CancelledError:
            pass
        except Exception as erro:
            logger_memoria.warning("evento=watchdog_thinking_longo status=erro motivo=%s", erro)

    def _on_agent_state(ev):
        novo = str(getattr(ev, "new_state", ""))
        logger_memoria.info(
            "evento=agent_state de=%s para=%s", getattr(ev, "old_state", ""), novo
        )
        tarefa = thinking_guard["task"]
        if tarefa and not tarefa.done():
            tarefa.cancel()
        thinking_guard["task"] = (
            asyncio.create_task(_guarda_thinking_longo()) if novo == "thinking" else None
        )

    def _on_user_state(ev):
        logger_memoria.info(
            "evento=user_state de=%s para=%s",
            getattr(ev, "old_state", ""),
            getattr(ev, "new_state", ""),
        )

    def _on_session_error(ev):
        erro = getattr(ev, "error", ev)
        logger_memoria.error(
            "evento=session_error tipo=%s recuperavel=%s detalhe=%s",
            type(erro).__name__,
            getattr(erro, "recoverable", "?"),
            str(erro)[:300],
        )

    session.on("agent_state_changed", _on_agent_state)
    session.on("user_state_changed", _on_user_state)
    session.on("error", _on_session_error)
    asyncio.create_task(_publish_tool_activity(ctx.room, activity_events, session))

    usar_bvc = (
        os.environ.get("JARVIS_USE_BVC", "0") == "1"
        and ".livekit.cloud" in os.environ.get("LIVEKIT_URL", "")
    )
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC() if usar_bvc else None,
            ),
            video_input=False,
            close_on_disconnect=True,
        ),
    )

    metrics_task = asyncio.create_task(_publish_system_metrics(ctx.room))
    email_watch_task = asyncio.create_task(_monitorar_chegada_emails(session, assistant))
    automacoes_watch_task = asyncio.create_task(_monitorar_automacoes_agendadas(session, assistant))
    session.on("close", lambda _: (metrics_task.cancel(), email_watch_task.cancel(), automacoes_watch_task.cancel()))

    # Aguarda o participante estar conectado com áudio pronto para garantir que a saudação seja ouvida
    try:
        if not ctx.room.remote_participants:
            for _ in range(15):
                await asyncio.sleep(0.2)
                if ctx.room.remote_participants:
                    break
        await asyncio.sleep(0.4)
        await assistant._publicar_estilo_voz_hud()
        saudacao_prompt = (
            "Inicialização concluída. Fale imediatamente em voz alta em Português do Brasil com tom descontraído, vivo e parceiro (1 frase curta): E aí, chefe! Sistemas online, tudo pronto por aqui."
            if assistant._estilo_voz == "descolado"
            else "Inicialização concluída. Fale imediatamente em voz alta em Português do Brasil: Sistemas online, chefe."
        )
        await session.generate_reply(user_input=saudacao_prompt)
    except Exception as erro_saudacao:
        logger_memoria.warning("evento=saudacao_inicial status=erro motivo=%s", erro_saudacao)

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=0))