import { NextResponse } from 'next/server';

export const revalidate = 15; // Cache de 15 segundos no Next.js

interface AssetData {
  symbol: string;
  name: string;
  category: 'global' | 'currency' | 'commodity' | 'equity' | 'index';
  correlation: 'direct' | 'inverse' | 'heavyweight';
  price: number;
  changePercent: number;
  dayHighPercent: number;
  dayLowPercent: number;
  momentum15m: number;
  rangePosition: number;
  sparkline: number[];
  currency: string;
  weight: number; // Peso no cálculo do acelerador de viés
}

const SYMBOL_CONFIG: Record<
  string,
  {
    name: string;
    category: AssetData['category'];
    correlation: AssetData['correlation'];
    currency: string;
    weight: number;
  }
> = {
  EWZ: {
    name: 'EWZ (ETF Brasil NYSE)',
    category: 'global',
    correlation: 'direct',
    currency: 'USD',
    weight: 0.22,
  },
  'ES=F': {
    name: 'S&P 500 Futuro',
    category: 'global',
    correlation: 'direct',
    currency: 'USD',
    weight: 0.18,
  },
  'NQ=F': {
    name: 'Nasdaq Futuro',
    category: 'global',
    correlation: 'direct',
    currency: 'USD',
    weight: 0.10,
  },
  'BRL=X': {
    name: 'Dólar Comercial (USD/BRL)',
    category: 'currency',
    correlation: 'inverse',
    currency: 'BRL',
    weight: -0.20,
  },
  'DX-Y.NYB': {
    name: 'DXY (Índice Dólar Global)',
    category: 'currency',
    correlation: 'inverse',
    currency: 'USD',
    weight: -0.08,
  },
  '^TNX': {
    name: 'Treasury 10Y (Juros EUA)',
    category: 'global',
    correlation: 'inverse',
    currency: '%',
    weight: -0.08,
  },
  'BZ=F': {
    name: 'Petróleo Brent',
    category: 'commodity',
    correlation: 'direct',
    currency: 'USD',
    weight: 0.08,
  },
  'PETR4.SA': {
    name: 'Petrobras PN (PETR4)',
    category: 'equity',
    correlation: 'heavyweight',
    currency: 'BRL',
    weight: 0.14,
  },
  'VALE3.SA': {
    name: 'Vale ON (VALE3)',
    category: 'equity',
    correlation: 'heavyweight',
    currency: 'BRL',
    weight: 0.14,
  },
  'ITUB4.SA': {
    name: 'Itaú Unibanco (ITUB4)',
    category: 'equity',
    correlation: 'heavyweight',
    currency: 'BRL',
    weight: 0.10,
  },
  '^BVSP': {
    name: 'Ibovespa / Mini Índice',
    category: 'index',
    correlation: 'direct',
    currency: 'pts',
    weight: 0.10,
  },
};

interface CachedQuantPayload {
  timestamp: number;
  assets: AssetData[];
  biasScore: number;
  biasDelta15m: number;
  biasState: string;
  summary: string;
}

let cachedResult: CachedQuantPayload | null = null;

const YAHOO_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
};

function pctVsBase(value: number, base: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(base) || base <= 0) return 0;
  return Number((((value - base) / base) * 100).toFixed(2));
}

function sampleSeries(values: number[], targetCount = 24): number[] {
  if (values.length <= targetCount) return values;
  const sampled: number[] = [];
  const step = (values.length - 1) / (targetCount - 1);
  for (let i = 0; i < targetCount; i++) {
    const idx = Math.min(values.length - 1, Math.round(i * step));
    sampled.push(values[idx]);
  }
  return sampled;
}

async function fetchPreviousDailyClose(symbol: string, currentPrice: number): Promise<number> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
      symbol
    )}?interval=1d&range=5d`;
    const res = await fetch(url, { headers: YAHOO_HEADERS, cache: 'no-store' });
    if (!res.ok) return 0;
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    const timestamps: number[] = Array.isArray(result?.timestamp) ? result.timestamp : [];
    const rawCloses: unknown[] = result?.indicators?.quote?.[0]?.close ?? [];
    const pairs: Array<{ ts: number; close: number }> = [];
    for (let i = 0; i < rawCloses.length; i++) {
      const c = rawCloses[i];
      if (typeof c === 'number' && Number.isFinite(c) && c > 0) {
        pairs.push({ ts: timestamps[i] ?? 0, close: c });
      }
    }
    if (pairs.length >= 2) {
      const last = pairs[pairs.length - 1];
      const todayUtc = new Date().toISOString().slice(0, 10);
      const lastUtc = last.ts ? new Date(last.ts * 1000).toISOString().slice(0, 10) : '';
      const samePrice = Math.abs(last.close - currentPrice) / Math.max(currentPrice, 1e-6) < 0.002;
      if (samePrice || lastUtc === todayUtc) {
        return pairs[pairs.length - 2].close;
      }
      return last.close;
    }
    return pairs[0]?.close ?? 0;
  } catch {
    return 0;
  }
}

interface YahooSymbolStats {
  price: number;
  changePercent: number;
  dayHighPercent: number;
  dayLowPercent: number;
  momentum15m: number;
  change15mAgo: number;
  rangePosition: number;
  sparkline: number[];
}

async function fetchYahooSymbol(symbol: string): Promise<YahooSymbolStats> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
      symbol
    )}?interval=5m&range=1d`;
    const res = await fetch(url, {
      headers: YAHOO_HEADERS,
      cache: 'no-store',
    });

    if (!res.ok) throw new Error(`Yahoo status ${res.status}`);
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    const meta = result?.meta;
    const quote = result?.indicators?.quote?.[0] ?? {};

    const rawCloses: unknown[] = Array.isArray(quote.close) ? quote.close : [];
    const rawHighs: unknown[] = Array.isArray(quote.high) ? quote.high : [];
    const rawLows: unknown[] = Array.isArray(quote.low) ? quote.low : [];

    const validCloses = rawCloses.filter(
      (v): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0
    );
    const validHighs = rawHighs.filter(
      (v): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0
    );
    const validLows = rawLows.filter(
      (v): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0
    );

    const currentPrice: number =
      typeof meta?.regularMarketPrice === 'number' && meta.regularMarketPrice > 0
        ? meta.regularMarketPrice
        : validCloses[validCloses.length - 1] ?? 0;

    let previousClose: number = meta?.chartPreviousClose ?? meta?.previousClose ?? 0;
    // Em câmbio (ex.: BRL=X), chartPreviousClose no range=1d costuma vir igual ao preço atual
    if (!previousClose || previousClose === currentPrice) {
      const dailyPrev = await fetchPreviousDailyClose(symbol, currentPrice);
      if (dailyPrev > 0) {
        previousClose = dailyPrev;
      } else {
        previousClose = currentPrice;
      }
    }

    const changePercent = pctVsBase(currentPrice, previousClose);

    const metaHigh =
      typeof meta?.regularMarketDayHigh === 'number' && meta.regularMarketDayHigh > 0
        ? meta.regularMarketDayHigh
        : 0;
    const metaLow =
      typeof meta?.regularMarketDayLow === 'number' && meta.regularMarketDayLow > 0
        ? meta.regularMarketDayLow
        : 0;

    const allHighCandidates = [...validHighs, ...validCloses, currentPrice];
    if (metaHigh > 0) allHighCandidates.push(metaHigh);
    const allLowCandidates = [...validLows, ...validCloses, currentPrice];
    if (metaLow > 0) allLowCandidates.push(metaLow);

    const dayHigh = allHighCandidates.length > 0 ? Math.max(...allHighCandidates) : currentPrice;
    const dayLow = allLowCandidates.length > 0 ? Math.min(...allLowCandidates) : currentPrice;

    const dayHighPercent = pctVsBase(dayHigh, previousClose);
    const dayLowPercent = pctVsBase(dayLow, previousClose);

    const span = dayHigh - dayLow;
    const rangePosition =
      span > 0
        ? Math.max(0, Math.min(100, Math.round(((currentPrice - dayLow) / span) * 100)))
        : 50;

    // Momento dos últimos ~15 minutos (3 candles de 5m atrás)
    const refIdx15m = Math.max(0, validCloses.length - 4);
    const price15mAgo = validCloses.length > 0 ? validCloses[refIdx15m] : currentPrice;
    const change15mAgo = pctVsBase(price15mAgo, previousClose);
    const momentum15m = Number((changePercent - change15mAgo).toFixed(2));

    const pctSeries = validCloses.map((c) => pctVsBase(c, previousClose));
    if (
      pctSeries.length === 0 ||
      Math.abs(pctSeries[pctSeries.length - 1] - changePercent) > 0.01
    ) {
      pctSeries.push(changePercent);
    }
    const sparkline = sampleSeries(pctSeries, 24);

    return {
      price: currentPrice,
      changePercent,
      dayHighPercent,
      dayLowPercent,
      momentum15m,
      change15mAgo,
      rangePosition,
      sparkline,
    };
  } catch {
    return {
      price: 0,
      changePercent: 0,
      dayHighPercent: 0,
      dayLowPercent: 0,
      momentum15m: 0,
      change15mAgo: 0,
      rangePosition: 50,
      sparkline: [],
    };
  }
}

function computeBiasScore(pairs: Array<{ change: number; weight: number }>): number {
  let weightedSum = 0;
  let totalAbsWeight = 0;
  for (const item of pairs) {
    if (item.change !== 0) {
      weightedSum += item.change * item.weight;
      totalAbsWeight += Math.abs(item.weight);
    }
  }
  let rawScore = totalAbsWeight > 0 ? (weightedSum / (totalAbsWeight * 1.5)) * 100 : 0;
  if (!Number.isFinite(rawScore)) rawScore = 0;
  return Math.round(Math.max(-100, Math.min(100, rawScore)));
}

export async function GET() {
  const now = Date.now();
  if (cachedResult && now - cachedResult.timestamp < 15_000) {
    return NextResponse.json(cachedResult);
  }

  const symbols = Object.keys(SYMBOL_CONFIG);
  const results = await Promise.allSettled(symbols.map((sym) => fetchYahooSymbol(sym)));

  const assets: AssetData[] = [];
  const currentPairs: Array<{ change: number; weight: number }> = [];
  const pairs15mAgo: Array<{ change: number; weight: number }> = [];

  symbols.forEach((symbol, idx) => {
    const cfg = SYMBOL_CONFIG[symbol];
    const r = results[idx];
    const val =
      r.status === 'fulfilled'
        ? r.value
        : {
            price: 0,
            changePercent: 0,
            dayHighPercent: 0,
            dayLowPercent: 0,
            momentum15m: 0,
            change15mAgo: 0,
            rangePosition: 50,
            sparkline: [],
          };

    const safeChange = Number.isFinite(val.changePercent) ? val.changePercent : 0;
    const safePrice = Number.isFinite(val.price) ? val.price : 0;
    const safe15mAgo = Number.isFinite(val.change15mAgo) ? val.change15mAgo : safeChange;

    assets.push({
      symbol,
      name: cfg.name,
      category: cfg.category,
      correlation: cfg.correlation,
      currency: cfg.currency,
      weight: cfg.weight,
      price: safePrice,
      changePercent: safeChange,
      dayHighPercent: val.dayHighPercent,
      dayLowPercent: val.dayLowPercent,
      momentum15m: val.momentum15m,
      rangePosition: val.rangePosition,
      sparkline: val.sparkline,
    });

    currentPairs.push({ change: safeChange, weight: cfg.weight });
    pairs15mAgo.push({ change: safe15mAgo, weight: cfg.weight });
  });

  const biasScore = computeBiasScore(currentPairs);
  const biasScore15mAgo = computeBiasScore(pairs15mAgo);
  const biasDelta15m = biasScore - biasScore15mAgo;

  let biasState = 'NEUTRO';
  if (biasScore >= 40) biasState = 'FORTE COMPRA';
  else if (biasScore >= 12) biasState = 'COMPRADOR';
  else if (biasScore <= -40) biasState = 'FORTE VENDA';
  else if (biasScore <= -12) biasState = 'VENDEDOR';

  const ewz = assets.find((a) => a.symbol === 'EWZ');
  const sp = assets.find((a) => a.symbol === 'ES=F');
  const usd = assets.find((a) => a.symbol === 'BRL=X');
  const petr = assets.find((a) => a.symbol === 'PETR4.SA');
  const vale = assets.find((a) => a.symbol === 'VALE3.SA');

  const drivers: string[] = [];
  if (ewz && ewz.changePercent !== 0)
    drivers.push(`EWZ ${ewz.changePercent > 0 ? '+' : ''}${ewz.changePercent}%`);
  if (sp && sp.changePercent !== 0)
    drivers.push(`S&P ${sp.changePercent > 0 ? '+' : ''}${sp.changePercent}%`);
  if (usd && usd.changePercent !== 0)
    drivers.push(`Dólar ${usd.changePercent > 0 ? '+' : ''}${usd.changePercent}%`);
  if (petr && petr.changePercent !== 0)
    drivers.push(`PETR4 ${petr.changePercent > 0 ? '+' : ''}${petr.changePercent}%`);
  if (vale && vale.changePercent !== 0)
    drivers.push(`VALE3 ${vale.changePercent > 0 ? '+' : ''}${vale.changePercent}%`);

  const summary =
    drivers.length > 0 ? drivers.join(' · ') : 'Aguardando abertura dos mercados globais';

  cachedResult = {
    timestamp: now,
    assets,
    biasScore,
    biasDelta15m,
    biasState,
    summary,
  };

  return NextResponse.json(cachedResult);
}
