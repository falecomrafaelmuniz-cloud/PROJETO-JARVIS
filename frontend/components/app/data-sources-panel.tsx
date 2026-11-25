'use client';

import React, { useState } from 'react';
import {
  Check,
  AlertTriangle,
  RefreshCw,
  ExternalLink,
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
}

const INITIAL_DATA_SOURCES: DataSourceItem[] = [
  {
    id: 'brapi',
    name: 'brapi (B3 & Finanças)',
    category: 'financas',
    description: 'Cotações de ações da B3, dividendos, fundos imobiliários e câmbio em tempo real.',
    status: 'connected',
    toolsCount: 8,
    voiceExample: 'Jarvis, quanto está a Petrobras e o dólar hoje?',
    tags: ['Ações', 'B3', 'Câmbio', 'Ibovespa', 'Dividendos'],
    docHelp: 'Conexão ativa direta com o mercado financeiro brasileiro. Responde em tempo real via voz.',
    lastSync: 'Há 1 minuto',
  },
  {
    id: 'gmail',
    name: 'Gmail & Notificações',
    category: 'produtividade',
    description: 'Leitura, busca inteligente, resumo de e-mails importantes e envio de rascunhos.',
    status: 'connected',
    toolsCount: 6,
    advanced: true,
    voiceExample: 'Jarvis, leia meus últimos e-mails importantes.',
    tags: ['E-mails', 'Inbox', 'Rascunhos', 'Alertas'],
    docHelp: 'Permite ao Jarvis monitorar e redigir e-mails diretamente com comando de voz no Windows.',
    lastSync: 'Tempo real',
  },
  {
    id: 'gdrive',
    name: 'Google Drive & Nuvem',
    category: 'produtividade',
    description: 'Acesso a documentos, planilhas, PDFs e pastas sincronizadas no Google Cloud.',
    status: 'connected',
    toolsCount: 5,
    advanced: true,
    voiceExample: 'Jarvis, abra meu Google Drive e busque a planilha de metas.',
    tags: ['Documentos', 'Cloud', 'Planilhas', 'Arquivos'],
    docHelp: 'Conecta ao Drive do usuário e abre arquivos em abas dedicadas no Chrome.',
    lastSync: 'Há 5 minutos',
  },
  {
    id: 'mercadolivre',
    name: 'Mercado Livre & Compras',
    category: 'produtividade',
    description: 'Auditoria antifraude de vendedores, reputação MercadoLíder e busca de menor preço.',
    status: 'connected',
    toolsCount: 4,
    voiceExample: 'Jarvis, pesquise o menor preço de iPhone 16 em lojas oficiais.',
    tags: ['Compras', 'Menor Preço', 'Lojas Oficiais', 'Reputação'],
    docHelp: 'Analisa termos de entrega, medalhas de reputação e abre abas verificadas.',
    lastSync: 'Sob demanda',
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp Web',
    category: 'comunicacao',
    description: 'Acesso rápido a conversas, envio de mensagens e monitoramento de notificações.',
    status: 'connected',
    toolsCount: 3,
    voiceExample: 'Jarvis, abra o WhatsApp na tela 2.',
    tags: ['Mensagens', 'Contatos', 'Grupos'],
    docHelp: 'Restaura a aba do WhatsApp Web e traz para o monitor desejado.',
    lastSync: 'Conectado',
  },
  {
    id: 'obsidian',
    name: 'Obsidian & Notas Locais',
    category: 'produtividade',
    description: 'Cofre em Markdown, anotações rápidas e base de conhecimento local no PC.',
    status: 'connected',
    toolsCount: 4,
    voiceExample: 'Jarvis, crie uma anotação no Obsidian sobre a reunião de hoje.',
    tags: ['Markdown', 'Notas', 'Cofre Local', 'Ideias'],
    docHelp: 'Leitura e escrita de arquivos .md diretamente no armazenamento local do computador.',
    lastSync: 'Local',
  },
  {
    id: 'youtube',
    name: 'YouTube & Multimídia',
    category: 'comunicacao',
    description: 'Busca de vídeos, reprodução automatizada, controle de player e atalhos de mídia.',
    status: 'connected',
    toolsCount: 5,
    voiceExample: 'Jarvis, coloque uma playlist lofi no YouTube para concentração.',
    tags: ['Música', 'Vídeos', 'Playlists', 'Midia'],
    docHelp: 'Controla abas do YouTube no navegador e pesquisa conteúdos em segundo plano.',
    lastSync: 'Tempo real',
  },
  {
    id: 'mcp-gemini',
    name: 'Servidor MCP: Gemini Deep Research',
    category: 'mcp',
    description: 'Protocolo MCP oficial para pesquisas aprofundadas na web, síntese e raciocínio.',
    status: 'connected',
    toolsCount: 12,
    advanced: true,
    voiceExample: 'Jarvis, faça uma pesquisa profunda sobre as tendências do mercado de IA.',
    tags: ['MCP', 'Raciocínio', 'Pesquisa Web', 'Docs'],
    docHelp: 'Servidor Model Context Protocol nativo executando em background.',
    lastSync: 'Operacional',
  },
  {
    id: 'mcp-desktop',
    name: 'Servidor MCP: Windows Motor Control',
    category: 'mcp',
    description: 'Comandos de baixo nível no Windows: automação de janelas, teclado, mouse e monitores.',
    status: 'connected',
    toolsCount: 15,
    voiceExample: 'Jarvis, mova o navegador para a tela 2 e abra a pasta downloads.',
    tags: ['MCP', 'Win32', 'Monitores', 'Automação'],
    docHelp: 'Servidor local com acesso nativo à Win32 API e UIAutomation.',
    lastSync: 'Ativo (Local)',
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
  const [activeTab, setActiveTab] = useState<'todos' | 'produtividade' | 'financas' | 'mcp'>('todos');
  const [searchQuery, setSearchQuery] = useState('');

  const { style, isDragging, wasDragged, dragHandlers } = useDraggableFloating('jarvis-pos-data-sources-v1');

  if (!isOpen) return null;

  const filteredSources = sources.filter((item) => {
    const matchesTab = activeTab === 'todos' || item.category === activeTab;
    const matchesSearch =
      searchQuery === '' ||
      item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));
    return matchesTab && matchesSearch;
  });

  const connectedCount = sources.filter((s) => s.status === 'connected').length;
  const mcpCount = sources.filter((s) => s.category === 'mcp').length;

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
        'fixed z-[1050] flex flex-col rounded-2xl border border-cyan-400/30 bg-slate-950/90 shadow-[0_0_40px_rgba(0,240,255,0.18)] backdrop-blur-2xl transition-shadow select-none',
        'w-[640px] max-h-[82vh] overflow-hidden',
        isDragging && 'ring-2 ring-cyan-300/80 cursor-grabbing'
      )}
    >
      {/* Header com controles da janela */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 bg-slate-900/60 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-cyan-400" />
          <span className="text-xs font-semibold tracking-wider text-cyan-200">
            CENTRAL DE FONTES DE DADOS & MCP
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 text-zinc-400 hover:bg-white/10 hover:text-white transition"
          title="Fechar painel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* KPI Cards Superiores (Estilo Lucas Victor) */}
      <div className="grid grid-cols-3 gap-2.5 px-4 pt-3 pb-2 border-b border-cyan-500/15 bg-slate-900/40">
        <div className="flex items-center gap-2.5 rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-3 py-2">
          <div className="flex size-7 items-center justify-center rounded-lg bg-emerald-500/20 text-emerald-400">
            <Check className="h-4 w-4" />
          </div>
          <div>
            <p className="text-[10px] font-mono tracking-wider text-emerald-400 uppercase font-semibold">
              Fontes Conectadas
            </p>
            <p className="text-sm font-bold text-slate-100">{connectedCount} ativas</p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 px-3 py-2">
          <div className="flex size-7 items-center justify-center rounded-lg bg-cyan-500/20 text-cyan-400">
            <Server className="h-4 w-4" />
          </div>
          <div>
            <p className="text-[10px] font-mono tracking-wider text-cyan-300 uppercase font-semibold">
              Servidores MCP
            </p>
            <p className="text-sm font-bold text-slate-100">{mcpCount} detectados</p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 rounded-xl border border-amber-500/30 bg-amber-950/20 px-3 py-2">
          <div className="flex size-7 items-center justify-center rounded-lg bg-amber-500/20 text-amber-400">
            <Radio className="h-4 w-4" />
          </div>
          <div>
            <p className="text-[10px] font-mono tracking-wider text-amber-300 uppercase font-semibold">
              Sincronização
            </p>
            <p className="text-sm font-bold text-slate-100">Tempo Real</p>
          </div>
        </div>
      </div>

      {/* Barra de Filtros e Busca */}
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-cyan-500/15">
        <div className="flex items-center gap-1.5">
          {(['todos', 'financas', 'produtividade', 'mcp'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={cn(
                'rounded-lg px-2.5 py-1 text-[11px] font-mono uppercase tracking-wider transition-all cursor-pointer',
                activeTab === tab
                  ? 'bg-cyan-500/25 border border-cyan-400/50 text-cyan-200 font-semibold shadow-[0_0_10px_rgba(0,240,255,0.2)]'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              )}
            >
              {tab === 'todos' ? 'Todas' : tab === 'financas' ? 'Finanças' : tab === 'produtividade' ? 'Produtividade' : 'MCP'}
            </button>
          ))}
        </div>

        <div className="relative flex items-center">
          <Search className="absolute left-2.5 h-3.5 w-3.5 text-slate-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Buscar conector..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-7 w-40 rounded-lg border border-cyan-500/20 bg-slate-900/60 pl-8 pr-2.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-cyan-400/60 focus:outline-none"
          />
        </div>
      </div>

      {/* Conteúdo Principal: Grid de Fontes ou Detalhe "Como Usar no HUD" */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2.5 max-h-[50vh]">
        {selectedSource ? (
          /* Visão Detalhada "Como usar no HUD" estilo Lucas Victor */
          <div className="rounded-xl border border-cyan-400/35 bg-slate-900/70 p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-3">
              <div className="flex items-center gap-2">
                <span className="flex size-2 rounded-full bg-emerald-400 animate-pulse" />
                <h3 className="font-mono text-sm font-bold uppercase tracking-wider text-cyan-200">
                  {selectedSource.name}
                </h3>
                {selectedSource.advanced && (
                  <span className="rounded bg-amber-500/20 border border-amber-400/40 px-1.5 py-0.5 text-[9px] font-mono uppercase tracking-wider text-amber-300">
                    Avançado
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => setSelectedSource(null)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <p className="text-xs text-slate-300 leading-relaxed">{selectedSource.description}</p>

            {/* Caixa "Como Usar no HUD" */}
            <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3 space-y-2">
              <div className="flex items-center gap-2 text-cyan-300 text-xs font-mono font-semibold uppercase tracking-wider">
                <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
                Como usar no HUD · Pergunte naturalmente ao Jarvis
              </div>
              <p className="text-xs text-slate-400">{selectedSource.docHelp}</p>

              <div className="flex flex-wrap gap-1.5 pt-1">
                {selectedSource.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-md border border-cyan-400/20 bg-slate-900/80 px-2 py-0.5 text-[10px] font-mono text-cyan-300"
                  >
                    {tag}
                  </span>
                ))}
              </div>

              {/* Chip de Exemplo de Voz */}
              <div className="mt-2 flex items-center justify-between rounded-lg border border-amber-400/30 bg-amber-950/20 px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-mono text-amber-300 font-semibold uppercase">Exemplo de fala:</span>
                  <span className="text-xs italic text-amber-100 font-serif">"{selectedSource.voiceExample}"</span>
                </div>
                {onOpenVoiceCommand && (
                  <button
                    type="button"
                    onClick={() => onOpenVoiceCommand(selectedSource.voiceExample)}
                    className="rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 text-[10px] px-2 py-1 font-mono uppercase tracking-wider transition cursor-pointer"
                  >
                    Falar agora
                  </button>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between pt-2">
              <span className="text-[11px] font-mono text-slate-400">Última sincronização: {selectedSource.lastSync}</span>
              <button
                type="button"
                onClick={() => setSelectedSource(null)}
                className="rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-3 py-1.5 text-xs font-mono uppercase text-cyan-200 hover:bg-cyan-500/25 transition cursor-pointer"
              >
                Voltar à lista
              </button>
            </div>
          </div>
        ) : (
          /* Grid de Cards dos Conectores estilo Lucas Victor */
          <div className="grid grid-cols-2 gap-2.5">
            {filteredSources.map((source) => (
              <div
                key={source.id}
                onClick={() => setSelectedSource(source)}
                className={cn(
                  'group flex flex-col justify-between rounded-xl border p-3 transition-all cursor-pointer',
                  'border-cyan-500/20 bg-slate-900/50 hover:border-cyan-400/50 hover:bg-slate-900/80 hover:shadow-[0_0_20px_rgba(0,240,255,0.15)]'
                )}
              >
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
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
                  <span className="text-emerald-400 font-semibold flex items-center gap-1">
                    <Check className="h-3 w-3" /> Conectado · {source.toolsCount} ações
                  </span>
                  <span className="text-cyan-400/70 group-hover:text-cyan-300 flex items-center">
                    Ver detalhes <ChevronRight className="h-3 w-3" />
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Rodapé com Status Operacional */}
      <div className="flex items-center justify-between px-4 py-2 border-t border-cyan-500/15 bg-slate-950/60 text-[10px] font-mono text-slate-400">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#10b981]" />
          <span className="text-emerald-300 font-semibold uppercase tracking-wider">
            SISTEMA ONLINE · Todas as conexões operacionais
          </span>
        </div>
        <span>Jarvis v3.0 Hub</span>
      </div>
    </div>
  );
}
