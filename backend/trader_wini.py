"""Módulo Trader de Mini Índice (WIN/B3) do Jarvis.

Reúne: base de conhecimento (livros/técnicas via RAG), painel de mercado/pré-mercado,
leitura da tela do Profit por visão computacional, gestão de risco, execução de ordens
com confirmação, monitoramento contínuo, guia de configuração do Profit e criação de
indicadores NTSL.

Segurança por padrão: modo SIMULADOR, confirmação obrigatória por voz antes de enviar
ordem, stop obrigatório, limites diários de perda/contratos/operações.
"""

import asyncio
import ctypes
import hashlib
import json
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from google import genai
from google.genai import types as genai_types
from livekit.agents import function_tool
from PIL import Image

logger = logging.getLogger("jarvis.memoria")

try:
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=-3))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA_BIBLIOTECA = os.path.join(BASE_DIR, "Biblioteca_Trader")
PASTA_INDICADORES = os.path.join(BASE_DIR, "Indicadores_NTSL")
DB_BIBLIOTECA = os.path.join(BASE_DIR, "biblioteca_trader_db")
ARQ_CONFIG = os.path.join(BASE_DIR, "trader_config.json")
ARQ_ESTADO = os.path.join(BASE_DIR, "trader_estado.json")
ARQ_DIARIO = os.path.join(BASE_DIR, "Diario_Trader.jsonl")
ARQ_GUIA_PROFIT = os.path.join(PASTA_BIBLIOTECA, "01_Guia_Configuracao_Profit.md")

MODELOS_VISAO = ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest")
VALOR_PONTO_WIN = 0.20  # R$ por ponto por contrato (tick de 5 pontos = R$ 1,00)

CONFIG_PADRAO: dict = {
    "modo": "simulador",  # "simulador" (padrão seguro) ou "real"
    "max_contratos": 2,
    "risco_max_por_trade_reais": 60.0,
    "perda_max_dia_reais": 150.0,
    "max_trades_dia": 8,
    "stop_min_pontos": 40,
    "stop_max_pontos": 150,
    "rr_minimo": 1.0,
    "inicio_operacao": "09:05",
    "fim_operacao": "17:30",
    "validade_ordem_segundos": 120,
    "pausa_apos_stops_seguidos": 3,
    "pausa_minutos": 20,
}


# ----------------------------------------------------------------------------
# Utilitários de persistência
# ----------------------------------------------------------------------------
def _ler_json(caminho: str, padrao):
    try:
        if os.path.isfile(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
            if isinstance(dados, type(padrao)):
                return dados
    except Exception as erro:
        logger.warning("evento=trader_ler_json status=erro arquivo=%s motivo=%s", caminho, erro)
    return padrao


def _gravar_json(caminho: str, dados) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def carregar_config() -> dict:
    cfg = dict(CONFIG_PADRAO)
    cfg.update(_ler_json(ARQ_CONFIG, {}))
    if not os.path.isfile(ARQ_CONFIG):
        _gravar_json(ARQ_CONFIG, cfg)
    return cfg


def carregar_estado() -> dict:
    hoje = datetime.now(TZ).strftime("%Y-%m-%d")
    estado = _ler_json(ARQ_ESTADO, {})
    if estado.get("data") != hoje:
        estado = {
            "data": hoje,
            "trades": 0,
            "resultado_reais": 0.0,
            "stops_seguidos": 0,
            "pausa_ate": 0.0,
            "travado": False,
            "motivo_trava": "",
            "ordem_pendente": None,
        }
    return estado


def _diario(tipo: str, dados: dict) -> None:
    try:
        registro = {"quando": datetime.now(TZ).isoformat(timespec="seconds"), "tipo": tipo, **dados}
        with open(ARQ_DIARIO, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception as erro:
        logger.warning("evento=trader_diario status=erro motivo=%s", erro)


# ----------------------------------------------------------------------------
# Contrato vigente do WIN
# ----------------------------------------------------------------------------
_LETRAS_VENC = {2: "G", 4: "J", 6: "M", 8: "Q", 10: "V", 12: "Z"}


def _quarta_proxima_do_dia_15(ano: int, mes: int) -> date:
    d = date(ano, mes, 15)
    delta = 2 - d.weekday()
    if delta < -3:
        delta += 7
    return d + timedelta(days=delta)


def contrato_vigente(hoje: date | None = None) -> tuple[str, date | None]:
    hoje = hoje or datetime.now(TZ).date()
    ano, mes = hoje.year, hoje.month
    for _ in range(14):
        if mes in _LETRAS_VENC:
            venc = _quarta_proxima_do_dia_15(ano, mes)
            if hoje <= venc:
                return f"WIN{_LETRAS_VENC[mes]}{str(ano)[-2:]}", venc
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    return "WINFUT", None


# ----------------------------------------------------------------------------
# Dados de mercado (Yahoo Finance + Google News RSS: gratuitos, sem chave)
# ----------------------------------------------------------------------------
_HEADERS_WEB = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

ALIASES_MERCADO = {
    "ewz": "EWZ", "s&p": "ES=F", "s&p 500": "ES=F", "sp500": "ES=F", "es": "ES=F", "spx": "^GSPC",
    "nasdaq": "NQ=F", "nq": "NQ=F", "dow": "YM=F", "dow jones": "YM=F", "russell": "RTY=F",
    "vix": "^VIX", "dxy": "DX-Y.NYB", "dolar index": "DX-Y.NYB", "dólar index": "DX-Y.NYB",
    "dolar": "USDBRL=X", "dólar": "USDBRL=X", "usdbrl": "USDBRL=X", "euro": "EURUSD=X",
    "brent": "BZ=F", "petroleo": "CL=F", "petróleo": "CL=F", "wti": "CL=F", "ouro": "GC=F",
    "cobre": "HG=F", "treasury": "^TNX", "juros eua": "^TNX", "t10": "^TNX", "t10y": "^TNX",
    "vale": "VALE", "petrobras": "PBR", "itau": "ITUB", "itaú": "ITUB", "bradesco": "BBD",
    "ibovespa": "^BVSP", "ibov": "^BVSP", "china": "FXI", "bitcoin": "BTC-USD",
}

# símbolo, rótulo, peso, limiar de variação (%) para pontuação cheia, invertido?
PAINEL_PRE_MERCADO = [
    ("EWZ", "EWZ (Brasil em NY)", 2.0, 0.6, False),
    ("ES=F", "S&P 500 futuro", 1.5, 0.35, False),
    ("NQ=F", "Nasdaq futuro", 0.5, 0.5, False),
    ("^VIX", "VIX (índice do medo)", 1.0, 6.0, True),
    ("DX-Y.NYB", "Dólar global (DXY)", 1.0, 0.3, True),
    ("USDBRL=X", "Dólar/Real", 1.5, 0.6, True),
    ("^TNX", "Juro 10 anos EUA", 0.5, 2.0, True),
    ("BZ=F", "Petróleo Brent", 0.5, 1.2, False),
    ("VALE", "Vale (ADR)", 1.0, 1.2, False),
    ("PBR", "Petrobras (ADR)", 1.0, 1.2, False),
    ("ITUB", "Itaú (ADR)", 0.5, 1.0, False),
]


def _resolver_simbolo(texto: str) -> str:
    chave = (texto or "").strip().lower()
    return ALIASES_MERCADO.get(chave, (texto or "").strip().upper())


async def _cotacao_yahoo(client: httpx.AsyncClient, simbolo: str) -> dict:
    try:
        resp = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}",
            params={"interval": "5m", "range": "1d", "includePrePost": "true"},
        )
        resp.raise_for_status()
        resultado = resp.json()["chart"]["result"][0]
        meta = resultado.get("meta", {})
        quote = (resultado.get("indicators", {}).get("quote") or [{}])[0]
        fechamentos = quote.get("close") or []
        tempos = resultado.get("timestamp") or []
        preco, ts = None, None
        for valor, instante in zip(reversed(fechamentos), reversed(tempos)):
            if valor is not None:
                preco, ts = valor, instante
                break
        if preco is None:
            preco, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        anterior = meta.get("chartPreviousClose") or meta.get("previousClose")
        variacao = (preco / anterior - 1) * 100 if preco and anterior else None
        hora = datetime.fromtimestamp(ts, TZ).strftime("%d/%m %H:%M") if ts else "?"
        return {
            "simbolo": simbolo,
            "nome": meta.get("shortName") or simbolo,
            "preco": preco,
            "anterior": anterior,
            "var_pct": variacao,
            "hora": hora,
        }
    except Exception as erro:
        return {"simbolo": simbolo, "erro": str(erro)[:90]}


def _fmt_cotacao(c: dict, rotulo: str = "") -> str:
    nome = rotulo or c.get("nome") or c.get("simbolo")
    if c.get("erro") or c.get("preco") is None:
        return f"- {nome} ({c.get('simbolo')}): indisponível ({c.get('erro', 'sem dados')})"
    var = c.get("var_pct")
    var_txt = f"{var:+.2f}%" if var is not None else "n/d"
    return f"- {nome}: {c['preco']:.2f} ({var_txt}) [dado de {c['hora']}]"


async def _noticias_google(client: httpx.AsyncClient, consulta: str, limite: int = 6) -> list[str]:
    try:
        resp = await client.get(
            "https://news.google.com/rss/search",
            params={"q": f"{consulta} when:1d", "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"},
        )
        resp.raise_for_status()
        raiz = ET.fromstring(resp.content)
        itens, vistos = [], set()
        for item in raiz.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            if not titulo or titulo in vistos:
                continue
            vistos.add(titulo)
            hora = ""
            try:
                hora = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(TZ).strftime("%d/%m %H:%M")
            except Exception:
                pass
            itens.append(f"- [{hora}] {titulo}")
            if len(itens) >= limite:
                break
        return itens
    except Exception as erro:
        return [f"- (falha ao buscar notícias: {str(erro)[:80]})"]


# ----------------------------------------------------------------------------
# Visão computacional / controle da tela
# ----------------------------------------------------------------------------
def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _visao(img: Image.Image, prompt: str, largura_max: int = 1920) -> str:
    imagem = img.copy()
    if imagem.width > largura_max:
        altura = int(imagem.height * largura_max / imagem.width)
        imagem = imagem.resize((largura_max, altura), Image.Resampling.LANCZOS)
    cliente = genai.Client(api_key=_api_key(), http_options=genai_types.HttpOptions(timeout=30_000))
    ultimo_erro = None
    for modelo in MODELOS_VISAO:
        try:
            resp = cliente.models.generate_content(model=modelo, contents=[imagem, prompt])
            if resp and resp.text:
                return resp.text.strip()
        except Exception as erro:
            ultimo_erro = erro
    return f"ERRO_VISAO: {ultimo_erro}"


def _extrair_json(texto: str):
    if not texto:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", texto, re.DOTALL)
    bruto = m.group(1) if m else None
    if bruto is None:
        ini, fim = texto.find("{"), texto.rfind("}")
        if ini == -1 or fim <= ini:
            return None
        bruto = texto[ini : fim + 1]
    try:
        return json.loads(bruto)
    except Exception:
        return None


_VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "delete": 0x2E, "backspace": 0x08,
    "f5": 0x74, "f9": 0x78, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
_TECLAS_LIVRES = {"enter", "tab", "esc", "delete", "backspace", "ctrl+a", "ctrl+v", "ctrl+s", "f5", "f9", "up", "down", "left", "right"}


def _pressionar_tecla(tecla: str) -> None:
    user32 = ctypes.windll.user32
    partes = tecla.lower().strip().split("+")
    codigos = []
    for parte in partes:
        if parte == "ctrl":
            codigos.append(0x11)
        elif len(parte) == 1 and parte.isalnum():
            codigos.append(ord(parte.upper()))
        elif parte in _VK:
            codigos.append(_VK[parte])
        else:
            raise ValueError(f"tecla não suportada: {tecla}")
    for vk in codigos:
        user32.keybd_event(vk, 0, 1 if 0x25 <= vk <= 0x28 else 0, 0)
        time.sleep(0.02)
    for vk in reversed(codigos):
        user32.keybd_event(vk, 0, 2 | (1 if 0x25 <= vk <= 0x28 else 0), 0)
        time.sleep(0.02)


def copiar_para_clipboard(texto: str) -> None:
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    dados = (texto + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(0x0002, len(dados))
    ponteiro = kernel32.GlobalLock(handle)
    ctypes.memmove(ponteiro, dados, len(dados))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        raise RuntimeError("não foi possível abrir a área de transferência")
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(13, handle)  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def _digitar_texto(texto: str) -> None:
    if texto and all(c.isdigit() or c == "-" for c in texto):
        user32 = ctypes.windll.user32
        for caractere in texto:
            vk = 0xBD if caractere == "-" else 0x30 + int(caractere)
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.015)
            user32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.015)
        return
    copiar_para_clipboard(texto)
    _pressionar_tecla("ctrl+v")


_REGEX_BOTAO_ORDEM = re.compile(
    r"\b(compr\w*|vend\w*|enviar|envio|executar|zerar|inverter|confirmar\s+ordem|boleta\s+flash)\b", re.IGNORECASE
)

PROMPT_LEITURA_PROFIT = """Você é um analista profissional de mini índice (WIN) lendo a tela da plataforma Profit (Nelogica).
Hora atual (Brasília): {agora}. Contrato vigente estimado: {contrato}.
{foco}
Descreva SOMENTE o que está realmente visível. Se algo estiver ilegível ou ausente, use null. NÃO invente valores.
Responda SOMENTE com JSON:
{{
 "conta": "simulador|real|desconhecida", "evidencia_conta": "texto visto na tela",
 "ativo": "...", "timeframe": "...", "preco_atual": null, "variacao_dia": null,
 "maxima_dia": null, "minima_dia": null, "abertura_dia": null,
 "tendencia": "alta|baixa|lateral + explicação curta",
 "estrutura_price_action": "topos/fundos, rompimentos, falhas, pullbacks, barras de força/fraqueza dos últimos candles",
 "vwap": "preço está acima/abaixo e distância em pontos",
 "medias_e_indicadores": ["nome: leitura"],
 "volume_e_fluxo": "volume dos últimos candles, agressão, saldo/delta se visível",
 "book_e_tape": "desequilíbrios, ordens grandes, absorção, velocidade do tape, se visível",
 "niveis_relevantes": ["preço: o que é (máx/mín dia, ajuste, VWAP, POC, número redondo...)"],
 "posicao_aberta": null, "ordens_abertas": null, "resultado_dia": null,
 "boleta_visivel": false,
 "cenario": "cenário provável nos próximos candles e o que o invalidaria",
 "qualidade_leitura": "boa|parcial|ruim e por quê"
}}"""

PROMPT_PRECHECK = """Analise a tela do Profit e responda SOMENTE com JSON:
{"conta":"simulador|real|desconhecida","evidencia_conta":"texto exato visto na tela","ativo":"ticker da boleta/gráfico principal","boleta_visivel":true,"preco_atual":null,"posicao_atual":"zerado|comprado N|vendido N|desconhecida","ordens_abertas":"nenhuma|descrição|desconhecida"}
Seja literal: só responda "simulador" se houver indicação explícita (ex.: 'Simulador', 'Conta simulada', 'Demo') visível na tela."""

PROMPT_PASSO = """Você opera o mouse e o teclado de um PC Windows com a plataforma Profit (Nelogica) para cumprir UM objetivo, um passo por vez.
OBJETIVO: {objetivo}
REGRAS: {regras}
PASSOS JÁ EXECUTADOS: {historico}
Observe a tela atual e escolha a PRÓXIMA ação. Responda SOMENTE com JSON:
{{"acao":"clicar|digitar|tecla|esperar|concluido|abortar","box_2d":[ymin,xmin,ymax,xmax],"clique":"esquerdo|duplo|direito","descricao":"elemento alvo","texto":"","substituir":false,"tecla":"","segundos":1,"motivo":"curto"}}
- clicar: box_2d (0-1000) do elemento alvo EXATO (botão, campo, menu, aba).
- digitar: texto a digitar no campo já focado (substituir=true seleciona tudo antes).
- tecla: uma de enter, tab, esc, delete, backspace, ctrl+a, ctrl+v, ctrl+s, f5, f9, up, down, left, right.
- concluido: SOMENTE quando o objetivo estiver cumprido e verificado na tela.
- abortar: se algo diferir do esperado (conta, ativo, valores, diálogo inesperado) ou se não achar o elemento."""


def _prompt_monitor(foco: str) -> str:
    return (
        "Você é um analista de mini índice (WIN) observando o Profit em tempo real para um trader. "
        f"Hora (Brasília): {datetime.now(TZ).strftime('%H:%M:%S')}. {('Foco do trader: ' + foco) if foco else ''}\n"
        "Decida se HÁ ALGO que mereça interromper o trader AGORA: setup completo (gatilho de price action + fluxo a favor), "
        "rompimento ou falha de nível relevante (VWAP, máx/mín do dia, ajuste, abertura), absorção/exaustão no fluxo, "
        "posição aberta perto do stop/alvo ou sem proteção, ou risco anormal. Se for apenas ruído, alerta=false.\n"
        'Responda SOMENTE JSON: {"alerta":false,"prioridade":"alta|media|baixa","titulo":"curto","leitura":"2 frases",'
        '"gatilho_sugerido":{"lado":"compra|venda","entrada":"condição/preço","stop_pontos":0,"alvo_pontos":0}}'
    )


# ----------------------------------------------------------------------------
# Biblioteca de conhecimento (RAG)
# ----------------------------------------------------------------------------
def _embed(textos: list[str], tarefa: str) -> list[list[float]]:
    cliente = genai.Client(api_key=_api_key())
    saida: list[list[float]] = []
    for i in range(0, len(textos), 64):
        lote = textos[i : i + 64]
        for tentativa in range(4):
            try:
                resp = cliente.models.embed_content(
                    model="gemini-embedding-001",
                    contents=lote,
                    config=genai_types.EmbedContentConfig(task_type=tarefa, output_dimensionality=768),
                )
                saida.extend([e.values for e in resp.embeddings])
                break
            except Exception:
                if tentativa == 3:
                    raise
                time.sleep(2 * (tentativa + 1))
    return saida


def _extrair_paginas(caminho: str) -> list[tuple[str, str]]:
    ext = os.path.splitext(caminho)[1].lower()
    paginas: list[tuple[str, str]] = []
    if ext == ".pdf":
        from pypdf import PdfReader

        leitor = PdfReader(caminho)
        for i, pagina in enumerate(leitor.pages, start=1):
            try:
                texto = pagina.extract_text() or ""
            except Exception:
                texto = ""
            if texto.strip():
                paginas.append((f"p.{i}", texto))
    elif ext in {".txt", ".md", ".csv"}:
        with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
            paginas.append(("", f.read()))
    elif ext == ".docx":
        import zipfile

        with zipfile.ZipFile(caminho) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        texto = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
        paginas.append(("", texto))
    return paginas


def _quebrar(texto: str, tamanho: int = 1300, sobreposicao: int = 200) -> list[str]:
    texto = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", texto)).strip()
    if len(texto) <= tamanho:
        return [texto] if len(texto) > 40 else []
    partes, ini = [], 0
    while ini < len(texto):
        fim = min(ini + tamanho, len(texto))
        if fim < len(texto):
            corte = max(texto.rfind("\n\n", ini, fim), texto.rfind(". ", ini, fim))
            if corte > ini + tamanho * 0.5:
                fim = corte + 1
        partes.append(texto[ini:fim].strip())
        if fim >= len(texto):
            break
        ini = max(fim - sobreposicao, ini + 1)
    return [p for p in partes if len(p) > 40]


class _ColecaoFallback:
    def __init__(self, caminho_json: str):
        self.caminho = caminho_json
        self.dados = self._carregar()

    def _carregar(self) -> list[dict]:
        if os.path.exists(self.caminho):
            try:
                with open(self.caminho, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _salvar(self):
        try:
            with open(self.caminho, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, ensure_ascii=False)
        except Exception:
            pass

    def count(self) -> int:
        return len(self.dados)

    def delete(self, where: dict):
        fonte = where.get("fonte")
        if fonte:
            self.dados = [d for d in self.dados if d.get("metadata", {}).get("fonte") != fonte]
            self._salvar()

    def add(self, ids: list[str], documents: list[str], embeddings: list[list[float]], metadatas: list[dict]):
        for doc_id, doc, emb, meta in zip(ids, documents, embeddings, metadatas):
            self.dados.append({
                "id": doc_id,
                "document": doc,
                "embedding": emb,
                "metadata": meta,
            })
        self._salvar()

    def query(self, query_embeddings: list[list[float]], n_results: int = 5) -> dict:
        if not self.dados or not query_embeddings:
            return {"documents": [[]], "metadatas": [[]]}
        q_emb = query_embeddings[0]
        q_norm = math.sqrt(sum(x * x for x in q_emb)) or 1.0
        scores = []
        for item in self.dados:
            d_emb = item.get("embedding") or []
            if not d_emb:
                continue
            dot = sum(a * b for a, b in zip(q_emb, d_emb))
            d_norm = math.sqrt(sum(y * y for y in d_emb)) or 1.0
            sim = dot / (q_norm * d_norm)
            scores.append((sim, item["document"], item.get("metadata", {})))
        scores.sort(key=lambda x: x[0], reverse=True)
        top = scores[:n_results]
        return {
            "documents": [[item[1] for item in top]],
            "metadatas": [[item[2] for item in top]],
        }


class BibliotecaTrader:
    EXTENSOES = {".pdf", ".txt", ".md", ".csv", ".docx"}

    def __init__(self) -> None:
        self._col = None

    def _colecao(self):
        if self._col is None:
            try:
                import chromadb

                os.makedirs(DB_BIBLIOTECA, exist_ok=True)
                cliente = chromadb.PersistentClient(path=DB_BIBLIOTECA)
                self._col = cliente.get_or_create_collection("livros_trader", metadata={"hnsw:space": "cosine"})
            except Exception as e:
                logger.warning(f"ChromaDB indisponível ({e}). Usando base vetorial JSON nativa.")
                self._col = _ColecaoFallback(os.path.join(BASE_DIR, "biblioteca_trader_db.json"))
        return self._col

    def _arquivos(self) -> list[str]:
        os.makedirs(PASTA_BIBLIOTECA, exist_ok=True)
        achados = []
        for raiz, _, nomes in os.walk(PASTA_BIBLIOTECA):
            for nome in nomes:
                if os.path.splitext(nome)[1].lower() in self.EXTENSOES:
                    achados.append(os.path.join(raiz, nome))
        return sorted(achados)

    def indexar(self, forcar: bool = False) -> str:
        arq_manifesto = os.path.join(BASE_DIR, "biblioteca_trader_manifesto.json")
        manifesto = _ler_json(arq_manifesto, {})
        col = self._colecao()
        relatorio, total_chunks = [], 0
        arquivos = self._arquivos()
        if not arquivos:
            return f"Biblioteca vazia. Coloque PDFs/TXT/MD/DOCX em: {PASTA_BIBLIOTECA}"
        for caminho in arquivos:
            rel = os.path.relpath(caminho, PASTA_BIBLIOTECA).replace("\\", "/")
            assinatura = f"{os.path.getsize(caminho)}-{int(os.path.getmtime(caminho))}"
            if not forcar and manifesto.get(rel) == assinatura:
                continue
            try:
                paginas = _extrair_paginas(caminho)
            except Exception as erro:
                relatorio.append(f"✗ {rel}: erro ao ler ({str(erro)[:80]})")
                continue
            documentos, metadados = [], []
            for rotulo, texto in paginas:
                for parte in _quebrar(texto):
                    documentos.append(parte)
                    metadados.append({"fonte": rel, "pagina": rotulo})
            if not documentos:
                relatorio.append(f"✗ {rel}: sem texto extraível (PDF escaneado? precisa de OCR)")
                continue
            try:
                col.delete(where={"fonte": rel})
            except Exception:
                pass
            base_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
            for i in range(0, len(documentos), 128):
                lote_docs = documentos[i : i + 128]
                vetores = _embed(lote_docs, "RETRIEVAL_DOCUMENT")
                col.add(
                    ids=[f"{base_id}-{i + j}" for j in range(len(lote_docs))],
                    documents=lote_docs,
                    embeddings=vetores,
                    metadatas=metadados[i : i + 128],
                )
            manifesto[rel] = assinatura
            total_chunks += len(documentos)
            relatorio.append(f"✓ {rel}: {len(documentos)} trechos indexados")
        _gravar_json(arq_manifesto, manifesto)
        if not relatorio:
            return f"Biblioteca já atualizada ({col.count()} trechos, {len(arquivos)} arquivos)."
        return "\n".join(relatorio) + f"\nTotal na base: {col.count()} trechos."

    def buscar(self, pergunta: str, k: int = 5) -> str:
        col = self._colecao()
        if col.count() == 0:
            resultado = self.indexar()
            if col.count() == 0:
                return resultado
        vetor = _embed([pergunta], "RETRIEVAL_QUERY")[0]
        res = col.query(query_embeddings=[vetor], n_results=max(1, min(k, 10)))
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        if not docs:
            return "Nada encontrado na biblioteca para essa pergunta."
        blocos = []
        for doc, meta in zip(docs, metas):
            ref = f"{meta.get('fonte', '?')} {meta.get('pagina', '')}".strip()
            blocos.append(f"[{ref}]\n{doc.strip()}")
        return "\n\n---\n\n".join(blocos)


biblioteca_trader = BibliotecaTrader()


# ----------------------------------------------------------------------------
# Ferramentas do Jarvis (mixin herdado pela classe Assistant)
# ----------------------------------------------------------------------------
class TraderMixin:
    def _trader_init(self) -> None:
        self._trader_monitor: dict = {"ativo": False, "intervalo": 30, "ultima": 0.0, "foco": "", "tela": "atual"}
        self._trader_ocupado = False
        self._trader_lock = asyncio.Lock()

    # ----- gestão de risco ------------------------------------------------
    def _trader_validar_ordem(self, ordem: dict, cfg: dict, estado: dict) -> list[str]:
        problemas = []
        agora = datetime.now(TZ)
        hhmm = agora.strftime("%H:%M")
        if agora.weekday() >= 5:
            problemas.append("Hoje não é dia de pregão (fim de semana).")
        if not (cfg["inicio_operacao"] <= hhmm <= cfg["fim_operacao"]):
            problemas.append(f"Fora da janela de operação ({cfg['inicio_operacao']}–{cfg['fim_operacao']}); agora são {hhmm}.")
        if estado.get("travado"):
            problemas.append(f"Operações travadas hoje: {estado.get('motivo_trava')}.")
        if time.time() < float(estado.get("pausa_ate") or 0):
            faltam = int((estado["pausa_ate"] - time.time()) / 60) + 1
            problemas.append(f"Pausa obrigatória após stops seguidos: faltam ~{faltam} min.")
        if estado.get("trades", 0) >= cfg["max_trades_dia"]:
            problemas.append(f"Limite de {cfg['max_trades_dia']} operações no dia atingido.")
        if ordem["quantidade"] < 1 or ordem["quantidade"] > cfg["max_contratos"]:
            problemas.append(f"Quantidade deve ser de 1 a {cfg['max_contratos']} contratos.")
        stop = ordem["stop_pontos"]
        if stop < cfg["stop_min_pontos"] or stop > cfg["stop_max_pontos"]:
            problemas.append(f"Stop deve estar entre {cfg['stop_min_pontos']} e {cfg['stop_max_pontos']} pontos (recebido {stop}).")
        if ordem["alvo_pontos"] <= 0 or (stop > 0 and ordem["alvo_pontos"] / stop < cfg["rr_minimo"]):
            problemas.append(f"Relação retorno/risco abaixo do mínimo ({cfg['rr_minimo']}:1).")
        risco = stop * ordem["quantidade"] * VALOR_PONTO_WIN
        if risco > cfg["risco_max_por_trade_reais"]:
            problemas.append(f"Risco da operação R$ {risco:.2f} acima do máximo por trade (R$ {cfg['risco_max_por_trade_reais']:.2f}).")
        if estado.get("resultado_reais", 0.0) - risco < -cfg["perda_max_dia_reais"]:
            problemas.append(
                f"Se esse stop for atingido, ultrapassa a perda máxima do dia (R$ {cfg['perda_max_dia_reais']:.2f}); "
                f"resultado do dia: R$ {estado.get('resultado_reais', 0.0):.2f}."
            )
        return problemas

    # ----- núcleo: executar objetivo na tela com verificação passo a passo ----
    def _trader_loop_tela(
        self,
        objetivo: str,
        regras: str,
        max_passos: int = 12,
        permitir_ordem: bool = False,
        qual_tela: str = "atual",
    ) -> dict:
        historico: list[str] = []
        cliques_ordem = 0
        for passo in range(1, max_passos + 1):
            img = self._capturar_imagem_tela(qual_tela)
            if not img:
                return {"ok": False, "motivo": "sem captura de tela", "historico": historico}
            prompt = PROMPT_PASSO.format(
                objetivo=objetivo, regras=regras, historico=" | ".join(historico[-8:]) or "nenhum"
            )
            acao = _extrair_json(_visao(img, prompt))
            if not acao:
                historico.append(f"{passo}: resposta de visão inválida")
                time.sleep(0.6)
                continue
            tipo = str(acao.get("acao", "")).lower()
            motivo = str(acao.get("motivo", ""))[:120]
            if tipo == "concluido":
                return {"ok": True, "motivo": motivo, "historico": historico}
            if tipo == "abortar":
                return {"ok": False, "motivo": motivo or "abortado pela leitura da tela", "historico": historico}
            try:
                if tipo == "clicar":
                    caixa = acao.get("box_2d")
                    descricao = str(acao.get("descricao", ""))[:80]
                    if not caixa or len(caixa) != 4:
                        historico.append(f"{passo}: caixa inválida")
                        continue
                    eh_botao_ordem = bool(_REGEX_BOTAO_ORDEM.search(descricao))
                    if eh_botao_ordem and not permitir_ordem:
                        return {"ok": False, "motivo": f"bloqueado: clique em botão de ordem ('{descricao}') sem autorização", "historico": historico}
                    if eh_botao_ordem:
                        cliques_ordem += 1
                        if cliques_ordem > 3:
                            return {"ok": False, "motivo": "limite de cliques em botões de ordem excedido", "historico": historico}
                    ymin, xmin, ymax, xmax = [float(v) for v in caixa]
                    x0, y0, _, _, _ = self._obter_retangulo_monitor_alvo(qual_tela)
                    x = x0 + int(((xmin + xmax) / 2000.0) * img.width)
                    y = y0 + int(((ymin + ymax) / 2000.0) * img.height)
                    modo_clique = str(acao.get("clique", "esquerdo")).lower()
                    self._executar_clique_mouse(
                        x, y, "direito" if "dire" in modo_clique else "esquerdo", "duplo" in modo_clique
                    )
                    historico.append(f"{passo}: clicou em '{descricao}'")
                elif tipo == "digitar":
                    texto = str(acao.get("texto", ""))
                    if not texto or len(texto) > 4000:
                        historico.append(f"{passo}: texto inválido")
                        continue
                    if acao.get("substituir"):
                        _pressionar_tecla("ctrl+a")
                    _digitar_texto(texto)
                    historico.append(f"{passo}: digitou '{texto[:30]}'")
                elif tipo == "tecla":
                    tecla = str(acao.get("tecla", "")).lower()
                    if tecla not in _TECLAS_LIVRES:
                        historico.append(f"{passo}: tecla '{tecla}' não permitida")
                        continue
                    _pressionar_tecla(tecla)
                    historico.append(f"{passo}: tecla {tecla}")
                elif tipo == "esperar":
                    time.sleep(min(float(acao.get("segundos", 1) or 1), 5))
                    historico.append(f"{passo}: esperou")
                else:
                    historico.append(f"{passo}: ação desconhecida '{tipo}'")
            except Exception as erro:
                historico.append(f"{passo}: erro {str(erro)[:60]}")
            time.sleep(0.7)
        return {"ok": False, "motivo": "limite de passos atingido sem concluir", "historico": historico}

    # ----- ferramentas: leitura e conhecimento ----------------------------
    @function_tool(
        description=(
            "Lê em tempo real a tela do Profit (gráfico, candles, VWAP, médias, volume, book, tape, posição, conta) "
            "e devolve uma leitura técnica estruturada de price action e fluxo para o mini índice. "
            "Use SEMPRE antes de sugerir ou preparar qualquer operação e sempre que precisar de contexto atualizado. "
            "'foco' direciona a análise (ex.: 'olhe o fluxo no book', 'confira o stop'). 'qual_tela': 'atual', 'tela 2', 'tela da direita'."
        )
    )
    async def trader_ler_tela_profit(self, foco: str = "", qual_tela: str = "atual") -> str:
        img = await asyncio.to_thread(self._capturar_imagem_tela, qual_tela)
        if not img:
            return "Aviso: não consegui capturar a tela. Confirme em qual monitor o Profit está (use qual_tela='tela 2', por exemplo)."
        contrato, _ = contrato_vigente()
        prompt = PROMPT_LEITURA_PROFIT.format(
            agora=datetime.now(TZ).strftime("%d/%m %H:%M:%S"),
            contrato=contrato,
            foco=f"Foco do trader: {foco}" if foco.strip() else "",
        )
        return await asyncio.to_thread(_visao, img, prompt)

    @function_tool(
        description=(
            "Consulta a biblioteca de conhecimento do trader (livros, apostilas e técnicas de price action, fluxo, leitura de preço, "
            "gestão de risco, NTSL, configuração do Profit). Use para fundamentar setups, critérios de entrada/stop/alvo e dúvidas de método. "
            "Passe uma pergunta específica em português."
        )
    )
    async def trader_consultar_biblioteca(self, pergunta: str, quantidade_trechos: int = 5) -> str:
        if not pergunta.strip():
            return "Aviso: informe a pergunta a consultar."
        return await asyncio.to_thread(biblioteca_trader.buscar, pergunta.strip(), int(quantidade_trechos))

    @function_tool(
        description=(
            "Indexa na base de conhecimento os livros/arquivos novos colocados na pasta Biblioteca_Trader "
            "(PDF, TXT, MD, DOCX). Use quando o usuário disser que adicionou materiais. 'reindexar_tudo' refaz tudo do zero."
        )
    )
    async def trader_indexar_biblioteca(self, reindexar_tudo: bool = False) -> str:
        return await asyncio.to_thread(biblioteca_trader.indexar, bool(reindexar_tudo))

    # ----- ferramentas: dados de mercado ----------------------------------
    @function_tool(
        description=(
            "Monta o painel de PRÉ-MERCADO/contexto em tempo real: EWZ, S&P e Nasdaq futuros, VIX, DXY, dólar/real, juro 10 anos EUA, "
            "Brent, ADRs (Vale, Petrobras, Itaú), notícias do dia e um placar de viés para o WIN. "
            "Use na rotina antes das 9h e sempre que o usuário pedir o cenário. DI e WDO devem ser lidos na tela do Profit."
        )
    )
    async def trader_painel_pre_mercado(self) -> str:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS_WEB, follow_redirects=True) as client:
            cotacoes, noticias_a, noticias_b = await asyncio.gather(
                asyncio.gather(*[_cotacao_yahoo(client, s[0]) for s in PAINEL_PRE_MERCADO]),
                _noticias_google(client, "Ibovespa mercado hoje", 5),
                _noticias_google(client, "dólar juros futuros Copom Fed", 5),
            )
        linhas, placar, peso_total = [], 0.0, 0.0
        for (simbolo, rotulo, peso, limiar, invertido), cot in zip(PAINEL_PRE_MERCADO, cotacoes):
            linhas.append(_fmt_cotacao(cot, rotulo))
            if cot.get("var_pct") is None:
                continue
            forca = max(-1.0, min(1.0, cot["var_pct"] / limiar))
            placar += peso * forca * (-1 if invertido else 1)
            peso_total += peso
        pontuacao = (placar / peso_total * 100) if peso_total else 0.0
        if pontuacao >= 35:
            vies = "COMPRADOR forte"
        elif pontuacao >= 15:
            vies = "COMPRADOR moderado"
        elif pontuacao <= -35:
            vies = "VENDEDOR forte"
        elif pontuacao <= -15:
            vies = "VENDEDOR moderado"
        else:
            vies = "NEUTRO/MISTO"
        agora = datetime.now(TZ)
        contrato, venc = contrato_vigente()
        saida = [
            f"PAINEL {agora.strftime('%d/%m %H:%M')} (Brasília) | contrato vigente: {contrato}"
            + (f" (vence {venc.strftime('%d/%m')}; liquidez costuma migrar 1-3 dias antes)" if venc else ""),
            "",
            *linhas,
            "",
            f"PLACAR EXTERNO: {pontuacao:+.0f}/100 → viés {vies} para a abertura (é ponto de partida, não ordem).",
            "FALTA LER NO PROFIT: DI (taxa subindo pressiona o índice), WDO (costuma andar inverso), ajuste/fechamento anterior do WIN, "
            "gap e VWAP, máx/mín anterior, agenda econômica do dia.",
            "",
            "NOTÍCIAS (mercado):",
            *noticias_a,
            "NOTÍCIAS (juros/dólar/macro):",
            *noticias_b,
        ]
        _diario("painel_pre_mercado", {"placar": round(pontuacao), "vies": vies})
        return "\n".join(saida)

    @function_tool(
        description=(
            "Busca cotação em tempo real (Yahoo Finance) de qualquer ativo que o trader pedir: EWZ, S&P, Nasdaq, VIX, DXY, dólar, "
            "Brent, petróleo, ouro, Treasury, Vale, Petrobras, Itaú, Ibovespa, bitcoin ou qualquer ticker. Aceita vários separados por vírgula."
        )
    )
    async def trader_buscar_dado_mercado(self, ativos: str) -> str:
        lista = [a for a in (x.strip() for x in ativos.split(",")) if a][:12]
        if not lista:
            return "Aviso: informe ao menos um ativo."
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS_WEB, follow_redirects=True) as client:
            cotacoes = await asyncio.gather(*[_cotacao_yahoo(client, _resolver_simbolo(a)) for a in lista])
        return "\n".join(_fmt_cotacao(c) for c in cotacoes)

    @function_tool(
        description=(
            "Busca as manchetes mais recentes (últimas 24h) sobre um tema de mercado: 'Copom', 'payroll', 'Fed', 'Petrobras', 'Vale minério', "
            "'Ibovespa', 'dólar', 'fiscal' etc. Use para checar notícia que possa mover o WIN."
        )
    )
    async def trader_noticias_mercado(self, tema: str) -> str:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS_WEB, follow_redirects=True) as client:
            itens = await _noticias_google(client, tema.strip() or "Ibovespa", 8)
        return "\n".join(itens) or "Nenhuma notícia encontrada."

    # ----- ferramentas: configuração do Profit e NTSL ---------------------
    @function_tool(
        description=(
            "Entrega o passo a passo de como configurar o Profit para operar mini índice (workspace, gráficos, indicadores, book/tape, "
            "boleta, ativos correlacionados, alertas, conta simulador, checklist). 'topico' filtra a seção (ex.: 'indicadores', 'boleta'); "
            "vazio devolve o índice. Combine com trader_ler_tela_profit para conferir o que já está configurado."
        )
    )
    async def trader_guia_profit(self, topico: str = "") -> str:
        try:
            with open(ARQ_GUIA_PROFIT, "r", encoding="utf-8") as f:
                conteudo = f.read()
        except Exception:
            return f"Guia não encontrado em {ARQ_GUIA_PROFIT}."
        secoes = re.split(r"(?m)^## ", conteudo)[1:]
        if not topico.strip():
            return "Seções disponíveis:\n" + "\n".join("- " + s.splitlines()[0] for s in secoes)
        termo = topico.strip().lower()
        achadas = [s for s in secoes if termo in s.lower()]
        if not achadas:
            return "Seção não encontrada. Seções:\n" + "\n".join("- " + s.splitlines()[0] for s in secoes)
        return "\n\n".join("## " + s.strip() for s in achadas[:3])

    @function_tool(
        description=(
            "Salva um indicador/estratégia em NTSL (linguagem do Profit) em arquivo e copia o código para a área de transferência, "
            "pronto para colar no Editor de Estratégias do Profit. Depois use trader_agir_na_tela para abrir o editor, colar, compilar "
            "e corrigir erros lendo a mensagem do compilador. Consulte a biblioteca (NTSL) antes de escrever o código."
        )
    )
    async def trader_criar_indicador_ntsl(self, nome: str, codigo: str, descricao: str = "") -> str:
        nome_limpo = re.sub(r"[^A-Za-z0-9_\-]", "_", nome.strip())[:60] or "indicador"
        if len(codigo.strip()) < 20:
            return "Aviso: código NTSL vazio ou curto demais."
        os.makedirs(PASTA_INDICADORES, exist_ok=True)
        caminho = os.path.join(PASTA_INDICADORES, f"{nome_limpo}.txt")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(f"// {descricao}\n{codigo}" if descricao else codigo)
        try:
            await asyncio.to_thread(copiar_para_clipboard, codigo)
            clip = "Código copiado para a área de transferência."
        except Exception as erro:
            clip = f"(não consegui copiar para a área de transferência: {erro})"
        aviso = "" if re.search(r"\bplot\w*\s*\(|\bbuy|\bsell", codigo, re.IGNORECASE) else " Atenção: não encontrei Plot()/ordens no código."
        _diario("indicador_ntsl", {"nome": nome_limpo, "descricao": descricao})
        return f"Indicador salvo em {caminho}. {clip}{aviso} Próximo passo: abrir o Editor de Estratégias do Profit, colar (Ctrl+V), compilar e corrigir erros."

    @function_tool(
        description=(
            "Executa na tela um objetivo NÃO financeiro no Profit/Windows com verificação passo a passo por visão: abrir o editor NTSL, "
            "colar o código, compilar, abrir janelas (book, tape, boleta), adicionar indicadores, mudar timeframe, configurar colunas, etc. "
            "NÃO envia ordens (para ordens use trader_preparar_ordem e trader_executar_ordem_pendente). Descreva o objetivo com precisão."
        )
    )
    async def trader_agir_na_tela(self, objetivo: str, qual_tela: str = "atual", max_passos: int = 12) -> str:
        if self._trader_ocupado:
            return "Aviso: outra ação de tela do trader está em andamento."
        async with self._trader_lock:
            self._trader_ocupado = True
            try:
                regras = (
                    "Não clique em botões de compra/venda/envio/zerar. Não altere configurações não relacionadas ao objetivo. "
                    "Se aparecer diálogo inesperado ou erro, descreva em 'motivo' e aborte."
                )
                res = await asyncio.to_thread(
                    self._trader_loop_tela, objetivo, regras, max(3, min(int(max_passos), 20)), False, qual_tela
                )
            finally:
                self._trader_ocupado = False
        _diario("agir_na_tela", {"objetivo": objetivo, "ok": res["ok"], "motivo": res["motivo"]})
        return f"{'CONCLUÍDO' if res['ok'] else 'NÃO CONCLUÍDO'}: {res['motivo']} | passos: {' ; '.join(res['historico'][-10:])}"

    # ----- ferramentas: ordens e risco ------------------------------------
    @function_tool(
        description=(
            "PREPARA (não envia) uma ordem de mini índice aplicando a gestão de risco: stop obrigatório, tamanho, risco em R$, relação "
            "retorno/risco, janela de horário, limites diários e pausas. Se aprovada, fica pendente e você DEVE ler o resumo ao usuário e pedir "
            "confirmação por voz. 'lado': 'compra' ou 'venda'. stop_pontos e alvo_pontos em pontos do WIN. 'tipo_ordem': 'mercado' ou 'limite' "
            "(com preco_limite). 'motivo': a tese (contexto, price action, fluxo, nível)."
        )
    )
    async def trader_preparar_ordem(
        self,
        lado: str,
        quantidade: int,
        stop_pontos: int,
        alvo_pontos: int,
        motivo: str,
        tipo_ordem: str = "mercado",
        preco_limite: int = 0,
    ) -> str:
        cfg, estado = carregar_config(), carregar_estado()
        lado_n = "compra" if lado.strip().lower().startswith(("c", "b")) else "venda"
        ordem = {
            "lado": lado_n,
            "quantidade": int(quantidade),
            "stop_pontos": int(stop_pontos),
            "alvo_pontos": int(alvo_pontos),
            "tipo": "limite" if tipo_ordem.strip().lower().startswith("l") else "mercado",
            "preco_limite": int(preco_limite or 0),
            "motivo": motivo.strip()[:600],
            "ativo": contrato_vigente()[0],
            "criada_em": time.time(),
        }
        if ordem["tipo"] == "limite" and ordem["preco_limite"] <= 0:
            return "Recusada: ordem limite exige preco_limite."
        problemas = self._trader_validar_ordem(ordem, cfg, estado)
        if problemas:
            estado["ordem_pendente"] = None
            _gravar_json(ARQ_ESTADO, estado)
            _diario("ordem_recusada", {**ordem, "problemas": problemas})
            return "ORDEM RECUSADA pela gestão de risco:\n- " + "\n- ".join(problemas)
        estado["ordem_pendente"] = ordem
        _gravar_json(ARQ_ESTADO, estado)
        risco = ordem["stop_pontos"] * ordem["quantidade"] * VALOR_PONTO_WIN
        retorno = ordem["alvo_pontos"] * ordem["quantidade"] * VALOR_PONTO_WIN
        _diario("ordem_preparada", ordem)
        return (
            f"ORDEM APROVADA E PENDENTE (válida por {cfg['validade_ordem_segundos']}s): {ordem['lado'].upper()} {ordem['quantidade']} x {ordem['ativo']} "
            f"a {ordem['tipo']}{' @' + str(ordem['preco_limite']) if ordem['tipo'] == 'limite' else ''} | stop {ordem['stop_pontos']} pts (risco R$ {risco:.2f}) | "
            f"alvo {ordem['alvo_pontos']} pts (retorno R$ {retorno:.2f}) | R:R {ordem['alvo_pontos'] / ordem['stop_pontos']:.2f}:1 | modo: {cfg['modo'].upper()}. "
            "Leia isso ao usuário em 2 frases e pergunte se confirma. NÃO execute sem um 'sim/executa/confirmo' explícito dele."
        )

    @function_tool(
        description=(
            "EXECUTA no Profit a ordem pendente preparada por trader_preparar_ordem. Só chame DEPOIS de o usuário confirmar explicitamente por voz "
            "(ex.: 'executa', 'confirmo', 'manda'). Revalida risco, confere conta (simulador) e ativo na tela e opera a boleta com verificação."
        )
    )
    async def trader_executar_ordem_pendente(self, confirmacao_do_usuario: str, qual_tela: str = "atual") -> str:
        if not re.search(r"\b(sim|executa|executar|confirmo|confirmado|manda|pode|vai|bora|fecha)\b", confirmacao_do_usuario, re.IGNORECASE):
            return "Recusada: sem confirmação explícita do usuário. Peça para ele confirmar por voz."
        if self._trader_ocupado:
            return "Aviso: outra ação de tela do trader está em andamento."
        cfg, estado = carregar_config(), carregar_estado()
        ordem = estado.get("ordem_pendente")
        if not ordem:
            return "Não há ordem pendente. Prepare uma com trader_preparar_ordem."
        if time.time() - float(ordem["criada_em"]) > cfg["validade_ordem_segundos"]:
            estado["ordem_pendente"] = None
            _gravar_json(ARQ_ESTADO, estado)
            return "Ordem expirada (preço pode ter mudado). Releia a tela e prepare novamente."
        problemas = self._trader_validar_ordem(ordem, cfg, estado)
        if problemas:
            estado["ordem_pendente"] = None
            _gravar_json(ARQ_ESTADO, estado)
            return "ORDEM BLOQUEADA na revalidação:\n- " + "\n- ".join(problemas)

        async with self._trader_lock:
            self._trader_ocupado = True
            try:
                img = await asyncio.to_thread(self._capturar_imagem_tela, qual_tela)
                if not img:
                    return "Aviso: sem captura de tela; ordem não enviada."
                pre = _extrair_json(await asyncio.to_thread(_visao, img, PROMPT_PRECHECK)) or {}
                conta = str(pre.get("conta", "desconhecida")).lower()
                if cfg["modo"] == "simulador" and conta != "simulador":
                    _diario("ordem_bloqueada", {"motivo": "conta não confirmada como simulador", "pre": pre})
                    return (
                        f"BLOQUEADA: o modo é SIMULADOR e a tela indica conta '{conta}' ({pre.get('evidencia_conta')}). "
                        "Deixe a conta Simulador visível no Profit e tente de novo."
                    )
                if not pre.get("boleta_visivel"):
                    return "BLOQUEADA: não vejo a boleta do Profit na tela. Abra a boleta do WIN e tente de novo."
                posicao = str(pre.get("posicao_atual", "")).lower()
                if posicao and posicao not in {"zerado", "desconhecida", "none", ""}:
                    return f"BLOQUEADA: já existe posição aberta ({pre.get('posicao_atual')}). Gerencie ou zere antes de nova entrada."

                estado["ordem_pendente"] = None
                estado["trades"] = estado.get("trades", 0) + 1
                _gravar_json(ARQ_ESTADO, estado)  # anti-duplicidade: consome a ordem antes de clicar

                botao = "COMPRA" if ordem["lado"] == "compra" else "VENDA"
                objetivo = (
                    f"Enviar UMA ordem de {botao} de {ordem['quantidade']} contrato(s) de {ordem['ativo']} ({ordem['tipo']}"
                    f"{' a ' + str(ordem['preco_limite']) if ordem['tipo'] == 'limite' else ''}) na boleta aberta, com STOP LOSS de "
                    f"{ordem['stop_pontos']} pontos e GAIN de {ordem['alvo_pontos']} pontos a partir da entrada. Passos: conferir que o ativo da "
                    f"boleta é {ordem['ativo']} (ou WINFUT); ajustar quantidade para {ordem['quantidade']}; preencher stop/gain se a boleta tiver esses campos "
                    "(ou usar ordem OCO/stop); clicar no botão de " + botao + " uma única vez; verificar na tela que a posição abriu e que há ordem de stop ativa. "
                    "Se não conseguir proteger com stop, ZERE a posição imediatamente e aborte."
                )
                regras = (
                    "Confira ativo, lado e quantidade antes do clique final. Nunca clique mais de uma vez no botão de envio. "
                    + ("A conta deve ser SIMULADOR; se não for, aborte. " if cfg["modo"] == "simulador" else "")
                    + "Não mexa em outras configurações."
                )
                res = await asyncio.to_thread(self._trader_loop_tela, objetivo, regras, 16, True, qual_tela)
                img_pos = await asyncio.to_thread(self._capturar_imagem_tela, qual_tela)
                verificacao = ""
                if img_pos:
                    verificacao = await asyncio.to_thread(
                        _visao,
                        img_pos,
                        "Na tela do Profit: há posição aberta? Qual lado, quantidade e preço médio? Existe ordem de stop e de gain ativas (preços)? "
                        "Responda em 3 linhas objetivas, sem inventar.",
                    )
            finally:
                self._trader_ocupado = False
        _diario("ordem_executada", {**ordem, "ok": res["ok"], "motivo": res["motivo"], "passos": res["historico"], "verificacao": verificacao})
        return (
            f"{'EXECUÇÃO CONCLUÍDA' if res['ok'] else 'EXECUÇÃO INCERTA/ABORTADA'}: {res['motivo']}\n"
            f"VERIFICAÇÃO NA TELA: {verificacao}\n"
            "Informe o usuário do estado real da posição e do stop. Se houver dúvida, peça para ele olhar a tela ou use trader_zerar_posicao_e_cancelar_ordens."
        )

    @function_tool(
        description=(
            "EMERGÊNCIA / redução de risco: zera a posição a mercado e cancela ordens pendentes do WIN no Profit. Pode ser chamada imediatamente "
            "quando o usuário disser 'zera', 'sai', 'fecha tudo' ou quando a leitura mostrar risco anormal. Não exige confirmação adicional."
        )
    )
    async def trader_zerar_posicao_e_cancelar_ordens(self, qual_tela: str = "atual") -> str:
        async with self._trader_lock:
            self._trader_ocupado = True
            try:
                objetivo = (
                    "Zerar a posição aberta do WIN a MERCADO (botão 'Zerar'/'Zerar posição' da boleta ou do book) e cancelar todas as ordens pendentes "
                    "(stop/gain/limite) do ativo. Conclua somente quando a tela mostrar posição zerada e nenhuma ordem pendente."
                )
                regras = "Priorize velocidade. Clique no botão de zerar UMA vez; depois verifique. Se já estiver zerado, conclua."
                res = await asyncio.to_thread(self._trader_loop_tela, objetivo, regras, 12, True, qual_tela)
            finally:
                self._trader_ocupado = False
        _diario("zerar_posicao", {"ok": res["ok"], "motivo": res["motivo"], "passos": res["historico"]})
        return f"{'CONCLUÍDO' if res['ok'] else 'ATENÇÃO - NÃO CONFIRMADO'}: {res['motivo']}. Peça ao usuário para conferir a posição na tela."

    @function_tool(
        description=(
            "Registra o resultado financeiro de uma operação ou do fechamento parcial do dia (positivo = ganho, negativo = perda, em R$), "
            "atualiza stops seguidos, pausas e a trava de perda máxima diária. Use depois de cada operação encerrada (leia o resultado na tela)."
        )
    )
    async def trader_registrar_resultado(self, resultado_reais: float, observacao: str = "") -> str:
        cfg, estado = carregar_config(), carregar_estado()
        estado["resultado_reais"] = round(float(estado.get("resultado_reais", 0.0)) + float(resultado_reais), 2)
        if resultado_reais < 0:
            estado["stops_seguidos"] = int(estado.get("stops_seguidos", 0)) + 1
        else:
            estado["stops_seguidos"] = 0
        avisos = []
        if estado["stops_seguidos"] >= cfg["pausa_apos_stops_seguidos"]:
            estado["pausa_ate"] = time.time() + cfg["pausa_minutos"] * 60
            avisos.append(f"{estado['stops_seguidos']} perdas seguidas: pausa obrigatória de {cfg['pausa_minutos']} min.")
        if estado["resultado_reais"] <= -cfg["perda_max_dia_reais"]:
            estado["travado"] = True
            estado["motivo_trava"] = f"perda máxima do dia atingida (R$ {estado['resultado_reais']:.2f})"
            avisos.append("PERDA MÁXIMA DO DIA ATINGIDA: operações travadas até amanhã. Encerre por hoje.")
        _gravar_json(ARQ_ESTADO, estado)
        _diario("resultado", {"resultado_reais": resultado_reais, "acumulado": estado["resultado_reais"], "obs": observacao})
        return f"Resultado do dia: R$ {estado['resultado_reais']:.2f} | operações: {estado['trades']} | perdas seguidas: {estado['stops_seguidos']}. " + " ".join(avisos)

    @function_tool(
        description=(
            "Mostra o status de risco e do sistema de trade: modo (simulador/real), limites, operações e resultado do dia, trava/pausa, "
            "ordem pendente, monitoramento e contrato vigente do WIN."
        )
    )
    async def trader_status_e_risco(self) -> str:
        cfg, estado = carregar_config(), carregar_estado()
        contrato, venc = contrato_vigente()
        pausa = max(0, int((float(estado.get("pausa_ate") or 0) - time.time()) / 60) + 1) if time.time() < float(estado.get("pausa_ate") or 0) else 0
        mon = self._trader_monitor
        return (
            f"Contrato: {contrato}{' (vence ' + venc.strftime('%d/%m') + ')' if venc else ''} | MODO: {cfg['modo'].upper()}\n"
            f"Limites: {cfg['max_contratos']} contratos | risco/trade R$ {cfg['risco_max_por_trade_reais']:.2f} | perda máx/dia R$ {cfg['perda_max_dia_reais']:.2f} | "
            f"{cfg['max_trades_dia']} ops/dia | stop {cfg['stop_min_pontos']}-{cfg['stop_max_pontos']} pts | R:R mín {cfg['rr_minimo']} | janela {cfg['inicio_operacao']}-{cfg['fim_operacao']}\n"
            f"Hoje: {estado['trades']} ops | resultado R$ {estado['resultado_reais']:.2f} | perdas seguidas {estado['stops_seguidos']} | "
            f"travado: {'SIM - ' + estado['motivo_trava'] if estado['travado'] else 'não'} | pausa: {str(pausa) + ' min' if pausa else 'não'}\n"
            f"Ordem pendente: {estado['ordem_pendente'] if estado['ordem_pendente'] else 'nenhuma'}\n"
            f"Monitoramento: {'ATIVO a cada ' + str(mon['intervalo']) + 's' if mon['ativo'] else 'desligado'}"
        )

    @function_tool(
        description=(
            "Altera os limites de risco do trader por pedido do usuário (0 = manter valor atual). Para mudar o modo para 'real' é obrigatório "
            "confirmo_conta_real=True, e só deve ser usado se o usuário pedir explicitamente e reafirmar. Padrão e recomendado: 'simulador'."
        )
    )
    async def trader_configurar_limites(
        self,
        modo: str = "",
        max_contratos: int = 0,
        risco_max_por_trade_reais: float = 0.0,
        perda_max_dia_reais: float = 0.0,
        max_trades_dia: int = 0,
        stop_max_pontos: int = 0,
        confirmo_conta_real: bool = False,
    ) -> str:
        cfg = carregar_config()
        alterados = []
        if modo.strip():
            m = modo.strip().lower()
            if m not in {"simulador", "real"}:
                return "Modo inválido: use 'simulador' ou 'real'."
            if m == "real" and not confirmo_conta_real:
                return "Recusado: para o modo REAL o usuário precisa confirmar explicitamente (confirmo_conta_real=True)."
            cfg["modo"] = m
            alterados.append(f"modo={m}")
        if 0 < max_contratos <= 20:
            cfg["max_contratos"] = int(max_contratos)
            alterados.append(f"max_contratos={max_contratos}")
        if risco_max_por_trade_reais > 0:
            cfg["risco_max_por_trade_reais"] = float(risco_max_por_trade_reais)
            alterados.append(f"risco/trade={risco_max_por_trade_reais}")
        if perda_max_dia_reais > 0:
            cfg["perda_max_dia_reais"] = float(perda_max_dia_reais)
            alterados.append(f"perda_max_dia={perda_max_dia_reais}")
        if 0 < max_trades_dia <= 50:
            cfg["max_trades_dia"] = int(max_trades_dia)
            alterados.append(f"max_trades_dia={max_trades_dia}")
        if stop_max_pontos > 0:
            cfg["stop_max_pontos"] = int(stop_max_pontos)
            alterados.append(f"stop_max={stop_max_pontos}")
        if not alterados:
            return "Nenhuma alteração informada."
        _gravar_json(ARQ_CONFIG, cfg)
        _diario("config_alterada", {"alteracoes": alterados})
        return "Limites atualizados: " + ", ".join(alterados)

    @function_tool(
        description=(
            "Liga o monitoramento contínuo do Profit: a cada N segundos o Jarvis lê a tela e AVISA POR VOZ quando houver setup, rompimento/falha de "
            "nível, absorção, risco ou posição perto do stop/alvo. Use quando o usuário pedir para acompanhar o mercado/operar durante o dia. "
            "'qual_tela' = monitor do Profit (ex.: 'tela 2'); com 'atual' usa o monitor onde está o mouse."
        )
    )
    async def trader_iniciar_monitoramento(self, intervalo_segundos: int = 30, foco: str = "", qual_tela: str = "atual") -> str:
        self._trader_monitor.update(
            {"ativo": True, "intervalo": max(15, min(int(intervalo_segundos), 300)), "foco": foco.strip(), "tela": qual_tela, "ultima": 0.0}
        )
        return f"Monitoramento ligado a cada {self._trader_monitor['intervalo']}s na tela '{qual_tela}'. Avisarei por voz só quando houver algo relevante."

    @function_tool(description="Desliga o monitoramento contínuo do Profit.")
    async def trader_parar_monitoramento(self) -> str:
        self._trader_monitor["ativo"] = False
        return "Monitoramento desligado."


# ----------------------------------------------------------------------------
# Tarefa de fundo: monitoramento contínuo (iniciada no entrypoint do agente)
# ----------------------------------------------------------------------------
async def monitorar_profit(session, assistant) -> None:
    ultimos_alertas: dict[str, float] = {}
    while True:
        try:
            await asyncio.sleep(3)
            mon = getattr(assistant, "_trader_monitor", None)
            if not mon or not mon.get("ativo") or getattr(assistant, "_trader_ocupado", False):
                continue
            agora = time.monotonic()
            if agora - mon["ultima"] < mon["intervalo"]:
                continue
            mon["ultima"] = agora
            img = await asyncio.to_thread(assistant._capturar_imagem_tela, mon.get("tela", "atual"))
            if not img:
                continue
            dados = _extrair_json(await asyncio.to_thread(_visao, img, _prompt_monitor(mon.get("foco", ""))))
            if not dados or not dados.get("alerta"):
                continue
            if str(dados.get("prioridade", "")).lower() not in {"alta", "media", "média"}:
                continue
            chave = str(dados.get("titulo", ""))[:60].lower()
            if agora - ultimos_alertas.get(chave, 0) < 180:
                continue
            ultimos_alertas[chave] = agora
            _diario("alerta_monitor", dados)
            await session.generate_reply(
                instructions=(
                    "ALERTA DO MONITORAMENTO DO PROFIT (leitura automática da tela agora): "
                    f"{dados.get('titulo')} — {dados.get('leitura')} | gatilho sugerido: {dados.get('gatilho_sugerido')}. "
                    "Fale em até 3 frases curtas, como trader experiente. Se fizer sentido, rode trader_ler_tela_profit para confirmar e, "
                    "só se o setup for claro, prepare a ordem com trader_preparar_ordem e peça a confirmação do usuário. Sem confirmação, nada é enviado."
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            logger.warning("evento=trader_monitor status=erro motivo=%s", erro)
            await asyncio.sleep(5)
