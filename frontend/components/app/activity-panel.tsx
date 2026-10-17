'use client';

import { useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { useRoomContext, useVoiceAssistant } from '@livekit/components-react';
import { cn } from '@/lib/shadcn/utils';

const ACTIVITY_TOPIC = 'jarvis.activity';
const MAX_ACTIVITIES = 20;

type ActivityStatus = 'running' | 'done' | 'error' | 'cancelled';

interface ActivityItem {
  id: string;
  number: number;
  tool: string;
  status: ActivityStatus;
}

interface ActivityPacket {
  type?: string;
  event?: string;
  id?: string;
  tool?: string;
  status?: string;
}

const TOOL_LABELS: Record<string, string> = {
  salvar_na_memoria: 'Salvando memória no Drive',
  buscar_na_memoria: 'Consultando memórias',
  abrir_site: 'Abrindo site',
  abrir_programa: 'Abrindo aplicativo',
  fechar_programa: 'Fechando aplicativo',
  executar_comando_terminal: 'Executando comando',
  abrir_pasta: 'Abrindo pasta',
  criar_pasta: 'Criando pasta',
  organizar_mover_item: 'Movendo arquivo ou pasta',
  apagar_pasta: 'Apagando pasta',
  fechar_pasta: 'Fechando pasta',
  buscar_no_google_drive: 'Buscando no Google Drive',
  listar_pasta_google_drive: 'Listando pasta do Drive',
  ler_arquivo_google_drive: 'Lendo arquivo do Drive',
  criar_pasta_google_drive: 'Criando pasta no Drive',
  criar_arquivo_texto_google_drive: 'Criando arquivo de texto no Drive',
  atualizar_arquivo_texto_google_drive: 'Atualizando arquivo de texto no Drive',
  criar_documento_google_docs: 'Criando documento Google Docs',
  renomear_item_google_drive: 'Renomeando item no Drive',
  mover_item_google_drive: 'Movendo item no Drive',
  substituir_trecho_google_docs: 'Editando Google Docs',
  enviar_item_google_drive_para_lixeira: 'Enviando item do Drive à lixeira',
  listar_eventos_google_calendar: 'Consultando Google Calendar',
  criar_evento_google_calendar: 'Criando evento no Calendar',
  editar_evento_google_calendar: 'Editando evento no Calendar',
  excluir_evento_google_calendar: 'Excluindo evento do Calendar',
};

function getToolLabel(tool: string) {
  return TOOL_LABELS[tool] ?? tool.split('_').join(' ');
}

export function ActivityPanel() {
  const room = useRoomContext();
  const { state: agentState } = useVoiceAssistant();
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [totalActions, setTotalActions] = useState(0);
  const nextNumber = useRef(1);
  const seenActivityIds = useRef(new Set<string>());

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== ACTIVITY_TOPIC) return;

      let packet: ActivityPacket;
      try {
        packet = JSON.parse(new TextDecoder().decode(payload)) as ActivityPacket;
      } catch {
        return;
      }

      if (packet.type !== 'jarvis_activity' || !packet.id) return;

      if (packet.event === 'started' && packet.tool) {
        if (seenActivityIds.current.has(packet.id)) return;
        seenActivityIds.current.add(packet.id);

        const activity: ActivityItem = {
          id: packet.id,
          number: nextNumber.current++,
          tool: packet.tool,
          status: 'running',
        };
        setActivities((current) => [...current, activity].slice(-MAX_ACTIVITIES));
        setTotalActions((current) => current + 1);
      } else if (packet.event === 'ended') {
        const status: ActivityStatus =
          packet.status === 'done' ? 'done' : packet.status === 'cancelled' ? 'cancelled' : 'error';
        setActivities((current) =>
          current.map((item) => (item.id === packet.id ? { ...item, status } : item))
        );
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room]);

  return (
    <aside
      aria-label="Atividades do Jarvis"
      className="pointer-events-auto absolute top-3 right-3 z-30 w-[min(17rem,calc(100vw-1.5rem))] overflow-hidden rounded-lg border border-cyan-400/20 bg-black/75 text-white shadow-2xl shadow-black/40 backdrop-blur-xl"
    >
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold tracking-wide">Atividades do Jarvis</h2>
          <p className="mt-0.5 text-[9px] text-white/50">Execuções desta sessão</p>
        </div>
        <span className="rounded-full border border-cyan-300/20 px-1.5 py-0.5 text-[8px] tracking-wider text-cyan-200/80 uppercase">
          {totalActions} {totalActions === 1 ? 'ação' : 'ações'}
        </span>
      </div>

      <ol
        aria-live="polite"
        className="flex max-h-[min(40vh,20rem)] flex-col gap-1.5 overflow-y-auto p-2"
      >
        {agentState === 'thinking' && (
          <li className="px-1.5 py-3 text-center text-[10px] text-white/45">
            Analisando solicitação...
          </li>
        )}
        {activities.length === 0 && agentState !== 'thinking' && (
          <li className="px-1.5 py-3 text-center text-[10px] text-white/45">
            Aguardando ações do Jarvis...
          </li>
        )}
        {[...activities].reverse().map((activity) => {
          const isRunning = activity.status === 'running';
          const isDone = activity.status === 'done';
          const statusLabel = isRunning
            ? 'Executando'
            : isDone
              ? 'Concluída'
              : activity.status === 'cancelled'
                ? 'Cancelada'
                : 'Falhou';

          return (
            <li
              key={activity.id}
              className={cn(
                'flex items-start gap-2 rounded-md border px-2 py-1.5 transition-colors',
                isRunning && 'border-red-400/40 bg-red-500/10',
                isDone && 'border-emerald-400/30 bg-emerald-500/10',
                activity.status === 'error' && 'border-red-500/40 bg-red-950/40',
                activity.status === 'cancelled' && 'border-amber-300/25 bg-amber-400/5'
              )}
            >
              <span
                className={cn(
                  'mt-0.5 flex size-6 shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold',
                  isRunning && 'bg-red-500/20 text-red-300',
                  isDone && 'bg-emerald-500/20 text-emerald-300',
                  activity.status === 'error' && 'bg-red-500/20 text-red-300',
                  activity.status === 'cancelled' && 'bg-amber-500/15 text-amber-200'
                )}
              >
                {String(activity.number).padStart(2, '0')}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[10px] leading-4 text-white/90">{getToolLabel(activity.tool)}</p>
                <p
                  className={cn(
                    'mt-0.5 text-[8px] font-medium tracking-wide uppercase',
                    isRunning && 'text-red-300',
                    isDone && 'text-emerald-300',
                    activity.status === 'error' && 'text-red-300',
                    activity.status === 'cancelled' && 'text-amber-200'
                  )}
                >
                  {statusLabel}
                </p>
              </div>
              {isRunning && (
                <span className="mt-2 size-2 shrink-0 animate-pulse rounded-full bg-red-400" />
              )}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
