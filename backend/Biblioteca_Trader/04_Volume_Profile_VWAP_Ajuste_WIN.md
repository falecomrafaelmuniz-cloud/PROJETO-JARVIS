# GUIA MESTRE DE VWAP, VOLUME PROFILE E PONTOS INSTITUCIONAIS (WIN - B3)
**Jarvis Trader Specialist · Referências Matemáticas de Preço no Mini Índice**

---

## 1. VWAP (VOLUME WEIGHTED AVERAGE PRICE)
A VWAP é o preço médio ponderado por volume negociado no dia. Ela representa o custo médio das posições institucionais de curto prazo:
- **Acima da VWAP Diária**: Mercado em controle comprador. Instituições que compraram no dia estão no lucro.
- **Abaixo da VWAP Diária**: Mercado em controle vendedor.
- **A VWAP como Imã de Preço**: Em dias de consolidação, o preço tende a retornar ciclicamente para a VWAP. Em dias de tendência forte, o preço toca a VWAP em pullbacks e é ejetado com nova agressão.

### 1.1 Bandas de Desvio Padrão da VWAP (VWAP Bands)
- **1º Desvio Padrão (+1 / -1)**: Limite de volatilidade normal do mercado intraday (68% das negociações ocorrem dentro dessa faixa).
- **2º Desvio Padrão (+2 / -2)**: Região de sobrecompra extrema (+2) ou sobrevenda extrema (-2). Rompimentos sustentados além da 2ª banda indicam dias atípicos de forte tendência direcional (Trend Day).
- **Setup de Retorno à Média**: Quando o preço atinge a 2ª banda da VWAP acompanhado de exaustão de fluxo e divergência em oscilador, há alta probabilidade de retorno à VWAP central.

---

## 2. O AJUSTE DIÁRIO DA B3 (DIÁRIO DE PREÇOS)
O Ajuste Diário é o preço oficial calculado pela B3 no final de cada pregão para liquidação financeira das posições de futuros:
- **Cálculo da B3**: Média ponderada dos negócios realizados entre 16h45 e 17h00.
- **Importância Crítica no Dia Seguinte**:
  - Todo participante institucional que dormiu posicionado tem seu saldo financeiro calibrado no preço do Ajuste.
  - Portanto, o preço de ajuste do dia anterior funciona como uma das resistências ou suportes mais poderosos de todo o pregão.
  - **Reação no Ajuste**: Ao se aproximar do Ajuste anterior, o preço costuma defender a posição ou sofrer absorção pesada.

---

## 3. PONTOS INSTITUCIONAIS RELEVANTES (LINHA D'ÁGUA)
1. **Fechamento Anterior (Linha D'água)**: O fechamento do dia anterior define a variação percentual de 0,00% do dia. É o divisor de águas entre o pregão estar positivo ou negativo.
2. **Máxima e Mínima do Dia Anterior (Prior Day High / Low)**:
   - Acima da máxima do dia anterior: Rompimento de alta, busca de novas máximas históricas ou alvos de projeção.
   - Abaixo da mínima do dia anterior: Fraqueza expressiva do mercado interno.
3. **Preço de Abertura do Dia**:
   - Se o preço está acima da abertura: Candle diário está verde (comprador).
   - Se o preço está abaixo da abertura: Candle diário está vermelho (vendedor).

---

## 4. VOLUME PROFILE (DISTRIBUIÇÃO DE VOLUME POR PREÇO)
Diferente do volume tradicional que mede volume por tempo, o Volume Profile plota o volume financeiro em cada nível de preço:
- **POC (Point of Control)**: O nível de preço exato onde ocorreu a maior quantidade de contratos negociados no dia. É o ponto de maior liquidez e consenso entre compradores e vendedores.
- **Value Area (Área de Valor - 70% do Volume)**:
  - **VAH (Value Area High)**: Teto da área de valor (resistência para o dia).
  - **VAL (Value Area Low)**: Piso da área de valor (suporte para o dia).
- **Níveis de Baixo Volume (Low Volume Nodes - LVN)**: Regiões de rejeição rápida de preço. Quando o mercado entra em um LVN, o preço se desloca com grande velocidade até a próxima região de alta liquidez.

---

## 5. SETUP OPERACIONAL: TESTE DE VWAP COM CONFLUÊNCIA DE MÉDIA 21
1. **Condição Prévia**: Mini Índice em tendência de alta no 5 minutos (preço abriu em gap de alta ou fez perna de impulsão).
2. **Correção**: Preço recua suavemente em 2 ou 3 barras pequenas até a linha da VWAP Diária, que coincide com a Média Móvel Exponencial de 21 períodos.
3. **Gatilho**: Formação de candle martelo ou barra de alta fechando na máxima tocando a VWAP.
4. **Entrada**: 5 pontos acima da máxima do candle gatilho.
5. **Stop Loss**: 10 pontos abaixo da VWAP (geralmente entre 100 e 150 pontos).
6. **Alvo 1**: Teste da máxima do dia (+200 a +300 pontos).
7. **Alvo 2**: 1º Desvio padrão superior da VWAP (+400 a +600 pontos).
