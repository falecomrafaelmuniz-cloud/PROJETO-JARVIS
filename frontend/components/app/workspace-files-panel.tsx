'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  Check,
  CheckSquare,
  ChevronDown,
  Copy,
  Edit2,
  FileCode,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Globe,
  Headphones,
  Image as ImageIcon,
  Layers,
  Loader2,
  Mic,
  Plus,
  RotateCcw,
  SendHorizontal,
  Sparkles,
  Square,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';
import {
  ChatMessage,
  EXPERTS_CATALOG,
  ExpertPersona,
  NotebookItem,
  WorkspaceFileItem,
  getExpertById,
} from '@/lib/notebooks-types';

interface WorkspaceFilesPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenEmailComposer?: () => void;
}

export function WorkspaceFilesPanel({
  isOpen,
  onClose,
  onOpenEmailComposer,
}: WorkspaceFilesPanelProps) {
  // Notebooks State
  const [notebooks, setNotebooks] = useState<NotebookItem[]>([]);
  const [activeNotebookId, setActiveNotebookId] = useState<string>('geral');
  const [isNotebookMenuOpen, setIsNotebookMenuOpen] = useState(false);
  const [isCreatingNotebook, setIsCreatingNotebook] = useState(false);
  const [newNotebookName, setNewNotebookName] = useState('');
  const [isRenamingNotebook, setIsRenamingNotebook] = useState(false);
  const [renameNotebookName, setRenameNotebookName] = useState('');

  // Files State
  const [files, setFiles] = useState<WorkspaceFileItem[]>([]);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);

  // Chat State
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [inputPrompt, setInputPrompt] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [isExpertMenuOpen, setIsExpertMenuOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const expertDropdownRef = useRef<HTMLDivElement>(null);

  // Floating & Window State
  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-workspace-files-v3', false);
  const {
    style: dragStyle,
    isDragging,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-workspace-files-v3');

  // Scroll automático para a última mensagem
  const scrollToBottom = useCallback(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, []);

  // Conexão LiveKit para sincronização de voz em tempo real
  let room: any = null;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    room = useRoomContext();
  } catch {
    room = null;
  }

  // Sincroniza o caderno ativo com o Jarvis (backend) sempre que mudar ou abrir/fechar
  useEffect(() => {
    if (!room?.localParticipant) return;
    try {
      const payload = JSON.stringify({
        type: 'jarvis_active_notebook_sync',
        notebookId: activeNotebookId,
        isOpen,
      });
      room.localParticipant.publishData(new TextEncoder().encode(payload), {
        reliable: true,
        topic: 'jarvis.workspace',
      });
    } catch (err) {
      console.warn('Erro ao sincronizar caderno ativo:', err);
    }

    return () => {
      // Ao fechar ou desmontar a central, avisa IMEDIATAMENTE o Jarvis para parar de gravar
      if (room?.localParticipant) {
        try {
          const closePayload = JSON.stringify({
            type: 'jarvis_active_notebook_sync',
            notebookId: activeNotebookId,
            isOpen: false,
          });
          room.localParticipant.publishData(new TextEncoder().encode(closePayload), {
            reliable: true,
            topic: 'jarvis.workspace',
          });
        } catch {}
      }
    };
  }, [room, activeNotebookId, isOpen]);

  // Fecha menu de cadernos ou especialista se clicar fora
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsNotebookMenuOpen(false);
      }
      if (expertDropdownRef.current && !expertDropdownRef.current.contains(e.target as Node)) {
        setIsExpertMenuOpen(false);
      }
    }
    if (isNotebookMenuOpen || isExpertMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isNotebookMenuOpen, isExpertMenuOpen]);

  // Carrega lista de Cadernos
  const loadNotebooks = useCallback(async () => {
    try {
      const res = await fetch('/api/workspace-files/notebooks');
      const data = await res.json();
      if (data.ok && Array.isArray(data.notebooks)) {
        setNotebooks(data.notebooks);
        // Se o caderno ativo não existir na lista, volta para 'geral'
        if (!data.notebooks.some((n: NotebookItem) => n.id === activeNotebookId)) {
          setActiveNotebookId('geral');
        }
      }
    } catch (err) {
      console.error('Erro ao carregar cadernos:', err);
    }
  }, [activeNotebookId]);

  // Carrega Arquivos do Caderno Ativo
  const loadFiles = useCallback(async (nbId: string) => {
    try {
      const res = await fetch(`/api/workspace-files?notebookId=${encodeURIComponent(nbId)}`);
      const data = await res.json();
      if (data.ok && Array.isArray(data.files)) {
        setFiles(data.files);
        // Seleciona todos por padrão para facilitar consultas conjuntas
        setSelectedFileIds(data.files.map((f: WorkspaceFileItem) => f.id));
      }
    } catch (err) {
      console.error('Erro ao buscar arquivos:', err);
    }
  }, []);

  // Carrega Histórico do Chat do Caderno Ativo
  const loadChat = useCallback(async (nbId: string) => {
    try {
      const res = await fetch(`/api/workspace-files/chat?notebookId=${encodeURIComponent(nbId)}`);
      const data = await res.json();
      if (data.ok && Array.isArray(data.messages)) {
        setChatMessages(data.messages);
        setTimeout(scrollToBottom, 150);
      }
    } catch (err) {
      console.error('Erro ao buscar histórico de chat:', err);
    }
  }, [scrollToBottom]);

  // Escuta atualizações de chat e troca de caderno em tempo real vindas do Jarvis
  useEffect(() => {
    if (!room) return;
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== 'jarvis.workspace' && topic !== 'jarvis.hud') return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));

        // Troca de caderno ativo por voz (ex: "abra o caderno Porto Panorama")
        if (
          packet?.type === 'jarvis_switch_notebook' ||
          (packet?.type === 'jarvis_hud_control' && packet?.notebookId)
        ) {
          const targetId = packet.notebookId;
          if (targetId) {
            setActiveNotebookId(targetId);
            loadFiles(targetId);
            loadChat(targetId);
            loadNotebooks();
          }
        }

        // Atualização geral de cadernos ou especialista por voz
        if (
          packet?.type === 'jarvis_notebooks_updated' ||
          packet?.type === 'jarvis_notebook_expert_updated'
        ) {
          loadNotebooks();
          const targetId = packet.activeNotebookId || packet.notebookId;
          if (targetId && targetId !== activeNotebookId) {
            setActiveNotebookId(targetId);
            loadFiles(targetId);
            loadChat(targetId);
          }
        }

        // Limpeza de histórico do chat por voz
        if (packet?.type === 'jarvis_notebook_chat_cleared') {
          if (!packet.notebookId || packet.notebookId === activeNotebookId) {
            setChatMessages([]);
          }
        }

        // Atualização de arquivos no caderno (upload ou movimentação por voz)
        if (packet?.type === 'jarvis_notebook_files_updated') {
          if (!packet.notebookId || packet.notebookId === activeNotebookId) {
            loadFiles(activeNotebookId);
            loadNotebooks();
          }
        }

        // Atualização de mensagens do chat do caderno ativo
        if (packet?.type === 'jarvis_notebook_chat_update') {
          if (packet.notebookId === activeNotebookId && Array.isArray(packet.messages)) {
            setChatMessages(packet.messages);
            setTimeout(scrollToBottom, 80);
          }
        }
      } catch (err) {
        console.warn('Falha ao processar evento jarvis.workspace:', err);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room, activeNotebookId, scrollToBottom, loadFiles, loadChat, loadNotebooks]);

  // Inicialização e troca de caderno
  useEffect(() => {
    if (isOpen) {
      loadNotebooks();
    }
  }, [isOpen, loadNotebooks]);

  useEffect(() => {
    if (isOpen && activeNotebookId) {
      loadFiles(activeNotebookId);
      loadChat(activeNotebookId);
    }
  }, [isOpen, activeNotebookId, loadFiles, loadChat]);

  // Criação de Caderno
  const handleCreateNotebook = async () => {
    const name = newNotebookName.trim();
    if (!name) return;
    try {
      const res = await fetch('/api/workspace-files/notebooks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (data.ok && data.notebook) {
        setNewNotebookName('');
        setIsCreatingNotebook(false);
        setIsNotebookMenuOpen(false);
        await loadNotebooks();
        setActiveNotebookId(data.notebook.id);
      }
    } catch (err) {
      console.error('Erro ao criar caderno:', err);
    }
  };

  // Renomear Caderno
  const handleRenameNotebook = async () => {
    const name = renameNotebookName.trim();
    if (!name || !activeNotebookId) return;
    try {
      const res = await fetch('/api/workspace-files/notebooks', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: activeNotebookId, name }),
      });
      const data = await res.json();
      if (data.ok && data.notebook) {
        setIsRenamingNotebook(false);
        await loadNotebooks();
      }
    } catch (err) {
      console.error('Erro ao renomear caderno:', err);
    }
  };

  // Excluir Caderno
  const handleDeleteNotebook = async (id: string, name: string) => {
    if (id === 'geral') {
      alert('O caderno padrão Geral não pode ser excluído.');
      return;
    }
    const confirmDelete = window.confirm(
      `Deseja realmente excluir o caderno "${name}"?\nTodos os seus arquivos e conversas serão removidos.`
    );
    if (!confirmDelete) return;

    try {
      const res = await fetch(`/api/workspace-files/notebooks?id=${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (data.ok) {
        setIsNotebookMenuOpen(false);
        setActiveNotebookId('geral');
        await loadNotebooks();
      }
    } catch (err) {
      console.error('Erro ao excluir caderno:', err);
    }
  };

  // Limpar Histórico do Chat (mantendo arquivos intactos!)
  const handleClearChat = async () => {
    if (chatMessages.length === 0) return;
    const confirmClear = window.confirm(
      'Deseja limpar todo o histórico de mensagens deste caderno?\n\nOs arquivos continuarão intactos.'
    );
    if (!confirmClear) return;

    try {
      const res = await fetch(`/api/workspace-files/chat?notebookId=${encodeURIComponent(activeNotebookId)}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (data.ok) {
        setChatMessages([]);
      }
    } catch (err) {
      console.error('Erro ao limpar chat:', err);
    }
  };

  // Upload de Arquivos
  const handleUploadFiles = async (fileList: FileList | File[]) => {
    if (!fileList || fileList.length === 0) return;
    setIsUploading(true);

    try {
      const formData = new FormData();
      formData.append('notebookId', activeNotebookId);
      Array.from(fileList).forEach((f) => formData.append('files', f));

      const res = await fetch(`/api/workspace-files?notebookId=${encodeURIComponent(activeNotebookId)}`, {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();
      if (data.ok && Array.isArray(data.files)) {
        setFiles(data.files);
        // Seleciona os novos arquivos também
        setSelectedFileIds(data.files.map((f: WorkspaceFileItem) => f.id));
        loadNotebooks();
      }
    } catch (err) {
      console.error('Erro no upload de arquivos:', err);
    } finally {
      setIsUploading(false);
    }
  };

  // Suporte a colar print / imagem com Ctrl+V
  useEffect(() => {
    function handlePaste(e: ClipboardEvent) {
      if (!isOpen) return;
      // Se estiver digitando no input de texto, não intercepta texto
      const activeEl = document.activeElement;
      if (activeEl?.tagName === 'INPUT' || activeEl?.tagName === 'TEXTAREA') {
        const items = e.clipboardData?.items;
        if (!items) return;
        const hasImage = Array.from(items).some((item) => item.type.startsWith('image/'));
        if (!hasImage) return; // Permite colar texto normal no input
      }

      const items = e.clipboardData?.items;
      if (!items) return;

      const filesToUpload: File[] = [];
      for (const item of Array.from(items)) {
        if (item.kind === 'file') {
          const file = item.getAsFile();
          if (file) filesToUpload.push(file);
        }
      }

      if (filesToUpload.length > 0) {
        e.preventDefault();
        handleUploadFiles(filesToUpload);
      }
    }

    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, [isOpen, activeNotebookId]);

  // Excluir Arquivo Único
  const handleDeleteFile = async (fileId: string) => {
    try {
      const res = await fetch(
        `/api/workspace-files?notebookId=${encodeURIComponent(activeNotebookId)}&id=${encodeURIComponent(fileId)}`,
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.ok && Array.isArray(data.files)) {
        setFiles(data.files);
        setSelectedFileIds((prev) => prev.filter((id) => id !== fileId));
        loadNotebooks();
      }
    } catch (err) {
      console.error('Erro ao excluir arquivo:', err);
    }
  };

  // Checkbox: Selecionar Todos / Desmarcar Todos
  const isAllSelected = files.length > 0 && selectedFileIds.length === files.length;
  const handleToggleSelectAll = () => {
    if (isAllSelected) {
      setSelectedFileIds([]);
    } else {
      setSelectedFileIds(files.map((f) => f.id));
    }
  };

  const handleToggleFileSelection = (fileId: string) => {
    setSelectedFileIds((prev) =>
      prev.includes(fileId) ? prev.filter((id) => id !== fileId) : [...prev, fileId]
    );
  };

  const handleClosePanel = () => {
    if (room?.localParticipant) {
      try {
        const payload = JSON.stringify({
          type: 'jarvis_active_notebook_sync',
          notebookId: activeNotebookId,
          isOpen: false,
        });
        room.localParticipant.publishData(new TextEncoder().encode(payload), {
          reliable: true,
          topic: 'jarvis.workspace',
        });
      } catch {}
    }
    onClose();
  };

  // Caderno Atual Objeto e Especialista Ativo
  const currentNotebook = notebooks.find((n) => n.id === activeNotebookId) || {
    id: 'geral',
    name: 'Geral',
    fileCount: files.length,
    messageCount: chatMessages.length,
    createdAt: '',
    updatedAt: '',
  };

  const activeExpert: ExpertPersona = getExpertById(currentNotebook.expert, currentNotebook.name);

  // Troca de especialista do caderno
  const handleSelectExpert = async (expertId: string) => {
    setIsExpertMenuOpen(false);
    try {
      const res = await fetch('/api/workspace-files/notebooks', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: activeNotebookId, expert: expertId }),
      });
      const data = await res.json();
      if (data.ok && data.notebook) {
        setNotebooks((prev) =>
          prev.map((n) => (n.id === activeNotebookId ? { ...n, expert: expertId } : n))
        );
      }
    } catch (err) {
      console.error('Erro ao atualizar especialista:', err);
    }

    if (room?.localParticipant) {
      try {
        const payload = JSON.stringify({
          type: 'jarvis_notebook_expert_updated',
          notebookId: activeNotebookId,
          expert: expertId,
        });
        room.localParticipant.publishData(new TextEncoder().encode(payload), {
          reliable: true,
          topic: 'jarvis.workspace',
        });
      } catch {}
    }
  };

  // Enviar Pergunta ao Caderno (com histórico e busca web ativa)
  const handleSendMessage = async (promptOverride?: string) => {
    const textToSend = (promptOverride !== undefined ? promptOverride : inputPrompt).trim();
    if (!textToSend || isLoading) return;

    if (selectedFileIds.length === 0) {
      alert('Selecione pelo menos um arquivo na coluna de fontes para analisar.');
      return;
    }

    // Nomes dos arquivos selecionados para preview
    const selectedNames = files
      .filter((f) => selectedFileIds.includes(f.id))
      .map((f) => f.name);

    // Mensagem otimista do usuário
    const optimisticUserMsg: ChatMessage = {
      id: `temp-${Date.now()}-u`,
      role: 'user',
      content: textToSend,
      timestamp: new Date().toISOString(),
      sources: selectedNames,
    };

    setChatMessages((prev) => [...prev, optimisticUserMsg]);
    setInputPrompt('');
    setIsLoading(true);
    setTimeout(scrollToBottom, 50);

    try {
      const res = await fetch('/api/workspace-files/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebookId: activeNotebookId,
          fileIds: selectedFileIds,
          customPrompt: textToSend,
          action: 'analisar',
          expert: activeExpert.id,
        }),
      });

      const data = await res.json();
      if (data.ok && Array.isArray(data.chatHistory)) {
        setChatMessages(data.chatHistory);
        loadNotebooks();
      } else if (data.ok && data.resultado) {
        const assistantMsg: ChatMessage = {
          id: `msg-${Date.now()}-a`,
          role: 'assistant',
          content: data.resultado,
          timestamp: new Date().toISOString(),
          sources: data.arquivos || selectedNames,
          buscouWeb: data.buscouWeb,
        };
        setChatMessages((prev) => [...prev, assistantMsg]);
      } else {
        const errorMsg: ChatMessage = {
          id: `err-${Date.now()}`,
          role: 'assistant',
          content: `⚠️ Falha na análise: ${data.error || 'Erro desconhecido.'}`,
          timestamp: new Date().toISOString(),
        };
        setChatMessages((prev) => [...prev, errorMsg]);
      }
    } catch (err) {
      console.error('Erro na requisição:', err);
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: 'assistant',
        content: '⚠️ Ocorreu um erro de conexão ao processar os arquivos.',
        timestamp: new Date().toISOString(),
      };
      setChatMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
      setTimeout(scrollToBottom, 100);
    }
  };

  // Botão Analisar Conjunto
  const handleAnalyzeConjunto = () => {
    if (selectedFileIds.length === 0) return;
    const prompt =
      inputPrompt.trim() ||
      'Faça uma análise executiva completa cruzando todos os dados dos documentos selecionados. Se houver valores e índices, pesquise na web se necessário e apresente as conclusões numéricas.';
    handleSendMessage(prompt);
  };

  // Copiar Resposta
  const handleCopyText = (text: string, msgId: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(msgId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Ícones por tipo de arquivo
  const renderFileIcon = (type: WorkspaceFileItem['type']) => {
    switch (type) {
      case 'image':
        return <ImageIcon className="size-4 text-emerald-400" />;
      case 'pdf':
        return <FileText className="size-4 text-rose-400" />;
      case 'spreadsheet':
        return <FileSpreadsheet className="size-4 text-teal-400" />;
      case 'code':
        return <FileCode className="size-4 text-violet-400" />;
      case 'audio':
        return <Headphones className="size-4 text-amber-400" />;
      default:
        return <FileText className="size-4 text-cyan-400" />;
    }
  };

  if (!isOpen) return null;

  return (
    <div
      style={dragStyle} data-hud-panel="files"
      className={cn(
        'fixed z-50 flex flex-col overflow-hidden rounded-xl border border-cyan-500/40 bg-[#030a16]/95 backdrop-blur-xl shadow-[0_0_35px_rgba(6,182,212,0.25)] transition-shadow duration-300 font-sans text-white select-none',
        isDragging && 'opacity-90 shadow-[0_0_45px_rgba(6,182,212,0.4)]',
        'w-[840px] max-w-[96vw] h-[580px] max-h-[88vh] top-12 left-[calc(50%-420px)] max-md:left-2'
      )}
    >
      {/* BARRA SUPERIOR HUD: Título, Seletor de Cadernos e Controles */}
      <div
        {...dragHandlers}
        className="flex items-center justify-between border-b border-cyan-500/30 bg-gradient-to-r from-cyan-950/60 via-slate-900/80 to-cyan-950/60 px-3 py-2 cursor-grab active:cursor-grabbing"
      >
        <div className="flex items-center gap-2.5">
          <BookOpen className="size-4 text-cyan-400 animate-pulse" />
          <span className="text-[11px] font-bold tracking-wider text-cyan-300 uppercase">
            CENTRAL DE ARQUIVOS · NOTEBOOKS
          </span>

          {/* SELETOR DE CADERNOS DROPDOWN */}
          <div data-no-drag="true" ref={dropdownRef} className="relative ml-2">
            <button
              type="button"
              onClick={() => setIsNotebookMenuOpen((prev) => !prev)}
              className="flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-950/50 px-2.5 py-1 text-[11px] font-semibold text-cyan-200 hover:border-cyan-400 hover:bg-cyan-900/50 transition-all shadow-sm"
            >
              <FolderOpen className="size-3.5 text-cyan-400" />
              <span className="max-w-[130px] truncate">{currentNotebook.name}</span>
              <ChevronDown className="size-3 text-cyan-400/80" />
            </button>

            {/* Menu Dropdown de Cadernos */}
            {isNotebookMenuOpen && (
              <div className="absolute left-0 top-full mt-1.5 w-64 rounded-xl border border-cyan-500/40 bg-[#040e22]/98 p-2 shadow-2xl backdrop-blur-2xl z-50">
                <div className="mb-2 flex items-center justify-between border-b border-white/10 pb-1.5 px-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-white/50">
                    Seus Cadernos
                  </span>
                  <button
                    type="button"
                    onClick={() => setIsCreatingNotebook(true)}
                    className="flex items-center gap-1 rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-cyan-300 hover:bg-cyan-500/30"
                  >
                    <Plus className="size-3" />
                    <span>Novo</span>
                  </button>
                </div>

                {/* Input de criação rápida */}
                {isCreatingNotebook && (
                  <div className="mb-2 flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-black/40 p-1.5">
                    <input
                      type="text"
                      autoFocus
                      placeholder="Nome do caderno..."
                      value={newNotebookName}
                      onChange={(e) => setNewNotebookName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleCreateNotebook();
                        if (e.key === 'Escape') setIsCreatingNotebook(false);
                      }}
                      className="flex-1 bg-transparent px-1.5 text-[11px] text-white placeholder-white/40 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={handleCreateNotebook}
                      className="rounded bg-cyan-500 px-2 py-0.5 text-[10px] font-bold text-black hover:bg-cyan-400"
                    >
                      Criar
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsCreatingNotebook(false)}
                      className="text-white/40 hover:text-white"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                )}

                {/* Lista de Cadernos */}
                <div className="max-h-48 overflow-y-auto space-y-1 pr-1 custom-scrollbar">
                  {notebooks.map((nb) => {
                    const isActive = nb.id === activeNotebookId;
                    return (
                      <div
                        key={nb.id}
                        className={cn(
                          'group flex items-center justify-between rounded-lg px-2 py-1.5 text-[11px] transition-all cursor-pointer',
                          isActive
                            ? 'border border-cyan-500/50 bg-cyan-500/20 text-cyan-200 font-bold'
                            : 'text-white/70 hover:bg-white/5 hover:text-white'
                        )}
                        onClick={() => {
                          setActiveNotebookId(nb.id);
                          setIsNotebookMenuOpen(false);
                        }}
                      >
                        <div className="flex items-center gap-1.5 truncate">
                          <FolderOpen className={cn('size-3.5', isActive ? 'text-cyan-300' : 'text-white/40')} />
                          <span className="truncate">{nb.name}</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[9px] text-white/40">
                            {nb.fileCount} {nb.fileCount === 1 ? 'doc' : 'docs'}
                          </span>
                          {nb.id !== 'geral' && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteNotebook(nb.id, nb.name);
                              }}
                              className="opacity-0 group-hover:opacity-100 p-0.5 text-red-400/70 hover:text-red-300 transition-opacity"
                              title="Excluir Caderno"
                            >
                              <Trash2 className="size-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Botão de Renomear Caderno */}
          {activeNotebookId !== 'geral' && (
            <button
              data-no-drag="true"
              type="button"
              onClick={() => {
                setRenameNotebookName(currentNotebook.name);
                setIsRenamingNotebook((prev) => !prev);
              }}
              title="Renomear Caderno Ativo"
              className="text-white/40 hover:text-cyan-300 transition-colors p-1"
            >
              <Edit2 className="size-3" />
            </button>
          )}

          {isRenamingNotebook && (
            <div data-no-drag="true" className="flex items-center gap-1">
              <input
                type="text"
                autoFocus
                value={renameNotebookName}
                onChange={(e) => setRenameNotebookName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleRenameNotebook();
                  if (e.key === 'Escape') setIsRenamingNotebook(false);
                }}
                className="w-28 rounded border border-cyan-500/50 bg-black/60 px-1.5 py-0.5 text-[10px] text-white focus:outline-none"
              />
              <button
                type="button"
                onClick={handleRenameNotebook}
                className="rounded bg-cyan-500/30 px-1.5 py-0.5 text-[9px] font-bold text-cyan-200 hover:bg-cyan-500/50"
              >
                Salvar
              </button>
            </div>
          )}
        </div>

        {/* Janela Controles: Minimizar, Fechar */}
        <div data-no-drag="true">
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={handleClosePanel}
            closeTitle="Fechar central de arquivos"
          />
        </div>
      </div>

      {/* CONTEÚDO PRINCIPAL: 2 COLUNAS (NotebookLM style) */}
      {!minimized && (
        <div className="flex-1 flex overflow-hidden min-h-0 divide-x divide-cyan-500/20">
          {/* ========================================== */}
          {/* COLUNA ESQUERDA: FONTES DO CADERNO (320px) */}
          {/* ========================================== */}
          <div className="w-[320px] flex-shrink-0 flex flex-col bg-slate-950/40 p-3 space-y-2.5 overflow-hidden">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-wider text-cyan-400 uppercase">
                FONTES DO CADERNO ({files.length})
              </span>
              <span className="text-[9px] text-white/40 font-mono">
                {selectedFileIds.length} de {files.length} selecionados
              </span>
            </div>

            {/* Dropzone de Upload e Ctrl+V */}
            <div
              data-no-drag="true"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragOver(true);
              }}
              onDragLeave={() => setIsDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragOver(false);
                if (e.dataTransfer.files) handleUploadFiles(e.dataTransfer.files);
              }}
              className={cn(
                'group flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-3 text-center transition-all cursor-pointer select-none',
                isDragOver
                  ? 'border-cyan-400 bg-cyan-500/20 shadow-[0_0_20px_rgba(6,182,212,0.3)]'
                  : 'border-cyan-500/30 bg-cyan-950/20 hover:border-cyan-400 hover:bg-cyan-950/40'
              )}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) handleUploadFiles(e.target.files);
                }}
              />
              <UploadCloud
                className={cn(
                  'size-5 transition-transform group-hover:scale-110',
                  isDragOver ? 'text-cyan-300' : 'text-cyan-400'
                )}
              />
              <span className="mt-1 text-[11px] font-semibold text-white/90">
                {isUploading ? 'Adicionando fontes...' : 'Solte arquivos ou clique aqui'}
              </span>
              <span className="text-[9px] text-white/40">
                PDFs, imagens, tabelas · Aceita <kbd className="rounded bg-white/10 px-1 text-cyan-300 font-mono">Ctrl+V</kbd>
              </span>
            </div>

            {/* Barra de Ação de Seleção e Botão Analisar Conjunto */}
            {files.length > 0 && (
              <div data-no-drag="true" className="flex items-center justify-between gap-1.5 pt-1">
                <button
                  type="button"
                  onClick={handleToggleSelectAll}
                  className="flex items-center gap-1.5 text-[10px] text-white/70 hover:text-white transition-colors"
                >
                  {isAllSelected ? (
                    <CheckSquare className="size-3.5 text-cyan-400" />
                  ) : selectedFileIds.length > 0 ? (
                    <div className="size-3.5 rounded border border-cyan-400 bg-cyan-500/30 flex items-center justify-center text-[8px] font-bold text-cyan-200">
                      -
                    </div>
                  ) : (
                    <Square className="size-3.5 text-white/40" />
                  )}
                  <span>{isAllSelected ? 'Desmarcar' : 'Selecionar todos'}</span>
                </button>

                {/* BOTÃO DE DESTAQUE: ANALISAR CONJUNTO */}
                <button
                  type="button"
                  disabled={selectedFileIds.length === 0 || isLoading}
                  onClick={handleAnalyzeConjunto}
                  title="Cruza todas as fontes marcadas e calcula com dados da web"
                  className={cn(
                    'flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[10px] font-bold tracking-wide transition-all shadow-md',
                    selectedFileIds.length > 0 && !isLoading
                      ? 'border-cyan-300/60 bg-gradient-to-r from-cyan-600/40 via-cyan-500/30 to-blue-600/40 text-cyan-100 hover:border-cyan-300 hover:brightness-110 shadow-[0_0_12px_rgba(6,182,212,0.3)] cursor-pointer'
                      : 'border-white/10 bg-white/5 text-white/30 cursor-not-allowed opacity-50'
                  )}
                >
                  <Sparkles className="size-3 text-cyan-300 animate-pulse" />
                  <span>ANALISAR ({selectedFileIds.length})</span>
                </button>
              </div>
            )}

            {/* Lista Rolável de Arquivos */}
            <div data-no-drag="true" className="flex-1 overflow-y-auto space-y-1 pr-1 custom-scrollbar">
              {files.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center p-4 text-white/40">
                  <FolderOpen className="size-7 text-white/20 mb-1" />
                  <p className="text-[11px]">Nenhuma fonte neste caderno.</p>
                  <p className="text-[9px] text-white/30">Arraste ou cole (Ctrl+V) arquivos aqui.</p>
                </div>
              ) : (
                files.map((file) => {
                  const isChecked = selectedFileIds.includes(file.id);
                  return (
                    <div
                      key={file.id}
                      onClick={() => handleToggleFileSelection(file.id)}
                      className={cn(
                        'group flex items-center justify-between rounded-lg border px-2 py-1.5 transition-all cursor-pointer',
                        isChecked
                          ? 'border-cyan-500/40 bg-cyan-950/40 text-white'
                          : 'border-white/5 bg-white/[0.02] text-white/50 hover:border-white/10 hover:bg-white/[0.05]'
                      )}
                    >
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleToggleFileSelection(file.id);
                          }}
                          className="flex-shrink-0"
                        >
                          {isChecked ? (
                            <CheckSquare className="size-3.5 text-cyan-400" />
                          ) : (
                            <Square className="size-3.5 text-white/30 group-hover:text-white/50" />
                          )}
                        </button>

                        <div className="flex-shrink-0">{renderFileIcon(file.type)}</div>

                        <div className="min-w-0 flex-1">
                          <p
                            className={cn(
                              'truncate text-[11px] font-medium leading-tight',
                              isChecked ? 'text-white' : 'text-white/60'
                            )}
                            title={file.name}
                          >
                            {file.name}
                          </p>
                          <span className="text-[9px] text-white/40 font-mono">
                            {file.formattedSize}
                          </span>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteFile(file.id);
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1 text-red-400/60 hover:text-red-300 transition-opacity flex-shrink-0"
                        title="Remover fonte do caderno"
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* ==================================================== */}
          {/* COLUNA DIREITA: CHAT PERSISTENTE COM O CADERNO */}
          {/* ==================================================== */}
          <div className="flex-1 flex flex-col bg-black/40 overflow-hidden">
            {/* Cabeçalho da Conversa */}
            <div className="flex items-center justify-between border-b border-cyan-500/20 bg-slate-950/60 px-3 py-2">
              <div className="flex items-center gap-2 flex-wrap min-w-0">
                <Sparkles className="size-3.5 text-cyan-400 shrink-0" />
                <span className="text-[11px] font-bold text-white/90 truncate">
                  Conversa
                </span>
                <span className="text-[9px] text-cyan-400/80 font-mono shrink-0">
                  ({chatMessages.length})
                </span>

                {/* SELETOR DE ESPECIALISTA DO CADERNO */}
                <div data-no-drag="true" ref={expertDropdownRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setIsExpertMenuOpen((prev) => !prev)}
                    title="Clique para trocar o especialista consultor deste caderno"
                    className={cn(
                      'flex items-center gap-1.5 rounded-lg border px-2 py-0.5 text-[10px] font-semibold transition-all shadow-sm cursor-pointer',
                      activeExpert.badgeColor,
                      'hover:brightness-125'
                    )}
                  >
                    <span>{activeExpert.icon}</span>
                    <span className="max-w-[130px] truncate">{activeExpert.name}</span>
                    <ChevronDown className="size-3 opacity-70 shrink-0" />
                  </button>

                  {/* Dropdown Popover de Especialistas */}
                  {isExpertMenuOpen && (
                    <div className="absolute left-0 top-full mt-1.5 w-80 rounded-xl border border-cyan-500/50 bg-[#040e22]/98 p-2.5 shadow-2xl backdrop-blur-2xl z-50 animate-in fade-in zoom-in-95 duration-150">
                      <div className="mb-2 border-b border-white/10 pb-1.5 px-1 flex items-center justify-between">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-cyan-300">
                          Especialista Consultor
                        </span>
                        <span className="text-[9px] text-white/40">Foco do Caderno</span>
                      </div>

                      <div className="max-h-64 overflow-y-auto space-y-1.5 pr-1 custom-scrollbar">
                        {EXPERTS_CATALOG.map((exp) => {
                          const isSelected = exp.id === activeExpert.id;
                          return (
                            <button
                              key={exp.id}
                              type="button"
                              onClick={() => handleSelectExpert(exp.id)}
                              className={cn(
                                'w-full text-left flex items-start gap-2.5 rounded-lg p-2 transition-all cursor-pointer',
                                isSelected
                                  ? 'border border-cyan-500/60 bg-cyan-950/70 text-white shadow-sm'
                                  : 'border border-white/5 bg-white/[0.02] hover:bg-white/[0.06] text-white/70 hover:text-white'
                              )}
                            >
                              <span className="text-lg mt-0.5 shrink-0">{exp.icon}</span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center justify-between">
                                  <span className="text-[11px] font-bold truncate text-white">
                                    {exp.name}
                                  </span>
                                  {isSelected && <Check className="size-3.5 text-cyan-400 shrink-0" />}
                                </div>
                                <p className="text-[9px] text-cyan-300 font-mono truncate">
                                  {exp.shortRole}
                                </p>
                                <p className="text-[9px] text-white/50 line-clamp-2 mt-0.5 leading-tight">
                                  {exp.description}
                                </p>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Botão Limpar Histórico do Chat */}
              <button
                data-no-drag="true"
                type="button"
                onClick={handleClearChat}
                disabled={chatMessages.length === 0}
                title="Apaga o histórico de mensagens mantendo todos os arquivos intactos"
                className={cn(
                  'flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-medium transition-all shrink-0 ml-1',
                  chatMessages.length > 0
                    ? 'border-white/10 bg-white/5 text-white/60 hover:border-red-500/40 hover:bg-red-950/30 hover:text-red-300 cursor-pointer'
                    : 'border-transparent text-white/20 cursor-not-allowed'
                )}
              >
                <RotateCcw className="size-3" />
                <span className="hidden sm:inline">Limpar</span>
              </button>
            </div>

            {/* Linha do Tempo das Mensagens */}
            <div
              data-no-drag="true"
              ref={chatScrollRef}
              className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar"
            >
              {chatMessages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center p-6 text-white/40 space-y-2">
                  <BookOpen className="size-9 text-cyan-400/30" />
                  <p className="text-[12px] font-semibold text-white/70">
                    Caderno &quot;{currentNotebook.name}&quot; pronto.
                  </p>
                  <p className="text-[11px] text-white/40 max-w-sm">
                    Selecione as fontes na coluna à esquerda e faça qualquer pergunta abaixo, ou clique em{' '}
                    <strong className="text-cyan-300">ANALISAR</strong> para cruzar os dados com busca na web.
                  </p>
                </div>
              ) : (
                chatMessages.map((msg) => {
                  const isUser = msg.role === 'user';
                  return (
                    <div
                      key={msg.id}
                      className={cn('flex flex-col', isUser ? 'items-end' : 'items-start')}
                    >
                      <div
                        className={cn(
                          'max-w-[90%] rounded-xl px-3.5 py-2.5 text-[12px] leading-relaxed shadow-lg',
                          isUser
                            ? 'bg-gradient-to-r from-cyan-600/30 to-blue-600/30 border border-cyan-400/40 text-cyan-50'
                            : 'bg-slate-900/90 border border-cyan-500/30 text-white/95'
                        )}
                      >
                        {/* Identificação do Especialista na Resposta */}
                        {!isUser && (
                          <div className="mb-1.5 flex items-center gap-1.5">
                            {(() => {
                              const exp = getExpertById(msg.expert || currentNotebook.expert, currentNotebook.name);
                              return (
                                <span
                                  className={cn(
                                    'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-bold border tracking-wide',
                                    exp.badgeColor
                                  )}
                                >
                                  <span>{exp.icon}</span>
                                  <span>{exp.name}</span>
                                </span>
                              );
                            })()}
                          </div>
                        )}

                        {/* Conteúdo da Mensagem com suporte a quebras de linha e markdown básico */}
                        <div className="whitespace-pre-wrap font-sans select-text">
                          {msg.content}
                        </div>

                        {/* Rodapé da Resposta com Tags de Fontes e Botão de Copiar */}
                        {!isUser && (
                          <div className="mt-2.5 pt-2 border-t border-white/10 flex items-center justify-between gap-2 text-[10px]">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              {msg.buscouWeb && (
                                <span className="flex items-center gap-1 rounded bg-cyan-950/80 border border-cyan-500/30 px-1.5 py-0.5 text-cyan-300 font-mono">
                                  <Globe className="size-2.5 text-cyan-400" />
                                  Web em tempo real
                                </span>
                              )}
                              {msg.sources && msg.sources.length > 0 && (
                                <span className="text-white/40">
                                  Fontes: {msg.sources.join(', ')}
                                </span>
                              )}
                            </div>

                            <button
                              type="button"
                              onClick={() => handleCopyText(msg.content, msg.id)}
                              className="flex items-center gap-1 text-white/40 hover:text-cyan-300 transition-colors p-1"
                              title="Copiar resposta"
                            >
                              {copiedId === msg.id ? (
                                <>
                                  <Check className="size-3 text-emerald-400" />
                                  <span className="text-emerald-400">Copiado</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="size-3" />
                                  <span>Copiar</span>
                                </>
                              )}
                            </button>
                          </div>
                        )}
                      </div>
                      <span className="mt-0.5 px-1 text-[9px] text-white/30 font-mono">
                        {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  );
                })
              )}

              {/* Indicador de Carregamento / Analisando */}
              {isLoading && (
                <div className="flex items-center gap-2 rounded-xl border border-cyan-500/30 bg-slate-900/80 px-3 py-2 text-[11px] text-cyan-200 w-fit">
                  <Loader2 className="size-3.5 animate-spin text-cyan-400" />
                  <span>JARVIS está consultando as fontes e a web...</span>
                </div>
              )}
            </div>

            {/* Barra de Entrada de Pergunta */}
            <div data-no-drag="true" className="border-t border-cyan-500/20 bg-slate-950/80 p-2.5">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSendMessage();
                }}
                className="flex items-center gap-2 rounded-xl border border-cyan-500/40 bg-black/60 px-3 py-1.5 shadow-inner focus-within:border-cyan-400 transition-colors"
              >
                <input
                  type="text"
                  placeholder="Pergunte aos arquivos selecionados (busca web ativa)..."
                  value={inputPrompt}
                  onChange={(e) => setInputPrompt(e.target.value)}
                  disabled={isLoading}
                  className="flex-1 bg-transparent text-[11px] text-white placeholder-white/40 focus:outline-none"
                />

                <button
                  type="submit"
                  disabled={!inputPrompt.trim() || isLoading || selectedFileIds.length === 0}
                  className={cn(
                    'flex items-center justify-center rounded-lg p-1.5 transition-all',
                    inputPrompt.trim() && !isLoading && selectedFileIds.length > 0
                      ? 'bg-cyan-500 text-black hover:bg-cyan-400 shadow-[0_0_10px_rgba(6,182,212,0.4)] cursor-pointer'
                      : 'bg-white/5 text-white/20 cursor-not-allowed'
                  )}
                  title="Enviar pergunta (Enter)"
                >
                  <SendHorizontal className="size-3.5" />
                </button>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
