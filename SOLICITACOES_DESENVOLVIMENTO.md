# 📋 Fila de Desenvolvimento Jarvis ➔ Antigravity

> Este arquivo registra as solicitações de melhorias e tarefas feitas por voz pelo usuário via Jarvis para o Antigravity implementar.

| ID | Data / Hora | Título | Arquivo Alvo | Prioridade | Status |
|---|---|---|---|---|---|
| #24 | 09/11/2026 04:11:16 | **Melhorar estabilidade e precisão das operações de navegador (abrir, maximizar, focar)** | `backend/jarvis_browser.py` | ALTA | 🟢 concluido |
| #23 | 09/11/2026 03:42:37 | **Implementar Painel de Lembretes com Status Visual e Expiração Automática** | `frontend/components/app/system-dashboard.tsx` | ALTA | 🟡 concluido |
| #22 | 09/11/2026 03:40:37 | **Adicionar botão de Lembretes no HUD** | `frontend/components/app/system-dashboard.tsx` | NORMAL | 🟡 concluido |
| #21 | 09/11/2026 01:36:19 | **Suporte a senha em boletos Graiche** | `backend/agent.py` | MÉDIA | 🟡 concluido |
| #20 | 09/11/2026 00:11:32 | **CANCELAR Solicitação #19** | `backend/agent.py` | BAIXA | 🟡 concluido |
| #19 | 08/11/2026 23:38:10 | **Integração de voz para criação de Zaps no Zapier** | `backend/agent.py` | ALTA | 🟡 concluido |
| #18 | 08/11/2026 14:52:11 | **Corrigir seleção automática de microfone para priorizar o padrão do sistema** | `backend/jarvis_browser.py, frontend/components/agents-ui/agent-control-bar.tsx` | ALTA | 🟡 concluido |
| #17 | 07/11/2026 02:50:00 | **Remover TRADER B3 da barra de ferramentas e deixar botão flutuante arrastável e redimensionável ciano** | `frontend/components/agents-ui/agent-control-bar.tsx, frontend/components/app/session-view.tsx` | ALTA | 🟡 concluido |
| #16 | 07/11/2026 02:35:00 | **Fix: Fixar Acelerador com Ponteiro no topo, impedir auto-hide e persistência do Painel Trader** | `frontend/components/app/quant-panel.tsx, frontend/components/agents-ui/agent-control-bar.tsx, frontend/components/app/session-view.tsx` | ALTA | 🟡 concluido |
| #15 | 07/11/2026 02:15:00 | **Botão TRADER B3 e Painel Quant com Acelerador de Viés e Ativos Correlacionados** | `frontend/components/app/quant-panel.tsx, frontend/app/api/quant-data/route.ts, frontend/components/agents-ui/agent-control-bar.tsx, frontend/components/app/session-view.tsx` | ALTA | 🟡 concluido |
| #14 | 07/11/2026 01:40:00 | **Especialista em Day Trade no Mini Índice (WIN - B3) & Plataforma Profit (Nelogica)** | `backend/trader_wini.py, backend/agent.py, backend/prompts.py, frontend/components/app/activity-panel.tsx` | ALTA | 🟡 concluido |
| #13 | 06/11/2026 03:17:00 | **Preservação total de abas anteriores, fala concisa ancorada na tela e pergunta de fechamento** | `backend/agent.py, backend/jarvis_browser.py, backend/jarvis_chrome_extension/service_worker.js, backend/prompts.py` | ALTA | 🟡 concluido |
| #12 | 06/11/2026 03:03:00 | **Condução autônoma da aula sem perguntas de permissão e ritmo adequado com nova aba garantida** | `backend/agent.py, backend/jarvis_browser.py, backend/prompts.py` | ALTA | 🟡 concluido |
| #11 | 06/11/2026 02:46:00 | **Ferramenta de clique visual de precisão em fotos, imagens e elementos na tela** | `backend/agent.py, backend/prompts.py` | ALTA | 🟡 concluido |
| #10 | 06/11/2026 02:25:00 | **Visão da tela em tempo real para aulas e navegação multi-aba dinâmica** | `backend/agent.py, backend/jarvis_browser.py, backend/jarvis_chrome_extension/service_worker.js, backend/prompts.py` | ALTA | 🟡 concluido |
| #9 | 06/11/2026 01:31:09 | **Remover lousa e implantar ensino didático com abertura de artigos visuais no navegador** | `frontend/components/app/session-view.tsx, backend/agent.py` | ALTA | 🟡 concluido |
| #8 | 06/11/2026 00:16:56 | **Corrigir interação com elementos SVG na lousa de ensino** | `frontend/components/app/teaching-board.tsx` | ALTA | 🟡 concluido |
| #7 | 05/11/2026 23:20:48 | **Modernização e otimização da Lousa de Ensino** | `frontend/components/app/teaching-board.tsx` | ALTA | 🟡 concluido |
| #6 | 05/11/2026 22:54:44 | **Implementação de desenho e manipulação SVG na Lousa de Ensino** | `frontend/components/app/teaching-board.tsx` | MÉDIA | 🟡 concluido |
| #5 | 05/11/2026 22:52:28 | **Adição de ferramenta nativa para duplicar item no Google Drive** | `backend/google_workspace.py` | ALTA | 🟡 cancelado |
| #4 | 05/11/2026 22:35:11 | **Desenvolvimento de Módulo de Auto-Monitoramento Neural e Otimização de Performance** | `backend/agent.py, frontend/components/app/system-dashboard.tsx` | ALTA | 🟡 cancelado |
| #3 | 05/11/2026 22:28:47 | **Re-executar duplicação exata de arquivo com imagens no Google Drive** | `backend/google_workspace.py` | ALTA | 🟡 cancelado |
| #2 | 05/11/2026 22:27:47 | **Corrigir duplicação de arquivo do Google Docs com preservação total de conteúdo e imagens** | `backend/google_workspace.py` | ALTA | 🟡 concluido |
| #1 | 05/11/2026 02:21:35 | **Modernização da Lousa de Ensino** | `frontend/components/app/teaching-board.tsx` | ALTA | 🟡 concluido |

## Detalhes da Última Solicitação:
- **ID**: #24
- **Título**: Melhorar estabilidade e precisão das operações de navegador (abrir, maximizar, focar)
- **Arquivo Alvo**: `backend/jarvis_browser.py`
- **Descrição**: O Jarvis está enfrentando dificuldades intermitentes para abrir, maximizar e gerenciar abas e sites no navegador (`abrir_site`, `maximizar_ou_restaurar_item_ou_janela`). É necessário revisar e modernizar o módulo `jarvis_browser.py` para garantir abertura instantânea, localização visual precisa e controle robusto de abas e janelas, suportando múltiplos monitores e restaurando o foco corretamente. Prioridade: ALTA.
- **Prioridade**: ALTA