'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  ArrowUpRight,
  Circle,
  Copy,
  ImagePlus,
  Layers,
  Maximize2,
  Minimize2,
  MousePointer,
  Pencil,
  Shapes,
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

type BoardTool = 'select' | 'pen' | 'line' | 'rectangle' | 'ellipse';
type BoardItemType = 'text' | 'path' | 'line' | 'rectangle' | 'ellipse' | 'image' | 'svg';

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
  svgContent?: string;
  viewBox?: string;
}

interface BoardPacket {
  type?: string;
  action?: string;
  element?: unknown;
  target?: string;
  changes?: Partial<BoardItem>;
}

// Bibliotecas de SVGs Vetoriais Técnicos Prontos para Ensino e Ilustração
const SVG_PRESETS: { id: string; name: string; category: string; svgContent: string; viewBox: string; defaultWidth: number; defaultHeight: number }[] = [
  {
    id: 'turbine',
    name: 'Turbina Aeronáutica (Turbofan Cutaway)',
    category: 'Engenharia Aeroespacial',
    viewBox: '0 0 520 340',
    defaultWidth: 540,
    defaultHeight: 340,
    svgContent: `
      <defs>
        <linearGradient id="tf-flame" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#ff9900" stop-opacity="0.95" />
          <stop offset="35%" stop-color="#ef4444" stop-opacity="0.9" />
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.0" />
        </linearGradient>
        <linearGradient id="tf-core" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#1e293b" />
          <stop offset="50%" stop-color="#334155" />
          <stop offset="100%" stop-color="#0f172a" />
        </linearGradient>
      </defs>
      <!-- Nacelle Externa (Carenagem) -->
      <path d="M 50 60 Q 150 30 340 50 L 380 80 L 340 100 L 160 90 Q 75 90 50 60 Z" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/>
      <path d="M 50 280 Q 150 310 340 290 L 380 260 L 340 240 L 160 250 Q 75 250 50 280 Z" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/>
      <!-- Fluxo de Ar Secundário (Bypass) -->
      <path d="M 25 55 L 140 68 L 340 78 L 430 90" stroke="#38bdf8" stroke-width="2.5" stroke-dasharray="6 4" fill="none"/>
      <path d="M 25 285 L 140 272 L 340 262 L 430 250" stroke="#38bdf8" stroke-width="2.5" stroke-dasharray="6 4" fill="none"/>
      <!-- Cone Frontal Giratório (Spinner) -->
      <path d="M 40 170 Q 95 135 115 170 Q 95 205 40 170 Z" fill="#0284c7" stroke="#e0f2fe" stroke-width="2"/>
      <!-- Palhetas do Fan Frontal -->
      <ellipse cx="115" cy="170" rx="14" ry="92" fill="none" stroke="#38bdf8" stroke-width="3.5"/>
      <line x1="115" y1="78" x2="115" y2="262" stroke="#7dd3fc" stroke-width="3"/>
      <line x1="102" y1="100" x2="128" y2="240" stroke="#38bdf8" stroke-width="2.5"/>
      <line x1="128" y1="100" x2="102" y2="240" stroke="#38bdf8" stroke-width="2.5"/>
      <!-- Carenagem do Núcleo Interno -->
      <path d="M 115 125 L 225 135 L 320 140 L 400 155 L 400 185 L 320 200 L 225 205 L 115 215 Z" fill="url(#tf-core)" stroke="#64748b" stroke-width="2"/>
      <!-- Estágios do Compressor (Baixa e Alta Pressão) -->
      <line x1="145" y1="128" x2="145" y2="212" stroke="#38bdf8" stroke-width="3"/>
      <line x1="170" y1="131" x2="170" y2="209" stroke="#38bdf8" stroke-width="3"/>
      <line x1="195" y1="133" x2="195" y2="207" stroke="#0ea5e9" stroke-width="3"/>
      <line x1="220" y1="135" x2="220" y2="205" stroke="#0ea5e9" stroke-width="3"/>
      <!-- Câmara de Combustão -->
      <rect x="240" y="138" width="60" height="64" rx="6" fill="#450a0a" stroke="#ef4444" stroke-width="2"/>
      <path d="M 245 160 Q 260 145 275 170 Q 285 150 295 170 L 295 175 Q 275 190 245 170 Z" fill="#f59e0b" opacity="0.95"/>
      <circle cx="270" cy="170" r="7" fill="#fbbf24"/>
      <!-- Estágios da Turbina -->
      <line x1="315" y1="142" x2="315" y2="198" stroke="#f97316" stroke-width="3.5"/>
      <line x1="335" y1="144" x2="335" y2="196" stroke="#f97316" stroke-width="3.5"/>
      <line x1="355" y1="148" x2="355" y2="192" stroke="#ef4444" stroke-width="3"/>
      <!-- Eixo Central -->
      <line x1="105" y1="170" x2="380" y2="170" stroke="#f8fafc" stroke-width="3.5" stroke-dasharray="8 4"/>
      <!-- Pluma de Exaustão / Empuxo -->
      <polygon points="400,152 495,135 480,170 495,205 400,188" fill="url(#tf-flame)"/>
      <line x1="415" y1="170" x2="505" y2="170" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <!-- Etiquetas Técnicas -->
      <text x="75" y="40" fill="#38bdf8" font-size="12" font-weight="bold" font-family="sans-serif">1. FAN FRONTAL</text>
      <text x="145" y="118" fill="#7dd3fc" font-size="11" font-weight="bold" font-family="sans-serif">2. COMPRESSÃO</text>
      <text x="240" y="128" fill="#f87171" font-size="11" font-weight="bold" font-family="sans-serif">3. COMBUSTÃO</text>
      <text x="320" y="132" fill="#fb923c" font-size="11" font-weight="bold" font-family="sans-serif">4. TURBINA</text>
      <text x="420" y="132" fill="#facc15" font-size="12" font-weight="bold" font-family="sans-serif">5. EMPUXO</text>
    `,
  },
  {
    id: 'ai-chip',
    name: 'Processador Neural & Chip IA',
    category: 'Inteligência Artificial',
    viewBox: '0 0 520 340',
    defaultWidth: 520,
    defaultHeight: 340,
    svgContent: `
      <!-- Placa de Circuito Base -->
      <rect x="60" y="40" width="400" height="260" rx="16" fill="#031514" stroke="#22d3ee" stroke-width="2"/>
      <!-- Matriz de Pinos Dourados -->
      <rect x="42" y="70" width="18" height="200" fill="#f59e0b" rx="2" opacity="0.8"/>
      <rect x="460" y="70" width="18" height="200" fill="#f59e0b" rx="2" opacity="0.8"/>
      <!-- Trilha de Dados Holográficos -->
      <path d="M 60 90 L 140 90 L 170 120 L 220 120" stroke="#22d3ee" stroke-width="2.5" fill="none"/>
      <path d="M 60 250 L 140 250 L 170 220 L 220 220" stroke="#22d3ee" stroke-width="2.5" fill="none"/>
      <path d="M 460 90 L 380 90 L 350 120 L 300 120" stroke="#34d399" stroke-width="2.5" fill="none"/>
      <path d="M 460 250 L 380 250 L 350 220 L 300 220" stroke="#34d399" stroke-width="2.5" fill="none"/>
      <!-- Núcleo Central de Processamento Neural -->
      <rect x="180" y="90" width="160" height="160" rx="12" fill="#082b27" stroke="#38bdf8" stroke-width="3"/>
      <!-- Grade de Cores Tensores -->
      <rect x="195" y="105" width="55" height="55" rx="6" fill="#0e7490" stroke="#67e8f9" stroke-width="1.5"/>
      <rect x="270" y="105" width="55" height="55" rx="6" fill="#0e7490" stroke="#67e8f9" stroke-width="1.5"/>
      <rect x="195" y="180" width="55" height="55" rx="6" fill="#0e7490" stroke="#67e8f9" stroke-width="1.5"/>
      <rect x="270" y="180" width="55" height="55" rx="6" fill="#0e7490" stroke="#67e8f9" stroke-width="1.5"/>
      <!-- Centro Neural Pulsante -->
      <circle cx="260" cy="170" r="14" fill="#22d3ee" opacity="0.9"/>
      <circle cx="260" cy="170" r="24" fill="none" stroke="#22d3ee" stroke-width="2" stroke-dasharray="4 4"/>
      <!-- Rótulos Técnicos -->
      <text x="260" y="74" text-anchor="middle" fill="#67e8f9" font-size="13" font-weight="bold" font-family="sans-serif">NPU · TENSOR CORES MATRIX</text>
      <text x="260" y="278" text-anchor="middle" fill="#34d399" font-size="11" font-weight="bold" font-family="sans-serif">BARRAMENTO NEURAL 10 TB/s</text>
    `,
  },
  {
    id: 'atom',
    name: 'Átomo & Órbitas Quânticas',
    category: 'Física & Química',
    viewBox: '0 0 520 340',
    defaultWidth: 500,
    defaultHeight: 340,
    svgContent: `
      <!-- Órbitas Elípticas em Perspectiva 3D -->
      <ellipse cx="260" cy="170" rx="190" ry="60" fill="none" stroke="#22d3ee" stroke-width="2.5" opacity="0.8" transform="rotate(-30 260 170)"/>
      <ellipse cx="260" cy="170" rx="190" ry="60" fill="none" stroke="#38bdf8" stroke-width="2.5" opacity="0.8" transform="rotate(30 260 170)"/>
      <ellipse cx="260" cy="170" rx="190" ry="60" fill="none" stroke="#a855f7" stroke-width="2.5" opacity="0.8" transform="rotate(90 260 170)"/>
      <!-- Elétrons em Órbita -->
      <circle cx="110" cy="110" r="8" fill="#38bdf8" stroke="#ffffff" stroke-width="2"/>
      <circle cx="410" cy="110" r="8" fill="#22d3ee" stroke="#ffffff" stroke-width="2"/>
      <circle cx="260" cy="30" r="8" fill="#c084fc" stroke="#ffffff" stroke-width="2"/>
      <circle cx="260" cy="310" r="8" fill="#c084fc" stroke="#ffffff" stroke-width="2"/>
      <!-- Núcleo Atômico Central (Prótons e Nêutrons) -->
      <circle cx="260" cy="170" r="32" fill="#082b27" stroke="#22d3ee" stroke-width="2" opacity="0.6"/>
      <circle cx="250" cy="162" r="12" fill="#ef4444"/>
      <circle cx="270" cy="162" r="12" fill="#3b82f6"/>
      <circle cx="260" cy="178" r="12" fill="#f59e0b"/>
      <circle cx="272" cy="175" r="10" fill="#ef4444"/>
      <circle cx="248" cy="175" r="10" fill="#3b82f6"/>
      <!-- Etiquetas -->
      <text x="260" y="224" text-anchor="middle" fill="#67e8f9" font-size="12" font-weight="bold" font-family="sans-serif">NÚCLEO (PRÓTONS / NÊUTRONS)</text>
      <text x="120" y="94" fill="#38bdf8" font-size="11" font-weight="bold" font-family="sans-serif">e⁻ ÓRBITA</text>
    `,
  },
  {
    id: 'circuit',
    name: 'Circuito Lógico Digital',
    category: 'Eletrônica Digital',
    viewBox: '0 0 520 340',
    defaultWidth: 520,
    defaultHeight: 340,
    svgContent: `
      <!-- Linhas de Barramento -->
      <line x1="50" y1="90" x2="160" y2="90" stroke="#22d3ee" stroke-width="3"/>
      <line x1="50" y1="130" x2="160" y2="130" stroke="#22d3ee" stroke-width="3"/>
      <line x1="50" y1="210" x2="160" y2="210" stroke="#34d399" stroke-width="3"/>
      <line x1="50" y1="250" x2="160" y2="250" stroke="#34d399" stroke-width="3"/>
      <!-- Porta AND 1 -->
      <path d="M 160 70 L 210 70 A 40 40 0 0 1 210 150 L 160 150 Z" fill="#082b27" stroke="#22d3ee" stroke-width="2.5"/>
      <text x="195" y="116" text-anchor="middle" fill="#67e8f9" font-size="13" font-weight="bold" font-family="sans-serif">AND</text>
      <!-- Porta AND 2 -->
      <path d="M 160 190 L 210 190 A 40 40 0 0 1 210 270 L 160 270 Z" fill="#082b27" stroke="#34d399" stroke-width="2.5"/>
      <text x="195" y="236" text-anchor="middle" fill="#a7f3d0" font-size="13" font-weight="bold" font-family="sans-serif">AND</text>
      <!-- Saídas intermediárias -->
      <line x1="250" y1="110" x2="310" y2="110" stroke="#22d3ee" stroke-width="3"/>
      <line x1="310" y1="110" x2="310" y2="150" stroke="#22d3ee" stroke-width="3"/>
      <line x1="310" y1="150" x2="350" y2="150" stroke="#22d3ee" stroke-width="3"/>
      <line x1="250" y1="230" x2="310" y2="230" stroke="#34d399" stroke-width="3"/>
      <line x1="310" y1="230" x2="310" y2="190" stroke="#34d399" stroke-width="3"/>
      <line x1="310" y1="190" x2="350" y2="190" stroke="#34d399" stroke-width="3"/>
      <!-- Porta OR Final -->
      <path d="M 350 130 Q 380 170 350 210 Q 400 210 440 170 Q 400 130 350 130 Z" fill="#0c2338" stroke="#fbbf24" stroke-width="2.5"/>
      <text x="390" y="176" text-anchor="middle" fill="#fde68a" font-size="14" font-weight="bold" font-family="sans-serif">OR</text>
      <!-- Saída Final Q -->
      <line x1="440" y1="170" x2="490" y2="170" stroke="#fbbf24" stroke-width="3.5"/>
      <circle cx="495" cy="170" r="7" fill="#fbbf24"/>
      <!-- Rótulos -->
      <text x="40" y="94" fill="#22d3ee" font-size="12" font-weight="bold" font-family="sans-serif">A</text>
      <text x="40" y="134" fill="#22d3ee" font-size="12" font-weight="bold" font-family="sans-serif">B</text>
      <text x="40" y="214" fill="#34d399" font-size="12" font-weight="bold" font-family="sans-serif">C</text>
      <text x="40" y="254" fill="#34d399" font-size="12" font-weight="bold" font-family="sans-serif">D</text>
      <text x="470" y="155" fill="#fde68a" font-size="13" font-weight="bold" font-family="sans-serif">Q = (A·B) + (C·D)</text>
    `,
  },
];

function isBoardItem(value: unknown): value is BoardItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<BoardItem>;
  if (
    typeof item.id !== 'string' ||
    !['text', 'path', 'line', 'rectangle', 'ellipse', 'image', 'svg'].includes(item.type ?? '') ||
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
  if (item.type === 'svg') {
    return (
      typeof item.svgContent === 'string' &&
      item.svgContent.length <= 500_000 &&
      typeof item.width === 'number' &&
      typeof item.height === 'number'
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
  const [loaded, setLoaded] = useState(false);
  const [tool, setTool] = useState<BoardTool>('select');
  const [chalkColor, setChalkColor] = useState(CHALK_COLORS[0].value);
  const [textDraft, setTextDraft] = useState('');
  const [draft, setDraft] = useState<BoardItem | null>(null);
  const [storageError, setStorageError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isSvgMenuOpen, setIsSvgMenuOpen] = useState(false);
  const [customSvgCode, setCustomSvgCode] = useState('');
  const [showCustomSvgModal, setShowCustomSvgModal] = useState(false);

  // Informações de arraste e redimensionamento ativo de itens
  const dragInfo = useRef<{
    isDragging: boolean;
    isResizing: boolean;
    startPoint: { x: number; y: number };
    itemOrigPos: { x: number; y: number; width: number; height: number };
  } | null>(null);

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

  // Atalho do teclado: ESC fecha a lousa e restaura o navegador, Delete apaga selecionado
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        fecharLousa();
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId) {
        const activeTag = (document.activeElement?.tagName || '').toLowerCase();
        if (activeTag !== 'input' && activeTag !== 'textarea') {
          event.preventDefault();
          setItems((prev) => prev.filter((it) => it.id !== selectedId));
          setSelectedId(null);
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, selectedId]);

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
          setSelectedId(null);
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
    dragInfo.current = null;
  }, [draft]);

  const handleCanvasPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const point = getBoardPoint(event, svgRef.current);

    // Se estiver no modo de seleção e clicar no fundo, desseleciona
    if (tool === 'select') {
      setSelectedId(null);
      return;
    }

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

  const handleCanvasPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const point = getBoardPoint(event, svgRef.current);

    // Manipulação interativa: Arrastar ou Redimensionar item selecionado
    if (dragInfo.current && selectedId) {
      const { isDragging, isResizing, startPoint, itemOrigPos } = dragInfo.current;
      const deltaX = point.x - startPoint.x;
      const deltaY = point.y - startPoint.y;

      if (isDragging) {
        setItems((prev) =>
          prev.map((it) =>
            it.id === selectedId
              ? {
                  ...it,
                  x: Math.max(0, Math.min(BOARD_WIDTH - 60, Math.round(itemOrigPos.x + deltaX))),
                  y: Math.max(0, Math.min(BOARD_HEIGHT - 60, Math.round(itemOrigPos.y + deltaY))),
                }
              : it
          )
        );
      } else if (isResizing) {
        setItems((prev) =>
          prev.map((it) =>
            it.id === selectedId
              ? {
                  ...it,
                  width: Math.max(80, Math.min(BOARD_WIDTH - it.x, Math.round(itemOrigPos.width + deltaX))),
                  height: Math.max(50, Math.min(BOARD_HEIGHT - it.y, Math.round(itemOrigPos.height + deltaY))),
                }
              : it
          )
        );
      }
      return;
    }

    // Desenho normal com caneta/formas
    if (!draft) return;
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

  const handleItemPointerDown = (
    event: React.PointerEvent,
    item: BoardItem,
    isResize = false
  ) => {
    if (tool !== 'select') return;
    event.stopPropagation();
    setSelectedId(item.id);
    if (!svgRef.current) return;
    const point = getBoardPoint(event as unknown as React.PointerEvent<SVGSVGElement>, svgRef.current);
    dragInfo.current = {
      isDragging: !isResize,
      isResizing: isResize,
      startPoint: point,
      itemOrigPos: {
        x: item.x,
        y: item.y,
        width: item.width ?? 380,
        height: item.height ?? 260,
      },
    };
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
        y: 120 + current.filter((item) => item.type === 'text').length * 60,
        text,
        color: chalkColor,
        size: 28,
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
      const src = canvas.toDataURL('image/jpeg', 0.82);
      if (src.length > 2_500_000) {
        setStorageError('A imagem é grande demais. Escolha uma imagem menor.');
        return;
      }
      setItems((current) => [
        ...current,
        {
          id: `image-${Date.now()}`,
          type: 'image',
          x: 680,
          y: 240,
          width: Math.min(540, Math.round(canvas.width * 0.8)),
          height: Math.min(340, Math.round(canvas.height * 0.8)),
          src,
          label: file.name.slice(0, 80),
        },
      ]);
    } catch {
      setStorageError('Não foi possível carregar essa imagem.');
    }
  };

  const addPresetSvg = (presetId: string) => {
    const preset = SVG_PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    setItems((current) => [
      ...current,
      {
        id: `svg-${Date.now()}`,
        type: 'svg',
        x: 680,
        y: 220,
        width: preset.defaultWidth,
        height: preset.defaultHeight,
        color: '#22d3ee',
        svgContent: preset.svgContent,
        viewBox: preset.viewBox,
        label: preset.name,
      },
    ]);
    setIsSvgMenuOpen(false);
  };

  const addCustomSvg = () => {
    const code = customSvgCode.trim();
    if (!code) return;
    setItems((current) => [
      ...current,
      {
        id: `svg-${Date.now()}`,
        type: 'svg',
        x: 680,
        y: 220,
        width: 520,
        height: 340,
        color: chalkColor || '#22d3ee',
        svgContent: code,
        viewBox: '0 0 520 340',
        label: 'Desenho Vetorial SVG',
      },
    ]);
    setCustomSvgCode('');
    setShowCustomSvgModal(false);
  };

  const duplicarSelecionado = () => {
    if (!selectedId) return;
    const item = items.find((it) => it.id === selectedId);
    if (!item) return;
    const clone: BoardItem = {
      ...item,
      id: `${item.type}-${Date.now()}`,
      x: item.x + 40,
      y: item.y + 40,
    };
    setItems((prev) => [...prev, clone]);
    setSelectedId(clone.id);
  };

  const trazerParaFrente = () => {
    if (!selectedId) return;
    setItems((prev) => {
      const item = prev.find((it) => it.id === selectedId);
      if (!item) return prev;
      return [...prev.filter((it) => it.id !== selectedId), item];
    });
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
        <div className="fixed inset-0 z-50 flex flex-col w-screen h-screen bg-slate-950 p-0 m-0 overflow-hidden select-none">
          <section
            aria-label="Lousa de ensino do Jarvis Widescreen 16:9"
            className="relative flex flex-col h-full w-full overflow-hidden text-white bg-slate-950 rounded-none border-0"
          >
            {/* Linhas decorativas holográficas no topo */}
            <div className="pointer-events-none absolute inset-x-8 top-0 z-20 h-px bg-gradient-to-r from-transparent via-cyan-400/80 to-transparent" />
            <div className="pointer-events-none absolute -top-1 left-1/2 z-20 h-2 w-48 -translate-x-1/2 rounded-full bg-cyan-400/50 blur-xs" />

            {/* Cabeçalho Principal Widescreen */}
            <header className="flex shrink-0 items-center justify-between border-b border-cyan-500/20 bg-slate-950/95 px-6 py-2.5 text-white backdrop-blur-xl">
              <div className="flex items-center gap-3">
                <div className="flex size-9 items-center justify-center rounded-xl border border-cyan-400/40 bg-cyan-950/50 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.3)]">
                  <Pencil className="size-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold tracking-wide uppercase text-cyan-100">
                      Lousa do Jarvis · Cinema Widescreen 16:9
                    </h2>
                    <span className="rounded-full border border-cyan-400/40 bg-cyan-950/60 px-2 py-0.5 text-[10px] font-mono text-cyan-300">
                      1920×1080 Full HD
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">
                    Pressione <kbd className="rounded border border-cyan-400/40 bg-cyan-950/80 px-1.5 py-0.5 text-[10px] text-cyan-300 font-semibold">ESC</kbd> para voltar à tela principal do Jarvis · Arraste elementos livremente na lousa
                  </p>
                </div>
              </div>

              {/* Botões de Ação do Cabeçalho */}
              <div className="flex items-center gap-3">
                {/* Botão em Grande Destaque de Tela Cheia Nativa do Monitor */}
                <button
                  type="button"
                  onClick={toggleFullscreen}
                  className={cn(
                    'flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold tracking-wider uppercase transition-all duration-300 active:scale-95 shadow-lg',
                    isFullscreen
                      ? 'border border-amber-400/50 bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 shadow-[0_0_15px_rgba(251,191,36,0.3)]'
                      : 'border border-cyan-400/60 bg-gradient-to-r from-cyan-600/40 via-blue-600/40 to-cyan-500/40 text-cyan-100 hover:border-cyan-300 hover:from-cyan-500/60 hover:to-blue-600/60 shadow-[0_0_20px_rgba(6,182,212,0.45)]'
                  )}
                  title={isFullscreen ? 'Sair da tela cheia do monitor (ESC)' : 'Expandir para TELA CHEIA no Monitor (Sem abas, sem barras)'}
                >
                  {isFullscreen ? (
                    <>
                      <Minimize2 className="size-4 text-amber-300" />
                      <span>Sair da Tela Cheia (ESC)</span>
                    </>
                  ) : (
                    <>
                      <Maximize2 className="size-4 text-cyan-300" />
                      <span>Modo Tela Cheia (F11 / Monitor 100%)</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={fecharLousa}
                  className="rounded-xl border border-white/10 bg-slate-900/80 p-2 text-slate-300 transition hover:border-rose-400/40 hover:bg-rose-950/40 hover:text-rose-200"
                  title="Fechar lousa e voltar ao Jarvis (ESC)"
                  aria-label="Fechar lousa"
                >
                  <X className="size-5" />
                </button>
              </div>
            </header>

            {/* Barra de Ferramentas Avançadas & Manipulação SVG */}
            <div className="flex shrink-0 flex-wrap items-center gap-2.5 border-b border-cyan-500/20 bg-slate-900/90 px-6 py-2 backdrop-blur-xl">
              {/* Ferramentas de Seleção e Desenho */}
              <div className="flex items-center gap-1 rounded-xl border border-cyan-500/30 bg-slate-950/80 p-1 shadow-inner">
                {[
                  { value: 'select', label: 'Mover & Selecionar (Arraste elementos)', icon: MousePointer },
                  { value: 'pen', label: 'Desenhar à mão livre', icon: Pencil },
                  { value: 'line', label: 'Linha / Seta Direcional', icon: ArrowUpRight },
                  { value: 'rectangle', label: 'Retângulo / Card 3D', icon: Square },
                  { value: 'ellipse', label: 'Círculo / Órbita', icon: Circle },
                ].map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    type="button"
                    title={label}
                    aria-label={label}
                    aria-pressed={tool === value}
                    onClick={() => {
                      setTool(value as BoardTool);
                      if (value !== 'select') setSelectedId(null);
                    }}
                    className={cn(
                      'rounded-lg p-2 transition-all duration-200',
                      tool === value
                        ? 'border border-cyan-400/60 bg-cyan-500/25 text-cyan-200 shadow-[0_0_12px_rgba(6,182,212,0.4)]'
                        : 'text-slate-400 hover:bg-slate-800/80 hover:text-cyan-200'
                    )}
                  >
                    <Icon className="size-4" />
                  </button>
                ))}
              </div>

              {/* Botão de Inserção de Vetores e Desenhos SVG Técnicos */}
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setIsSvgMenuOpen((prev) => !prev)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-400/40 bg-gradient-to-r from-cyan-950/60 to-blue-950/60 px-3 py-2 text-xs font-semibold text-cyan-200 shadow-[0_0_12px_rgba(6,182,212,0.2)] transition hover:border-cyan-300 hover:text-white"
                  title="Inserir Ilustrações e Desenhos Vetoriais SVG (Turbina, Chip IA, Átomo, Circuitos)"
                >
                  <Shapes className="size-4 text-cyan-300" />
                  <span>Desenhos SVG</span>
                </button>

                {/* Dropdown de Presets SVG */}
                {isSvgMenuOpen && (
                  <div className="absolute top-full left-0 z-50 mt-2 w-80 rounded-2xl border border-cyan-400/40 bg-slate-950/95 p-3 shadow-[0_10px_30px_rgba(0,0,0,0.8),0_0_20px_rgba(6,182,212,0.25)] backdrop-blur-2xl">
                    <p className="px-2 pb-2 text-[11px] font-bold tracking-wider uppercase text-cyan-400 border-b border-cyan-500/20">
                      Modelos Vetoriais Prontos (SVG)
                    </p>
                    <div className="mt-2 space-y-1">
                      {SVG_PRESETS.map((preset) => (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => addPresetSvg(preset.id)}
                          className="flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-left text-xs text-slate-200 transition hover:bg-cyan-500/20 hover:text-cyan-100"
                        >
                          <div>
                            <p className="font-semibold">{preset.name}</p>
                            <p className="text-[10px] text-slate-400">{preset.category}</p>
                          </div>
                          <span className="text-cyan-400 text-xs">＋ Inserir</span>
                        </button>
                      ))}
                    </div>
                    <div className="mt-2.5 pt-2 border-t border-cyan-500/20">
                      <button
                        type="button"
                        onClick={() => {
                          setIsSvgMenuOpen(false);
                          setShowCustomSvgModal(true);
                        }}
                        className="w-full rounded-xl border border-cyan-500/30 bg-slate-900/80 px-2.5 py-1.5 text-center text-xs font-medium text-cyan-300 hover:bg-cyan-900/40"
                      >
                        Colar Código SVG Customizado...
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Seletor de Cores de Giz / Caneta */}
              <div className="flex items-center gap-1.5 rounded-xl border border-cyan-500/20 bg-slate-950/80 px-2.5 py-1.5 shadow-inner">
                {CHALK_COLORS.map((chalk) => (
                  <button
                    key={chalk.value}
                    type="button"
                    title={chalk.name}
                    aria-label={chalk.name}
                    aria-pressed={chalkColor === chalk.value}
                    onClick={() => {
                      setChalkColor(chalk.value);
                      if (selectedId) {
                        setItems((prev) =>
                          prev.map((it) => (it.id === selectedId ? { ...it, color: chalk.value } : it))
                        );
                      }
                    }}
                    className={cn(
                      'size-5 rounded-full border border-white/20 transition-all duration-200',
                      chalkColor === chalk.value
                        ? 'scale-115 ring-2 ring-cyan-400 ring-offset-2 ring-offset-slate-950 shadow-[0_0_8px_rgba(34,211,238,0.7)]'
                        : 'opacity-85 hover:scale-110 hover:opacity-100'
                    )}
                    style={{ backgroundColor: chalk.value }}
                  />
                ))}
              </div>

              {/* Caixa de Texto Rápido */}
              <div className="flex min-w-[200px] flex-1 items-center gap-2 sm:max-w-md">
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
                  placeholder="Anotação na lousa..."
                  aria-label="Texto para adicionar à lousa"
                  className="h-8 min-w-0 flex-1 rounded-xl border border-cyan-500/30 bg-slate-950/90 px-3 text-xs text-cyan-100 outline-none placeholder:text-slate-500 transition-all focus:border-cyan-400 focus:shadow-[0_0_12px_rgba(6,182,212,0.25)] focus:ring-1 focus:ring-cyan-400/40"
                />
                <button
                  type="button"
                  onClick={addText}
                  className="rounded-xl border border-cyan-400/40 bg-cyan-500/20 px-3 py-1.5 text-xs font-semibold text-cyan-200 transition-all hover:bg-cyan-500/30 hover:shadow-[0_0_10px_rgba(6,182,212,0.35)] active:scale-95"
                >
                  Adicionar
                </button>
              </div>

              {/* Botão de Upload de Imagem */}
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
                className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-500/30 bg-slate-950/80 px-3 py-1.5 text-xs font-medium text-cyan-200 transition-all hover:border-cyan-400/50 hover:bg-slate-900 hover:text-cyan-100"
              >
                <ImagePlus className="size-4 text-cyan-400" />
                <span>Imagem 3D</span>
              </button>

              {/* Ações para o Item Selecionado */}
              {selectedId && (
                <div className="flex items-center gap-1.5 rounded-xl border border-amber-400/40 bg-amber-950/40 px-2 py-1 shadow-[0_0_10px_rgba(251,191,36,0.2)]">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-amber-300">
                    Item Selecionado:
                  </span>
                  <button
                    type="button"
                    onClick={duplicarSelecionado}
                    title="Duplicar elemento"
                    className="rounded-lg p-1 text-amber-200 hover:bg-amber-900/50 hover:text-white"
                  >
                    <Copy className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={trazerParaFrente}
                    title="Trazer para frente"
                    className="rounded-lg p-1 text-amber-200 hover:bg-amber-900/50 hover:text-white"
                  >
                    <Layers className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setItems((prev) => prev.filter((it) => it.id !== selectedId));
                      setSelectedId(null);
                    }}
                    title="Excluir item selecionado (Delete)"
                    className="rounded-lg p-1 text-rose-300 hover:bg-rose-950/60 hover:text-rose-100"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              )}

              {/* Botões Desfazer e Limpar */}
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setItems((current) => current.slice(0, -1))}
                  disabled={items.length === 0}
                  className="rounded-xl border border-cyan-500/20 bg-slate-950/80 p-2 text-cyan-200/80 transition-all hover:border-cyan-400/40 hover:bg-slate-900 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-30"
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
                      setSelectedId(null);
                    }
                  }}
                  disabled={items.length === 0}
                  className="rounded-xl border border-rose-500/30 bg-rose-950/30 p-2 text-rose-300 transition-all hover:border-rose-400/50 hover:bg-rose-900/40 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-30"
                  title="Limpar lousa completa"
                  aria-label="Limpar lousa completa"
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
            </div>

            {/* Modal para Colar SVG Customizado */}
            {showCustomSvgModal && (
              <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4">
                <div className="w-full max-w-lg rounded-2xl border border-cyan-400/40 bg-slate-950 p-5 shadow-2xl">
                  <div className="flex items-center justify-between pb-3 border-b border-cyan-500/20">
                    <h3 className="text-sm font-bold text-cyan-300">Inserir Código SVG Vetorial</h3>
                    <button
                      type="button"
                      onClick={() => setShowCustomSvgModal(false)}
                      className="text-slate-400 hover:text-white"
                    >
                      <X className="size-4" />
                    </button>
                  </div>
                  <p className="mt-2 text-xs text-slate-400">
                    Cole o código interno do SVG (ex.: &lt;path ... /&gt;, &lt;circle ... /&gt;, &lt;g&gt;...&lt;/g&gt;) ou o elemento &lt;svg&gt; completo:
                  </p>
                  <textarea
                    rows={7}
                    value={customSvgCode}
                    onChange={(e) => setCustomSvgCode(e.target.value)}
                    placeholder="<circle cx='100' cy='100' r='50' fill='#22d3ee' />..."
                    className="mt-3 w-full rounded-xl border border-cyan-500/30 bg-slate-900 p-3 font-mono text-xs text-cyan-200 outline-none focus:border-cyan-400"
                  />
                  <div className="mt-4 flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setShowCustomSvgModal(false)}
                      className="rounded-xl px-4 py-2 text-xs text-slate-400 hover:bg-slate-900"
                    >
                      Cancelar
                    </button>
                    <button
                      type="button"
                      onClick={addCustomSvg}
                      className="rounded-xl bg-cyan-500/30 border border-cyan-400/60 px-5 py-2 text-xs font-bold text-cyan-100 hover:bg-cyan-500/50"
                    >
                      Renderizar na Lousa
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Área Principal de Exibição Widescreen 16:9 */}
            <div className="relative flex-1 overflow-hidden bg-slate-950 flex items-center justify-center p-0">
              <svg
                ref={svgRef}
                viewBox={`0 0 ${BOARD_WIDTH} ${BOARD_HEIGHT}`}
                role="img"
                aria-label="Área de desenho da lousa widescreen"
                className={cn(
                  'h-full w-full touch-none bg-[#05110e]',
                  tool === 'select' ? 'cursor-default' : tool === 'pen' ? 'cursor-crosshair' : 'cursor-cell'
                )}
                onPointerDown={handleCanvasPointerDown}
                onPointerMove={handleCanvasPointerMove}
                onPointerUp={finishDrawing}
                onPointerCancel={finishDrawing}
              >
                <defs>
                  <linearGradient id="chalkboard-surface" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#081815" />
                    <stop offset="50%" stopColor="#040f0d" />
                    <stop offset="100%" stopColor="#020807" />
                  </linearGradient>

                  <filter id="chalk-soften" x="-5%" y="-5%" width="110%" height="110%">
                    <feGaussianBlur in="SourceGraphic" stdDeviation="0.35" result="soft" />
                    <feMerge>
                      <feMergeNode in="soft" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>

                  {/* Filtro 3D Profundo para Cards e Imagens que Saltam da Tela */}
                  <filter id="card-3d-depth" x="-40%" y="-40%" width="180%" height="180%">
                    <feDropShadow dx="0" dy="28" stdDeviation="30" floodColor="#000000" floodOpacity="0.95" />
                    <feDropShadow dx="0" dy="8" stdDeviation="16" floodColor="#06b6d4" floodOpacity="0.5" />
                  </filter>

                  {/* Borda Gradiente Cibernética */}
                  <linearGradient id="cyber-frame-grad" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.95" />
                    <stop offset="50%" stopColor="#0ea5e9" stopOpacity="0.5" />
                    <stop offset="100%" stopColor="#a855f7" stopOpacity="0.85" />
                  </linearGradient>

                  {/* Brilho Reflexivo de Vidro 3D */}
                  <linearGradient id="card-glass-gloss" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#ffffff" stopOpacity="0.25" />
                    <stop offset="40%" stopColor="#ffffff" stopOpacity="0.04" />
                    <stop offset="100%" stopColor="#000000" stopOpacity="0.25" />
                  </linearGradient>

                  {/* Fundo de Card Tático 3D */}
                  <linearGradient id="box-card-bg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#082b24" stopOpacity="0.88" />
                    <stop offset="100%" stopColor="#031410" stopOpacity="0.96" />
                  </linearGradient>

                  {/* Grade Holográfica de Fundo */}
                  <pattern id="board-grain" width="80" height="80" patternUnits="userSpaceOnUse">
                    <circle cx="40" cy="40" r="0.7" fill="#22d3ee" fillOpacity="0.08" />
                    <path d="M 0 40 L 80 40 M 40 0 L 40 80" stroke="#22d3ee" strokeOpacity="0.03" strokeWidth="1" />
                  </pattern>

                  {/* Marcador de Seta */}
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

                {/* Superfície da Lousa Widescreen */}
                <rect width={BOARD_WIDTH} height={BOARD_HEIGHT} fill="url(#chalkboard-surface)" />
                <rect width={BOARD_WIDTH} height={BOARD_HEIGHT} fill="url(#board-grain)" />

                {/* Itens Renderizados na Lousa */}
                {items.map((item) => {
                  const isSelected = selectedId === item.id;

                  // 1. TEXTO DIDÁTICO MODERNO
                  if (item.type === 'text') {
                    const fontSize = item.size ?? 26;
                    const maxChars = fontSize >= 32 ? 45 : fontSize >= 22 ? 55 : 68;
                    const lineStep = Math.round(fontSize * 1.32);
                    const lines = textLines(item.text ?? '', maxChars);
                    return (
                      <g
                        key={item.id}
                        className={cn('cursor-move transition-all', isSelected ? 'filter drop-shadow-[0_0_12px_rgba(34,211,238,0.7)]' : '')}
                        onPointerDown={(e) => handleItemPointerDown(e, item)}
                      >
                        {isSelected && (
                          <rect
                            x={item.x - 10}
                            y={item.y - fontSize - 6}
                            width={Math.max(200, (lines[0]?.length || 10) * (fontSize * 0.58) + 20)}
                            height={lines.length * lineStep + 20}
                            rx="8"
                            fill="rgba(6, 182, 212, 0.08)"
                            stroke="#38bdf8"
                            strokeWidth="1.5"
                            strokeDasharray="6 4"
                          />
                        )}
                        <text
                          x={item.x}
                          y={item.y}
                          fill={item.color ?? '#f8f5e7'}
                          fontSize={fontSize}
                          fontWeight="600"
                          fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                          filter="url(#chalk-soften)"
                        >
                          {lines.map((line, index) => (
                            <tspan key={`${item.id}-${index}`} x={item.x} dy={index === 0 ? 0 : lineStep}>
                              {line}
                            </tspan>
                          ))}
                        </text>
                      </g>
                    );
                  }

                  // 2. LINHAS E TRAÇOS
                  if (item.type === 'path' || item.type === 'line') {
                    return (
                      <g
                        key={item.id}
                        className="cursor-move"
                        onPointerDown={(e) => handleItemPointerDown(e, item)}
                      >
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

                  // 3. RETÂNGULOS / CARDS DE ESQUEMA FLUTUANTES
                  if (item.type === 'rectangle') {
                    const rectW = item.width ?? 450;
                    const rectH = item.height ?? 76;
                    return (
                      <g
                        key={item.id}
                        className={cn('cursor-move transition-all duration-200', isSelected ? 'scale-[1.02]' : '')}
                        onPointerDown={(e) => handleItemPointerDown(e, item)}
                      >
                        <rect
                          x={item.x}
                          y={item.y}
                          width={rectW}
                          height={rectH}
                          fill="url(#box-card-bg)"
                          stroke={isSelected ? '#38bdf8' : (item.color ?? '#22d3ee')}
                          strokeWidth={isSelected ? 3.5 : (item.size ?? 3)}
                          rx="16"
                          filter="url(#card-3d-depth)"
                        />
                        {/* Linha de brilho superior interna */}
                        <line
                          x1={item.x + 16}
                          y1={item.y + 2}
                          x2={item.x + rectW - 16}
                          y2={item.y + 2}
                          stroke="#ffffff"
                          strokeOpacity="0.25"
                          strokeWidth="1.5"
                        />
                        {item.label && (
                          <text
                            x={item.x + 20}
                            y={item.y + Math.min(rectH - 12, 46)}
                            fill={item.color ?? '#f8f5e7'}
                            fontSize="22"
                            fontWeight="bold"
                            fontFamily="system-ui, -apple-system, 'Segoe UI', Roboto, 'Inter', sans-serif"
                            filter="url(#chalk-soften)"
                          >
                            {item.label}
                          </text>
                        )}
                        {/* Alça de redimensionamento no canto inferior direito */}
                        {isSelected && (
                          <g
                            className="cursor-nwse-resize"
                            onPointerDown={(e) => handleItemPointerDown(e, item, true)}
                          >
                            <rect
                              x={item.x + rectW - 8}
                              y={item.y + rectH - 8}
                              width="16"
                              height="16"
                              rx="4"
                              fill="#38bdf8"
                              stroke="#020807"
                              strokeWidth="2"
                            />
                          </g>
                        )}
                      </g>
                    );
                  }

                  // 4. CÍRCULOS E ELIPSES
                  if (item.type === 'ellipse') {
                    return (
                      <g
                        key={item.id}
                        className="cursor-move"
                        onPointerDown={(e) => handleItemPointerDown(e, item)}
                      >
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

                  // 5. DESENHOS VETORIAIS SVG NATIVOS
                  if (item.type === 'svg') {
                    const svgW = item.width ?? 540;
                    const svgH = item.height ?? 340;
                    return (
                      <g
                        key={item.id}
                        className={cn('cursor-move transition-all duration-200', isSelected ? 'scale-[1.01]' : '')}
                        onPointerDown={(e) => handleItemPointerDown(e, item)}
                      >
                        {/* Placa Traseira 3D Cibernética */}
                        <rect
                          x={item.x - 6}
                          y={item.y - 6}
                          width={svgW + 12}
                          height={svgH + 12}
                          rx="20"
                          fill="#031210"
                          fillOpacity="0.92"
                          stroke={isSelected ? '#38bdf8' : (item.color ?? '#22d3ee')}
                          strokeWidth={isSelected ? 3.5 : 2}
                          filter="url(#card-3d-depth)"
                        />
                        {/* Cantos Táticos HUD estilo Jarvis */}
                        <path
                          d={`M ${item.x - 6} ${item.y + 16} L ${item.x - 6} ${item.y - 6} L ${item.x + 16} ${item.y - 6}`}
                          stroke="#22d3ee"
                          strokeWidth="3"
                          fill="none"
                        />
                        <path
                          d={`M ${item.x + svgW - 10} ${item.y - 6} L ${item.x + svgW + 6} ${item.y - 6} L ${item.x + svgW + 6} ${item.y + 16}`}
                          stroke="#22d3ee"
                          strokeWidth="3"
                          fill="none"
                        />
                        <path
                          d={`M ${item.x - 6} ${item.y + svgH - 10} L ${item.x - 6} ${item.y + svgH + 6} L ${item.x + 16} ${item.y + svgH + 6}`}
                          stroke="#22d3ee"
                          strokeWidth="3"
                          fill="none"
                        />
                        <path
                          d={`M ${item.x + svgW - 10} ${item.y + svgH + 6} L ${item.x + svgW + 6} ${item.y + svgH + 6} L ${item.x + svgW + 6} ${item.y + svgH - 10}`}
                          stroke="#22d3ee"
                          strokeWidth="3"
                          fill="none"
                        />

                        {/* Conteúdo Vetorial SVG Interno */}
                        <svg
                          x={item.x}
                          y={item.y}
                          width={svgW}
                          height={svgH}
                          viewBox={item.viewBox || '0 0 520 340'}
                          preserveAspectRatio="xMidYMid meet"
                          dangerouslySetInnerHTML={{ __html: item.svgContent || '' }}
                        />

                        {/* Legenda Tática Inferior */}
                        {item.label && (
                          <g>
                            <rect
                              x={item.x + 12}
                              y={item.y + svgH - 36}
                              width={Math.min(svgW - 24, item.label.length * 10 + 36)}
                              height="28"
                              rx="8"
                              fill="rgba(3, 17, 14, 0.94)"
                              stroke="#22d3ee"
                              strokeWidth="1.5"
                              strokeOpacity="0.6"
                            />
                            <circle cx={item.x + 24} cy={item.y + svgH - 22} r="3.5" fill="#38bdf8" />
                            <text
                              x={item.x + 36}
                              y={item.y + svgH - 18}
                              fill="#38bdf8"
                              fontSize="12"
                              fontWeight="bold"
                              fontFamily="system-ui, -apple-system, sans-serif"
                            >
                              {item.label}
                            </text>
                          </g>
                        )}

                        {/* Alça de redimensionamento no canto inferior direito */}
                        {isSelected && (
                          <g
                            className="cursor-nwse-resize"
                            onPointerDown={(e) => handleItemPointerDown(e, item, true)}
                          >
                            <rect
                              x={item.x + svgW - 8}
                              y={item.y + svgH - 8}
                              width="16"
                              height="16"
                              rx="4"
                              fill="#38bdf8"
                              stroke="#020807"
                              strokeWidth="2"
                            />
                          </g>
                        )}
                      </g>
                    );
                  }

                  // 6. IMAGENS 3D QUE SALTAM DA TELA COM PROFUNDIDADE
                  const imgW = item.width ?? 540;
                  const imgH = item.height ?? 330;
                  return (
                    <g
                      key={item.id}
                      className={cn(
                        'cursor-move transition-transform duration-300 hover:scale-[1.02]',
                        isSelected ? 'scale-[1.02]' : ''
                      )}
                      onPointerDown={(e) => handleItemPointerDown(e, item)}
                    >
                      {/* Placa Traseira 3D que salta para fora das letras com sombra profunda */}
                      <rect
                        x={item.x - 6}
                        y={item.y - 6}
                        width={imgW + 12}
                        height={imgH + 12}
                        rx="22"
                        fill="#03110e"
                        stroke={isSelected ? '#38bdf8' : 'url(#cyber-frame-grad)'}
                        strokeWidth={isSelected ? 4 : 3.5}
                        filter="url(#card-3d-depth)"
                      />
                      <clipPath id={`clip-${item.id}`}>
                        <rect x={item.x} y={item.y} width={imgW} height={imgH} rx="16" />
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
                      {/* Cantos HUD nos 4 vértices */}
                      <path
                        d={`M ${item.x - 6} ${item.y + 16} L ${item.x - 6} ${item.y - 6} L ${item.x + 16} ${item.y - 6}`}
                        stroke="#22d3ee"
                        strokeWidth="3"
                        fill="none"
                        pointerEvents="none"
                      />
                      <path
                        d={`M ${item.x + imgW - 10} ${item.y - 6} L ${item.x + imgW + 6} ${item.y - 6} L ${item.x + imgW + 6} ${item.y + 16}`}
                        stroke="#22d3ee"
                        strokeWidth="3"
                        fill="none"
                        pointerEvents="none"
                      />
                      <path
                        d={`M ${item.x - 6} ${item.y + imgH - 10} L ${item.x - 6} ${item.y + imgH + 6} L ${item.x + 16} ${item.y + imgH + 6}`}
                        stroke="#22d3ee"
                        strokeWidth="3"
                        fill="none"
                        pointerEvents="none"
                      />
                      <path
                        d={`M ${item.x + imgW - 10} ${item.y + imgH + 6} L ${item.x + imgW + 6} ${item.y + imgH + 6} L ${item.x + imgW + 6} ${item.y + imgH - 10}`}
                        stroke="#22d3ee"
                        strokeWidth="3"
                        fill="none"
                        pointerEvents="none"
                      />
                      {item.label && (
                        <g>
                          <rect
                            x={item.x + 14}
                            y={item.y + imgH - 42}
                            width={imgW - 28}
                            height="32"
                            rx="10"
                            fill="rgba(3, 17, 14, 0.94)"
                            stroke="#22d3ee"
                            strokeWidth="1.2"
                            strokeOpacity="0.6"
                          />
                          <circle cx={item.x + 28} cy={item.y + imgH - 26} r="4" fill="#38bdf8" />
                          <text
                            x={item.x + 40}
                            y={item.y + imgH - 21}
                            fill="#38bdf8"
                            fontSize="13"
                            fontWeight="bold"
                            letterSpacing="0.04em"
                            fontFamily="system-ui, -apple-system, sans-serif"
                          >
                            {item.label}
                          </text>
                        </g>
                      )}
                      {/* Alça de redimensionamento */}
                      {isSelected && (
                        <g
                          className="cursor-nwse-resize"
                          onPointerDown={(e) => handleItemPointerDown(e, item, true)}
                        >
                          <rect
                            x={item.x + imgW - 8}
                            y={item.y + imgH - 8}
                            width="16"
                            height="16"
                            rx="4"
                            fill="#38bdf8"
                            stroke="#020807"
                            strokeWidth="2"
                          />
                        </g>
                      )}
                    </g>
                  );
                })}

                {/* Rascunho durante desenho com caneta ou retângulos */}
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
                    fill="#38bdf8"
                    fontSize="26"
                    fontWeight="bold"
                    fontFamily="system-ui, -apple-system, sans-serif"
                    opacity="0.6"
                  >
                    Peça uma aula ao Jarvis ou use as ferramentas acima para desenhar e inserir diagramas SVG
                  </text>
                )}
              </svg>
            </div>

            {/* Rodapé Fixo com Status HUD */}
            <footer className="flex shrink-0 items-center justify-between border-t border-cyan-500/20 bg-slate-950/95 px-6 py-2 text-[11px] text-cyan-400/80 backdrop-blur-xl">
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-1.5">
                  <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
                  HUD Interativo Online
                </span>
                <span>•</span>
                <span>Arraste elementos pelo modo Mover (Mouse)</span>
                <span>•</span>
                <span>ESC fecha a lousa e retorna ao Jarvis</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-slate-400">
                  {items.length} elemento{items.length === 1 ? '' : 's'} na lousa
                </span>
              </div>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
