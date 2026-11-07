# GUIA MESTRE DE OPERAÇÕES EM GRÁFICO DE RENKO NO MINI ÍNDICE (WIN - B3)
**Jarvis Trader Specialist · Gráficos Atemporais, Ruído Zero e Momentum**

---

## 1. O QUE É O GRÁFICO DE RENKO
O gráfico de Renko é um tipo de representação gráfica atemporal baseada unicamente na variação de preço. Não há eixo de tempo:
- Um novo box só é desenhado quando o preço se desloca um número fixo de pontos.
- Se o mercado fica parado durante 30 minutos na mesma faixa, **nenhum box novo é criado**, eliminando a ansiedade e o ruído de gráficos temporais de 1 ou 2 minutos.

### 1.1 Tamanhos de Box Ideais para o Mini Índice
- **10R (50 pontos por box)**: Excelente para operações rápidas de scalping (alvos de 70 a 120 pontos, stops de 50 a 80 pontos). Alta frequência de sinais.
- **15R (75 pontos por box)**: O padrão de ouro para Day Trade clássico de tendência no WIN. Filtra falsos rompimentos e identifica pernas limpas de 300 a 800 pontos.
- **20R / 25R (100 a 125 pontos por box)**: Indicado para dias de alta volatilidade (Payroll, Super Quarta) ou para operações de posição mais longa (swing intraday).

---

## 2. A MECÂNICA DE REVERSÃO NO RENKO
- Para continuar na mesma direção, basta o preço avançar o tamanho de 1 box (ex: 75 pontos no 15R).
- **Para reverter**, o preço precisa se deslocar o dobro: a amplitude do box atual + a amplitude do novo box oposto (ou seja, 150 pontos no 15R).
- Isso significa que o Renko tem uma inércia direcional embutida que impede o operador de ficar trocando de lado a cada oscilação passageira.

---

## 3. SETUPS LUCRATIVOS EM RENKO NO WIN

### 3.1 Setup Box de Reversão na Média Móvel 21 (Pullback Limpo)
1. **Tendência**: Preço fazendo sequência de boxes verdes acima da EMA 21.
2. **Correção**: Preço forma 1 ou 2 boxes vermelhos de correção que tocam a EMA 21 ou a VWAP.
3. **Gatilho**: O primeiro box verde que fecha voltando a favor da tendência acima da média.
4. **Entrada**: Na abertura do próximo box ou no fechamento do box verde de virada.
5. **Stop Loss**: 1 box abaixo da mínima da correção (stop técnico fixo e milimétrico).
6. **Alvo**: Mínimo de 2 vezes o tamanho do box (ex: 150 a 250 pontos no 15R).

### 3.2 Setup Cruzamento de Médias 9 e 21 com Renko
- **Compra**: EMA 9 cruza acima da EMA 21 + Box de Renko fecha acima de ambas + VWAP ascendente.
- **Venda**: EMA 9 cruza abaixo da EMA 21 + Box de Renko fecha abaixo de ambas + VWAP descendente.
- **Regra de Coloração**: Verde para compra, Vermelho para venda e Branco para consolidação.

---

## 4. VANTAGENS DO RENKO INTEGRADO COM O JARVIS
1. **Identificação Visual Instantânea pela Visão Computacional**: Como os boxes são retângulos perfeitos de tamanho constante, o modelo de visão do Jarvis identifica reversões, suportes e topos duplos com precisão matemática superior a 95%.
2. **Cálculo de Stop e Alvo sem Ambiguidade**: No Renko, o stop loss e o alvo são múltiplos exatos do tamanho do box, facilitando a automação de ordens OCO com relação Risco/Retorno perfeita.
