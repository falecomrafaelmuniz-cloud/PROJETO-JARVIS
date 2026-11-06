'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Activity, Brain, Eye, EyeOff, Settings, Sparkles, X } from 'lucide-react';
import { useVoiceAssistant } from '@livekit/components-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  baseRadius: number;
  pulsePhase: number;
  activity: number;
}

interface Pulse {
  sourceIdx: number;
  targetIdx: number;
  progress: number;
  speed: number;
  color: string;
}

const THEMES = {
  cyan: {
    name: 'Ciano Jarvis',
    primary: '#22d3ee',
    secondary: '#06b6d4',
    glow: 'rgba(34, 211, 238, 0.4)',
    line: 'rgba(34, 211, 238, 0.15)',
    activeLine: 'rgba(34, 211, 238, 0.45)',
  },
  purple: {
    name: 'Roxo Matrix',
    primary: '#c084fc',
    secondary: '#a855f7',
    glow: 'rgba(192, 132, 252, 0.4)',
    line: 'rgba(192, 132, 252, 0.15)',
    activeLine: 'rgba(192, 132, 252, 0.45)',
  },
  emerald: {
    name: 'Verde Neural',
    primary: '#34d399',
    secondary: '#10b981',
    glow: 'rgba(52, 211, 153, 0.4)',
    line: 'rgba(52, 211, 153, 0.15)',
    activeLine: 'rgba(52, 211, 153, 0.45)',
  },
  amber: {
    name: 'Ouro Sináptico',
    primary: '#fbbf24',
    secondary: '#f59e0b',
    glow: 'rgba(251, 191, 36, 0.4)',
    line: 'rgba(251, 191, 36, 0.15)',
    activeLine: 'rgba(251, 191, 36, 0.45)',
  },
};

type ThemeKey = keyof typeof THEMES;

export function NeuralNetworkVisualizer() {
  const { state: agentState } = useVoiceAssistant();
  const [isEnabled, setIsEnabled] = useState(false);
  const [theme, setTheme] = useState<ThemeKey>('cyan');
  const [density, setDensity] = useState<'low' | 'medium' | 'high'>('medium');
  const [isConfigOpen, setIsConfigOpen] = useState(false);

  const {
    style: buttonDragStyle,
    isDragging: isButtonDragging,
    isResizingEdge: isButtonResizingEdge,
    wasDragged: wasButtonDragged,
    dragHandlers: buttonDragHandlers,
  } = useDraggableFloating('jarvis-pos-neural-net-button-v1');

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef<{ x: number; y: number; active: boolean }>({ x: -1000, y: -1000, active: false });

  // Carrega preferências salvas
  useEffect(() => {
    try {
      const savedEnabled = localStorage.getItem('jarvis-neural-net-enabled');
      if (savedEnabled !== null) setIsEnabled(savedEnabled === 'true');
      const savedTheme = localStorage.getItem('jarvis-neural-net-theme') as ThemeKey;
      if (savedTheme && THEMES[savedTheme]) setTheme(savedTheme);
      const savedDensity = localStorage.getItem('jarvis-neural-net-density') as 'low' | 'medium' | 'high';
      if (savedDensity) setDensity(savedDensity);
    } catch {
      // Ignora erro de storage
    }
  }, []);

  const toggleEnabled = () => {
    setIsEnabled((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('jarvis-neural-net-enabled', String(next));
      } catch {}
      return next;
    });
  };

  const changeTheme = (newTheme: ThemeKey) => {
    setTheme(newTheme);
    try {
      localStorage.setItem('jarvis-neural-net-theme', newTheme);
    } catch {}
  };

  const changeDensity = (newDensity: 'low' | 'medium' | 'high') => {
    setDensity(newDensity);
    try {
      localStorage.setItem('jarvis-neural-net-density', newDensity);
    } catch {}
  };

  // Motor da Rede Neural no Canvas
  useEffect(() => {
    if (!isEnabled) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let animId: number;
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
      initNodes();
    };

    window.addEventListener('resize', handleResize);

    const handleMouseMove = (e: MouseEvent) => {
      mouseRef.current.x = e.clientX;
      mouseRef.current.y = e.clientY;
      mouseRef.current.active = true;
    };

    const handleMouseLeave = () => {
      mouseRef.current.active = false;
      mouseRef.current.x = -1000;
      mouseRef.current.y = -1000;
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseleave', handleMouseLeave);

    const nodeCount = density === 'low' ? 32 : density === 'high' ? 75 : 50;
    const maxDistance = density === 'low' ? 140 : density === 'high' ? 115 : 125;
    let nodes: Node[] = [];
    let pulses: Pulse[] = [];

    const initNodes = () => {
      nodes = [];
      for (let i = 0; i < nodeCount; i++) {
        const baseRadius = Math.random() * 2.2 + 1.8;
        nodes.push({
          x: Math.random() * width,
          y: Math.random() * height,
          vx: (Math.random() - 0.5) * 0.45,
          vy: (Math.random() - 0.5) * 0.45,
          radius: baseRadius,
          baseRadius,
          pulsePhase: Math.random() * Math.PI * 2,
          activity: 0.2,
        });
      }
    };

    initNodes();

    let lastPulseTime = 0;

    const render = (time: number) => {
      ctx.clearRect(0, 0, width, height);

      const activeTheme = THEMES[theme] || THEMES.cyan;
      const isSpeaking = agentState === 'speaking';
      const isThinking = agentState === 'thinking';
      const isListening = agentState === 'listening';

      // Ajusta velocidade e atividade global conforme o estado do Jarvis
      const activityMultiplier = isSpeaking ? 2.5 : isThinking ? 2.0 : isListening ? 1.4 : 1.0;
      const speedMultiplier = isSpeaking ? 1.6 : isThinking ? 1.4 : 1.0;

      // 1. Atualiza posições dos nós neurais
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        n.x += n.vx * speedMultiplier;
        n.y += n.vy * speedMultiplier;

        // Rebate nas bordas suaves
        if (n.x < 0 || n.x > width) n.vx *= -1;
        if (n.y < 0 || n.y > height) n.vy *= -1;

        // Pulso oscilante natural
        n.pulsePhase += 0.03 * activityMultiplier;
        n.radius = n.baseRadius + Math.sin(n.pulsePhase) * (isSpeaking ? 1.5 : 0.6);

        // Interação com o mouse
        if (mouseRef.current.active) {
          const dx = mouseRef.current.x - n.x;
          const dy = mouseRef.current.y - n.y;
          const dist = Math.hypot(dx, dy);
          if (dist < 130) {
            n.activity = Math.min(1.0, n.activity + 0.08);
            // Leve atração ao mouse
            n.x += (dx / dist) * 0.35;
            n.y += (dy / dist) * 0.35;
          } else {
            n.activity = Math.max(0.15, n.activity - 0.02);
          }
        } else {
          n.activity = isSpeaking ? 0.8 : isThinking ? 0.6 : isListening ? 0.4 : 0.18;
        }
      }

      // 2. Cria novas sinapses de dados (pulsos correndo pelas conexões)
      if (time - lastPulseTime > (isSpeaking ? 120 : isThinking ? 180 : 380)) {
        lastPulseTime = time;
        if (nodes.length > 2) {
          const sIdx = Math.floor(Math.random() * nodes.length);
          // Procura vizinho próximo
          for (let j = 0; j < nodes.length; j++) {
            if (sIdx === j) continue;
            const dist = Math.hypot(nodes[sIdx].x - nodes[j].x, nodes[sIdx].y - nodes[j].y);
            if (dist < maxDistance) {
              pulses.push({
                sourceIdx: sIdx,
                targetIdx: j,
                progress: 0,
                speed: (Math.random() * 0.025 + 0.015) * activityMultiplier,
                color: activeTheme.primary,
              });
              break;
            }
          }
        }
      }

      // 3. Desenha conexões sinápticas (linhas)
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const dist = Math.hypot(dx, dy);

          if (dist < maxDistance) {
            const alpha = (1 - dist / maxDistance) * (isSpeaking ? 0.35 : 0.18);
            ctx.strokeStyle = activeTheme.line.replace('0.15', alpha.toFixed(3));
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.stroke();
          }
        }

        // Conecta ao mouse se estiver próximo
        if (mouseRef.current.active) {
          const mdx = mouseRef.current.x - nodes[i].x;
          const mdy = mouseRef.current.y - nodes[i].y;
          const mdist = Math.hypot(mdx, mdy);
          if (mdist < 140) {
            const malpha = (1 - mdist / 140) * 0.45;
            ctx.strokeStyle = activeTheme.activeLine.replace('0.45', malpha.toFixed(3));
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(mouseRef.current.x, mouseRef.current.y);
            ctx.stroke();
          }
        }
      }

      // 4. Desenha e atualiza os pulsos de dados (fótons sinápticos)
      for (let p = pulses.length - 1; p >= 0; p--) {
        const pulse = pulses[p];
        pulse.progress += pulse.speed;

        if (pulse.progress >= 1) {
          pulses.splice(p, 1);
          continue;
        }

        const s = nodes[pulse.sourceIdx];
        const t = nodes[pulse.targetIdx];
        if (!s || !t) continue;

        const px = s.x + (t.x - s.x) * pulse.progress;
        const py = s.y + (t.y - s.y) * pulse.progress;

        ctx.fillStyle = activeTheme.primary;
        ctx.shadowColor = activeTheme.glow;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(px, py, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0; // reset
      }

      // 5. Desenha os nós neurais (células brilhantes)
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const glowIntensity = n.activity * (isSpeaking ? 16 : 8);

        ctx.fillStyle = activeTheme.primary;
        ctx.shadowColor = activeTheme.glow;
        ctx.shadowBlur = glowIntensity;

        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
        ctx.fill();

        // Núcleo central brilhante
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius * 0.45, 0, Math.PI * 2);
        ctx.fill();

        ctx.shadowBlur = 0; // reset
      }

      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseleave', handleMouseLeave);
    };
  }, [isEnabled, theme, density, agentState]);

  return (
    <>
      {/* Canvas da Rede Neural (atrás dos controles, sobre o fundo) */}
      {isEnabled && (
        <canvas
          ref={canvasRef}
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-0 h-full w-full opacity-80 transition-opacity duration-700"
        />
      )}

      {/* Botão flutuante arrastável no topo do HUD */}
      <div
        style={buttonDragStyle}
        {...buttonDragHandlers}
        className={cn(
          'pointer-events-auto absolute top-3 left-[calc(min(17rem,50vw-1.125rem)+8.25rem)] z-30 inline-flex touch-none items-center gap-1.5 select-none transition-all duration-200',
          isButtonDragging ? 'cursor-grabbing' : 'cursor-grab'
        )}
      >
        <button
          type="button"
          onClick={() => {
            if (wasButtonDragged()) return;
            toggleEnabled();
          }}
          title={isEnabled ? 'Rede Neural Ativa · Clique para Desligar' : 'Rede Neural Desligada · Clique para Ativar'}
          aria-label={isEnabled ? 'Desativar visualização de rede neural' : 'Ativar visualização de rede neural'}
          className={cn(
            'inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold tracking-wider uppercase transition-all duration-200 backdrop-blur-2xl',
            isEnabled
              ? 'border border-cyan-400/50 bg-black/85 text-cyan-200 shadow-[0_0_16px_rgba(6,182,212,0.35)] ring-1 ring-cyan-400/30'
              : 'border border-white/10 bg-black/70 text-slate-400 hover:border-white/20 hover:text-slate-200'
          )}
        >
          <Brain
            className={cn(
              'size-3.5 transition-colors',
              isEnabled ? 'text-cyan-300 drop-shadow-[0_0_8px_rgba(34,211,238,0.9)] animate-pulse' : 'text-slate-500'
            )}
          />
          <span className="hidden sm:inline">Rede Neural</span>
          <span
            className={cn(
              'size-2 rounded-full transition-all',
              isEnabled ? 'bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,1)]' : 'bg-slate-600'
            )}
          />
        </button>

        {/* Botãozinho de Configuração rápida */}
        {isEnabled && (
          <button
            type="button"
            onClick={() => {
              if (wasButtonDragged()) return;
              setIsConfigOpen(true);
            }}
            title="Configurar estilo da Rede Neural"
            aria-label="Abrir configurações da rede neural"
            className="rounded-xl border border-cyan-400/30 bg-black/80 p-2 text-cyan-300 backdrop-blur-2xl transition hover:border-cyan-300/60 hover:bg-slate-900/90 active:scale-95"
          >
            <Settings className="size-3.5" />
          </button>
        )}
      </div>

      {/* Modal de Personalização da Rede Neural */}
      {isConfigOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-3 backdrop-blur-md">
          <div className="relative w-full max-w-sm rounded-2xl border border-cyan-500/30 bg-slate-950/95 p-5 text-white shadow-[0_0_35px_rgba(6,182,212,0.25)] backdrop-blur-2xl">
            <header className="flex items-center justify-between border-b border-white/10 pb-3">
              <div className="flex items-center gap-2.5">
                <Brain className="size-5 text-cyan-400 drop-shadow-[0_0_8px_rgba(34,211,238,0.8)]" />
                <h3 className="text-sm font-semibold tracking-wide">Rede Neural do Jarvis</h3>
              </div>
              <button
                type="button"
                onClick={() => setIsConfigOpen(false)}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-white/10 hover:text-white"
                aria-label="Fechar configurações da rede neural"
              >
                <X className="size-4" />
              </button>
            </header>

            <div className="mt-4 space-y-4 text-xs">
              <div>
                <label className="text-slate-400">Tema Sináptico:</label>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {(Object.keys(THEMES) as ThemeKey[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => changeTheme(key)}
                      className={cn(
                        'flex items-center gap-2 rounded-xl border p-2.5 transition text-left',
                        theme === key
                          ? 'border-cyan-400 bg-cyan-950/40 text-cyan-200 shadow-[0_0_10px_rgba(6,182,212,0.25)]'
                          : 'border-white/10 bg-slate-900/60 text-slate-300 hover:border-white/20'
                      )}
                    >
                      <span className="size-3 rounded-full" style={{ backgroundColor: THEMES[key].primary }} />
                      <span className="font-medium">{THEMES[key].name}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-slate-400">Densidade de Neurônios:</label>
                <div className="mt-2 flex gap-2">
                  {(['low', 'medium', 'high'] as const).map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => changeDensity(d)}
                      className={cn(
                        'flex-1 rounded-xl border py-2 text-center font-medium capitalize transition',
                        density === d
                          ? 'border-cyan-400 bg-cyan-950/40 text-cyan-200'
                          : 'border-white/10 bg-slate-900/60 text-slate-300 hover:border-white/20'
                      )}
                    >
                      {d === 'low' ? 'Leve (30)' : d === 'medium' ? 'Equilibrado (50)' : 'Denso (75)'}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-cyan-500/20 bg-cyan-950/20 p-3 text-[11px] text-cyan-300/80">
                💡 <strong>Dica:</strong> A rede reage à voz do Jarvis em tempo real (acelera quando ele fala e pulsa suavemente quando ele ouve).
              </div>
            </div>

            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => setIsConfigOpen(false)}
                className="rounded-xl border border-cyan-400/40 bg-cyan-500/20 px-4 py-2 text-xs font-semibold text-cyan-200 transition hover:bg-cyan-500/30"
              >
                Concluir
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

