import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import { exec } from 'child_process';

export const dynamic = 'force-dynamic';

export interface FavoriteItem {
  id: string;
  nome: string;
  url: string;
  tipo?: 'site' | 'app' | 'pasta' | 'arquivo' | 'sistema' | 'script';
  categoria?: string;
  criado_em?: string;
  atualizado_em?: string;
  ordem?: number;
}

function getFavoritesFilePath(): string {
  return path.resolve(process.cwd(), '..', 'backend', 'favoritos_jarvis.json');
}

function readFavorites(): FavoriteItem[] {
  const filePath = getFavoritesFilePath();
  if (!fs.existsSync(filePath)) {
    try {
      fs.writeFileSync(filePath, '[]', 'utf-8');
    } catch {
      // ignore
    }
    return [];
  }
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Higieniza: nomes sempre em MAIÚSCULAS e limpa atalhos de apps que foram salvos com https://
    return parsed.map((item: FavoriteItem) => {
      const nomeUpper = String(item.nome || '').toUpperCase();
      let urlSanitized = String(item.url || '');
      if (item.tipo === 'app' && urlSanitized.toLowerCase().startsWith('http')) {
        urlSanitized = urlSanitized.replace(/^https?:\/\//i, '').replace(/\/$/, '');
      }
      return {
        ...item,
        nome: nomeUpper,
        url: urlSanitized,
      };
    });
  } catch {
    return [];
  }
}

function saveFavorites(items: FavoriteItem[]): boolean {
  const filePath = getFavoritesFilePath();
  try {
    fs.writeFileSync(filePath, JSON.stringify(items, null, 2), 'utf-8');
    return true;
  } catch {
    return false;
  }
}

function normalizeUrl(url: string): string {
  let u = (url || '').trim();
  if (!u) return '';
  if (!u.startsWith('http://') && !u.startsWith('https://')) {
    u = `https://${u}`;
  }
  return u;
}

export async function GET() {
  const favorites = readFavorites();
  return NextResponse.json({ status: 'ok', favorites });
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const action = String(body.action || 'add').toLowerCase();
    let favorites = readFavorites();

    if (action === 'add') {
      const rawTarget = String(body.url || body.item || '').trim();
      if (!rawTarget) {
        return NextResponse.json(
          { status: 'error', message: 'Destino ou nome inválido/vazio.' },
          { status: 400 }
        );
      }

      const explicitTipo = body.tipo ? String(body.tipo).toLowerCase() : '';
      const rawTargetLower = rawTarget.toLowerCase();

      // Extensões para classificação
      const docExts = ['.pdf', '.xlsx', '.xls', '.docx', '.doc', '.pptx', '.txt', '.csv', '.jpg', '.png', '.mp4', '.zip'];
      const scriptExts = ['.bat', '.cmd', '.ps1', '.py', '.vbs'];
      const sistemaKeywords = ['control', 'ms-settings:', 'taskmgr', 'cleanmgr', 'cmd', 'powershell'];

      let tipo: 'site' | 'app' | 'pasta' | 'arquivo' | 'sistema' | 'script' = 'app';
      let url = rawTarget;
      let defaultCat = 'Aplicativos';

      if (explicitTipo && ['site', 'app', 'pasta', 'arquivo', 'sistema', 'script'].includes(explicitTipo)) {
        tipo = explicitTipo as any;
      } else if (
        rawTarget.startsWith('http://') ||
        rawTarget.startsWith('https://') ||
        rawTarget.startsWith('www.') ||
        (rawTarget.includes('.') && !rawTarget.includes(' ') && !rawTargetLower.endsWith('.exe'))
      ) {
        tipo = 'site';
      } else if (rawTargetLower.startsWith('pasta ') || rawTarget.endsWith('\\') || rawTarget.endsWith('/')) {
        tipo = 'pasta';
      } else if (scriptExts.some((ext) => rawTargetLower.endsWith(ext))) {
        tipo = 'script';
      } else if (docExts.some((ext) => rawTargetLower.endsWith(ext))) {
        tipo = 'arquivo';
      } else if (sistemaKeywords.some((k) => rawTargetLower === k || rawTargetLower.includes(k))) {
        tipo = 'sistema';
      }

      if (tipo === 'site') {
        url = normalizeUrl(rawTarget);
        defaultCat = 'Sites';
      } else if (tipo === 'pasta') {
        defaultCat = 'Pastas';
      } else if (tipo === 'arquivo') {
        defaultCat = 'Arquivos';
      } else if (tipo === 'sistema') {
        defaultCat = 'Sistema';
      } else if (tipo === 'script') {
        defaultCat = 'Scripts';
      } else {
        url = rawTarget.replace(/^https?:\/\//i, '').replace(/\/$/, '');
        defaultCat = 'Aplicativos';
      }

      let nome = String(body.nome || '').trim();
      if (!nome) {
        if (tipo === 'site') {
          try {
            const parsed = new URL(url);
            const domain = parsed.hostname.replace(/^www\./, '');
            nome = domain.charAt(0).toUpperCase() + domain.slice(1);
          } catch {
            nome = url;
          }
        } else {
          nome = rawTarget;
        }
      }
      nome = nome.toUpperCase();

      const categoria = String(body.categoria || defaultCat).trim() || defaultCat;
      const existing = favorites.find(
        (f) =>
          f.url.replace(/\/$/, '').toLowerCase() === url.replace(/\/$/, '').toLowerCase() ||
          (!ehSite && f.nome.toLowerCase() === nome.toLowerCase())
      );

      const nowStr = new Date().toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });

      if (existing) {
        existing.nome = nome;
        existing.tipo = tipo;
        existing.url = url;
        existing.categoria = categoria;
        existing.atualizado_em = nowStr;
      } else {
        const newItem: FavoriteItem = {
          id: `fav-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
          nome,
          url,
          tipo,
          categoria,
          criado_em: nowStr,
          ordem: favorites.length,
        };
        favorites.push(newItem);
      }

      saveFavorites(favorites);
      return NextResponse.json({ status: 'ok', action: 'add', favorites });
    }

    if (action === 'update' || action === 'rename') {
      const id = String(body.id || '').trim();
      const item = favorites.find((f) => f.id === id);
      if (!item) {
        return NextResponse.json(
          { status: 'error', message: 'Favorito não encontrado.' },
          { status: 404 }
        );
      }

      if (body.nome !== undefined) {
        const n = String(body.nome).trim();
        if (n) item.nome = n.toUpperCase();
      }

      // Regra de Proteção: Aplicativos instalados NUNCA têm seu comando/caminho alterado
      if (item.tipo === 'app') {
        if (item.url.toLowerCase().startsWith('http')) {
          item.url = item.url.replace(/^https?:\/\//i, '').replace(/\/$/, '');
        }
      } else {
        if (body.url !== undefined && String(body.url).trim()) {
          const u = normalizeUrl(body.url);
          if (u) item.url = u;
        }
      }

      if (body.categoria !== undefined) {
        item.categoria = String(body.categoria).trim() || (item.tipo === 'app' ? 'Aplicativos' : 'Geral');
      }
      item.atualizado_em = new Date().toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });

      saveFavorites(favorites);
      return NextResponse.json({ status: 'ok', action: 'update', favorites });
    }

    if (action === 'delete') {
      const id = String(body.id || '').trim();
      const prevLen = favorites.length;
      favorites = favorites.filter((f) => f.id !== id);
      if (favorites.length === prevLen) {
        return NextResponse.json(
          { status: 'error', message: 'Favorito não encontrado para remoção.' },
          { status: 404 }
        );
      }
      saveFavorites(favorites);
      return NextResponse.json({ status: 'ok', action: 'delete', favorites });
    }

    if (action === 'reorder' && Array.isArray(body.ids)) {
      const orderMap = new Map<string, number>();
      body.ids.forEach((id: string, idx: number) => {
        orderMap.set(String(id), idx);
      });
      favorites.sort((a, b) => {
        const ordA = orderMap.has(a.id) ? (orderMap.get(a.id) as number) : 999;
        const ordB = orderMap.has(b.id) ? (orderMap.get(b.id) as number) : 999;
        return ordA - ordB;
      });
      favorites.forEach((f, idx) => {
        f.ordem = idx;
      });
      saveFavorites(favorites);
      return NextResponse.json({ status: 'ok', action: 'reorder', favorites });
    }

    if (action === 'open') {
      const target = String(body.url || body.item || '').trim();
      const tipo = String(body.tipo || '').toLowerCase();
      if (target) {
        if (tipo === 'pasta') {
          exec(`explorer.exe "${target}"`, { windowsHide: true }, () => {});
        } else if (tipo === 'arquivo' || tipo === 'script') {
          exec(`start "" "${target}"`, { windowsHide: true }, () => {});
        } else if (tipo === 'sistema') {
          exec(`start "" ${target}`, { windowsHide: true }, () => {});
        } else if (tipo === 'site' || target.startsWith('http://') || target.startsWith('https://')) {
          exec(`start "" "${target}"`, { windowsHide: true }, () => {});
        } else {
          exec(`start "" "${target}"`, { windowsHide: true }, () => {});
        }
      }
      return NextResponse.json({ status: 'ok', action: 'open', target, tipo });
    }

    return NextResponse.json(
      { status: 'error', message: `Ação desconhecida: ${action}` },
      { status: 400 }
    );
  } catch (err) {
    return NextResponse.json(
      { status: 'error', message: err instanceof Error ? err.message : 'Erro interno.' },
      { status: 500 }
    );
  }
}
