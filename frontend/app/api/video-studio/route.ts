import { NextRequest, NextResponse } from 'next/server';
import fs from 'node:fs/promises';
import { createReadStream, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import crypto from 'node:crypto';
import { Readable } from 'node:stream';

const execFileAsync = promisify(execFile);

function resolvePaths() {
  const backendDir = path.resolve(process.cwd(), '..', 'backend');
  const venvPython = path.resolve(process.cwd(), '..', '.venv', 'Scripts', 'python.exe');
  const pythonExe = existsSync(venvPython) ? venvPython : 'python';
  const scriptPath = path.join(backendDir, 'jarvis_video_studio.py');
  const outputDir = path.join(backendDir, 'video_studio_output');
  const jobsDir = path.join(outputDir, 'jobs');
  return { backendDir, pythonExe, scriptPath, outputDir, jobsDir };
}

function sanitizeFilename(raw: string): string | null {
  const base = path.basename(raw.trim());
  if (!base || base !== raw.trim()) return null;
  if (!/^[a-zA-Z0-9._-]+$/.test(base)) return null;
  return base;
}

export async function GET(req: NextRequest) {
  const { outputDir, jobsDir } = resolvePaths();
  const { searchParams } = new URL(req.url);

  const fileParam = searchParams.get('file');
  if (fileParam) {
    const safeName = sanitizeFilename(fileParam);
    if (!safeName || !safeName.endsWith('.mp4')) {
      return NextResponse.json({ error: 'Arquivo inválido.' }, { status: 400 });
    }
    const filePath = path.join(outputDir, safeName);
    if (!existsSync(filePath)) {
      return NextResponse.json({ error: 'Vídeo não encontrado.' }, { status: 404 });
    }

    const stat = statSync(filePath);
    const total = stat.size;
    const range = req.headers.get('range');
    const download = searchParams.get('download') === '1';

    if (range) {
      const match = /bytes=(\d+)-(\d*)/.exec(range);
      if (match) {
        const start = Number(match[1]);
        const end = match[2] ? Math.min(total - 1, Number(match[2])) : total - 1;
        if (start >= total || end < start) {
          return new NextResponse(null, {
            status: 416,
            headers: { 'Content-Range': `bytes */${total}` },
          });
        }
        const chunkSize = end - start + 1;
        const stream = createReadStream(filePath, { start, end });
        const webStream = Readable.toWeb(stream) as ReadableStream;
        return new NextResponse(webStream, {
          status: 206,
          headers: {
            'Content-Range': `bytes ${start}-${end}/${total}`,
            'Accept-Ranges': 'bytes',
            'Content-Length': String(chunkSize),
            'Content-Type': 'video/mp4',
          },
        });
      }
    }

    const stream = createReadStream(filePath);
    const webStream = Readable.toWeb(stream) as ReadableStream;
    const headers: Record<string, string> = {
      'Content-Type': 'video/mp4',
      'Content-Length': String(total),
      'Accept-Ranges': 'bytes',
    };
    if (download) {
      headers['Content-Disposition'] = `attachment; filename="${safeName}"`;
    }
    return new NextResponse(webStream, { status: 200, headers });
  }

  const jobIdParam = searchParams.get('jobId');
  if (jobIdParam) {
    const safeJob = sanitizeFilename(`${jobIdParam}.json`);
    if (!safeJob) {
      return NextResponse.json({ error: 'jobId inválido.' }, { status: 400 });
    }
    const jobPath = path.join(jobsDir, safeJob);
    if (!existsSync(jobPath)) {
      return NextResponse.json({ status: 'queued', progress: 2, step: 'Iniciando...' });
    }
    const raw = await fs.readFile(jobPath, 'utf-8');
    return NextResponse.json(JSON.parse(raw));
  }

  // Lista biblioteca de vídeos salvos
  await fs.mkdir(outputDir, { recursive: true });
  const entries = await fs.readdir(outputDir, { withFileTypes: true });
  const videos = [];

  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.mp4')) continue;
    const full = path.join(outputDir, entry.name);
    const st = await fs.stat(full);
    const metaPath = path.join(outputDir, `${entry.name.slice(0, -4)}.json`);
    let meta: Record<string, unknown> = {};
    if (existsSync(metaPath)) {
      try {
        meta = JSON.parse(await fs.readFile(metaPath, 'utf-8'));
      } catch {
        // ignore broken sidecar
      }
    }
    videos.push({
      filename: entry.name,
      titulo: typeof meta.titulo === 'string' ? meta.titulo : entry.name.replace(/\.mp4$/, ''),
      aspect_ratio: typeof meta.aspect_ratio === 'string' ? meta.aspect_ratio : '9:16',
      voice: typeof meta.voice === 'string' ? meta.voice : 'Orus',
      duration_sec: typeof meta.duration_sec === 'number' ? meta.duration_sec : 0,
      scenes_count: typeof meta.scenes_count === 'number' ? meta.scenes_count : 0,
      size_bytes: st.size,
      created_at:
        typeof meta.created_at === 'string'
          ? meta.created_at
          : new Date(st.mtimeMs).toISOString(),
    });
  }

  videos.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return NextResponse.json({ videos });
}

export async function POST(req: NextRequest) {
  const { pythonExe, scriptPath, jobsDir, outputDir } = resolvePaths();
  await fs.mkdir(jobsDir, { recursive: true });

  const body = (await req.json()) as Record<string, unknown>;
  const action = String(body.action || '');

  if (action === 'generate_script') {
    const tmpId = crypto.randomUUID().slice(0, 8);
    const inputPath = path.join(jobsDir, `script_${tmpId}.json`);
    await fs.writeFile(inputPath, JSON.stringify(body, null, 2), 'utf-8');

    try {
      const { stdout } = await execFileAsync(
        pythonExe,
        [scriptPath, 'script', '--input', inputPath],
        {
          timeout: 65_000,
          maxBuffer: 10 * 1024 * 1024,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        }
      );
      const parsed = JSON.parse(stdout);
      return NextResponse.json(parsed);
    } catch (err: unknown) {
      const e = err as { stderr?: string; message?: string };
      return NextResponse.json(
        { error: e.stderr || e.message || 'Erro ao gerar roteiro.' },
        { status: 500 }
      );
    } finally {
      await fs.unlink(inputPath).catch(() => {});
    }
  }

  if (action === 'start_render') {
    const jobId = `vid_${Date.now()}_${crypto.randomUUID().slice(0, 6)}`;
    const inputPath = path.join(jobsDir, `${jobId}_input.json`);
    const statusPath = path.join(jobsDir, `${jobId}.json`);

    await fs.writeFile(
      inputPath,
      JSON.stringify({ ...body, job_id: jobId }, null, 2),
      'utf-8'
    );
    await fs.writeFile(
      statusPath,
      JSON.stringify({
        job_id: jobId,
        status: 'queued',
        progress: 2,
        step: 'Inicializando motor de vídeo...',
      }),
      'utf-8'
    );

    const child = spawn(pythonExe, [scriptPath, 'render', '--input', inputPath], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    child.unref();

    return NextResponse.json({ jobId });
  }

  if (action === 'delete') {
    const safeName = sanitizeFilename(String(body.filename || ''));
    if (!safeName || !safeName.endsWith('.mp4')) {
      return NextResponse.json({ error: 'Arquivo inválido.' }, { status: 400 });
    }
    const mp4Path = path.join(outputDir, safeName);
    const jsonPath = path.join(outputDir, `${safeName.slice(0, -4)}.json`);
    await fs.unlink(mp4Path).catch(() => {});
    await fs.unlink(jsonPath).catch(() => {});
    return NextResponse.json({ ok: true });
  }

  return NextResponse.json({ error: 'Ação desconhecida.' }, { status: 400 });
}
