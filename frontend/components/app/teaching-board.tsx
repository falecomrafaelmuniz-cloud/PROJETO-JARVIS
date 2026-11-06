'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  ArrowUpRight,
  Circle,
  ImagePlus,
  Maximize2,
  Minimize2,
  Pencil,
  Square,
  Trash2,
  Type,
  Undo2,
  X,
} from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const BOARD_TOPIC = 'jarvis.board';
const BOARD_STORAGE_KEY = 'jarvis-teaching-board-v1';
const BOARD_WIDTH = 1920;
const BOARD_HEIGHT = 1080;
const CHALK_COLORS = [
  { name: 'Giz branco', value: '#f8f5e7' },
  { name: 'Ciano Jarvis', value: '#22d3ee' },
  { name: 'Verde neon', value: '#34d399' },
  { name: 'Amarelo ouro', value: '#fbbf24' },
  { name: 'Rosa choque', value: '#f472b6' },
  { name: 'Roxo holográfico', value: '#a78bfa' },
  { name: 'Laranja chama', value: '#fb923c' },
];

type BoardTool = 'pen' | 'line' | 'rectangle' | 'ellipse';
type BoardItemType = 'text' | 'path' | 'line' | 'rectangle' | 'ellipse' | 'image';

interface BoardItem {
  id: string;
  type: BoardItemType;
  x: number;
  y: number;
  width?: number;
  height?: number;
  color?: string;
  size?: number;
  text?: string;
  points?: string;
  src?: string;
  label?: string;
}

interface BoardPacket {
  type?: string;
  action?: string;
  element?: unknown;
  target?: string;
  changes?: Partial<BoardItem>;
}

function isBoardItem(value: unknown): value is BoardItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<BoardItem>;
  if (
    typeof item.id !== 'string' ||
    !['text', 'path', 'line', 'rectangle', 'ellipse', 'image'].includes(item.type ?? '') ||
    typeof item.x !== 'number' ||
    typeof item.y !== 'number' ||
    !Number.isFinite(item.x) ||
    !Number.isFinite(item.y)
  ) {
    return false;
  }
  if (item.type === 'text') return typeof item.text === 'string' && item.text.length <= 3000;
  if (item.type === 'image') {
    return (
      typeof item.src === 'string' &&
      item.src.length <= 3_000_000 &&
      (item.src.startsWith('data:image/') || item.src.startsWith('https://'))
    );
  }
  if (item.type === 'path' || item.type === 'line') {
    return typeof item.points === 'string' && item.points.length <= 20_000;
  }
  return (
    typeof item.width === 'number' &&
    typeof item.height === 'number' &&
    Number.isFinite(item.width) &&
    Number.isFinite(item.height)
  );
}

function getBoardPoint(event: React.PointerEvent<SVGSVGElement>, svg: SVGSVGElement) {
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const matrix = svg.getScreenCTM();
  if (!matrix) return { x: 0, y: 0 };
  const transformed = point.matrixTransform(matrix.inverse());
  return {
    x: Math.max(0, Math.min(BOARD_WIDTH, transformed.x)),
    y: Math.max(0, Math.min(BOARD_HEIGHT, transformed.y)),
  };
}

function textLines(text: string, maxCharacters = 44) {
  const lines: string[] = [];
  for (const paragraph of text.split('\n')) {
    if (!paragraph) {
      lines.push('');
      continue;
    }
    let remaining = paragraph;
    while (remaining.length > maxCharacters) {
      let breakAt = remaining.lastIndexOf(' ', maxCharacters);
      if (breakAt < 1) breakAt = maxCharacters;
      lines.push(remaining.slice(0, breakAt));
      remaining = remaining.slice(breakAt).trimStart();
    }
    lines.push(remaining);
  }
  return lines;
}

export function TeachingBoard() {
  const room = useRoomContext();
  const {
    style: buttonDragStyle,
    isDragging: isButtonDragging,
    isResizingEdge: isButtonResizingEdge,
    wasDragged: wasButtonDragged,
    dragHandlers: buttonDragHandlers,
  } = useDraggableFloating('jarvis-pos-board-button-v1');
  const svgRef = useRef<SVGSVGElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const drawingStart = useRef<{ x: number; y: number } | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [items, setItems] = useState<BoardItem[]>([]);

  const toggleFullscreen = async () => {
    try {
      if (!document.fullscreenElement) {
        if (document.documentElement.requestFullscreen) {
          await document.documentElement.requestFullscreen();
        }
        setIsFullscreen(true);
      } else {
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        }
        setIsFullscreen(false);
      }
    } catch {
      setIsFullscreen((prev) => !prev);
    }
  };

  const fecharLousa = async () => {
    if (typeof document !== 'undefined' && document.fullscreenElement) {
      try {
        await document.exitFullscreen();
      } catch {}
    }
    setIsFullscreen(false);
    setIsOpen(false);
  };

  useEffect(() => {
    const onFsChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  // Atalho do teclado: ESC fecha a lousa e restaura o navegador
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        fecharLousa();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);
  const [loaded, setLoaded] = useState(false);
  const [tool, setTool] = useState<BoardTool>('pen');
  const [chalkColor, setChalkColor] = useState(CHALK_COLORS[0].value);
  const [textDraft, setTextDraft] = useState('');
  const [draft, setDraft] = useState<BoardItem | null>(null);
  const [storageError, setStorageError] = useState('');

  useEffect(() => {
    try {
      const saved = localStorage.getItem(BOARD_STORAGE_KEY);
      if (saved) {
        const parsed: unknown = JSON.parse(saved);
        if (Array.isArray(parsed)) setItems(parsed.filter(isBoardItem));
      }
    } catch {
      setStorageError('Não foi possível recuperar a lousa salva neste navegador.');
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    try {
      localStorage.setItem(BOARD_STORAGE_KEY, JSON.stringify(items));
      setStorageError('');
    } catch {
      setStorageError('A lousa está cheia para salvar no navegador. Remova uma imagem grande.');
    }
  }, [items, loaded]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== BOARD_TOPIC) return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload)) as BoardPacket;
        if (packet.type !== 'jarvis_board') return;
        if (packet.action === 'close') {
          setIsOpen(false);
          return;
        }
        setIsOpen(true);
        if (packet.action === 'open') {
          return;
        }
        if (packet.action === 'clear') {
          setItems([]);
        } else if (packet.action === 'undo') {
          setItems((current) => current.slice(0, -1));
        } else if (packet.action === 'remove' && typeof packet.target === 'string') {
          const query = packet.target.trim().toLowerCase();
          setItems((current) => {
            if (!query || query === 'ultimo' || query === 'último') {
              return current.slice(0, -1);
            }
            const idx = [...current]
              .reverse()
              .findIndex(
                (item) =>
                  item.id.toLowerCase() === query ||
                  (item.text ?? '').toLowerCase().includes(query) ||
                  (item.label ?? '').toLowerCase().includes(query)
              );
            if (idx === -1) return current;
            const realIndex = current.length - 1 - idx;
            return current.filter((_, index) => index !== realIndex);
          });
        } else if (
          packet.action === 'update' &&
          typeof packet.target === 'string' &&
          packet.changes
        ) {
          const query = packet.target.trim().toLowerCase();
          const changes = packet.changes;
          setItems((current) => {
            const idx = [...current]
              .reverse()
              .findIndex(
                (item) =>
                  query === 'ultimo' ||
                  query === 'último' ||
                  item.id.toLowerCase() === query ||
                  (item.text ?? '').toLowerCase().includes(query) ||
                  (item.label ?? '').toLowerCase().includes(query)
              );
            if (idx === -1) return current;
            const realIndex = current.length - 1 - idx;
            return current.map((item, index) => {
              if (index !== realIndex) return item;
              const updated: BoardItem = { ...item };
              if (typeof changes.text === 'string') {
                if (item.type === 'text') updated.text = changes.text;
                else updated.label = changes.text;
              }
              if (typeof changes.label === 'string') updated.label = changes.label;
              if (typeof changes.color === 'string') updated.color = changes.color;
              if (typeof changes.x === 'number' && Number.isFinite(changes.x))
                updated.x = changes.x;
              if (typeof changes.y === 'number' && Number.isFinite(changes.y))
                updated.y = changes.y;
              if (typeof changes.size === 'number' && Number.isFinite(changes.size))
                updated.size = changes.size;
              return updated;
            });
          });
        } else if (packet.action === 'add' && isBoardItem(packet.element)) {
          setItems((current) => [...current, packet.element as BoardItem]);
        }
      } catch {
        setStorageError('Não foi possível interpretar uma atualização da lousa.');
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room]);

  const finishDrawing = useCallback(() => {
    if (draft) {
      if (draft.type === 'path' || draft.type === 'line') {
        const pointCount = (draft.points?.match(/,/g) ?? []).length;
        if (pointCount >= 2) setItems((current) => [...current, draft]);
      } else if ((draft.width ?? 0) > 2 || (draft.height ?? 0) > 2) {
        setItems((current) => [...current, draft]);
      }
    }
    setDraft(null);
    drawingStart.current = null;
  }, [draft]);

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const point = getBoardPoint(event, svgRef.current);
    drawingStart.current = point;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === 'pen') {
      setDraft({
        id: `draw-${Date.now()}`,
        type: 'path',
        ...point,
        color: chalkColor,
        size: 5,
        points: `${point.x},${point.y}`,
      });
      return;
    }
    if (tool === 'line') {
      setDraft({
        id: `line-${Date.now()}`,
        type: 'line',
        ...point,
        color: chalkColor,
        size: 4,
        points: `${point.x},${point.y}`,
      });
      return;
    }
    setDraft({
      id: `shape-${Date.now()}`,
      type: tool,
      ...point,
      width: 0,
      height: 0,
      color: chalkColor,
      size: 4,
    });
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!draft || !svgRef.current) return;
    const point = getBoardPoint(event, svgRef.current);
    const start = drawingStart.current;
    if (!start) return;
    setDraft((current) => {
      if (!current) return null;
      if (current.type === 'path' || current.type === 'line') {
        return { ...current, points: `${current.points} ${point.x},${point.y}` };
      }
      return {
        ...current,
        x: Math.min(start.x, point.x),
        y: Math.min(start.y, point.y),
        width: Math.abs(point.x - start.x),
        height: Math.abs(point.y - start.y),
      };
    });
  };

  const addText = () => {
    const text = textDraft.trim();
    if (!text) return;
    setItems((current) => [
      ...current,
      {
        id: `text-${Date.now()}`,
        type: 'text',
        x: 80,
        y: 95 + current.filter((item) => item.type === 'text').length * 70,
        text,
        color: chalkColor,
        size: 30,
      },
    ]);
    setTextDraft('');
  };

  const addImage = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setStorageError('Escolha um arquivo de imagem válido.');
      return;
    }
    try {
      const bitmap = await createImageBitmap(file);
      const scale = Math.min(1, 1400 / bitmap.width, 1000 / bitmap.height);
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas indisponível');
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      const src = canvas.toDataURL('image/jpeg', 0.78);
      if (src.length > 2_500_000) {
        setStorageError('A imagem é grande demais. Escolha uma imagem menor.');
        return;
      }
      setItems((current) => [
        ...current,
        {
          id: `image-${Date.now()}`,
          type: 'image',
          x: 420,
          y: 180,
          width: Math.min(360, canvas.width),
          height: Math.min(260, canvas.height),
          src,
          label: file.name.slice(0, 120),
        },
      ]);
    } catch {
      setStorageError('Não foi possível carregar essa imagem.');
    }
  };

  return (
    <>
      <button
        type="button"
        style={buttonDragStyle}
        {...buttonDragHandlers}
        onClick={() => {
          if (wasButtonDragged()) return;
          setIsOpen(true);
        }}
        title="Clique para abrir · Arraste pelo centro para mover · Arraste pela borda para mudar o tamanho"
        className={cn(
          'pointer-events-auto absolute top-3 left-[calc(min(17rem,50vw-1.125rem)+1.25rem)] z-30 inline-flex touch-none items-center gap-2 px-3 py-2 text-xs font-semibold tracking-wider text-cyan-200 uppercase transition-all duration-200 select-none',
          'rounded-xl border border-cyan-400/30 bg-black/80 backdrop-blur-2xl',
          'shadow-[0_0_15px_rgba(6,182,212,0.18)] shadow-[0_4px_20px_rgba(0,0,0,0.6)] ring-1 shadow-cyan-950/40 ring-cyan-500/20 hover:border-cyan-300/60 hover:shadow-[0_0_20px_rgba(6,182,212,0.35)]',
          isButtonDragging || isButtonResizingEdge
            ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
            : '',
          isButtonDragging ? 'cursor-grabbing' : 'cursor-grab'
        )}
        aria-label="Abrir lousa de ensino"
      >
        <Pencil className="size-3.5 text-cyan-300 drop-shadow-[0_0_6px_rgba(34,211,238,0.8)]" />
        Lousa
      </button>

      {isOpen && (
        <div
          className={cn(
            'fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md transition-all duration-300',
            isFullscreen ? 'p-0' : 'p-2 sm:p-5'
          )}
        >
          <section
            aria-label="Lousa de ensino do Jarvis"
            className={cn(
              'relative flex flex-col overflow-hidden text-white backdrop-blur-2xl transition-all duration-300',
              isFullscreen
                ? 'h-full w-full rounded-none border-0 bg-black/95'
                : 'h-full max-h-[960px] w-full max-w-[1500px] rounded-2xl border border-cyan-400/30 bg-black/90 shadow-[0_0_40px_rgba(6,182,212,0.25)] shadow-cyan-950/50'
            )}
          >
            {/* Visual de linhas holográficas nos cantos superiores */}
            <div className="pointer-events-none absolute inset-x-8 top-0 z-20 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
            <div className="pointer-events-none absolute -top-1 left-1/2 z-20 h-2 w-32 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 bg-slate-900/90 px-4 py-3 text-white">
              <div className="flex items-center gap-3">
                <div className="flex size-9 items-center justify-center rounded-xl bg-cyan-400/10 text-cyan-300">
                  <Pencil className="size-4" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold">Lousa do Jarvis</h2>
                  <p className="text-xs text-slate-400">
                    Pressione <kbd className="rounded border border-cyan-400/40 bg-cyan-950/60 px-1 text-[10px] text-cyan-300">ESC</kbd> para fechar ou alterne para tela cheia.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={toggleFullscreen}
                  className="rounded-lg border border-cyan-400/30 bg-cyan-950/40 p-2 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.25)] transition hover:border-cyan-300 hover:bg-cyan-900/50 hover:text-white active:scale-95"
                  title={isFullscreen ? 'Sair da tela cheia do monitor (ESC)' : 'Expandir para TELA CHEIA Widescreen (100% do Monitor)'}
                  aria-label="Alternar tela cheia"
                >
                  {isFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
                </button>
                <button
                  type="button"
                  onClick={fecharLousa}
                  className="rounded-lg p-2 text-slate-300 transition hover:bg-white/10 hover:text-white"
                  title="Fechar lousa e voltar ao Jarvis (ESC)"
                  aria-label="Fechar lousa"
                >
                  <X className="size-5" />
                </button>
              </div>
            </header>

            <div className="flex flex-wrap items-center gap-2.5 border-b border-cyan-500/20 bg-slate-950/90 px-4 py-2.5 backdrop-blur-xl">
              <div className="flex items-center gap-1 rounded-xl border border-cyan-500/20 bg-slate-900/80 p-1 shadow-inner">
                {[
                  { value: 'pen', label: 'Desenhar', icon: Pencil },
                  { value: 'line', label: 'Linha', icon: ArrowUpRight },
                  { value: 'rectangle', label: 'Retângulo', icon: Square },
                  { value: 'ellipse', label: 'Elipse', icon: Circle },
                ].map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    type="button"
                    title={label}
                    aria-label={label}
                    aria-pressed={tool === value}
                    onClick={() => setTool(value as BoardTool)}
                    className={cn(
                      'rounded-lg p-2 transition-all duration-200',
                      tool === value
                        ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/50 shadow-[0_0_12px_rgba(6,182,212,0.35)]'
                        : 'text-slate-400 hover:text-cyan-200 hover:bg-slate-800/60'
                    )}
                  >
                    <Icon className="size-4" />
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-1.5 rounded-xl border border-cyan-500/20 bg-slate-900/80 px-2.5 py-1.5 shadow-inner">
                {CHALK_COLORS.map((chalk) => (
                  <button
                    key={chalk.value}
                    type="button"
                    title={chalk.name}
                    aria-label={chalk.name}
                    aria-pressed={chalkColor === chalk.value}
                    onClick={() => setChalkColor(chalk.value)}
                    className={cn(
                      'size-5 rounded-full border border-white/20 transition-all duration-200',
                      chalkColor === chalk.value
                        ? 'ring-2 ring-cyan-400 ring-offset-2 ring-offset-slate-950 scale-110 shadow-[0_0_8px_rgba(34,211,238,0.7)]'
                        : 'hover:scale-115 opacity-85 hover:opacity-100'
                    )}
                    style={{ backgroundColor: chalk.value }}
                  />
                ))}
              </div>

              <div className="flex min-w-[220px] flex-1 items-center gap-2 sm:max-w-xl">
                <Type className="size-4 shrink-0 text-cyan-400/70" />
                <input
                  value={textDraft}
                  onChange={(event) => setTextDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      addText();
                    }
                  }}
                  placeholder="Escreva uma anotação na lousa..."
                  aria-label="Texto para adicionar à lousa"
                  className="h-9 min-w-0 flex-1 rounded-xl border border-cyan-500/30 bg-slate-900/90 px-3 text-sm text-cyan-100 outline-none placeholder:text-slate-500 transition-all focus:border-cyan-400 focus:shadow-[0_0_12px_rgba(6,182,212,0.25)] focus:ring-1 focus:ring-cyan-400/40"
                />
                <button
                  type="button"
                  onClick={addText}
                  className="rounded-xl border border-cyan-400/40 bg-cyan-500/20 px-3.5 py-2 text-xs font-semibold text-cyan-200 transition-all hover:bg-cyan-500/30 hover:shadow-[0_0_12px_rgba(6,182,212,0.4)] active:scale-95"
                >
                  Adicionar
                </button>
              </div>

              <input
                ref={uploadRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) void addImage(file);
                  event.currentTarget.value = '';
                }}
              />
              <button
                type="button"
                onClick={() => uploadRef.current?.click()}
                className="inline-flex items-center gap-2 rounded-xl border border-cyan-500/20 bg-slate-900/80 px-3 py-2 text-xs font-medium text-cyan-200/90 transition-all hover:border-cyan-400/40 hover:bg-slate-800/80 hover:text-cyan-100"
              >
                <ImagePlus className="size-4 text-cyan-400" />
                Imagem
              </button>
              <button
                type="button"
                onClick={() => setItems((current) => current.slice(0, -1))}
                disabled={items.length === 0}
                className="rounded-xl border border-cyan-500/20 bg-slate-900/80 p-2 text-cyan-200/80 transition-all hover:border-cyan-400/40 hover:bg-slate-800/80 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-30"
                title="Desfazer última adição"
                aria-label="Desfazer última adição"
              >
                <Undo2 className="size-4" />
              </button>
              <button
                type="button"
                onClick={() => {
                  if (items.length > 0 && window.confirm('Limpar todo o conteúdo da lousa?')) {
                    setItems([]);
                  }
                }}
                disabled={items.length === 0}
                className="rounded-xl border border-rose-500/30 bg-rose-950/30 p-2 text-rose-300 transition-all hover:border-rose-400/50 hover:bg-rose-900/40 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-30"
                title="Limpar lousa"
                aria-label="Limpar lousa"
              >
                <Trash2 className="size-4" />
              </button>
            </div>

            <div className={cn(
              'relative min-h-0 flex-1 overflow-hidden bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 transition-all duration-300',
              isFullscreen ? 'p-0' : 'p-3 sm:p-5'
            )}>
              <div className={cn(
                'h-full bg-slate-950/90 transition-all duration-300 flex items-center justify-center',
                isFullscreen ? 'rounded-none border-0 p-0 shadow-none' : 'rounded-2xl border-2 border-cyan-500/30 p-1.5 shadow-[0_0_35px_rgba(6,182,212,0.15),inset_0_0_20px_rgba(0,0,0,0.8)] sm:p-3'
              )}>
                <svg
                  ref={svgRef}
                  viewBox={`0 0 ${BOARD_WIDTH} ${BOARD_HEIGHT}`}
                  role="img"
                  aria-label="Área de desenho da lousa"
                  className={cn(
                    'h-full w-full touch-none rounded-xl bg-[#081512] shadow-[inset_0_4px_30px_rgba(0,0,0,0.9)]',
                    tool === 'pen' ? 'cursor-crosshair' : 'cursor-cell'
                  )}
                  onPointerDown={handlePointerDown}
                  onPointerMove={handlePointerMove}
                  onPointerUp={finishDrawing}
                  onPointerCancel={finishDrawing}
                >
                  <defs>
                    <linearGradient id="chalkboard-surface" x1="0" y1="0" x2="0.9" y2="1">
                      <stop offset="0%" stopColor="#0a1a16" />
                      <stop offset="50%" stopColor="#081512" />
                      <stop offset="100%" stopColor="#05100e" />
                    </linearGradient>
                    <filter id="chalk-soften" x="-5%" y="-5%" width="110%" height="110%">
                      <feGaussianBlur in="SourceGraphic" stdDeviation="0.45" result="soft" />
                      <feMerge>
                        <feMergeNode in="soft" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                    {/* Filtros 3D Avançados para Imagens e Cards Flutuantes */}
                    <filter id="card-3d-depth" x="-30%" y="-30%" width="170%" height="170%">
                      <feDropShadow dx="0" dy="24" stdDeviation="28" floodColor="#000000" floodOpacity="0.95" />
                      <feDropShadow dx="0" dy="8" stdDeviation="16" floodColor="#06b6d4" floodOpacity="0.45" />
                    </filter>
                    <linearGradient id="cyber-frame-grad" x1="0" y1="0" x2="1" y2="1">
                      <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.9" />
                      <stop offset="50%" stopColor="#0ea5e9" stopOpacity="0.5" />
                      <stop offset="100%" stopColor="#a855f7" stopOpacity="0.85" />
                    </linearGradient>
                    <linearGradient id="card-glass-gloss" x1="0" y1="0" x2="1" y2="1">
                      <stop offset="0%" stopColor="#ffffff" stopOpacity="0.22" />
                      <stop offset="40%" stopColor="#ffffff" stopOpacity="0.04" />
                      <stop offset="100%" stopColor="#000000" stopOpacity="0.2" />
                    </linearGradient>
                    <linearGradient id="box-card-bg" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#08231c" stopOpacity="0.85" />
                      <stop offset="100%" stopColor="#03110e" stopOpacity="0.95" />
                    </linearGradient>
                    <pattern
                      id="board-grain"
                      width="100"
                      height="100"
                      patternUnits="userSpaceOnUse"
                    >
                      <circle cx="50" cy="50" r="0.8" fill="#22d3ee" fillOpacity=".04" />
                      <path
                        d="M0 50h100M50 0v100"
                        stroke="#22d3ee"
                        strokeOpacity=".02"
                        strokeWidth="1"
                      />
                    </pattern>
                    <marker
                      id="board-arrowhead"
                      markerWidth="10"
                      markerHeight="10"
                      refX="8"
                      refY="5"
                      orient="auto"
                      markerUnits="strokeWidth"
                    >
                      <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
                    </marker>
                  </defs>
                  <rect width={BOARD_WIDTH} height={BOARD_HEIGHT} fill="url(#chalkboard-surface)" />
                  <rect width={BOARD_WIDTH} height={BOARD_HEIGHT} fill="url(#board-grain)" />
                  {items.map((item) => {
                    if (item.type === 'text') {
                      const fontSize = item.size ?? 26;
                      const maxChars = fontSize >= 30 ? 38 : fontSize >= 22 ? 48 : 58;
                      const lineStep = Math.round(fontSize * 1.28);
                      const lines = textLines(item.text ?? '', maxChars);
                      return (
                        <text
                          key={item.id}
                          x={item.x}
                          y={item.y}
                          fill={item.color ?? '#f8f5e7'}
                          fontSize={fontSize}
                          fontWeight="600"
                          fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                          filter="url(#chalk-soften)"
                        >
                          {lines.map((line, index) => (
                            <tspan
                              key={`${item.id}-${index}`}
                              x={item.x}
                              dy={index === 0 ? 0 : lineStep}
                            >
                              {line}
                            </tspan>
                          ))}
                        </text>
                      );
                    }
                    if (item.type === 'path' || item.type === 'line') {
                      return (
                        <g key={item.id}>
                          <polyline
                            points={item.points}
                            fill="none"
                            stroke={item.color ?? '#f8f5e7'}
                            strokeWidth={item.size ?? 4}
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            filter="url(#chalk-soften)"
                            markerEnd={item.type === 'line' ? 'url(#board-arrowhead)' : undefined}
                          />
                          {item.label && (
                            <text
                              x={item.x + (item.width ?? 0) / 2}
                              y={item.y + (item.height ?? 0) / 2 - 12}
                              fill={item.color ?? '#f8f5e7'}
                              fontSize="22"
                              fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                              filter="url(#chalk-soften)"
                            >
                              {item.label}
                            </text>
                          )}
                        </g>
                      );
                    }
                    if (item.type === 'rectangle') {
                      return (
                        <g key={item.id} className="transition-all duration-200">
                          {/* Fundo de vidro fosco 3D para caixas de esquema */}
                          <rect
                            x={item.x}
                            y={item.y}
                            width={item.width}
                            height={item.height}
                            fill="url(#box-card-bg)"
                            stroke={item.color ?? '#22d3ee'}
                            strokeWidth={item.size ?? 3}
                            rx="16"
                            filter="url(#card-3d-depth)"
                          />
                          {item.label && (
                            <text
                              x={item.x + 12}
                              y={item.y + Math.min((item.height ?? 0) - 8, 34)}
                              fill={item.color ?? '#f8f5e7'}
                              fontSize="22"
                              fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                              filter="url(#chalk-soften)"
                            >
                              {item.label}
                            </text>
                          )}
                        </g>
                      );
                    }
                    if (item.type === 'ellipse') {
                      return (
                        <g key={item.id}>
                          <ellipse
                            cx={item.x + (item.width ?? 0) / 2}
                            cy={item.y + (item.height ?? 0) / 2}
                            rx={(item.width ?? 0) / 2}
                            ry={(item.height ?? 0) / 2}
                            fill="none"
                            stroke={item.color ?? '#f8f5e7'}
                            strokeWidth={item.size ?? 4}
                            filter="url(#chalk-soften)"
                          />
                          {item.label && (
                            <text
                              x={item.x + (item.width ?? 0) / 2}
                              y={item.y + (item.height ?? 0) / 2}
                              textAnchor="middle"
                              fill={item.color ?? '#f8f5e7'}
                              fontSize="22"
                              fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                              filter="url(#chalk-soften)"
                            >
                              {item.label}
                            </text>
                          )}
                        </g>
                      );
                    }
                    const imgW = item.width ?? 380;
                    const imgH = item.height ?? 260;
                    return (
                      <g key={item.id} className="cursor-pointer transition-transform duration-300 hover:scale-105">
                        {/* Placa Traseira 3D que salta para fora das letras com sombra profunda */}
                        <rect
                          x={item.x - 6}
                          y={item.y - 6}
                          width={imgW + 12}
                          height={imgH + 12}
                          rx="22"
                          fill="#03110e"
                          stroke="url(#cyber-frame-grad)"
                          strokeWidth="3.5"
                          filter="url(#card-3d-depth)"
                        />
                        <clipPath id={`clip-${item.id}`}>
                          <rect
                            x={item.x}
                            y={item.y}
                            width={imgW}
                            height={imgH}
                            rx="16"
                          />
                        </clipPath>
                        <image
                          href={item.src}
                          x={item.x}
                          y={item.y}
                          width={imgW}
                          height={imgH}
                          preserveAspectRatio="xMidYMid slice"
                          clipPath={`url(#clip-${item.id})`}
                        />
                        {/* Brilho reflexivo de vidro 3D sobre a imagem */}
                        <rect
                          x={item.x}
                          y={item.y}
                          width={imgW}
                          height={imgH / 2}
                          rx="16"
                          fill="url(#card-glass-gloss)"
                          pointerEvents="none"
                        />
                        {/* Borda interna holográfica */}
                        <rect
                          x={item.x}
                          y={item.y}
                          width={imgW}
                          height={imgH}
                          rx="16"
                          fill="none"
                          stroke="#22d3ee"
                          strokeWidth="1.5"
                          strokeOpacity="0.5"
                          pointerEvents="none"
                        />
                        {item.label && (
                          <g>
                            <rect
                              x={item.x + 12}
                              y={item.y + imgH - 38}
                              width={imgW - 24}
                              height="30"
                              rx="8"
                              fill="rgba(3, 17, 14, 0.92)"
                              stroke="#22d3ee"
                              strokeWidth="1"
                              strokeOpacity="0.4"
                            />
                            <text
                              x={item.x + 22}
                              y={item.y + imgH - 18}
                              fill="#38bdf8"
                              fontSize="13"
                              fontWeight="700"
                              letterSpacing="0.06em"
                              fontFamily="system-ui, -apple-system, sans-serif"
                            >
                              {item.label}
                            </text>
                          </g>
                        )}
                      </g>
                    );
                  })}
                  {draft &&
                    (draft.type === 'path' || draft.type === 'line' ? (
                      <polyline
                        points={draft.points}
                        fill="none"
                        stroke={draft.color}
                        strokeWidth={draft.size}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        filter="url(#chalk-soften)"
                        markerEnd={draft.type === 'line' ? 'url(#board-arrowhead)' : undefined}
                      />
                    ) : draft.type === 'rectangle' ? (
                      <rect
                        x={draft.x}
                        y={draft.y}
                        width={draft.width}
                        height={draft.height}
                        fill="none"
                        stroke={draft.color}
                        strokeWidth={draft.size}
                        strokeDasharray="8 5"
                        filter="url(#chalk-soften)"
                      />
                    ) : (
                      <ellipse
                        cx={draft.x + (draft.width ?? 0) / 2}
                        cy={draft.y + (draft.height ?? 0) / 2}
                        rx={(draft.width ?? 0) / 2}
                        ry={(draft.height ?? 0) / 2}
                        fill="none"
                        stroke={draft.color}
                        strokeWidth={draft.size}
                        strokeDasharray="8 5"
                        filter="url(#chalk-soften)"
                      />
                    ))}
                  {items.length === 0 && !draft && (
                    <text
                      x={BOARD_WIDTH / 2}
                      y={BOARD_HEIGHT / 2}
                      textAnchor="middle"
                      fill="#d0dbc8"
                      fontSize="24"
                    >
                      Peça uma explicação ao Jarvis ou comece a desenhar
                    </text>
                  )}
                </svg>
              </div>
              <p className="pointer-events-none absolute right-8 bottom-7 rounded-full border border-cyan-500/20 bg-slate-950/80 px-3.5 py-1 text-[11px] font-medium tracking-wide text-cyan-300/80 shadow-md backdrop-blur-md">
                Moldura de Titânio · HUD Integrado · Giz Sincronizado
              </p>
              {storageError && (
                <p
                  role="status"
                  className="absolute bottom-6 left-7 max-w-[70%] rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800 shadow"
                >
                  {storageError}
                </p>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
