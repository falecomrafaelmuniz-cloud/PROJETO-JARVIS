'use client';

import React, { useState } from 'react';
import {
  MessageSquare,
  Sparkles,
  FileText,
  Database,
  Brain,
  Activity,
  TrendingUp,
  ScrollText,
  Settings,
  HelpCircle,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Layers,
  Radio,
  Sliders,
  ExternalLink,
  Shield,
  Zap,
} from 'lucide-react';
import { cn } from '@/lib/shadcn/utils';

export interface JarvisSidebarProps {
  isOpen: boolean;
  onToggleOpen: () => void;
  onSelectAction: (actionId: string) => void;
  activePanel?: string;
  isOrbMode?: boolean;
}

export function JarvisSidebar({
  isOpen,
  onToggleOpen,
  onSelectAction,
  activePanel,
  isOrbMode = false,
}: JarvisSidebarProps) {
  const [dataDropdownOpen, setDataDropdownOpen] = useState(true);

  // No Modo Orb, a sidebar fica 100% invisível para não poluir a tela
  if (isOrbMode) return null;

  return (
    <>
      {/* Botão de Toggle na borda esquerda (quando recolhido) */}
      {!isOpen && (
        <button
          type="button"
          onClick={onToggleOpen}
          title="Abrir Menu do Jarvis"
          className={cn(
            'fixed top-1/2 left-0 -translate-y-1/2 z-[1020]',
            'flex h-14 w-6 items-center justify-center rounded-r-xl border border-l-0 border-cyan-400/40',
            'bg-slate-950/85 text-cyan-300 shadow-[0_0_20px_rgba(0,240,255,0.25)] backdrop-blur-xl',
            'hover:w-8 hover:bg-cyan-500/20 hover:text-cyan-100 transition-all cursor-pointer'
          )}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      )}

      {/* Sidebar Lateral Completa (Estilo Lucas Victor) */}
      <aside
        className={cn(
          'fixed top-0 left-0 bottom-0 z-[1030] flex flex-col',
          'w-[310px] sm:w-[330px] border-r border-cyan-500/25 bg-slate-950/92 shadow-[0_0_40px_rgba(0,0,0,0.85)] backdrop-blur-2xl',
          'transition-transform duration-300 select-none overflow-hidden',
          isOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        {/* Header do Menu */}
        <div className="flex items-center justify-between border-b border-cyan-500/20 px-4 py-3.5 bg-slate-900/40">
          <div>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#00f0ff] animate-pulse" />
              <h2 className="font-mono text-sm font-bold tracking-[0.25em] uppercase text-cyan-200">
                JARVIS
              </h2>
            </div>
            <p className="text-[9px] font-mono tracking-wider text-cyan-400/70 uppercase">
              Assistente de IA · Sistema Neural
            </p>
          </div>
          <button
            type="button"
            onClick={onToggleOpen}
            title="Recolher menu lateral"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-cyan-200 transition cursor-pointer"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        </div>

        {/* Lista de Seções com Scroll Suave */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
          {/* GRUPO 1: VISÃO GERAL */}
          <div className="space-y-1">
            <p className="px-2 text-[10px] font-mono tracking-[0.2em] uppercase text-slate-400 font-semibold">
              Visão Geral
            </p>

            <button
              type="button"
              onClick={() => onSelectAction('chat')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10 hover:shadow-[0_0_15px_rgba(0,240,255,0.1)]'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <MessageSquare className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Chat
                </p>
                <p className="text-[10px] text-slate-400">Converse, pesquise e execute</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => onSelectAction('jarvis')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10 hover:shadow-[0_0_15px_rgba(0,240,255,0.1)]'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <Sparkles className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Jarvis
                </p>
                <p className="text-[10px] text-slate-400">Voz, HUD e monitor inteligente</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => onSelectAction('briefing')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10 hover:shadow-[0_0_15px_rgba(0,240,255,0.1)]'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <FileText className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Briefing
                </p>
                <p className="text-[10px] text-slate-400">Agenda, e-mails, notícias e clima</p>
              </div>
            </button>
          </div>

          {/* GRUPO 2: DADOS E AUTOMAÇÃO */}
          <div className="space-y-1">
            <p className="px-2 text-[10px] font-mono tracking-[0.2em] uppercase text-slate-400 font-semibold">
              Dados e Automação
            </p>

            {/* Fontes de Dados (com Dropdown) */}
            <div className="space-y-1">
              <button
                type="button"
                onClick={() => onSelectAction('data-sources')}
                className={cn(
                  'group flex w-full items-start justify-between rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                  activePanel === 'data-sources'
                    ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-200 shadow-[0_0_18px_rgba(0,240,255,0.2)]'
                    : 'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
                )}
              >
                <div className="flex items-start gap-3 min-w-0">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                    <Database className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                      Fontes de Dados
                    </p>
                    <p className="text-[10px] text-slate-400">Conexões, canais e MCP</p>
                  </div>
                </div>
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    setDataDropdownOpen((prev) => !prev);
                  }}
                  className="rounded p-1 text-slate-400 hover:text-slate-200 cursor-pointer"
                >
                  <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', dataDropdownOpen && 'rotate-180')} />
                </span>
              </button>

              {/* Subitens do Dropdown Fontes de Dados */}
              {dataDropdownOpen && (
                <div className="ml-7 pl-3 border-l border-cyan-500/20 space-y-1">
                  <button
                    type="button"
                    onClick={() => onSelectAction('data-sources')}
                    className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs font-mono text-cyan-300 hover:bg-cyan-500/15 transition cursor-pointer"
                  >
                    <span>Central de Conexões</span>
                    <span className="text-[9px] rounded bg-emerald-500/20 text-emerald-400 px-1 py-0.2">8 ativas</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onSelectAction('memory')}
                    className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs font-mono text-slate-300 hover:text-cyan-200 hover:bg-cyan-500/15 transition cursor-pointer"
                  >
                    <span>Memória</span>
                    <span className="text-[9px] text-slate-500">Persistente</span>
                  </button>
                </div>
              )}
            </div>

            {/* Monitoramento */}
            <button
              type="button"
              onClick={() => onSelectAction('monitoring')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                activePanel === 'system'
                  ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-200 shadow-[0_0_18px_rgba(0,240,255,0.2)]'
                  : 'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <Activity className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Monitoramento
                </p>
                <p className="text-[10px] text-slate-400">Saúde do sistema, hardware e rede</p>
              </div>
            </button>

            {/* Financeiro */}
            <button
              type="button"
              onClick={() => onSelectAction('finance')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                activePanel === 'trader'
                  ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-200 shadow-[0_0_18px_rgba(0,240,255,0.2)]'
                  : 'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <TrendingUp className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Financeiro
                </p>
                <p className="text-[10px] text-slate-400">Saldos, B3, cotações e quant</p>
              </div>
            </button>
          </div>

          {/* GRUPO 3: SISTEMA */}
          <div className="space-y-1">
            <p className="px-2 text-[10px] font-mono tracking-[0.2em] uppercase text-slate-400 font-semibold">
              Sistema
            </p>

            <button
              type="button"
              onClick={() => onSelectAction('logs')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                activePanel === 'activity'
                  ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-200 shadow-[0_0_18px_rgba(0,240,255,0.2)]'
                  : 'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <ScrollText className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Logs
                </p>
                <p className="text-[10px] text-slate-400">Eventos e rastreamento técnico</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => onSelectAction('settings')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <Settings className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Configurações
                </p>
                <p className="text-[10px] text-slate-400">Preferências, voz e segurança</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => onSelectAction('get-started')}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl border p-2.5 text-left transition-all cursor-pointer',
                'border-transparent hover:border-cyan-400/30 hover:bg-cyan-500/10'
              )}
            >
              <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-300">
                <HelpCircle className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">
                  Primeiros Passos
                </p>
                <p className="text-[10px] text-slate-400">Nome, chaves, voz e atalhos</p>
              </div>
            </button>
          </div>
        </div>

        {/* Rodapé com Indicador de Status Sistema Online (Estilo Lucas Victor) */}
        <div className="border-t border-cyan-500/20 bg-slate-900/60 p-3">
          <div className="flex items-center gap-2.5 rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-3 py-2">
            <span className="relative flex size-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex size-2.5 rounded-full bg-emerald-500" />
            </span>
            <div>
              <p className="text-[10px] font-mono font-bold uppercase tracking-wider text-emerald-400">
                SISTEMA ONLINE
              </p>
              <p className="text-[9px] text-slate-400">Todos os sistemas operacionais</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
