'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

interface TimeData {
  hours: string;
  minutes: string;
  seconds: string;
  secondsNum: number;
  period: string;
  dayOfWeek: string;
  dayNumber: string;
  monthName: string;
  year: string;
}

function getTimeData(date: Date, is24h: boolean): TimeData {
  const formatterOptions: Intl.DateTimeFormatOptions = {
    timeZone: 'America/Sao_Paulo',
    hour12: !is24h,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  };

  const parts = new Intl.DateTimeFormat('pt-BR', formatterOptions).formatToParts(date);
  let hours = '00';
  let minutes = '00';
  let seconds = '00';
  let period = '';

  for (const part of parts) {
    if (part.type === 'hour') hours = part.value;
    if (part.type === 'minute') minutes = part.value;
    if (part.type === 'second') seconds = part.value;
    if (part.type === 'dayPeriod') period = part.value.toUpperCase();
  }

  const dateParts = new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).formatToParts(date);

  let dayOfWeek = '';
  let dayNumber = '';
  let monthName = '';
  let year = '';

  for (const part of dateParts) {
    if (part.type === 'weekday') {
      dayOfWeek = part.value.charAt(0).toUpperCase() + part.value.slice(1);
    }
    if (part.type === 'day') dayNumber = part.value;
    if (part.type === 'month') {
      monthName = part.value.charAt(0).toUpperCase() + part.value.slice(1);
    }
    if (part.type === 'year') year = part.value;
  }

  return {
    hours,
    minutes,
    seconds,
    secondsNum: parseInt(seconds, 10) || 0,
    period,
    dayOfWeek,
    dayNumber,
    monthName,
    year,
  };
}

export function ClockWidget() {
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-clock-widget-v1');

  const [currentDate, setCurrentDate] = useState<Date | null>(null);
  const [isCompact, setIsCompact] = useState(false);
  const [is24h, setIs24h] = useState(true);

  // Carrega preferências salvas
  useEffect(() => {
    try {
      const savedCompact = localStorage.getItem('jarvis_clock_compact');
      if (savedCompact !== null) {
        setIsCompact(savedCompact === 'true');
      }
      const saved24h = localStorage.getItem('jarvis_clock_24h');
      if (saved24h !== null) {
        setIs24h(saved24h === 'true');
      }
    } catch {
      // Ignora erro de localStorage
    }
  }, []);

  // Atualiza relógio com precisão a cada segundo
  useEffect(() => {
    setCurrentDate(new Date());
    const interval = window.setInterval(() => {
      setCurrentDate(new Date());
    }, 1000);
    return () => window.clearInterval(interval);
  }, []);

  const toggleCompact = () => {
    setIsCompact((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('jarvis_clock_compact', String(next));
      } catch {}
      return next;
    });
  };

  const toggleFormat = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIs24h((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('jarvis_clock_24h', String(next));
      } catch {}
      return next;
    });
  };

  const timeData = useMemo(() => {
    if (!currentDate) return null;
    return getTimeData(currentDate, is24h);
  }, [currentDate, is24h]);

  if (!timeData) return null;

  // Cálculo do anel de progresso dos segundos (raio 22, circunferência ~138.2)
  const ringRadius = 22;
  const circumference = 2 * Math.PI * ringRadius;
  const strokeDashoffset = circumference - (timeData.secondsNum / 60) * circumference;

  return (
    <div
      aria-label="Widget de Relógio do Jarvis"
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pelo centro para mover o Relógio · Arraste pela borda para redimensionar"
      className={cn(
        'pointer-events-auto absolute top-3 left-1/2 z-30 -translate-x-1/2 touch-none select-none',
        'rounded-2xl border border-cyan-400/30 bg-black/80 text-white backdrop-blur-2xl',
        'shadow-[0_8px_32px_rgba(0,0,0,0.6)] shadow-cyan-950/40 transition-all duration-200',
        isDragging || isResizingEdge
          ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
          : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab',
        isCompact ? 'w-auto px-4 py-2' : 'w-[min(20rem,calc(100vw-2rem))] p-3.5'
      )}
    >
      {/* Visual de linhas holográficas nos cantos superiores */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-12 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />

      {isCompact ? (
        /* MODO COMPACTO (Pill Sleek HUD) */
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center">
            <span className="absolute size-2 animate-ping rounded-full bg-cyan-400" />
            <span className="relative size-2 rounded-full bg-cyan-400" />
          </div>
          <div className="flex items-baseline gap-1 font-mono">
            <span className="text-sm font-bold tracking-tight text-white drop-shadow-[0_0_8px_rgba(34,211,238,0.6)]">
              {timeData.hours}:{timeData.minutes}
            </span>
            <span className="text-[11px] font-semibold text-cyan-300">:{timeData.seconds}</span>
            {!is24h && <span className="ml-0.5 text-[9px] text-white/50">{timeData.period}</span>}
          </div>
          <span className="text-[10px] text-white/40">|</span>
          <span className="text-[11px] font-medium text-white/75">
            {timeData.dayNumber} {timeData.monthName.slice(0, 3)}
          </span>
          <button
            onClick={toggleCompact}
            title="Expandir relógio"
            className="ml-1 p-1 text-white/40 transition-colors hover:text-cyan-300"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          </button>
        </div>
      ) : (
        /* MODO COMPLETO (Futuristic Holographic HUD) */
        <div className="space-y-3">
          {/* Header do Widget */}
          <div className="flex items-center justify-between border-b border-white/10 pb-2">
            <div className="flex items-center gap-2">
              <div className="relative flex size-2 items-center justify-center">
                <span className="absolute size-2 animate-ping rounded-full bg-cyan-400/80" />
                <span className="relative size-1.5 rounded-full bg-cyan-300 shadow-[0_0_8px_#22d3ee]" />
              </div>
              <span className="text-[10px] font-semibold tracking-widest text-cyan-200/90 uppercase">
                JARVIS • CHRONO
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                onClick={toggleFormat}
                title="Alternar entre 24h e 12h"
                className="rounded-md border border-cyan-400/20 bg-cyan-950/40 px-1.5 py-0.5 font-mono text-[9px] font-medium text-cyan-300/80 transition-colors hover:bg-cyan-900/60"
              >
                {is24h ? '24H' : '12H'}
              </button>
              <button
                onClick={toggleCompact}
                title="Minimizar relógio"
                className="rounded-md p-1 text-white/40 transition-colors hover:bg-white/5 hover:text-white/80"
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            </div>
          </div>

          {/* Display Principal do Relógio */}
          <div className="flex items-center justify-between px-1">
            {/* Horas e Minutos Gigantes com Brilho Neon */}
            <div className="flex flex-col">
              <div className="flex items-baseline font-mono tracking-tight select-none">
                <span className="bg-gradient-to-b from-white via-cyan-100 to-cyan-300 bg-clip-text text-4xl font-extrabold text-transparent drop-shadow-[0_0_16px_rgba(6,182,212,0.6)]">
                  {timeData.hours}
                </span>
                <span className="animate-pulse px-1 text-3xl font-light text-cyan-300/80">:</span>
                <span className="bg-gradient-to-b from-white via-cyan-100 to-cyan-300 bg-clip-text text-4xl font-extrabold text-transparent drop-shadow-[0_0_16px_rgba(6,182,212,0.6)]">
                  {timeData.minutes}
                </span>
                {!is24h && (
                  <span className="ml-1.5 text-xs font-semibold text-cyan-400/80">
                    {timeData.period}
                  </span>
                )}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-white/50">
                <span className="size-1 rounded-full bg-cyan-400/60" />
                <span className="font-medium tracking-wide">Fuso de Brasília (BRT • UTC-03)</span>
              </div>
            </div>

            {/* Anel Circular Futurista para Segundos (Reator Arc HUD) */}
            <div className="relative flex size-14 items-center justify-center">
              <svg className="size-14 -rotate-90 transform" viewBox="0 0 52 52">
                {/* Trilha de fundo */}
                <circle
                  cx="26"
                  cy="26"
                  r={ringRadius}
                  stroke="rgba(255, 255, 255, 0.1)"
                  strokeWidth="3"
                  fill="transparent"
                />
                {/* Arco de progresso ativo com neon */}
                <circle
                  cx="26"
                  cy="26"
                  r={ringRadius}
                  stroke="url(#cyanGlowGradient)"
                  strokeWidth="3.2"
                  strokeDasharray={circumference}
                  strokeDashoffset={strokeDashoffset}
                  strokeLinecap="round"
                  fill="transparent"
                  className="transition-all duration-300 ease-linear"
                />
                <defs>
                  <linearGradient id="cyanGlowGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#38bdf8" />
                    <stop offset="100%" stopColor="#06b6d4" />
                  </linearGradient>
                </defs>
              </svg>
              {/* Segundos exibidos no centro do anel */}
              <div className="absolute flex flex-col items-center justify-center font-mono">
                <span className="text-xs font-extrabold text-cyan-300 drop-shadow-[0_0_6px_rgba(34,211,238,0.8)]">
                  {timeData.seconds}
                </span>
                <span className="-mt-0.5 text-[7px] text-white/40 uppercase">SEG</span>
              </div>
            </div>
          </div>

          {/* Barra de Data e Dia da Semana */}
          <div className="flex items-center justify-between rounded-xl border border-white/5 bg-white/[0.03] p-2">
            <div className="flex items-center gap-2">
              <div className="rounded-lg border border-cyan-400/20 bg-cyan-500/10 p-1.5 text-cyan-300">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
              </div>
              <div>
                <p className="text-[11px] font-semibold tracking-wide text-white">
                  {timeData.dayOfWeek}
                </p>
                <p className="text-[9px] text-white/50">
                  {timeData.dayNumber} de {timeData.monthName} de {timeData.year}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1 rounded-md border border-cyan-400/20 bg-cyan-500/10 px-2 py-1">
              <span className="size-1.5 animate-pulse rounded-full bg-cyan-400" />
              <span className="font-mono text-[9px] font-medium text-cyan-300">ONLINE</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
