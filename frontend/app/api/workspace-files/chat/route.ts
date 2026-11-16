import { NextResponse } from 'next/server';
import { clearNotebookChat, getNotebookChat } from '@/lib/notebooks-storage';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const notebookId = searchParams.get('notebookId') || 'geral';
    const messages = getNotebookChat(notebookId);
    return NextResponse.json({ ok: true, messages });
  } catch (err) {
    console.error('Erro ao ler chat do caderno:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao recuperar histórico do caderno' },
      { status: 500 }
    );
  }
}

export async function DELETE(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const notebookId = searchParams.get('notebookId') || 'geral';
    clearNotebookChat(notebookId);
    return NextResponse.json({ ok: true, cleared: true, notebookId });
  } catch (err) {
    console.error('Erro ao limpar chat do caderno:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao limpar histórico do caderno' },
      { status: 500 }
    );
  }
}
