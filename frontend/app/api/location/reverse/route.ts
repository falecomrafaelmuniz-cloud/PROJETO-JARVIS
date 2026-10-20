import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

interface LocationNames {
  country: string | null;
  state: string | null;
  city: string | null;
}

interface CachedLocation {
  location: LocationNames;
  expiresAt: number;
}

const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const MAX_CACHE_ENTRIES = 128;
const locationCache = new Map<string, CachedLocation>();
const pendingLookups = new Map<string, Promise<LocationNames>>();
let requestQueue: Promise<void> = Promise.resolve();
let lastRequestAt = 0;

function getAddressName(address: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = address[key];
    if (typeof value === 'string' && value.trim()) return value.trim().slice(0, 120);
  }
  return null;
}

function fetchNominatim(url: URL): Promise<Response> {
  const request = requestQueue.then(async () => {
    const waitMs = Math.max(0, 1000 - (Date.now() - lastRequestAt));
    if (waitMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, waitMs));
    }

    lastRequestAt = Date.now();
    return fetch(url, {
      cache: 'no-store',
      headers: {
        'User-Agent':
          'Jarvis/1.0 (location panel; https://github.com/falecomrafaelmuniz-noyem/PROJETO-JARVIS)',
      },
      signal: AbortSignal.timeout(8000),
    });
  });

  requestQueue = request.then(
    () => undefined,
    () => undefined
  );
  return request;
}

async function lookupLocation(latitude: number, longitude: number): Promise<LocationNames> {
  const reverseGeocodingUrl =
    process.env.LOCATION_REVERSE_GEOCODING_URL ?? 'https://nominatim.openstreetmap.org/reverse';
  const url = new URL(reverseGeocodingUrl);
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('addressdetails', '1');
  url.searchParams.set('zoom', '10');
  url.searchParams.set('accept-language', 'pt-BR');
  url.searchParams.set('lat', String(latitude));
  url.searchParams.set('lon', String(longitude));

  const response = await fetchNominatim(url);
  if (!response.ok) {
    throw new Error(`Nominatim returned HTTP ${response.status}`);
  }

  const result: unknown = await response.json();
  if (!result || typeof result !== 'object' || !('address' in result)) {
    throw new Error('Nominatim returned no address details');
  }

  const address = result.address;
  if (!address || typeof address !== 'object') {
    throw new Error('Nominatim returned invalid address details');
  }

  const addressRecord = address as Record<string, unknown>;
  const location = {
    country: getAddressName(addressRecord, ['country']),
    state: getAddressName(addressRecord, ['state', 'province', 'region']),
    city: getAddressName(addressRecord, [
      'city',
      'town',
      'municipality',
      'village',
      'hamlet',
      'suburb',
    ]),
  };

  if (!location.country && !location.state && !location.city) {
    throw new Error('Nominatim returned no country, state, or city');
  }
  return location;
}

async function getLocation(latitude: number, longitude: number): Promise<LocationNames> {
  const key = `${latitude.toFixed(4)},${longitude.toFixed(4)}`;
  const cached = locationCache.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    locationCache.delete(key);
    locationCache.set(key, cached);
    return cached.location;
  }
  if (cached) locationCache.delete(key);

  const pending = pendingLookups.get(key);
  if (pending) return pending;

  const lookup = lookupLocation(latitude, longitude);
  pendingLookups.set(key, lookup);
  try {
    const location = await lookup;
    locationCache.set(key, { location, expiresAt: Date.now() + CACHE_TTL_MS });
    if (locationCache.size > MAX_CACHE_ENTRIES) {
      const oldestKey = locationCache.keys().next().value;
      if (oldestKey) locationCache.delete(oldestKey);
    }
    return location;
  } finally {
    pendingLookups.delete(key);
  }
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const latitudeValue = url.searchParams.get('lat');
  const longitudeValue = url.searchParams.get('lon');
  const latitude = Number(latitudeValue);
  const longitude = Number(longitudeValue);

  if (
    latitudeValue === null ||
    longitudeValue === null ||
    !Number.isFinite(latitude) ||
    latitude < -90 ||
    latitude > 90 ||
    !Number.isFinite(longitude) ||
    longitude < -180 ||
    longitude > 180
  ) {
    return NextResponse.json({ error: 'Invalid coordinates' }, { status: 400 });
  }

  try {
    const location = await getLocation(latitude, longitude);
    return NextResponse.json(location, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    console.error(
      'Reverse geocoding failed:',
      error instanceof Error ? error.message : 'Unknown error'
    );
    return NextResponse.json(
      { error: 'Location names are temporarily unavailable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } }
    );
  }
}
