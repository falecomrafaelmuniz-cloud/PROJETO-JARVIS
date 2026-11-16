import { NextResponse } from 'next/server';
import {
  createNotebook,
  deleteNotebook,
  listNotebooks,
  renameNotebook,
  updateNotebookExpert,
} from '@/lib/notebooks-storage';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  try {
    const list = listNotebooks();
    return NextResponse.json({ ok: true, notebooks: list });
  } catch (err) {
    console.error('Erro ao listar cadernos:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao listar cadernos' },
      { status: 500 }
    );
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json().catch(() => ({}));
    const name = (body.name || '').trim();
    if (!name) {
      return NextResponse.json(
        { ok: false, error: 'O nome do caderno é obrigatório.' },
        { status: 400 }
      );
    }
    const created = createNotebook(name);
    return NextResponse.json({ ok: true, notebook: created });
  } catch (err) {
    console.error('Erro ao criar caderno:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao criar caderno' },
      { status: 500 }
    );
  }
}

export async function PATCH(req: Request) {
  try {
    const body = await req.json().catch(() => ({}));
    const id = (body.id || '').trim();
    if (!id) {
      return NextResponse.json(
        { ok: false, error: 'ID do caderno é obrigatório.' },
        { status: 400 }
      );
    }

    let updated = null;

    // Se estiver atualizando o especialista do caderno
    if (body.expert !== undefined) {
      updated = updateNotebookExpert(id, String(body.expert));
    }

    // Se estiver renomeando o caderno
    if (body.name !== undefined && String(body.name).trim()) {
      updated = renameNotebook(id, String(body.name).trim());
    }

    if (!updated) {
      return NextResponse.json(
        { ok: false, error: 'Caderno não encontrado ou nenhum campo para atualizar.' },
        { status: 404 }
      );
    }
    return NextResponse.json({ ok: true, notebook: updated });
  } catch (err) {
    console.error('Erro ao atualizar caderno:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao atualizar caderno' },
      { status: 500 }
    );
  }
}

export async function DELETE(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const id = (searchParams.get('id') || '').trim();
    if (!id) {
      return NextResponse.json(
        { ok: false, error: 'ID do caderno é obrigatório.' },
        { status: 400 }
      );
    }
    if (id === 'geral') {
      return NextResponse.json(
        { ok: false, error: 'O caderno padrão Geral não pode ser excluído.' },
        { status: 400 }
      );
    }
    const deleted = deleteNotebook(id);
    if (!deleted) {
      return NextResponse.json(
        { ok: false, error: 'Caderno não encontrado ou erro ao excluir.' },
        { status: 404 }
      );
    }
    return NextResponse.json({ ok: true, deletedId: id });
  } catch (err) {
    console.error('Erro ao excluir caderno:', err);
    return NextResponse.json(
      { ok: false, error: 'Falha ao excluir caderno' },
      { status: 500 }
    );
  }
}
