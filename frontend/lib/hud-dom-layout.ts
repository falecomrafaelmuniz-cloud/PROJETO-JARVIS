import { HUD_PANEL_STORAGE_KEYS, type HudPanelId, type PanelTransform } from './hud-layout-manager';

/**
 * Layout de painéis baseado nas MEDIDAS REAIS de cada janela no navegador.
 * Cada painel tem o atributo data-hud-panel="<id>" na raiz. Medimos a largura/altura
 * reais (offsetWidth/offsetHeight, sem o transform) e a posição nativa (offsetLeft/Top),
 * e então calculamos translate + scale para que nenhuma caixa encoste na outra.
 */

interface MeasuredPanel {
  id: HudPanelId;
  w: number;
  h: number;
  nativeCx: number;
  nativeCy: number;
}

const MARGIN = 10;
const GAP = 12;
const MIN_SCALE = 0.35;
// Faixa superior ocupada pelo menu "Painéis" e inferior pela barra de controles (microfone etc.)
const CENTER_TOP_RESERVED = 58;
const CENTER_BOTTOM_RESERVED = 100;

// Colunas da esquerda para a direita
const COLUMNS: { ids: HudPanelId[]; side: boolean }[] = [
  { ids: ['system', 'trader'], side: true },
  { ids: ['email', 'antigravity'], side: false },
  { ids: ['clock', 'files'], side: false },
  { ids: ['reminders', 'favorites'], side: false },
  { ids: ['activity', 'automations'], side: true },
];

function getAbsoluteOffset(el: HTMLElement) {
  let left = 0;
  let top = 0;
  let node: HTMLElement | null = el;
  while (node) {
    left += node.offsetLeft;
    top += node.offsetTop;
    node = node.offsetParent as HTMLElement | null;
  }
  return { left, top };
}

function measurePanel(id: HudPanelId): MeasuredPanel | null {
  if (typeof document === 'undefined') return null;
  const el = document.querySelector<HTMLElement>(`[data-hud-panel="${id}"]`);
  if (!el) return null;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  if (!w || !h) return null;
  const { left, top } = getAbsoluteOffset(el);
  return { id, w, h, nativeCx: left + w / 2, nativeCy: top + h / 2 };
}

function toTransform(p: MeasuredPanel, left: number, top: number, scale: number): PanelTransform {
  const targetCx = left + (p.w * scale) / 2;
  const targetCy = top + (p.h * scale) / 2;
  return {
    x: Math.round(targetCx - p.nativeCx),
    y: Math.round(targetCy - p.nativeCy),
    scale: Math.round(scale * 1000) / 1000,
  };
}

/** Organiza todos os painéis abertos em colunas, sem nenhuma sobreposição. */
export function organizeHudPanelsDom(openPanels: HudPanelId[]): Record<string, PanelTransform> | null {
  if (typeof window === 'undefined') return null;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const columns = COLUMNS.map((col) => ({
    side: col.side,
    panels: col.ids
      .filter((id) => openPanels.includes(id))
      .map(measurePanel)
      .filter((p): p is MeasuredPanel => p !== null),
  })).filter((col) => col.panels.length > 0);

  if (columns.length === 0) return null;

  // 1) Escala por coluna para caber na altura
  const colData = columns.map((col) => {
    const top = col.side ? MARGIN : CENTER_TOP_RESERVED;
    const bottom = col.side ? MARGIN : CENTER_BOTTOM_RESERVED;
    const availH = vh - top - bottom;
    const sumH = col.panels.reduce((acc, p) => acc + p.h, 0);
    const gaps = (col.panels.length - 1) * GAP;
    const scale = Math.min(1, (availH - gaps) / sumH);
    const maxW = Math.max(...col.panels.map((p) => p.w));
    return { ...col, top, bottom, availH, scale, width: maxW * scale };
  });

  // 2) Fator global para caber na largura
  const totalW = colData.reduce((acc, c) => acc + c.width, 0);
  const availW = vw - 2 * MARGIN - (colData.length - 1) * GAP;
  const widthFactor = Math.min(1, availW / totalW);
  colData.forEach((c) => {
    c.scale = Math.max(MIN_SCALE, c.scale * widthFactor);
    c.width = Math.max(...c.panels.map((p) => p.w)) * c.scale;
  });

  // 3) Distribui as colunas horizontalmente (laterais coladas nas bordas)
  const usedW = colData.reduce((acc, c) => acc + c.width, 0);
  const freeW = vw - 2 * MARGIN - usedW;
  const colGap = colData.length > 1 ? Math.max(GAP, freeW / (colData.length - 1)) : 0;

  const result: Record<string, PanelTransform> = {};
  let cursorX = colData.length === 1 ? (vw - colData[0].width) / 2 : MARGIN;

  colData.forEach((col) => {
    const s = col.scale;
    const heights = col.panels.map((p) => p.h * s);
    const sumH = heights.reduce((a, b) => a + b, 0);

    let cursorY: number;
    let rowGap: number;
    if (col.panels.length === 1) {
      // Painel único: centralizado verticalmente na área livre
      cursorY = col.top + Math.max(0, (col.availH - sumH) / 2);
      rowGap = 0;
    } else if (col.side) {
      // Colunas laterais: um no topo e outro na base
      cursorY = col.top;
      rowGap = Math.max(GAP, (col.availH - sumH) / (col.panels.length - 1));
    } else {
      // Coluna central: empilhados de cima para baixo
      cursorY = col.top;
      rowGap = GAP;
    }

    col.panels.forEach((p, idx) => {
      const pw = p.w * s;
      const left = cursorX + (col.width - pw) / 2;
      result[HUD_PANEL_STORAGE_KEYS[p.id]] = toTransform(p, left, cursorY, s);
      cursorY += heights[idx] + rowGap;
    });

    cursorX += col.width + colGap;
  });

  return result;
}

/** Move um painel específico para uma região da tela (esquerda, direita, topo, baixo, centro...). */
export function positionSinglePanelDom(
  id: HudPanelId,
  position: string,
  size: string
): Record<string, PanelTransform> | null {
  if (typeof window === 'undefined') return null;
  const p = measurePanel(id);
  if (!p) return null;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pos = position.toLowerCase();
  const sz = size.toLowerCase();

  let scale = 1;
  if (sz.includes('pequen') || sz.includes('compact') || sz.includes('mini')) scale = 0.7;
  else if (sz.includes('grand') || sz.includes('max') || sz.includes('ampli')) scale = 1.2;
  // Nunca deixa o painel maior que a tela
  scale = Math.max(
    MIN_SCALE,
    Math.min(scale, (vw - 2 * MARGIN) / p.w, (vh - 2 * MARGIN) / p.h)
  );

  const w = p.w * scale;
  const h = p.h * scale;
  const isLeft = pos.includes('esquerd') || pos.includes('left');
  const isRight = pos.includes('direit') || pos.includes('right');
  const isTop = pos.includes('cima') || pos.includes('topo') || pos.includes('top');
  const isBottom = pos.includes('baixo') || pos.includes('base') || pos.includes('bottom');

  const left = isLeft ? MARGIN : isRight ? vw - MARGIN - w : (vw - w) / 2;
  const centeredX = !isLeft && !isRight;
  const top = isTop
    ? centeredX
      ? CENTER_TOP_RESERVED
      : MARGIN
    : isBottom
      ? vh - (centeredX ? CENTER_BOTTOM_RESERVED : MARGIN) - h
      : (vh - h) / 2;

  return { [HUD_PANEL_STORAGE_KEYS[id]]: toTransform(p, left, Math.max(MARGIN, top), scale) };
}
