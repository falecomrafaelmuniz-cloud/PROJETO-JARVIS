'use client';

import { useEffect, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { Check, RefreshCw } from 'lucide-react';
import { useRoomContext, useSessionContext } from '@livekit/components-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import {
  type DashboardCoordinates,
  type DashboardLocationStatus,
  dashboardEvents,
} from '@/lib/jarvis-dashboard';
import { cn } from '@/lib/shadcn/utils';

const SYSTEM_TOPIC = 'jarvis.system';

interface SystemMetrics {
  cpuPercent: number;
  memoryPercent: number;
  memoryUsedBytes: number;
  memoryTotalBytes: number;
  sentBytesPerSecond: number | null;
  receivedBytesPerSecond: number | null;
}

interface SystemPacket {
  type?: string;
  cpu_percent?: number;
  memory_percent?: number;
  memory_used_bytes?: number;
  memory_total_bytes?: number;
  sent_bytes_per_second?: number | null;
  received_bytes_per_second?: number | null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function parseSystemMetrics(value: unknown): SystemMetrics | null {
  if (!value || typeof value !== 'object') return null;
  const packet = value as SystemPacket;
  if (
    packet.type !== 'jarvis_system' ||
    !isFiniteNumber(packet.cpu_percent) ||
    !isFiniteNumber(packet.memory_percent) ||
    !isFiniteNumber(packet.memory_used_bytes) ||
    !isFiniteNumber(packet.memory_total_bytes)
  ) {
    return null;
  }

  return {
    cpuPercent: Math.min(100, Math.max(0, packet.cpu_percent)),
    memoryPercent: Math.min(100, Math.max(0, packet.memory_percent)),
    memoryUsedBytes: Math.max(0, packet.memory_used_bytes),
    memoryTotalBytes: Math.max(0, packet.memory_total_bytes),
    sentBytesPerSecond: isFiniteNumber(packet.sent_bytes_per_second)
      ? Math.max(0, packet.sent_bytes_per_second)
      : null,
    receivedBytesPerSecond: isFiniteNumber(packet.received_bytes_per_second)
      ? Math.max(0, packet.received_bytes_per_second)
      : null,
  };
}

function formatBytesPerSecond(value: number | null) {
  if (value === null) return 'Amostrando...';
  const megabitsPerSecond = (value * 8) / 1_000_000;
  return `${megabitsPerSecond.toFixed(2)} Mbps`;
}

function formatMemory(bytes: number) {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

const LOCATION_LABELS: Record<DashboardLocationStatus, string> = {
  offline: 'Sessão desconectada',
  requesting: 'Obtendo localização...',
  shared: 'Compartilhada nesta sessão',
  unavailable: 'Indisponível',
};

interface SystemDashboardProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export function SystemDashboard({ isOpen = true, onClose }: SystemDashboardProps = {}) {
  const room = useRoomContext();
  const { isConnected } = useSessionContext();
  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-system-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-system-dashboard-v2');
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [locationStatus, setLocationStatus] = useState<DashboardLocationStatus>('requesting');
  const [locationCoordinates, setLocationCoordinates] = useState<DashboardCoordinates | null>(null);
  const [updateState, setUpdateState] = useState<'idle' | 'updating' | 'success' | 'error'>('idle');
  const [updateMsg, setUpdateMsg] = useState<string | null>(null);

  const handleUpdateClick = async () => {
    if (updateState === 'updating') return;
    setUpdateState('updating');
    setUpdateMsg('Atualizando pelo GitHub...');
    try {
      const res = await fetch('/api/update', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || data.status === 'error') {
        throw new Error(data.message || 'Erro ao atualizar');
      }
      setUpdateState('success');
      setUpdateMsg(data.message || 'Atualizado!');
      setTimeout(() => {
        window.location.reload();
      }, 1500);
    } catch (err) {
      setUpdateState('error');
      setUpdateMsg(err instanceof Error ? err.message : 'Falha na atualização');
      setTimeout(() => {
        setUpdateState('idle');
        setUpdateMsg(null);
      }, 4000);
    }
  };

  useEffect(() => {
    setMetrics(null);
    setLocationStatus(isConnected ? 'requesting' : 'offline');
    setLocationCoordinates(null);
  }, [isConnected]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== SYSTEM_TOPIC) return;

      try {
        const packet: unknown = JSON.parse(new TextDecoder().decode(payload));
        const nextMetrics = parseSystemMetrics(packet);
        if (nextMetrics) {
          setMetrics(nextMetrics);
        }
      } catch (error) {
        console.warn('Não foi possível ler as métricas do sistema recebidas.', error);
      }
    };

    const handleLocation = (event: Event) => {
      const status = (event as CustomEvent<DashboardLocationStatus>).detail;
      if (
        status === 'offline' ||
        status === 'requesting' ||
        status === 'shared' ||
        status === 'unavailable'
      ) {
        setLocationStatus(status);
      }
    };

    const handleCoordinates = (event: Event) => {
      const coordinates = (event as CustomEvent<DashboardCoordinates | null>).detail;
      if (
        coordinates === null ||
        (coordinates &&
          isFiniteNumber(coordinates.latitude) &&
          isFiniteNumber(coordinates.longitude) &&
          coordinates.latitude >= -90 &&
          coordinates.latitude <= 90 &&
          coordinates.longitude >= -180 &&
          coordinates.longitude <= 180)
      ) {
        setLocationCoordinates(coordinates);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    window.addEventListener(dashboardEvents.location, handleLocation);
    window.addEventListener(dashboardEvents.coordinates, handleCoordinates);

    return () => {
      room.off(RoomEvent.DataReceived, handleData);
      window.removeEventListener(dashboardEvents.location, handleLocation);
      window.removeEventListener(dashboardEvents.coordinates, handleCoordinates);
    };
  }, [room]);

  if (!isOpen) return null;

  return (
    <aside
      aria-label="Painéis do sistema"
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pela mãozinha para mover · Arraste pelas bordas para aumentar ou diminuir"
      className={cn(
        'pointer-events-auto absolute top-3 left-3 z-30 w-[min(17rem,calc(50vw-1.125rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/30 bg-black/80 text-white backdrop-blur-2xl',
        'shadow-[0_0_20px_rgba(6,182,212,0.18)] shadow-[0_8px_32px_rgba(0,0,0,0.6)] ring-1 shadow-cyan-950/40 ring-cyan-500/20 transition-all duration-200',
        isDragging || isResizingEdge
          ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
          : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Visual de linhas holográficas nos cantos superiores */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-12 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold tracking-wide">Painéis do Jarvis</h2>
          <p className="mt-0.5 text-[9px] text-white/50">Monitoramento desta sessão</p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-no-drag="true"
            onClick={handleUpdateClick}
            disabled={updateState === 'updating'}
            title="Buscar atualizações do Jarvis no GitHub"
            className={cn(
              'flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-medium transition',
              updateState === 'updating'
                ? 'border-cyan-400/50 bg-cyan-500/20 text-cyan-200'
                : updateState === 'success'
                  ? 'border-emerald-400/50 bg-emerald-500/20 text-emerald-200'
                  : 'border-white/15 bg-white/5 text-white/75 hover:border-cyan-400/40 hover:text-cyan-200'
            )}
          >
            {updateState === 'updating' ? (
              <RefreshCw className="size-2.5 animate-spin" />
            ) : updateState === 'success' ? (
              <Check className="size-2.5 text-emerald-300" />
            ) : (
              <RefreshCw className="size-2.5" />
            )}
            <span>{updateState === 'updating' ? 'Atualizando...' : 'Atualizar'}</span>
          </button>
          <span className="hidden items-center gap-1 rounded-full border border-cyan-400/20 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] font-medium text-cyan-300 sm:flex">
            <span className="size-1.5 animate-pulse rounded-full bg-cyan-400" />
            Ativo
          </span>
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
          />
        </div>
      </div>
      {!minimized && updateMsg && (
        <div className="border-b border-white/10 bg-cyan-950/40 px-3 py-1 text-[9px] text-cyan-200">
          {updateMsg}
        </div>
      )}

      {!minimized && (
      <div className="space-y-2 p-2 text-[9px]">
        <section aria-label="Uso do sistema" className="space-y-1.5">
          <h3 className="font-semibold tracking-wide text-white/60 uppercase">Sistema</h3>
          <MetricBar label="CPU" value={metrics?.cpuPercent ?? null} />
          <MetricBar label="Memória" value={metrics?.memoryPercent ?? null} />
          {metrics && (
            <p className="pl-14 text-white/45">
              {formatMemory(metrics.memoryUsedBytes)} / {formatMemory(metrics.memoryTotalBytes)}
            </p>
          )}
        </section>

        <section aria-label="Consumo de rede" className="border-t border-white/10 pt-2">
          <h3 className="mb-1 font-semibold tracking-wide text-white/60 uppercase">
            Rede total do computador
          </h3>
          <div className="grid grid-cols-1 gap-1 text-white/75 sm:grid-cols-2 sm:gap-2">
            <p>↑ TX {formatBytesPerSecond(metrics?.sentBytesPerSecond ?? null)}</p>
            <p>↓ RX {formatBytesPerSecond(metrics?.receivedBytesPerSecond ?? null)}</p>
          </div>
        </section>

        <section aria-label="Localização" className="border-t border-white/10 pt-2">
          <h3 className="mb-1 font-semibold tracking-wide text-white/60 uppercase">Localização</h3>
          <p className={locationStatus === 'shared' ? 'text-emerald-300' : 'text-white/70'}>
            {LOCATION_LABELS[locationStatus]}
          </p>
          {locationCoordinates && (
            <div className="mt-0.5 space-y-0.5 text-white/65">
              {[
                locationCoordinates.city,
                locationCoordinates.state,
                locationCoordinates.country,
              ].filter(Boolean).length > 0 && (
                <p className="font-medium text-white/90">
                  {[
                    locationCoordinates.city,
                    locationCoordinates.state,
                    locationCoordinates.country,
                  ]
                    .filter(Boolean)
                    .join(', ')}
                </p>
              )}
              <p className="font-mono break-all text-white/45">
                {locationCoordinates.latitude.toFixed(4)},{' '}
                {locationCoordinates.longitude.toFixed(4)}
              </p>
            </div>
          )}
        </section>
      </div>
      )}
    </aside>
  );
}

function MetricBar({ label, value }: { label: string; value: number | null }) {
  const formattedValue = value === null ? '--' : `${value.toFixed(0)}%`;
  return (
    <div className="flex items-center gap-2">
      <span className="w-12 shrink-0 text-white/70">{label}</span>
      <div
        aria-label={`${label}: ${formattedValue}`}
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value ?? 0}
      >
        <div
          className={`h-full rounded-full transition-[width] ${
            value !== null && value >= 90 ? 'bg-amber-300/90' : 'bg-cyan-300/80'
          }`}
          style={{ width: `${value ?? 0}%` }}
        />
      </div>
      <span className="w-7 text-right font-mono text-white/70">{formattedValue}</span>
    </div>
  );
}
