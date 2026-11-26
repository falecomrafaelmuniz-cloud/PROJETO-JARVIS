'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  Activity,
  Bell,
  Bot,
  ChevronDown,
  Clock,
  Code2,
  Film,
  FolderOpen,
  Hand,
  LayoutGrid,
  Mail,
  Maximize2,
  Minimize2,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Star,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import {
  DEFAULT_JARVIS_THEME_HEX,
  JARVIS_THEME_PRESETS,
} from '@/lib/jarvis-theme';
import { cn } from '@/lib/shadcn/utils';

export type HudPanelId =
  | 'system'
  | 'clock'
  | 'activity'
  | 'trader'
  | 'automations'
  | 'reminders'
  | 'favorites'
  | 'email'
  | 'files'
  | 'neural'
  | 'videos'
  | 'antigravity';

export type HudPanelsState = Record<HudPanelId, boolean>;

export const DEFAULT_HUD_PANELS: HudPanelsState = {
  system: true,
  clock: true,
  activity: true,
  trader: false,
  automations: false,
  reminders: false,
  favorites: false,
  email: false,
  files: false,
  neural: false,
  videos: false,
  antigravity: false,
};

interface HudMenuProps {
  panels: HudPanelsState;
  onTogglePanel: (id: HudPanelId) => void;
  onOpenNeuralConfig: () => void;
  onResetLayout: () => void;
  onOrganizeLayout?: () => void;
  orbStyle?: 'vanta' | 'quantum';
  onToggleOrbStyle?: () => void;
  themeHex?: string;
  onChangeThemeHex?: (hex: string) => void;
  docked?: boolean;
  orbMode?: boolean;
  onToggleOrbMode?: () => void;
  // retrocompatibilidade
  compactMode?: boolean;
  onToggleCompactMode?: () => void;
}

interface MenuItemDef {
  id: HudPanelId;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const MENU_ITEMS: MenuItemDef[] = [
  { id: 'system', label: 'Sistema', icon: Activity },
  { id: 'clock', label: 'Relógio', icon: Clock },
  { id: 'activity', label: 'Atividades', icon: Zap },
  { id: 'files', label: 'Central de Arquivos', icon: FolderOpen },
  { id: 'trader', label: 'Trader B3', icon: TrendingUp },
  { id: 'automations', label: 'Automações', icon: Bot },
  { id: 'reminders', label: 'Lembretes', icon: Bell },
  { id: 'favorites', label: 'Atalhos', icon: Star },
  { id: 'email', label: 'E-mail', icon: Mail },
  { id: 'neural', label: 'Rede Neural', icon: Sparkles },
  { id: 'antigravity', label: 'Antigravity Code', icon: Code2 },
  { id: 'videos', label: 'Vídeos', icon: Film },
];

export function HudMenu({
  panels,
  onTogglePanel,
  onOpenNeuralConfig,
  onResetLayout,
  onOrganizeLayout,
  orbStyle = 'vanta',
  onToggleOrbStyle,
  themeHex = DEFAULT_JARVIS_THEME_HEX,
  onChangeThemeHex,
  docked = false,
  orbMode = false,
  onToggleOrbMode,
  compactMode,
  onToggleCompactMode,
}: HudMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { style, isDragging, wasDragged, dragHandlers } =
    useDraggableFloating('jarvis-pos-hud-menu-v1');

  useEffect(() => {
    if (!open) return;
    const handlePointerDownOutside = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', handlePointerDownOutside);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDownOutside);
    };
  }, [open]);

  const activeCount = MENU_ITEMS.filter((item) => panels[item.id]).length;

  return (
    <div
      ref={containerRef}
      style={docked ? undefined : style}
      {...(docked ? { 'data-no-drag': 'true' } : dragHandlers)}
      className={cn(
        docked
          ? 'relative ml-1 select-none'
          : 'fixed top-3 left-1/2 z-[1000] -translate-x-1/2 select-none',
        !docked && (open || isDragging) ? 'z-[1100]' : '',
        docked && open ? 'z-[1100]' : ''
      )}
    >
      {docked ? (
        <button
          type="button"
          data-no-drag="true"
          onClick={() => setOpen((prev) => !prev)}
          title="Abrir menu de painéis do Jarvis"
          className={cn(
            'inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-xs font-medium tracking-wide transition',
            open
              ? 'border-cyan-400/60 bg-cyan-500/30 text-cyan-200'
              : 'border-cyan-400/25 bg-cyan-500/10 text-cyan-300/90 hover:border-cyan-400/45 hover:bg-cyan-500/20'
          )}
        >
          <LayoutGrid className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Painéis</span>
          <span className="rounded-full border border-cyan-300/30 bg-cyan-400/15 px-1.5 py-0.2 text-[10px] font-semibold text-cyan-200">
            {activeCount}/{MENU_ITEMS.length}
          </span>
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 transition-transform duration-200',
              open ? 'rotate-0' : 'rotate-180'
            )}
          />
        </button>
      ) : (
        <div className="flex items-center gap-1 rounded-full border border-cyan-300/35 bg-slate-950/75 p-1 text-xs text-cyan-100 shadow-[0_0_25px_rgba(34,211,238,0.22)] backdrop-blur-md">
          <span
            data-drag-handle="true"
            title="Segure e arraste para mover o menu"
            aria-label="Mover menu"
            className="inline-flex h-6 w-6 cursor-grab items-center justify-center rounded-full text-cyan-300/75 transition hover:bg-cyan-400/15 hover:text-cyan-100 active:cursor-grabbing"
          >
            <Hand className="h-3 w-3" />
          </span>

          <button
            type="button"
            data-no-drag="true"
            onClick={() => {
              if (!wasDragged()) setOpen((prev) => !prev);
            }}
            className="inline-flex items-center gap-2 rounded-full px-2.5 py-1 font-medium tracking-wide transition hover:bg-cyan-400/15"
          >
            <LayoutGrid className="h-3.5 w-3.5 text-cyan-300" />
            <span>Painéis</span>
            <span className="rounded-full border border-cyan-300/30 bg-cyan-400/15 px-1.5 py-0.2 text-[10px] font-semibold text-cyan-200">
              {activeCount}/{MENU_ITEMS.length}
            </span>
            <ChevronDown
              className={cn(
                'h-3.5 w-3.5 text-cyan-300/80 transition-transform duration-200',
                open ? 'rotate-180' : ''
              )}
            />
          </button>
        </div>
      )}

      {open && (
        <div
          data-no-drag="true"
          className={cn(
            'w-[350px] sm:w-[380px] rounded-xl border border-cyan-400/30 bg-slate-950/95 p-3.5 text-slate-100 shadow-[0_0_40px_rgba(34,211,238,0.25)] backdrop-blur-2xl select-none',
            docked
              ? 'absolute bottom-full left-1/2 z-[1100] mb-3 -translate-x-1/2'
              : 'mt-2 -translate-x-1/2'
          )}
          style={docked ? undefined : { marginLeft: '50%' }}
        >
          {/* Cabeçalho Limpo sem instruções redundantes */}
          <div className="mb-2.5 flex items-center justify-between border-b border-cyan-400/20 pb-2">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#00f0ff] animate-pulse" />
              <p className="text-[11px] font-mono font-bold tracking-[0.2em] text-cyan-200 uppercase">
                PAINÉIS DO JARVIS
              </p>
            </div>
            <span className="rounded-full border border-cyan-400/30 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-mono font-semibold text-cyan-300">
              {activeCount}/{MENU_ITEMS.length} ATIVOS
            </span>
          </div>

          {/* Barra de Ações Rápidas: 100% Padronizada, Centralizada e Simétrica */}
          <div className="grid grid-cols-4 gap-1.5 mb-3">
            {(onToggleOrbMode || onToggleCompactMode) && (
              <button
                type="button"
                onClick={onToggleOrbMode || onToggleCompactMode}
                title={orbMode || compactMode ? "Expandir para modo completo" : "Ativar Modo Orb (apenas o Orb flutuante 5x5 cm)"}
                className={cn(
                  "flex h-8 w-full items-center justify-center gap-1 rounded-lg border text-[10px] font-mono font-medium transition cursor-pointer px-1.5 text-center",
                  (orbMode || compactMode)
                    ? "border-amber-400/60 bg-amber-500/20 text-amber-200 shadow-[0_0_12px_rgba(245,158,11,0.3)]"
                    : "border-cyan-400/30 bg-cyan-500/10 text-cyan-200 hover:border-cyan-400/60 hover:bg-cyan-500/20"
                )}
              >
                <Sparkles className="h-3 w-3 shrink-0" />
                <span className="truncate">{(orbMode || compactMode) ? "Expandir" : "Modo Orb"}</span>
              </button>
            )}

            <button
              type="button"
              onClick={onResetLayout}
              title="Restaurar layout padrão de 3 janelas (Sistema, Relógio e Atividades)"
              className="flex h-8 w-full items-center justify-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-1.5 text-[10px] font-mono font-medium text-cyan-200 transition hover:border-cyan-400/60 hover:bg-cyan-500/20 cursor-pointer text-center"
            >
              <RotateCcw className="h-3 w-3 shrink-0" />
              <span className="truncate">Padrão</span>
            </button>

            {onOrganizeLayout && (
              <button
                type="button"
                onClick={onOrganizeLayout}
                title="Distribuir painéis abertos de forma organizada e sem sobreposição"
                className="flex h-8 w-full items-center justify-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-1.5 text-[10px] font-mono font-medium text-cyan-200 transition hover:border-cyan-400/60 hover:bg-cyan-500/20 cursor-pointer text-center"
              >
                <LayoutGrid className="h-3 w-3 shrink-0" />
                <span className="truncate">Organizar</span>
              </button>
            )}

            {onToggleOrbStyle && (
              <button
                type="button"
                onClick={onToggleOrbStyle}
                title="Alternar estilo do Orb: Quântico ou Clássico"
                className="flex h-8 w-full items-center justify-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-500/15 px-1.5 text-[10px] font-mono font-medium text-cyan-100 transition hover:border-cyan-400/60 hover:bg-cyan-500/25 cursor-pointer text-center"
              >
                <Sparkles className="h-3 w-3 shrink-0 text-cyan-300" />
                <span className="truncate">{orbStyle === 'quantum' ? 'Quântico' : 'Clássico'}</span>
              </button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-1.5">
            {MENU_ITEMS.map(({ id, label, icon: Icon }) => {
              const active = panels[id];
              return (
                <div key={id} className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => onTogglePanel(id)}
                    className={cn(
                      'flex flex-1 items-center justify-between rounded-lg border px-2.5 py-2 text-left text-[11px] font-medium transition',
                      active
                        ? 'border-cyan-300/55 bg-cyan-400/20 text-cyan-100 shadow-[0_0_14px_rgba(34,211,238,0.22)]'
                        : 'border-slate-700/70 bg-slate-900/70 text-slate-300 hover:border-cyan-300/35 hover:text-cyan-100'
                    )}
                  >
                    <span className="flex items-center gap-1.5 truncate">
                      <Icon
                        className={cn(
                          'h-3.5 w-3.5 shrink-0',
                          active ? 'text-cyan-300' : 'text-slate-400'
                        )}
                      />
                      <span className="truncate">{label}</span>
                    </span>
                    <span
                      className={cn(
                        'ml-1.5 h-2 w-2 shrink-0 rounded-full',
                        active
                          ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]'
                          : 'bg-slate-600'
                      )}
                    />
                  </button>
                  {id === 'neural' && (
                    <button
                      type="button"
                      onClick={() => {
                        if (!panels.neural) {
                          onTogglePanel('neural');
                        }
                        onOpenNeuralConfig();
                        setOpen(false);
                      }}
                      title="Configurar cores e densidade da Rede Neural"
                      aria-label="Configurar Rede Neural"
                      className="inline-flex h-full items-center justify-center rounded-lg border border-cyan-300/25 bg-slate-900/80 px-2 text-cyan-300/80 transition hover:border-cyan-300/55 hover:bg-cyan-400/15 hover:text-cyan-100"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {onChangeThemeHex && (
            <div className="mt-3 border-t border-cyan-300/15 pt-2.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] font-semibold tracking-[0.16em] text-cyan-200/85 uppercase">
                  Cor do Jarvis (Orb + Painéis)
                </span>
                <button
                  type="button"
                  onClick={() => onChangeThemeHex(DEFAULT_JARVIS_THEME_HEX)}
                  className="text-[10px] font-medium text-cyan-300/80 hover:text-cyan-100"
                >
                  Padrão
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {JARVIS_THEME_PRESETS.map((preset) => {
                  const isSelected = themeHex.toLowerCase() === preset.hex.toLowerCase();
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => onChangeThemeHex(preset.hex)}
                      title={preset.label}
                      aria-label={`Cor ${preset.label}`}
                      style={{ backgroundColor: preset.hex }}
                      className={cn(
                        'size-6 rounded-full border transition-transform hover:scale-110',
                        isSelected
                          ? 'scale-110 border-white ring-2 ring-white/60'
                          : 'border-white/25'
                      )}
                    />
                  );
                })}
                <label
                  title="Escolher qualquer cor personalizada"
                  className="inline-flex h-6 cursor-pointer items-center gap-1 rounded-full border border-cyan-300/35 bg-slate-900/90 px-2 text-[10px] font-semibold text-cyan-100 hover:border-cyan-300/70"
                >
                  <span>Outra</span>
                  <input
                    type="color"
                    value={themeHex}
                    onChange={(e) => onChangeThemeHex(e.target.value)}
                    aria-label="Escolher cor personalizada do Jarvis"
                    className="h-4 w-4 cursor-pointer border-0 bg-transparent p-0"
                  />
                </label>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
