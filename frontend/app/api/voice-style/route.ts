import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export const revalidate = 0;

export type JarvisVoiceStyle = 'descolado' | 'executivo';

function getConfigPath(): string {
  return path.resolve(process.cwd(), '..', 'backend', 'jarvis_voz_config.json');
}

function normalizeStyle(raw: unknown): JarvisVoiceStyle {
  const s = String(raw || '').trim().toLowerCase();
  if (s === 'executivo' || s.includes('serio') || s.includes('sério')) {
    return 'executivo';
  }
  return 'descolado';
}

export async function GET() {
  try {
    const cfgPath = getConfigPath();
    if (fs.existsSync(cfgPath)) {
      const parsed = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
      return NextResponse.json({
        status: 'ok',
        style: normalizeStyle(parsed?.style),
      });
    }
  } catch {
    // fallback para o perfil descolado original
  }
  return NextResponse.json({ status: 'ok', style: 'descolado' });
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const style = normalizeStyle(body?.style);
    const cfgPath = getConfigPath();
    fs.writeFileSync(
      cfgPath,
      JSON.stringify({ style, updated_at: new Date().toISOString() }, null, 2),
      'utf8'
    );
    return NextResponse.json({ status: 'ok', style });
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        message: error instanceof Error ? error.message : 'Erro ao salvar estilo de voz.',
      },
      { status: 500 }
    );
  }
}
