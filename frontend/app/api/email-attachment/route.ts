import { NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import { mkdir, writeFile } from 'fs/promises';
import os from 'os';
import path from 'path';

export const revalidate = 0;

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const entries = formData.getAll('files');
    if (!entries || entries.length === 0) {
      return NextResponse.json({ files: [] });
    }

    const uploadDir = path.join(os.tmpdir(), 'jarvis-email-attachments');
    await mkdir(uploadDir, { recursive: true });

    const savedFiles: Array<{
      id: string;
      tipo: 'local';
      nome: string;
      caminho_ou_id: string;
    }> = [];

    for (const entry of entries) {
      if (!(entry instanceof File)) continue;
      const safeName = path.basename(entry.name || 'anexo');
      const uniqueId = randomUUID().slice(0, 8);
      const targetPath = path.join(uploadDir, `${uniqueId}-${safeName}`);
      const arrayBuffer = await entry.arrayBuffer();
      await writeFile(targetPath, Buffer.from(arrayBuffer));

      savedFiles.push({
        id: `local-${uniqueId}`,
        tipo: 'local',
        nome: safeName,
        caminho_ou_id: targetPath,
      });
    }

    return NextResponse.json({ files: savedFiles });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Falha ao salvar anexo';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
