'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  FileCode2,
  Folder,
  FolderOpen,
  GitBranch,
  Loader2,
  Maximize2,
  Mic,
  Minimize2,
  RefreshCw,
  Send,
  Sparkles,
  X,
} from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const ANTIGRAVITY_TOPIC = 'jarvis.antigravity';

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'folder';
  children?: FileTreeNode[];
}

export interface OpenTab {
  name: string;
  path: string;
  content: string;
  language: string;
  isModified?: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  diffBadge?: string;
  steps?: {
    tipo: string;
    mensagem?: string;
    ferramenta?: string;
    resultado?: string;
    timestamp: string;
  }[];
}

interface AntigravityPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenRequest?: () => void;
}

function detectLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  if (['py'].includes(ext)) return 'python';
  if (['ts', 'tsx'].includes(ext)) return 'typescript';
  if (['js', 'jsx'].includes(ext)) return 'javascript';
  if (['json'].includes(ext)) return 'json';
  if (['css'].includes(ext)) return 'css';
  if (['md'].includes(ext)) return 'markdown';
  if (['ps1'].includes(ext)) return 'powershell';
  return 'plaintext';
}

const PYTHON_REGEX = /(#.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(import|from|as|def|class|async|await|return|if|else|elif|try|except|finally|for|while|in|is|not|and|or|None|True|False)\b|\b(\d+\.?\d*)\b|\b([A-Z][a-zA-Z0-9_]*)\b/g;
const TS_REGEX = /(\/\/.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(import|from|export|default|const|let|var|function|return|if|else|async|await|type|interface|class|true|false|null|undefined)\b|\b(\d+\.?\d*)\b|\b([A-Z][a-zA-Z0-9_]*)\b/g;

// Colorizador de sintaxe memoizado
function highlightCode(code: string, language: string): React.ReactNode[] {
  const lines = code.split('\n');
  return lines.map((line, lineIdx) => {
    const tokens: React.ReactNode[] = [];
    let keyIdx = 0;
    const regex = language === 'python' ? PYTHON_REGEX : TS_REGEX;
    regex.lastIndex = 0;

    let match;
    let lastIndex = 0;

    while ((match = regex.exec(line)) !== null) {
      if (match.index > lastIndex) {
        tokens.push(
          <span key={`p-${lineIdx}-${keyIdx++}`} className="text-gray-300">
            {line.substring(lastIndex, match.index)}
          </span>
        );
      }

      const [full, comment, str, keyword, number, typeName] = match;
      if (comment) {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-[#6a9955] italic">
            {comment}
          </span>
        );
      } else if (str) {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-[#ce9178]">
            {str}
          </span>
        );
      } else if (keyword) {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-[#c586c0] font-medium">
            {keyword}
          </span>
        );
      } else if (number) {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-[#b5cea8]">
            {number}
          </span>
        );
      } else if (typeName) {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-[#4ec9b0]">
            {typeName}
          </span>
        );
      } else {
        tokens.push(
          <span key={`t-${lineIdx}-${keyIdx++}`} className="text-gray-300">
            {full}
          </span>
        );
      }
      lastIndex = regex.lastIndex;
    }

    if (lastIndex < line.length) {
      tokens.push(
        <span key={`p-end-${lineIdx}-${keyIdx++}`} className="text-gray-300">
          {line.substring(lastIndex)}
        </span>
      );
    }

    if (tokens.length === 0) {
      tokens.push(<span key={`empty-${lineIdx}`}>&nbsp;</span>);
    }

    return (
      <div key={`line-${lineIdx}`} className="flex leading-6 hover:bg-white/5 transition-colors font-mono">
        <span className="w-12 select-none pr-4 text-right text-xs text-gray-600 shrink-0">
          {lineIdx + 1}
        </span>
        <span className="flex-1 whitespace-pre pl-1">{tokens}</span>
      </div>
    );
  });
}

// Visualizador de código isolado e memoizado
const CodeViewer = React.memo(function CodeViewer({
  content,
  language,
  loading,
}: {
  content?: string;
  language?: string;
  loading: boolean;
}) {
  const highlighted = useMemo(() => {
    if (!content) return null;
    return highlightCode(content, language || 'plaintext');
  }, [content, language]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-gray-400 font-mono">
        <Loader2 className="h-4 w-4 animate-spin mr-2" /> Carregando código...
      </div>
    );
  }

  if (!content) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-xs font-mono">
        <Code2 className="h-10 w-10 text-gray-600 mb-2" />
        <p>Selecione um arquivo no Explorador ao lado para visualizar e editar o código.</p>
      </div>
    );
  }

  return <div className="min-w-fit">{highlighted}</div>;
});

// Área de Input Isolada para digitação ultra-rápida sem re-renderizar o editor
const ChatInputArea = React.memo(function ChatInputArea({
  onSend,
  isExecuting,
  selectedModel,
}: {
  onSend: (prompt: string) => void;
  isExecuting: boolean;
  selectedModel: string;
}) {
  const [text, setText] = useState('');

  const handleSend = () => {
    if (!text.trim() || isExecuting) return;
    onSend(text.trim());
    setText('');
  };

  return (
    <div className="border-t border-[#3c3c3c] bg-[#1e1e1e] p-2.5 space-y-2 shrink-0">
      <div className="relative rounded-lg border border-[#3c3c3c] bg-[#252526] focus-within:border-[#007acc] transition-colors p-2">
        <textarea
          rows={2}
          placeholder="Ask anything, @ to mention, / for actions..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          className="w-full resize-none bg-transparent text-xs text-white placeholder-gray-500 focus:outline-hidden"
        />

        <div className="flex items-center justify-between pt-1 text-[11px] text-gray-400">
          <button
            type="button"
            className="flex items-center gap-1 rounded bg-[#1e1e1e] border border-[#3c3c3c] px-2 py-0.5 text-[10px] text-gray-300 hover:text-white"
          >
            <span>+ {selectedModel}</span>
            <ChevronDown className="h-3 w-3" />
          </button>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              title="Voz / Microfone"
              className="p-1 text-gray-400 hover:text-white transition-colors cursor-pointer"
            >
              <Mic className="h-3.5 w-3.5" />
            </button>

            <button
              type="button"
              onClick={handleSend}
              disabled={!text.trim() || isExecuting}
              className={cn(
                'flex h-6 w-6 items-center justify-center rounded-md bg-[#007acc] text-white transition-all',
                !text.trim() || isExecuting
                  ? 'opacity-40 cursor-not-allowed'
                  : 'hover:bg-[#0098ff] cursor-pointer'
              )}
            >
              {isExecuting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3 w-3" />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

export function AntigravityPanel({ isOpen, onClose, onOpenRequest }: AntigravityPanelProps) {
  const room = useRoomContext();

  // Estados de layout
  const [isStudioMode, setIsStudioMode] = useState(true);
  const [activeActivityTab, setActiveActivityTab] = useState<'explorer' | 'chat' | 'git'>('explorer');

  // Estados de arquivos e abas
  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set(['backend', 'frontend']));
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
  const [activeTabPath, setActiveTabPath] = useState<string>('');
  const [loadingFile, setLoadingFile] = useState(false);

  // Estados do Chat Antigravity
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        'Sistemas prontos! O Motor Antigravity está conectado e operando diretamente no workspace do Jarvis. ' +
        'Você pode me pedir para criar rotas, componentes, consertar bugs ou inspecionar qualquer arquivo ao lado.',
      timestamp: 'Agora',
      diffBadge: 'Workspace Conectado',
    },
  ]);
  const [isExecuting, setIsExecuting] = useState(false);
  const [statusEngine, setStatusEngine] = useState<'idle' | 'pensando' | 'executando_ferramenta' | 'concluido' | 'erro'>('idle');
  const [selectedModel] = useState('Gemini 3.8 Flash (Medium)');

  const chatEndRef = useRef<HTMLDivElement>(null);

  // Floating hooks
  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-antigravity-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-antigravity-panel-v1');

  // Carrega árvore do projeto ao abrir
  const fetchTree = useCallback(async () => {
    try {
      const res = await fetch('/api/antigravity?action=tree');
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.tree)) {
          setFileTree(json.tree);
        }
      }
    } catch (e) {
      console.warn('Erro ao carregar árvore:', e);
    }
  }, []);

  // Abre arquivo e coloca como aba ativa
  const handleOpenFile = useCallback(
    async (relPath: string) => {
      const existing = openTabs.find((t) => t.path === relPath);
      if (existing) {
        setActiveTabPath(relPath);
        return;
      }

      setLoadingFile(true);
      try {
        const res = await fetch(`/api/antigravity?action=file&path=${encodeURIComponent(relPath)}`);
        if (res.ok) {
          const json = await res.json();
          const newTab: OpenTab = {
            name: json.name || relPath.split('/').pop() || 'arquivo',
            path: json.path || relPath,
            content: json.content || '',
            language: detectLanguage(relPath),
          };
          setOpenTabs((prev) => [...prev, newTab]);
          setActiveTabPath(newTab.path);
        }
      } catch (e) {
        console.error('Falha ao abrir arquivo:', e);
      } finally {
        setLoadingFile(false);
      }
    },
    [openTabs]
  );

  const handleCloseTab = (path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const remaining = openTabs.filter((t) => t.path !== path);
    setOpenTabs(remaining);
    if (activeTabPath === path && remaining.length > 0) {
      setActiveTabPath(remaining[remaining.length - 1].path);
    } else if (remaining.length === 0) {
      setActiveTabPath('');
    }
  };

  const toggleFolder = (folderPath: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) {
        next.delete(folderPath);
      } else {
        next.add(folderPath);
      }
      return next;
    });
  };

  useEffect(() => {
    if (isOpen) {
      fetchTree();
      if (openTabs.length === 0) {
        handleOpenFile('backend/agent.py');
      }
    }
  }, [isOpen, fetchTree, handleOpenFile, openTabs.length]);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // Listener para eventos do LiveKit do Motor Antigravity
  useEffect(() => {
    const handleData = (payload: Uint8Array, _participant?: unknown, _kind?: unknown, topic?: string) => {
      if (topic !== ANTIGRAVITY_TOPIC) return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));

        if (packet?.type === 'antigravity_step' && packet.step) {
          const st = packet.step;
          if (st.tipo === 'inicio') {
            setIsExecuting(true);
            setStatusEngine('pensando');
            if (onOpenRequest) onOpenRequest();
          } else if (st.tipo === 'pensando') {
            setStatusEngine('pensando');
          } else if (st.tipo === 'ferramenta_inicio') {
            setStatusEngine('executando_ferramenta');
          } else if (st.tipo === 'concluido') {
            setStatusEngine('concluido');
            setIsExecuting(false);
            const arqs = st.arquivos_alterados?.join(', ') || '';
            const msgFinal: ChatMessage = {
              id: String(Date.now()),
              role: 'assistant',
              content: st.resumo || 'Tarefa concluída com sucesso pelo Antigravity!',
              timestamp: 'Agora',
              diffBadge: arqs ? `${st.arquivos_alterados.length} arquivos alterados` : undefined,
            };
            setMessages((prev) => [...prev, msgFinal]);

            if (activeTabPath && st.arquivos_alterados?.includes(activeTabPath)) {
              handleOpenFile(activeTabPath);
            }
          } else if (st.tipo === 'erro') {
            setStatusEngine('erro');
            setIsExecuting(false);
          }
        }

        if (packet?.type === 'antigravity_status') {
          if (packet.estado) setStatusEngine(packet.estado);
          if (packet.isExecuting !== undefined) setIsExecuting(Boolean(packet.isExecuting));
          if (packet.open_panel && onOpenRequest) onOpenRequest();
        }
      } catch (e) {
        console.warn('Erro ao decodificar evento antigravity:', e);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room, onOpenRequest, activeTabPath, handleOpenFile]);

  // Envio de prompt disparado pelo ChatInputArea
  const handleSendPrompt = useCallback(
    async (promptText: string) => {
      const userMsg: ChatMessage = {
        id: String(Date.now()),
        role: 'user',
        content: promptText,
        timestamp: 'Agora',
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsExecuting(true);
      setStatusEngine('pensando');

      // 1. Tenta notificar o LiveKit se a sala estiver conectada
      try {
        if (room?.localParticipant) {
          const payload = {
            type: 'jarvis_antigravity_command',
            action: 'run_task',
            tarefa: promptText,
            arquivo_alvo: activeTabPath || '',
          };
          const encoder = new TextEncoder();
          room.localParticipant
            .publishData(encoder.encode(JSON.stringify(payload)), {
              reliable: true,
              topic: ANTIGRAVITY_TOPIC,
            })
            .catch(() => {});
        }
      } catch (err) {
        console.warn('LiveKit data channel não disponível, usando API direta:', err);
      }

      // 2. Executa a requisição diretamente via API REST do Antigravity
      try {
        const res = await fetch('/api/antigravity', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action: 'chat',
            message: promptText,
            activeTab: activeTabPath || '',
          }),
        });

        const data = await res.json();

        if (data.ok) {
          const arqs: string[] = data.arquivos_alterados || [];
          const diffBadge =
            arqs.length > 0
              ? `${arqs.length} arquivo${arqs.length > 1 ? 's' : ''} alterado${arqs.length > 1 ? 's' : ''}`
              : undefined;

          const assistantMsg: ChatMessage = {
            id: String(Date.now() + 1),
            role: 'assistant',
            content: data.reply || 'Processamento concluído com sucesso pelo Antigravity.',
            timestamp: 'Agora',
            diffBadge,
          };

          setMessages((prev) => [...prev, assistantMsg]);
          setStatusEngine('concluido');

          // Recarrega arquivo aberto caso tenha sido alterado
          if (activeTabPath && arqs.includes(activeTabPath)) {
            handleOpenFile(activeTabPath);
          }
          fetchTree();
        } else {
          const errorMsg: ChatMessage = {
            id: String(Date.now() + 1),
            role: 'assistant',
            content: `⚠️ Erro no Antigravity: ${data.error || 'Não foi possível obter resposta.'}`,
            timestamp: 'Agora',
          };
          setMessages((prev) => [...prev, errorMsg]);
          setStatusEngine('erro');
        }
      } catch (err: unknown) {
        const errObj = err as { message?: string };
        const errorMsg: ChatMessage = {
          id: String(Date.now() + 1),
          role: 'assistant',
          content: `⚠️ Erro de conexão com o servidor Antigravity: ${errObj?.message || String(err)}`,
          timestamp: 'Agora',
        };
        setMessages((prev) => [...prev, errorMsg]);
        setStatusEngine('erro');
      } finally {
        setIsExecuting(false);
      }
    },
    [activeTabPath, room, handleOpenFile, fetchTree]
  );

  const activeTab = useMemo(
    () => openTabs.find((t) => t.path === activeTabPath),
    [openTabs, activeTabPath]
  );

  // Renderizador de nós da árvore de arquivos
  const renderTreeItem = useCallback(
    (node: FileTreeNode, depth = 0): React.ReactNode => {
      const isFolder = node.type === 'folder';
      const isExpanded = expandedFolders.has(node.path);
      const isSelected = activeTabPath === node.path;

      return (
        <div key={node.path} className="text-xs font-mono select-none">
          <div
            onClick={() => {
              if (isFolder) {
                toggleFolder(node.path);
              } else {
                handleOpenFile(node.path);
              }
            }}
            style={{ paddingLeft: `${depth * 14 + 10}px` }}
            className={cn(
              'flex items-center gap-1.5 py-1 pr-2 rounded-sm cursor-pointer transition-colors',
              isSelected
                ? 'bg-[#04395e] text-white font-medium'
                : 'text-gray-300 hover:bg-[#2a2d2e] hover:text-white'
            )}
          >
            {isFolder ? (
              <>
                {isExpanded ? (
                  <ChevronDown className="h-3.5 w-3.5 text-gray-400 shrink-0" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 text-gray-400 shrink-0" />
                )}
                {isExpanded ? (
                  <FolderOpen className="h-3.5 w-3.5 text-[#dcb67a] shrink-0" />
                ) : (
                  <Folder className="h-3.5 w-3.5 text-[#dcb67a] shrink-0" />
                )}
              </>
            ) : (
              <>
                <span className="w-3.5 shrink-0" />
                <FileCode2 className="h-3.5 w-3.5 text-[#519aba] shrink-0" />
              </>
            )}
            <span className="truncate">{node.name}</span>
          </div>

          {isFolder && isExpanded && node.children && (
            <div>{node.children.map((child) => renderTreeItem(child, depth + 1))}</div>
          )}
        </div>
      );
    },
    [activeTabPath, expandedFolders, handleOpenFile]
  );

  if (!isOpen) return null;

  return (
    <aside
      aria-label="Antigravity IDE Studio"
      data-hud-panel="antigravity"
      style={isStudioMode ? undefined : dragStyle}
      {...(isStudioMode ? {} : dragHandlers)}
      className={cn(
        'pointer-events-auto z-40 overflow-hidden font-sans border text-white transition-all duration-200',
        isStudioMode
          ? 'fixed inset-3 md:inset-6 rounded-xl border-[#3c3c3c] bg-[#1e1e1e] shadow-[0_16px_50px_rgba(0,0,0,0.85)] flex flex-col'
          : cn(
              'absolute top-12 left-12 w-[850px] h-[650px] rounded-xl border-[#3c3c3c] bg-[#1e1e1e] shadow-[0_12px_40px_rgba(0,0,0,0.7)] flex flex-col',
              isDragging || isResizingEdge ? 'ring-1 ring-[#007acc]' : ''
            )
      )}
    >
      {/* Top Window Bar (Estilo VS Code Titlebar) */}
      <div className="flex h-9 items-center justify-between border-b border-[#3c3c3c] bg-[#323233] px-3 select-none shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-[#007acc]" />
            <span className="text-xs font-semibold tracking-wide text-gray-200">
              Antigravity Studio — Jarvis Workspace
            </span>
          </div>
          <span className="hidden md:inline-block rounded bg-[#252526] border border-[#3c3c3c] px-2 py-0.5 text-[10px] text-gray-400 font-mono">
            {activeTabPath || 'backend/agent.py'}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsStudioMode(!isStudioMode)}
            title={isStudioMode ? 'Modo Janela Flutuante' : 'Modo IDE Studio Completo'}
            className="flex items-center gap-1 rounded bg-[#252526] hover:bg-[#3c3c3c] px-2 py-1 text-[11px] text-gray-300 transition-colors cursor-pointer border border-[#454545]"
          >
            {isStudioMode ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
            <span className="hidden sm:inline">{isStudioMode ? 'Flutuante' : 'IDE Full'}</span>
          </button>

          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
          />
        </div>
      </div>

      {!minimized && (
        <div className="flex flex-1 overflow-hidden">
          {/* 1. Activity Bar (Faixa Vertical Extrema Esquerda) */}
          <div className="flex w-12 flex-col items-center justify-between border-r border-[#3c3c3c] bg-[#333333] py-2 shrink-0 select-none">
            <div className="flex flex-col items-center gap-4">
              <button
                onClick={() => setActiveActivityTab('explorer')}
                title="Explorer"
                className={cn(
                  'p-2 rounded-md transition-all cursor-pointer relative',
                  activeActivityTab === 'explorer'
                    ? 'text-white'
                    : 'text-gray-400 hover:text-gray-200'
                )}
              >
                {activeActivityTab === 'explorer' && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-0.5 bg-white" />
                )}
                <FolderTree className="h-5 w-5" />
              </button>

              <button
                onClick={() => setActiveActivityTab('chat')}
                title="Antigravity Chat"
                className={cn(
                  'p-2 rounded-md transition-all cursor-pointer relative',
                  activeActivityTab === 'chat'
                    ? 'text-white'
                    : 'text-gray-400 hover:text-gray-200'
                )}
              >
                {activeActivityTab === 'chat' && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-0.5 bg-white" />
                )}
                <Sparkles className="h-5 w-5 text-emerald-400" />
              </button>

              <button
                onClick={() => setActiveActivityTab('git')}
                title="Source Control"
                className="p-2 text-gray-400 hover:text-gray-200 transition-colors cursor-pointer"
              >
                <GitBranch className="h-5 w-5" />
              </button>
            </div>

            <div className="flex flex-col items-center gap-3">
              <button
                onClick={fetchTree}
                title="Atualizar Workspace"
                className="p-2 text-gray-400 hover:text-white transition-colors cursor-pointer"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* 2. Coluna Esquerda Dividida: Folder Tree (Acima) + Chat Antigravity (Abaixo) */}
          <div className="flex w-80 md:w-96 flex-col border-r border-[#3c3c3c] bg-[#252526] shrink-0 overflow-hidden">
            {/* Topo Esquerdo: Explorador do Projeto */}
            <div className="flex flex-col h-48 border-b border-[#3c3c3c] shrink-0">
              <div className="flex items-center justify-between px-3 py-1.5 bg-[#252526] text-xs font-semibold text-gray-300 uppercase tracking-wider select-none border-b border-[#333333]">
                <div className="flex items-center gap-1.5">
                  <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
                  <span>JARVIS WORKSPACE</span>
                </div>
                <button onClick={fetchTree} title="Recarregar arquivos" className="cursor-pointer">
                  <RefreshCw className="h-3 w-3 text-gray-400 hover:text-white" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto p-1.5 scrollbar-thin scrollbar-thumb-gray-600">
                {fileTree.length === 0 ? (
                  <div className="flex items-center justify-center h-full text-xs text-gray-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> Carregando pastas...
                  </div>
                ) : (
                  fileTree.map((node) => renderTreeItem(node, 0))
                )}
              </div>
            </div>

            {/* Inferior Esquerdo: Chat Antigravity (Com Input Isolado) */}
            <div className="flex flex-1 flex-col overflow-hidden bg-[#1e1e1e]">
              <div className="flex items-center justify-between px-3 py-2 border-b border-[#333333] bg-[#252526] text-xs font-semibold text-gray-200">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-emerald-400" />
                  <span>Antigravity</span>
                </div>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[9px] font-mono uppercase tracking-wide border',
                    statusEngine === 'idle' && 'border-white/20 text-gray-300',
                    statusEngine === 'pensando' && 'border-amber-500/40 bg-amber-500/10 text-amber-300 animate-pulse',
                    statusEngine === 'executando_ferramenta' && 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300 animate-pulse',
                    statusEngine === 'concluido' && 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  )}
                >
                  {statusEngine === 'idle' && 'Pronto'}
                  {statusEngine === 'pensando' && 'Pensando...'}
                  {statusEngine === 'executando_ferramenta' && 'Editando Código'}
                  {statusEngine === 'concluido' && 'Concluído'}
                </span>
              </div>

              {/* Mensagens do Chat */}
              <div className="flex-1 overflow-y-auto p-3 space-y-3 font-sans text-xs select-text scrollbar-thin scrollbar-thumb-gray-700">
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={cn(
                      'rounded-lg p-2.5 space-y-1.5 transition-all leading-relaxed',
                      m.role === 'user'
                        ? 'bg-[#004b75]/40 border border-[#007acc]/40 text-gray-100'
                        : 'bg-[#252526] border border-[#3c3c3c] text-gray-200'
                    )}
                  >
                    <div className="flex items-center justify-between text-[10px] text-gray-400">
                      <span className="font-semibold text-gray-300">
                        {m.role === 'user' ? 'Você' : 'Antigravity'}
                      </span>
                      <span>{m.timestamp}</span>
                    </div>

                    <div className="whitespace-pre-wrap">{m.content}</div>

                    {m.diffBadge && (
                      <div className="mt-2 flex items-center justify-between rounded bg-[#1e1e1e] border border-[#3c3c3c] px-2 py-1 text-[10px]">
                        <span className="text-emerald-400 font-mono font-medium">{m.diffBadge}</span>
                        <span className="text-[#007acc] cursor-pointer hover:underline font-semibold">Review</span>
                      </div>
                    )}
                  </div>
                ))}

                {isExecuting && (
                  <div className="rounded-lg p-3 space-y-2 bg-[#252526] border border-[#007acc]/40 text-gray-200">
                    <div className="flex items-center justify-between text-[11px] text-gray-300">
                      <span className="font-semibold text-emerald-400 flex items-center gap-1.5">
                        <Sparkles className="h-3.5 w-3.5 animate-spin" /> Antigravity processando...
                      </span>
                      <span className="text-[10px] text-gray-500 font-mono">Agora</span>
                    </div>
                    <div className="text-xs text-gray-300 flex items-center gap-2 pt-1 font-mono">
                      <Loader2 className="h-4 w-4 animate-spin text-emerald-400 shrink-0" />
                      <span>Analisando o código e preparando resposta...</span>
                    </div>
                  </div>
                )}
                <div ref={chatEndRef} />
              </div>

              {/* Componente Isolado de Input de Chat */}
              <ChatInputArea
                onSend={handleSendPrompt}
                isExecuting={isExecuting}
                selectedModel={selectedModel}
              />
            </div>
          </div>

          {/* 3. Coluna Direita: Editor com Abas e Código com Linhas */}
          <div className="flex flex-1 flex-col overflow-hidden bg-[#1e1e1e]">
            {/* Barra de Abas do Editor */}
            <div className="flex h-9 items-center border-b border-[#3c3c3c] bg-[#252526] overflow-x-auto select-none scrollbar-none">
              {openTabs.length === 0 ? (
                <div className="px-3 text-xs text-gray-500 italic">Nenhum arquivo aberto</div>
              ) : (
                openTabs.map((tab) => {
                  const isActive = tab.path === activeTabPath;
                  return (
                    <div
                      key={tab.path}
                      onClick={() => setActiveTabPath(tab.path)}
                      className={cn(
                        'flex h-full items-center gap-2 border-r border-[#3c3c3c] px-3.5 text-xs font-mono cursor-pointer transition-all shrink-0',
                        isActive
                          ? 'bg-[#1e1e1e] text-white border-t-2 border-t-[#007acc] font-medium'
                          : 'bg-[#2d2d2d] text-gray-400 hover:bg-[#1e1e1e]/60 hover:text-gray-200'
                      )}
                    >
                      <FileCode2 className="h-3.5 w-3.5 text-[#519aba]" />
                      <span>{tab.name}</span>
                      {tab.isModified && <span className="text-[10px] text-amber-400 font-bold">M</span>}
                      <button
                        onClick={(e) => handleCloseTab(tab.path, e)}
                        className="rounded p-0.5 text-gray-500 hover:bg-white/10 hover:text-gray-300 cursor-pointer"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>

            {/* Breadcrumbs de Navegação */}
            {activeTab && (
              <div className="flex items-center gap-1.5 border-b border-[#2d2d2d] bg-[#1e1e1e] px-4 py-1 text-[11px] text-gray-400 font-mono select-none">
                <span className="text-gray-500">Jarvis</span>
                <ChevronRight className="h-3 w-3 text-gray-600" />
                {activeTab.path.split('/').map((part, idx, arr) => (
                  <React.Fragment key={idx}>
                    <span className={idx === arr.length - 1 ? 'text-gray-200 font-medium' : 'text-gray-400'}>
                      {part}
                    </span>
                    {idx < arr.length - 1 && <ChevronRight className="h-3 w-3 text-gray-600" />}
                  </React.Fragment>
                ))}
              </div>
            )}

            {/* Área de Visualização do Código Memoizada */}
            <div className="flex-1 overflow-auto bg-[#1e1e1e] p-2 text-xs select-text scrollbar-thin scrollbar-thumb-gray-700">
              <CodeViewer
                content={activeTab?.content}
                language={activeTab?.language}
                loading={loadingFile}
              />
            </div>

            {/* Status Bar no Rodapé (Idêntico ao VS Code) */}
            <div className="flex h-6 items-center justify-between border-t border-[#3c3c3c] bg-[#007acc] px-3 text-[11px] font-mono text-white select-none">
              <div className="flex items-center gap-3">
                <span>Ln {activeTab ? activeTab.content.split('\n').length : 0}, Col 1</span>
                <span>Spaces: 4</span>
                <span>UTF-8</span>
                <span>CRLF</span>
              </div>
              <div className="flex items-center gap-3">
                <span>{'{ }'} {activeTab?.language?.toUpperCase() || 'PYTHON'}</span>
                <span className="flex items-center gap-1 font-semibold">
                  <Sparkles className="h-3 w-3 text-white" /> Antigravity Engine
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function FolderTree(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      {...props}
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 8 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    </svg>
  );
}
