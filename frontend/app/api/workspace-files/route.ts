import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import {
  WorkspaceFileItem,
  formatBytes,
  getFileType,
  getNotebookDir,
  listNotebookFiles,
} from '@/lib/notebooks-storage';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// GET: Lista arquivos de um caderno específico (ou 'geral' por padrão)
export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const notebookId = searchParams.get('notebookId') || 'geral';
    const files = listNotebookFiles(notebookId);
    return NextResponse.json({ ok: true, files, notebookId });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Erro ao listar arquivos';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}

// POST: Upload de arquivos para o caderno ativo (Multipart ou Base64)
export async function POST(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    let notebookId = searchParams.get('notebookId') || '';

    const contentType = req.headers.get('content-type') || '';
    const savedFiles: WorkspaceFileItem[] = [];

    if (contentType.includes('multipart/form-data')) {
      const formData = await req.formData();
      if (!notebookId) {
        notebookId = (formData.get('notebookId') as string) || 'geral';
      }
      const dir = getNotebookDir(notebookId);
      const files = formData.getAll('files');

      for (const item of files) {
        if (!(item instanceof File)) continue;
        const rawName = path.basename(item.name || 'arquivo');
        const uniquePrefix = Math.random().toString(36).substring(2, 10);
        const savedName = `${uniquePrefix}-${rawName}`;
        const targetPath = path.join(dir, savedName);

        const arrayBuffer = await item.arrayBuffer();
        fs.writeFileSync(targetPath, Buffer.from(arrayBuffer));

        const stat = fs.statSync(targetPath);
        const ext = path.extname(rawName).toLowerCase();

        savedFiles.push({
          id: savedName,
          name: rawName,
          extension: ext,
          size: stat.size,
          formattedSize: formatBytes(stat.size),
          type: getFileType(ext),
          path: targetPath,
          uploadedAt: stat.mtime.toISOString(),
        });
      }
    } else if (contentType.includes('application/json')) {
      const body = await req.json();
      if (!notebookId) {
        notebookId = body.notebookId || 'geral';
      }
      const dir = getNotebookDir(notebookId);

      // Suporte para colar print / base64 ou texto direto
      if (body.base64 && body.name) {
        const rawName = path.basename(body.name);
        const uniquePrefix = Math.random().toString(36).substring(2, 10);
        const savedName = `${uniquePrefix}-${rawName}`;
        const targetPath = path.join(dir, savedName);

        const base64Data = body.base64.replace(/^data:.*?;base64,/, '');
        fs.writeFileSync(targetPath, Buffer.from(base64Data, 'base64'));

        const stat = fs.statSync(targetPath);
        const ext = path.extname(rawName).toLowerCase();

        savedFiles.push({
          id: savedName,
          name: rawName,
          extension: ext,
          size: stat.size,
          formattedSize: formatBytes(stat.size),
          type: getFileType(ext),
          path: targetPath,
          uploadedAt: stat.mtime.toISOString(),
        });
      } else if (body.text && body.name) {
        const rawName = path.basename(body.name);
        const uniquePrefix = Math.random().toString(36).substring(2, 10);
        const savedName = `${uniquePrefix}-${rawName}`;
        const targetPath = path.join(dir, savedName);

        fs.writeFileSync(targetPath, body.text, 'utf-8');
        const stat = fs.statSync(targetPath);
        const ext = path.extname(rawName).toLowerCase();

        savedFiles.push({
          id: savedName,
          name: rawName,
          extension: ext,
          size: stat.size,
          formattedSize: formatBytes(stat.size),
          type: getFileType(ext),
          path: targetPath,
          uploadedAt: stat.mtime.toISOString(),
        });
      }
    }

    if (!notebookId) notebookId = 'geral';
    const allFiles = listNotebookFiles(notebookId);
    return NextResponse.json({ ok: true, saved: savedFiles, files: allFiles, notebookId });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Erro ao salvar arquivo';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}

// DELETE: Exclui um arquivo pelo ID ou limpa todos os arquivos do caderno
export async function DELETE(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const notebookId = searchParams.get('notebookId') || 'geral';
    const fileId = searchParams.get('id');
    const clearAll = searchParams.get('all') === 'true';
    const dir = getNotebookDir(notebookId);

    if (clearAll) {
      const entries = fs.readdirSync(dir);
      for (const entry of entries) {
        if (entry === 'notebook.json' || entry === 'chat_history.json') continue;
        try {
          fs.unlinkSync(path.join(dir, entry));
        } catch {}
      }
      return NextResponse.json({ ok: true, files: [], notebookId });
    }

    if (fileId) {
      const safeId = path.basename(fileId);
      const filePath = path.join(dir, safeId);
      if (fs.existsSync(filePath)) {
        fs.unlinkSync(filePath);
      }
    }

    const allFiles = listNotebookFiles(notebookId);
    return NextResponse.json({ ok: true, files: allFiles, notebookId });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Erro ao excluir arquivo';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
