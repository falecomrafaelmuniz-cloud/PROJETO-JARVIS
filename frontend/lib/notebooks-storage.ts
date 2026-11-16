import fs from 'fs';
import path from 'path';

export * from './notebooks-types';
import {
  ChatMessage,
  NotebookItem,
  NotebookMetadata,
  WorkspaceFileItem,
  formatBytes,
  getFileType,
} from './notebooks-types';

export function getWorkspaceBaseDir(): string {
  const dir = path.resolve(process.cwd(), '..', 'backend', 'workspace_arquivos');
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

export function getNotebooksRootDir(): string {
  const root = path.join(getWorkspaceBaseDir(), 'notebooks');
  if (!fs.existsSync(root)) {
    fs.mkdirSync(root, { recursive: true });
  }
  return root;
}

export function ensureDefaultNotebook(): void {
  const notebooksRoot = getNotebooksRootDir();
  const baseDir = getWorkspaceBaseDir();

  // Verifica se o caderno 'geral' existe
  const defaultDir = path.join(notebooksRoot, 'geral');
  if (!fs.existsSync(defaultDir)) {
    fs.mkdirSync(defaultDir, { recursive: true });
    const meta: NotebookMetadata = {
      id: 'geral',
      name: 'Geral',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(path.join(defaultDir, 'notebook.json'), JSON.stringify(meta, null, 2), 'utf-8');
    fs.writeFileSync(path.join(defaultDir, 'chat_history.json'), JSON.stringify([], null, 2), 'utf-8');
  }

  // Migra arquivos legados na raiz de workspace_arquivos para o caderno geral
  try {
    const rootFiles = fs.readdirSync(baseDir);
    for (const file of rootFiles) {
      const fullPath = path.join(baseDir, file);
      if (file !== 'notebooks' && fs.statSync(fullPath).isFile()) {
        const dest = path.join(defaultDir, file);
        if (!fs.existsSync(dest)) {
          fs.renameSync(fullPath, dest);
        }
      }
    }
  } catch (err) {
    console.error('Erro ao migrar arquivos legados:', err);
  }
}

export function listNotebooks(): NotebookItem[] {
  ensureDefaultNotebook();
  const notebooksRoot = getNotebooksRootDir();
  const entries = fs.readdirSync(notebooksRoot);
  const list: NotebookItem[] = [];

  for (const entry of entries) {
    const nDir = path.join(notebooksRoot, entry);
    if (!fs.statSync(nDir).isDirectory()) continue;

    const metaPath = path.join(nDir, 'notebook.json');
    let meta: NotebookMetadata;
    if (fs.existsSync(metaPath)) {
      try {
        meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
      } catch {
        meta = {
          id: entry,
          name: entry.replace(/[-_]/g, ' '),
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
      }
    } else {
      meta = {
        id: entry,
        name: entry === 'geral' ? 'Geral' : entry.replace(/[-_]/g, ' '),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf-8');
    }

    // Conta arquivos (excluindo json internos)
    const files = fs.readdirSync(nDir).filter((f) => {
      if (f === 'notebook.json' || f === 'chat_history.json') return false;
      return fs.statSync(path.join(nDir, f)).isFile();
    });

    // Conta mensagens
    let messageCount = 0;
    const chatPath = path.join(nDir, 'chat_history.json');
    if (fs.existsSync(chatPath)) {
      try {
        const msgs = JSON.parse(fs.readFileSync(chatPath, 'utf-8'));
        if (Array.isArray(msgs)) messageCount = msgs.length;
      } catch {}
    }

    list.push({
      ...meta,
      fileCount: files.length,
      messageCount,
    });
  }

  // Ordena com 'geral' no topo ou por data de atualização
  return list.sort((a, b) => {
    if (a.id === 'geral') return -1;
    if (b.id === 'geral') return 1;
    return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
  });
}

export function createNotebook(name: string): NotebookItem {
  ensureDefaultNotebook();
  const trimmed = name.trim() || 'Novo Caderno';
  const slug =
    trimmed
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'caderno';

  const id = `${slug}-${Date.now().toString(36)}`;
  const nDir = path.join(getNotebooksRootDir(), id);
  fs.mkdirSync(nDir, { recursive: true });

  const meta: NotebookMetadata = {
    id,
    name: trimmed,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  fs.writeFileSync(path.join(nDir, 'notebook.json'), JSON.stringify(meta, null, 2), 'utf-8');
  fs.writeFileSync(path.join(nDir, 'chat_history.json'), JSON.stringify([], null, 2), 'utf-8');

  return {
    ...meta,
    fileCount: 0,
    messageCount: 0,
  };
}

export function renameNotebook(id: string, newName: string): NotebookItem | null {
  const notebooksRoot = getNotebooksRootDir();
  const nDir = path.join(notebooksRoot, id);
  if (!fs.existsSync(nDir) || !fs.statSync(nDir).isDirectory()) return null;

  const metaPath = path.join(nDir, 'notebook.json');
  let meta: NotebookMetadata;
  if (fs.existsSync(metaPath)) {
    try {
      meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
    } catch {
      meta = { id, name: newName, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    }
  } else {
    meta = { id, name: newName, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
  }

  meta.name = newName.trim();
  meta.updatedAt = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf-8');

  const files = fs.readdirSync(nDir).filter((f) => f !== 'notebook.json' && f !== 'chat_history.json');
  let messageCount = 0;
  try {
    const msgs = JSON.parse(fs.readFileSync(path.join(nDir, 'chat_history.json'), 'utf-8'));
    if (Array.isArray(msgs)) messageCount = msgs.length;
  } catch {}

  return {
    ...meta,
    fileCount: files.length,
    messageCount,
  };
}

export function updateNotebookExpert(id: string, expertId: string): NotebookItem | null {
  const notebooksRoot = getNotebooksRootDir();
  const nDir = path.join(notebooksRoot, id);
  if (!fs.existsSync(nDir) || !fs.statSync(nDir).isDirectory()) return null;

  const metaPath = path.join(nDir, 'notebook.json');
  let meta: NotebookMetadata;
  if (fs.existsSync(metaPath)) {
    try {
      meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
    } catch {
      meta = { id, name: id, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    }
  } else {
    meta = { id, name: id, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
  }

  meta.expert = expertId.trim();
  meta.updatedAt = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf-8');

  const files = fs.readdirSync(nDir).filter((f) => f !== 'notebook.json' && f !== 'chat_history.json');
  let messageCount = 0;
  try {
    const msgs = JSON.parse(fs.readFileSync(path.join(nDir, 'chat_history.json'), 'utf-8'));
    if (Array.isArray(msgs)) messageCount = msgs.length;
  } catch {}

  return {
    ...meta,
    fileCount: files.length,
    messageCount,
  };
}

export function getNotebookMetadata(id: string): NotebookMetadata | null {
  const notebooksRoot = getNotebooksRootDir();
  const nDir = path.join(notebooksRoot, id);
  if (!fs.existsSync(nDir) || !fs.statSync(nDir).isDirectory()) return null;

  const metaPath = path.join(nDir, 'notebook.json');
  if (fs.existsSync(metaPath)) {
    try {
      return JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
    } catch {
      return null;
    }
  }
  return null;
}

export function deleteNotebook(id: string): boolean {
  if (id === 'geral') return false; // Impede deletar o caderno padrão
  const nDir = path.join(getNotebooksRootDir(), id);
  if (!fs.existsSync(nDir)) return false;
  try {
    fs.rmSync(nDir, { recursive: true, force: true });
    return true;
  } catch (err) {
    console.error(`Erro ao deletar caderno ${id}:`, err);
    return false;
  }
}

export function getNotebookDir(id: string): string {
  ensureDefaultNotebook();
  const safeId = id && id.trim() ? id.trim() : 'geral';
  const nDir = path.join(getNotebooksRootDir(), safeId);
  if (!fs.existsSync(nDir)) {
    fs.mkdirSync(nDir, { recursive: true });
    const meta: NotebookMetadata = {
      id: safeId,
      name: safeId === 'geral' ? 'Geral' : safeId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(path.join(nDir, 'notebook.json'), JSON.stringify(meta, null, 2), 'utf-8');
    fs.writeFileSync(path.join(nDir, 'chat_history.json'), JSON.stringify([], null, 2), 'utf-8');
  }
  return nDir;
}

export function listNotebookFiles(notebookId: string): WorkspaceFileItem[] {
  const dir = getNotebookDir(notebookId);
  const entries = fs.readdirSync(dir);
  const items: WorkspaceFileItem[] = [];

  for (const filename of entries) {
    if (filename === 'notebook.json' || filename === 'chat_history.json') continue;
    const fullPath = path.join(dir, filename);
    try {
      const stats = fs.statSync(fullPath);
      if (!stats.isFile()) continue;

      const ext = path.extname(filename).toLowerCase();
      // Remove prefixo de hash (ex: b4c5a7ci-nome.pdf) para exibir ao usuário
      const cleanName = filename.replace(/^[a-z0-9]{8}-/, '');

      items.push({
        id: filename,
        name: cleanName,
        extension: ext,
        size: stats.size,
        formattedSize: formatBytes(stats.size),
        type: getFileType(ext),
        path: fullPath,
        uploadedAt: stats.mtime.toISOString(),
      });
    } catch {}
  }

  // Ordena os arquivos mais recentes primeiro
  return items.sort((a, b) => new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime());
}

export function getNotebookChat(notebookId: string): ChatMessage[] {
  const dir = getNotebookDir(notebookId);
  const chatFile = path.join(dir, 'chat_history.json');
  if (!fs.existsSync(chatFile)) return [];
  try {
    const raw = fs.readFileSync(chatFile, 'utf-8');
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveNotebookChat(notebookId: string, messages: ChatMessage[]): void {
  const dir = getNotebookDir(notebookId);
  const chatFile = path.join(dir, 'chat_history.json');
  fs.writeFileSync(chatFile, JSON.stringify(messages, null, 2), 'utf-8');
}

export function appendNotebookChatMessage(notebookId: string, message: ChatMessage): void {
  const messages = getNotebookChat(notebookId);
  messages.push(message);
  saveNotebookChat(notebookId, messages);
}

export function clearNotebookChat(notebookId: string): void {
  saveNotebookChat(notebookId, []);
}
