export interface JarvisThemePreset {
  id: string;
  label: string;
  hex: string;
}

export const DEFAULT_JARVIS_THEME_HEX = '#1da3b9';
export const JARVIS_THEME_STORAGE_KEY = 'jarvis-theme-color-v1';

export const JARVIS_THEME_PRESETS: JarvisThemePreset[] = [
  { id: 'cyan', label: 'Ciano Jarvis', hex: '#1da3b9' },
  { id: 'blue', label: 'Azul Stark', hex: '#3b82f6' },
  { id: 'emerald', label: 'Esmeralda', hex: '#10b981' },
  { id: 'gold', label: 'Dourado', hex: '#eab308' },
  { id: 'orange', label: 'Âmbar', hex: '#f97316' },
  { id: 'red', label: 'Rubi', hex: '#ef4444' },
  { id: 'violet', label: 'Violeta', hex: '#8b5cf6' },
  { id: 'pink', label: 'Rosa', hex: '#ec4899' },
];

const SHADE_KEYS = [
  '50',
  '100',
  '200',
  '300',
  '400',
  '500',
  '600',
  '700',
  '800',
  '900',
  '950',
] as const;

export function normalizeHex(hex: string): string {
  const clean = (hex || '').trim();
  return /^#[0-9a-fA-F]{6}$/.test(clean) ? clean.toLowerCase() : DEFAULT_JARVIS_THEME_HEX;
}

export function hexToNumber(hex: string): number {
  const safe = normalizeHex(hex);
  return Number.parseInt(safe.slice(1), 16);
}

export function applyJarvisThemeHex(hex: string): void {
  if (typeof document === 'undefined') return;
  const rootStyle = document.documentElement.style;
  const safe = normalizeHex(hex);

  if (safe === DEFAULT_JARVIS_THEME_HEX) {
    for (const shade of SHADE_KEYS) {
      rootStyle.removeProperty(`--color-cyan-${shade}`);
    }
    return;
  }

  const shades: Record<(typeof SHADE_KEYS)[number], string> = {
    '50': `color-mix(in srgb, ${safe} 14%, #ffffff)`,
    '100': `color-mix(in srgb, ${safe} 26%, #ffffff)`,
    '200': `color-mix(in srgb, ${safe} 44%, #ffffff)`,
    '300': `color-mix(in srgb, ${safe} 66%, #ffffff)`,
    '400': `color-mix(in srgb, ${safe} 84%, #ffffff)`,
    '500': safe,
    '600': `color-mix(in srgb, ${safe} 82%, #000000)`,
    '700': `color-mix(in srgb, ${safe} 66%, #000000)`,
    '800': `color-mix(in srgb, ${safe} 48%, #000000)`,
    '900': `color-mix(in srgb, ${safe} 34%, #000000)`,
    '950': `color-mix(in srgb, ${safe} 22%, #020617)`,
  };

  for (const shade of SHADE_KEYS) {
    rootStyle.setProperty(`--color-cyan-${shade}`, shades[shade]);
  }
}

export function loadJarvisThemeHex(): string {
  try {
    const saved = localStorage.getItem(JARVIS_THEME_STORAGE_KEY);
    return saved ? normalizeHex(saved) : DEFAULT_JARVIS_THEME_HEX;
  } catch {
    return DEFAULT_JARVIS_THEME_HEX;
  }
}

export function saveJarvisThemeHex(hex: string): string {
  const safe = normalizeHex(hex);
  try {
    localStorage.setItem(JARVIS_THEME_STORAGE_KEY, safe);
  } catch {
    // ignore storage errors
  }
  applyJarvisThemeHex(safe);
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('jarvis:theme-change', { detail: { hex: safe } }));
  }
  return safe;
}
