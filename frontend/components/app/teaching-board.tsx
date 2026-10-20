'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  ArrowUpRight,
  Circle,
  ImagePlus,
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
const BOARD_WIDTH = 1200;
const BOARD_HEIGHT = 760;
const CHALK_COLORS = [
  { name: 'Giz branco', value: '#f8f5e7' },
  { name: 'Giz amarelo', value: '#f5d76e' },
  { name: 'Giz azul', value: '#8ce5f2' },
  { name: 'Giz rosa', value: '#ff9fba' },
  { name: 'Giz laranja', value: '#ffb36b' },
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
  const [items, setItems] = useState<BoardItem[]>([]);
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-2 backdrop-blur-md sm:p-5">
          <section
            aria-label="Lousa de ensino do Jarvis"
            className="relative flex h-full max-h-[960px] w-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-cyan-400/30 bg-black/90 text-white shadow-[0_0_40px_rgba(6,182,212,0.25)] shadow-cyan-950/50 backdrop-blur-2xl"
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
                    Desenhe, escreva ou peça ao Jarvis para explicar visualmente.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded-lg p-2 text-slate-300 transition hover:bg-white/10 hover:text-white"
                aria-label="Fechar lousa"
              >
                <X className="size-5" />
              </button>
            </header>

            <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-3 py-2">
              <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1">
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
                      'rounded-md p-2 transition',
                      tool === value
                        ? 'bg-cyan-800 text-white shadow-sm'
                        : 'text-slate-600 hover:bg-white'
                    )}
                  >
                    <Icon className="size-4" />
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1.5">
                {CHALK_COLORS.map((chalk) => (
                  <button
                    key={chalk.value}
                    type="button"
                    title={chalk.name}
                    aria-label={chalk.name}
                    aria-pressed={chalkColor === chalk.value}
                    onClick={() => setChalkColor(chalk.value)}
                    className={cn(
                      'size-5 rounded-full border border-slate-400/70 transition',
                      chalkColor === chalk.value
                        ? 'ring-2 ring-cyan-800 ring-offset-2'
                        : 'hover:scale-110'
                    )}
                    style={{ backgroundColor: chalk.value }}
                  />
                ))}
              </div>

              <div className="flex min-w-[220px] flex-1 items-center gap-2 sm:max-w-xl">
                <Type className="size-4 shrink-0 text-slate-500" />
                <input
                  value={textDraft}
                  onChange={(event) => setTextDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      addText();
                    }
                  }}
                  placeholder="Escreva uma anotação..."
                  aria-label="Texto para adicionar à lousa"
                  className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-cyan-700 focus:ring-2 focus:ring-cyan-700/15"
                />
                <button
                  type="button"
                  onClick={addText}
                  className="rounded-lg bg-cyan-800 px-3 py-2 text-xs font-semibold text-white transition hover:bg-cyan-700"
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
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
              >
                <ImagePlus className="size-4" />
                Imagem
              </button>
              <button
                type="button"
                onClick={() => setItems((current) => current.slice(0, -1))}
                disabled={items.length === 0}
                className="rounded-lg border border-slate-200 p-2 text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
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
                className="rounded-lg border border-rose-200 p-2 text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40"
                title="Limpar lousa"
                aria-label="Limpar lousa"
              >
                <Trash2 className="size-4" />
              </button>
            </div>

            <div className="relative min-h-0 flex-1 overflow-hidden bg-[linear-gradient(135deg,#75421f,#c88b4a_12%,#8c5529_18%,#d6a15d_50%,#80502c_84%,#c58a4a)] p-3 shadow-[inset_0_2px_8px_#3b2418] sm:p-5">
              <div className="h-full rounded-md border-[5px] border-[#754624] bg-[linear-gradient(90deg,#9c6335,#e2b574_8%,#a36c3c_15%,#744522_85%,#d3a168_93%,#82502c)] p-1 shadow-[inset_0_0_0_2px_#e0b271,0_8px_18px_#392316] sm:border-[9px] sm:p-2">
                <svg
                  ref={svgRef}
                  viewBox={`0 0 ${BOARD_WIDTH} ${BOARD_HEIGHT}`}
                  role="img"
                  aria-label="Área de desenho da lousa"
                  className={cn(
                    'h-full w-full touch-none rounded-[2px] bg-[#17633e] shadow-[inset_0_5px_22px_#092b1c]/60',
                    tool === 'pen' ? 'cursor-crosshair' : 'cursor-cell'
                  )}
                  onPointerDown={handlePointerDown}
                  onPointerMove={handlePointerMove}
                  onPointerUp={finishDrawing}
                  onPointerCancel={finishDrawing}
                >
                  <defs>
                    <linearGradient id="chalkboard-surface" x1="0" y1="0" x2="0.9" y2="1">
                      <stop offset="0%" stopColor="#1a7047" />
                      <stop offset="50%" stopColor="#155c3a" />
                      <stop offset="100%" stopColor="#104b30" />
                    </linearGradient>
                    <filter id="chalk-soften" x="-5%" y="-5%" width="110%" height="110%">
                      <feGaussianBlur in="SourceGraphic" stdDeviation="0.45" result="soft" />
                      <feMerge>
                        <feMergeNode in="soft" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                    <pattern
                      id="board-grain"
                      width="190"
                      height="150"
                      patternUnits="userSpaceOnUse"
                    >
                      <path
                        d="M10 18h68M112 54h56M37 113h98M148 132h32"
                        stroke="#d5e9c9"
                        strokeOpacity=".035"
                        strokeWidth="2"
                      />
                      <circle cx="93" cy="24" r="1.5" fill="#d5e9c9" fillOpacity=".08" />
                      <circle cx="20" cy="82" r="1" fill="#071f16" fillOpacity=".22" />
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
                          fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
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
                              fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
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
                        <g key={item.id}>
                          <rect
                            x={item.x}
                            y={item.y}
                            width={item.width}
                            height={item.height}
                            fill="none"
                            stroke={item.color ?? '#f8f5e7'}
                            strokeWidth={item.size ?? 4}
                            rx="12"
                            filter="url(#chalk-soften)"
                          />
                          {item.label && (
                            <text
                              x={item.x + 12}
                              y={item.y + Math.min((item.height ?? 0) - 8, 34)}
                              fill={item.color ?? '#f8f5e7'}
                              fontSize="22"
                              fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
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
                              fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
                              filter="url(#chalk-soften)"
                            >
                              {item.label}
                            </text>
                          )}
                        </g>
                      );
                    }
                    return (
                      <g key={item.id}>
                        <image
                          href={item.src}
                          x={item.x}
                          y={item.y}
                          width={item.width ?? 320}
                          height={item.height ?? 220}
                          preserveAspectRatio="xMidYMid meet"
                        />
                        <rect
                          x={item.x}
                          y={item.y}
                          width={item.width ?? 320}
                          height={item.height ?? 220}
                          fill="none"
                          stroke="#f8f5e7"
                          strokeWidth="3"
                          filter="url(#chalk-soften)"
                        />
                        {item.label && (
                          <text
                            x={item.x}
                            y={item.y + (item.height ?? 220) + 22}
                            fill="#f5d76e"
                            fontSize="16"
                            fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
                            filter="url(#chalk-soften)"
                          >
                            {item.label}
                          </text>
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
              <p className="pointer-events-none absolute right-7 bottom-6 rounded-full bg-white/85 px-3 py-1 text-[11px] text-slate-500 shadow-sm">
                Moldura de madeira · Giz salvo neste navegador
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
