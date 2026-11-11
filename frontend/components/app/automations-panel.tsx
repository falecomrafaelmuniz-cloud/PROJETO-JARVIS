'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import {
  CheckCircle2,
  Clock,
  FolderOpen,
  KeyRound,
  Mail,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Workflow,
  Zap,
} from 'lucide-react';
import { useRoomContext } from '@livekit/components-react';
import { PanelHeaderControls, usePanelMinimized } from '@/components/app/panel-header-controls';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { cn } from '@/lib/shadcn/utils';

const AUTOMATIONS_TOPIC = 'jarvis.automations';

export interface AutomationItem {
  id: string;
  nome: string;
  remetente_ou_busca: string;
  pasta_drive: string;
  tipo_arquivo: string;
  organizar_por_ano_mes: boolean;
  marcar_como_lido: boolean;
  frequencia: 'diario' | 'hora_em_hora';
  horario: string;
  ativo: boolean;
  senha_anexo?: string;
  ultima_execucao_data: string;
  ultima_execucao_ts: number;
  ultimo_resultado: string;
}

interface AutomationsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenRequest?: () => void;
}

export function AutomationsPanel({ isOpen, onClose, onOpenRequest }: AutomationsPanelProps) {
  const room = useRoomContext();
  const [automations, setAutomations] = useState<AutomationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState('');

  // Form states
  const [nome, setNome] = useState('');
  const [remetenteOuBusca, setRemetenteOuBusca] = useState('');
  const [pastaDrive, setPastaDrive] = useState('');
  const [tipoArquivo, setTipoArquivo] = useState('pdf');
  const [organizarPorAnoMes, setOrganizarPorAnoMes] = useState(true);
  const [marcarComoLido, setMarcarComoLido] = useState(true);
  const [frequencia, setFrequencia] = useState<'diario' | 'hora_em_hora'>('diario');
  const [horario, setHorario] = useState('08:00');
  const [senhaAnexo, setSenhaAnexo] = useState('');

  const [minimized, toggleMinimized] = usePanelMinimized('jarvis-panel-min-automations-v1', false);
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-automations-panel-v2');

  const fetchAutomations = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/automations', { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.automations)) {
          setAutomations(json.automations);
        }
      }
    } catch (err) {
      console.warn('Erro ao buscar automações:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchAutomations();
    }
  }, [isOpen, fetchAutomations]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== AUTOMATIONS_TOPIC) return;
      try {
        const packet = JSON.parse(new TextDecoder().decode(payload));
        if (packet?.type === 'jarvis_automations_sync' && Array.isArray(packet.automations)) {
          setAutomations(packet.automations);
          setRunningId(null);
          if (packet.open_panel && onOpenRequest) {
            onOpenRequest();
          }
          if (packet.close_panel) {
            onClose();
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
  }, [room, onOpenRequest, onClose]);

  const resetForm = () => {
    setEditingId(null);
    setNome('');
    setRemetenteOuBusca('');
    setPastaDrive('');
    setTipoArquivo('pdf');
    setOrganizarPorAnoMes(true);
    setMarcarComoLido(true);
    setFrequencia('diario');
    setHorario('08:00');
    setSenhaAnexo('');
    setFormError('');
  };

  const handleOpenCreate = () => {
    resetForm();
    setShowForm(true);
  };

  const handleOpenEdit = (item: AutomationItem) => {
    setEditingId(item.id);
    setNome(item.nome);
    setRemetenteOuBusca(item.remetente_ou_busca);
    setPastaDrive(item.pasta_drive);
    setTipoArquivo(item.tipo_arquivo || 'pdf');
    setOrganizarPorAnoMes(item.organizar_por_ano_mes ?? true);
    setMarcarComoLido(item.marcar_como_lido ?? true);
    setFrequencia(item.frequencia || 'diario');
    setHorario(item.horario || '08:00');
    setSenhaAnexo(item.senha_anexo || '');
    setFormError('');
    setShowForm(true);
  };

  const handleSaveForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!nome.trim() || !remetenteOuBusca.trim() || !pastaDrive.trim()) {
      setFormError('Preencha o Nome, o Remetente/Busca no Gmail e o Nome da Pasta no Drive.');
      return;
    }

    try {
      const res = await fetch('/api/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'create_or_update',
          id: editingId || '',
          nome: nome.trim(),
          remetente_ou_busca: remetenteOuBusca.trim(),
          pasta_drive: pastaDrive.trim(),
          tipo_arquivo: tipoArquivo,
          organizar_por_ano_mes: organizarPorAnoMes,
          marcar_como_lido: marcarComoLido,
          frequencia,
          horario,
          senha_anexo: senhaAnexo.trim(),
          ativo: true,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        setFormError(json.error || 'Erro ao salvar automação.');
        return;
      }
      if (Array.isArray(json.automations)) {
        setAutomations(json.automations);
      }
      setShowForm(false);
      resetForm();
    } catch (err) {
      setFormError(String(err));
    }
  };

  const handleToggleActive = async (item: AutomationItem) => {
    try {
      const res = await fetch('/api/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'toggle',
          id: item.id,
          ativo: !item.ativo,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.automations)) {
        setAutomations(json.automations);
      }
    } catch (err) {
      console.warn('Erro ao alternar status da automação:', err);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch('/api/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'delete',
          id,
        }),
      });
      const json = await res.json();
      if (json.ok && Array.isArray(json.automations)) {
        setAutomations(json.automations);
      }
    } catch (err) {
      console.warn('Erro ao excluir automação:', err);
    }
  };

  const handleRunNow = async (item: AutomationItem) => {
    setRunningId(item.id);
    try {
      const data = new TextEncoder().encode(
        JSON.stringify({
          type: 'jarvis_automation_run_now',
          id: item.id,
        })
      );
      await room.localParticipant.publishData(data, {
        reliable: true,
        topic: AUTOMATIONS_TOPIC,
      });
      setTimeout(() => {
        fetchAutomations();
        setRunningId(null);
      }, 4500);
    } catch (err) {
      console.warn('Erro ao disparar automação:', err);
      setRunningId(null);
    }
  };

  if (!isOpen) return null;

  return (
    <aside
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pela mãozinha para mover · Arraste pelas bordas para aumentar ou diminuir"
      className={cn(
        'pointer-events-auto fixed bottom-24 right-3 z-40 flex max-h-[75vh] w-[min(28rem,calc(100vw-1.5rem))] touch-none flex-col overflow-hidden rounded-2xl border border-cyan-500/35 bg-black/90 text-slate-100 shadow-[0_0_35px_rgba(6,182,212,0.22)] backdrop-blur-2xl select-none',
        isResizingEdge ? 'ring-2 ring-cyan-300/70' : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Cabeçalho */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 bg-cyan-950/30 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-lg border border-cyan-400/40 bg-cyan-500/15 text-cyan-300 shadow-[0_0_10px_rgba(34,211,238,0.3)]">
            <Workflow className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-xs font-bold tracking-wider text-cyan-200 uppercase">
                Automações
              </h2>
              <span className="rounded-full border border-cyan-400/30 bg-cyan-500/15 px-2 py-0.5 font-mono text-[10px] font-bold text-cyan-300">
                {automations.length}
              </span>
            </div>
            <p className="text-[10px] text-slate-400">
              Rotinas automáticas Gmail ➔ Google Drive
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {!showForm && !minimized && (
            <button
              type="button"
              data-no-drag="true"
              onClick={handleOpenCreate}
              className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/50 bg-cyan-500/20 px-2 py-1 font-mono text-[10px] font-bold text-cyan-200 shadow-[0_0_12px_rgba(6,182,212,0.3)] transition hover:bg-cyan-500/35 hover:text-white"
            >
              <Plus className="size-3" />
              <span>Criar</span>
            </button>
          )}
          <button
            type="button"
            data-no-drag="true"
            onClick={fetchAutomations}
            title="Atualizar lista"
            className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-200/80 transition hover:border-cyan-300/45 hover:bg-cyan-400/20 hover:text-cyan-100"
          >
            <RefreshCw className={cn('size-3', loading ? 'animate-spin text-cyan-300' : '')} />
          </button>
          <PanelHeaderControls
            minimized={minimized}
            onToggleMinimize={toggleMinimized}
            onClose={onClose}
          />
        </div>
      </div>

      {/* Corpo do Painel */}
      {!minimized && (
      <div
        data-scrollable="true"
        className="flex-1 space-y-3 overflow-y-auto p-4"
      >
        {showForm ? (
          <form onSubmit={handleSaveForm} className="space-y-3 rounded-xl border border-cyan-500/25 bg-slate-950/80 p-3.5">
            <div className="flex items-center justify-between border-b border-white/10 pb-2">
              <span className="font-mono text-xs font-bold text-cyan-300 uppercase">
                {editingId ? 'Editar Automação' : '+ Nova Automação'}
              </span>
              <button
                type="button"
                onClick={() => {
                  setShowForm(false);
                  resetForm();
                }}
                className="text-xs text-slate-400 hover:text-white"
              >
                Cancelar
              </button>
            </div>

            {formError && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-950/50 px-3 py-2 text-xs text-rose-200">
                {formError}
              </div>
            )}

            <div className="space-y-1">
              <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                Nome da Automação
              </label>
              <input
                type="text"
                value={nome}
                onChange={(e) => setNome(e.target.value)}
                placeholder="Ex: Boletos Administradora / Notas Fiscais"
                className="w-full rounded-lg border border-white/15 bg-black/70 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
              />
            </div>

            <div className="space-y-1">
              <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                Remetente ou Filtro no Gmail
              </label>
              <input
                type="text"
                value={remetenteOuBusca}
                onChange={(e) => setRemetenteOuBusca(e.target.value)}
                placeholder="Ex: boletos@graiche.com.br; enel; vivo"
                className="w-full rounded-lg border border-white/15 bg-black/70 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
              />
              <p className="text-[10px] text-slate-400">
                Pode colocar quantos remetentes quiser separados por <strong>;</strong> (ponto e vírgula) ou <strong>,</strong> (vírgula).
              </p>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <div className="space-y-1">
                <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                  Tipo de Anexo
                </label>
                <select
                  value={tipoArquivo}
                  onChange={(e) => setTipoArquivo(e.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/80 px-2.5 py-2 text-xs text-white focus:border-cyan-400 focus:outline-none"
                >
                  <option value="pdf">Apenas PDF (.pdf)</option>
                  <option value="todos">Qualquer Anexo</option>
                  <option value="xml">Nota Fiscal (.xml)</option>
                  <option value="planilha">Planilha (.xlsx/.csv)</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                  Pasta no Google Drive
                </label>
                <input
                  type="text"
                  value={pastaDrive}
                  onChange={(e) => setPastaDrive(e.target.value)}
                  placeholder="Ex: Boletos Mensais / Notas Fiscais"
                  className="w-full rounded-lg border border-white/15 bg-black/70 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <div className="space-y-1">
                <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                  Frequência
                </label>
                <select
                  value={frequencia}
                  onChange={(e) => setFrequencia(e.target.value as 'diario' | 'hora_em_hora')}
                  className="w-full rounded-lg border border-white/15 bg-black/80 px-2.5 py-2 text-xs text-white focus:border-cyan-400 focus:outline-none"
                >
                  <option value="diario">Diariamente</option>
                  <option value="hora_em_hora">De hora em hora</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="block font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                  Horário
                </label>
                <input
                  type="time"
                  value={horario}
                  onChange={(e) => setHorario(e.target.value)}
                  disabled={frequencia === 'hora_em_hora'}
                  className="w-full rounded-lg border border-white/15 bg-black/70 px-3 py-2 text-xs text-white disabled:opacity-40 focus:border-cyan-400 focus:outline-none"
                />
              </div>
            </div>

            <div className="space-y-1">
              <label className="flex items-center gap-1.5 font-mono text-[10px] tracking-wider text-slate-300 uppercase">
                <KeyRound className="size-3 text-cyan-400" />
                <span>Senha do PDF / Anexo (Opcional)</span>
              </label>
              <input
                type="text"
                value={senhaAnexo}
                onChange={(e) => setSenhaAnexo(e.target.value)}
                placeholder="Ex: 2138 (4 primeiros dígitos do CPF)"
                className="w-full rounded-lg border border-white/15 bg-black/70 px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
              />
              <p className="text-[10px] text-slate-400">
                Se o PDF tiver senha, o Jarvis destrava automaticamente e já salva aberto (sem senha) no Drive.
              </p>
            </div>

            <div className="space-y-2 pt-1">
              <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  checked={organizarPorAnoMes}
                  onChange={(e) => setOrganizarPorAnoMes(e.target.checked)}
                  className="rounded border-white/20 bg-black text-cyan-400 focus:ring-cyan-400"
                />
                <span>Criar subpasta automática por Ano e Mês (Ex: 2026 / 11 NOVEMBRO)</span>
              </label>

              <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  checked={marcarComoLido}
                  onChange={(e) => setMarcarComoLido(e.target.checked)}
                  className="rounded border-white/20 bg-black text-cyan-400 focus:ring-cyan-400"
                />
                <span>Marcar e-mail como lido após salvar o arquivo no Drive</span>
              </label>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  setShowForm(false);
                  resetForm();
                }}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-white/10"
              >
                Cancelar
              </button>
              <button
                type="submit"
                className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-400/50 bg-cyan-500/25 px-4 py-1.5 font-mono text-xs font-bold text-cyan-100 shadow-[0_0_15px_rgba(6,182,212,0.35)] transition hover:bg-cyan-500/40"
              >
                <CheckCircle2 className="size-3.5" />
                <span>Salvar Automação</span>
              </button>
            </div>
          </form>
        ) : automations.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/15 bg-white/[0.02] px-4 py-8 text-center">
            <Zap className="mb-2 size-8 text-cyan-400/50" />
            <p className="text-xs font-semibold text-slate-200">
              Nenhuma automação criada ainda
            </p>
            <p className="mt-1 max-w-xs text-[11px] text-slate-400">
              Clique no botão <span className="font-bold text-cyan-300">+ Criar</span> acima para configurar sua primeira rotina automática entre Gmail e Google Drive.
            </p>
            <button
              type="button"
              onClick={handleOpenCreate}
              className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-cyan-400/50 bg-cyan-500/20 px-4 py-2 font-mono text-xs font-bold text-cyan-200 shadow-[0_0_15px_rgba(6,182,212,0.3)] transition hover:bg-cyan-500/35"
            >
              <Plus className="size-4" />
              <span>+ Criar Automação</span>
            </button>
          </div>
        ) : (
          automations.map((item) => {
            const isRunning = runningId === item.id;
            return (
              <div
                key={item.id}
                className={cn(
                  'rounded-xl border p-3 transition-all',
                  item.ativo
                    ? 'border-cyan-500/30 bg-slate-900/75 shadow-[0_0_15px_rgba(6,182,212,0.1)]'
                    : 'border-white/10 bg-black/50 opacity-70'
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'size-2.5 shrink-0 rounded-full',
                        item.ativo
                          ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]'
                          : 'bg-slate-600'
                      )}
                    />
                    <h3 className="text-xs font-bold text-white">{item.nome}</h3>
                  </div>

                  <span
                    className={cn(
                      'rounded-md px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase',
                      item.ativo
                        ? 'border border-emerald-400/40 bg-emerald-500/15 text-emerald-300'
                        : 'border border-white/10 bg-white/5 text-slate-400'
                    )}
                  >
                    {item.ativo ? 'Ativo' : 'Pausado'}
                  </span>
                </div>

                {/* Detalhes da regra */}
                <div className="mt-2 space-y-1 text-[11px] text-slate-300">
                  <div className="flex items-center gap-1.5">
                    <Mail className="size-3 shrink-0 text-cyan-400" />
                    <span className="truncate">
                      Gmail: <strong className="text-cyan-200">{item.remetente_ou_busca}</strong> ({(item.tipo_arquivo || 'pdf').toUpperCase()})
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <FolderOpen className="size-3 shrink-0 text-amber-400" />
                    <span className="truncate">
                      Drive: <strong className="text-amber-200">{item.pasta_drive}</strong>
                      {item.organizar_por_ano_mes ? ' / ANO / MÊS' : ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
                    <Clock className="size-3 shrink-0 text-slate-400" />
                    <span>
                      {item.frequencia === 'hora_em_hora'
                        ? 'Executa de hora em hora'
                        : `Diariamente às ${item.horario}`}
                    </span>
                  </div>
                  {item.senha_anexo && (
                    <div className="flex items-center gap-1.5 text-[10px] text-cyan-300/90">
                      <KeyRound className="size-3 shrink-0 text-cyan-400" />
                      <span>
                        Desbloqueio automático de PDF ativo (Senha: <strong>{item.senha_anexo}</strong>)
                      </span>
                    </div>
                  )}
                </div>

                {/* Último resultado */}
                {item.ultimo_resultado && (
                  <div className="mt-2 rounded-lg border border-white/5 bg-black/50 px-2.5 py-1.5 text-[10px] text-slate-400">
                    {item.ultimo_resultado}
                  </div>
                )}

                {/* Ações */}
                <div className="mt-2.5 flex items-center justify-between border-t border-white/10 pt-2">
                  <button
                    type="button"
                    disabled={isRunning}
                    onClick={() => handleRunNow(item)}
                    className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/40 bg-cyan-500/15 px-2.5 py-1 font-mono text-[10px] font-bold text-cyan-200 transition hover:bg-cyan-500/30 disabled:opacity-50"
                  >
                    <Play className={cn('size-3', isRunning ? 'animate-spin' : '')} />
                    <span>{isRunning ? 'Executando...' : 'Executar agora'}</span>
                  </button>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleToggleActive(item)}
                      title={item.ativo ? 'Pausar automação' : 'Ativar automação'}
                      className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-slate-300 transition hover:border-cyan-400/40 hover:text-cyan-200"
                    >
                      {item.ativo ? <Pause className="size-3" /> : <Play className="size-3" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenEdit(item)}
                      title="Editar automação"
                      className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-slate-300 transition hover:border-cyan-400/40 hover:text-cyan-200"
                    >
                      <Pencil className="size-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(item.id)}
                      title="Excluir automação"
                      className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-slate-400 transition hover:border-rose-400/40 hover:bg-rose-500/15 hover:text-rose-300"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
      )}
    </aside>
  );
}
