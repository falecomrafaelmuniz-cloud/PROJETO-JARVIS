'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Gauge,
} from 'lucide-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

interface AssetData {
  symbol: string;
  name: string;
  category: 'global' | 'currency' | 'commodity' | 'equity' | 'index';
  correlation: 'direct' | 'inverse' | 'heavyweight';
  price: number;
  changePercent: number;
  dayHighPercent?: number;
  dayLowPercent?: number;
  momentum15m?: number;
  rangePosition?: number;
  sparkline?: number[];
  currency: string;
  weight: number;
}

interface QuantResponse {
  timestamp: number;
  assets: AssetData[];
  biasScore: number;
  biasDelta15m?: number;
  biasState: string;
  summary: string;
}

function formatSignedPct(value: number): string {
  const safe = Number.isFinite(value) ? value : 0;
  return `${safe > 0 ? '+' : ''}${safe.toFixed(2)}%`;
}

function getAssetFlowBadge(asset: AssetData): { label: string; cls: string } | null {
  const day = asset.changePercent;
  const high = asset.dayHighPercent ?? day;
  const low = asset.dayLowPercent ?? day;
  const m15 = asset.momentum15m ?? 0;

  // Estava negativo no dia e virou positivo com fluxo comprador
  if (day > 0 && low < -0.1 && m15 >= 0.05) {
    return { label: 'Positivando', cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35' };
  }
  // Ainda positivo no dia, mas devolveu bastante da máxima ou caindo nos últimos 15m
  if (day > 0 && (high - day >= 0.4 || m15 <= -0.12)) {
    return { label: 'Devolvendo', cls: 'bg-amber-500/20 text-amber-300 border-amber-400/35' };
  }
  // Negativo no dia, mas puxando forte nos últimos 15m
  if (day < 0 && m15 >= 0.15) {
    return { label: 'Recuperando', cls: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/35' };
  }
  // Caindo perto da mínima com fluxo vendedor
  if (day < 0 && (asset.rangePosition ?? 50) <= 20 && m15 <= -0.1) {
    return { label: 'Pressionado', cls: 'bg-rose-500/20 text-rose-300 border-rose-400/35' };
  }
  return null;
}

function IntradaySparkline({
  values,
  momentum15m,
  changePercent,
}: {
  values: number[];
  momentum15m: number;
  changePercent: number;
}) {
  if (!values || values.length < 2) {
    return <div className="h-14 w-full rounded-md bg-white/[0.03]" />;
  }

  const w = 180;
  const h = 56;
  const padY = 5;
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawSpan = Math.max(0.04, rawMax - rawMin);
  // Usa estritamente a amplitude real do ativo no dia para o gráfico não ficar achatado
  const minVal = rawMin - rawSpan * 0.08;
  const maxVal = rawMax + rawSpan * 0.08;
  const span = Math.max(0.05, maxVal - minVal);

  const toY = (v: number) => h - padY - ((v - minVal) / span) * (h - padY * 2);
  const coords = values.map((v, i) => ({
    x: Number(((i / (values.length - 1)) * w).toFixed(1)),
    y: Number(toY(v).toFixed(1)),
  }));
  const points = coords.map((p) => `${p.x},${p.y}`);
  const areaPoints = `0,${h} ${points.join(' ')} ${w},${h}`;

  const showZeroLine = minVal <= 0 && maxVal >= 0;
  const zeroY = toY(0);
  const last = coords[coords.length - 1];
  const strokeColor =
    momentum15m > 0.04 ? '#34d399' : momentum15m < -0.04 ? '#fb7185' : changePercent >= 0 ? '#22d3ee' : '#f43f5e';

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-14 w-full overflow-visible rounded-md bg-slate-950/60"
    >
      <polygon fill={strokeColor} opacity="0.14" points={areaPoints} />
      {showZeroLine && (
        <line
          x1={0}
          y1={zeroY}
          x2={w}
          y2={zeroY}
          stroke="rgba(226,232,240,0.4)"
          strokeWidth="1.2"
          strokeDasharray="3 3"
        />
      )}
      <polyline
        fill="none"
        stroke={strokeColor}
        strokeWidth="2.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={points.join(' ')}
      />
      <circle cx={last.x} cy={last.y} r="3.4" fill={strokeColor} stroke="#ffffff" strokeWidth="1" />
    </svg>
  );
}

interface QuantPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function QuantPanel({ isOpen, onClose }: QuantPanelProps) {
  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-quant-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-quant-panel-v5', {
    defaultScale: 0.42,
    transformOrigin: 'top left',
  });

  const [quantData, setQuantData] = useState<QuantResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string>('');
  const abortRef = useRef<AbortController | null>(null);

  const fetchQuantData = useCallback(async () => {
    if (!isOpen) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      setLoading(true);
      const res = await fetch('/api/quant-data', {
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!res.ok) throw new Error('Falha ao obter dados');
      const data: QuantResponse = await res.json();
      if (controller.signal.aborted) return;
      setQuantData(data);
      setLastUpdated(
        new Date().toLocaleTimeString('pt-BR', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') {
        console.warn('Erro ao carregar dados quant:', err);
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      abortRef.current?.abort();
      return;
    }
    fetchQuantData();
    const interval = setInterval(fetchQuantData, 20_000);
    return () => {
      clearInterval(interval);
      abortRef.current?.abort();
    };
  }, [isOpen, fetchQuantData]);

  if (!isOpen) return null;

  const rawScore = Number(quantData?.biasScore);
  const biasScore = Number.isFinite(rawScore) ? Math.max(-100, Math.min(100, rawScore)) : 0;
  const biasState = quantData?.biasState ?? 'MOMENTO EM EQUILÃBRIO';

  // Ã‚ngulo do ponteiro: -90deg (venda max) a +90deg (compra max)
  const needleAngle = Number.isFinite(biasScore) ? (biasScore / 100) * 90 : 0;

  // Cores dinÃ¢micas do acelerador
  const getBiasColor = () => {
    if (biasScore >= 35) return 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10';
    if (biasScore >= 10) return 'text-emerald-300 border-emerald-400/30 bg-emerald-400/10';
    if (biasScore <= -35) return 'text-rose-500 border-rose-500/40 bg-rose-500/10';
    if (biasScore <= -10) return 'text-amber-400 border-amber-400/30 bg-amber-400/10';
    return 'text-cyan-300 border-cyan-400/30 bg-cyan-400/10';
  };

  const getNeedleColor = () => {
    if (biasScore >= 20) return '#10b981'; // emerald
    if (biasScore <= -20) return '#f43f5e'; // rose
    return '#06b6d4'; // cyan
  };

  return (
    <aside
      aria-label="Painel Quant B3"
      style={dragStyle} data-hud-panel="trader"
      {...dragHandlers}
      title="Arraste pela mãozinha para mover · Arraste pelas bordas para aumentar ou diminuir"
      className={cn(
        'pointer-events-auto absolute top-[17.5rem] left-3 z-40 w-[min(58rem,calc(100vw-1.5rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/35 bg-black/92 text-white backdrop-blur-2xl',
        'shadow-[0_0_30px_rgba(6,182,212,0.22)] shadow-[0_12px_40px_rgba(0,0,0,0.75)] ring-1 shadow-cyan-950/50 ring-cyan-500/25 transition-all duration-200',
        isDragging || isResizingEdge ? 'shadow-[0_0_35px_rgba(6,182,212,0.45)] ring-1 ring-cyan-300/80' : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Visual de linhas hologrÃ¡ficas nos cantos superiores */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/70 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-16 -translate-x-1/2 rounded-full bg-cyan-400/50 blur-xs" />

      {/* HEADER DO PAINEL (ÃREA DE ARRASTO) */}
      <div
        className="flex items-center justify-between border-b border-cyan-500/20 bg-cyan-950/30 px-3.5 py-2.5"
      >
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-lg border border-cyan-400/40 bg-cyan-500/15 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.3)]">
            <Gauge className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-sm font-bold tracking-wider text-cyan-100 uppercase">
                Painel Quant · Trader B3
              </h2>
              <span className="flex size-2.5 rounded-full bg-emerald-400 shadow-[0_0_6px_#10b981]" />
            </div>
            <p className="text-[10px] text-white/50">
              Mini Ãndice (WIN) Â· CorrelaÃ§Ãµes Globais
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            data-no-drag="true"
            onClick={fetchQuantData}
            title="Atualizar cotações agora"
            className="flex size-6 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-200/80 transition hover:border-cyan-300/45 hover:bg-cyan-400/20 hover:text-cyan-100"
          >
            <RefreshCw className={cn('size-3', loading && 'animate-spin text-cyan-300')} />
          </button>
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
            closeTitle="Fechar Painel"
          />
        </div>
      </div>

      {!minimized && (
        <>
          {/* ======================================================== */}
          {/* SEÃ‡ÃƒO FIXA: ACELERADOR DE VIÃ‰S QUANT (TACÃ”METRO / SPEEDOMETER) */}
          {/* Fica sempre visÃ­vel no topo e NUNCA Ã© escondido pelo scroll! */}
          {/* ======================================================== */}
          <div className="relative border-b border-cyan-500/20 bg-gradient-to-b from-white/[0.04] to-transparent px-4 py-3 shadow-inner">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold tracking-wider text-white/85 uppercase">
                Acelerador de Viés (Momento)
              </span>
              <span className="font-mono text-xs font-medium text-white/65">
                {lastUpdated ? `Atualizado ${lastUpdated}` : 'Sincronizando...'}
              </span>
            </div>

            {/* Medidor SVG Radial tipo TacÃ´metro com Ponteiro */}
            <div className="mt-1.5 flex flex-col items-center gap-4 md:flex-row md:items-center md:justify-between">
              {/* Medidor SVG Radial tipo Tacômetro com Ponteiro */}
              <div className="relative flex shrink-0 flex-col items-center justify-center">
                <svg viewBox="0 0 200 110" className="w-48 drop-shadow-[0_0_15px_rgba(6,182,212,0.25)]">
                  <defs>
                    <linearGradient id="quantGaugeGradientFixed" x1="0%" y1="0%" x2="100%" y2="0%">
                      <stop offset="0%" stopColor="#ef4444" />
                      <stop offset="35%" stopColor="#f59e0b" />
                      <stop offset="50%" stopColor="#06b6d4" />
                      <stop offset="65%" stopColor="#10b981" />
                      <stop offset="100%" stopColor="#059669" />
                    </linearGradient>
                  </defs>
                  <path
                    d="M 20 100 A 80 80 0 0 1 180 100"
                    fill="none"
                    stroke="#1e293b"
                    strokeWidth="12"
                    strokeLinecap="round"
                  />
                  <path
                    d="M 20 100 A 80 80 0 0 1 180 100"
                    fill="none"
                    stroke="url(#quantGaugeGradientFixed)"
                    strokeWidth="10"
                    strokeLinecap="round"
                    opacity="0.9"
                  />
                  <line x1="20" y1="100" x2="15" y2="100" stroke="#ef4444" strokeWidth="2.5" />
                  <line x1="100" y1="20" x2="100" y2="14" stroke="#06b6d4" strokeWidth="2.5" />
                  <line x1="180" y1="100" x2="185" y2="100" stroke="#10b981" strokeWidth="2.5" />
                  <g transform="translate(100, 100)">
                    <line
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="-68"
                      stroke={getNeedleColor()}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      transform={`rotate(${needleAngle})`}
                      style={{
                        transition: 'transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1)',
                      }}
                    />
                    <circle cx="0" cy="0" r="7" fill="#0f172a" stroke={getNeedleColor()} strokeWidth="3" />
                    <circle cx="0" cy="0" r="3" fill="#ffffff" />
                  </g>
                </svg>
                <div className="flex w-48 justify-between px-1 text-[11px] font-bold">
                  <span className="text-rose-400">VENDA</span>
                  <span className="text-cyan-300">0%</span>
                  <span className="text-emerald-400">COMPRA</span>
                </div>
              </div>

              {/* Score, Tendência 15m e Resumo em fonte grande */}
              <div className="flex flex-1 flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <div
                    className={cn(
                      'rounded-xl border px-3.5 py-1.5 font-mono text-2xl font-extrabold tracking-wider shadow-sm transition-all',
                      getBiasColor()
                    )}
                  >
                    {biasScore > 0 ? `+${biasScore}%` : `${biasScore}%`}
                  </div>
                  <div
                    className={cn(
                      'rounded-xl border px-3 py-1.5 text-sm font-bold tracking-wide uppercase',
                      getBiasColor()
                    )}
                  >
                    {biasState}
                  </div>
                  {typeof quantData?.biasDelta15m === 'number' && quantData.biasDelta15m !== 0 && (
                    <div
                      title="Direção do viés nos últimos 15 minutos"
                      className={cn(
                        'rounded-xl border px-2.5 py-1.5 font-mono text-xs font-bold',
                        quantData.biasDelta15m > 0
                          ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-300'
                          : 'border-rose-400/40 bg-rose-500/15 text-rose-300'
                      )}
                    >
                      {quantData.biasDelta15m > 0
                        ? `▲ +${quantData.biasDelta15m} pts (15m)`
                        : `▼ ${quantData.biasDelta15m} pts (15m)`}
                    </div>
                  )}
                </div>

                <div className="rounded-lg border border-white/15 bg-black/50 px-3 py-2 text-xs font-medium text-white/90 sm:text-sm">
                  <span className="font-bold text-cyan-300">Resumo: </span>
                  {quantData?.summary ?? 'Obtendo cotações dos mercados...'}
                </div>
              </div>
            </div>
          </div>

          {/* ======================================================== */}
          {/* SEÃ‡ÃƒO ROLÃVEL: GRADE DOS ATIVOS E AÃ‡Ã•ES RÃPIDAS          */}
          {/* ======================================================== */}
          <div
            data-scrollable="true"
            className="flex max-h-[calc(100vh-14rem)] flex-col gap-2.5 overflow-y-auto p-3.5 lg:max-h-none lg:overflow-visible"
          >
            {/* GRADE DOS ATIVOS QUE INFLUENCIAM O MINI ÃNDICE */}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-bold tracking-wider text-white/85 uppercase">
                  Termômetro e Fluxo Intraday (5m)
                </span>
                <span className="text-xs font-semibold text-white/65">
                  {quantData?.assets.length ?? 11} ativos
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {(quantData?.assets ?? []).map((asset) => {
                  const isPos = asset.changePercent > 0;
                  const isNeg = asset.changePercent < 0;
                  const m15 = asset.momentum15m ?? 0;
                  const lowPct = asset.dayLowPercent ?? asset.changePercent;
                  const highPct = asset.dayHighPercent ?? asset.changePercent;
                  const rangePos = asset.rangePosition ?? 50;
                  const flowBadge = getAssetFlowBadge(asset);

                  return (
                    <div
                      key={asset.symbol}
                      className="flex flex-col justify-between gap-1.5 rounded-xl border border-white/15 bg-white/[0.04] p-2.5 transition-all hover:border-cyan-400/50 hover:bg-white/[0.07]"
                    >
                      <div className="flex items-start justify-between gap-1.5">
                        <div className="min-w-0">
                          <span className="font-mono text-base font-extrabold tracking-wide text-white">
                            {asset.symbol.replace('.SA', '').replace('^', '').replace('=F', '')}
                          </span>
                          {flowBadge && (
                            <span
                              className={cn(
                                'mt-0.5 block w-fit rounded border px-1.5 py-0.5 text-[11px] leading-none font-bold',
                                flowBadge.cls
                              )}
                            >
                              {flowBadge.label}
                            </span>
                          )}
                        </div>
                        <div className="text-right">
                          <span
                            className={cn(
                              'flex items-center justify-end gap-1 font-mono text-base font-extrabold',
                              isPos ? 'text-emerald-400' : isNeg ? 'text-rose-400' : 'text-white/75'
                            )}
                          >
                            {isPos ? (
                              <TrendingUp className="size-4 shrink-0" />
                            ) : isNeg ? (
                              <TrendingDown className="size-4 shrink-0" />
                            ) : null}
                            {formatSignedPct(asset.changePercent)}
                          </span>
                          <span
                            title="Variação nos últimos 15 minutos"
                            className={cn(
                              'block font-mono text-xs font-bold',
                              m15 > 0.02
                                ? 'text-emerald-300'
                                : m15 < -0.02
                                ? 'text-rose-300'
                                : 'text-slate-300'
                            )}
                          >
                            {m15 > 0.02 ? '▲' : m15 < -0.02 ? '▼' : '•'} {formatSignedPct(m15)} (15m)
                          </span>
                        </div>
                      </div>

                      <IntradaySparkline
                        values={asset.sparkline ?? []}
                        momentum15m={m15}
                        changePercent={asset.changePercent}
                      />

                      <div className="space-y-1">
                        <div className="relative h-2 w-full rounded-full bg-slate-800">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-rose-500/70 via-cyan-400/70 to-emerald-400/80"
                            style={{ width: `${rangePos}%` }}
                          />
                          <span
                            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-cyan-300 shadow-[0_0_8px_rgba(34,211,238,0.95)]"
                            style={{ left: `${rangePos}%` }}
                          />
                        </div>
                        <div className="flex justify-between font-mono text-xs font-semibold text-white/75">
                          <span>Mín {formatSignedPct(lowPct)}</span>
                          <span>Máx {formatSignedPct(highPct)}</span>
                        </div>
                      </div>

                      <div className="flex items-center justify-between text-xs text-white/75">
                        <span className="truncate pr-2 font-medium" title={asset.name}>
                          {asset.name.split('(')[0]}
                        </span>
                        <span
                          className={cn(
                            'shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold',
                            asset.correlation === 'inverse'
                              ? 'bg-amber-500/20 text-amber-300'
                              : asset.correlation === 'heavyweight'
                              ? 'bg-purple-500/20 text-purple-300'
                              : 'bg-cyan-500/20 text-cyan-300'
                          )}
                        >
                          {asset.correlation === 'inverse'
                            ? 'Inversa'
                            : asset.correlation === 'heavyweight'
                            ? 'Peso B3'
                            : 'Direta'}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
