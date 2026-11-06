'use client';

import { useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { useRoomContext, useVoiceAssistant } from '@livekit/components-react';
import { useDraggableFloating } from '@/hooks/use-draggable-floating';
import { notifyDashboard } from '@/lib/jarvis-dashboard';
import { cn } from '@/lib/shadcn/utils';

const ACTIVITY_TOPIC = 'jarvis.activity';
const MAX_ACTIVITIES = 20;

type ActivityStatus = 'running' | 'done' | 'error' | 'cancelled';

interface ActivityItem {
  id: string;
  number: number;
  tool: string;
  status: ActivityStatus;
  startedAt: number;
}

interface ActivityPacket {
  type?: string;
  event?: string;
  id?: string;
  tool?: string;
  status?: string;
}

const TOOL_LABELS: Record<string, string> = {
  obter_horario_brasilia: 'Consultando horário de Brasília',
  obter_localizacao_atual: 'Obtendo localização atual',
  pesquisar_assunto_para_aula: 'Pesquisando conteúdo e abrindo aula no navegador',
  salvar_na_memoria: 'Salvando memória no Drive',
  buscar_na_memoria: 'Consultando memórias',
  tocar_musica: 'Tocando música no YouTube',
  controlar_midia: 'Controlando reprodução de mídia',
  abrir_site: 'Abrindo site',
  abrir_programa: 'Abrindo aplicativo',
  fechar_programa: 'Fechando aplicativo',
  fechar_aba_ou_site: 'Fechando aba do navegador',
  executar_comando_terminal: 'Executando comando',
  abrir_pasta: 'Abrindo pasta',
  criar_pasta: 'Criando pasta',
  organizar_mover_item: 'Movendo arquivo ou pasta',
  apagar_pasta: 'Apagando pasta',
  fechar_pasta: 'Fechando pasta',
  minimizar_item_ou_janela: 'Minimizando janela ou aba',
  maximizar_ou_restaurar_item_ou_janela: 'Maximizando/restaurando janela ou aba',
  tirar_print_e_salvar: 'Tirando print da tela e salvando na pasta',
  buscar_no_google_drive: 'Buscando no Google Drive',
  listar_pasta_google_drive: 'Listando pasta do Drive',
  ler_arquivo_google_drive: 'Lendo arquivo do Drive',
  criar_pasta_google_drive: 'Criando pasta no Drive',
  criar_arquivo_texto_google_drive: 'Criando arquivo de texto no Drive',
  atualizar_arquivo_texto_google_drive: 'Atualizando arquivo de texto no Drive',
  criar_documento_google_docs: 'Criando documento Google Docs',
  criar_planilha_google_sheets: 'Criando planilha no Google Sheets',
  ler_planilha_google_sheets: 'Lendo planilha do Google Sheets',
  atualizar_planilha_google_sheets: 'Atualizando células no Google Sheets',
  acrescentar_linhas_google_sheets: 'Adicionando linhas no Google Sheets',
  limpar_intervalo_google_sheets: 'Limpando intervalo no Google Sheets',
  executar_operacoes_google_sheets: 'Formatando planilha no Google Sheets',
  criar_apresentacao_google_slides: 'Criando apresentação no Google Slides',
  ler_apresentacao_google_slides: 'Lendo apresentação do Google Slides',
  adicionar_slide_google_slides: 'Adicionando slide no Google Slides',
  substituir_texto_google_slides: 'Editando texto no Google Slides',
  excluir_slide_google_slides: 'Excluindo slide no Google Slides',
  executar_operacoes_google_slides: 'Atualizando apresentação no Google Slides',
  iniciar_selecao_google_photos: 'Iniciando seletor do Google Fotos',
  listar_fotos_selecionadas_google_photos: 'Baixando mídias do Google Fotos',
  usar_fotos_selecionadas_google_photos: 'Processando fotos selecionadas do Google Fotos',
  renomear_item_google_drive: 'Renomeando item no Drive',
  mover_item_google_drive: 'Movendo item no Drive',
  substituir_trecho_google_docs: 'Editando Google Docs',
  enviar_item_google_drive_para_lixeira: 'Enviando item do Drive à lixeira',
  listar_eventos_google_calendar: 'Consultando Google Calendar',
  criar_evento_google_calendar: 'Criando evento no Calendar',
  editar_evento_google_calendar: 'Editando evento no Calendar',
  excluir_evento_google_calendar: 'Excluindo evento no Calendar',
  listar_emails_gmail: 'Consultando e-mails no Gmail',
  ler_email_gmail: 'Lendo e-mail no Gmail',
  abrir_rascunho_email: 'Abrindo janela de novo e-mail na tela',
  abrir_ou_atualizar_rascunho_email: 'Escrevendo rascunho de e-mail na tela',
  abrir_explorador_para_anexar: 'Abrindo Explorador de Arquivos para anexar',
  anexar_selecao_ou_contexto_atual: 'Anexando item selecionado ou da tela atual',
  anexar_arquivo_no_email: 'Anexando arquivo ao e-mail',
  remover_anexo_do_email: 'Removendo anexo do e-mail',
  confirmar_e_enviar_email: 'Enviando e-mail pelo Gmail',
  fechar_rascunho_email: 'Fechando rascunho de e-mail',
  enviar_email_gmail: 'Enviando e-mail pelo Gmail',
  responder_email_gmail: 'Respondendo e-mail no Gmail',
  marcar_email_como_lido_gmail: 'Marcando e-mail como lido',
  enviar_email_para_lixeira_gmail: 'Movendo e-mail para a lixeira',
  analisar_e_sugerir_organizacao_emails: 'Analisando remetentes e Marcadores no Gmail',
  aprovar_ou_rejeitar_sugestoes_email: 'Organizando remetentes nos Marcadores do Gmail',
  navegar_ou_pesquisar_no_gmail: 'Navegando/pesquisando dentro do Gmail',
  listar_marcadores_gmail: 'Listando seus Marcadores do Gmail',
  abrir_ou_listar_marcador_gmail: 'Abrindo/listando Marcador no Gmail',
  organizar_remetente_no_marcador_gmail: 'Movendo remetente para Marcador no Gmail',
  apagar_marcador_ou_emails_gmail: 'Removendo/apagando no Marcador do Gmail',
  configurar_aviso_chegada_email: 'Ativando alerta de chegada de e-mail',
  listar_avisos_chegada_email: 'Consultando alertas de chegada de e-mail',
  remover_aviso_chegada_email: 'Removendo alerta de chegada de e-mail',
  analisar_tela_ou_janela_ativa: 'Analisando o que está na sua tela',
  ler_ou_resumir_documento_ou_pdf: 'Lendo e analisando documento/PDF',
  consultar_e_adicionar_em_planilha: 'Consultando/atualizando Planilha Google',
};

const MAX_RUNNING_DURATION_MS = 35_000;

function getToolLabel(tool: string) {
  return TOOL_LABELS[tool] ?? tool.split('_').join(' ');
}

export function ActivityPanel() {
  const room = useRoomContext();
  const { state: agentState } = useVoiceAssistant();
  const {
    style: dragStyle,
    isDragging,
    isResizingEdge,
    dragHandlers,
  } = useDraggableFloating('jarvis-pos-activity-panel-v1');
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [totalActions, setTotalActions] = useState(0);
  const [clockTick, setClockTick] = useState(() => Date.now());
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
          startedAt: Date.now(),
        };
        setActivities((current) => [...current, activity].slice(-MAX_ACTIVITIES));
        setTotalActions((current) => current + 1);
      } else if (packet.event === 'ended') {
        const status: ActivityStatus =
          packet.status === 'done' ? 'done' : packet.status === 'cancelled' ? 'cancelled' : 'error';
        if (status === 'error') {
          notifyDashboard(`Falha em ${getToolLabel(packet.tool ?? 'uma tarefa')}`, 'error');
        }
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

  useEffect(() => {
    if (!activities.some((activity) => activity.status === 'running')) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setClockTick(now);
      setActivities((current) =>
        current.map((item) =>
          item.status === 'running' && now - item.startedAt >= MAX_RUNNING_DURATION_MS
            ? { ...item, status: 'cancelled' }
            : item
        )
      );
    }, 1000);
    return () => window.clearInterval(timer);
  }, [activities]);

  return (
    <aside
      aria-label="Atividades do Jarvis"
      style={dragStyle}
      {...dragHandlers}
      title="Arraste pelo centro para mover · Arraste pela borda para aumentar ou diminuir"
      className={cn(
        'pointer-events-auto absolute top-3 right-3 z-30 w-[min(17rem,calc(50vw-1.125rem))] touch-none overflow-hidden select-none',
        'rounded-2xl border border-cyan-400/30 bg-black/80 text-white backdrop-blur-2xl',
        'shadow-[0_0_20px_rgba(6,182,212,0.18)] shadow-[0_8px_32px_rgba(0,0,0,0.6)] ring-1 shadow-cyan-950/40 ring-cyan-500/20 transition-all duration-200',
        isDragging || isResizingEdge
          ? 'shadow-[0_0_25px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/80'
          : '',
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      )}
    >
      {/* Visual de linhas holográficas nos cantos superiores */}
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" />
      <div className="pointer-events-none absolute -top-1 left-1/2 h-1.5 w-12 -translate-x-1/2 rounded-full bg-cyan-400/40 blur-xs" />
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold tracking-wide">Tarefas em andamento</h2>
          <p className="mt-0.5 text-[9px] text-white/50">Em execução primeiro; recentes abaixo</p>
        </div>
        <span className="rounded-full border border-cyan-300/20 px-1.5 py-0.5 text-[8px] tracking-wider text-cyan-200/80 uppercase">
          {totalActions} {totalActions === 1 ? 'ação' : 'ações'}
        </span>
      </div>

      <ol
        aria-live="polite"
        data-scrollable="true"
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
        {[...activities]
          .sort((first, second) => {
            if (first.status === 'running' && second.status !== 'running') return -1;
            if (first.status !== 'running' && second.status === 'running') return 1;
            return second.number - first.number;
          })
          .map((activity) => {
            const isRunning = activity.status === 'running';
            const isDone = activity.status === 'done';
            const statusLabel = isRunning
              ? 'Executando'
              : isDone
                ? 'Concluída'
                : activity.status === 'cancelled'
                  ? 'Cancelada'
                  : 'Falhou';
            const elapsedSeconds = isRunning
              ? Math.floor((clockTick - activity.startedAt) / 1000)
              : 0;
            const elapsedLabel =
              elapsedSeconds >= 60
                ? `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`
                : `${elapsedSeconds}s`;

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
                  <p className="text-[10px] leading-4 text-white/90">
                    {getToolLabel(activity.tool)}
                  </p>
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
                  {isRunning && (
                    <p
                      className={cn(
                        'mt-0.5 text-[8px]',
                        elapsedSeconds >= 45 ? 'text-amber-200' : 'text-white/45'
                      )}
                    >
                      {elapsedSeconds >= 45 ? 'Ainda executando · ' : 'Tempo decorrido · '}
                      {elapsedLabel}
                    </p>
                  )}
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
