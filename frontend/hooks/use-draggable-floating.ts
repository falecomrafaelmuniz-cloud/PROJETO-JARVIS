'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface TransformState {
  x: number;
  y: number;
  scale: number;
}

type EdgeHover = 'none' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

const MIN_SCALE = 0.35;
const MAX_SCALE = 2.25;
const PIXELS_PER_UNIT_SCALE = 140;

function clampScale(value: number) {
  return Math.round(Math.min(MAX_SCALE, Math.max(MIN_SCALE, value)) * 1000) / 1000;
}

function detectEdge(clientX: number, clientY: number, rect: DOMRect, circular = false): EdgeHover {
  if (circular) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const radius = Math.min(rect.width, rect.height) / 2;
    const dx = clientX - cx;
    const dy = clientY - cy;
    const dist = Math.hypot(dx, dy);
    const innerRing = Math.max(radius - 28, radius * 0.72);
    if (dist >= innerRing && dist <= radius + 18) {
      if (dx >= 0 && dy >= 0) return 'se';
      if (dx < 0 && dy < 0) return 'nw';
      if (dx >= 0 && dy < 0) return 'ne';
      return 'sw';
    }
    return 'none';
  }

  const thresholdX = Math.min(13, Math.max(6, rect.width * 0.16));
  const thresholdY = Math.min(13, Math.max(6, rect.height * 0.18));

  const nearLeft = clientX - rect.left <= thresholdX;
  const nearRight = rect.right - clientX <= thresholdX;
  const nearTop = clientY - rect.top <= thresholdY;
  const nearBottom = rect.bottom - clientY <= thresholdY;

  if (nearTop && nearLeft) return 'nw';
  if (nearTop && nearRight) return 'ne';
  if (nearBottom && nearLeft) return 'sw';
  if (nearBottom && nearRight) return 'se';
  if (nearLeft) return 'w';
  if (nearRight) return 'e';
  if (nearTop) return 'n';
  if (nearBottom) return 's';
  return 'none';
}

function getOutwardUnitVector(
  edge: EdgeHover,
  clientX: number,
  clientY: number,
  centerX: number,
  centerY: number,
  circular: boolean
): { ux: number; uy: number } {
  if (circular) {
    const dx = clientX - centerX;
    const dy = clientY - centerY;
    const len = Math.hypot(dx, dy) || 1;
    return { ux: dx / len, uy: dy / len };
  }

  const invSqrt2 = Math.SQRT1_2;
  switch (edge) {
    case 'e':
      return { ux: 1, uy: 0 };
    case 'w':
      return { ux: -1, uy: 0 };
    case 's':
      return { ux: 0, uy: 1 };
    case 'n':
      return { ux: 0, uy: -1 };
    case 'se':
      return { ux: invSqrt2, uy: invSqrt2 };
    case 'sw':
      return { ux: -invSqrt2, uy: invSqrt2 };
    case 'ne':
      return { ux: invSqrt2, uy: -invSqrt2 };
    case 'nw':
      return { ux: -invSqrt2, uy: -invSqrt2 };
    default:
      return { ux: 1, uy: 0 };
  }
}

function getCursorForEdge(edge: EdgeHover, isActive: boolean): string | undefined {
  switch (edge) {
    case 'nw':
    case 'se':
      return 'nwse-resize';
    case 'ne':
    case 'sw':
      return 'nesw-resize';
    case 'e':
    case 'w':
      return 'ew-resize';
    case 'n':
    case 's':
      return 'ns-resize';
    default:
      return isActive ? 'grabbing' : undefined;
  }
}

export function useDraggableFloating(storageKey: string, options?: { circular?: boolean }) {
  const isCircular = Boolean(options?.circular);
  const [state, setState] = useState<TransformState>({ x: 0, y: 0, scale: 1 });
  const [isDragging, setIsDragging] = useState(false);
  const [edgeHover, setEdgeHover] = useState<EdgeHover>('none');

  const interactionRef = useRef<{
    mode: 'move' | 'resize';
    edge: EdgeHover;
    startX: number;
    startY: number;
    initialX: number;
    initialY: number;
    initialScale: number;
    outwardX: number;
    outwardY: number;
    moved: boolean;
  } | null>(null);

  const persist = useCallback(
    (next: TransformState) => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // ignore storage errors
      }
    },
    [storageKey]
  );

  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<TransformState>;
        const x = typeof parsed.x === 'number' && Number.isFinite(parsed.x) ? parsed.x : 0;
        const y = typeof parsed.y === 'number' && Number.isFinite(parsed.y) ? parsed.y : 0;
        const scale =
          typeof parsed.scale === 'number' && Number.isFinite(parsed.scale)
            ? clampScale(parsed.scale)
            : 1;
        setState({ x, y, scale });
      }
    } catch {
      // ignore storage errors
    }
  }, [storageKey]);

  useEffect(() => {
    const handleWindowPointerMove = (event: PointerEvent) => {
      const current = interactionRef.current;
      if (!current) return;

      const dx = event.clientX - current.startX;
      const dy = event.clientY - current.startY;
      const threshold = current.mode === 'resize' ? 2 : 5;

      if (!current.moved) {
        if (Math.abs(dx) <= threshold && Math.abs(dy) <= threshold) {
          return;
        }
        current.moved = true;
        setIsDragging(true);
      }

      if (current.mode === 'resize') {
        const outwardPixels = dx * current.outwardX + dy * current.outwardY;
        const nextScale = clampScale(current.initialScale + outwardPixels / PIXELS_PER_UNIT_SCALE);
        setState((prev) => ({
          ...prev,
          scale: nextScale,
        }));
        return;
      }

      setState((prev) => ({
        ...prev,
        x: current.initialX + dx,
        y: current.initialY + dy,
      }));
    };

    const handleWindowPointerUp = () => {
      const current = interactionRef.current;
      if (!current) return;

      if (current.moved) {
        setState((prev) => {
          persist(prev);
          return prev;
        });
        setIsDragging(false);
        setEdgeHover('none');
        setTimeout(() => {
          interactionRef.current = null;
        }, 0);
      } else {
        interactionRef.current = null;
      }
    };

    const handleResetLayout = () => {
      const resetState: TransformState = { x: 0, y: 0, scale: 1 };
      setState(resetState);
      try {
        localStorage.removeItem(storageKey);
      } catch {
        // ignore storage errors
      }
    };

    window.addEventListener('pointermove', handleWindowPointerMove);
    window.addEventListener('pointerup', handleWindowPointerUp);
    window.addEventListener('pointercancel', handleWindowPointerUp);
    window.addEventListener('jarvis:hud-reset-layout', handleResetLayout);

    return () => {
      window.removeEventListener('pointermove', handleWindowPointerMove);
      window.removeEventListener('pointerup', handleWindowPointerUp);
      window.removeEventListener('pointercancel', handleWindowPointerUp);
      window.removeEventListener('jarvis:hud-reset-layout', handleResetLayout);
    };
  }, [persist, storageKey]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const edge = detectEdge(event.clientX, event.clientY, rect, isCircular);

      if (edge === 'none') {
        const target = event.target as HTMLElement | null;
        const isHandle = Boolean(target?.closest('[data-drag-handle="true"]'));
        if (
          !isHandle &&
          target?.closest('button, a, input, textarea, select, [data-no-drag="true"], [data-scrollable="true"]')
        ) {
          return;
        }
      }

      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const { ux, uy } = getOutwardUnitVector(
        edge,
        event.clientX,
        event.clientY,
        centerX,
        centerY,
        isCircular
      );

      interactionRef.current = {
        mode: edge !== 'none' ? 'resize' : 'move',
        edge,
        startX: event.clientX,
        startY: event.clientY,
        initialX: state.x,
        initialY: state.y,
        initialScale: state.scale,
        outwardX: ux,
        outwardY: uy,
        moved: false,
      };
    },
    [isCircular, state.scale, state.x, state.y]
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (interactionRef.current) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const detected = detectEdge(event.clientX, event.clientY, rect, isCircular);
      setEdgeHover((prev) => (prev === detected ? prev : detected));
    },
    [isCircular]
  );

  const onPointerLeave = useCallback(() => {
    if (!interactionRef.current) {
      setEdgeHover('none');
    }
  }, []);

  const wasDragged = useCallback(() => {
    return Boolean(interactionRef.current?.moved);
  }, []);

  const resetTransform = useCallback(() => {
    setState({ x: 0, y: 0, scale: 1 });
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // ignore storage errors
    }
  }, [storageKey]);

  const cursorStyle = getCursorForEdge(edgeHover, isDragging);

  return {
    style: {
      transform: `translate3d(${state.x}px, ${state.y}px, 0) scale(${state.scale})`,
      transformOrigin: 'center center',
      ...(cursorStyle ? { cursor: cursorStyle } : {}),
    },
    scale: state.scale,
    isDragging,
    isResizingEdge: edgeHover !== 'none',
    wasDragged,
    resetTransform,
    dragHandlers: {
      onPointerDown,
      onPointerMove,
      onPointerLeave,
    },
  };
}
