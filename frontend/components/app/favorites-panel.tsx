'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  AppWindow,
  Bookmark,
  Check,
  Edit2,
  ExternalLink,
  FileText,
  Folder,
  Globe,
  Plus,
  Search,
  Settings,
  Star,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const FAVORITES_TOPIC = 'jarvis.favorites';

export interface FavoriteItem {
  id: string;
  nome: string;
  url: string;
  tipo?: 'site' | 'app' | 'pasta' | 'arquivo' | 'sistema' | 'script';
  categoria?: string;
  criado_em?: string;
  atualizado_em?: string;
  ordem?: number;
}

interface FavoritesPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenRequest?: () => void;
}

function extractDomain(url: string): string {
  try {
    const u = url.startsWith('http') ? url : `https://${url}`;
    const parsed = new URL(u);
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

export function FavoritesPanel({ isOpen, onClose, onOpenRequest }: FavoritesPanelProps) {
  const room = useRoomContext();
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('todos');
  const [showAddForm, setShowAddForm] = useState(false);

  // Form states para adicionar
  const [novoTipo, setNovoTipo] = useState<'site' | 'app' | 'pasta' | 'arquivo' | 'sistema' | 'script'>('site');
  const [novoNome, setNovoNome] = useState('');
  const [novaUrl, setNovaUrl] = useState('');
  const [novaCategoria, setNovaCategoria] = useState('Geral');
  const [saving, setSaving] = useState(false);
  const [browsing, setBrowsing] = useState(false);

  // Edit states para renomear/editar
  const [editingItem, setEditingItem] = useState<FavoriteItem | null>(null);
  const [editNome, setEditNome] = useState('');
  const [editUrl, setEditUrl] = useState('');
  const [editCategoria, setEditCategoria] = useState('');
  const [updating, setUpdating] = useState(false);

  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-favorites-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-favorites-panel-v1');

  const fetchFavorites = useCallback(async () => {
    try {
      const res = await fetch('/api/favorites', { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.favorites)) {
          setFavorites(json.favorites);
        }
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchFavorites();
    }
  }, [isOpen, fetchFavorites]);

  // Listener para sincronização em tempo real via LiveKit DataPacket (quando o Jarvis altera por voz)
  useEffect(() => {
    if (!room) return;
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== FAVORITES_TOPIC) return;
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);
        if (data.type === 'jarvis_favorites_sync' && Array.isArray(data.favorites)) {
          setFavorites(data.favorites);
          if (data.open_panel && onOpenRequest) {
            onOpenRequest();
          }
        }
      } catch {
        // ignore
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room, onOpenRequest]);

  // Categorias disponíveis dinâmicas
  const categories = useMemo(() => {
    const cats = new Set<string>();
    favorites.forEach((f) => {
      if (f.categoria && f.categoria.trim()) {
        cats.add(f.categoria.trim());
      }
    });
    return ['todos', ...Array.from(cats)];
  }, [favorites]);

  // Filtragem de busca e categoria
  const filteredFavorites = useMemo(() => {
    return favorites.filter((f) => {
      const matchCat =
        selectedCategory === 'todos' ||
        (f.categoria || 'Geral').toLowerCase() === selectedCategory.toLowerCase();
      const s = search.toLowerCase().trim();
      const matchSearch =
        !s ||
        f.nome.toLowerCase().includes(s) ||
        f.url.toLowerCase().includes(s) ||
        (f.categoria || '').toLowerCase().includes(s);
      return matchCat && matchSearch;
    });
  }, [favorites, selectedCategory, search]);

  const handleSalvarNovo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!novaUrl.trim()) return;
    setSaving(true);
    const catDefaults: Record<string, string> = {
      site: 'Sites',
      app: 'Aplicativos',
      pasta: 'Pastas',
      arquivo: 'Arquivos',
      sistema: 'Sistema',
      script: 'Scripts',
    };
    const catFinal = novaCategoria.trim() || catDefaults[novoTipo] || 'Geral';
    const rawNome = novoNome.trim() || novaUrl.trim();
    const nomeFinal = rawNome.toUpperCase();
    try {
      const res = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'add',
          tipo: novoTipo,
          nome: nomeFinal,
          url: novaUrl.trim(),
          categoria: catFinal,
        }),
      });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.favorites)) {
          setFavorites(json.favorites);
        }
        setNovoNome('');
        setNovaUrl('');
        setNovaCategoria(catDefaults[novoTipo] || 'Geral');
        setShowAddForm(false);
      } else {
        const errJson = await res.json().catch(() => ({}));
        alert(errJson.message || 'Não foi possível salvar o atalho. Tente novamente.');
      }
    } catch (err) {
      console.error('Erro ao salvar:', err);
      alert('Falha na comunicação com o servidor ao salvar.');
    } finally {
      setSaving(false);
    }
  };

  const handleIniciarEdicao = (fav: FavoriteItem) => {
    setEditingItem(fav);
    setEditNome(fav.nome.toUpperCase());
    setEditUrl(fav.url);
    setEditCategoria(fav.categoria || 'Geral');
  };

  const handleSalvarEdicao = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingItem) return;
    setUpdating(true);
    try {
      const res = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'update',
          id: editingItem.id,
          nome: editNome.trim().toUpperCase(),
          url: editingItem.tipo === 'site' ? editUrl.trim() : editingItem.url,
          categoria: editCategoria.trim() || 'Geral',
        }),
      });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.favorites)) {
          setFavorites(json.favorites);
        }
        setEditingItem(null);
      }
    } catch {
      // ignore
    } finally {
      setUpdating(false);
    }
  };

  const handleRemover = async (id: string) => {
    try {
      const res = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', id }),
      });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.favorites)) {
          setFavorites(json.favorites);
        }
      }
    } catch {
      // ignore
    }
  };

  const handleAbrirUrl = (fav: FavoriteItem) => {
    const isWeb = fav.tipo === 'site' || fav.url.startsWith('http://') || fav.url.startsWith('https://');

    if (room && room.localParticipant) {
      try {
        const payload = { action: 'open_url', url: fav.url, nome: fav.nome, tipo: fav.tipo };
        room.localParticipant.publishData(
          new TextEncoder().encode(JSON.stringify(payload)),
          { reliable: true, topic: FAVORITES_TOPIC }
        );
      } catch {}
    }

    if (!isWeb) {
      fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'open', url: fav.url, tipo: fav.tipo }),
      }).catch(() => {});
      return;
    }

    window.open(fav.url, '_blank', 'noopener,noreferrer');
  };

  const handleColarClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text && (text.startsWith('http://') || text.startsWith('https://') || text.includes('.'))) {
        setNovaUrl(text.trim());
        if (!novoNome) {
          const dom = extractDomain(text);
          setNovoNome(dom.charAt(0).toUpperCase() + dom.slice(1));
        }
      }
    } catch {
      // ignore
    }
  };

  const handleBrowseFolder = async () => {
    setBrowsing(true);
    try {
      const res = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'browse_folder' }),
      });
      if (res.ok) {
        const json = await res.json();
        if (json.status === 'ok' && json.path) {
          setNovaUrl(json.path);
          const cleanName = String(json.name || '').replace(/^(a\s+)?pasta\s+/i, '').trim();
          setNovoNome(cleanName ? cleanName.toUpperCase() : 'PASTA');
          setNovaCategoria('Pastas');
        }
      }
    } catch {
      // ignore
    } finally {
      setBrowsing(false);
    }
  };

  const handleBrowseFile = async () => {
    setBrowsing(true);
    try {
      const res = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'browse_file' }),
      });
      if (res.ok) {
        const json = await res.json();
        if (json.status === 'ok' && json.path) {
          setNovaUrl(json.path);
          if (!novoNome) {
            setNovoNome(json.name ? json.name.toUpperCase() : 'ARQUIVO');
          }
          setNovaCategoria('Arquivos');
        }
      }
    } catch {
      // ignore
    } finally {
      setBrowsing(false);
    }
  };

  if (!isOpen) return null;

  return (
    <aside
      aria-label="Atalhos"
      style={dragStyle}
      data-hud-panel="favorites"
      {...dragHandlers}
      title="Arraste pela barra de título para mover · Arraste pelas bordas para redimensionar"
      className={cn(
        'pointer-events-auto absolute top-16 right-3 z-30 w-[min(26rem,calc(100vw-1.5rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/35 bg-black/85 text-white backdrop-blur-2xl',
        'shadow-[0_0_24px_rgba(6,182,212,0.22)] shadow-[0_12px_40px_rgba(0,0,0,0.7)] ring-1 ring-cyan-500/25 transition-all duration-200',
        isDragging || isResizingEdge
          ? 'shadow-[0_0_30px_rgba(6,182,212,0.45)] ring-1 ring-cyan-300/80'
          : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Linhas holográficas estilo Jarvis HUD */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/70 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-16 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />

      {/* Cabeçalho */}
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3.5 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-500/10 text-cyan-300">
            <Star className="size-4 fill-cyan-400/20" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-wide text-white uppercase truncate">
              Atalhos
            </h2>
            <p className="text-[11px] text-cyan-300/60 truncate">
              {favorites.length} {favorites.length === 1 ? 'atalho salvo (sites e apps)' : 'atalhos salvos (sites e apps)'}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5" data-no-drag="true">
          <button
            type="button"
            onClick={() => setShowAddForm((v) => !v)}
            title={showAddForm ? 'Cancelar adição' : 'Adicionar novo site'}
            className={cn(
              'flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition cursor-pointer',
              showAddForm
                ? 'border-cyan-400 bg-cyan-500/30 text-white'
                : 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300 hover:border-cyan-400/60 hover:bg-cyan-500/20'
            )}
          >
            <Plus className={cn('size-3.5 transition-transform', showAddForm ? 'rotate-45' : '')} />
            <span className="hidden sm:inline">{showAddForm ? 'Fechar' : 'Novo'}</span>
          </button>

          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
          />
        </div>
      </div>

      {!minimized && (
        <div className="flex flex-col max-h-[calc(85vh-6rem)]">
          {/* Barra de Busca e Categorias */}
          <div className="border-b border-white/10 p-2.5 space-y-2 bg-black/40" data-no-drag="true">
            {/* Input de Busca */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-cyan-300/50" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Buscar por nome, link ou categoria..."
                className="w-full rounded-lg border border-white/10 bg-white/5 py-1.5 pl-8 pr-7 text-xs text-white placeholder-white/40 focus:border-cyan-400 focus:bg-black/60 focus:outline-none"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-white/40 hover:text-white"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>

            {/* Categorias (Pills) */}
            {categories.length > 2 && (
              <div className="flex items-center gap-1 overflow-x-auto no-scrollbar py-0.5 text-[11px]">
                {categories.map((cat) => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setSelectedCategory(cat)}
                    className={cn(
                      'shrink-0 rounded-full px-2.5 py-0.5 capitalize transition cursor-pointer',
                      selectedCategory.toLowerCase() === cat.toLowerCase()
                        ? 'border border-cyan-400/60 bg-cyan-500/25 text-cyan-200 font-semibold'
                        : 'border border-white/10 bg-white/5 text-white/60 hover:text-white hover:border-white/20'
                    )}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Formulário de Adicionar Favorito */}
          {showAddForm && (
            <form
              onSubmit={handleSalvarNovo}
              className="border-b border-cyan-400/20 bg-cyan-950/25 p-3 space-y-2.5 text-xs"
              data-no-drag="true"
            >
              {/* Abas: Site, App, Pasta, Arquivo, Sistema */}
              <div className="flex items-center justify-between gap-1">
                <div className="flex items-center gap-1 overflow-x-auto no-scrollbar bg-black/60 p-0.5 rounded-lg border border-white/10 text-[10px]">
                  <button
                    type="button"
                    onClick={() => {
                      setNovoTipo('site');
                      setNovaCategoria('Sites');
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded-md transition font-medium cursor-pointer',
                      novoTipo === 'site'
                        ? 'bg-cyan-500/30 text-cyan-200 border border-cyan-400/50'
                        : 'text-white/60 hover:text-white'
                    )}
                  >
                    <Globe className="size-3" />
                    <span>Site</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNovoTipo('app');
                      setNovaCategoria('Aplicativos');
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded-md transition font-medium cursor-pointer',
                      novoTipo === 'app'
                        ? 'bg-purple-500/30 text-purple-200 border border-purple-400/50'
                        : 'text-white/60 hover:text-white'
                    )}
                  >
                    <AppWindow className="size-3" />
                    <span>App</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNovoTipo('pasta');
                      setNovaCategoria('Pastas');
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded-md transition font-medium cursor-pointer',
                      novoTipo === 'pasta'
                        ? 'bg-amber-500/30 text-amber-200 border border-amber-400/50'
                        : 'text-white/60 hover:text-white'
                    )}
                  >
                    <Folder className="size-3" />
                    <span>Pasta</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNovoTipo('arquivo');
                      setNovaCategoria('Arquivos');
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded-md transition font-medium cursor-pointer',
                      novoTipo === 'arquivo'
                        ? 'bg-emerald-500/30 text-emerald-200 border border-emerald-400/50'
                        : 'text-white/60 hover:text-white'
                    )}
                  >
                    <FileText className="size-3" />
                    <span>Arquivo</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNovoTipo('sistema');
                      setNovaCategoria('Sistema');
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded-md transition font-medium cursor-pointer',
                      novoTipo === 'sistema'
                        ? 'bg-orange-500/30 text-orange-200 border border-orange-400/50'
                        : 'text-white/60 hover:text-white'
                    )}
                  >
                    <Settings className="size-3" />
                    <span>Sistema</span>
                  </button>
                </div>

                {novoTipo === 'site' && (
                  <button
                    type="button"
                    onClick={handleColarClipboard}
                    title="Colar link da área de transferência"
                    className="shrink-0 text-cyan-400/80 hover:text-cyan-200 underline text-[10px] cursor-pointer"
                  >
                    Colar Link
                  </button>
                )}
              </div>

              {/* Botões de Acesso Rápido / Sugestões conforme o tipo */}
              {novoTipo === 'app' && (
                <div className="space-y-1">
                  <span className="text-[10px] text-purple-300/70 font-medium">Programas sugeridos:</span>
                  <div className="flex flex-wrap gap-1">
                    {['Calculadora', 'Bloco de Notas', 'Excel', 'Word', 'Cortecloud', 'WhatsApp', 'Spotify'].map((appSug) => (
                      <button
                        key={appSug}
                        type="button"
                        onClick={() => {
                          setNovaUrl(appSug);
                          setNovoNome(appSug);
                          setNovaCategoria('Aplicativos');
                        }}
                        className="rounded-md border border-purple-500/30 bg-purple-950/40 px-2 py-0.5 text-[10px] text-purple-200 hover:bg-purple-500/20 hover:border-purple-400 cursor-pointer"
                      >
                        +{appSug}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {novoTipo === 'pasta' && (
                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={handleBrowseFolder}
                    disabled={browsing}
                    className="w-full flex items-center justify-center gap-2 rounded-lg border border-amber-400/50 bg-amber-500/20 hover:bg-amber-500/30 py-2 px-3 text-amber-200 font-semibold text-xs transition cursor-pointer shadow-xs active:scale-[0.98]"
                  >
                    <Folder className="size-4 text-amber-300" />
                    <span>{browsing ? 'Selecione a pasta na janela aberta...' : '📁 Escolher Pasta no Explorador de Arquivos...'}</span>
                  </button>
                  <div className="space-y-1">
                    <span className="text-[10px] text-amber-300/70 font-medium">Ou escolha uma pasta comum:</span>
                    <div className="flex flex-wrap gap-1">
                      {['Downloads', 'Documentos', 'Área de Trabalho', 'Imagens', 'Vídeos'].map((pSug) => (
                        <button
                          key={pSug}
                          type="button"
                          onClick={() => {
                            setNovaUrl(pSug);
                            setNovoNome(pSug.toUpperCase());
                            setNovaCategoria('Pastas');
                          }}
                          className="rounded-md border border-amber-500/30 bg-amber-950/40 px-2 py-0.5 text-[10px] text-amber-200 hover:bg-amber-500/20 hover:border-amber-400 cursor-pointer"
                        >
                          +{pSug}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {novoTipo === 'arquivo' && (
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={handleBrowseFile}
                    disabled={browsing}
                    className="w-full flex items-center justify-center gap-2 rounded-lg border border-emerald-400/50 bg-emerald-500/20 hover:bg-emerald-500/30 py-2 px-3 text-emerald-200 font-semibold text-xs transition cursor-pointer shadow-xs active:scale-[0.98]"
                  >
                    <FileText className="size-4 text-emerald-300" />
                    <span>{browsing ? 'Selecione o arquivo na janela aberta...' : '📄 Escolher Arquivo no Computador...'}</span>
                  </button>
                </div>
              )}

              {novoTipo === 'sistema' && (
                <div className="space-y-1">
                  <span className="text-[10px] text-orange-300/70 font-medium">Ferramentas do Sistema:</span>
                  <div className="flex flex-wrap gap-1">
                    {[
                      { l: 'Painel de Controle', c: 'control' },
                      { l: 'Configurações', c: 'ms-settings:' },
                      { l: 'Gerenciador de Tarefas', c: 'taskmgr' },
                      { l: 'Prompt CMD', c: 'cmd' },
                      { l: 'PowerShell', c: 'powershell' },
                    ].map((sSug) => (
                      <button
                        key={sSug.l}
                        type="button"
                        onClick={() => {
                          setNovaUrl(sSug.c);
                          setNovoNome(sSug.l.toUpperCase());
                          setNovaCategoria('Sistema');
                        }}
                        className="rounded-md border border-orange-500/30 bg-orange-950/40 px-2 py-0.5 text-[10px] text-orange-200 hover:bg-orange-500/20 hover:border-orange-400 cursor-pointer"
                      >
                        +{sSug.l}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 gap-2">
                <div>
                  <input
                    type="text"
                    required
                    value={novaUrl}
                    onChange={(e) => setNovaUrl(e.target.value)}
                    placeholder={
                      novoTipo === 'site'
                        ? 'URL do site (ex: https://youtube.com)'
                        : novoTipo === 'pasta'
                        ? 'Nome ou caminho da pasta (ex: Downloads, C:\\Projetos)'
                        : novoTipo === 'arquivo'
                        ? 'Caminho do arquivo (ex: C:\\Documentos\\planilha.xlsx)'
                        : novoTipo === 'sistema'
                        ? 'Comando de sistema (ex: control, ms-settings:, taskmgr)'
                        : 'Nome do programa (ex: Calculadora, Excel, Cortecloud)'
                    }
                    className="w-full rounded-md border border-white/15 bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/40 focus:border-cyan-400 focus:outline-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={novoNome}
                    onChange={(e) => setNovoNome(e.target.value)}
                    placeholder="Nome de exibição (opcional)"
                    className="rounded-md border border-white/15 bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/40 focus:border-cyan-400 focus:outline-none"
                  />
                  <input
                    type="text"
                    value={novaCategoria}
                    onChange={(e) => setNovaCategoria(e.target.value)}
                    placeholder="Categoria"
                    className="rounded-md border border-white/15 bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/40 focus:border-cyan-400 focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setShowAddForm(false)}
                  className="rounded-md px-3 py-1 text-xs text-white/60 hover:text-white cursor-pointer"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving || !novaUrl.trim()}
                  className="flex items-center gap-1 rounded-md bg-cyan-500/80 hover:bg-cyan-400 px-3 py-1 text-xs font-semibold text-black transition disabled:opacity-50 cursor-pointer"
                >
                  <Plus className="size-3.5" />
                  <span>
                    {saving
                      ? 'Salvando...'
                      : novoTipo === 'site'
                      ? 'Salvar Site'
                      : novoTipo === 'pasta'
                      ? 'Salvar Pasta'
                      : novoTipo === 'arquivo'
                      ? 'Salvar Arquivo'
                      : novoTipo === 'sistema'
                      ? 'Salvar Ferramenta'
                      : 'Salvar Aplicativo'}
                  </span>
                </button>
              </div>
            </form>
          )}

          {/* Modal / Formulário Inline de Edição */}
          {editingItem && (
            <form
              onSubmit={handleSalvarEdicao}
              className="border-b border-amber-400/30 bg-amber-950/20 p-3 space-y-2 text-xs"
              data-no-drag="true"
            >
              <div className="text-[11px] font-semibold text-amber-300 uppercase tracking-wider flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <Edit2 className="size-3" />
                  <span>
                    {editingItem.tipo === 'app'
                      ? 'Renomear Aplicativo'
                      : 'Editar / Renomear Site'}
                  </span>
                </div>
                {editingItem.tipo === 'app' && (
                  <span className="text-[9px] text-cyan-300/90 font-mono bg-cyan-950/70 border border-cyan-400/30 px-1.5 py-0.5 rounded">
                    🔒 Atalho protegido
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 gap-2">
                <div>
                  <input
                    type="text"
                    required
                    value={editNome}
                    onChange={(e) => setEditNome(e.target.value.toUpperCase())}
                    placeholder="NOME (EM MAIÚSCULAS)"
                    className="w-full rounded-md border border-amber-400/40 bg-black/70 px-2.5 py-1.5 text-xs text-white uppercase focus:border-amber-400 focus:outline-none"
                  />
                </div>

                {editingItem.tipo !== 'app' && (
                  <div>
                    <input
                      type="text"
                      required
                      value={editUrl}
                      onChange={(e) => setEditUrl(e.target.value)}
                      placeholder="URL (https://...)"
                      className="w-full rounded-md border border-amber-400/40 bg-black/70 px-2.5 py-1.5 text-xs text-white focus:border-amber-400 focus:outline-none"
                    />
                  </div>
                )}

                <div>
                  <input
                    type="text"
                    value={editCategoria}
                    onChange={(e) => setEditCategoria(e.target.value)}
                    placeholder="Categoria"
                    className="w-full rounded-md border border-amber-400/40 bg-black/70 px-2.5 py-1.5 text-xs text-white focus:border-amber-400 focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setEditingItem(null)}
                  className="rounded-md px-3 py-1 text-xs text-white/60 hover:text-white cursor-pointer"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={updating || !editNome.trim()}
                  className="flex items-center gap-1 rounded-md bg-amber-400 hover:bg-amber-300 px-3 py-1 text-xs font-semibold text-black transition disabled:opacity-50 cursor-pointer"
                >
                  <Check className="size-3.5" />
                  <span>{updating ? 'Atualizando...' : 'Confirmar Alteração'}</span>
                </button>
              </div>
            </form>
          )}

          {/* Lista de Favoritos */}
          <div className="overflow-y-auto p-2.5 space-y-1.5 flex-1" data-no-drag="true">
            {filteredFavorites.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-center text-xs text-white/50">
                <Bookmark className="size-8 stroke-1 text-cyan-400/30 mb-2" />
                <p className="font-medium text-white/70">Nenhum atalho encontrado</p>
                <p className="mt-1 text-[11px] text-white/40 max-w-xs">
                  Diga por voz: &quot;Jarvis, adicione o aplicativo Calculadora aos atalhos&quot; ou &quot;Jarvis, salve este site nos atalhos&quot;.
                </p>
              </div>
            ) : (
              filteredFavorites.map((fav) => {
                const domain = extractDomain(fav.url);
                const faviconUrl = `https://www.google.com/s2/favicons?domain=${domain}&sz=64`;
                const nomeMaiusculo = (fav.nome || '').toUpperCase();

                return (
                  <div
                    key={fav.id}
                    className="group relative flex items-center justify-between gap-2.5 rounded-xl border border-white/10 bg-white/5 px-2.5 py-2 transition hover:border-cyan-400/40 hover:bg-cyan-950/20"
                  >
                    {/* Botão principal clicável para abrir o item (site, app, pasta, arquivo, sistema) */}
                    <button
                      type="button"
                      onClick={() => handleAbrirUrl(fav)}
                      className="flex items-center gap-2.5 min-w-0 flex-1 text-left cursor-pointer"
                      title={
                        fav.tipo === 'pasta'
                          ? `Abrir pasta ${nomeMaiusculo} no Explorador`
                          : fav.tipo === 'arquivo'
                          ? `Abrir arquivo ${nomeMaiusculo}`
                          : fav.tipo === 'sistema'
                          ? `Abrir ferramenta ${nomeMaiusculo}`
                          : fav.tipo === 'app'
                          ? `Abrir aplicativo ${nomeMaiusculo}`
                          : `Abrir ${fav.url} no navegador`
                      }
                    >
                      {/* Ícone contextual conforme o tipo de atalho */}
                      <div className="relative flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/15 bg-black/60 overflow-hidden shadow-xs">
                        {fav.tipo === 'pasta' ? (
                          <Folder className="size-4 text-amber-300" />
                        ) : fav.tipo === 'arquivo' ? (
                          <FileText className="size-4 text-emerald-300" />
                        ) : fav.tipo === 'sistema' ? (
                          <Settings className="size-4 text-orange-300" />
                        ) : fav.tipo === 'script' ? (
                          <Terminal className="size-4 text-pink-300" />
                        ) : fav.tipo === 'app' || (!fav.url.startsWith('http://') && !fav.url.startsWith('https://')) ? (
                          <AppWindow className="size-4 text-purple-300" />
                        ) : (
                          <>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={faviconUrl}
                              alt=""
                              className="size-4.5 object-contain"
                              onError={(e) => {
                                e.currentTarget.style.display = 'none';
                                const parent = e.currentTarget.parentElement;
                                if (parent) {
                                  const fallback = parent.querySelector('.fav-fallback');
                                  if (fallback) fallback.classList.remove('hidden');
                                }
                              }}
                            />
                            <Globe className="fav-fallback hidden size-4 text-cyan-300/70" />
                          </>
                        )}
                      </div>

                      {/* Textos: Nome em MAIÚSCULAS e Categoria estilizada */}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1.5 pr-1">
                          <span className="text-xs font-bold tracking-wide text-white truncate group-hover:text-cyan-200 uppercase">
                            {nomeMaiusculo}
                          </span>
                          {fav.categoria && (
                            <span
                              className={cn(
                                'shrink-0 rounded-full border px-1.5 py-0.2 text-[9px] font-medium uppercase',
                                fav.tipo === 'pasta'
                                  ? 'border-amber-400/30 bg-amber-500/10 text-amber-300'
                                  : fav.tipo === 'arquivo'
                                  ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-300'
                                  : fav.tipo === 'sistema'
                                  ? 'border-orange-400/30 bg-orange-500/10 text-orange-300'
                                  : fav.tipo === 'script'
                                  ? 'border-pink-400/30 bg-pink-500/10 text-pink-300'
                                  : fav.tipo === 'app'
                                  ? 'border-purple-400/30 bg-purple-500/10 text-purple-300'
                                  : 'border-cyan-400/20 bg-cyan-500/10 text-cyan-300'
                              )}
                            >
                              {fav.categoria}
                            </span>
                          )}
                        </div>
                      </div>
                    </button>

                    {/* Botões de Ação na Direita */}
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleAbrirUrl(fav)}
                        title="Abrir no navegador"
                        className="rounded-lg p-1.5 text-white/50 hover:bg-cyan-500/20 hover:text-cyan-200 transition cursor-pointer"
                      >
                        <ExternalLink className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleIniciarEdicao(fav)}
                        title="Editar / Renomear este atalho"
                        className="rounded-lg p-1.5 text-white/50 hover:bg-amber-500/20 hover:text-amber-300 transition cursor-pointer"
                      >
                        <Edit2 className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRemover(fav.id)}
                        title="Apagar este atalho"
                        className="rounded-lg p-1.5 text-white/50 hover:bg-red-500/20 hover:text-red-300 transition cursor-pointer"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
