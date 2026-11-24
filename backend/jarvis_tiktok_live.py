"""
Módulo de Integração com o TikTok LIVE para o Assistente Jarvis.
Permite ao Jarvis conectar-se a qualquer transmissão ao vivo no TikTok,
capturar comentários em tempo real, filtrar perguntas de espectadores,
acompanhar presentes/curtidas e atuar como co-host de transmissão.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.tiktok")

try:
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import (
        CommentEvent,
        ConnectEvent,
        DisconnectEvent,
        GiftEvent,
        LikeEvent,
        LiveEndEvent,
        QuestionNewEvent,
    )
    TIKTOKLIVE_DISPONIVEL = True
except ImportError:
    TIKTOKLIVE_DISPONIVEL = False
    logger.warning("Biblioteca TikTokLive não encontrada. Execute 'pip install TikTokLive'.")


class TikTokLiveManager:
    """
    Gerenciador singleton para escuta e interação com transmissões ao vivo do TikTok.
    Mantém histórico circular de mensagens, perguntas e presentes recebidos.
    """

    _instancia: Optional["TikTokLiveManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TikTokLiveManager":
        with cls._lock:
            if cls._instancia is None:
                cls._instancia = super().__new__(cls)
                cls._instancia._inicializar()
            return cls._instancia

    def _inicializar(self) -> None:
        self.client: Optional[Any] = None
        self.usuario_alvo: str = ""
        self.conectado: bool = False
        self.erro_conexao: Optional[str] = None
        self.inicio_sessao: float = 0.0

        # Estatísticas da live
        self.total_comentarios: int = 0
        self.total_likes: int = 0
        self.total_presentes: int = 0
        self.visualizadores: int = 0

        # Filas circulares em memória
        self.comentarios: deque = deque(maxlen=150)
        self.perguntas: deque = deque(maxlen=60)
        self.presentes: deque = deque(maxlen=30)

        # Controle de loop assíncrono em thread separada
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()

    def _detectar_se_pergunta(self, texto: str) -> bool:
        """Heurística simples para identificar dúvidas de espectadores."""
        t = texto.strip().lower()
        if "?" in t:
            return True
        palavras_chave = [
            "como ", "qual ", "onde ", "quando ", "por que ", "pq ", "quanto ",
            "quem ", "vc sabe", "você sabe", "jarvis,", "jarvis ", "explica",
            "pode falar", "dúvida", "duvida", "funciona"
        ]
        return any(t.startswith(p) or f" {p}" in t for p in palavras_chave)

    def _configurar_eventos(self, client: Any) -> None:
        """Registra os ouvintes de eventos da live no cliente."""

        @client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            self.conectado = True
            self.erro_conexao = None
            self.inicio_sessao = time.time()
            logger.info(f"evento=tiktok_live status=conectado usuario=@{self.usuario_alvo}")

        @client.on(DisconnectEvent)
        async def on_disconnect(event: DisconnectEvent):
            self.conectado = False
            logger.info(f"evento=tiktok_live status=desconectado usuario=@{self.usuario_alvo}")

        @client.on(LiveEndEvent)
        async def on_live_end(event: LiveEndEvent):
            self.conectado = False
            logger.info(f"evento=tiktok_live status=live_encerrada usuario=@{self.usuario_alvo}")

        @client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            try:
                self.total_comentarios += 1
                user_nick = (event.user.nick_name if event.user else None) or (event.user.unique_id if event.user else "Espectador")
                texto = event.comment or ""
                item = {
                    "id": f"{time.time()}_{self.total_comentarios}",
                    "usuario": user_nick,
                    "texto": texto,
                    "timestamp": time.time(),
                    "lida": False,
                }
                self.comentarios.append(item)
                if self._detectar_se_pergunta(texto):
                    self.perguntas.append(item)
            except Exception as e:
                logger.error(f"evento=tiktok_live_comment_erro erro={e}")

        @client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            try:
                self.total_presentes += 1
                user_nick = (event.user.nick_name if event.user else None) or "Espectador"
                nome_gift = (event.gift.gift_name if event.gift else None) or "Presente"
                qtd = getattr(event, "repeat_count", 1) or 1
                item = {
                    "usuario": user_nick,
                    "presente": nome_gift,
                    "quantidade": qtd,
                    "timestamp": time.time(),
                }
                self.presentes.append(item)
                logger.info(f"evento=tiktok_gift usuario={user_nick} presente={nome_gift} qtd={qtd}")
            except Exception as e:
                logger.error(f"evento=tiktok_live_gift_erro erro={e}")

        @client.on(LikeEvent)
        async def on_like(event: LikeEvent):
            try:
                total = getattr(event, "total", 0) or 0
                if total > self.total_likes:
                    self.total_likes = total
                else:
                    self.total_likes += getattr(event, "count", 1) or 1
            except Exception:
                pass

        @client.on(QuestionNewEvent)
        async def on_question(event: QuestionNewEvent):
            try:
                texto_pergunta = getattr(event, "question_text", "") or ""
                user_nick = getattr(event, "user_nickname", "Espectador") or "Espectador"
                item = {
                    "id": f"q_{time.time()}",
                    "usuario": user_nick,
                    "texto": texto_pergunta,
                    "timestamp": time.time(),
                    "lida": False,
                    "destaque": True,
                }
                self.perguntas.append(item)
            except Exception as e:
                logger.error(f"evento=tiktok_qa_erro erro={e}")

    def conectar(self, usuario: str = "") -> Dict[str, Any]:
        """
        Conecta o monitor de Live ao canal do TikTok especificado.
        Se vazio ou omitido, utiliza o perfil padrão @falecomrafaelmuniz.
        """
        if not TIKTOKLIVE_DISPONIVEL:
            return {
                "sucesso": False,
                "erro": "Módulo TikTokLive não está instalado no ambiente Python.",
            }

        import os
        usuario_limpo = (usuario or "").strip().lstrip("@")
        if not usuario_limpo or usuario_limpo.lower() in ("meu", "minha", "minha live", "padrao", "padrão", "live"):
            usuario_limpo = os.getenv("TIKTOK_DEFAULT_USERNAME", "falecomrafaelmuniz").strip().lstrip("@")

        if not usuario_limpo:
            return {"sucesso": False, "erro": "Nome de usuário do TikTok inválido."}

        # Se já estiver conectado ao mesmo canal, confirma
        if self.conectado and self.usuario_alvo == usuario_limpo:
            return {
                "sucesso": True,
                "mensagem": f"Jarvis já está conectado e monitorando a live de @{usuario_limpo}.",
                "usuario": usuario_limpo,
            }

        # Desconecta de live anterior se houver
        self.desconectar()

        self.usuario_alvo = usuario_limpo
        self.erro_conexao = None
        self._stop_event.clear()

        def _thread_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            try:
                client = TikTokLiveClient(unique_id=f"@{self.usuario_alvo}")
                self.client = client
                self._configurar_eventos(client)

                logger.info(f"evento=tiktok_live status=iniciando_conexao usuario=@{self.usuario_alvo}")
                loop.run_until_complete(client.start())
            except Exception as exc:
                self.erro_conexao = str(exc)
                self.conectado = False
                logger.error(f"evento=tiktok_live_erro erro={exc}")
            finally:
                self.conectado = False
                try:
                    loop.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=_thread_worker, daemon=True, name="JarvisTikTokLive")
        self._thread.start()

        # Aguarda brevemente para ver se conectou ou falhou imediatamente
        for _ in range(10):
            time.sleep(0.3)
            if self.conectado:
                break
            if self.erro_conexao:
                break

        if self.erro_conexao:
            return {
                "sucesso": False,
                "erro": f"Não foi possível conectar à live de @{usuario_limpo}: {self.erro_conexao}",
            }

        return {
            "sucesso": True,
            "mensagem": f"Conexão iniciada com a live de @{usuario_limpo}. Jarvis agora está escutando comentários, perguntas e presentes em tempo real.",
            "usuario": usuario_limpo,
        }

    def desconectar(self) -> Dict[str, Any]:
        """Desconecta o Jarvis da live atual."""
        self._stop_event.set()
        if self.client:
            try:
                if self.client.connected:
                    self.client.disconnect()
            except Exception as e:
                logger.warning(f"evento=tiktok_disconnect_warn erro={e}")
            self.client = None

        self.conectado = False
        antigo_usuario = self.usuario_alvo
        self.usuario_alvo = ""
        return {
            "sucesso": True,
            "mensagem": f"Desconectado da live de @{antigo_usuario}." if antigo_usuario else "Nenhuma live estava ativa.",
        }

    def obter_perguntas_e_comentarios(
        self,
        limite: int = 6,
        apenas_perguntas: bool = True,
        marcar_lidas: bool = True,
    ) -> Dict[str, Any]:
        """
        Retorna as perguntas ou comentários mais recentes dos participantes.
        """
        if not self.conectado and not self.comentarios:
            return {
                "conectado": False,
                "total": 0,
                "mensagens": [],
                "aviso": "Jarvis não está conectado a nenhuma live do TikTok no momento. Diga 'Jarvis, conecte na live do @usuario'."
            }

        fonte = self.perguntas if (apenas_perguntas and self.perguntas) else self.comentarios
        itens_filtrados: List[Dict[str, Any]] = []

        # Pega as últimas mensagens não lidas ou as mais recentes
        for item in reversed(fonte):
            if len(itens_filtrados) >= limite:
                break
            itens_filtrados.append({
                "usuario": item["usuario"],
                "texto": item["texto"],
                "tempo_atras_segundos": int(time.time() - item["timestamp"]),
                "foi_lida": item.get("lida", False),
            })
            if marcar_lidas:
                item["lida"] = True

        itens_filtrados.reverse()

        return {
            "conectado": self.conectado,
            "usuario_live": self.usuario_alvo,
            "total_mensagens_capturadas": self.total_comentarios,
            "mensagens": itens_filtrados,
            "tipo": "perguntas" if apenas_perguntas else "comentarios_gerais",
        }

    def obter_status_live(self) -> Dict[str, Any]:
        """Retorna o resumo atual de métricas da transmissão."""
        ultimos_presentes = [
            f"{p['usuario']} enviou {p['quantidade']}x {p['presente']}"
            for p in list(self.presentes)[-3:]
        ]
        tempo_ativo = int(time.time() - self.inicio_sessao) if self.conectado and self.inicio_sessao > 0 else 0

        return {
            "conectado": self.conectado,
            "usuario_live": self.usuario_alvo,
            "tempo_ativo_segundos": tempo_ativo,
            "total_comentarios": self.total_comentarios,
            "total_likes": self.total_likes,
            "total_presentes": self.total_presentes,
            "ultimos_presentes": ultimos_presentes,
            "perguntas_pendentes": len([p for p in self.perguntas if not p.get("lida", False)]),
        }


# Instância global
tiktok_manager = TikTokLiveManager()
