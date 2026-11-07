'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  TrendingUp,
  TrendingDown,
  Activity,
  RefreshCw,
  Gauge,
  X,
  Minimize2,
  Maximize2,
  AlertOctagon,
  Eye,
  FileText,
  ShieldCheck,
} from 'lucide-react';
import { useRoomContext, useVoiceAssistant } from '@livekit/components-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

interface AssetData {
  symbol: string;
  name: string;
  category: 'global' | 'currency' | 'commodity' | 'equity' | 'index';
  correlation: 'direct' | 'inverse' | 'heavyweight';
  price: number;
  changePercent: number;
  currency: string;
  weight: number;
}

interface QuantResponse {
  timestamp: number;
  assets: AssetData[];
  biasScore: number;
  biasState: string;
  summary: string;
}

interface QuantPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function QuantPanel({ isOpen, onClose }: QuantPanelProps) {
  const room = useRoomContext();
  const { state: agentState } = useVoiceAssistant();
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-quant-panel-v1');

  const [quantData, setQuantData] = useState<QuantResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string>('');

  const fetchQuantData = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/quant-data', { cache: 'no-store' });
      if (!res.ok) throw new Error('Falha ao obter dados');
      const data: QuantResponse = await res.json();
      setQuantData(data);
      setLastUpdated(
        new Date().toLocaleTimeString('pt-BR', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    } catch (err) {
      console.warn('Erro ao carregar dados quant:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    fetchQuantData();
    const interval = setInterval(fetchQuantData, 20_000); // Atualiza a cada 20 segundos
    return () => clearInterval(interval);
  }, [isOpen, fetchQuantData]);

  // Função para enviar comando de texto para o chat do Jarvis
  const enviarComandoJarvis = async (texto: string) => {
    try {
      if (!room || !room.localParticipant) return;
      const encoder = new TextEncoder();
      const payload = encoder.encode(
        JSON.stringify({
          type: 'user_chat_command',
          text: texto,
          timestamp: Date.now(),
        })
      );
      // Envia via data channel se disponível
      await room.localParticipant.publishData(payload, { reliable: true });
    } catch (e) {
      console.warn('Erro ao disparar comando:', e);
    }
  };

  if (!isOpen) return null;

  const biasScore = quantData?.biasScore ?? 0;
  const biasState = quantData?.biasState ?? 'CALCULANDO';

  // Ângulo do ponteiro do tacômetro: -90deg (venda max) a +90deg (compra max)
  const needleAngle = Math.max(-90, Math.min(90, (biasScore / 100) * 90));

  // Cores dinâmicas do acelerador
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
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pelo cabeçalho para mover · Arraste pela borda para redimensionar"
      className={cn(
        'pointer-events-auto absolute top-16 left-4 z-40 w-[min(26rem,calc(100vw-2rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/30 bg-black/85 text-white backdrop-blur-2xl',
        'shadow-[0_0_30px_rgba(6,182,212,0.22)] shadow-[0_12px_40px_rgba(0,0,0,0.7)] ring-1 shadow-cyan-950/50 ring-cyan-500/25 transition-all duration-200',
        isDragging || isResizingEdge ? 'shadow-[0_0_35px_rgba(6,182,212,0.45)] ring-1 ring-cyan-300/80' : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Visual de linhas holográficas nos cantos superiores */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/70 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-16 -translate-x-1/2 rounded-full bg-cyan-400/50 blur-xs" />

      {/* HEADER DO PAINEL */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 bg-cyan-950/20 px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-lg border border-cyan-400/40 bg-cyan-500/15 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.3)]">
            <Gauge className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <h2 className="font-mono text-xs font-bold tracking-wider text-cyan-100 uppercase">
                Painel Quant · Trader B3
              </h2>
              <span className="flex size-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#10b981]" />
            </div>
            <p className="text-[10px] text-white/50">
              Mini Índice (WIN) · Correlações Globais
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={fetchQuantData}
            title="Atualizar cotações agora"
            className="flex size-7 items-center justify-center rounded-lg text-white/60 hover:bg-white/10 hover:text-cyan-300 transition-colors"
          >
            <RefreshCw className={cn('size-3.5', loading && 'animate-spin text-cyan-400')} />
          </button>
          <button
            type="button"
            onClick={() => setMinimized(!minimized)}
            title={minimized ? 'Expandir' : 'Minimizar'}
            className="flex size-7 items-center justify-center rounded-lg text-white/60 hover:bg-white/10 hover:text-white transition-colors"
          >
            {minimized ? <Maximize2 className="size-3.5" /> : <Minimize2 className="size-3.5" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Fechar Painel"
            className="flex size-7 items-center justify-center rounded-lg text-white/60 hover:bg-rose-500/20 hover:text-rose-300 transition-colors"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>

      {!minimized && (
        <div className="flex max-h-[calc(85vh-8rem)] flex-col gap-3 overflow-y-auto p-3.5 [scrollbar-width:thin]">
          {/* ======================================================== */}
          {/* ACELERADOR DE VIÉS QUANT (TACÔMETRO / SPEEDOMETER)      */}
          {/* ======================================================== */}
          <div className="relative overflow-hidden rounded-xl border border-cyan-500/25 bg-gradient-to-b from-white/[0.04] to-transparent p-3 shadow-inner">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold tracking-wider text-white/70 uppercase">
                Acelerador de Viés (Momento)
              </span>
              <span className="font-mono text-[10px] text-white/40">
                {lastUpdated ? `Atualizado ${lastUpdated}` : 'Sincronizando...'}
              </span>
            </div>

            {/* Medidor SVG Radial tipo Tacômetro */}
            <div className="relative my-2 flex flex-col items-center justify-center">
              <svg viewBox="0 0 200 110" className="w-56 drop-shadow-[0_0_15px_rgba(6,182,212,0.25)]">
                {/* Arco de fundo (gradiente vermelho -> ciano -> verde) */}
                <defs>
                  <linearGradient id="quantGaugeGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#ef4444" />
                    <stop offset="35%" stopColor="#f59e0b" />
                    <stop offset="50%" stopColor="#06b6d4" />
                    <stop offset="65%" stopColor="#10b981" />
                    <stop offset="100%" stopColor="#059669" />
                  </linearGradient>
                </defs>

                {/* Trilho base escuro */}
                <path
                  d="M 20 100 A 80 80 0 0 1 180 100"
                  fill="none"
                  stroke="#1e293b"
                  strokeWidth="12"
                  strokeLinecap="round"
                />

                {/* Trilho colorido */}
                <path
                  d="M 20 100 A 80 80 0 0 1 180 100"
                  fill="none"
                  stroke="url(#quantGaugeGradient)"
                  strokeWidth="10"
                  strokeLinecap="round"
                  opacity="0.9"
                />

                {/* Marcadores discretos */}
                <line x1="20" y1="100" x2="15" y2="100" stroke="#ef4444" strokeWidth="2" />
                <line x1="100" y1="20" x2="100" y2="14" stroke="#06b6d4" strokeWidth="2" />
                <line x1="180" y1="100" x2="185" y2="100" stroke="#10b981" strokeWidth="2" />

                {/* Ponteiro central rotativo */}
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

              {/* Rótulos do tacômetro */}
              <div className="flex w-56 justify-between px-2 text-[9px] font-bold">
                <span className="text-rose-400">VENDA (-100%)</span>
                <span className="text-cyan-300">0%</span>
                <span className="text-emerald-400">COMPRA (+100%)</span>
              </div>

              {/* Placa Digital de Score */}
              <div className="mt-2.5 flex items-center gap-2">
                <div
                  className={cn(
                    'rounded-xl border px-3 py-1 font-mono text-base font-extrabold tracking-wider shadow-sm transition-all',
                    getBiasColor()
                  )}
                >
                  {biasScore > 0 ? `+${biasScore}%` : `${biasScore}%`}
                </div>
                <div
                  className={cn(
                    'rounded-xl border px-2.5 py-1 text-xs font-bold tracking-wide uppercase',
                    getBiasColor()
                  )}
                >
                  {biasState}
                </div>
              </div>
            </div>

            {/* Resumo Interpretativo de Mercado */}
            <div className="mt-1 rounded-lg border border-white/10 bg-black/40 px-2.5 py-1.5 text-center text-[10px] text-white/80">
              <span className="text-cyan-300 font-semibold">Resumo: </span>
              {quantData?.summary ?? 'Obtendo cotações dos mercados...'}
            </div>
          </div>

          {/* ======================================================== */}
          {/* GRADE DOS ATIVOS QUE INFLUENCIAM O MINI ÍNDICE           */}
          {/* ======================================================== */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-semibold tracking-wider text-white/70 uppercase">
                Termômetro de Ativos Correlacionados
              </span>
              <span className="text-[9px] text-white/40">11 ativos monitorados</span>
            </div>

            <div className="grid grid-cols-2 gap-1.5">
              {(quantData?.assets ?? []).map((asset) => {
                const isPos = asset.changePercent > 0;
                const isNeg = asset.changePercent < 0;

                // Identifica se a variação ajuda ou atrapalha o índice
                // (Para Dólar e DI, alta é vermelho/pressão de baixa para o índice)
                const isFavorable =
                  asset.correlation === 'inverse' ? isNeg : isPos;
                const isUnfavorable =
                  asset.correlation === 'inverse' ? isPos : isNeg;

                return (
                  <div
                    key={asset.symbol}
                    className="flex flex-col justify-between rounded-lg border border-white/10 bg-white/[0.03] p-2 hover:border-cyan-500/40 hover:bg-white/[0.06] transition-all"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-white/95">
                        {asset.symbol.replace('.SA', '').replace('^', '').replace('=F', '')}
                      </span>
                      <span
                        className={cn(
                          'flex items-center gap-0.5 font-mono text-xs font-bold',
                          isPos ? 'text-emerald-400' : isNeg ? 'text-rose-400' : 'text-white/60'
                        )}
                      >
                        {isPos ? (
                          <TrendingUp className="size-3" />
                        ) : isNeg ? (
                          <TrendingDown className="size-3" />
                        ) : null}
                        {isPos ? `+${asset.changePercent}%` : `${asset.changePercent}%`}
                      </span>
                    </div>

                    <div className="mt-1 flex items-center justify-between text-[9px] text-white/50">
                      <span className="truncate max-w-[85px]" title={asset.name}>
                        {asset.name.split('(')[0]}
                      </span>
                      <span
                        className={cn(
                          'rounded px-1 py-0.2 text-[8px] font-semibold',
                          asset.correlation === 'inverse'
                            ? 'bg-amber-500/15 text-amber-300'
                            : asset.correlation === 'heavyweight'
                            ? 'bg-purple-500/15 text-purple-300'
                            : 'bg-cyan-500/15 text-cyan-300'
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

          {/* ======================================================== */}
          {/* BOTÕES DE AÇÕES RÁPIDAS COM O JARVIS                     */}
          {/* ======================================================== */}
          <div>
            <span className="mb-1.5 block text-[11px] font-semibold tracking-wider text-white/70 uppercase">
              Comandos Rápidos do Jarvis
            </span>
            <div className="grid grid-cols-2 gap-1.5">
              <button
                type="button"
                onClick={() =>
                  enviarComandoJarvis('Jarvis, leia a tela do Profit e me informe o cenário agora.')
                }
                className="flex items-center justify-center gap-1.5 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-2 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/20 hover:border-cyan-400 transition-colors shadow-sm"
              >
                <Eye className="size-3.5 text-cyan-300" />
                <span>Ler Tela Profit</span>
              </button>

              <button
                type="button"
                onClick={() =>
                  enviarComandoJarvis('Jarvis, faça o panorama do pré-mercado de hoje.')
                }
                className="flex items-center justify-center gap-1.5 rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-2 py-2 text-xs font-semibold text-emerald-200 hover:bg-emerald-500/20 hover:border-emerald-400 transition-colors shadow-sm"
              >
                <Activity className="size-3.5 text-emerald-300" />
                <span>Pré-Mercado</span>
              </button>

              <button
                type="button"
                onClick={() =>
                  enviarComandoJarvis('Jarvis, me apresente o guia de configuração do layout no Profit.')
                }
                className="flex items-center justify-center gap-1.5 rounded-lg border border-purple-400/30 bg-purple-500/10 px-2 py-2 text-xs font-semibold text-purple-200 hover:bg-purple-500/20 hover:border-purple-400 transition-colors shadow-sm"
              >
                <FileText className="size-3.5 text-purple-300" />
                <span>Guia do Profit</span>
              </button>

              <button
                type="button"
                onClick={() =>
                  enviarComandoJarvis('Jarvis, emergência! Zere a posição a mercado e cancele todas as ordens no Profit!')
                }
                className="flex items-center justify-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/20 px-2 py-2 text-xs font-bold text-rose-200 hover:bg-rose-500/35 hover:border-rose-400 transition-colors shadow-[0_0_12px_rgba(244,63,94,0.25)]"
              >
                <AlertOctagon className="size-3.5 text-rose-400" />
                <span>Zerar / Pânico</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
