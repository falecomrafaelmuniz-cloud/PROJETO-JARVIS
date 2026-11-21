'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  useRemoteParticipants,
  useRoomContext,
  useSessionContext,
  useSessionMessages,
  useTrackVolume,
  useVoiceAssistant,
} from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';
import type { AppConfig } from '@/app-config';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { ActivityPanel } from '@/components/app/activity-panel';
import { AntigravityPanel } from '@/components/app/antigravity-panel';
import { AutomationsPanel } from '@/components/app/automations-panel';
import { ClockWidget } from '@/components/app/clock-widget';
import { EmailComposer } from '@/components/app/email-composer';
import {
  DEFAULT_HUD_PANELS,
  HudMenu,
  type HudPanelId,
  type HudPanelsState,
} from '@/components/app/hud-menu';
import { LocationSharing } from '@/components/app/location-sharing';
import { RemindersPanel } from '@/components/app/reminders-panel';
import { FavoritesPanel } from '@/components/app/favorites-panel';
import { SystemDashboard } from '@/components/app/system-dashboard';
import { NeuralNetworkVisualizer } from '@/components/app/neural-network';
import { QuantPanel } from '@/components/app/quant-panel';
import { TileLayout } from '@/components/app/tile-layout';
import { WorkspaceFilesPanel } from '@/components/app/workspace-files-panel';
import { QuantumParticleOrb } from '@/components/app/quantum-particle-orb';
import { VideoStudioPanel } from '@/components/app/video-studio-panel';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import {
  calculateLayoutPositions,
  calculateSinglePanelTransform,
  HUD_PANEL_STORAGE_KEYS,
  type LayoutMode,
} from '@/lib/hud-layout-manager';
import { organizeHudPanelsDom, positionSinglePanelDom } from '@/lib/hud-dom-layout';
import {
  applyJarvisThemeHex,
  DEFAULT_JARVIS_THEME_HEX,
  hexToNumber,
  loadJarvisThemeHex,
  saveJarvisThemeHex,
} from '@/lib/jarvis-theme';
import { cn } from '@/lib/shadcn/utils';
import { Shimmer } from '../ai-elements/shimmer';

const MotionBottom = motion.create('div');

const MotionMessage = motion.create(Shimmer);

const BOTTOM_VIEW_MOTION_PROPS = {
  variants: {
    visible: {
      opacity: 1,
      translateY: '0%',
    },
    hidden: {
      opacity: 0,
      translateY: '100%',
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: {
    duration: 0.3,
    delay: 0.5,
    ease: 'easeOut' as const,
  },
};

const SHIMMER_MOTION_PROPS = {
  variants: {
    visible: {
      opacity: 1,
      transition: {
        ease: 'easeIn' as const,
        duration: 0.5,
        delay: 0.8,
      },
    },
    hidden: {
      opacity: 0,
      transition: {
        ease: 'easeIn' as const,
        duration: 0.5,
        delay: 0,
      },
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
};

interface FadeProps {
  top?: boolean;
  bottom?: boolean;
  className?: string;
}

export function Fade({ top = false, bottom = false, className }: FadeProps) {
  return (
    <div
      className={cn(
        'from-background pointer-events-none h-4 bg-linear-to-b to-transparent',
        top && 'bg-linear-to-b',
        bottom && 'bg-linear-to-t',
        className
      )}
    />
  );
}

interface SessionViewProps {
  appConfig: AppConfig;
  onManualDisconnect?: () => void;
}

interface P5Instance {
  pixelDensity?: (val: number) => void;
  frameRate?: (fps: number) => void;
  noLoop?: () => void;
  loop?: () => void;
}

interface VantaEffect {
  options: { chaos: number };
  p5?: P5Instance;
  setOptions(options: { chaos: number }): void;
  destroy(): void;
}

interface VantaTrunkOptions {
  el: HTMLDivElement;
  p5: unknown;
  mouseControls: boolean;
  touchControls: boolean;
  gyroControls: boolean;
  minHeight: number;
  minWidth: number;
  scale: number;
  scaleMobile: number;
  color: number;
  backgroundColor: number;
  spacing: number;
  chaos: number;
}

type VantaWindow = Window & {
  VANTA?: {
    TRUNK?: (options: VantaTrunkOptions) => VantaEffect;
  };
  p5?: unknown;
};

// --- Sub-componente para controle de performance da Orb ---
const VantaController = ({ vantaRef }: { vantaRef: React.RefObject<VantaEffect | null> }) => {
  const { audioTrack } = useVoiceAssistant();
  const volume = useTrackVolume(audioTrack);

  useEffect(() => {
    const effect = vantaRef.current;
    if (!effect) return;

    // Atualizar Chaos conforme Volume (Reatividade à voz)
    const baseChaos = 3.0;
    const voiceChaos = volume * 7.0;
    const finalChaos = baseChaos + voiceChaos;

    if (Math.abs(effect.options.chaos - finalChaos) > 0.05) {
      effect.options.chaos = finalChaos;
    }
  }, [volume, vantaRef]);

  return null;
};

// --- Componente Modular da Orb com seu próprio ciclo de vida ---
const VantaOrb = ({
  color,
  vantaRef,
}: {
  color: number;
  vantaRef: React.RefObject<VantaEffect | null>;
}) => {
  const localRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let vantaEffect: VantaEffect | null = null;
    let attempts = 0;
    let initTimer: ReturnType<typeof setTimeout> | undefined;

    const handleVisibilityChange = () => {
      if (!vantaEffect?.p5) return;
      try {
        if (document.hidden) {
          vantaEffect.p5.noLoop?.();
        } else {
          vantaEffect.p5.loop?.();
        }
      } catch {
        // ignora se o p5 ainda estiver inicializando
      }
    };

    const tryInitVanta = () => {
      const el = localRef.current;
      const win = window as VantaWindow;
      const trunk = win.VANTA?.TRUNK;
      const p5 = win.p5;

      if (el && trunk && p5) {
        try {
          // A cor agora vem via prop
          vantaEffect = trunk({
            el: el,
            p5,
            mouseControls: false,
            touchControls: false,
            gyroControls: false,
            minHeight: 200.0,
            minWidth: 200.0,
            scale: 1.0,
            scaleMobile: 1.0,
            color: color,
            backgroundColor: 0x000000,
            spacing: 0.0,
            chaos: 3.0,
          });
          if (vantaEffect?.p5) {
            try {
              vantaEffect.p5.pixelDensity?.(1);
              vantaEffect.p5.frameRate?.(36);
            } catch {
              // fallback silencioso
            }
          }
          vantaRef.current = vantaEffect;
        } catch (error) {
          console.error('Vanta Orb Init Error:', error);
          attempts++;
          if (attempts < 10) initTimer = setTimeout(tryInitVanta, 500);
        }
      } else {
        attempts++;
        if (attempts < 50) initTimer = setTimeout(tryInitVanta, 100);
      }
    };

    tryInitVanta();
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange);
    }

    return () => {
      clearTimeout(initTimer);
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
      }
      if (vantaEffect) {
        try {
          if (vantaRef.current === vantaEffect) {
            vantaRef.current = null;
          }
          vantaEffect.destroy();
        } catch (error) {
          console.error('Vanta Orb Cleanup Error:', error);
        }
      }
    };
  }, [color, vantaRef]);

  return (
    <div
      ref={localRef}
      className="h-[1000px] w-[1000px]"
      style={{
        transform: 'scale(0.5) translateY(-15%)',
        transformOrigin: 'center center',
      }}
    />
  );
};

export const SessionView = ({
  appConfig,
  onManualDisconnect,
  ...props
}: React.ComponentProps<'section'> & SessionViewProps) => {
  const room = useRoomContext();
  const session = useSessionContext();
  const { state: agentState } = useVoiceAssistant();
  const { messages } = useSessionMessages(session);
  const [chatOpen, setChatOpen] = useState(false);
  const [hudPanels, setHudPanels] = useState<HudPanelsState>(DEFAULT_HUD_PANELS);
  const hudPanelsRef = useRef<HudPanelsState>(DEFAULT_HUD_PANELS);
  useEffect(() => {
    hudPanelsRef.current = hudPanels;
  }, [hudPanels]);
  const [neuralConfigSignal, setNeuralConfigSignal] = useState(0);
  const [orbStyle, setOrbStyle] = useState<'vanta' | 'quantum'>('quantum');
  const [themeHex, setThemeHex] = useState<string>(DEFAULT_JARVIS_THEME_HEX);

  useEffect(() => {
    const savedTheme = loadJarvisThemeHex();
    setThemeHex(savedTheme);
    applyJarvisThemeHex(savedTheme);
  }, []);

  const handleChangeThemeHex = useCallback((hex: string) => {
    const saved = saveJarvisThemeHex(hex);
    setThemeHex(saved);
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('jarvis-orb-style-v1');
      if (saved === 'quantum' || saved === 'vanta') {
        setOrbStyle(saved);
      }
    } catch {}
  }, []);

  const toggleOrbStyle = useCallback(() => {
    setOrbStyle((prev) => {
      const next = prev === 'vanta' ? 'quantum' : 'vanta';
      try {
        localStorage.setItem('jarvis-orb-style-v1', next);
      } catch {}
      return next;
    });
  }, []);

  // Escuta comandos de voz para abrir/fechar/alternar painéis do HUD em tempo real
  useEffect(() => {
    if (!room) return;
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== 'jarvis.hud' && topic !== 'jarvis.workspace') return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));
        if (packet?.type === 'jarvis_switch_notebook') {
          setPanelOpen('files', true);
          return;
        }
        if (packet?.type === 'jarvis_hud_control') {
          const target = String(packet.panel || '').toLowerCase().trim();
          const action = packet.action || 'open';

          const panelMap: Record<string, HudPanelId> = {
            files: 'files',
            arquivos: 'files',
            arquivo: 'files',
            central: 'files',
            central_arquivos: 'files',
            workspace: 'files',
            notebook: 'files',
            notebooks: 'files',
            caderno: 'files',
            cadernos: 'files',
            reminders: 'reminders',
            lembretes: 'reminders',
            lembrete: 'reminders',
            favorites: 'favorites',
            favoritos: 'favorites',
            favorito: 'favorites',
            atalhos: 'favorites',
            atalho: 'favorites',
            bookmarks: 'favorites',
            automations: 'automations',
            automacoes: 'automations',
            automacao: 'automations',
            tarefas_agendadas: 'automations',
            email: 'email',
            emails: 'email',
            rascunho: 'email',
            system: 'system',
            sistema: 'system',
            dashboard: 'system',
            metricas: 'system',
            activity: 'activity',
            atividades: 'activity',
            atividade: 'activity',
            acoes: 'activity',
            tarefas: 'activity',
            trader: 'trader',
            quant: 'trader',
            mercado: 'trader',
            clock: 'clock',
            relogio: 'clock',
            neural: 'neural',
            rede_neural: 'neural',
            antigravity: 'antigravity',
            codigo: 'antigravity',
            desenvolvimento: 'antigravity',
            dev: 'antigravity',
            programacao: 'antigravity',
          };

          if (target === 'all' || target === 'todos' || target === 'tudo') {
            const nextVal = action === 'open' ? true : action === 'close' ? false : undefined;
            setHudPanels((prev) => {
              const next = { ...prev };
              (Object.keys(next) as HudPanelId[]).forEach((k) => {
                next[k] = nextVal !== undefined ? nextVal : !prev[k];
              });
              try {
                localStorage.setItem('jarvis-hud-panels-v1', JSON.stringify(next));
              } catch {}
              return next;
            });
            return;
          }

          const panelId = panelMap[target];
          if (panelId) {
            setHudPanels((prev) => {
              const current = prev[panelId];
              const nextVal = action === 'open' ? true : action === 'close' ? false : !current;
              if (current === nextVal) return prev;
              const next = { ...prev, [panelId]: nextVal };
              try {
                localStorage.setItem('jarvis-hud-panels-v1', JSON.stringify(next));
              } catch {}
              return next;
            });
          }
        }

        // Organização e Posicionamento Inteligente dos Layouts do HUD (Anti-sobreposição)
        if (
          packet?.type === 'jarvis_hud_organize' ||
          packet?.type === 'jarvis_hud_layout' ||
          packet?.type === 'jarvis_hud_reset_layout'
        ) {
          if (packet.type === 'jarvis_hud_reset_layout' || packet.action === 'reset' || packet.mode === 'reset') {
            setHudPanels(DEFAULT_HUD_PANELS);
            persistHudPanels(DEFAULT_HUD_PANELS);
            Object.values(HUD_PANEL_STORAGE_KEYS).forEach((key) => {
              try {
                localStorage.removeItem(key);
              } catch {}
            });
            window.dispatchEvent(new CustomEvent('jarvis:hud-reset-layout'));
            return;
          }

          const targetPanelRaw = String(packet.panel || '').toLowerCase().trim();
          const panelMap: Record<string, HudPanelId> = {
            files: 'files', arquivos: 'files', central: 'files', workspace: 'files', caderno: 'files',
            reminders: 'reminders', lembretes: 'reminders', lembrete: 'reminders',
            favorites: 'favorites', favoritos: 'favorites', favorito: 'favorites', atalhos: 'favorites', atalho: 'favorites', bookmarks: 'favorites',
            system: 'system', sistema: 'system', dashboard: 'system',
            trader: 'trader', quant: 'trader', mercado: 'trader',
            automations: 'automations', automacoes: 'automations',
            activity: 'activity', atividades: 'activity',
            email: 'email', emails: 'email',
            clock: 'clock', relogio: 'clock',
          };

          const targetPanelId = panelMap[targetPanelRaw];

          if (targetPanelId && packet.position) {
            // Posicionamento de um painel específico
            const transform = calculateSinglePanelTransform(
              targetPanelId,
              String(packet.position || 'center'),
              String(packet.size || 'normal'),
              typeof window !== 'undefined' ? window.innerWidth : 1920,
              typeof window !== 'undefined' ? window.innerHeight : 1080
            );
            const storageKey = HUD_PANEL_STORAGE_KEYS[targetPanelId];
            setTimeout(() => {
              // Usa as medidas reais do painel na tela; cai no cálculo fixo se não achar o elemento
              const measured = positionSinglePanelDom(
                targetPanelId,
                String(packet.position || 'center'),
                String(packet.size || 'normal')
              );
              window.dispatchEvent(
                new CustomEvent('jarvis:hud-set-transforms', {
                  detail: { positions: measured ?? { [storageKey]: transform } },
                })
              );
            }, 50);
            return;
          }

          // Organização coletiva de painéis abertos (evita que fiquem um em cima do outro)
          const mode = (packet.mode as LayoutMode) || 'side_by_side';
          setTimeout(() => {
            const activePanels = (Object.keys(hudPanelsRef.current) as HudPanelId[]).filter(
              (k) => hudPanelsRef.current[k]
            );
            const positions =
              organizeHudPanelsDom(activePanels) ??
              calculateLayoutPositions(activePanels, mode, window.innerWidth, window.innerHeight);
            window.dispatchEvent(
              new CustomEvent('jarvis:hud-set-transforms', {
                detail: { positions },
              })
            );
          }, 0);
        }
      } catch (err) {
        console.warn('Erro ao processar mensagem jarvis.hud:', err);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('jarvis-hud-panels-v1');
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<HudPanelsState>;
        setHudPanels((prev) => ({
          ...prev,
          ...(typeof parsed.system === 'boolean' ? { system: parsed.system } : {}),
          ...(typeof parsed.clock === 'boolean' ? { clock: parsed.clock } : {}),
          ...(typeof parsed.activity === 'boolean' ? { activity: parsed.activity } : {}),
          ...(typeof parsed.trader === 'boolean' ? { trader: parsed.trader } : {}),
          ...(typeof parsed.automations === 'boolean' ? { automations: parsed.automations } : {}),
          ...(typeof parsed.reminders === 'boolean' ? { reminders: parsed.reminders } : {}),
          ...(typeof parsed.favorites === 'boolean' ? { favorites: parsed.favorites } : {}),
          ...(typeof parsed.email === 'boolean' ? { email: parsed.email } : {}),
          ...(typeof parsed.files === 'boolean' ? { files: parsed.files } : {}),
          ...(typeof parsed.neural === 'boolean' ? { neural: parsed.neural } : {}),
          ...(typeof parsed.videos === 'boolean' ? { videos: parsed.videos } : {}),
        }));
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const persistHudPanels = useCallback((next: HudPanelsState) => {
    try {
      localStorage.setItem('jarvis-hud-panels-v1', JSON.stringify(next));
    } catch {
      // ignore storage errors
    }
  }, []);

  const setPanelOpen = useCallback(
    (id: HudPanelId, open: boolean) => {
      setHudPanels((prev) => {
        if (prev[id] === open) return prev;
        const next = { ...prev, [id]: open };
        persistHudPanels(next);
        return next;
      });

      // Se for a central de arquivos, sincroniza imediatamente com o Jarvis backend
      if (id === 'files' && room?.localParticipant) {
        try {
          const payload = JSON.stringify({
            type: 'jarvis_active_notebook_sync',
            isOpen: open,
          });
          room.localParticipant.publishData(new TextEncoder().encode(payload), {
            reliable: true,
            topic: 'jarvis.workspace',
          });
        } catch {}
      }
    },
    [persistHudPanels, room]
  );

  const toggleHudPanel = useCallback(
    (id: HudPanelId) => {
      setHudPanels((prev) => {
        const next = { ...prev, [id]: !prev[id] };
        persistHudPanels(next);
        return next;
      });
    },
    [persistHudPanels]
  );

  const handleCloseSystem = useCallback(() => setPanelOpen('system', false), [setPanelOpen]);
  const handleCloseClock = useCallback(() => setPanelOpen('clock', false), [setPanelOpen]);
  const handleCloseActivity = useCallback(() => setPanelOpen('activity', false), [setPanelOpen]);
  const handleCloseTrader = useCallback(() => setPanelOpen('trader', false), [setPanelOpen]);
  const handleCloseAutomations = useCallback(() => setPanelOpen('automations', false), [setPanelOpen]);
  const handleOpenAutomations = useCallback(() => setPanelOpen('automations', true), [setPanelOpen]);
  const handleCloseReminders = useCallback(() => setPanelOpen('reminders', false), [setPanelOpen]);
  const handleOpenReminders = useCallback(() => setPanelOpen('reminders', true), [setPanelOpen]);
  const handleCloseFavorites = useCallback(() => setPanelOpen('favorites', false), [setPanelOpen]);
  const handleOpenFavorites = useCallback(() => setPanelOpen('favorites', true), [setPanelOpen]);
  const handleCloseEmail = useCallback(() => setPanelOpen('email', false), [setPanelOpen]);
  const handleOpenEmail = useCallback(() => setPanelOpen('email', true), [setPanelOpen]);
  const handleCloseFiles = useCallback(() => setPanelOpen('files', false), [setPanelOpen]);
  const handleCloseVideos = useCallback(() => setPanelOpen('videos', false), [setPanelOpen]);
  const handleCloseAntigravity = useCallback(() => setPanelOpen('antigravity', false), [setPanelOpen]);
  const handleOpenAntigravity = useCallback(() => setPanelOpen('antigravity', true), [setPanelOpen]);

  const handleOpenNeuralConfig = useCallback(() => {
    setNeuralConfigSignal((prev) => prev + 1);
  }, []);

  const handleResetHudLayout = useCallback(() => {
    setHudPanels(DEFAULT_HUD_PANELS);
    persistHudPanels(DEFAULT_HUD_PANELS);
    Object.values(HUD_PANEL_STORAGE_KEYS).forEach((key) => {
      try {
        localStorage.removeItem(key);
      } catch {}
    });
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('jarvis:hud-reset-layout'));
    }, 0);
  }, [persistHudPanels]);

  const handleOrganizeHudLayout = useCallback(() => {
    const activePanels = (Object.keys(hudPanels) as HudPanelId[]).filter(
      (k) => hudPanels[k]
    );
    if (activePanels.length <= 1) {
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('jarvis:hud-reset-layout'));
      }, 0);
    } else {
      setTimeout(() => {
        const positions =
          organizeHudPanelsDom(activePanels) ??
          calculateLayoutPositions(activePanels, 'auto', window.innerWidth, window.innerHeight);
        window.dispatchEvent(
          new CustomEvent('jarvis:hud-set-transforms', {
            detail: { positions },
          })
        );
      }, 0);
    }
  }, [hudPanels]);

  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const vantaEffectRef = useRef<VantaEffect | null>(null);
  const {
    style: controlBarDragStyle,
    isDragging: isControlBarDragging,
    isResizingEdge: isControlBarResizingEdge,
    wasDragged: wasControlBarDragged,
    dragHandlers: controlBarDragHandlers,
  } = useDraggableFloating('jarvis-pos-control-bar-v1');
  const {
    style: orbDragStyle,
    isDragging: isOrbDragging,
    isResizingEdge: isOrbResizingEdge,
    wasDragged: wasOrbDragged,
    dragHandlers: orbDragHandlers,
  } = useDraggableFloating('jarvis-pos-vanta-orb-v1', { circular: true });

  // Monitora participantes para detectar Persona (Alice/Járvis)
  const participants = useRemoteParticipants();
  const agentParticipant = participants.find((p) => !p.isLocal);
  const agentPersona = agentParticipant?.attributes?.['agent_persona'] || 'jarvis';

  // Definição de Cores
  const currentColor =
    agentPersona === 'alice' ? 0xff69b4 : hexToNumber(themeHex);

  useEffect(() => {
    const loadScript = (src: string): Promise<boolean> => {
      return new Promise((resolve) => {
        if (typeof document === 'undefined') return resolve(false);
        if (document.querySelector(`script[src="${src}"]`)) return resolve(true);
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.onload = () => resolve(true);
        script.onerror = () => resolve(false);
        document.body.appendChild(script);
      });
    };

    const setup = async () => {
      await loadScript('https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.4.0/p5.min.js');
      await loadScript('https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.trunk.min.js');
    };
    setup();
  }, []);

  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    chat: appConfig.supportsChatInput,
    camera: appConfig.supportsVideoInput,
    screenShare: appConfig.supportsScreenShare,
  };

  useEffect(() => {
    const lastMessage = messages.at(-1);
    const lastMessageIsLocal = lastMessage?.from?.isLocal === true;
    if (scrollAreaRef.current && lastMessageIsLocal) {
      scrollAreaRef.current.scrollTop = scrollAreaRef.current.scrollHeight;
    }
  }, [messages]);

  const handleDisconnect = () => {
    persistHudPanels(hudPanelsRef.current);
    if (onManualDisconnect) onManualDisconnect();
    try {
      if (session.end) session.end();
    } catch (e) {
      console.warn('Erro ao desconectar sessão:', e);
    }
  };

  const handleBackgroundClick = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      if (wasOrbDragged() || wasControlBarDragged()) return;
      const target = event.target as HTMLElement | null;
      if (
        target?.closest(
          '[data-hud-panel], aside, [data-control-bar="true"], [data-no-bg-toggle="true"], button, a, input, textarea, select, [role="dialog"]'
        )
      ) {
        return;
      }
      window.dispatchEvent(new CustomEvent('jarvis:toggle-control-bar'));
    },
    [wasControlBarDragged, wasOrbDragged]
  );

  return (
    <section
      onClick={handleBackgroundClick}
      className="relative flex h-svh w-svw flex-col overflow-hidden bg-black"
      {...props}
    >
      <VantaController vantaRef={vantaEffectRef} />
      <ActivityPanel isOpen={hudPanels.activity} onClose={handleCloseActivity} />
      <SystemDashboard isOpen={hudPanels.system} onClose={handleCloseSystem} />
      <ClockWidget isOpen={hudPanels.clock} onClose={handleCloseClock} />
      <LocationSharing />
      <NeuralNetworkVisualizer
        isOpen={hudPanels.neural}
        configOpenSignal={neuralConfigSignal}
      />
      <EmailComposer
        isOpen={hudPanels.email}
        onClose={handleCloseEmail}
        onOpenRequest={handleOpenEmail}
      />
      {hudPanels.trader && (
        <QuantPanel isOpen={hudPanels.trader} onClose={handleCloseTrader} />
      )}
      {hudPanels.videos && (
        <VideoStudioPanel isOpen={hudPanels.videos} onClose={handleCloseVideos} />
      )}
      <AutomationsPanel
        isOpen={hudPanels.automations}
        onClose={handleCloseAutomations}
        onOpenRequest={handleOpenAutomations}
      />
      <RemindersPanel
        isOpen={hudPanels.reminders}
        onClose={handleCloseReminders}
        onOpenRequest={handleOpenReminders}
      />
      <FavoritesPanel
        isOpen={hudPanels.favorites}
        onClose={handleCloseFavorites}
        onOpenRequest={handleOpenFavorites}
      />
      <WorkspaceFilesPanel
        isOpen={hudPanels.files}
        onClose={handleCloseFiles}
        onOpenEmailComposer={handleOpenEmail}
      />
      <AntigravityPanel
        isOpen={hudPanels.antigravity}
        onClose={handleCloseAntigravity}
        onOpenRequest={handleOpenAntigravity}
      />

      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <div className="absolute inset-0 flex items-center justify-center overflow-hidden">
          <AnimatePresence mode="wait">
            <motion.div
              key={session.isConnected ? `vanta-${agentPersona}` : 'vanta-disconnected'}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.5, ease: 'easeInOut' }}
              className="p5-canvas-container absolute inset-0 flex items-center justify-center"
            >
              <div
                style={orbDragStyle}
                {...orbDragHandlers}
                onDoubleClick={toggleOrbStyle}
                className={cn(
                  'pointer-events-auto relative flex size-[360px] touch-none items-center justify-center rounded-full transition-shadow select-none',
                  isOrbResizingEdge ? 'ring-1 ring-cyan-300/60' : '',
                  isOrbDragging ? 'cursor-grabbing' : 'cursor-grab'
                )}
              >
                <div className="pointer-events-none flex flex-col items-center justify-center">
                  {orbStyle === 'quantum' ? (
                    <QuantumParticleOrb key={`q-${currentColor}`} color={currentColor} />
                  ) : (
                    <VantaOrb key={`v-${currentColor}`} color={currentColor} vantaRef={vantaEffectRef} />
                  )}
                  {/* Status Cyberpunk OSONE */}
                  <div className="mt-1 flex flex-col items-center justify-center text-center pointer-events-none select-none">
                    <div className="flex items-center gap-2 text-[10px] font-mono tracking-[0.25em] uppercase text-cyan-400 font-semibold drop-shadow-[0_0_8px_rgba(255,85,0,0.6)]">
                      <span className="inline-block size-1.5 rounded-xs bg-cyan-500 animate-pulse" />
                      {agentState === 'thinking' ? 'PROCESSANDO REDE NEURAL' : session.isConnected ? 'NEURAL LINK ACTIVE' : 'SISTEMA EM ESPERA'}
                    </div>
                    <p className="mt-0.5 font-serif italic text-xs tracking-wider text-cyan-200/75 drop-shadow-[0_0_6px_rgba(255,85,0,0.3)]">
                      {agentState === 'thinking'
                        ? '"Sintetizando pensamentos..."'
                        : agentState === 'speaking'
                          ? '"Transmitindo resposta..."'
                          : session.isConnected
                            ? '"Ouvindo seus pensamentos..."'
                            : '"Aguardando conexão..."'}
                    </p>
                  </div>
                </div>
              </div>
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="relative z-10">
          <TileLayout chatOpen={chatOpen} />
        </div>
      </div>

      <div className="pointer-events-none flex-1" />

      <MotionBottom
        {...BOTTOM_VIEW_MOTION_PROPS}
        className="relative z-10 mx-auto mb-4 w-full max-w-3xl px-3 pb-3 md:pb-12"
      >
        {appConfig.isPreConnectBufferEnabled && (
          <AnimatePresence>
            {messages.length === 0 && (
              <MotionMessage
                key="pre-connect-message"
                duration={2}
                aria-hidden={messages.length > 0}
                {...SHIMMER_MOTION_PROPS}
                className="pointer-events-none mx-auto block w-full max-w-2xl pb-8 text-center text-sm font-semibold"
              >
                O Jarvis está ouvindo, pode falar...
              </MotionMessage>
            )}
          </AnimatePresence>
        )}

        <div
          style={controlBarDragStyle}
          {...controlBarDragHandlers}
          onClickCapture={(event) => {
            if (wasControlBarDragged()) {
              event.stopPropagation();
              event.preventDefault();
            }
          }}
          title="Arraste pelo centro para mover · Arraste pela borda para aumentar ou diminuir"
          className={cn(
            'relative mx-auto w-full max-w-2xl touch-none rounded-[31px] bg-transparent transition-shadow select-none',
            isControlBarDragging || isControlBarResizingEdge ? 'ring-1 ring-cyan-300/60' : '',
            isControlBarDragging ? 'cursor-grabbing' : 'cursor-grab'
          )}
        >
          <AgentControlBar
            variant="livekit"
            controls={controls}
            isChatOpen={chatOpen}
            messages={messages}
            isConnected={true}
            onDisconnect={handleDisconnect}
            onIsChatOpenChange={setChatOpen}
            extraControls={
              <HudMenu
                docked
                panels={hudPanels}
                onTogglePanel={toggleHudPanel}
                onOpenNeuralConfig={handleOpenNeuralConfig}
                onResetLayout={handleResetHudLayout}
                onOrganizeLayout={handleOrganizeHudLayout}
                orbStyle={orbStyle}
                onToggleOrbStyle={toggleOrbStyle}
                themeHex={themeHex}
                onChangeThemeHex={handleChangeThemeHex}
              />
            }
          />
        </div>
      </MotionBottom>
    </section>
  );
};
