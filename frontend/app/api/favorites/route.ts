import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import { exec } from 'child_process';

export const dynamic = 'force-dynamic';

export interface FavoriteItem {
  id: string;
  nome: string;
  url: string;
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
    return Array.isArray(parsed) ? parsed : [];
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
      const url = normalizeUrl(body.url);
      if (!url) {
        return NextResponse.json(
          { status: 'error', message: 'URL inválida ou vazia.' },
          { status: 400 }
        );
      }

      let nome = String(body.nome || '').trim();
      if (!nome) {
        try {
          const parsed = new URL(url);
          const domain = parsed.hostname.replace(/^www\./, '');
          nome = domain.charAt(0).toUpperCase() + domain.slice(1);
        } catch {
          nome = url;
        }
      }

      const categoria = String(body.categoria || 'Geral').trim() || 'Geral';
      const existing = favorites.find(
        (f) => f.url.replace(/\/$/, '').toLowerCase() === url.replace(/\/$/, '').toLowerCase()
      );

      const nowStr = new Date().toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });

      if (existing) {
        existing.nome = nome;
        existing.categoria = categoria;
        existing.atualizado_em = nowStr;
      } else {
        const newItem: FavoriteItem = {
          id: `fav-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
          nome,
          url,
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

      if (body.nome !== undefined) item.nome = String(body.nome).trim() || item.nome;
      if (body.url !== undefined) {
        const u = normalizeUrl(body.url);
        if (u) item.url = u;
      }
      if (body.categoria !== undefined) {
        item.categoria = String(body.categoria).trim() || 'Geral';
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
      const url = normalizeUrl(body.url);
      if (url) {
        // Abre no navegador padrão do Windows em segundo plano
        exec(`start "" "${url}"`, { windowsHide: true }, () => {});
      }
      return NextResponse.json({ status: 'ok', action: 'open', url });
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
