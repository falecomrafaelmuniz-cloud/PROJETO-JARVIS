import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  try {
    const res = await fetch('http://ip-api.com/json/?fields=status,country,regionName,city,lat,lon', {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      return NextResponse.json({ error: 'IP lookup failed' }, { status: 502 });
    }
    const data = await res.json();
    if (data.status !== 'success') {
      return NextResponse.json({ error: 'IP lookup error' }, { status: 502 });
    }
    return NextResponse.json({
      latitude: data.lat,
      longitude: data.lon,
      city: data.city || 'Bauru',
      state: data.regionName || 'São Paulo',
      country: data.country === 'Brazil' ? 'Brasil' : data.country,
    });
  } catch (error) {
    return NextResponse.json({ error: 'Timeout' }, { status: 502 });
  }
}

