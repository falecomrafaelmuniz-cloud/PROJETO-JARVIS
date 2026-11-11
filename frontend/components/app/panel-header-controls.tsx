'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Hand, Maximize2, Minimize2, X } from 'lucide-react';

export function usePanelMinimized(storageKey: string, defaultMinimized = false) {
  const [minimized, setMinimized] = useState(defaultMinimized);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw === 'true') setMinimized(true);
      else if (raw === 'false') setMinimized(false);
    } catch {
      // ignore storage failures
    }
  }, [storageKey]);

  const setAndPersist = useCallback(
    (next: boolean | ((prev: boolean) => boolean)) => {
      setMinimized((prev) => {
        const resolved = typeof next === 'function' ? next(prev) : next;
        try {
          window.localStorage.setItem(storageKey, String(resolved));
        } catch {
          // ignore storage failures
        }
        return resolved;
      });
    },
    [storageKey]
  );

  const toggleMinimized = useCallback(() => {
    setAndPersist((prev) => !prev);
  }, [setAndPersist]);

  return [minimized, toggleMinimized, setAndPersist] as const;
}

interface PanelHeaderControlsProps {
  minimized: boolean;
  onToggleMinimize: () => void;
  onClose?: () => void;
  dragTitle?: string;
  minimizeTitle?: string;
  expandTitle?: string;
  closeTitle?: string;
}

export function PanelHeaderControls({
  minimized,
  onToggleMinimize,
  onClose,
  dragTitle = 'Segure e arraste para mover o painel',
  minimizeTitle = 'Minimizar painel',
  expandTitle = 'Expandir painel',
  closeTitle = 'Fechar painel',
}: PanelHeaderControlsProps) {
  return (
    <div className="flex items-center gap-1">
      <span
        data-drag-handle="true"
        role="button"
        tabIndex={-1}
        aria-label={dragTitle}
        title={dragTitle}
        className="inline-flex h-6 w-6 cursor-grab items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-200/80 transition hover:border-cyan-300/45 hover:bg-cyan-400/20 hover:text-cyan-100 active:cursor-grabbing"
      >
        <Hand className="h-3 w-3" />
      </span>
      <button
        type="button"
        data-no-drag="true"
        onClick={onToggleMinimize}
        title={minimized ? expandTitle : minimizeTitle}
        aria-label={minimized ? expandTitle : minimizeTitle}
        className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-200/80 transition hover:border-cyan-300/45 hover:bg-cyan-400/20 hover:text-cyan-100"
      >
        {minimized ? <Maximize2 className="h-3 w-3" /> : <Minimize2 className="h-3 w-3" />}
      </button>
      {onClose ? (
        <button
          type="button"
          data-no-drag="true"
          onClick={onClose}
          title={closeTitle}
          aria-label={closeTitle}
          className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-200/80 transition hover:border-rose-400/50 hover:bg-rose-500/20 hover:text-rose-200"
        >
          <X className="h-3 w-3" />
        </button>
      ) : null}
    </div>
  );
}
