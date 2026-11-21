import os
import sys
import json
import asyncio
import subprocess
import logging
from datetime import datetime
from typing import Callable, Any, Dict, List, Optional
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("JarvisAntigravity")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s - [AntigravityEngine] - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Caminho raiz do projeto Jarvis
RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(RAIZ_PROJETO, ".env"))

_ARQUIVOS_MODIFICADOS_SESSAO: List[str] = []

def _resolver_caminho(caminho: str) -> str:
    c = (caminho or "").strip()
    c = os.path.expandvars(os.path.expanduser(c)).replace("/", os.sep).replace("\\", os.sep)
    if os.path.isabs(c):
        return os.path.normpath(c)
    return os.path.normpath(os.path.join(RAIZ_PROJETO, c))

def _obter_caminho_exibicao(abs_path: str) -> str:
    try:
        rel = os.path.relpath(abs_path, RAIZ_PROJETO)
        if not rel.startswith(".."):
            return rel
        return abs_path
    except Exception:
        return abs_path

# --- FERRAMENTAS STANDALONE (Sem referências a instâncias com threads/locks) ---

def tool_read_file(caminho: str, linha_inicio: int = 1, quantidade_linhas: int = 200) -> str:
    """Lê o conteúdo de um arquivo do projeto ou de qualquer local do PC com número de linhas.
    Aceita caminhos relativos ao projeto Jarvis ou caminhos absolutos no Windows (ex: C:\\Users\\Usuario\\Desktop\\...)."""
    abs_path = _resolver_caminho(caminho)
    if not os.path.isfile(abs_path):
        return f"ERRO: Arquivo '{caminho}' não existe."
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
        total = len(linhas)
        inicio = max(1, linha_inicio or 1)
        qtd = max(1, min(1000, quantidade_linhas or 200))
        fim = min(total, inicio + qtd - 1)
        trecho = [f"{i}: {linhas[i-1]}" for i in range(inicio, fim + 1)]
        exib = _obter_caminho_exibicao(abs_path)
        return f"=== Arquivo: {exib} (Linhas {inicio} a {fim} de {total}) ===\n" + "".join(trecho)
    except Exception as e:
        return f"ERRO ao ler '{caminho}': {e}"

def tool_write_file(caminho: str, conteudo: str) -> str:
    """Cria ou sobrescreve completamente um arquivo em qualquer pasta do PC ou do projeto Jarvis.
    Aceita caminhos relativos ao projeto ou caminhos absolutos no Windows (ex: C:\\Users\\Usuario\\Desktop\\teste.py)."""
    abs_path = _resolver_caminho(caminho)
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(conteudo)
        exib = _obter_caminho_exibicao(abs_path)
        if exib not in _ARQUIVOS_MODIFICADOS_SESSAO:
            _ARQUIVOS_MODIFICADOS_SESSAO.append(exib)
        return f"SUCESSO: Arquivo '{exib}' gravado com sucesso ({len(conteudo)} caracteres)."
    except Exception as e:
        return f"ERRO ao gravar '{caminho}': {e}"

def tool_edit_file(caminho: str, alvo: str, substituicao: str) -> str:
    """
    Substituição cirúrgica no arquivo em qualquer pasta do computador ou projeto. O texto 'alvo' precisa coincidir exatamente
    com o trecho existente no arquivo.
    """
    abs_path = _resolver_caminho(caminho)
    if not os.path.isfile(abs_path):
        return f"ERRO: Arquivo '{caminho}' não existe."
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            conteudo = f.read()

        if alvo not in conteudo:
            alvo_norm = alvo.replace("\r\n", "\n")
            conteudo_norm = conteudo.replace("\r\n", "\n")
            if alvo_norm in conteudo_norm:
                subst_norm = substituicao.replace("\r\n", "\n")
                novo_conteudo = conteudo_norm.replace(alvo_norm, subst_norm, 1)
            else:
                return (
                    f"ERRO: O trecho 'alvo' não foi encontrado no arquivo '{caminho}'. "
                    f"Leia o arquivo primeiro para copiar o trecho com a indentação exata."
                )
        else:
            novo_conteudo = conteudo.replace(alvo, substituicao, 1)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(novo_conteudo)

        exib = _obter_caminho_exibicao(abs_path)
        if exib not in _ARQUIVOS_MODIFICADOS_SESSAO:
            _ARQUIVOS_MODIFICADOS_SESSAO.append(exib)
        return f"SUCESSO: Trecho substituído com sucesso no arquivo '{exib}'."
    except Exception as e:
        return f"ERRO ao editar '{caminho}': {e}"

def tool_run_command(comando: str, diretorio: str = "") -> str:
    """Executa um comando no terminal PowerShell no Windows com acesso total ao computador.
    Pode executar comandos de sistema, scripts, compilações, inspeção de processos, etc.
    O argumento opcional 'diretorio' define a pasta de execução (padrão: raiz do projeto Jarvis)."""
    cwd = _resolver_caminho(diretorio) if diretorio else RAIZ_PROJETO
    if not os.path.isdir(cwd):
        cwd = RAIZ_PROJETO
    try:
        res = subprocess.run(
            comando,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        resultado = []
        if out:
            resultado.append(f"STDOUT:\n{out[:3000]}")
        if err:
            resultado.append(f"STDERR:\n{err[:3000]}")
        if not out and not err:
            resultado.append("(Comando concluído sem saída de texto)")
        resultado.append(f"[Código de saída: {res.returncode}]")
        return "\n".join(resultado)
    except subprocess.TimeoutExpired:
        return "ERRO: Comando expirou tempo limite de 60s e foi interrompido."
    except Exception as e:
        return f"ERRO ao executar comando: {e}"

def tool_list_dir(subpasta: str = "") -> str:
    """Lista arquivos e pastas de qualquer diretório do computador ou do projeto (ex: C:\\, C:\\Users\\Usuario\\Desktop, etc)."""
    abs_path = _resolver_caminho(subpasta)
    if not os.path.isdir(abs_path):
        return f"ERRO: Diretório '{subpasta}' não encontrado."

    ignorar = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build"}
    itens = []
    try:
        for item in os.listdir(abs_path):
            if item in ignorar:
                continue
            full = os.path.join(abs_path, item)
            itens.append(f"{'[DIR] ' if os.path.isdir(full) else '[ARQ] '} {item}")
        return f"Conteúdo de {abs_path}:\n" + "\n".join(sorted(itens))
    except Exception as e:
        return f"ERRO ao listar diretório '{abs_path}': {e}"

def tool_search_code(termo: str, subpasta: str = "") -> str:
    """Pesquisa termos, funções ou variáveis nos arquivos do projeto."""
    t_clean = termo.strip().lower()
    if not t_clean or len(t_clean) < 2:
        return "ERRO: Termo muito curto para busca."

    base = _resolver_caminho(subpasta)
    ignorar = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "bin"}
    exts = {".py", ".tsx", ".ts", ".js", ".json", ".css", ".html", ".md"}
    resultados = []

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ignorar]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in exts:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, RAIZ_PROJETO)
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        for num, linha in enumerate(f, start=1):
                            if t_clean in linha.lower():
                                resultados.append(f"{rel}:{num} -> {linha.strip()[:100]}")
                                if len(resultados) >= 25:
                                    break
                except Exception:
                    pass
            if len(resultados) >= 25:
                break
        if len(resultados) >= 25:
            break

    if not resultados:
        return f"Nenhuma ocorrência encontrada para '{termo}'."
    return f"Ocorrências de '{termo}':\n" + "\n".join(resultados)

ALL_ANTIGRAVITY_TOOLS = [
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_run_command,
    tool_list_dir,
    tool_search_code,
]


class AntigravityEngine:
    """
    Motor Autônomo Antigravity integrado ao Jarvis.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GOOGLE_API_KEY ou GEMINI_API_KEY não encontrada no ambiente!")
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.estado_atual = "idle"
        self.tarefa_atual: Optional[str] = None
        self.historico_passos: List[Dict[str, Any]] = []
        self._cancelar = False

    async def executar_tarefa(
        self,
        prompt_tarefa: str,
        arquivo_alvo: str = "",
        max_passos: int = 15,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        global _ARQUIVOS_MODIFICADOS_SESSAO
        _ARQUIVOS_MODIFICADOS_SESSAO = []

        if not self.client:
            self.api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                return {
                    "sucesso": False,
                    "resumo": "Chave de API do Gemini (GOOGLE_API_KEY) não encontrada no ambiente.",
                    "arquivos_alterados": [],
                    "passos_executados": 0,
                }
            self.client = genai.Client(api_key=self.api_key)

        self.tarefa_atual = prompt_tarefa
        self.historico_passos = []
        self.estado_atual = "pensando"
        self._cancelar = False

        async def emitir_evento(tipo: str, dados: Dict[str, Any]):
            evento = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "tipo": tipo,
                **dados,
            }
            self.historico_passos.append(evento)
            if on_event:
                try:
                    if asyncio.iscoroutinefunction(on_event):
                        await on_event(evento)
                    else:
                        on_event(evento)
                except Exception as e:
                    logger.warning(f"Erro ao emitir evento de status: {e}")

        await emitir_evento("inicio", {
            "mensagem": f"Iniciando Antigravity: {prompt_tarefa}",
            "arquivo_alvo": arquivo_alvo,
        })

        system_instruction = (
            "Você é o Motor Antigravity Autônomo com ACESSO TOTAL AO COMPUTADOR do usuário, integrado diretamente ao Jarvis.\n"
            "O USUÁRIO CONCEDEU PERMISSÃO TOTAL E IRRESTRITA PARA VOCÊ OPERAR EM TODO O SISTEMA OPERACIONAL (WINDOWS).\n\n"
            "SUAS CAPACIDADES E ALCANCE:\n"
            "1. ACESSO COMPLETO AO PC: Você tem visibilidade e autorização irrestrita de leitura, escrita, criação, edição e exclusão em qualquer diretório ou arquivo do computador "
            "(como Desktop/Área de Trabalho, Downloads, Documentos, todas as unidades de disco como C:\\, D:\\, pastas de outros projetos, etc.) através de caminhos absolutos ou relativos.\n"
            "2. FERRAMENTAS DISPONÍVEIS:\n"
            "   - `tool_read_file`: Lê qualquer arquivo de código ou texto no computador com números de linha.\n"
            "   - `tool_write_file`: Cria ou sobrescreve arquivos em qualquer pasta do PC.\n"
            "   - `tool_edit_file`: Edita com precisão cirúrgica trechos de código ou texto em qualquer arquivo.\n"
            "   - `tool_run_command`: Terminal PowerShell irrestrito no Windows. Pode compilar, instalar pacotes, executar scripts, consultar o sistema, gerenciar processos, navegar pastas, etc.\n"
            "   - `tool_list_dir`: Lista qualquer pasta do computador (ex: 'C:\\', 'C:\\Users\\Usuario\\Desktop', etc.).\n"
            "   - `tool_search_code`: Busca termos e funções em qualquer pasta do PC ou projeto.\n\n"
            "DIRETRIZES DE COMPORTAMENTO:\n"
            "- Quando o usuário perguntar se você enxerga o computador ou o projeto, responda com total clareza e segurança confirmando que tem ACESSO TOTAL ao computador e ao projeto Jarvis, e que pode operar em qualquer pasta ou executar qualquer comando no terminal que ele solicitar.\n"
            "- Sempre que for criar ou alterar código ou arquivos, use as ferramentas diretamente e reporte o resultado de forma clara, objetiva e em português (pt-BR).\n"
            "- Seja proativo, rápido e execute o que for pedido sem hesitação."
        )

        modelos_candidatos = [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-flash",
        ]

        chat = None
        for m in modelos_candidatos:
            try:
                chat = self.client.chats.create(
                    model=m,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=ALL_ANTIGRAVITY_TOOLS,
                        temperature=0.2,
                    ),
                )
                logger.info(f"Antigravity conectado com sucesso usando o modelo: {m}")
                break
            except Exception as e:
                logger.warning(f"Falha ao iniciar chat com modelo {m}: {e}")

        if not chat:
            await emitir_evento("erro", {"mensagem": "Não foi possível conectar a nenhum modelo Gemini."})
            return {
                "sucesso": False,
                "resumo": "Falha na inicialização do modelo Gemini.",
                "arquivos_alterados": [],
                "passos_executados": 0,
            }

        prompt_inicial = prompt_tarefa
        if arquivo_alvo:
            prompt_inicial += f"\n[Arquivo atualmente em foco no editor: {arquivo_alvo}]"

        passo = 0
        resposta_final = ""
        mensagem_envio = prompt_inicial

        while passo < max_passos and not self._cancelar:
            passo += 1
            self.estado_atual = "pensando"
            await emitir_evento("pensando", {"passo": passo, "mensagem": f"Passo {passo}/{max_passos}: Analisando..."})

            try:
                # Chama o send_message sem pickle issues
                resposta = await asyncio.to_thread(chat.send_message, mensagem_envio)
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem para Gemini: {e}")
                await emitir_evento("erro", {"passo": passo, "mensagem": f"Erro de comunicação: {e}"})
                return {
                    "sucesso": False,
                    "resumo": f"Erro na iteração {passo}: {e}",
                    "arquivos_alterados": list(_ARQUIVOS_MODIFICADOS_SESSAO),
                    "passos_executados": passo,
                }

            function_calls = resposta.function_calls
            if not function_calls:
                resposta_final = resposta.text or "Processamento concluído."
                self.estado_atual = "concluido"
                await emitir_evento("concluido", {
                    "passo": passo,
                    "resumo": resposta_final,
                    "arquivos_alterados": list(_ARQUIVOS_MODIFICADOS_SESSAO),
                })
                break

            tool_responses = []
            for call in function_calls:
                nome_func = call.name
                args = call.args or {}
                self.estado_atual = "executando_ferramenta"
                await emitir_evento("ferramenta_inicio", {
                    "passo": passo,
                    "ferramenta": nome_func,
                    "args": args,
                })

                resultado_str = ""
                try:
                    if nome_func == "tool_read_file":
                        resultado_str = tool_read_file(
                            caminho=args.get("caminho", ""),
                            linha_inicio=int(args.get("linha_inicio", 1)),
                            quantidade_linhas=int(args.get("quantidade_linhas", 200)),
                        )
                    elif nome_func == "tool_write_file":
                        resultado_str = tool_write_file(
                            caminho=args.get("caminho", ""),
                            conteudo=args.get("conteudo", ""),
                        )
                    elif nome_func == "tool_edit_file":
                        resultado_str = tool_edit_file(
                            caminho=args.get("caminho", ""),
                            alvo=args.get("alvo", ""),
                            substituicao=args.get("substituicao", ""),
                        )
                    elif nome_func == "tool_run_command":
                        cmd = args.get("comando", "")
                        dir_cmd = args.get("diretorio", "")
                        resultado_str = tool_run_command(cmd, dir_cmd)
                    elif nome_func == "tool_list_dir":
                        resultado_str = tool_list_dir(subpasta=args.get("subpasta", ""))
                    elif nome_func == "tool_search_code":
                        resultado_str = tool_search_code(
                            termo=args.get("termo", ""),
                            subpasta=args.get("subpasta", ""),
                        )
                    else:
                        resultado_str = f"ERRO: Ferramenta desconhecida '{nome_func}'."
                except Exception as ex_tool:
                    resultado_str = f"ERRO ao executar '{nome_func}': {ex_tool}"

                await emitir_evento("ferramenta_fim", {
                    "passo": passo,
                    "ferramenta": nome_func,
                    "resultado": resultado_str[:500] + ("..." if len(resultado_str) > 500 else ""),
                })

                tool_responses.append(
                    genai_types.Part.from_function_response(
                        name=nome_func,
                        response={"result": resultado_str},
                    )
                )

            mensagem_envio = tool_responses

        if passo >= max_passos and self.estado_atual != "concluido":
            resposta_final = "Limite máximo de passos atingido."

        return {
            "sucesso": self.estado_atual == "concluido",
            "resumo": resposta_final,
            "arquivos_alterados": list(_ARQUIVOS_MODIFICADOS_SESSAO),
            "passos_executados": passo,
            "historico": self.historico_passos,
        }

    @staticmethod
    def atualizar_status_fila(id_tarefa: int, status: str, detalhes_resolucao: str = "") -> bool:
        caminho_json = os.path.join(RAIZ_PROJETO, "solicitacoes_antigravity.json")
        caminho_md = os.path.join(RAIZ_PROJETO, "SOLICITACOES_DESENVOLVIMENTO.md")
        if not os.path.isfile(caminho_json):
            return False

        try:
            with open(caminho_json, "r", encoding="utf-8") as f:
                tarefas = json.load(f)

            atualizado = False
            for t in tarefas:
                if t.get("id") == id_tarefa:
                    t["status"] = status
                    if detalhes_resolucao:
                        t["resolucao"] = detalhes_resolucao
                        t["resolvido_em"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    atualizado = True
                    break

            if atualizado:
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
                    st = t.get("status", "pendente")
                    emoji = "🟢" if st == "concluida" else ("🔵" if st == "em_progresso" else "🟡")
                    linhas_md.append(
                        f"| #{t['id']} | {t['data_hora']} | **{t['titulo']}** | `{t['arquivo_sugerido']}` | {t.get('prioridade', 'NORMAL').upper()} | {emoji} {st} |"
                    )

                with open(caminho_md, "w", encoding="utf-8") as f:
                    f.write("\n".join(linhas_md))

                return True
        except Exception as e:
            logger.error(f"Erro ao atualizar status da fila: {e}")
        return False


_antigravity_engine_instance: Optional[AntigravityEngine] = None

def obter_antigravity_engine() -> AntigravityEngine:
    global _antigravity_engine_instance
    if _antigravity_engine_instance is None:
        _antigravity_engine_instance = AntigravityEngine()
    return _antigravity_engine_instance


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=str, required=True)
    parser.add_argument("--target", type=str, default="")
    args = parser.parse_args()

    engine = obter_antigravity_engine()
    res = asyncio.run(engine.executar_tarefa(args.prompt, args.target))
    print(json.dumps(res, ensure_ascii=False))
