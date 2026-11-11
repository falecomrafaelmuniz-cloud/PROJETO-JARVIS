'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { ExternalLink, FileText, Image as ImageIcon, Paperclip, Plus, Trash2, X } from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const REMINDERS_TOPIC = 'jarvis.reminders';

export type ReminderStatus = 'pendente' | 'em_andamento' | 'concluido';

export interface ReminderAttachment {
  id: string;
  nome: string;
  mime: string;
  arquivo_salvo: string;
}

export interface ReminderItem {
  id: string;
  texto: string;
  status: ReminderStatus;
  criado_em: string;
  concluido_em_data?: string;
  anexos?: ReminderAttachment[];
}

interface PendingAttachment {
  nome: string;
  mime: string;
  base64: string;
}

interface RemindersPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenRequest?: () => void;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = (err) => reject(err);
    reader.readAsDataURL(file);
  });
}

export function RemindersPanel({ isOpen, onClose, onOpenRequest }: RemindersPanelProps) {
  const room = useRoomContext();
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [novoTexto, setNovoTexto] = useState('');
  const [pendingFiles, setPendingFiles] = useState<PendingAttachment[]>([]);
  const [adding, setAdding] = useState(false);
  const [attachingToId, setAttachingToId] = useState<string | null>(null);

  const newFileInputRef = useRef<HTMLInputElement>(null);
  const itemFileInputRef = useRef<HTMLInputElement>(null);

  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-reminders-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-reminders-panel-v2');

  const fetchReminders = useCallback(async () => {
    try {
      const res = await fetch('/api/reminders', { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.reminders)) {
          setReminders(json.reminders);
        }
      }
    } catch (err) {
      console.warn('Erro ao carregar lembretes:', err);
    }
  }, []);

  useEffect(() => {
    fetchReminders();
    const timer = window.setInterval(fetchReminders, 60_000);
    return () => window.clearInterval(timer);
  }, [fetchReminders]);

  useEffect(() => {
    if (isOpen) {
      fetchReminders();
    }
  }, [isOpen, fetchReminders]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== REMINDERS_TOPIC) return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));
        if (packet?.type === 'jarvis_reminders_sync') {
          if (Array.isArray(packet.reminders)) {
            setReminders(packet.reminders);
          } else {
            fetchReminders();
          }
          if (packet.open_panel && onOpenRequest) {
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
  }, [room, fetchReminders, onOpenRequest]);

  const handleSelectNewFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const converted: PendingAttachment[] = [];
    for (const file of Array.from(files)) {
      try {
        const b64 = await fileToBase64(file);
        converted.push({
          nome: file.name,
          mime: file.type || 'application/octet-stream',
          base64: b64,
        });
      } catch (err) {
        console.warn('Erro ao ler arquivo:', err);
      }
    }
    setPendingFiles((prev) => [...prev, ...converted]);
    e.target.value = '';
  };

  const handlePastePrint = async (e: React.ClipboardEvent<HTMLInputElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) {
          try {
            const b64 = await fileToBase64(file);
            const ext = file.type.split('/')[1] || 'png';
            const nomeArquivo =
              file.name && file.name !== 'image.png'
                ? file.name
                : `Print_${new Date().toLocaleTimeString('pt-BR').replace(/:/g, '-')}.${ext}`;
            setPendingFiles((prev) => [
              ...prev,
              {
                nome: nomeArquivo,
                mime: file.type || 'image/png',
                base64: b64,
              },
            ]);
          } catch (err) {
            console.warn('Erro ao colar print:', err);
          }
        }
      }
    }
  };

  const handleSelectItemFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    const targetId = attachingToId;
    if (!files || files.length === 0 || !targetId) return;

    const converted: PendingAttachment[] = [];
    for (const file of Array.from(files)) {
      try {
        const b64 = await fileToBase64(file);
        converted.push({
          nome: file.name,
          mime: file.type || 'application/octet-stream',
          base64: b64,
        });
      } catch (err) {
        console.warn('Erro ao ler anexo:', err);
      }
    }
    e.target.value = '';
    setAttachingToId(null);

    if (converted.length > 0) {
      try {
        const res = await fetch('/api/reminders', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action: 'attach',
            id: targetId,
            anexos: converted,
          }),
        });
        const json = await res.json();
        if (json.ok && Array.isArray(json.reminders)) {
          setReminders(json.reminders);
        }
      } catch (err) {
        console.warn('Erro ao anexar arquivo no lembrete:', err);
      }
    }
  };

  const handleRemoveAttachment = async (reminderId: string, attachmentId: string) => {
    try {
      const res = await fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'remove_attachment',
          id: reminderId,
          attachment_id: attachmentId,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.reminders)) {
        setReminders(json.reminders);
      }
    } catch (err) {
      console.warn('Erro ao remover anexo do lembrete:', err);
    }
  };

  const handleAddReminder = async (e: React.FormEvent) => {
    e.preventDefault();
    const textoLimpo = novoTexto.trim();
    if ((!textoLimpo && pendingFiles.length === 0) || adding) return;

    try {
      setAdding(true);
      const res = await fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'add',
          texto: textoLimpo,
          status: 'pendente',
          anexos: pendingFiles,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.reminders)) {
        setReminders(json.reminders);
        setNovoTexto('');
        setPendingFiles([]);
      }
    } catch (err) {
      console.warn('Erro ao adicionar lembrete:', err);
    } finally {
      setAdding(false);
    }
  };

  const handleUpdateStatus = async (id: string, nextStatus: ReminderStatus) => {
    try {
      const res = await fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'update_status',
          id,
          status: nextStatus,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.reminders)) {
        setReminders(json.reminders);
      }
    } catch (err) {
      console.warn('Erro ao atualizar status do lembrete:', err);
    }
  };

  const handleCycleStatus = (item: ReminderItem) => {
    const next: ReminderStatus =
      item.status === 'pendente'
        ? 'em_andamento'
        : item.status === 'em_andamento'
          ? 'concluido'
          : 'pendente';
    handleUpdateStatus(item.id, next);
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch('/api/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'delete',
          id,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.reminders)) {
        setReminders(json.reminders);
      }
    } catch (err) {
      console.warn('Erro ao excluir lembrete:', err);
    }
  };

  if (!isOpen) return null;

  return (
    <aside
      aria-label="Lembretes do Jarvis"
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pela mãozinha para mover · Arraste pelas bordas para aumentar ou diminuir"
      className={cn(
        'pointer-events-auto absolute top-14 right-[calc(min(17rem,50vw-1.125rem)+1.25rem)] z-30 w-[min(20rem,calc(100vw-1.5rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/30 bg-black/80 text-white backdrop-blur-2xl',
        'shadow-[0_0_20px_rgba(6,182,212,0.18)] shadow-[0_8px_32px_rgba(0,0,0,0.6)] ring-1 shadow-cyan-950/40 ring-cyan-500/20 transition-all duration-200',
        isDragging || isResizingEdge
          ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
          : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Visual de linhas holográficas nos cantos superiores (idêntico ao ActivityPanel) */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-12 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />

      {/* Inputs ocultos para anexar qualquer extensão de arquivo */}
      <input
        ref={newFileInputRef}
        type="file"
        multiple
        onChange={handleSelectNewFiles}
        className="hidden"
      />
      <input
        ref={itemFileInputRef}
        type="file"
        multiple
        onChange={handleSelectItemFiles}
        className="hidden"
      />

      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold tracking-wide">Lembretes</h2>
          <p className="mt-0.5 text-[9px] text-white/50">
            Vermelho pendente · Amarelo andamento · Verde concluído
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="rounded-full border border-cyan-300/20 px-1.5 py-0.5 text-[8px] tracking-wider text-cyan-200/80 uppercase">
            {reminders.length} {reminders.length === 1 ? 'item' : 'itens'}
          </span>
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
            closeTitle="Fechar painel de lembretes"
          />
        </div>
      </div>

      {!minimized && (
      <>
      {/* Barra rápida para adicionar novo lembrete + Clipe de anexo */}
      <form
        onSubmit={handleAddReminder}
        data-no-drag="true"
        className="border-b border-white/10 px-2.5 py-2"
      >
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            value={novoTexto}
            onChange={(e) => setNovoTexto(e.target.value)}
            onPaste={handlePastePrint}
            placeholder="Novo lembrete (ou Ctrl+V p/ print)..."
            className="min-w-0 flex-1 rounded-md border border-white/15 bg-black/60 px-2 py-1 text-[10px] text-white placeholder-white/40 focus:border-cyan-400 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => newFileInputRef.current?.click()}
            title="Anexar arquivo (PDF, foto, print, documento, qualquer extensão)"
            className={cn(
              'inline-flex items-center justify-center rounded-md border p-1.5 transition',
              pendingFiles.length > 0
                ? 'border-cyan-400/60 bg-cyan-500/25 text-cyan-200'
                : 'border-white/15 bg-white/5 text-white/70 hover:border-cyan-400/40 hover:text-cyan-200'
            )}
          >
            <Paperclip className="size-3" />
          </button>
          <button
            type="submit"
            disabled={(!novoTexto.trim() && pendingFiles.length === 0) || adding}
            title="Adicionar lembrete (entra como Pendente)"
            className="inline-flex items-center gap-1 rounded-md border border-cyan-400/40 bg-cyan-500/20 px-2 py-1 font-mono text-[10px] font-bold text-cyan-200 transition hover:bg-cyan-500/35 disabled:opacity-40"
          >
            <Plus className="size-3" />
            <span>Criar</span>
          </button>
        </div>

        {/* Chips de arquivos selecionados antes de salvar */}
        {pendingFiles.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {pendingFiles.map((pf, i) => (
              <span
                key={`${pf.nome}-${i}`}
                className="inline-flex max-w-full items-center gap-1 rounded border border-cyan-400/30 bg-cyan-950/60 px-1.5 py-0.5 text-[9px] text-cyan-200"
              >
                <Paperclip className="size-2.5 shrink-0 text-cyan-400" />
                <span className="truncate max-w-[11rem]">{pf.nome}</span>
                <button
                  type="button"
                  onClick={() =>
                    setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))
                  }
                  className="ml-0.5 text-white/50 hover:text-rose-300"
                >
                  <X className="size-2.5" />
                </button>
              </span>
            ))}
          </div>
        )}
      </form>

      <ol
        aria-live="polite"
        data-scrollable="true"
        className="flex max-h-[min(45vh,22rem)] flex-col gap-1.5 overflow-y-auto p-2"
      >
        {reminders.length === 0 ? (
          <li className="px-1.5 py-4 text-center text-[10px] text-white/45">
            Nenhum lembrete ativo. Digite acima, anexe no clipe ou peça por voz ao Jarvis!
          </li>
        ) : (
          reminders.map((item, idx) => {
            const numberStr = String(idx + 1).padStart(2, '0');
            const isRed = item.status === 'pendente';
            const isYellow = item.status === 'em_andamento';
            const isGreen = item.status === 'concluido';

            const statusLabel = isRed
              ? 'Pendente'
              : isYellow
                ? 'Em andamento'
                : 'Concluído · Apaga amanhã';

            const anexos = Array.isArray(item.anexos) ? item.anexos : [];

            return (
              <li
                key={item.id}
                className={cn(
                  'flex items-start gap-2 rounded-md border px-2 py-1.5 transition-colors',
                  isRed && 'border-red-400/40 bg-red-500/10',
                  isYellow && 'border-amber-300/35 bg-amber-400/10',
                  isGreen && 'border-emerald-400/30 bg-emerald-500/10'
                )}
              >
                <button
                  type="button"
                  onClick={() => handleCycleStatus(item)}
                  title="Clique para alternar status (Vermelho ➔ Amarelo ➔ Verde)"
                  className={cn(
                    'mt-0.5 flex size-6 shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold transition hover:scale-105',
                    isRed && 'bg-red-500/20 text-red-300',
                    isYellow && 'bg-amber-500/20 text-amber-200',
                    isGreen && 'bg-emerald-500/20 text-emerald-300'
                  )}
                >
                  {numberStr}
                </button>

                <div className="min-w-0 flex-1">
                  <p
                    className={cn(
                      'text-[10px] leading-4 text-white/90 break-words',
                      isGreen && 'line-through text-white/65'
                    )}
                  >
                    {item.texto}
                  </p>

                  {/* Lista de anexos do lembrete */}
                  {anexos.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {anexos.map((anexo) => {
                        const isImage = (anexo.mime || '').startsWith('image/');
                        const fileUrl = `/api/reminders?file=${encodeURIComponent(
                          anexo.arquivo_salvo
                        )}&mime=${encodeURIComponent(
                          anexo.mime || 'application/octet-stream'
                        )}&name=${encodeURIComponent(anexo.nome)}`;

                        return (
                          <span
                            key={anexo.id}
                            className="inline-flex max-w-full items-center gap-1 rounded border border-white/15 bg-black/50 px-1.5 py-0.5 text-[9px] text-cyan-200 transition hover:border-cyan-400/50"
                          >
                            <a
                              href={fileUrl}
                              target="_blank"
                              rel="noreferrer"
                              title={`Abrir ${anexo.nome}`}
                              className="inline-flex items-center gap-1 truncate max-w-[10rem] hover:underline"
                            >
                              {isImage ? (
                                <ImageIcon className="size-2.5 shrink-0 text-cyan-300" />
                              ) : (
                                <FileText className="size-2.5 shrink-0 text-cyan-300" />
                              )}
                              <span className="truncate">{anexo.nome}</span>
                              <ExternalLink className="size-2 shrink-0 opacity-60" />
                            </a>
                            <button
                              type="button"
                              onClick={() => handleRemoveAttachment(item.id, anexo.id)}
                              title="Remover anexo"
                              className="ml-0.5 text-white/40 hover:text-rose-300"
                            >
                              <X className="size-2.5" />
                            </button>
                          </span>
                        );
                      })}
                    </div>
                  )}

                  <div className="mt-0.5 flex items-center gap-1.5">
                    <p
                      className={cn(
                        'text-[8px] font-medium tracking-wide uppercase',
                        isRed && 'text-red-300',
                        isYellow && 'text-amber-200',
                        isGreen && 'text-emerald-300'
                      )}
                    >
                      {statusLabel}
                    </p>
                    {item.criado_em && (
                      <span className="text-[8px] text-white/35">· {item.criado_em}</span>
                    )}
                  </div>
                </div>

                {/* Clipe para anexar arquivo neste item + Seletores de cor (Vermelho / Amarelo / Verde) + Excluir */}
                <div className="mt-1 flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      setAttachingToId(item.id);
                      itemFileInputRef.current?.click();
                    }}
                    title="Anexar arquivo neste lembrete (PDF, foto, print, etc.)"
                    className="mr-0.5 rounded p-0.5 text-white/45 transition hover:bg-cyan-500/20 hover:text-cyan-300"
                  >
                    <Paperclip className="size-2.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleUpdateStatus(item.id, 'pendente')}
                    title="Vermelho: Pendente"
                    className={cn(
                      'size-2.5 rounded-full bg-red-400 transition',
                      isRed
                        ? 'scale-110 ring-2 ring-red-300/80 animate-pulse'
                        : 'opacity-35 hover:opacity-80'
                    )}
                  />
                  <button
                    type="button"
                    onClick={() => handleUpdateStatus(item.id, 'em_andamento')}
                    title="Amarelo: Em andamento"
                    className={cn(
                      'size-2.5 rounded-full bg-amber-400 transition',
                      isYellow
                        ? 'scale-110 ring-2 ring-amber-200/80'
                        : 'opacity-35 hover:opacity-80'
                    )}
                  />
                  <button
                    type="button"
                    onClick={() => handleUpdateStatus(item.id, 'concluido')}
                    title="Verde: Concluído (apaga automaticamente no dia seguinte)"
                    className={cn(
                      'size-2.5 rounded-full bg-emerald-400 transition',
                      isGreen
                        ? 'scale-110 ring-2 ring-emerald-200/80'
                        : 'opacity-35 hover:opacity-80'
                    )}
                  />
                  <button
                    type="button"
                    onClick={() => handleDelete(item.id)}
                    title="Excluir lembrete agora"
                    className="ml-0.5 rounded p-0.5 text-white/35 transition hover:bg-rose-500/20 hover:text-rose-300"
                  >
                    <Trash2 className="size-2.5" />
                  </button>
                </div>
              </li>
            );
          })
        )}
      </ol>
      </>
      )}
    </aside>
  );
}
