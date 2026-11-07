# GUIA MESTRE DE CONFIGURAÇÃO DO PROFIT PARA OPERAR MINI ÍNDICE (WIN)
**Jarvis Trader Specialist - B3 Mini Índice & Tape Reading**

Este guia descreve passo a passo como deixar o Profit (Nelogica) 100% configurado para que o operador e o Jarvis consigam analisar e executar operações com máxima clareza técnica.

---

## 1. SEGURANÇA E CONTA INICIAL (OBRIGATÓRIO)
1. **Conta Ativa**: Selecione a conta **SIMULADOR** (tarja amarela na barra superior ou no Chart Trading).
2. **Senha de Roteamento**: Deixe a senha salva/desbloqueada no Profit para a sessão de trading, para permitir envios ágeis via teclado/botões sem pop-up bloqueador.
3. **Gerenciamento de Risco da Nelogica**:
   - Acesse: *Negociação -> Gerenciamento de Risco*.
   - Defina perda máxima diária (ex: R$ 300,00) e bloqueio da plataforma caso atingida.
   - Defina horário limite de encerramento (ex: 17:30).

---

## 2. LAYOUT DE TELAS E GRÁFICOS RECOMENDADO

### Layout de 1 Monitor (Resolução Full HD 1920x1080)
Divida a tela em 3 colunas principais:

#### COLUNA 1 (Esquerda - 45% da tela): Gráficos de Candlestick
- **Gráfico Superior (5 Minutos - WIN vigente)**:
  - Objetivo: Direção do dia, pivôs, pullbacks e suportes/resistências maiores.
  - Indicadores:
    - **VWAP Diária**: Linha amarela ou azul espessa. (Suporte/Resistência institucional).
    - **Média Móvel Exponencial 9 (EMA 9)**: Curto prazo / trailing stop / momentum.
    - **Média Móvel Exponencial 21 (EMA 21)**: Guia da tendência intraday.
    - **Média Móvel Aritmética 200 (SMA 200)**: Tendência macro do dia.
    - **Volume Financeiro ou de Quantidade**: Com média de 20 períodos para validar rompimentos.
- **Gráfico Inferior (2 Minutos ou 1 Minuto - WIN vigente)**:
  - Objetivo: Gatilho de entrada fino (martelos, engulfing, rompimento de máxima/mínima anterior).
  - Indicadores: Mesmas médias (9 e 21) e VWAP para convergência de sinal.

#### COLUNA 2 (Centro - 30% da tela): Tape Reading / Fluxo
- **Times & Trades (Negócios Realizados)**:
  - Aba *Negócios*.
  - Filtro: Ocultar lotes pequenos (exibir apenas agressões acima de 15 ou 20 contratos de WIN) para enxergar apenas atuação institucional (bancos, corretoras gringas e tesourarias).
  - Aba *Saldo dos Players*: Mostra quem está comprado e vendido no dia (BTG, Ideal, XP, UBS, Morgan Stanley, etc.).
- **Livro Visual (SuperDOM ou Book de Ofertas)**:
  - Visualização gráfica da fila de ordens no gráfico ou janela lateral.
  - Permite identificar escoras de lotes grandes (suportes e resistências passivas).

#### COLUNA 3 (Direita - 25% da tela): Boleta de Execução e Grade Macro
- **Chart Trading lateral** ou **SuperDOM**:
  - Botões visíveis: *Compra a Mercado*, *Venda a Mercado*, *Zerar*, *Cancelar Ordens + Zerar*.
  - Quantidade padrão travada em **1 contrato**.
- **Grade de Cotações com Ativos Correlacionados**:
  - `WDOFUT`: Dólar Futuro (correlação inversa natural com o índice).
  - `DI1FUT` (ex: DI1F29, DI1F31): Juros futuros (juros subindo pressiona bolsa para baixo).
  - `PETR4` e `VALE3`: Principais blue chips que pesam ~25% na composição do Ibovespa.
  - `ITUB4` / `BBDC4`: Bancos (peso expressivo no índice).

---

## 3. CONFIGURAÇÃO DE ESTRATÉGIA OCO (GAIN E LOSS AUTOMÁTICO)
Para nunca operar sem Stop Loss:
1. No Chart Trading ou no SuperDOM, clique no ícone de engrenagem ao lado de **Estratégia**.
2. Crie uma nova regra chamada **"WIN_PADRAO_150_100"**:
   - **Modo**: Ticks (1 tick do WIN = 5 pontos).
   - **Gain**: 30 ticks (= 150 pontos = R$ 30,00 por contrato).
   - **Loss**: 20 ticks (= 100 pontos = R$ 20,00 por contrato).
   - **Stop Offset**: 10 ticks (para garantir execução em gap de volatilidade).
3. Salve e selecione essa regra como ativa. A cada ordem aberta, o Profit colocará automaticamente a ordem de gain e stop loss.

---

## 4. ATALHOS DE TECLADO ESSENCIAIS NO PROFIT
- **F4**: Alternar tipo de gráfico (Candle / Linha / Barras / Renko).
- **Shift + Clique no Gráfico**: Posicionar ordem de Compra Limitada no preço clicado.
- **Alt + Clique no Gráfico**: Posicionar ordem de Venda Limitada no preço clicado.
- **Shift + C**: Comprar a Mercado.
- **Alt + V**: Vender a Mercado.
- **Espaço** ou **Z**: Zerar posição imediatamente.
- **Ctrl + Z**: Cancelar todas as ordens pendentes.

---

## 5. ROTINA OPERACIONAL COM O JARVIS

### 08:30 às 08:55 (Pré-Mercado)
1. Diga ao Jarvis: *"Jarvis, faça o panorama do pré-mercado"*.
2. O Jarvis consultará EWZ, S&P 500, Nasdaq, VIX, DXY, Petróleo Brent, Minério de Ferro e Notícias macro.
3. Ele traçará o viés inicial do dia (Alta, Baixa ou Neutro/Consolidação) e estimará a abertura e primeiro suporte/resistência.

### 09:00 (Abertura do Mini Índice)
1. Aguarde os primeiros 15 minutos (09:00 às 09:15) para o mercado formar o range inicial (Opening Range).
2. Não tente adivinhar a abertura com ordens a mercado.
3. Peça: *"Jarvis, leia a tela do Profit e avalie o range de abertura"*.

### 10:00 (Abertura do Mercado À Vista / Ações B3)
1. Petrobras, Vale e Bancos começam a cotar. Volume dispara.
2. Acompanhe a correlação: se Petrobras e Vale abrem fortes em alta e Dólar cai, confirmação compradora no índice.

### Validação de Entradas (Operação Semi-Automática)
1. O Jarvis identificará o setup (ex: Pullback na VWAP + EMA 21, Martelo no 2min com fluxo institucional comprador).
2. O Jarvis informará na tela e por voz:
   - *"Identifiquei compra em 128.450 pts. Stop em 128.350 pts (100 pts) e Alvo em 128.650 pts (200 pts). Deseja executar?"*
3. O operador valida visualmente no Profit e diz: *"Executa"* ou *"Confirmo"*.
4. O Jarvis posiciona/executa a ordem com a estratégia OCO acoplada.
