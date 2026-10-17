from dotenv import load_dotenv
import os
import shutil
import asyncio
import subprocess
import uuid
import json
import logging
from logging.handlers import RotatingFileHandler

import chromadb
from google import genai

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

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                voice="Charon",
                temperature=0.6,
            ),
        )

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
        return None

    # --- INÍCIO DAS FERRAMENTAS DO JARVIS ---

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

    @function_tool(description="Busca informacoes do passado na memoria de longo prazo.")
    async def buscar_na_memoria(self, assunto_ou_pergunta: str):
        logger_memoria.info("evento=buscar status=iniciado")
        try:
            quantidade_memorias = colecao_memoria.count()
            if quantidade_memorias == 0:
                logger_memoria.info("evento=buscar status=sem_resultados resultados=0")
                return "Nenhuma lembrança encontrada sobre esse assunto na memória."

            resultados = colecao_memoria.query(
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

    @function_tool(description="Abre um endereço web no Chrome padrão; reutiliza a aba da Jarvis quando a extensão está ativa.")
    async def abrir_site(self, url: str, nome_site: str):
        print(f"[AÇÃO] O Jarvis está abrindo o site: {nome_site}")
        try:
            resultado = await asyncio.to_thread(jarvis_chrome.open_url, url)
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

    @function_tool(description="Fecha um programa em execucao no computador.")
    async def fechar_programa(self, nome_programa: str):
        nome_limpo = nome_programa.lower().replace(".exe", "").replace("o programa", "").replace("aplicativo", "").strip()
        
        dicionario = {
            "google chrome": "chrome", "bloco de notas": "notepad",
            "calculadora": "calculator", "vs code": "code", "word": "winword", "excel": "excel"
        }
        for chave, valor in dicionario.items():
            if chave in nome_limpo:
                nome_limpo = valor
                break

        print(f"[AÇÃO] O Jarvis está forçando o fechamento do programa: '{nome_limpo}'")
        try:
            nome_powershell = nome_limpo.replace("'", "''")
            ps_command = f"""
            $nome = '{nome_powershell}'
            $padrao = [regex]::Escape($nome)
            Get-Process | Where-Object {{ $_.ProcessName -match $padrao -or $_.MainWindowTitle -match $padrao }} | Stop-Process -Force
            """
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command], capture_output=True)
            await asyncio.sleep(0.5)
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

    @function_tool(description="Fecha uma pasta que esta aberta no Windows.")
    async def fechar_pasta(self, nome_pasta: str):
        nome_limpo = nome_pasta.lower().replace("fechar", "").replace("fecha", "").replace("pasta", "").replace(" a ", "").replace(" o ", "").strip()
        
        if "trabalho" in nome_limpo or "desktop" in nome_limpo:
            nome_limpo = "desktop"
            
        print(f"[AÇÃO] O Jarvis está tentando fechar a janela relacionada a: '{nome_limpo}'")
        
        try:
            nome_powershell = nome_limpo.replace("'", "''")
            ps_command = f'''
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
            if (-not $fechou) {{
                Write-Output "FALHA_AO_FECHAR"
            }}
            '''
            result = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_command], capture_output=True, text=True)
            await asyncio.sleep(0.5)
            
            if "FALHA_AO_FECHAR" in result.stdout:
                return f"Aviso: O Windows não encontrou nenhuma pasta aberta com esse nome."
                
            return f"Ação executada com SUCESSO."
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


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession()
    activity_events: asyncio.Queue[ToolExecutionUpdatedEvent | None] = asyncio.Queue()
    session.on("tool_execution_updated", activity_events.put_nowait)
    session.on("close", lambda _: activity_events.put_nowait(None))
    asyncio.create_task(_publish_tool_activity(ctx.room, activity_events))

    await session.start( 
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    await session.generate_reply( 
        instructions=SESSION_INSTRUCTION + "\nCumprimente o usuário de forma breve e confiante."
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))