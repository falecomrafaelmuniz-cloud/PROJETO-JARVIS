'use client';

import { useEffect, useRef } from 'react';
import { RoomEvent } from 'livekit-client';
import { toast } from 'sonner';
import { useSessionContext } from '@livekit/components-react';
import {
  notifyDashboard,
  setDashboardCoordinates,
  setDashboardLocationStatus,
} from '@/lib/jarvis-dashboard';

const LOCATION_TOPIC = 'jarvis.location';

interface ReverseGeocodedLocation {
  country: string | null;
  state: string | null;
  city: string | null;
}

interface SessionLocation {
  latitude: number;
  longitude: number;
  country?: string;
  state?: string;
  city?: string;
}

async function lookupLocationNames(
  latitude: number,
  longitude: number
): Promise<ReverseGeocodedLocation> {
  const params = new URLSearchParams({
    lat: String(latitude),
    lon: String(longitude),
  });
  const response = await fetch(`/api/location/reverse?${params.toString()}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    throw new Error(`Localização reversa falhou com HTTP ${response.status}.`);
  }

  const result: unknown = await response.json();
  if (!result || typeof result !== 'object') {
    throw new Error('Resposta de localização reversa inválida.');
  }

  const location = result as Partial<ReverseGeocodedLocation>;
  for (const value of [location.country, location.state, location.city]) {
    if (value !== null && value !== undefined && typeof value !== 'string') {
      throw new Error('Resposta de localização reversa inválida.');
    }
  }
  return {
    country: location.country ?? null,
    state: location.state ?? null,
    city: location.city ?? null,
  };
}

export function LocationSharing() {
  const { isConnected, room } = useSessionContext();
  const requestedForSession = useRef(false);
  const currentLocation = useRef<SessionLocation | null>(null);

  useEffect(() => {
    const handleParticipantConnected = () => {
      const location = currentLocation.current;
      if (!location) return;

      room.localParticipant
        .publishData(
          new TextEncoder().encode(JSON.stringify({ type: 'jarvis_location', ...location })),
          { reliable: true, topic: LOCATION_TOPIC }
        )
        .catch((error: unknown) => {
          console.warn('Falha ao reenviar a localização ao agente conectado.', error);
          notifyDashboard(
            'Falha ao atualizar localização do agente',
            'error',
            'Tente reconectar o Jarvis.'
          );
        });
    };

    room.on(RoomEvent.ParticipantConnected, handleParticipantConnected);
    return () => {
      room.off(RoomEvent.ParticipantConnected, handleParticipantConnected);
    };
  }, [room]);

  useEffect(() => {
    if (!isConnected) {
      requestedForSession.current = false;
      currentLocation.current = null;
      setDashboardLocationStatus('offline');
      setDashboardCoordinates(null);
      return;
    }

    if (requestedForSession.current) return;
    requestedForSession.current = true;
    currentLocation.current = null;
    setDashboardLocationStatus('requesting');
    setDashboardCoordinates(null);

    if (!navigator.geolocation) {
      setDashboardLocationStatus('unavailable');
      notifyDashboard('Localização indisponível', 'warning', 'Este navegador não oferece suporte.');
      toast.error('Este navegador não oferece suporte à localização.');
      return;
    }

    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        const publishLocation = async () => {
          const location = currentLocation.current;
          if (!location) throw new Error('Localização ainda não está disponível na sessão.');
          await room.localParticipant.publishData(
            new TextEncoder().encode(
              JSON.stringify({
                type: 'jarvis_location',
                ...location,
              })
            ),
            { reliable: true, topic: LOCATION_TOPIC }
          );
        };

        try {
          currentLocation.current = {
            latitude: coords.latitude,
            longitude: coords.longitude,
          };
          await publishLocation();
          setDashboardLocationStatus('shared');
          setDashboardCoordinates({
            latitude: coords.latitude,
            longitude: coords.longitude,
          });
          notifyDashboard('Coordenadas compartilhadas', 'info', 'Identificando cidade e estado...');

          try {
            const names = await lookupLocationNames(coords.latitude, coords.longitude);
            currentLocation.current = {
              latitude: coords.latitude,
              longitude: coords.longitude,
              country: names.country ?? undefined,
              state: names.state ?? undefined,
              city: names.city ?? undefined,
            };
            await publishLocation();
            const enrichedCoordinates = {
              ...currentLocation.current,
            };
            setDashboardCoordinates(enrichedCoordinates);
            const place = [names.city, names.state, names.country].filter(Boolean).join(', ');
            if (!place) throw new Error('O serviço não retornou país, estado ou cidade.');
            notifyDashboard('Localização identificada', 'info', place);
            toast.success(`Localização atual: ${place}`);
          } catch (error) {
            console.warn('Não foi possível identificar o nome da localização.', error);
            try {
              const res = await fetch('/api/location/ip');
              if (res.ok) {
                const ipData = await res.json();
                if (ipData.city) {
                  currentLocation.current = {
                    ...currentLocation.current,
                    city: ipData.city,
                    state: ipData.state,
                    country: ipData.country,
                  };
                  await publishLocation();
                  setDashboardCoordinates(currentLocation.current);
                  const place = [ipData.city, ipData.state, ipData.country].filter(Boolean).join(', ');
                  notifyDashboard('Localização identificada', 'info', place);
                  toast.success(`Localização atual: ${place}`);
                  return;
                }
              }
            } catch {}
            notifyDashboard(
              'Cidade e estado indisponíveis',
              'warning',
              'As coordenadas continuam disponíveis nesta sessão.'
            );
            toast.warning('Compartilhei as coordenadas, mas não consegui identificar a cidade.');
          }
        } catch (error) {
          console.warn('Falha ao compartilhar localização nesta sessão.', error);
          setDashboardLocationStatus('unavailable');
          notifyDashboard(
            'Falha ao compartilhar localização',
            'error',
            'Tente reconectar o Jarvis.'
          );
          toast.error('Não foi possível compartilhar a localização com o Jarvis.');
        }
      },
      async (error) => {
        try {
          const res = await fetch('/api/location/ip');
          if (res.ok) {
            const ipData = await res.json();
            if (ipData.city) {
              currentLocation.current = {
                latitude: ipData.latitude,
                longitude: ipData.longitude,
                city: ipData.city,
                state: ipData.state,
                country: ipData.country,
              };
              await room.localParticipant.publishData(
                new TextEncoder().encode(
                  JSON.stringify({
                    type: 'jarvis_location',
                    ...currentLocation.current,
                  })
                ),
                { reliable: true, topic: LOCATION_TOPIC }
              );
              setDashboardLocationStatus('shared');
              setDashboardCoordinates(currentLocation.current);
              const place = [ipData.city, ipData.state, ipData.country].filter(Boolean).join(', ');
              notifyDashboard('Localização identificada via rede', 'info', place);
              toast.success(`Localização atual: ${place}`);
              return;
            }
          }
        } catch {}

        setDashboardLocationStatus('unavailable');
        if (error.code === error.PERMISSION_DENIED) {
          notifyDashboard(
            'Localização não autorizada',
            'warning',
            'Permita o acesso nas configurações do navegador.'
          );
          toast.error('Permita o acesso à localização no navegador para usar esse recurso.');
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          notifyDashboard(
            'Localização indisponível',
            'warning',
            'O navegador não conseguiu determinar sua posição.'
          );
          toast.error('O navegador não conseguiu determinar sua localização.');
        } else {
          notifyDashboard(
            'Localização demorou para responder',
            'warning',
            'Tente reconectar o Jarvis.'
          );
          toast.error('A localização demorou para responder. Tente reconectar o Jarvis.');
        }
      },
      { enableHighAccuracy: false, maximumAge: 0, timeout: 15_000 }
    );
  }, [isConnected, room]);

  return null;
}
