import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export const dynamic = 'force-dynamic';

export interface AutomationTask {
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
  criado_em_ts?: number;
  ids_processados?: string[];
  ultima_execucao_data: string;
  ultima_execucao_ts: number;
  ultimo_resultado: string;
}

function getTasksFilePath(): string {
  return path.resolve(process.cwd(), '..', 'backend', 'tarefas_agendadas.json');
}

function readTasks(): AutomationTask[] {
  const filePath = getTasksFilePath();
  if (!fs.existsSync(filePath)) {
    fs.writeFileSync(filePath, '[]', 'utf-8');
    return [];
  }
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeTasks(tasks: AutomationTask[]): void {
  const filePath = getTasksFilePath();
  fs.writeFileSync(filePath, JSON.stringify(tasks, null, 2), 'utf-8');
}

export async function GET() {
  try {
    const automations = readTasks();
    return NextResponse.json({ ok: true, automations });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: String(err), automations: [] },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const action = String(body.action || '');
    const tasks = readTasks();

    if (action === 'create_or_update') {
      const id = String(body.id || '').trim();
      const nome = String(body.nome || '').trim();
      const remetente_ou_busca = String(body.remetente_ou_busca || '').trim();
      const pasta_drive = String(body.pasta_drive || '').trim();
      const tipo_arquivo = String(body.tipo_arquivo || 'pdf').trim().toLowerCase();
      const organizar_por_ano_mes =
        body.organizar_por_ano_mes !== undefined ? Boolean(body.organizar_por_ano_mes) : true;
      const marcar_como_lido =
        body.marcar_como_lido !== undefined ? Boolean(body.marcar_como_lido) : true;
      const frequencia: 'diario' | 'hora_em_hora' =
        body.frequencia === 'hora_em_hora' ? 'hora_em_hora' : 'diario';
      let horario = String(body.horario || '08:00').trim();
      if (horario.length === 4 && horario.includes(':')) {
        horario = '0' + horario;
      }
      const ativo = body.ativo !== undefined ? Boolean(body.ativo) : true;
      const senha_anexo = String(body.senha_anexo || '').trim();

      if (!nome || !remetente_ou_busca || !pasta_drive) {
        return NextResponse.json(
          { ok: false, error: 'Preencha Nome, Remetente/Busca e Pasta do Google Drive.' },
          { status: 400 }
        );
      }

      if (id) {
        const idx = tasks.findIndex((t) => t.id === id);
        if (idx !== -1) {
          const old = tasks[idx];
          tasks[idx] = {
            ...old,
            nome,
            remetente_ou_busca,
            pasta_drive,
            tipo_arquivo,
            organizar_por_ano_mes,
            marcar_como_lido,
            frequencia,
            horario,
            ativo,
            senha_anexo,
            ultima_execucao_data: old.horario !== horario ? '' : old.ultima_execucao_data,
          };
          writeTasks(tasks);
          return NextResponse.json({ ok: true, automations: tasks, item: tasks[idx] });
        }
      }

      const newTask: AutomationTask = {
        id: `auto-${Math.random().toString(36).substring(2, 10)}`,
        nome,
        remetente_ou_busca,
        pasta_drive,
        tipo_arquivo,
        organizar_por_ano_mes,
        marcar_como_lido,
        frequencia,
        horario,
        ativo,
        senha_anexo,
        criado_em_ts: Date.now() / 1000,
        ids_processados: [],
        ultima_execucao_data: '',
        ultima_execucao_ts: 0,
        ultimo_resultado: 'Aguardando execução',
      };
      tasks.push(newTask);
      writeTasks(tasks);
      return NextResponse.json({ ok: true, automations: tasks, item: newTask });
    }

    if (action === 'toggle') {
      const id = String(body.id || '').trim();
      const idx = tasks.findIndex((t) => t.id === id);
      if (idx !== -1) {
        tasks[idx].ativo = body.ativo !== undefined ? Boolean(body.ativo) : !tasks[idx].ativo;
        writeTasks(tasks);
      }
      return NextResponse.json({ ok: true, automations: tasks });
    }

    if (action === 'delete') {
      const id = String(body.id || '').trim();
      const filtered = tasks.filter((t) => t.id !== id);
      writeTasks(filtered);
      return NextResponse.json({ ok: true, automations: filtered });
    }

    return NextResponse.json({ ok: false, error: 'Ação inválida' }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}

