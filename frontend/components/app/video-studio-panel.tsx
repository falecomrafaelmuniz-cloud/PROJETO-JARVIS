'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Download,
  Film,
  ImagePlus,
  Loader2,
  Maximize2,
  Minimize2,
  Play,
  Sparkles,
  Trash2,
  Upload,
  Video,
  Wand2,
  X,
} from 'lucide-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

type SceneCategory = 'alerta' | 'risco' | 'dado' | 'solucao' | 'acao';

interface VideoScene {
  id: string;
  titulo_curto: string;
  categoria: SceneCategory;
  destaque_numero: string;
  texto_tela: string;
  narracao: string;
  prompt_imagem: string;
  duracao_seg: number;
  imagem_base64: string;
  caption_mode?: 'screen_box' | 'karaoke_narration' | 'none';
}

interface SavedVideoItem {
  filename: string;
  titulo: string;
  aspect_ratio: string;
  voice: string;
  duration_sec: number;
  scenes_count: number;
  size_bytes: number;
  created_at: string;
}

interface RenderJobState {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  step: string;
  output_file?: string;
  error?: string;
}

interface VideoStudioPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

const CATEGORY_LABELS: Record<SceneCategory, { label: string; badgeCls: string }> = {
  alerta: { label: 'Alerta', badgeCls: 'border-rose-400/50 bg-rose-500/20 text-rose-200' },
  risco: { label: 'Risco', badgeCls: 'border-amber-400/50 bg-amber-500/20 text-amber-200' },
  dado: { label: 'Fato', badgeCls: 'border-cyan-400/50 bg-cyan-500/20 text-cyan-200' },
  solucao: { label: 'Solução', badgeCls: 'border-emerald-400/50 bg-emerald-500/20 text-emerald-200' },
  acao: { label: 'Ação / Voto', badgeCls: 'border-teal-400/50 bg-teal-500/20 text-teal-200' },
};

async function compressImageToDataUrl(file: File, maxDim = 1080): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
  const targetW = Math.max(1, Math.round(bitmap.width * scale));
  const targetH = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement('canvas');
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    bitmap.close();
    throw new Error('Falha ao preparar imagem');
  }
  ctx.drawImage(bitmap, 0, 0, targetW, targetH);
  bitmap.close();
  return canvas.toDataURL('image/jpeg', 0.86);
}

export function VideoStudioPanel({ isOpen, onClose }: VideoStudioPanelProps) {
  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-videos-v1', false);
  const { style: dragStyle, isDragging, isResizingEdge, dragHandlers } =
    useDraggableFloating('jarvis-pos-video-studio-v2');

  const [tab, setTab] = useState<'create' | 'library'>('create');
  const [expanded, setExpanded] = useState(false);
  const [panelSize, setPanelSize] = useState<{ width: number; height: number }>({
    width: 1024,
    height: 760,
  });
  const savedSizeRef = useRef<{ width: number; height: number } | null>(null);
  const [idea, setIdea] = useState('');
  const [tone, setTone] = useState<'urgente_convincente' | 'informativo' | 'impactante'>(
    'urgente_convincente'
  );
  const [aspectRatio, setAspectRatio] = useState<'9:16' | '16:9'>('9:16');
  const [voice, setVoice] = useState<'Orus' | 'Kore' | 'Aoede'>('Orus');
  const [sceneCount, setSceneCount] = useState<number>(5);
  const [captionMode, setCaptionMode] = useState<'screen_box' | 'karaoke_narration' | 'none'>(
    'screen_box'
  );
  const [referenceImages, setReferenceImages] = useState<string[]>([]);

  const [videoTitle, setVideoTitle] = useState('');
  const [scenes, setScenes] = useState<VideoScene[]>([]);
  const [generatingScript, setGeneratingScript] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [jobState, setJobState] = useState<RenderJobState | null>(null);
  const [activeVideoUrl, setActiveVideoUrl] = useState<string | null>(null);
  const [activeFilename, setActiveFilename] = useState<string | null>(null);
  const [library, setLibrary] = useState<SavedVideoItem[]>([]);
  const [loadingLibrary, setLoadingLibrary] = useState(false);

  const pollTimerRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchLibrary = useCallback(async () => {
    setLoadingLibrary(true);
    try {
      const res = await fetch('/api/video-studio');
      if (res.ok) {
        const data = (await res.json()) as { videos?: SavedVideoItem[] };
        setLibrary(data.videos || []);
      }
    } catch {
      // ignore network error
    } finally {
      setLoadingLibrary(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      void fetchLibrary();
    }
  }, [isOpen, fetchLibrary]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('jarvis-size-video-studio-v1');
      if (saved) {
        const parsed = JSON.parse(saved) as { width?: number; height?: number };
        if (typeof parsed.width === 'number' && typeof parsed.height === 'number') {
          setPanelSize({
            width: Math.max(540, Math.min(window.innerWidth - 24, parsed.width)),
            height: Math.max(420, Math.min(window.innerHeight - 32, parsed.height)),
          });
          return;
        }
      }
    } catch {
      // ignore storage error
    }
    setPanelSize({
      width: Math.max(560, Math.min(1040, window.innerWidth - 32)),
      height: Math.max(460, Math.min(780, window.innerHeight - 64)),
    });
  }, []);

  const persistPanelSize = useCallback((size: { width: number; height: number }) => {
    try {
      localStorage.setItem('jarvis-size-video-studio-v1', JSON.stringify(size));
    } catch {
      // ignore storage error
    }
  }, []);

  const startEdgeResize = useCallback(
    (edge: 'w' | 'e' | 's' | 'sw' | 'se', e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();

      const startX = e.clientX;
      const startY = e.clientY;
      const startW = panelSize.width;
      const startH = panelSize.height;
      let latest = { width: startW, height: startH };

      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        const maxW = Math.max(560, window.innerWidth - 24);
        const maxH = Math.max(420, window.innerHeight - 28);

        let nextW = startW;
        let nextH = startH;
        if (edge === 'e' || edge === 'se') {
          nextW = Math.max(540, Math.min(maxW, startW + dx));
        } else if (edge === 'w' || edge === 'sw') {
          nextW = Math.max(540, Math.min(maxW, startW - dx));
        }
        if (edge === 's' || edge === 'se' || edge === 'sw') {
          nextH = Math.max(420, Math.min(maxH, startH + dy));
        }

        latest = { width: Math.round(nextW), height: Math.round(nextH) };
        setPanelSize(latest);
        setExpanded(false);
      };

      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        persistPanelSize(latest);
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
    },
    [panelSize.height, panelSize.width, persistPanelSize]
  );

  const handleToggleExpand = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next) {
        savedSizeRef.current = panelSize;
        const full = {
          width: Math.max(640, window.innerWidth - 32),
          height: Math.max(520, window.innerHeight - 56),
        };
        setPanelSize(full);
      } else if (savedSizeRef.current) {
        setPanelSize(savedSizeRef.current);
      } else {
        setPanelSize({
          width: Math.max(560, Math.min(1040, window.innerWidth - 32)),
          height: Math.max(460, Math.min(780, window.innerHeight - 64)),
        });
      }
      return next;
    });
  }, [panelSize]);

  const handleAttachRefs = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []).slice(0, 4 - referenceImages.length);
    if (!files.length) return;
    try {
      const converted = await Promise.all(files.map((f) => compressImageToDataUrl(f, 1080)));
      setReferenceImages((prev) => [...prev, ...converted].slice(0, 4));
    } catch {
      setErrorMsg('Não foi possível carregar uma das imagens de referência.');
    }
    e.target.value = '';
  };

  const handleGenerateScript = async () => {
    if (!idea.trim()) {
      setErrorMsg('Escreva primeiro a ideia ou o assunto do vídeo.');
      return;
    }
    setErrorMsg(null);
    setGeneratingScript(true);
    try {
      const res = await fetch('/api/video-studio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'generate_script',
          idea: idea.trim(),
          tone,
          aspect_ratio: aspectRatio,
          scene_count: sceneCount,
          caption_mode: captionMode,
          reference_images: referenceImages,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.error || 'Erro ao criar roteiro.');
      }
      setVideoTitle(data.titulo || 'Vídeo Jarvis');
      const rawScenes: VideoScene[] = Array.isArray(data.cenas) ? data.cenas : [];
      setScenes(
        rawScenes.map((sc) => ({
          ...sc,
          caption_mode: sc.caption_mode || captionMode,
        }))
      );
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Erro inesperado ao criar roteiro.');
    } finally {
      setGeneratingScript(false);
    }
  };

  const updateScene = (index: number, patch: Partial<VideoScene>) => {
    setScenes((prev) => prev.map((sc, i) => (i === index ? { ...sc, ...patch } : sc)));
  };

  const applyGlobalCaptionMode = (target: 'screen_box' | 'karaoke_narration') => {
    const next = captionMode === target ? 'none' : target;
    setCaptionMode(next);
    setScenes((prev) => prev.map((sc) => ({ ...sc, caption_mode: next })));
  };

  const handleAttachScenePhoto = async (
    index: number,
    e: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const dataUrl = await compressImageToDataUrl(file, 1080);
      updateScene(index, { imagem_base64: dataUrl });
    } catch {
      setErrorMsg('Falha ao carregar foto da cena.');
    }
    e.target.value = '';
  };

  const handleRenderVideo = async () => {
    if (!scenes.length) {
      setErrorMsg('Gere o roteiro antes de produzir o vídeo.');
      return;
    }
    setErrorMsg(null);
    stopPolling();
    setActiveVideoUrl(null);
    setActiveFilename(null);
    setJobState({
      job_id: '',
      status: 'queued',
      progress: 2,
      step: 'Enviando cenas para produção...',
    });

    try {
      const res = await fetch('/api/video-studio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'start_render',
          titulo: videoTitle.trim() || 'Vídeo Jarvis',
          aspect_ratio: aspectRatio,
          voice,
          caption_mode: captionMode,
          scenes,
        }),
      });
      const data = (await res.json()) as { jobId?: string; error?: string };
      if (!res.ok || !data.jobId) {
        throw new Error(data.error || 'Erro ao iniciar renderização.');
      }
      const jobId = data.jobId;
      pollTimerRef.current = window.setInterval(async () => {
        try {
          const pollRes = await fetch(`/api/video-studio?jobId=${encodeURIComponent(jobId)}`);
          if (!pollRes.ok) return;
          const statusData = (await pollRes.json()) as RenderJobState;
          setJobState(statusData);
          if (statusData.status === 'done' && statusData.output_file) {
            stopPolling();
            setActiveFilename(statusData.output_file);
            setActiveVideoUrl(
              `/api/video-studio?file=${encodeURIComponent(statusData.output_file)}&t=${Date.now()}`
            );
            void fetchLibrary();
          } else if (statusData.status === 'error') {
            stopPolling();
            setErrorMsg(statusData.error || 'Falha durante a renderização.');
          }
        } catch {
          // keep polling briefly
        }
      }, 1200);
    } catch (err: unknown) {
      stopPolling();
      setJobState(null);
      setErrorMsg(err instanceof Error ? err.message : 'Erro ao iniciar renderização.');
    }
  };

  const handleDeleteVideo = async (filename: string) => {
    try {
      const res = await fetch('/api/video-studio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', filename }),
      });
      if (res.ok) {
        setLibrary((prev) => prev.filter((item) => item.filename !== filename));
        if (activeFilename === filename) {
          setActiveFilename(null);
          setActiveVideoUrl(null);
        }
      }
    } catch {
      // ignore
    }
  };

  const applyCondoTemplate = () => {
    setIdea(
      'Assembleia online do nosso condomínio está aberta e precisa do voto dos moradores urgente. Mostre de forma direta e respeitosa o que piora e trava no prédio se o quórum não for atingido a tempo (obras/manutenção paradas, custos extras ou risco de multa), reforce que votar leva menos de 2 minutos pelo aplicativo/link oficial e chame o morador para votar hoje.'
    );
    setTone('urgente_convincente');
    setAspectRatio('9:16');
  };

  if (!isOpen) return null;

  const isRendering = jobState?.status === 'queued' || jobState?.status === 'running';
  const ideaRows = Math.max(4, Math.min(14, Math.round((panelSize.height - 420) / 28)));
  const baseSceneRows = Math.max(3, Math.min(7, Math.round((panelSize.height - 460) / 70)));

  return (
    <aside
      aria-label="Estúdio de Vídeos Jarvis"
      style={{
        ...(expanded ? { transform: 'none' } : dragStyle),
        width: `${panelSize.width}px`,
        height: minimized ? undefined : `${panelSize.height}px`,
      }}
      {...dragHandlers}
      title="Arraste pelo topo para mover · Puxe as bordas ou o canto inferior para redimensionar"
      className={cn(
        'pointer-events-auto absolute top-14 right-4 z-40 flex max-h-[calc(100vh-4.25rem)] max-w-[calc(100vw-2rem)] flex-col touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/35 bg-slate-950/95 text-slate-100 backdrop-blur-2xl',
        'shadow-[0_12px_45px_rgba(0,0,0,0.82)] ring-1 ring-cyan-400/25',
        isDragging || isResizingEdge ? 'ring-cyan-300/80 shadow-[0_0_35px_rgba(34,211,238,0.45)]' : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      <div className="pointer-events-none absolute inset-x-4 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/75 to-transparent" />

      {/* Alças de redimensionamento pelas bordas e cantos */}
      {!minimized && (
        <>
          <div
            data-no-drag="true"
            onPointerDown={(e) => startEdgeResize('w', e)}
            className="absolute inset-y-3 left-0 z-30 w-2.5 cursor-ew-resize hover:bg-cyan-400/20"
            title="Arraste para ajustar a largura"
          />
          <div
            data-no-drag="true"
            onPointerDown={(e) => startEdgeResize('e', e)}
            className="absolute inset-y-3 right-0 z-30 w-2.5 cursor-ew-resize hover:bg-cyan-400/20"
            title="Arraste para ajustar a largura"
          />
          <div
            data-no-drag="true"
            onPointerDown={(e) => startEdgeResize('s', e)}
            className="absolute inset-x-3 bottom-0 z-30 h-2.5 cursor-ns-resize hover:bg-cyan-400/20"
            title="Arraste para ajustar a altura"
          />
          <div
            data-no-drag="true"
            onPointerDown={(e) => startEdgeResize('sw', e)}
            className="absolute bottom-0 left-0 z-30 size-4 cursor-nesw-resize"
            title="Arraste para redimensionar"
          />
          <div
            data-no-drag="true"
            onPointerDown={(e) => startEdgeResize('se', e)}
            className="absolute right-0 bottom-0 z-30 flex size-5 cursor-nwse-resize items-end justify-end p-1 text-cyan-300/70 hover:text-cyan-200"
            title="Arraste para redimensionar"
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
              <path d="M9 1L1 9M9 5L5 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>
        </>
      )}

      {/* Header */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 bg-cyan-950/30 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-lg border border-cyan-400/40 bg-cyan-500/15 text-cyan-300">
            <Film className="size-4" />
          </div>
          <div>
            <h2 className="font-mono text-sm font-bold tracking-wider text-cyan-100 uppercase">
              Estúdio de Vídeos · Sob Demanda
            </h2>
            <p className="text-xs text-slate-400">
              Só usa recursos quando você clica · MP4 pronto para WhatsApp
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5" data-no-drag="true">
          <button
            type="button"
            onClick={handleToggleExpand}
            title={expanded ? 'Tamanho confortável padrão' : 'Expandir painel ao máximo'}
            aria-label={expanded ? 'Reduzir painel' : 'Expandir painel'}
            className="flex size-7 items-center justify-center rounded-md border border-cyan-400/25 bg-slate-900/80 text-cyan-200 transition hover:border-cyan-300/60 hover:bg-cyan-500/20"
          >
            {expanded ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
          </button>
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
            closeTitle="Fechar Estúdio de Vídeos (zero peso no Jarvis)"
          />
        </div>
      </div>

      {!minimized && (
        <div data-no-drag="true" className="flex min-h-0 flex-1 cursor-default flex-col">
          {/* Tabs */}
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/60 px-4 py-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setTab('create')}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold transition',
                  tab === 'create'
                    ? 'border-cyan-300/50 bg-cyan-500/20 text-cyan-100'
                    : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-cyan-400/35'
                )}
              >
                <Wand2 className="size-4" />
                Criar Vídeo
              </button>
              <button
                type="button"
                onClick={() => setTab('library')}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold transition',
                  tab === 'library'
                    ? 'border-cyan-300/50 bg-cyan-500/20 text-cyan-100'
                    : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-cyan-400/35'
                )}
              >
                <Video className="size-4" />
                Meus Vídeos ({library.length})
              </button>
            </div>

            {tab === 'create' && (
              <button
                type="button"
                onClick={applyCondoTemplate}
                className="rounded-lg border border-amber-400/35 bg-amber-500/15 px-2.5 py-1.5 text-xs font-semibold text-amber-200 transition hover:bg-amber-500/25"
              >
                Modelo: Assembleia / Prédio
              </button>
            )}
          </div>

          <div
            data-scrollable="true"
            className="min-h-0 flex-1 space-y-4 overflow-y-scroll p-4 pr-3 text-sm [&::-webkit-scrollbar]:w-2.5 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-slate-900/80 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-cyan-400/55 hover:[&::-webkit-scrollbar-thumb]:bg-cyan-300/80"
          >
            {errorMsg && (
              <div className="flex items-start justify-between gap-3 rounded-xl border border-rose-400/40 bg-rose-950/50 px-3.5 py-2.5 text-sm text-rose-100">
                <span>{errorMsg}</span>
                <button
                  type="button"
                  onClick={() => setErrorMsg(null)}
                  className="text-rose-300 hover:text-white"
                >
                  <X className="size-4" />
                </button>
              </div>
            )}

            {tab === 'create' ? (
              <>
                {/* Passo 1: Ideia e Configurações */}
                <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/55 p-3.5">
                  <div className="flex items-center justify-between">
                    <label
                      htmlFor="jarvis-video-idea"
                      className="text-sm font-bold text-cyan-100"
                    >
                      1. Qual é a ideia ou recado do vídeo?
                    </label>
                    <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-cyan-400/35 bg-cyan-500/15 px-2.5 py-1 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-500/25">
                      <Upload className="size-3.5" />
                      Anexar Prints/Fotos ({referenceImages.length}/4)
                      <input
                        type="file"
                        accept="image/*"
                        multiple
                        onChange={handleAttachRefs}
                        className="hidden"
                      />
                    </label>
                  </div>

                  <textarea
                    id="jarvis-video-idea"
                    rows={ideaRows}
                    value={idea}
                    onChange={(e) => setIdea(e.target.value)}
                    placeholder="Ex: Assembleia do condomínio aberta. Quero explicar aos moradores os riscos reais se não votarem na aprovação das contas e da reforma do telhado esta semana..."
                    className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950/90 p-3 text-base leading-relaxed text-slate-100 placeholder:text-slate-500 focus:border-cyan-400 focus:outline-none"
                  />

                  {referenceImages.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {referenceImages.map((src, i) => (
                        <div
                          key={i}
                          className="relative h-16 w-16 overflow-hidden rounded-lg border border-cyan-400/40"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={src}
                            alt={`Referência ${i + 1}`}
                            className="h-full w-full object-cover"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              setReferenceImages((prev) => prev.filter((_, idx) => idx !== i))
                            }
                            className="absolute top-0.5 right-0.5 rounded-full bg-black/80 p-0.5 text-white hover:bg-rose-600"
                          >
                            <X className="size-3" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                    <div>
                      <span className="mb-1 block text-xs font-semibold text-slate-300">
                        Formato
                      </span>
                      <select
                        value={aspectRatio}
                        onChange={(e) => setAspectRatio(e.target.value as '9:16' | '16:9')}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
                      >
                        <option value="9:16">9:16 (WhatsApp/Celular)</option>
                        <option value="16:9">16:9 (Tela Larga)</option>
                      </select>
                    </div>

                    <div>
                      <span className="mb-1 block text-xs font-semibold text-slate-300">
                        Tom do Vídeo
                      </span>
                      <select
                        value={tone}
                        onChange={(e) =>
                          setTone(
                            e.target.value as
                              | 'urgente_convincente'
                              | 'informativo'
                              | 'impactante'
                          )
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
                      >
                        <option value="urgente_convincente">Urgente & Convincente</option>
                        <option value="informativo">Claro & Informativo</option>
                        <option value="impactante">Impacto Rápido</option>
                      </select>
                    </div>

                    <div>
                      <span className="mb-1 block text-xs font-semibold text-slate-300">
                        Voz (PT-BR)
                      </span>
                      <select
                        value={voice}
                        onChange={(e) => setVoice(e.target.value as 'Orus' | 'Kore' | 'Aoede')}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
                      >
                        <option value="Orus">Orus (Firme)</option>
                        <option value="Kore">Kore (Clara)</option>
                        <option value="Aoede">Aoede (Dinâmica)</option>
                      </select>
                    </div>

                    <div>
                      <span className="mb-1 block text-xs font-semibold text-slate-300">
                        Cenas
                      </span>
                      <select
                        value={sceneCount}
                        onChange={(e) => setSceneCount(Number(e.target.value))}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 focus:border-cyan-400 focus:outline-none"
                      >
                        <option value={4}>4 cenas (~30s)</option>
                        <option value={5}>5 cenas (~40s)</option>
                        <option value={6}>6 cenas (~50s)</option>
                      </select>
                    </div>
                  </div>

                  <div className="space-y-2 rounded-xl border border-slate-700/80 bg-slate-950/75 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-bold text-cyan-100">
                        Legendas no Vídeo (escolha 1 ou desmarque as duas para sem legenda)
                      </span>
                      {captionMode === 'none' && (
                        <span className="rounded-full border border-slate-600 bg-slate-800 px-2.5 py-0.5 text-xs font-semibold text-slate-300">
                          Sem legenda na tela (vídeo limpo)
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <button
                        type="button"
                        onClick={() => applyGlobalCaptionMode('screen_box')}
                        aria-pressed={captionMode === 'screen_box'}
                        className={cn(
                          'flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm font-semibold transition',
                          captionMode === 'screen_box'
                            ? 'border-cyan-400 bg-cyan-500/20 text-cyan-50'
                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-500'
                        )}
                      >
                        <span
                          className={cn(
                            'flex size-5 shrink-0 items-center justify-center rounded border text-xs font-bold',
                            captionMode === 'screen_box'
                              ? 'border-cyan-300 bg-cyan-400 text-slate-950'
                              : 'border-slate-600 bg-slate-950 text-transparent'
                          )}
                        >
                          ✓
                        </span>
                        <span>
                          Caixa grande na tela
                          <span className="block text-xs font-normal text-slate-400">
                            Mostra o texto curto em destaque fixo
                          </span>
                        </span>
                      </button>

                      <button
                        type="button"
                        onClick={() => applyGlobalCaptionMode('karaoke_narration')}
                        aria-pressed={captionMode === 'karaoke_narration'}
                        className={cn(
                          'flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm font-semibold transition',
                          captionMode === 'karaoke_narration'
                            ? 'border-amber-300 bg-amber-500/20 text-amber-50'
                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-500'
                        )}
                      >
                        <span
                          className={cn(
                            'flex size-5 shrink-0 items-center justify-center rounded border text-xs font-bold',
                            captionMode === 'karaoke_narration'
                              ? 'border-amber-300 bg-amber-300 text-slate-950'
                              : 'border-slate-600 bg-slate-950 text-transparent'
                          )}
                        >
                          ✓
                        </span>
                        <span>
                          Narração falada (sincronizada)
                          <span className="block text-xs font-normal text-slate-400">
                            Destaca cada palavra junto com a voz
                          </span>
                        </span>
                      </button>
                    </div>
                  </div>

                  <button
                    type="button"
                    disabled={generatingScript || isRendering}
                    onClick={handleGenerateScript}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-cyan-300/50 bg-cyan-500/25 px-4 py-2.5 text-base font-bold text-cyan-50 transition hover:bg-cyan-500/35 disabled:opacity-50"
                  >
                    {generatingScript ? (
                      <>
                        <Loader2 className="size-5 animate-spin" />
                        Escrevendo Roteiro por Cenas...
                      </>
                    ) : (
                      <>
                        <Sparkles className="size-5" />
                        Criar Roteiro por Cenas
                      </>
                    )}
                  </button>
                </div>

                {/* Passo 2: Edição de Cenas */}
                {scenes.length > 0 && (
                  <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/55 p-3.5">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <span className="text-sm font-bold text-cyan-100">
                        2. Revise as Cenas e anexe fotos reais se quiser
                      </span>
                      <input
                        type="text"
                        value={videoTitle}
                        onChange={(e) => setVideoTitle(e.target.value)}
                        placeholder="Título do vídeo"
                        className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm font-semibold text-white focus:border-cyan-400 focus:outline-none sm:w-64"
                      />
                    </div>

                    <div className="space-y-2.5">
                      {scenes.map((sc, idx) => {
                        const catMeta = CATEGORY_LABELS[sc.categoria] || CATEGORY_LABELS.dado;
                        return (
                          <div
                            key={sc.id || idx}
                            className="space-y-2 rounded-xl border border-slate-700/80 bg-slate-950/75 p-3"
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-xs font-bold text-cyan-300">
                                  CENA {idx + 1}
                                </span>
                                <span
                                  className={cn(
                                    'rounded-full border px-2 py-0.5 text-xs font-semibold',
                                    catMeta.badgeCls
                                  )}
                                >
                                  {catMeta.label}
                                </span>
                                <input
                                  type="text"
                                  value={sc.destaque_numero}
                                  onChange={(e) =>
                                    updateScene(idx, { destaque_numero: e.target.value })
                                  }
                                  placeholder="Selo destaque"
                                  className="w-36 rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs font-semibold text-slate-200 focus:border-cyan-400 focus:outline-none"
                                />
                              </div>

                              <div className="flex items-center gap-2">
                                {sc.imagem_base64 ? (
                                  <div className="flex items-center gap-1.5 rounded-lg border border-emerald-400/40 bg-emerald-500/15 px-2 py-1 text-xs text-emerald-200">
                                    <span>Foto anexada</span>
                                    <button
                                      type="button"
                                      onClick={() => updateScene(idx, { imagem_base64: '' })}
                                      title="Remover foto desta cena"
                                      className="text-emerald-200 hover:text-rose-300"
                                    >
                                      <X className="size-3.5" />
                                    </button>
                                  </div>
                                ) : (
                                  <label className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-600 bg-slate-900 px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-cyan-400/50 hover:text-cyan-100">
                                    <ImagePlus className="size-3.5" />
                                    Foto/Print nesta cena
                                    <input
                                      type="file"
                                      accept="image/*"
                                      onChange={(e) => handleAttachScenePhoto(idx, e)}
                                      className="hidden"
                                    />
                                  </label>
                                )}
                              </div>
                            </div>

                            {sc.imagem_base64 && (
                              <div className="h-24 w-32 overflow-hidden rounded-lg border border-slate-700">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  src={sc.imagem_base64}
                                  alt={`Cena ${idx + 1}`}
                                  className="h-full w-full object-cover"
                                />
                              </div>
                            )}

                            <div className="grid gap-3 sm:grid-cols-2">
                              <div>
                                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                                  <label className="text-xs font-semibold text-slate-300">
                                    Texto Grande na Tela
                                  </label>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      const cur = sc.caption_mode ?? captionMode;
                                      updateScene(idx, {
                                        caption_mode: cur === 'screen_box' ? 'none' : 'screen_box',
                                      });
                                    }}
                                    aria-pressed={(sc.caption_mode ?? captionMode) === 'screen_box'}
                                    className={cn(
                                      'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold transition',
                                      (sc.caption_mode ?? captionMode) === 'screen_box'
                                        ? 'border-cyan-400 bg-cyan-500/25 text-cyan-100'
                                        : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200'
                                    )}
                                  >
                                    <span
                                      className={cn(
                                        'flex size-3.5 items-center justify-center rounded-sm border text-[10px] font-bold',
                                        (sc.caption_mode ?? captionMode) === 'screen_box'
                                          ? 'border-cyan-300 bg-cyan-400 text-slate-950'
                                          : 'border-slate-600 bg-slate-950 text-transparent'
                                      )}
                                    >
                                      ✓
                                    </span>
                                    Usar como legenda
                                  </button>
                                </div>
                                <textarea
                                  rows={Math.max(
                                    baseSceneRows,
                                    Math.min(8, Math.ceil((sc.texto_tela.length || 1) / 36))
                                  )}
                                  value={sc.texto_tela}
                                  onChange={(e) =>
                                    updateScene(idx, { texto_tela: e.target.value })
                                  }
                                  className="w-full resize-y rounded-lg border border-slate-700 bg-slate-900 p-2.5 text-base leading-snug font-medium text-white focus:border-cyan-400 focus:outline-none"
                                />
                              </div>
                              <div>
                                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                                  <label className="text-xs font-semibold text-slate-300">
                                    Narração Falada (Locutor)
                                  </label>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      const cur = sc.caption_mode ?? captionMode;
                                      updateScene(idx, {
                                        caption_mode:
                                          cur === 'karaoke_narration' ? 'none' : 'karaoke_narration',
                                      });
                                    }}
                                    aria-pressed={
                                      (sc.caption_mode ?? captionMode) === 'karaoke_narration'
                                    }
                                    className={cn(
                                      'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold transition',
                                      (sc.caption_mode ?? captionMode) === 'karaoke_narration'
                                        ? 'border-amber-300 bg-amber-500/25 text-amber-100'
                                        : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200'
                                    )}
                                  >
                                    <span
                                      className={cn(
                                        'flex size-3.5 items-center justify-center rounded-sm border text-[10px] font-bold',
                                        (sc.caption_mode ?? captionMode) === 'karaoke_narration'
                                          ? 'border-amber-300 bg-amber-300 text-slate-950'
                                          : 'border-slate-600 bg-slate-950 text-transparent'
                                      )}
                                    >
                                      ✓
                                    </span>
                                    Legenda sincronizada
                                  </button>
                                </div>
                                <textarea
                                  rows={Math.max(
                                    baseSceneRows,
                                    Math.min(10, Math.ceil((sc.narracao.length || 1) / 42))
                                  )}
                                  value={sc.narracao}
                                  onChange={(e) => updateScene(idx, { narracao: e.target.value })}
                                  className="w-full resize-y rounded-lg border border-slate-700 bg-slate-900 p-2.5 text-base leading-snug text-slate-100 focus:border-cyan-400 focus:outline-none"
                                />
                              </div>
                            </div>
                            {(sc.caption_mode ?? captionMode) === 'none' && (
                              <p className="text-xs font-medium text-slate-400">
                                Sem legenda nesta cena (as duas opções acima estão desmarcadas).
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    <button
                      type="button"
                      disabled={isRendering}
                      onClick={handleRenderVideo}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-emerald-300/50 bg-emerald-500/25 px-4 py-3 text-base font-bold text-emerald-50 transition hover:bg-emerald-500/35 disabled:opacity-50"
                    >
                      {isRendering ? (
                        <>
                          <Loader2 className="size-5 animate-spin" />
                          Produzindo Vídeo MP4 ({jobState?.progress ?? 0}%)...
                        </>
                      ) : (
                        <>
                          <Play className="size-5" />
                          Gerar Vídeo MP4 Agora
                        </>
                      )}
                    </button>

                    {jobState && (jobState.status === 'queued' || jobState.status === 'running') && (
                      <div className="space-y-1.5 rounded-xl border border-cyan-500/30 bg-slate-950/90 p-3">
                        <div className="flex items-center justify-between text-xs font-semibold text-cyan-200">
                          <span>{jobState.step}</span>
                          <span>{jobState.progress}%</span>
                        </div>
                        <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-emerald-400 transition-all duration-300"
                            style={{ width: `${Math.max(4, jobState.progress)}%` }}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Passo 3: Player & Download */}
                {activeVideoUrl && (
                  <div className="space-y-3 rounded-xl border border-emerald-400/40 bg-emerald-950/20 p-3.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-base font-bold text-emerald-200">
                        Vídeo Pronto para WhatsApp
                      </span>
                      {activeFilename && (
                        <a
                          href={`/api/video-studio?file=${encodeURIComponent(activeFilename)}&download=1`}
                          download={activeFilename}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300/50 bg-emerald-500/25 px-3.5 py-1.5 text-sm font-bold text-emerald-50 hover:bg-emerald-500/35"
                        >
                          <Download className="size-4" />
                          Baixar MP4
                        </a>
                      )}
                    </div>
                    <video
                      key={activeVideoUrl}
                      src={activeVideoUrl}
                      controls
                      className="mx-auto max-h-[380px] w-auto rounded-xl border border-slate-700 bg-black"
                    />
                  </div>
                )}
              </>
            ) : (
              /* Aba Biblioteca */
              <div className="space-y-3">
                {activeVideoUrl && (
                  <div className="space-y-2 rounded-xl border border-cyan-400/35 bg-slate-900/75 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-bold text-cyan-100">{activeFilename}</span>
                      {activeFilename && (
                        <a
                          href={`/api/video-studio?file=${encodeURIComponent(activeFilename)}&download=1`}
                          download={activeFilename}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300/45 bg-emerald-500/20 px-3 py-1 text-xs font-bold text-emerald-100 hover:bg-emerald-500/35"
                        >
                          <Download className="size-3.5" />
                          Baixar MP4
                        </a>
                      )}
                    </div>
                    <video
                      key={activeVideoUrl}
                      src={activeVideoUrl}
                      controls
                      className="mx-auto max-h-[340px] w-auto rounded-lg border border-slate-700 bg-black"
                    />
                  </div>
                )}

                {loadingLibrary ? (
                  <div className="flex items-center justify-center py-8 text-slate-400">
                    <Loader2 className="mr-2 size-5 animate-spin" />
                    Carregando vídeos salvos...
                  </div>
                ) : library.length === 0 ? (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-6 text-center text-slate-400">
                    Nenhum vídeo criado ainda. Vá na aba <strong>Criar Vídeo</strong> para gerar o
                    primeiro!
                  </div>
                ) : (
                  <div className="space-y-2">
                    {library.map((item) => (
                      <div
                        key={item.filename}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-900/65 p-3"
                      >
                        <div>
                          <p className="text-sm font-bold text-white">{item.titulo}</p>
                          <p className="text-xs text-slate-400">
                            {item.aspect_ratio} · {item.duration_sec}s ·{' '}
                            {(item.size_bytes / (1024 * 1024)).toFixed(1)} MB ·{' '}
                            {item.created_at.replace('T', ' ')}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setActiveFilename(item.filename);
                              setActiveVideoUrl(
                                `/api/video-studio?file=${encodeURIComponent(item.filename)}`
                              );
                            }}
                            className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/40 bg-cyan-500/15 px-3 py-1.5 text-xs font-bold text-cyan-100 hover:bg-cyan-500/25"
                          >
                            <Play className="size-3.5" />
                            Assistir
                          </button>
                          <a
                            href={`/api/video-studio?file=${encodeURIComponent(item.filename)}&download=1`}
                            download={item.filename}
                            className="inline-flex items-center gap-1 rounded-lg border border-emerald-400/40 bg-emerald-500/15 px-3 py-1.5 text-xs font-bold text-emerald-100 hover:bg-emerald-500/25"
                          >
                            <Download className="size-3.5" />
                            Baixar
                          </a>
                          <button
                            type="button"
                            onClick={() => handleDeleteVideo(item.filename)}
                            title="Excluir vídeo"
                            className="rounded-lg border border-rose-400/35 bg-rose-500/15 p-1.5 text-rose-200 hover:bg-rose-500/30"
                          >
                            <Trash2 className="size-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
