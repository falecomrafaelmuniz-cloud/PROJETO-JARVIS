import base64
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import pypdf
except ImportError:
    pypdf = None  # type: ignore

from googleapiclient.http import MediaIoBaseUpload

from google_workspace import (
    FOLDER_MIME_TYPE,
    GMAIL_SCOPE,
    GoogleWorkspaceError,
    google_workspace,
)

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
ARQUIVO_BANCO_TAREFAS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tarefas_agendadas.json",
)

MESES_PT = {
    1: "01 JANEIRO",
    2: "02 FEVEREIRO",
    3: "03 MARÇO",
    4: "04 ABRIL",
    5: "05 MAIO",
    6: "06 JUNHO",
    7: "07 JULHO",
    8: "08 AGOSTO",
    9: "09 SETEMBRO",
    10: "10 OUTUBRO",
    11: "11 NOVEMBRO",
    12: "12 DEZEMBRO",
}


def _escapar_query_drive(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def buscar_ou_criar_pasta_drive(
    drive_service, nome_pasta: str, parent_id: str | None = None
) -> str:
    """Encontra ou cria dinamicamente pastas no Google Drive."""
    nome_esc = _escapar_query_drive(nome_pasta)
    query = (
        f"mimeType = '{FOLDER_MIME_TYPE}' "
        f"and name = '{nome_esc}' "
        f"and trashed = false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    resposta = (
        drive_service.files()
        .list(q=query, fields="files(id, name)", pageSize=5)
        .execute()
    )
    pastas = resposta.get("files", [])
    if pastas:
        return pastas[0]["id"]

    metadados: dict = {
        "name": nome_pasta,
        "mimeType": FOLDER_MIME_TYPE,
    }
    if parent_id:
        metadados["parents"] = [parent_id]

    pasta_criada = (
        drive_service.files().create(body=metadados, fields="id").execute()
    )
    return pasta_criada.get("id")


def _arquivo_ja_existe_na_pasta(
    drive_service, nome_arquivo: str, pasta_id: str
) -> bool:
    nome_esc = _escapar_query_drive(nome_arquivo)
    query = (
        f"name = '{nome_esc}' "
        f"and '{pasta_id}' in parents "
        f"and trashed = false"
    )
    resp = (
        drive_service.files()
        .list(q=query, fields="files(id)", pageSize=1)
        .execute()
    )
    return bool(resp.get("files"))


def _extrair_anexos_recursivo(
    payload: dict, tipo_arquivo: str = "pdf"
) -> list[tuple[str, str, str, str | None]]:
    """Percorre recursivamente todas as camadas MIME do e-mail procurando anexos compatíveis.
    Retorna lista de tuplas: (nome_arquivo, mime_type, attachment_id, inline_data_b64).
    """
    encontrados: list[tuple[str, str, str, str | None]] = []
    filtro_ext = (tipo_arquivo or "pdf").strip().lower()

    extensoes_aceitas: tuple[str, ...] = ()
    if filtro_ext in {"pdf", ".pdf"}:
        extensoes_aceitas = (".pdf",)
    elif filtro_ext in {"xml", ".xml"}:
        extensoes_aceitas = (".xml",)
    elif filtro_ext in {"planilha", "excel", "xlsx", "csv"}:
        extensoes_aceitas = (".xlsx", ".xls", ".csv")
    elif filtro_ext in {"imagem", "foto", "png", "jpg"}:
        extensoes_aceitas = (".png", ".jpg", ".jpeg", ".webp")

    def _varrer(parte: dict):
        if not isinstance(parte, dict):
            return
        nome_arq = (parte.get("filename") or "").strip()
        mime = (parte.get("mimeType") or "application/octet-stream").lower()
        body = parte.get("body") or {}
        att_id = body.get("attachmentId")
        data_b64 = body.get("data")

        if (att_id or data_b64) and nome_arq:
            # Ignora imagens inline de assinatura de e-mail (Content-ID + inline) quando o filtro for 'todos'
            headers_parte = {
                str(h.get("name") or "").lower(): str(h.get("value") or "").lower()
                for h in (parte.get("headers") or [])
                if isinstance(h, dict)
            }
            disp = headers_parte.get("content-disposition", "")
            tem_cid = "content-id" in headers_parte or "x-attachment-id" in headers_parte
            if (
                filtro_ext in {"todos", "qualquer", "*"}
                and mime.startswith("image/")
                and "inline" in disp
                and tem_cid
            ):
                pass
            else:
                aceito = False
                if not extensoes_aceitas or filtro_ext in {"todos", "qualquer", "*"}:
                    aceito = True
                elif nome_arq.lower().endswith(extensoes_aceitas):
                    aceito = True
                elif ".pdf" in extensoes_aceitas and mime == "application/pdf":
                    aceito = True

                if aceito:
                    encontrados.append((nome_arq, mime, str(att_id or ""), data_b64))

        for sub in parte.get("parts") or []:
            _varrer(sub)

    _varrer(payload)
    return encontrados


def _desbloquear_pdf_se_protegido(
    dados_bytes: bytes,
    senha_configurada: str = "",
) -> tuple[bytes, bool]:
    """Verifica se o PDF possui senha; se tiver, destrava com a senha da automação
    (ou senhas padrão de CPF como '2138' / '879') e retorna o PDF já desbloqueado (sem senha)."""
    if pypdf is None or not dados_bytes.startswith(b"%PDF"):
        return dados_bytes, False

    try:
        reader = pypdf.PdfReader(io.BytesIO(dados_bytes))
        if not reader.is_encrypted:
            return dados_bytes, False

        candidatas: list[str] = []
        if senha_configurada:
            for parte in re.split(r"[,;\s]+", str(senha_configurada).strip()):
                p = parte.strip()
                if p and p not in candidatas:
                    candidatas.append(p)

        # Fallbacks inteligentes (4 primeiros dígitos ou 3 últimos dígitos do CPF informados pelo usuário)
        for padrao in ("2138", "879", ""):
            if padrao not in candidatas:
                candidatas.append(padrao)

        desbloqueado = False
        for tentativa in candidatas:
            try:
                status = reader.decrypt(tentativa)
                if status and int(status) > 0:
                    desbloqueado = True
                    break
            except Exception:
                continue

        if not desbloqueado:
            return dados_bytes, False

        writer = pypdf.PdfWriter()
        for pagina in reader.pages:
            writer.add_page(pagina)

        saida = io.BytesIO()
        writer.write(saida)
        bytes_limpos = saida.getvalue()
        if bytes_limpos.startswith(b"%PDF"):
            return bytes_limpos, True
    except Exception as erro:
        print(f"[JARVIS AUTOMAÇÃO] Aviso ao tentar remover senha do PDF: {erro}")

    return dados_bytes, False


def executar_rotina_automacao(tarefa: dict) -> dict:
    """Executa dinamicamente uma automação configurada pelo usuário."""
    agora = datetime.now(FUSO_BRASILIA)
    timestamp_str = agora.strftime("%d/%m/%Y %H:%M:%S")
    nome_rotina = tarefa.get("nome") or "Automação"
    remetente_ou_busca = (tarefa.get("remetente_ou_busca") or "").strip()
    pasta_drive_raiz = (tarefa.get("pasta_drive") or "Automações Jarvis").strip()
    tipo_arquivo = (tarefa.get("tipo_arquivo") or "pdf").strip().lower()
    organizar_por_ano_mes = bool(tarefa.get("organizar_por_ano_mes", True))
    marcar_como_lido = bool(tarefa.get("marcar_como_lido", True))
    apenas_nao_lidos = bool(tarefa.get("apenas_nao_lidos", True))
    senha_anexo = str(tarefa.get("senha_anexo") or "").strip()

    print(f"\n[JARVIS AUTOMAÇÃO] [{timestamp_str}] Executando '{nome_rotina}'...")

    if not remetente_ou_busca:
        return {
            "ok": False,
            "arquivos_salvos": [],
            "mensagem": "Filtro de busca/remetente do Gmail não informado na automação.",
        }

    try:
        with google_workspace._api_call_lock:
            gmail_service = google_workspace._service(
                "gmail", "v1", required_scopes=[GMAIL_SCOPE]
            )
            drive_service = google_workspace._service("drive", "v3")

            # 1. Estrutura de pastas no Google Drive
            id_pasta_raiz = buscar_ou_criar_pasta_drive(drive_service, pasta_drive_raiz)
            if organizar_por_ano_mes:
                pasta_mes_nome = f"{agora.year} / {MESES_PT[agora.month]}"
                id_pasta_destino = buscar_ou_criar_pasta_drive(
                    drive_service, pasta_mes_nome, parent_id=id_pasta_raiz
                )
                caminho_exibicao = f"{pasta_drive_raiz} / {pasta_mes_nome}"
            else:
                id_pasta_destino = id_pasta_raiz
                caminho_exibicao = pasta_drive_raiz

            # 2. Monta a query do Gmail (suporta múltiplos remetentes separados por ';', ',' ou ' OR ', e e-mails enviados para si mesmo)
            partes_q = ["(in:inbox OR -in:sent) -in:drafts -in:trash -in:spam"]
            itens_busca = [
                item.strip()
                for bloco in re.split(r"[;,]+", remetente_ou_busca)
                for item in re.split(r"\s+\bOR\b\s+", bloco.strip())
                if item.strip()
            ]
            termos_or: list[str] = []
            for item in itens_busca:
                if ":" in item:
                    termos_or.append(item)
                elif " " in item and "@" not in item:
                    termos_or.append(f'from:"{item}"')
                else:
                    termos_or.append(f"from:{item}")

            if len(termos_or) == 1:
                partes_q.append(termos_or[0])
            elif len(termos_or) > 1:
                partes_q.append("{" + " ".join(termos_or) + "}")

            partes_q.append("has:attachment")
            if tipo_arquivo in {"pdf", "xml", "xlsx", "csv"}:
                partes_q.append(f"filename:{tipo_arquivo}")
            if apenas_nao_lidos:
                partes_q.append("is:unread")
            if "newer_than:" not in remetente_ou_busca.lower() and "after:" not in remetente_ou_busca.lower():
                partes_q.append("newer_than:1d")

            query = " ".join(partes_q)
            resultado = (
                gmail_service.users()
                .messages()
                .list(userId="me", q=query, maxResults=5)
                .execute()
            )
            mensagens = resultado.get("messages", [])

            if not mensagens:
                msg_retorno = (
                    f"Nenhum novo e-mail recente encontrado para '{remetente_ou_busca}' "
                    f"(destino: '{caminho_exibicao}')."
                )
                print(f"[JARVIS AUTOMAÇÃO] {msg_retorno}")
                return {
                    "ok": True,
                    "arquivos_salvos": [],
                    "pasta_destino": caminho_exibicao,
                    "emails_processados": 0,
                    "mensagem": msg_retorno,
                }

            arquivos_salvos: list[str] = []
            emails_processados = 0
            ids_ja_processados = set(tarefa.get("ids_processados") or [])
            novos_ids_processados: list[str] = list(ids_ja_processados)

            # Limite de tempo: só aceita e-mails recebidos a partir de 15 min antes da criação da automação (ou últimas 24h)
            ts_agora = time.time()
            criado_em_ts = float(tarefa.get("criado_em_ts") or 0.0)
            if criado_em_ts > 0:
                limite_minimo_ts = max(criado_em_ts - 900.0, ts_agora - 86400.0)
            else:
                limite_minimo_ts = ts_agora - 86400.0

            for resumo in mensagens:
                msg_id = resumo["id"]
                if msg_id in ids_ja_processados:
                    continue

                msg = (
                    gmail_service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="full")
                    .execute()
                )

                # Verifica a data real de chegada do e-mail (internalDate em ms) para nunca puxar e-mails antigos
                internal_date_ms = float(msg.get("internalDate") or 0.0)
                if internal_date_ms > 0:
                    email_ts = internal_date_ms / 1000.0
                    if email_ts < limite_minimo_ts:
                        print(
                            f"[JARVIS AUTOMAÇÃO] Ignorando e-mail antigo (id={msg_id}) anterior à criação da automação."
                        )
                        continue

                payload = msg.get("payload") or {}
                anexos = _extrair_anexos_recursivo(payload, tipo_arquivo=tipo_arquivo)

                salvou_neste_email = False
                for nome_arquivo, mime_type, anexo_id, inline_b64 in anexos:
                    if anexo_id:
                        anexo = (
                            gmail_service.users()
                            .messages()
                            .attachments()
                            .get(userId="me", messageId=msg_id, id=anexo_id)
                            .execute()
                        )
                        dados_b64 = anexo.get("data", "")
                    else:
                        dados_b64 = inline_b64 or ""

                    if not dados_b64:
                        continue

                    if _arquivo_ja_existe_na_pasta(
                        drive_service, nome_arquivo, id_pasta_destino
                    ):
                        base_n, ext_n = os.path.splitext(nome_arquivo)
                        nome_arquivo = f"{base_n}_{msg_id[:6]}{ext_n}"

                    dados_bytes = base64.urlsafe_b64decode(dados_b64.encode("utf-8"))
                    foi_desbloqueado = False
                    if nome_arquivo.lower().endswith(".pdf") or mime_type == "application/pdf":
                        dados_bytes, foi_desbloqueado = _desbloquear_pdf_se_protegido(
                            dados_bytes, senha_configurada=senha_anexo
                        )

                    stream = io.BytesIO(dados_bytes)

                    metadados = {
                        "name": nome_arquivo,
                        "parents": [id_pasta_destino],
                    }
                    midia = MediaIoBaseUpload(
                        stream,
                        mimetype=mime_type or "application/pdf",
                        resumable=True,
                    )
                    drive_service.files().create(
                        body=metadados, media_body=midia, fields="id, name"
                    ).execute()

                    sufixo_senha = " (senha removida)" if foi_desbloqueado else ""
                    arquivos_salvos.append(f"{nome_arquivo}{sufixo_senha}")
                    salvou_neste_email = True
                    print(
                        f"[JARVIS AUTOMAÇÃO] Sucesso: '{nome_arquivo}'{sufixo_senha} salvo em '{caminho_exibicao}'."
                    )

                if salvou_neste_email:
                    emails_processados += 1
                    if msg_id not in novos_ids_processados:
                        novos_ids_processados.append(msg_id)
                    if marcar_como_lido:
                        gmail_service.users().messages().modify(
                            userId="me",
                            id=msg_id,
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute()

        if arquivos_salvos:
            lista_str = ", ".join(arquivos_salvos)
            msg_final = (
                f"{len(arquivos_salvos)} arquivo(s) salvo(s) ({lista_str}) "
                f"em '{caminho_exibicao}'."
            )
        else:
            msg_final = "Nenhum anexo novo recente para salvar."

        return {
            "ok": True,
            "arquivos_salvos": arquivos_salvos,
            "pasta_destino": caminho_exibicao,
            "emails_processados": emails_processados,
            "ids_processados": novos_ids_processados[-100:],
            "mensagem": msg_final,
        }

    except GoogleWorkspaceError as erro_gw:
        msg_erro = f"Erro de permissão Google: {erro_gw}"
        print(f"[JARVIS AUTOMAÇÃO Erro] {msg_erro}")
        return {"ok": False, "arquivos_salvos": [], "mensagem": msg_erro}
    except Exception as e:
        msg_erro = f"Falha na execução da automação '{nome_rotina}': {e}"
        print(f"[JARVIS AUTOMAÇÃO Erro] {msg_erro}")
        return {"ok": False, "arquivos_salvos": [], "mensagem": msg_erro}


class GerenciadorAgendamentosJARVIS:
    def __init__(self, caminho_arquivo: str = ARQUIVO_BANCO_TAREFAS):
        self.caminho_arquivo = caminho_arquivo
        self._lock = threading.RLock()
        self.tarefas = self.carregar_tarefas()

    def carregar_tarefas(self) -> list[dict]:
        """Lê as tarefas do arquivo JSON local (inicia vazio, sem nenhuma automação pré-fixada)."""
        with self._lock:
            if not os.path.exists(self.caminho_arquivo):
                self.salvar_tarefas([])
                return []
            try:
                with open(self.caminho_arquivo, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                    if isinstance(dados, list):
                        self.tarefas = dados
                        return dados
            except Exception:
                pass
            return []

    def salvar_tarefas(self, dados: list[dict] | None = None) -> None:
        """Persiste as tarefas atualizadas no JSON."""
        with self._lock:
            if dados is not None:
                self.tarefas = dados
            with open(self.caminho_arquivo, "w", encoding="utf-8") as f:
                json.dump(self.tarefas, f, indent=2, ensure_ascii=False)

    def criar_ou_atualizar_agendamento(
        self,
        nome: str,
        remetente_ou_busca: str,
        pasta_drive: str,
        horario: str = "08:00",
        frequencia: str = "diario",
        tipo_arquivo: str = "pdf",
        organizar_por_ano_mes: bool = True,
        marcar_como_lido: bool = True,
        ativo: bool = True,
        id_tarefa: str = "",
        senha_anexo: str = "",
    ) -> dict:
        """Cria uma nova automação ou atualiza uma existente."""
        freq_norm = (frequencia or "diario").strip().lower()
        if "hora" in freq_norm:
            freq_norm = "hora_em_hora"
        else:
            freq_norm = "diario"

        horario_limpo = (horario or "08:00").strip()
        if len(horario_limpo) == 4 and ":" in horario_limpo:
            horario_limpo = "0" + horario_limpo

        senha_limpa = str(senha_anexo or "").strip()

        with self._lock:
            self.carregar_tarefas()
            if id_tarefa:
                for t in self.tarefas:
                    if t.get("id") == id_tarefa:
                        if t.get("horario") != horario_limpo:
                            t["ultima_execucao_data"] = ""
                        t.update(
                            {
                                "nome": nome or t.get("nome", "Automação"),
                                "remetente_ou_busca": remetente_ou_busca or t.get("remetente_ou_busca", ""),
                                "pasta_drive": pasta_drive or t.get("pasta_drive", ""),
                                "tipo_arquivo": tipo_arquivo or t.get("tipo_arquivo", "pdf"),
                                "organizar_por_ano_mes": bool(organizar_por_ano_mes),
                                "marcar_como_lido": bool(marcar_como_lido),
                                "frequencia": freq_norm,
                                "horario": horario_limpo,
                                "ativo": bool(ativo),
                                "senha_anexo": senha_limpa,
                            }
                        )
                        self.salvar_tarefas()
                        return dict(t)

            novo_id = id_tarefa or f"auto-{uuid.uuid4().hex[:8]}"
            nova = {
                "id": novo_id,
                "nome": (nome or f"Automação {remetente_ou_busca}").strip(),
                "remetente_ou_busca": remetente_ou_busca.strip(),
                "pasta_drive": pasta_drive.strip(),
                "tipo_arquivo": (tipo_arquivo or "pdf").strip().lower(),
                "organizar_por_ano_mes": bool(organizar_por_ano_mes),
                "marcar_como_lido": bool(marcar_como_lido),
                "frequencia": freq_norm,
                "horario": horario_limpo,
                "ativo": bool(ativo),
                "senha_anexo": senha_limpa,
                "criado_em_ts": time.time(),
                "ids_processados": [],
                "ultima_execucao_data": "",
                "ultima_execucao_ts": 0.0,
                "ultimo_resultado": "Aguardando execução",
            }
            self.tarefas.append(nova)
            self.salvar_tarefas()
            return dict(nova)

    def excluir_agendamento(self, id_ou_nome: str) -> bool:
        termo = (id_ou_nome or "").strip().lower()
        if not termo:
            return False
        with self._lock:
            self.carregar_tarefas()
            antes = len(self.tarefas)
            self.tarefas = [
                t
                for t in self.tarefas
                if t.get("id", "").lower() != termo
                and termo not in t.get("nome", "").lower()
            ]
            if len(self.tarefas) < antes:
                self.salvar_tarefas()
                return True
            return False

    def alternar_ativo(self, id_tarefa: str, ativo: bool | None = None) -> dict | None:
        with self._lock:
            self.carregar_tarefas()
            for t in self.tarefas:
                if t.get("id") == id_tarefa:
                    t["ativo"] = (not t.get("ativo", True)) if ativo is None else bool(ativo)
                    self.salvar_tarefas()
                    return dict(t)
        return None

    def listar_agendados(self) -> list[dict]:
        with self._lock:
            self.carregar_tarefas()
            return [dict(t) for t in self.tarefas]

    def executar_agora(self, id_ou_nome_tarefa: str = "") -> dict:
        """Executa imediatamente uma automação criada pelo usuário."""
        termo = (id_ou_nome_tarefa or "").strip().lower()
        tarefa_alvo = None

        with self._lock:
            self.carregar_tarefas()
            for t in self.tarefas:
                if (
                    t.get("id", "").lower() == termo
                    or (termo and termo in t.get("id", "").lower())
                    or (termo and termo in t.get("nome", "").lower())
                    or (termo and termo in t.get("remetente_ou_busca", "").lower())
                ):
                    tarefa_alvo = t
                    break
            if tarefa_alvo is None and len(self.tarefas) == 1:
                tarefa_alvo = self.tarefas[0]

        if not tarefa_alvo:
            return {
                "ok": False,
                "mensagem": (
                    f"Automação '{id_ou_nome_tarefa}' não encontrada. "
                    "Crie uma automação primeiro clicando em '+ Criar' no painel de Automações."
                ),
            }

        resultado = executar_rotina_automacao(tarefa_alvo)
        agora = datetime.now(FUSO_BRASILIA)
        with self._lock:
            self.carregar_tarefas()
            for t in self.tarefas:
                if t.get("id") == tarefa_alvo.get("id"):
                    t["ultima_execucao_data"] = agora.strftime("%Y-%m-%d")
                    t["ultima_execucao_ts"] = time.time()
                    if isinstance(resultado.get("ids_processados"), list):
                        t["ids_processados"] = resultado["ids_processados"]
                    t["ultimo_resultado"] = (
                        f"[{agora.strftime('%d/%m %H:%M')}] {resultado.get('mensagem', '')}"
                    )
                    break
            self.salvar_tarefas()

        return resultado

    def verificar_e_executar_pendentes(self) -> list[dict]:
        """Verifica se alguma rotina ativa atingiu o horário programado e a executa."""
        agora = datetime.now(FUSO_BRASILIA)
        data_hoje = agora.strftime("%Y-%m-%d")
        hora_atual = agora.strftime("%H:%M")
        ts_atual = time.time()

        resultados_disparados: list[dict] = []

        with self._lock:
            self.carregar_tarefas()
            tarefas_ativas = [dict(t) for t in self.tarefas if t.get("ativo", True)]

        for t in tarefas_ativas:
            freq = t.get("frequencia", "diario")
            horario_alvo = t.get("horario", "08:00")
            ultima_data = t.get("ultima_execucao_data", "")
            ultimo_ts = float(t.get("ultima_execucao_ts") or 0.0)

            deve_rodar = False
            if freq == "diario":
                if hora_atual >= horario_alvo and ultima_data != data_hoje:
                    deve_rodar = True
            elif freq == "hora_em_hora":
                if (ts_atual - ultimo_ts) >= 3600.0:
                    deve_rodar = True

            if deve_rodar:
                res = executar_rotina_automacao(t)
                with self._lock:
                    self.carregar_tarefas()
                    for item in self.tarefas:
                        if item.get("id") == t.get("id"):
                            item["ultima_execucao_data"] = data_hoje
                            item["ultima_execucao_ts"] = ts_atual
                            if isinstance(res.get("ids_processados"), list):
                                item["ids_processados"] = res["ids_processados"]
                            item["ultimo_resultado"] = (
                                f"[{agora.strftime('%d/%m %H:%M')}] {res.get('mensagem', '')}"
                            )
                            break
                    self.salvar_tarefas()
                resultados_disparados.append(
                    {
                        "tarefa": t.get("nome", t.get("id")),
                        **res,
                    }
                )

        return resultados_disparados


gerenciador_automacoes = GerenciadorAgendamentosJARVIS()
