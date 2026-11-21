import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import util from 'util';

const execFileAsync = util.promisify(execFile);

export const dynamic = 'force-dynamic';

export interface AntigravityTask {
  id: number;
  data_hora: string;
  titulo: string;
  descricao: string;
  arquivo_sugerido: string;
  prioridade: string;
  status: 'pendente' | 'em_progresso' | 'concluida' | 'falha';
  origem: string;
  resolucao?: string;
  resolvido_em?: string;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'folder';
  children?: FileTreeNode[];
}

function getRootDir(): string {
  if (fs.existsSync(path.join(process.cwd(), 'backend', 'jarvis_antigravity.py'))) {
    return process.cwd();
  }
  const parent = path.resolve(process.cwd(), '..');
  if (fs.existsSync(path.join(parent, 'backend', 'jarvis_antigravity.py'))) {
    return parent;
  }
  const fallback = 'c:\\Users\\Usuario\\Downloads\\jvs_auth_92kx-main\\jvs_auth_92kx-main\\Jarvis- Aula 01';
  if (fs.existsSync(path.join(fallback, 'backend', 'jarvis_antigravity.py'))) {
    return fallback;
  }
  return parent;
}

function getTasksFilePath(): string {
  return path.resolve(getRootDir(), 'solicitacoes_antigravity.json');
}

function readTasks(): AntigravityTask[] {
  const filePath = getTasksFilePath();
  if (!fs.existsSync(filePath)) {
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

function writeTasks(tasks: AntigravityTask[]): void {
  const filePath = getTasksFilePath();
  fs.writeFileSync(filePath, JSON.stringify(tasks, null, 2), 'utf-8');
}

const IGNORE_DIRS = new Set([
  '.git',
  '.venv',
  'venv',
  'node_modules',
  '.next',
  '__pycache__',
  'dist',
  'build',
  'bin',
  'logs',
  'memoria_jarvis',
  'memoria_jarvis_local_backup_20261017',
]);

function buildFileTree(currentDir: string, baseDir: string, depth = 0, maxDepth = 4): FileTreeNode[] {
  if (depth > maxDepth) return [];
  try {
    const entries = fs.readdirSync(currentDir, { withFileTypes: true });
    const nodes: FileTreeNode[] = [];

    // Prioriza pastas primeiro
    const sorted = entries.sort((a, b) => {
      if (a.isDirectory() === b.isDirectory()) return a.name.localeCompare(b.name);
      return a.isDirectory() ? -1 : 1;
    });

    for (const entry of sorted) {
      if (entry.name.startsWith('.') && entry.name !== '.env') continue;
      if (IGNORE_DIRS.has(entry.name)) continue;

      const fullPath = path.join(currentDir, entry.name);
      const relPath = path.relative(baseDir, fullPath).replace(/\\/g, '/');

      if (entry.isDirectory()) {
        const children = buildFileTree(fullPath, baseDir, depth + 1, maxDepth);
        nodes.push({
          name: entry.name,
          path: relPath,
          type: 'folder',
          children,
        });
      } else {
        nodes.push({
          name: entry.name,
          path: relPath,
          type: 'file',
        });
      }
    }
    return nodes;
  } catch {
    return [];
  }
}

// GET: Retorna tarefas, árvore de arquivos ou conteúdo de um arquivo
export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const action = searchParams.get('action') || 'tasks';
    const rootDir = getRootDir();

    if (action === 'tree') {
      const tree = buildFileTree(rootDir, rootDir);
      return NextResponse.json({ ok: true, tree });
    }

    if (action === 'file') {
      const targetRel = searchParams.get('path') || '';
      if (!targetRel) {
        return NextResponse.json({ ok: false, error: 'Caminho não fornecido' }, { status: 400 });
      }

      let filePath = path.normalize(targetRel);
      if (!path.isAbsolute(filePath)) {
        filePath = path.normalize(path.join(rootDir, targetRel));
      }

      if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
        return NextResponse.json({ ok: false, error: 'Arquivo inválido ou não encontrado' }, { status: 404 });
      }

      const content = fs.readFileSync(filePath, 'utf-8');
      return NextResponse.json({
        ok: true,
        path: targetRel.replace(/\\/g, '/'),
        name: path.basename(filePath),
        content,
      });
    }

    // Default: lista de tarefas
    const tasks = readTasks();
    return NextResponse.json({ ok: true, tasks });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Erro ao processar requisição Antigravity';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}

// POST: Cria uma nova solicitação ou atualiza status
export async function POST(req: Request) {
  try {
    const body = await req.json();
    const action = body.action || 'create';

    const tasks = readTasks();

    if (action === 'create') {
      const novoId = tasks.length > 0 ? Math.max(...tasks.map((t) => t.id || 0)) + 1 : 1;
      const dataHora = new Date().toLocaleString('pt-BR');

      const novaTarefa: AntigravityTask = {
        id: novoId,
        data_hora: dataHora,
        titulo: (body.titulo || 'Nova solicitação').trim(),
        descricao: (body.descricao || '').trim(),
        arquivo_sugerido: (body.arquivo_sugerido || '').trim() || 'backend/agent.py',
        prioridade: body.prioridade || 'normal',
        status: 'pendente',
        origem: body.origem || 'Painel Antigravity Frontend',
      };

      tasks.push(novaTarefa);
      writeTasks(tasks);

      return NextResponse.json({ ok: true, task: novaTarefa, tasks });
    }

    if (action === 'update_status') {
      const id = Number(body.id);
      const novoStatus = body.status;
      const index = tasks.findIndex((t) => t.id === id);

      if (index === -1) {
        return NextResponse.json({ ok: false, error: 'Tarefa não encontrada' }, { status: 404 });
      }

      tasks[index].status = novoStatus;
      if (body.resolucao) {
        tasks[index].resolucao = body.resolucao;
        tasks[index].resolvido_em = new Date().toLocaleString('pt-BR');
      }

      writeTasks(tasks);
      return NextResponse.json({ ok: true, task: tasks[index], tasks });
    }

    if (action === 'chat') {
      const message = (body.message || '').trim();
      const activeTab = (body.activeTab || '').trim();

      if (!message) {
        return NextResponse.json({ ok: false, error: 'Mensagem vazia' }, { status: 400 });
      }

      const rootDir = getRootDir();
      const venvPy = path.join(rootDir, '.venv', 'Scripts', 'python.exe');
      const pythonExe = fs.existsSync(venvPy) ? venvPy : 'python';
      const scriptPath = path.join(rootDir, 'backend', 'jarvis_antigravity.py');

      try {
        const { stdout, stderr } = await execFileAsync(
          pythonExe,
          [scriptPath, '--prompt', message, '--target', activeTab],
          {
            cwd: rootDir,
            timeout: 90000,
            maxBuffer: 15 * 1024 * 1024,
            encoding: 'utf-8',
            env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
          }
        );

        const lines = stdout.trim().split('\n');
        let parsed = null;
        for (let i = lines.length - 1; i >= 0; i--) {
          const l = lines[i].trim();
          if (l.startsWith('{') && l.endsWith('}')) {
            try {
              parsed = JSON.parse(l);
              break;
            } catch {
              // continua procurando
            }
          }
        }

        if (!parsed) {
          try {
            parsed = JSON.parse(stdout.trim());
          } catch {
            return NextResponse.json(
              {
                ok: false,
                error: 'Resposta inválida do motor Antigravity',
                raw: stdout || stderr,
              },
              { status: 500 }
            );
          }
        }

        return NextResponse.json({
          ok: true,
          sucesso: parsed.sucesso ?? true,
          reply: parsed.resumo || 'Processamento concluído.',
          arquivos_alterados: parsed.arquivos_alterados || [],
          passos_executados: parsed.passos_executados || 1,
          historico: parsed.historico || [],
        });
      } catch (err: unknown) {
        const errObj = err as { message?: string; stderr?: string };
        return NextResponse.json(
          {
            ok: false,
            error: errObj?.message || 'Falha ao executar Antigravity via backend',
            stderr: errObj?.stderr || '',
          },
          { status: 500 }
        );
      }
    }

    return NextResponse.json({ ok: false, error: 'Ação desconhecida' }, { status: 400 });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Erro ao processar requisição';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
