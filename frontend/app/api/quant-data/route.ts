import { NextResponse } from 'next/server';

export const revalidate = 15; // Cache de 15 segundos no Next.js

interface AssetData {
  symbol: string;
  name: string;
  category: 'global' | 'currency' | 'commodity' | 'equity' | 'index';
  correlation: 'direct' | 'inverse' | 'heavyweight';
  price: number;
  changePercent: number;
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

let cachedResult: {
  timestamp: number;
  assets: AssetData[];
  biasScore: number;
  biasState: string;
  summary: string;
} | null = null;

async function fetchYahooSymbol(symbol: string): Promise<{ price: number; changePercent: number }> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
      symbol
    )}?interval=1d&range=2d`;
    const res = await fetch(url, {
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      },
      next: { revalidate: 15 },
    });

    if (!res.ok) throw new Error(`Yahoo status ${res.status}`);
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    const meta = result?.meta;
    const currentPrice = meta?.regularMarketPrice ?? 0;
    const previousClose = meta?.chartPreviousClose ?? meta?.previousClose ?? currentPrice;

    let changePercent = 0;
    if (previousClose && previousClose > 0) {
      changePercent = ((currentPrice - previousClose) / previousClose) * 100;
    }

    return {
      price: currentPrice,
      changePercent: Number(changePercent.toFixed(2)),
    };
  } catch {
    return { price: 0, changePercent: 0 };
  }
}

export async function GET() {
  const now = Date.now();
  // Se houver cache válido de menos de 15s, usa
  if (cachedResult && now - cachedResult.timestamp < 15_000) {
    return NextResponse.json(cachedResult);
  }

  const symbols = Object.keys(SYMBOL_CONFIG);
  const results = await Promise.allSettled(symbols.map((sym) => fetchYahooSymbol(sym)));

  const assets: AssetData[] = [];
  let weightedSum = 0;
  let totalAbsWeight = 0;

  symbols.forEach((symbol, idx) => {
    const cfg = SYMBOL_CONFIG[symbol];
    const r = results[idx];
    const safeChange =
      r.status === 'fulfilled' && Number.isFinite(r.value.changePercent)
        ? r.value.changePercent
        : 0;
    const safePrice =
      r.status === 'fulfilled' && Number.isFinite(r.value.price)
        ? r.value.price
        : 0;

    assets.push({
      symbol,
      name: cfg.name,
      category: cfg.category,
      correlation: cfg.correlation,
      currency: cfg.currency,
      weight: cfg.weight,
      price: safePrice,
      changePercent: safeChange,
    });

    // Se o ativo tem cotação/variação, computa no acelerador
    if (safeChange !== 0) {
      weightedSum += safeChange * cfg.weight;
      totalAbsWeight += Math.abs(cfg.weight);
    }
  });

  // Normaliza o acelerador entre -100% e +100%
  // 1.5% de movimento médio ponderado representa 100% no acelerador
  let rawScore = totalAbsWeight > 0 ? (weightedSum / (totalAbsWeight * 1.5)) * 100 : 0;
  if (!Number.isFinite(rawScore)) rawScore = 0;
  rawScore = Math.max(-100, Math.min(100, rawScore));
  const biasScore = Math.round(rawScore);

  let biasState = 'NEUTRO';
  if (biasScore >= 40) biasState = 'FORTE COMPRA';
  else if (biasScore >= 12) biasState = 'COMPRADOR';
  else if (biasScore <= -40) biasState = 'FORTE VENDA';
  else if (biasScore <= -12) biasState = 'VENDEDOR';

  // Resumo interpretativo
  const ewz = assets.find((a) => a.symbol === 'EWZ');
  const sp = assets.find((a) => a.symbol === 'ES=F');
  const usd = assets.find((a) => a.symbol === 'BRL=X');
  const petr = assets.find((a) => a.symbol === 'PETR4.SA');
  const vale = assets.find((a) => a.symbol === 'VALE3.SA');

  const drivers: string[] = [];
  if (ewz && ewz.changePercent !== 0) drivers.push(`EWZ ${ewz.changePercent > 0 ? '+' : ''}${ewz.changePercent}%`);
  if (sp && sp.changePercent !== 0) drivers.push(`S&P ${sp.changePercent > 0 ? '+' : ''}${sp.changePercent}%`);
  if (usd && usd.changePercent !== 0) drivers.push(`Dólar ${usd.changePercent > 0 ? '+' : ''}${usd.changePercent}%`);
  if (petr && petr.changePercent !== 0) drivers.push(`PETR4 ${petr.changePercent > 0 ? '+' : ''}${petr.changePercent}%`);
  if (vale && vale.changePercent !== 0) drivers.push(`VALE3 ${vale.changePercent > 0 ? '+' : ''}${vale.changePercent}%`);

  const summary = drivers.length > 0 ? drivers.join(' · ') : 'Aguardando abertura dos mercados globais';

  cachedResult = {
    timestamp: now,
    assets,
    biasScore,
    biasState,
    summary,
  };

  return NextResponse.json(cachedResult);
}
