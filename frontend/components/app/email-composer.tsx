'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { useRoomContext } from '@livekit/components-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const EMAIL_TOPIC = 'jarvis.email';

interface EmailAttachment {
  id: string;
  tipo: 'local' | 'drive';
  nome: string;
  caminho_ou_id: string;
  mime_type?: string;
}

interface EmailDraftState {
  aberto: boolean;
  para: string;
  cc: string;
  assunto: string;
  mensagem: string;
  anexos: EmailAttachment[];
  status: 'rascunho' | 'enviando' | 'enviado' | 'erro';
  erro: string;
}

const INITIAL_DRAFT: EmailDraftState = {
  aberto: false,
  para: '',
  cc: '',
  assunto: '',
  mensagem: '',
  anexos: [],
  status: 'rascunho',
  erro: '',
};

export function EmailComposer() {
  const room = useRoomContext();
  const [draft, setDraft] = useState<EmailDraftState>(INITIAL_DRAFT);
  const [showCc, setShowCc] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-email-composer-v1');

  const publishUserUpdate = useCallback(
    async (payload: Record<string, unknown>) => {
      try {
        const data = new TextEncoder().encode(
          JSON.stringify({
            type: 'jarvis_email_user_update',
            ...payload,
          })
        );
        await room.localParticipant.publishData(data, {
          reliable: true,
          topic: EMAIL_TOPIC,
        });
      } catch (err) {
        console.warn('Falha ao sincronizar rascunho de e-mail:', err);
      }
    },
    [room]
  );

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== EMAIL_TOPIC) return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));
        if (packet?.type === 'jarvis_email_draft') {
          setDraft({
            aberto: Boolean(packet.aberto),
            para: String(packet.para ?? ''),
            cc: String(packet.cc ?? ''),
            assunto: String(packet.assunto ?? ''),
            mensagem: String(packet.mensagem ?? ''),
            anexos: Array.isArray(packet.anexos) ? packet.anexos : [],
            status: packet.status ?? 'rascunho',
            erro: String(packet.erro ?? ''),
          });
          if (packet.cc) {
            setShowCc(true);
          }
        }
      } catch {
        // Ignora pacotes malformados
      }
    };

    const handleTranscription = (segments: Array<{ text?: string }> | undefined) => {
      if (!Array.isArray(segments)) return;
      const texto = segments
        .map((s) => s?.text || '')
        .join(' ')
        .toLowerCase();
      if (!texto) return;

      const pediuEmail =
        /\b(mandar|manda|enviar|envia|escrever|escreve|redigir|redige|criar|cria|preparar|prepara)\b.{0,35}\b(e-?mail|email)\b/i.test(
          texto
        ) ||
        /\bnovo\s+(e-?mail|email)\b/i.test(texto) ||
        /\bpara\s+quem\s+([eé]\s+o\s+(e-?mail|email)|(voc[eê]\s+)?(quer|deseja|gostaria|vai)\s+(mandar|enviar))\b/i.test(
          texto
        ) ||
        /\bqual\s+([eé]\s+)?o\s+(destinat[aá]rio|endere[cç]o\s+de\s+e-?mail)\b/i.test(texto);

      if (pediuEmail) {
        setDraft((prev) => {
          if (prev.aberto && prev.status !== 'enviado') return prev;
          return {
            ...(prev.status === 'enviado' ? INITIAL_DRAFT : prev),
            aberto: true,
            status: 'rascunho',
            erro: '',
          };
        });
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    room.on(RoomEvent.TranscriptionReceived, handleTranscription);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
    };
  }, [room]);

  const hasPara = draft.para.trim().length > 0;
  const hasAssunto = draft.assunto.trim().length > 0;
  const hasMensagem = draft.mensagem.trim().length > 0;
  const hasAnexos = draft.anexos.length > 0;

  const nextStep = !hasPara
    ? 'para'
    : !hasAssunto
      ? 'assunto'
      : !hasMensagem
        ? 'mensagem'
        : 'pronto';

  const handleFieldChange = (field: 'para' | 'cc' | 'assunto' | 'mensagem', value: string) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
    void publishUserUpdate({ [field]: value });
  };

  const handleManualFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;

    setIsUploading(true);
    try {
      const formData = new FormData();
      Array.from(selectedFiles).forEach((file) => {
        formData.append('files', file);
      });

      const response = await fetch('/api/email-attachment', {
        method: 'POST',
        body: formData,
      });
      const result = await response.json();
      if (Array.isArray(result?.files) && result.files.length > 0) {
        const nuevos: EmailAttachment[] = result.files;
        setDraft((prev) => ({
          ...prev,
          anexos: [...prev.anexos, ...nuevos],
        }));
        void publishUserUpdate({ adicionar_anexos: nuevos });
      }
    } catch (err) {
      console.warn('Falha ao anexar arquivo manualmente:', err);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleRemoveAttachment = (id: string) => {
    setDraft((prev) => ({
      ...prev,
      anexos: prev.anexos.filter((item) => item.id !== id),
    }));
    void publishUserUpdate({ remover_anexo_id: id });
  };

  const handleClose = () => {
    setDraft((prev) => ({ ...prev, aberto: false }));
    void publishUserUpdate({ aberto: false });
  };

  const handleSendNow = () => {
    if (!hasPara || !hasAssunto || !hasMensagem || draft.status === 'enviando') return;
    setDraft((prev) => ({ ...prev, status: 'enviando', erro: '' }));
    void publishUserUpdate({
      para: draft.para,
      cc: draft.cc,
      assunto: draft.assunto,
      mensagem: draft.mensagem,
      action: 'send_now',
    });
  };

  return (
    <>
      {/* Painel do Redator de E-mail */}
      {draft.aberto && (
        <aside
          aria-label="Compositor de E-mail do Jarvis"
          style={dragStyle}
          {...dragHandlers}
          title="Arraste pelo centro para mover · Arraste pela borda para redimensionar"
          className={cn(
            'pointer-events-auto absolute top-16 left-3 z-30 flex w-[min(26rem,calc(100vw-1.5rem))] touch-none flex-col overflow-hidden select-none',
            'rounded-2xl border border-cyan-400/30 bg-black/85 text-white backdrop-blur-2xl',
            'shadow-[0_0_20px_rgba(6,182,212,0.2)] shadow-[0_8px_32px_rgba(0,0,0,0.6)] ring-1 shadow-cyan-950/40 ring-cyan-500/20 transition-all duration-200',
            isDragging || isResizingEdge
              ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
              : '',
            isDragging ? 'cursor-grabbing' : 'cursor-grab'
          )}
        >
          {/* Visual de linhas holográficas nos cantos superiores */}
          <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
          <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-12 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />
          {/* Cabeçalho */}
          <div className="flex items-center justify-between border-b border-white/10 bg-gradient-to-r from-cyan-950/60 via-slate-900/60 to-black/60 px-3.5 py-2.5">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'size-2.5 rounded-full',
                  draft.status === 'enviando' && 'animate-ping bg-amber-400',
                  draft.status === 'enviado' && 'bg-emerald-400',
                  draft.status === 'erro' && 'bg-red-400',
                  draft.status === 'rascunho' && 'animate-pulse bg-cyan-400'
                )}
              />
              <div>
                <h2 className="text-xs font-semibold tracking-wide text-cyan-100">
                  Novo E-mail · Gmail
                </h2>
                <p className="text-[10px] text-white/55">
                  {draft.status === 'enviando'
                    ? 'Enviando mensagem e anexos...'
                    : draft.status === 'enviado'
                      ? 'E-mail enviado com sucesso!'
                      : draft.status === 'erro'
                        ? 'Erro ao enviar e-mail'
                        : nextStep === 'para'
                          ? 'Aguardando destinatário...'
                          : nextStep === 'assunto'
                            ? 'Aguardando assunto...'
                            : nextStep === 'mensagem'
                              ? 'Escrevendo mensagem...'
                              : 'Pronto para revisar, anexar ou enviar'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              {!showCc && (
                <button
                  type="button"
                  data-no-drag="true"
                  onClick={() => setShowCc(true)}
                  className="rounded border border-white/15 bg-white/5 px-1.5 py-0.5 text-[10px] text-white/70 transition hover:border-cyan-400/40 hover:text-cyan-200"
                >
                  + CC
                </button>
              )}
              <button
                type="button"
                data-no-drag="true"
                onClick={handleClose}
                title="Fechar rascunho"
                className="rounded border border-white/15 bg-white/5 px-2 py-0.5 text-[10px] text-white/70 transition hover:border-red-400/40 hover:bg-red-500/10 hover:text-red-200"
              >
                ✕
              </button>
            </div>
          </div>

          {/* Indicador de etapas em tempo real */}
          <div className="grid grid-cols-4 gap-1 border-b border-white/10 bg-white/[0.02] px-3 py-1.5 text-[9px]">
            <div
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 font-medium',
                hasPara
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : nextStep === 'para'
                    ? 'border border-cyan-400/40 bg-cyan-500/15 text-cyan-200'
                    : 'text-white/40'
              )}
            >
              <span>{hasPara ? '✓' : '1.'}</span>
              <span className="truncate">Para</span>
            </div>
            <div
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 font-medium',
                hasAssunto
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : nextStep === 'assunto'
                    ? 'border border-cyan-400/40 bg-cyan-500/15 text-cyan-200'
                    : 'text-white/40'
              )}
            >
              <span>{hasAssunto ? '✓' : '2.'}</span>
              <span className="truncate">Assunto</span>
            </div>
            <div
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 font-medium',
                hasMensagem
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : nextStep === 'mensagem'
                    ? 'border border-cyan-400/40 bg-cyan-500/15 text-cyan-200'
                    : 'text-white/40'
              )}
            >
              <span>{hasMensagem ? '✓' : '3.'}</span>
              <span className="truncate">Texto</span>
            </div>
            <div
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 font-medium',
                hasAnexos ? 'bg-emerald-500/15 text-emerald-300' : 'bg-white/5 text-white/55'
              )}
            >
              <span>{hasAnexos ? `📎 ${draft.anexos.length}` : '4.'}</span>
              <span className="truncate">Anexos</span>
            </div>
          </div>

          {/* Corpo do formulário */}
          <div className="flex flex-col gap-2.5 p-3">
            {/* Campo Para */}
            <div className="flex flex-col gap-1">
              <label className="flex items-center justify-between text-[10px] font-medium text-cyan-200/80">
                <span>Para (Destinatário)</span>
                {nextStep === 'para' && (
                  <span className="animate-pulse text-[9px] text-cyan-300">
                    ● Diga para quem enviar
                  </span>
                )}
              </label>
              <input
                type="text"
                data-no-drag="true"
                value={draft.para}
                onChange={(e) => handleFieldChange('para', e.target.value)}
                placeholder="nome@exemplo.com"
                className={cn(
                  'w-full rounded-md border bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/30 transition outline-none',
                  nextStep === 'para'
                    ? 'border-cyan-400/60 ring-1 ring-cyan-400/30'
                    : 'border-white/15 focus:border-cyan-400/60'
                )}
              />
            </div>

            {/* Campo CC opcional */}
            {showCc && (
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-cyan-200/80">CC (Em cópia)</label>
                <input
                  type="text"
                  data-no-drag="true"
                  value={draft.cc}
                  onChange={(e) => handleFieldChange('cc', e.target.value)}
                  placeholder="copia@exemplo.com"
                  className="w-full rounded-md border border-white/15 bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/30 transition outline-none focus:border-cyan-400/60"
                />
              </div>
            )}

            {/* Campo Assunto */}
            <div className="flex flex-col gap-1">
              <label className="flex items-center justify-between text-[10px] font-medium text-cyan-200/80">
                <span>Assunto</span>
                {nextStep === 'assunto' && (
                  <span className="animate-pulse text-[9px] text-cyan-300">
                    ● Diga o assunto do e-mail
                  </span>
                )}
              </label>
              <input
                type="text"
                data-no-drag="true"
                value={draft.assunto}
                onChange={(e) => handleFieldChange('assunto', e.target.value)}
                placeholder="Assunto do e-mail..."
                className={cn(
                  'w-full rounded-md border bg-black/60 px-2.5 py-1.5 text-xs text-white placeholder-white/30 transition outline-none',
                  nextStep === 'assunto'
                    ? 'border-cyan-400/60 ring-1 ring-cyan-400/30'
                    : 'border-white/15 focus:border-cyan-400/60'
                )}
              />
            </div>

            {/* Campo Mensagem */}
            <div className="flex flex-col gap-1">
              <label className="flex items-center justify-between text-[10px] font-medium text-cyan-200/80">
                <span>Mensagem</span>
                {nextStep === 'mensagem' && (
                  <span className="animate-pulse text-[9px] text-cyan-300">
                    ● Diga o que deseja escrever
                  </span>
                )}
              </label>
              <textarea
                rows={5}
                data-no-drag="true"
                data-scrollable="true"
                value={draft.mensagem}
                onChange={(e) => handleFieldChange('mensagem', e.target.value)}
                placeholder="O texto que o Jarvis está redigindo aparecerá aqui em tempo real..."
                className={cn(
                  'w-full resize-y rounded-md border bg-black/60 px-2.5 py-2 text-xs leading-relaxed text-white placeholder-white/30 transition outline-none',
                  nextStep === 'mensagem'
                    ? 'border-cyan-400/60 ring-1 ring-cyan-400/30'
                    : 'border-white/15 focus:border-cyan-400/60'
                )}
              />
            </div>

            {/* Lista de Anexos */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between text-[10px] font-medium text-cyan-200/80">
                <span>Anexos ({draft.anexos.length})</span>
                <div className="flex items-center gap-1.5">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={handleManualFileSelect}
                    className="hidden"
                  />
                  <button
                    type="button"
                    data-no-drag="true"
                    disabled={isUploading}
                    onClick={() => fileInputRef.current?.click()}
                    title="Escolher arquivo do computador para anexar"
                    className="flex items-center gap-1 rounded border border-cyan-400/40 bg-cyan-500/15 px-2 py-0.5 text-[10px] font-medium text-cyan-200 transition hover:bg-cyan-500/25 disabled:opacity-50"
                  >
                    <span>📎</span>
                    <span>{isUploading ? 'Anexando...' : '+ Anexar'}</span>
                  </button>
                </div>
              </div>

              {draft.anexos.length === 0 ? (
                <div className="rounded-md border border-dashed border-white/10 bg-white/[0.02] px-2.5 py-2 text-center text-[10px] text-white/40">
                  Nenhum arquivo anexado · peça por voz ou clique em &quot;+ Anexar&quot;
                </div>
              ) : (
                <ul
                  data-scrollable="true"
                  className="flex max-h-28 flex-col gap-1 overflow-y-auto pr-0.5"
                >
                  {draft.anexos.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center justify-between gap-2 rounded-md border border-cyan-400/25 bg-cyan-950/30 px-2.5 py-1.5 text-xs"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="shrink-0 rounded bg-cyan-500/20 px-1.5 py-0.5 text-[9px] font-semibold tracking-wider text-cyan-200 uppercase">
                          {item.tipo === 'drive' ? 'Drive' : 'PC'}
                        </span>
                        <span className="truncate text-[11px] text-white/90" title={item.nome}>
                          {item.nome}
                        </span>
                      </div>
                      <button
                        type="button"
                        data-no-drag="true"
                        onClick={() => handleRemoveAttachment(item.id)}
                        title="Remover anexo"
                        className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-white/50 transition hover:bg-red-500/20 hover:text-red-300"
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Mensagem de erro ou sucesso */}
            {draft.erro && (
              <div className="rounded-md border border-red-400/40 bg-red-950/50 px-2.5 py-1.5 text-[10px] text-red-200">
                {draft.erro}
              </div>
            )}
            {draft.status === 'enviado' && (
              <div className="rounded-md border border-emerald-400/40 bg-emerald-950/50 px-2.5 py-1.5 text-[10px] text-emerald-200">
                ✓ E-mail enviado com sucesso!
              </div>
            )}
          </div>

          {/* Rodapé com botão de envio direto */}
          <div className="flex items-center justify-between border-t border-white/10 bg-white/[0.02] px-3.5 py-2">
            <span className="text-[10px] text-white/50">
              Confirme por voz (&quot;pode enviar&quot;) ou clique ao lado
            </span>
            <button
              type="button"
              data-no-drag="true"
              disabled={!hasPara || !hasAssunto || !hasMensagem || draft.status === 'enviando'}
              onClick={handleSendNow}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-semibold transition',
                hasPara && hasAssunto && hasMensagem && draft.status !== 'enviando'
                  ? 'bg-cyan-500 text-black shadow-lg shadow-cyan-500/25 hover:bg-cyan-400'
                  : 'cursor-not-allowed bg-white/10 text-white/35'
              )}
            >
              {draft.status === 'enviando' ? 'Enviando...' : 'Enviar agora'}
            </button>
          </div>
        </aside>
      )}
    </>
  );
}
