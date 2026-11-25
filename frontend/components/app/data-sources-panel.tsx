'use client';

import React, { useState } from 'react';
import {
  Check,
  AlertTriangle,
  RefreshCw,
  Plus,
  Cpu,
  Database,
  TrendingUp,
  Mail,
  HardDrive,
  FileText,
  MessageSquare,
  Activity,
  Layers,
  Sparkles,
  Server,
  X,
  ChevronRight,
  Sliders,
  Radio,
  Search,
  ExternalLink,
  Trash2,
  Clock,
  Printer,
  Calendar,
  Waves,
  Users,
  Compass,
  DollarSign,
  Zap,
} from 'lucide-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

export interface DataSourceItem {
  id: string;
  name: string;
  category: 'financas' | 'produtividade' | 'comunicacao' | 'sistema' | 'mcp';
  description: string;
  status: 'connected' | 'attention' | 'disconnected';
  toolsCount: number;
  advanced?: boolean;
  voiceExample: string;
  tags: string[];
  docHelp?: string;
  lastSync?: string;
  mcpServerUrl?: string;
  actionChips?: string[];
  speechChips?: string[];
}

export interface McpExternalServer {
  id: string;
  name: string;
  source: string;
  status: 'ativo' | 'desativado';
}

const INITIAL_DATA_SOURCES: DataSourceItem[] = [
  {
    id: 'plaud',
    name: 'Plaud',
    category: 'produtividade',
    description: 'Gravador de voz com IA, notas e transcrições de reuniões e ideias.',
    status: 'connected',
    toolsCount: 5,
    voiceExample: 'Jarvis, busque minhas últimas gravações do Plaud e resuma os insights.',
    tags: ['Reuniões', 'Transcrições', 'Áudio', 'Notas'],
    docHelp: 'Acesse gravações do gravador Plaud e resumos inteligentes estruturados.',
    lastSync: 'Há 4 minutos',
    actionChips: ['áudio', 'reuniões', 'resumos'],
    speechChips: ['"Quais foram as reuniões de hoje?"', '"Resuma a gravação do Plaud."'],
  },
  {
    id: 'ewelink',
    name: 'eWeLink',
    category: 'mcp',
    description: 'Servidor MCP remoto para iluminação, interruptores Sonoff e tomadas inteligentes.',
    status: 'connected',
    toolsCount: 2,
    voiceExample: 'Jarvis, acende a luz da sala.',
    tags: ['luzes', 'tomadas', 'interruptores', 'ventiladores', 'aparelhos da casa'],
    docHelp: 'No app eWeLink, abra Perfil -> Pilot Features -> MCP Server e copie a URL do servidor.',
    lastSync: 'Conectado · 2 ferramentas',
    mcpServerUrl: 'https://eu-apia.coolkit.cc/v2/mcp/passthrough?key=ewelink_live_demo_9841',
    actionChips: ['luzes', 'tomadas', 'interruptores', 'ventiladores', 'aparelhos da casa'],
    speechChips: ['"Acende a luz da sala."', '"Apaga todas as luzes."', '"Liga o ventilador."'],
  },
  {
    id: 'brapi',
    name: 'brapi',
    category: 'financas',
    description: 'Cotações de ações da B3, dividendos, fundos imobiliários e taxas em tempo real.',
    status: 'connected',
    toolsCount: 8,
    voiceExample: 'Jarvis, qual a cotação de PETR4 e VALE3 agora?',
    tags: ['Ações B3', 'FIIs', 'Dividendos', 'Câmbio'],
    docHelp: 'Cotações oficiais da bolsa brasileira atualizadas a cada minuto no HUD.',
    lastSync: 'Há 1 minuto',
    actionChips: ['cotações', 'dividendos', 'mercado', 'carteira'],
    speechChips: ['"Como fechou a Petrobras hoje?"', '"Mostre as maiores altas da B3."'],
  },
  {
    id: 'bambu',
    name: 'Impressora 3D (Bambu Lab)',
    category: 'produtividade',
    description: 'Monitoramento de temperatura do bico, progresso de impressão e filamento restante.',
    status: 'connected',
    toolsCount: 4,
    voiceExample: 'Jarvis, qual o status da impressão na Bambu Lab?',
    tags: ['Impressão 3D', 'Bambu Lab', 'Filamento', 'Status'],
    docHelp: 'Acompanhe impressões 3D em tempo real e receba alertas de conclusão.',
    lastSync: 'Conectada · operacional',
    actionChips: ['status', 'câmera', 'temperatura', 'filamento'],
    speechChips: ['"A impressora 3D já terminou?"', '"Qual a temperatura do bico?"'],
  },
  {
    id: 'gdrive',
    name: 'Google Drive',
    category: 'produtividade',
    description: 'Documentos, planilhas, pastas e arquivos sincronizados na nuvem Google.',
    status: 'attention',
    advanced: true,
    toolsCount: 5,
    voiceExample: 'Jarvis, encontre a planilha financeira no meu Google Drive.',
    tags: ['Arquivos', 'Nuvem', 'Planilhas', 'Docs'],
    docHelp: 'Conexão ativa · precisa de atenção para renovar token de leitura.',
    lastSync: 'Conectada · precisa de atenção',
    actionChips: ['buscar', 'abrir', 'planilhas', 'compartilhar'],
    speechChips: ['"Abra o arquivo de metas no Drive."', '"Busque o PDF do contrato."'],
  },
  {
    id: 'obsidian',
    name: 'Obsidian',
    category: 'produtividade',
    description: 'Cofre em Markdown, anotações interligadas e segundo cérebro local.',
    status: 'disconnected',
    toolsCount: 3,
    voiceExample: 'Jarvis, salve esta ideia em uma nova nota no Obsidian.',
    tags: ['Markdown', 'Segundo Cérebro', 'Notas', 'Cofre'],
    docHelp: 'Acesso direto ao cofre de notas em formato Markdown local no seu computador.',
    lastSync: 'Não conectado',
    actionChips: ['nova nota', 'buscar', 'tags'],
    speechChips: ['"Crie uma nota sobre o projeto."', '"Leia a nota sobre reuniões."'],
  },
  {
    id: 'whoop',
    name: 'WHOOP',
    category: 'sistema',
    description: 'Métricas de biometria, recuperação física, sono e esforço cardiovascular.',
    status: 'connected',
    advanced: true,
    toolsCount: 4,
    voiceExample: 'Jarvis, qual meu score de recuperação e sono hoje no WHOOP?',
    tags: ['Sono', 'Recuperação', 'Strain', 'Saúde'],
    docHelp: 'Monitore sua prontidão biológica e sono sincronizados com o HUD.',
    lastSync: 'Conectada · operacional',
    actionChips: ['recuperação', 'sono', 'strain', 'batimentos'],
    speechChips: ['"Como foi meu sono esta noite?"', '"Estou pronto para treinar pesado?"'],
  },
  {
    id: 'notion',
    name: 'Notion',
    category: 'produtividade',
    description: 'Páginas e bancos de dados, quadros Kanban e documentação de projetos.',
    status: 'connected',
    toolsCount: 6,
    voiceExample: 'Jarvis, mostre as tarefas pendentes no quadro de projetos do Notion.',
    tags: ['Kanban', 'Bancos de Dados', 'Docs', 'Projetos'],
    docHelp: 'Lê e atualiza quadros de tarefas e anotações nos workspaces do Notion.',
    lastSync: 'Conectada · operacional',
    actionChips: ['tarefas', 'projetos', 'bancos de dados'],
    speechChips: ['"Adicione uma tarefa no Notion."', '"Quais tarefas vencem hoje?"'],
  },
  {
    id: 'alphavantage',
    name: 'Alpha Vantage',
    category: 'financas',
    description: 'Cotações de moedas internacionais, Forex, S&P500 e dados macroeconômicos.',
    status: 'attention',
    toolsCount: 5,
    voiceExample: 'Jarvis, quanto está a cotação do Bitcoin e do S&P 500?',
    tags: ['Forex', 'Cripto', 'Macro', 'S&P 500'],
    docHelp: 'API financeira internacional de alta frequência. Limite de requisições próximo.',
    lastSync: 'Conectada · precisa de atenção',
    actionChips: ['forex', 'cripto', 'bolsa global'],
    speechChips: ['"Qual a variação do Bitcoin hoje?"', '"Cotação do Dólar x Euro."'],
  },
  {
    id: 'gcalendar',
    name: 'Google Agenda',
    category: 'produtividade',
    description: 'Compromissos, reuniões, conferências do Meet e lembretes com horário.',
    status: 'connected',
    advanced: true,
    toolsCount: 7,
    voiceExample: 'Jarvis, quais são meus compromissos agendados para amanhã?',
    tags: ['Agenda', 'Meet', 'Reuniões', 'Horários'],
    docHelp: 'Sincroniza eventos e avisa minutos antes de cada reunião começar.',
    lastSync: 'Conectada · operacional',
    actionChips: ['hoje', 'amanhã', 'criar evento', 'reuniões'],
    speechChips: ['"O que tenho na agenda hoje?"', '"Marque um café amanhã às 15h."'],
  },
  {
    id: 'gmail',
    name: 'Gmail',
    category: 'produtividade',
    description: 'Monitoramento da caixa de entrada, síntese de e-mails prioritários e rascunhos.',
    status: 'connected',
    toolsCount: 6,
    voiceExample: 'Jarvis, leia meus últimos e-mails urgentes.',
    tags: ['E-mails', 'Caixa de Entrada', 'Rascunhos', 'Alertas'],
    docHelp: 'Permite ao Jarvis monitorar e redigir e-mails diretamente com comando de voz.',
    lastSync: 'Conectada · operacional',
    actionChips: ['novos e-mails', 'resumo', 'rascunho'],
    speechChips: ['"Recebi algum e-mail urgente?"', '"Resuma o e-mail do cliente."'],
  },
  {
    id: 'strava',
    name: 'Strava',
    category: 'sistema',
    description: 'Atividades físicas, treinos de corrida, ciclismo e distância percorrida.',
    status: 'connected',
    advanced: true,
    toolsCount: 3,
    voiceExample: 'Jarvis, qual foi meu ritmo na última corrida no Strava?',
    tags: ['Corrida', 'Ciclismo', 'Pace', 'Treinos'],
    docHelp: 'Sincroniza estatísticas de treino e quilometragem semanal.',
    lastSync: 'Conectada · operacional',
    actionChips: ['último treino', 'ritmo', 'distância semanal'],
    speechChips: ['"Quantos km corri nesta semana?"', '"Qual foi meu pace médio?"'],
  },
  {
    id: 'sendpaste',
    name: 'Enviar ou colar',
    category: 'produtividade',
    description: 'Cole texto ou envie documentos para análise instantânea de IA no HUD.',
    status: 'connected',
    toolsCount: 2,
    voiceExample: 'Jarvis, analise o texto que acabei de colar na área de transferência.',
    tags: ['Clipboard', 'Documentos', 'Análise', 'Rápido'],
    docHelp: 'Permite enviar arquivos PDF, imagens e textos soltos para leitura direta.',
    lastSync: 'Conectada · operacional',
    actionChips: ['colar', 'analisar', 'upload'],
    speechChips: ['"Analise este contrato que colei."', '"Resuma este documento."'],
  },
  {
    id: 'granola',
    name: 'Granola',
    category: 'produtividade',
    description: 'Notas e transcrições de reuniões do Zoom e Google Meet.',
    status: 'disconnected',
    toolsCount: 4,
    voiceExample: 'Jarvis, conecte ao Granola para importar notas de reuniões.',
    tags: ['Reuniões', 'Meet', 'Notas', 'Transcrição'],
    docHelp: 'Gera atas automáticas de reuniões e planos de ação.',
    lastSync: 'Não conectado',
    actionChips: ['atas', 'reuniões', 'ações'],
    speechChips: ['"Importar atas de reuniões do Granola."'],
  },
  {
    id: 'banco',
    name: 'Banco',
    category: 'financas',
    description: 'Saldos bancários, extratos, faturas de cartão e alertas de cobrança.',
    status: 'connected',
    toolsCount: 16,
    voiceExample: 'Jarvis, qual meu saldo consolidado e fatura prevista do cartão?',
    tags: ['Contas', 'Extrato', 'Faturas', 'Cartão'],
    docHelp: 'Visualização de saldos consolidados e gestão financeira com Open Finance.',
    lastSync: 'Conectado · 16 ferramentas',
    actionChips: ['saldo', 'fatura do cartão', 'extrato', 'despesas'],
    speechChips: ['"Qual o total das faturas do mês?"', '"Tive algum débito não esperado?"'],
  },
  {
    id: 'worldtides',
    name: 'WorldTides',
    category: 'sistema',
    description: 'Previsão de marés oceânicas, tábua de marés e condições marítimas.',
    status: 'connected',
    toolsCount: 3,
    voiceExample: 'Jarvis, qual a maré agora em Santos e previsão para o fim de tarde?',
    tags: ['Marés', 'Oceano', 'Praia', 'Navegação'],
    docHelp: 'Consulta altura de maré no datum CD em tempo real para litoral e esportes aquáticos.',
    lastSync: 'Conectada · marés no datum CD',
    actionChips: ['maré agora', 'próxima preamar', 'baixa-mar'],
    speechChips: ['"Como está a maré agora em Santos?"', '"A maré está subindo ou descendo?"'],
  },
  {
    id: 'googlecontatos',
    name: 'Google Contatos',
    category: 'comunicacao',
    description: 'Agenda de pessoas, números de telefone, e-mails e histórico de contatos.',
    status: 'connected',
    advanced: true,
    toolsCount: 4,
    voiceExample: 'Jarvis, qual o telefone do Dr. Paulo nos meus contatos?',
    tags: ['Pessoas', 'Telefones', 'Contatos', 'E-mails'],
    docHelp: 'Permite ao Jarvis identificar nomes de pessoas e contatos ao receber mensagens.',
    lastSync: 'Pessoas e contatos operacionais',
    actionChips: ['buscar contato', 'telefone', 'e-mail'],
    speechChips: ['"Encontre o número do João."', '"Qual o e-mail de contato da empresa?"'],
  },
  {
    id: 'hackernews',
    name: 'Hacker News',
    category: 'produtividade',
    description: 'Feed de notícias de tecnologia, lançamentos de startups e tendências em IA.',
    status: 'connected',
    toolsCount: 3,
    voiceExample: 'Jarvis, quais são as notícias mais votadas no Hacker News hoje?',
    tags: ['Tech', 'Notícias', 'Startups', 'IA'],
    docHelp: 'Monitora os tópicos mais relevantes da indústria de tecnologia mundial.',
    lastSync: 'Conectada · operacional',
    actionChips: ['top stories', 'notícias de IA', 'lançamentos'],
    speechChips: ['"O que está em alta no Hacker News?"', '"Algum lançamento importante de IA hoje?"'],
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp',
    category: 'comunicacao',
    description: 'Mensagens instantâneas, leitura de conversas e avisos importantes.',
    status: 'connected',
    toolsCount: 4,
    voiceExample: 'Jarvis, abra o WhatsApp e veja se tenho mensagens novas.',
    tags: ['Mensagens', 'Conversas', 'Contatos', 'Avisos'],
    docHelp: 'Conexão com WhatsApp Web com foco em produtividade e lembretes rápidos.',
    lastSync: 'Conectada · operacional',
    actionChips: ['mensagens não lidas', 'abrir chat', 'notificações'],
    speechChips: ['"Tem mensagem nova no WhatsApp?"', '"Abra o WhatsApp na segunda tela."'],
  },
];

const INITIAL_EXTERNAL_MCP_SERVERS: McpExternalServer[] = [
  {
    id: 'code-review',
    name: 'code-review',
    source: 'Codex · plugin code-review',
    status: 'desativado',
  },
  {
    id: 'codex_app',
    name: 'codex_app',
    source: 'Codex · plugin codex-app-tools',
    status: 'desativado',
  },
  {
    id: 'gemini-research',
    name: 'gemini_deep_research',
    source: 'Google Gemini MCP Server',
    status: 'ativo',
  },
  {
    id: 'desktop-automation',
    name: 'windows_motor_mcp',
    source: 'Desktop Local Automation Tools',
    status: 'ativo',
  },
];

interface DataSourcesPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenVoiceCommand?: (cmd: string) => void;
}

export function DataSourcesPanel({ isOpen, onClose, onOpenVoiceCommand }: DataSourcesPanelProps) {
  const [sources, setSources] = useState<DataSourceItem[]>(INITIAL_DATA_SOURCES);
  const [selectedSource, setSelectedSource] = useState<DataSourceItem | null>(null);
  const [externalMcp, setExternalMcp] = useState<McpExternalServer[]>(INITIAL_EXTERNAL_MCP_SERVERS);
  const [activeTab, setActiveTab] = useState<'todos' | 'produtividade' | 'financas' | 'comunicacao' | 'mcp'>('todos');
  const [searchQuery, setSearchQuery] = useState('');
  const [isAddingMcp, setIsAddingMcp] = useState(false);
  const [newMcpUrl, setNewMcpUrl] = useState('');
  const [isVerifying, setIsVerifying] = useState(false);
  const [verifyStatus, setVerifyStatus] = useState<string | null>(null);

  const { style, isDragging, wasDragged, dragHandlers } = useDraggableFloating('jarvis-pos-data-sources-v1');

  if (!isOpen) return null;

  const filteredSources = sources.filter((item) => {
    const matchesTab = activeTab === 'todos' || item.category === activeTab;
    const matchesSearch =
      searchQuery === '' ||
      item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase())) ||
      item.description.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesTab && matchesSearch;
  });

  const connectedCount = sources.filter((s) => s.status === 'connected').length;
  const attentionCount = sources.filter((s) => s.status === 'attention').length;
  const scheduledCount = 5; // Fontes com atualização programada
  const pendingCount = sources.filter((s) => s.status === 'disconnected').length;

  const handleVerifyConnection = (source: DataSourceItem) => {
    setIsVerifying(true);
    setVerifyStatus('Verificando integridade com o servidor MCP...');
    setTimeout(() => {
      setIsVerifying(false);
      setVerifyStatus(`Servidor MCP online: ${source.toolsCount} ferramentas sincronizadas.`);
    }, 900);
  };

  const handleToggleSourceConnection = (sourceId: string) => {
    setSources((prev) =>
      prev.map((s) => {
        if (s.id !== sourceId) return s;
        const nextStatus = s.status === 'connected' ? 'disconnected' : 'connected';
        return { ...s, status: nextStatus };
      })
    );
  };

  const handleAddExternalMcp = () => {
    if (!newMcpUrl.trim()) return;
    const newServer: McpExternalServer = {
      id: `mcp-${Date.now()}`,
      name: newMcpUrl.split('/').pop() || 'custom_mcp',
      source: 'Servidor MCP Remoto Cadastrado',
      status: 'ativo',
    };
    setExternalMcp((prev) => [...prev, newServer]);
    setNewMcpUrl('');
    setIsAddingMcp(false);
  };

  return (
    <div
      data-hud-panel="data-sources"
      style={style}
      {...dragHandlers}
      onClickCapture={(e) => {
        if (wasDragged()) {
          e.stopPropagation();
          e.preventDefault();
        }
      }}
      className={cn(
        'fixed z-[1050] flex flex-col rounded-2xl border border-cyan-400/30 bg-slate-950/95 shadow-[0_0_50px_rgba(0,240,255,0.22)] backdrop-blur-2xl transition-shadow select-none',
        'w-[720px] max-h-[85vh] overflow-hidden text-slate-100 font-sans',
        isDragging && 'ring-2 ring-cyan-300/80 cursor-grabbing'
      )}
    >
      {/* Cabeçalho Tecnológico com Botão Fechar */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 bg-slate-900/80 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex size-6 items-center justify-center rounded-lg bg-cyan-500/20 text-cyan-400 border border-cyan-400/40">
            <Database className="h-3.5 w-3.5" />
          </div>
          <div>
            <h2 className="text-xs font-mono font-bold tracking-wider uppercase text-cyan-200">
              CENTRAL DE FONTES DE DADOS & SERVIDORES MCP
            </h2>
            <p className="text-[10px] font-mono text-cyan-400/70">
              Conexões ativas, ferramentas disponíveis e integrações inteligentes
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10 hover:text-white transition cursor-pointer"
          title="Fechar painel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Os 4 Cards de KPI Oficiais do Lucas Victor */}
      <div className="grid grid-cols-4 gap-2 px-4 py-3 border-b border-cyan-500/15 bg-slate-900/40">
        {/* KPI 1: Fontes Conectadas */}
        <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-2.5 py-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-emerald-500/20 text-emerald-400">
            <Check className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[9px] font-mono tracking-wider text-emerald-400 uppercase font-semibold leading-tight truncate">
              Fontes Conect.
            </p>
            <p className="text-xs font-bold text-slate-100">{connectedCount} conectadas</p>
          </div>
        </div>

        {/* KPI 2: Saúde das Conexões */}
        <div className="flex items-center gap-2 rounded-xl border border-purple-500/30 bg-purple-950/20 px-2.5 py-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-purple-500/20 text-purple-400">
            <AlertTriangle className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[9px] font-mono tracking-wider text-purple-300 uppercase font-semibold leading-tight truncate">
              Saúde Conexões
            </p>
            <p className="text-xs font-bold text-slate-100">{attentionCount} pedem atenção</p>
          </div>
        </div>

        {/* KPI 3: Atualização Automática */}
        <div className="flex items-center gap-2 rounded-xl border border-cyan-500/30 bg-cyan-950/20 px-2.5 py-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/20 text-cyan-400">
            <Clock className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[9px] font-mono tracking-wider text-cyan-300 uppercase font-semibold leading-tight truncate">
              Atualização Auto
            </p>
            <p className="text-xs font-bold text-slate-100">{scheduledCount} ativas</p>
          </div>
        </div>

        {/* KPI 4: Novas Conexões */}
        <div className="flex items-center gap-2 rounded-xl border border-blue-500/30 bg-blue-950/20 px-2.5 py-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-blue-500/20 text-blue-400">
            <Plus className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[9px] font-mono tracking-wider text-blue-300 uppercase font-semibold leading-tight truncate">
              Novas Conexões
            </p>
            <p className="text-xs font-bold text-slate-100">{pendingCount} para conectar</p>
          </div>
        </div>
      </div>

      {/* Barra de Filtros e Busca */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-cyan-500/15 bg-slate-900/30">
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {(['todos', 'produtividade', 'financas', 'comunicacao', 'mcp'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={cn(
                'rounded-lg px-2.5 py-1 text-[11px] font-mono uppercase tracking-wider transition-all cursor-pointer whitespace-nowrap',
                activeTab === tab
                  ? 'bg-cyan-500/25 border border-cyan-400/50 text-cyan-200 font-semibold shadow-[0_0_10px_rgba(0,240,255,0.2)]'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              )}
            >
              {tab === 'todos'
                ? 'Todos'
                : tab === 'produtividade'
                  ? 'Produtividade'
                  : tab === 'financas'
                    ? 'Finanças'
                    : tab === 'comunicacao'
                      ? 'Comunicação'
                      : 'MCP Hub'}
            </button>
          ))}
        </div>

        <div className="relative flex items-center shrink-0">
          <Search className="absolute left-2.5 h-3.5 w-3.5 text-slate-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Buscar conector..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-7 w-44 rounded-lg border border-cyan-500/20 bg-slate-900/60 pl-8 pr-2.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-cyan-400/60 focus:outline-none"
          />
        </div>
      </div>

      {/* Conteúdo Principal Scrollável */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 max-h-[55vh]">
        {selectedSource ? (
          /* Visualização Expansível Idêntica ao eWeLink (Fotos 10, 12, 15) */
          <div className="rounded-xl border border-cyan-400/40 bg-slate-900/80 p-4 space-y-3.5 shadow-[0_0_25px_rgba(0,240,255,0.1)]">
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2.5">
              <div className="flex items-center gap-2.5">
                <span
                  className={cn(
                    'size-2.5 rounded-full',
                    selectedSource.status === 'connected'
                      ? 'bg-emerald-400 shadow-[0_0_8px_#10b981]'
                      : selectedSource.status === 'attention'
                        ? 'bg-amber-400 shadow-[0_0_8px_#f59e0b]'
                        : 'bg-slate-500'
                  )}
                />
                <h3 className="font-mono text-sm font-bold uppercase tracking-wider text-cyan-200">
                  {selectedSource.name}
                </h3>
                <span className="text-[11px] font-mono text-cyan-300/80">
                  {selectedSource.status === 'connected' ? `Conectado · ${selectedSource.toolsCount} ferramentas` : 'Desconectado'}
                </span>
                {selectedSource.advanced && (
                  <span className="rounded bg-amber-500/20 border border-amber-400/40 px-1.5 py-0.2 text-[9px] font-mono uppercase tracking-wider text-amber-300">
                    Avançado
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setSelectedSource(null);
                  setVerifyStatus(null);
                }}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Texto de Ajuda / Instruções de conexão */}
            <p className="text-xs text-slate-300 leading-relaxed font-sans">{selectedSource.docHelp}</p>

            {/* Caixa Azul "COMO USAR NO HUD" com Chips de Voz */}
            <div className="rounded-xl border border-cyan-500/35 bg-cyan-950/30 p-3.5 space-y-2.5">
              <div className="flex items-center gap-2 text-cyan-300 text-xs font-mono font-semibold uppercase tracking-wider">
                <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
                Como usar no HUD · Pergunte naturalmente ao JARVIS
              </div>

              {/* Chips de tags/ferramentas */}
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                {(selectedSource.actionChips || selectedSource.tags).map((chip) => (
                  <span
                    key={chip}
                    className="rounded-md border border-cyan-400/25 bg-slate-900/90 px-2.5 py-0.5 text-[10px] font-mono text-cyan-300 shadow-xs"
                  >
                    {chip}
                  </span>
                ))}
              </div>

              {/* Sugestões de Falas ao Jarvis */}
              <div className="space-y-1.5 pt-1">
                {(selectedSource.speechChips || [selectedSource.voiceExample]).map((speech, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between rounded-lg border border-amber-400/30 bg-amber-950/25 px-3 py-1.5"
                  >
                    <span className="text-xs italic text-amber-100 font-serif">{speech}</span>
                    {onOpenVoiceCommand && (
                      <button
                        type="button"
                        onClick={() => onOpenVoiceCommand(speech.replace(/^"|"$/g, ''))}
                        className="rounded bg-amber-500/20 hover:bg-amber-500/35 text-amber-300 text-[10px] px-2 py-0.5 font-mono uppercase tracking-wider transition cursor-pointer"
                      >
                        Falar
                      </button>
                    )}
                  </div>
                ))}
              </div>

              <p className="text-[10px] font-mono text-cyan-400/80 pt-1">
                ⓘ Ligar, consultar e executar acontece na hora; alterações profundas pedem confirmação de voz.
              </p>
            </div>

            {/* Bloco de Conexão com Servidor Remoto MCP (Foto 10 e 15) */}
            <div className="rounded-xl border border-slate-700/60 bg-slate-950/60 p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-slate-200 font-semibold">
                  Servidor Remoto · {selectedSource.toolsCount} ferramentas disponíveis
                </span>
                <span className="text-[10px] font-mono text-emerald-400">Protocolo MCP 1.0</span>
              </div>

              <div className="space-y-1">
                <label className="text-[10px] font-mono text-slate-400 uppercase">
                  Endereço do Servidor (vazio mantém o atual):
                </label>
                <input
                  type="text"
                  defaultValue={selectedSource.mcpServerUrl || 'https://mcp.jarvis.local/v1/tools'}
                  className="h-7 w-full rounded-lg border border-cyan-500/30 bg-slate-900/80 px-2.5 font-mono text-xs text-cyan-300 focus:border-cyan-400 focus:outline-none"
                />
              </div>

              {verifyStatus && (
                <p className="text-[11px] font-mono text-cyan-300 bg-cyan-950/40 p-1.5 rounded border border-cyan-500/20">
                  {verifyStatus}
                </p>
              )}

              <div className="flex items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => handleVerifyConnection(selectedSource)}
                  disabled={isVerifying}
                  className="flex items-center gap-1.5 rounded-lg border border-cyan-400/40 bg-cyan-500/15 hover:bg-cyan-500/25 px-3 py-1.5 text-xs font-mono uppercase text-cyan-200 transition cursor-pointer"
                >
                  <RefreshCw className={cn('h-3.5 w-3.5', isVerifying && 'animate-spin')} />
                  {isVerifying ? 'Verificando...' : 'Verificar conexão'}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    handleToggleSourceConnection(selectedSource.id);
                    setSelectedSource(null);
                  }}
                  className={cn(
                    'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-mono uppercase transition cursor-pointer',
                    selectedSource.status === 'connected'
                      ? 'border-red-500/40 bg-red-950/30 text-red-300 hover:bg-red-950/50'
                      : 'border-emerald-500/40 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-950/50'
                  )}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {selectedSource.status === 'connected' ? 'Desconectar' : 'Conectar agora'}
                </button>
              </div>
            </div>
          </div>
        ) : (
          /* Grid de Cards dos Conectores (Foto 1, 4, 13) */
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2.5">
              {filteredSources.map((source) => (
                <div
                  key={source.id}
                  onClick={() => setSelectedSource(source)}
                  className={cn(
                    'group flex flex-col justify-between rounded-xl border p-3 transition-all cursor-pointer',
                    'border-cyan-500/20 bg-slate-900/60 hover:border-cyan-400/50 hover:bg-slate-900/90 hover:shadow-[0_0_20px_rgba(0,240,255,0.15)]'
                  )}
                >
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            'size-2 rounded-full',
                            source.status === 'connected'
                              ? 'bg-emerald-400 animate-pulse'
                              : source.status === 'attention'
                                ? 'bg-amber-400'
                                : 'bg-slate-500'
                          )}
                        />
                        <h4 className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-300 transition">
                          {source.name}
                        </h4>
                      </div>
                      {source.advanced && (
                        <span className="rounded bg-amber-500/15 border border-amber-400/30 px-1 py-0.2 text-[8px] font-mono uppercase tracking-wider text-amber-300">
                          Avançado
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                      {source.description}
                    </p>
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-slate-800/80 pt-2 text-[10px] font-mono">
                    <span
                      className={cn(
                        'font-semibold flex items-center gap-1',
                        source.status === 'connected'
                          ? 'text-emerald-400'
                          : source.status === 'attention'
                            ? 'text-amber-400'
                            : 'text-slate-400'
                      )}
                    >
                      {source.status === 'connected' ? (
                        <>
                          <Check className="h-3 w-3" /> Conectado · {source.toolsCount} ações
                        </>
                      ) : source.status === 'attention' ? (
                        <>
                          <AlertTriangle className="h-3 w-3" /> Requer Atenção
                        </>
                      ) : (
                        <>
                          <Plus className="h-3 w-3" /> + Adicionar
                        </>
                      )}
                    </span>
                    <span className="text-cyan-400/70 group-hover:text-cyan-300 flex items-center">
                      Detalhes <ChevronRight className="h-3 w-3" />
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* Seção FERRAMENTAS EXTERNAS - Servidores MCP (Foto 13) */}
            <div className="rounded-xl border border-cyan-500/25 bg-slate-900/40 p-3.5 space-y-3">
              <div className="flex items-center justify-between border-b border-cyan-500/15 pb-2">
                <div>
                  <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-cyan-200 flex items-center gap-2">
                    <Server className="h-3.5 w-3.5 text-cyan-400" />
                    FERRAMENTAS EXTERNAS · Servidores MCP
                  </h3>
                  <p className="text-[10px] font-mono text-slate-400">
                    Servidores adicionais sem conector próprio · Detectados nos clientes
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setIsAddingMcp((prev) => !prev)}
                  className="flex items-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-500/10 hover:bg-cyan-500/20 px-2.5 py-1 text-[10px] font-mono uppercase text-cyan-200 transition cursor-pointer"
                >
                  <Plus className="h-3 w-3" /> Adicionar Servidor
                </button>
              </div>

              {isAddingMcp && (
                <div className="flex items-center gap-2 p-2 rounded-lg border border-cyan-400/30 bg-slate-950/70">
                  <input
                    type="text"
                    placeholder="Cole a URL ou comando do servidor MCP (ex: https://... ou npx -y ...)"
                    value={newMcpUrl}
                    onChange={(e) => setNewMcpUrl(e.target.value)}
                    className="h-7 flex-1 rounded border border-cyan-500/30 bg-slate-900 px-2 text-xs text-cyan-200 focus:border-cyan-400 focus:outline-none font-mono"
                  />
                  <button
                    type="button"
                    onClick={handleAddExternalMcp}
                    className="rounded bg-cyan-500 px-3 py-1 text-xs font-mono font-bold text-slate-950 hover:bg-cyan-400 transition cursor-pointer"
                  >
                    Salvar
                  </button>
                </div>
              )}

              <div className="grid grid-cols-2 gap-2">
                {externalMcp.map((srv) => (
                  <div
                    key={srv.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs"
                  >
                    <div>
                      <span className="font-mono font-bold text-slate-200 block">{srv.name}</span>
                      <span className="text-[10px] text-slate-400 font-mono">{srv.source}</span>
                    </div>
                    <span
                      className={cn(
                        'text-[10px] font-mono px-2 py-0.5 rounded border',
                        srv.status === 'ativo'
                          ? 'border-emerald-500/30 bg-emerald-950/30 text-emerald-300'
                          : 'border-slate-700 bg-slate-800/40 text-slate-400'
                      )}
                    >
                      {srv.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Rodapé Operacional com Status Neon */}
      <div className="flex items-center justify-between px-4 py-2 border-t border-cyan-500/15 bg-slate-950/80 text-[10px] font-mono text-slate-400">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#10b981]" />
          <span className="text-emerald-300 font-semibold uppercase tracking-wider">
            SISTEMA ONLINE · Protocolo Model Context Protocol Ativo
          </span>
        </div>
        <span>JARVIS · MCP Hub v3.2</span>
      </div>
    </div>
  );
}
