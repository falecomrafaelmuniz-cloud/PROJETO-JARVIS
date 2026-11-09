import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export const dynamic = 'force-dynamic';

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

function getBrasiliaDateString(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Sao_Paulo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function getBrasiliaDateTimeLabel(): string {
  return new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date());
}

function getRemindersFilePath(): string {
  return path.resolve(process.cwd(), '..', 'backend', 'lembretes_jarvis.json');
}

function getAttachmentsDir(): string {
  const dir = path.resolve(process.cwd(), '..', 'backend', 'lembretes_anexos');
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function deleteSavedAttachmentFiles(anexos?: ReminderAttachment[]) {
  if (!Array.isArray(anexos)) return;
  const dir = getAttachmentsDir();
  for (const a of anexos) {
    if (!a?.arquivo_salvo) continue;
    const safeName = path.basename(a.arquivo_salvo);
    const fullPath = path.join(dir, safeName);
    try {
      if (fs.existsSync(fullPath)) {
        fs.unlinkSync(fullPath);
      }
    } catch {
      // ignore
    }
  }
}

function saveBase64Attachment(nomeOriginal: string, mime: string, base64Data: string): ReminderAttachment {
  const dir = getAttachmentsDir();
  const ext = path.extname(nomeOriginal || '') || '';
  const base = path
    .basename(nomeOriginal || 'anexo', ext)
    .replace(/[^a-zA-Z0-9._-]/g, '_')
    .slice(0, 60);
  const uniqueId = `att-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
  const savedFileName = `${uniqueId}_${base}${ext}`;
  const fullPath = path.join(dir, savedFileName);

  const cleanBase64 = base64Data.includes(',')
    ? base64Data.split(',')[1]
    : base64Data;
  const buffer = Buffer.from(cleanBase64, 'base64');
  fs.writeFileSync(fullPath, buffer);

  return {
    id: uniqueId,
    nome: nomeOriginal || savedFileName,
    mime: mime || 'application/octet-stream',
    arquivo_salvo: savedFileName,
  };
}

function readAndPurgeReminders(): ReminderItem[] {
  const filePath = getRemindersFilePath();
  if (!fs.existsSync(filePath)) {
    fs.writeFileSync(filePath, '[]', 'utf-8');
    return [];
  }
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    const today = getBrasiliaDateString();
    const active: ReminderItem[] = [];
    let changed = false;

    for (const item of parsed as ReminderItem[]) {
      if (
        item.status === 'concluido' &&
        item.concluido_em_data &&
        item.concluido_em_data < today
      ) {
        deleteSavedAttachmentFiles(item.anexos);
        changed = true;
      } else {
        active.push(item);
      }
    }

    if (changed) {
      fs.writeFileSync(filePath, JSON.stringify(active, null, 2), 'utf-8');
    }
    return active;
  } catch {
    return [];
  }
}

function writeReminders(items: ReminderItem[]): void {
  const filePath = getRemindersFilePath();
  fs.writeFileSync(filePath, JSON.stringify(items, null, 2), 'utf-8');
}

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const fileParam = searchParams.get('file');
    const mimeParam = searchParams.get('mime') || 'application/octet-stream';
    const nameParam = searchParams.get('name') || fileParam || 'anexo';

    if (fileParam) {
      const safeName = path.basename(fileParam);
      const fullPath = path.join(getAttachmentsDir(), safeName);
      if (!fs.existsSync(fullPath)) {
        return new NextResponse('Arquivo não encontrado', { status: 404 });
      }
      const fileBuffer = fs.readFileSync(fullPath);
      return new NextResponse(fileBuffer, {
        status: 200,
        headers: {
          'Content-Type': mimeParam,
          'Content-Disposition': `inline; filename="${encodeURIComponent(nameParam)}"`,
        },
      });
    }

    const reminders = readAndPurgeReminders();
    return NextResponse.json({ ok: true, reminders });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: String(err), reminders: [] },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const action = String(body.action || '');
    const reminders = readAndPurgeReminders();
    const today = getBrasiliaDateString();

    if (action === 'add') {
      const textoRaw = String(body.texto || '').trim();
      const anexosInput = Array.isArray(body.anexos) ? body.anexos : [];
      const anexosSalvos: ReminderAttachment[] = [];

      for (const a of anexosInput) {
        if (a && a.base64) {
          anexosSalvos.push(
            saveBase64Attachment(
              String(a.nome || 'anexo'),
              String(a.mime || 'application/octet-stream'),
              String(a.base64)
            )
          );
        }
      }

      const texto = textoRaw || (anexosSalvos.length > 0 ? anexosSalvos[0].nome : '');
      if (!texto) {
        return NextResponse.json(
          { ok: false, error: 'Digite o texto do lembrete ou anexe um arquivo.' },
          { status: 400 }
        );
      }

      // Todos os lembretes novos entram sempre como PENDENTE (vermelho)
      const novo: ReminderItem = {
        id: `rem-${Math.random().toString(36).substring(2, 10)}`,
        texto,
        status: 'pendente',
        criado_em: getBrasiliaDateTimeLabel(),
        concluido_em_data: '',
        anexos: anexosSalvos,
      };
      reminders.push(novo);
      writeReminders(reminders);
      return NextResponse.json({ ok: true, reminders, item: novo });
    }

    if (action === 'attach') {
      const id = String(body.id || '').trim();
      const idx = reminders.findIndex((r) => r.id === id);
      if (idx === -1) {
        return NextResponse.json(
          { ok: false, error: 'Lembrete não encontrado.' },
          { status: 404 }
        );
      }
      const anexosInput = Array.isArray(body.anexos) ? body.anexos : [];
      const atuais = Array.isArray(reminders[idx].anexos) ? [...reminders[idx].anexos!] : [];
      for (const a of anexosInput) {
        if (a && a.base64) {
          atuais.push(
            saveBase64Attachment(
              String(a.nome || 'anexo'),
              String(a.mime || 'application/octet-stream'),
              String(a.base64)
            )
          );
        }
      }
      reminders[idx].anexos = atuais;
      writeReminders(reminders);
      return NextResponse.json({ ok: true, reminders });
    }

    if (action === 'remove_attachment') {
      const id = String(body.id || '').trim();
      const attId = String(body.attachment_id || '').trim();
      const idx = reminders.findIndex((r) => r.id === id);
      if (idx !== -1 && Array.isArray(reminders[idx].anexos)) {
        const toRemove = reminders[idx].anexos!.filter((a) => a.id === attId);
        deleteSavedAttachmentFiles(toRemove);
        reminders[idx].anexos = reminders[idx].anexos!.filter((a) => a.id !== attId);
        writeReminders(reminders);
      }
      return NextResponse.json({ ok: true, reminders });
    }

    if (action === 'update_status') {
      const id = String(body.id || '').trim();
      const status: ReminderStatus =
        body.status === 'em_andamento' || body.status === 'concluido'
          ? body.status
          : 'pendente';

      const idx = reminders.findIndex((r) => r.id === id);
      if (idx !== -1) {
        reminders[idx].status = status;
        reminders[idx].concluido_em_data = status === 'concluido' ? today : '';
        writeReminders(reminders);
      }
      return NextResponse.json({ ok: true, reminders });
    }

    if (action === 'delete') {
      const id = String(body.id || '').trim();
      const toDelete = reminders.filter((r) => r.id === id);
      for (const item of toDelete) {
        deleteSavedAttachmentFiles(item.anexos);
      }
      const filtered = reminders.filter((r) => r.id !== id);
      writeReminders(filtered);
      return NextResponse.json({ ok: true, reminders: filtered });
    }

    return NextResponse.json({ ok: false, error: 'Ação inválida' }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}
