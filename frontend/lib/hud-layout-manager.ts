export type HudPanelId =
  | 'system'
  | 'clock'
  | 'activity'
  | 'trader'
  | 'automations'
  | 'reminders'
  | 'favorites'
  | 'email'
  | 'files'
  | 'neural';

export interface PanelTransform {
  x: number;
  y: number;
  scale: number;
}

export const HUD_PANEL_STORAGE_KEYS: Record<HudPanelId, string> = {
  files: 'jarvis-pos-workspace-files-v3',
  system: 'jarvis-pos-system-dashboard-v2',
  reminders: 'jarvis-pos-reminders-panel-v2',
  trader: 'jarvis-pos-quant-panel-v3',
  automations: 'jarvis-pos-automations-panel-v2',
  activity: 'jarvis-pos-activity-panel-v2',
  email: 'jarvis-pos-email-composer-v2',
  favorites: 'jarvis-pos-favorites-panel-v1',
  clock: 'jarvis-pos-clock-widget-v2',
  neural: 'jarvis-pos-neural-v1',
};

export type LayoutMode = 'side_by_side' | 'grid' | 'unstack' | 'reset' | 'padrao' | 'auto';

/**
 * Calcula transformações (x, y, scale) para garantir separação total sem colisões
 * ou sobreposições entre os painéis abertos, adaptando-se à resolução da tela.
 */
export function calculateLayoutPositions(
  openPanels: HudPanelId[],
  mode: LayoutMode = 'auto',
  viewportWidth = 1920,
  viewportHeight = 1080
): Record<string, PanelTransform> {
  const result: Record<string, PanelTransform> = {};
  const vw = Math.max(1024, viewportWidth);
  const vh = Math.max(600, viewportHeight);

  if (mode === 'reset' || mode === 'padrao') {
    Object.values(HUD_PANEL_STORAGE_KEYS).forEach((key) => {
      result[key] = { x: 0, y: 0, scale: 1 };
    });
    return result;
  }

  // =========================================================================
  // CENÁRIO 1: ALTA DENSIDADE (5 a 9 PAINÉIS ABERTOS)
  // Layout Centro de Comando / Wall of Monitors: 5 Zonas Independentes sem colisão
  // =========================================================================
  if (openPanels.length >= 5) {
    const scaleSide = vw <= 1300 ? 0.48 : vw <= 1500 ? 0.52 : 0.56;
    const scaleFiles = vw <= 1300 ? 0.46 : vw <= 1500 ? 0.50 : 0.54;
    const scaleMid = vw <= 1300 ? 0.46 : vw <= 1500 ? 0.50 : 0.54;
    const scaleClock = vw <= 1300 ? 0.60 : vw <= 1500 ? 0.65 : 0.70;

    // Coluna 1 (Extrema Esquerda): Sistema (topo) e Trader (base)
    // sys: native W=352, H=440, L=12, T=12 -> cx=188, cy=232
    if (openPanels.includes('system')) {
      const targetSysCx = Math.round((352 * scaleSide) / 2 + 10);
      const targetSysCy = Math.round((440 * scaleSide) / 2 + 15);
      result[HUD_PANEL_STORAGE_KEYS.system] = {
        x: targetSysCx - 188,
        y: targetSysCy - 232,
        scale: scaleSide,
      };
    }
    // tra: native W=416, H=400, L=12, T=vh-96-400 -> cx=220, cy=vh-296
    if (openPanels.includes('trader')) {
      const targetTraCx = Math.round((416 * scaleSide) / 2 + 10);
      const targetTraCy = Math.round(vh - (400 * scaleSide) / 2 - 25);
      result[HUD_PANEL_STORAGE_KEYS.trader] = {
        x: targetTraCx - 220,
        y: targetTraCy - (vh - 296),
        scale: scaleSide,
      };
    }

    // Coluna 5 (Extrema Direita): Atividades (topo) e Automações (base)
    // act: native W=272, H=340, L=vw-12-272, T=12 -> cx=vw-148, cy=182
    if (openPanels.includes('activity')) {
      const targetActCx = Math.round(vw - (272 * scaleSide) / 2 - 10);
      const targetActCy = Math.round((340 * scaleSide) / 2 + 15);
      result[HUD_PANEL_STORAGE_KEYS.activity] = {
        x: targetActCx - (vw - 148),
        y: targetActCy - 182,
        scale: scaleSide,
      };
    }
    // aut: native W=384, H=420, L=vw-12-384, T=vh-96-420 -> cx=vw-204, cy=vh-306
    if (openPanels.includes('automations')) {
      const targetAutCx = Math.round(vw - (384 * scaleSide) / 2 - 10);
      const targetAutCy = Math.round(vh - (420 * scaleSide) / 2 - 25);
      result[HUD_PANEL_STORAGE_KEYS.automations] = {
        x: targetAutCx - (vw - 204),
        y: targetAutCy - (vh - 306),
        scale: scaleSide,
      };
    }

    // Coluna 3 (Centro): Relógio (topo) e Central de Arquivos (centro)
    // clk: native W=320, H=90, L=vw/2-160, T=56 -> cx=vw/2, cy=101
    if (openPanels.includes('clock')) {
      const targetClkCy = Math.round((90 * scaleClock) / 2 + 10);
      result[HUD_PANEL_STORAGE_KEYS.clock] = {
        x: 0,
        y: targetClkCy - 101,
        scale: scaleClock,
      };
    }
    // fil: native W=840, H=580, L=vw/2-420, T=48 -> cx=vw/2, cy=338
    if (openPanels.includes('files')) {
      const targetFilCy = Math.round(vh * 0.52);
      result[HUD_PANEL_STORAGE_KEYS.files] = {
        x: 0,
        y: targetFilCy - 338,
        scale: scaleFiles,
      };
    }

    // Coluna 2 (Meio-Esquerda): E-mail (entre coluna esquerda e Central de Arquivos)
    if (openPanels.includes('email')) {
      const leftColEnd = Math.max(
        Math.round(352 * scaleSide + 10),
        Math.round(416 * scaleSide + 10)
      );
      const filesLeftEdge = Math.round(vw / 2 - (840 * scaleFiles) / 2);
      const midLeftSpaceCx = Math.round((leftColEnd + filesLeftEdge) / 2);
      const targetEmlCy = Math.round(vh * 0.50);
      result[HUD_PANEL_STORAGE_KEYS.email] = {
        x: midLeftSpaceCx - 580,
        y: targetEmlCy - 266,
        scale: scaleMid,
      };
    }

    // Coluna 4 (Meio-Direita): Lembretes (entre Central de Arquivos e coluna direita)
    if (openPanels.includes('reminders')) {
      const filesRightEdge = Math.round(vw / 2 + (840 * scaleFiles) / 2);
      const rightColStart = Math.min(
        Math.round(vw - 272 * scaleSide - 10),
        Math.round(vw - 384 * scaleSide - 10)
      );
      const midRightSpaceCx = Math.round((filesRightEdge + rightColStart) / 2);
      const targetRemCy = Math.round(vh * 0.50);
      result[HUD_PANEL_STORAGE_KEYS.reminders] = {
        x: midRightSpaceCx - (vw - 452),
        y: targetRemCy - 266,
        scale: scaleMid,
      };
    }

    if (openPanels.includes('neural')) {
      result[HUD_PANEL_STORAGE_KEYS.neural] = { x: 0, y: 0, scale: 0.85 };
    }

    return result;
  }

  // =========================================================================
  // CENÁRIO 2: APENAS 1 PAINEL ABERTO
  // =========================================================================
  if (openPanels.length <= 1) {
    if (openPanels[0]) {
      result[HUD_PANEL_STORAGE_KEYS[openPanels[0]]] = { x: 0, y: 0, scale: 1 };
    }
    return result;
  }

  // =========================================================================
  // CENÁRIO 3: 2 OU 3 PAINÉIS SEM CENTRAL DE ARQUIVOS (Ex: Sistema + Relógio + Atividades)
  // =========================================================================
  if (!openPanels.includes('files') && openPanels.length <= 3) {
    // Posições nativas originais são perfeitas e não colidem
    openPanels.forEach((pid) => {
      result[HUD_PANEL_STORAGE_KEYS[pid]] = { x: 0, y: 0, scale: 1 };
    });
    return result;
  }

  // =========================================================================
  // CENÁRIO 4: CENTRAL DE ARQUIVOS + 1 OU 2 PAINÉIS (2 a 3 no total)
  // =========================================================================
  if (openPanels.includes('files') && openPanels.length <= 3) {
    const others = openPanels.filter((p) => p !== 'files');
    const scaleF = 0.74;
    const scaleO = 0.80;

    if (others.length === 1) {
      const otherId = others[0];
      const otherKey = HUD_PANEL_STORAGE_KEYS[otherId];
      if (otherId === 'system' || otherId === 'trader' || otherId === 'email') {
        result[otherKey] = { x: 0, y: 0, scale: scaleO };
        result[HUD_PANEL_STORAGE_KEYS.files] = {
          x: Math.round(vw * 0.16),
          y: 0,
          scale: scaleF,
        };
      } else {
        result[HUD_PANEL_STORAGE_KEYS.files] = {
          x: -Math.round(vw * 0.16),
          y: 0,
          scale: scaleF,
        };
        result[otherKey] = { x: 0, y: 0, scale: scaleO };
      }
      return result;
    }

    // Files + 2 outros painéis:
    result[HUD_PANEL_STORAGE_KEYS.files] = { x: 0, y: 0, scale: 0.68 };
    others.forEach((otherId) => {
      const otherKey = HUD_PANEL_STORAGE_KEYS[otherId];
      if (otherId === 'system') result[otherKey] = { x: -10, y: -20, scale: 0.74 };
      else if (otherId === 'trader') result[otherKey] = { x: -10, y: 20, scale: 0.74 };
      else if (otherId === 'activity') result[otherKey] = { x: 10, y: -20, scale: 0.74 };
      else if (otherId === 'automations') result[otherKey] = { x: 10, y: 20, scale: 0.74 };
      else if (otherId === 'clock') result[otherKey] = { x: 0, y: -40, scale: 0.75 };
      else if (otherId === 'reminders') result[otherKey] = { x: 30, y: 0, scale: 0.70 };
      else if (otherId === 'email') result[otherKey] = { x: -30, y: 0, scale: 0.70 };
      else result[otherKey] = { x: 0, y: 0, scale: 0.74 };
    });
    return result;
  }

  // =========================================================================
  // CENÁRIO 5: EXATAMENTE 4 PAINÉIS ABERTOS (4 Quadrantes Equilibrados)
  // =========================================================================
  const scale4 = 0.74;
  openPanels.forEach((pid) => {
    const key = HUD_PANEL_STORAGE_KEYS[pid];
    if (pid === 'system') result[key] = { x: 0, y: -Math.round(vh * 0.05), scale: scale4 };
    else if (pid === 'trader') result[key] = { x: 0, y: Math.round(vh * 0.05), scale: scale4 };
    else if (pid === 'activity') result[key] = { x: 0, y: -Math.round(vh * 0.05), scale: scale4 };
    else if (pid === 'automations') result[key] = { x: 0, y: Math.round(vh * 0.05), scale: scale4 };
    else if (pid === 'clock') result[key] = { x: 0, y: -Math.round(vh * 0.08), scale: 0.78 };
    else if (pid === 'files') result[key] = { x: 0, y: 0, scale: 0.64 };
    else if (pid === 'email') result[key] = { x: -Math.round(vw * 0.08), y: 0, scale: 0.68 };
    else if (pid === 'reminders') result[key] = { x: Math.round(vw * 0.08), y: 0, scale: 0.68 };
    else result[key] = { x: 0, y: 0, scale: scale4 };
  });

  return result;
}

/**
 * Calcula transformações quando o usuário pede por voz ou comando para mover um painel específico
 * (ex: "coloque a central de arquivos na direita", "jogue o trader no canto inferior esquerdo", etc.)
 */
export function calculateSinglePanelTransform(
  panelId: HudPanelId,
  position: string,
  size: string,
  viewportWidth = 1920,
  viewportHeight = 1080
): PanelTransform {
  const vw = Math.max(1024, viewportWidth);
  const vh = Math.max(600, viewportHeight);
  const pos = position.toLowerCase().trim();
  const sz = size.toLowerCase().trim();

  let scale = 1.0;
  if (sz.includes('pequeno') || sz.includes('compact') || sz.includes('mini')) scale = 0.72;
  else if (sz.includes('grande') || sz.includes('max') || sz.includes('ampliado')) scale = 1.18;

  let x = 0;
  let y = 0;

  if (panelId === 'files') {
    if (pos.includes('esquerd') || pos.includes('left')) x = -Math.round(vw * 0.22);
    else if (pos.includes('direit') || pos.includes('right')) x = Math.round(vw * 0.22);
    else if (pos.includes('centro') || pos.includes('center')) x = 0;
    else if (pos.includes('cima') || pos.includes('topo')) y = -Math.round(vh * 0.15);
    else if (pos.includes('baixo') || pos.includes('bottom')) y = Math.round(vh * 0.18);
  } else if (panelId === 'system' || panelId === 'trader') {
    if (pos.includes('direit') || pos.includes('right')) x = Math.round(vw * 0.55);
    else if (pos.includes('esquerd') || pos.includes('left')) x = 0;
    else if (pos.includes('centro') || pos.includes('center')) x = Math.round(vw * 0.28);
    else if (pos.includes('baixo') || pos.includes('bottom')) y = Math.round(vh * 0.35);
    else if (pos.includes('cima') || pos.includes('topo')) y = 0;
  } else if (panelId === 'reminders' || panelId === 'automations' || panelId === 'activity') {
    if (pos.includes('esquerd') || pos.includes('left')) x = -Math.round(vw * 0.55);
    else if (pos.includes('direit') || pos.includes('right')) x = 0;
    else if (pos.includes('centro') || pos.includes('center')) x = -Math.round(vw * 0.28);
    else if (pos.includes('baixo') || pos.includes('bottom')) y = Math.round(vh * 0.35);
    else if (pos.includes('cima') || pos.includes('topo')) y = 0;
  } else if (panelId === 'clock') {
    if (pos.includes('esquerd') || pos.includes('left')) x = -Math.round(vw * 0.35);
    else if (pos.includes('direit') || pos.includes('right')) x = Math.round(vw * 0.35);
    else if (pos.includes('centro') || pos.includes('center')) x = 0;
  }

  return { x, y, scale };
}
