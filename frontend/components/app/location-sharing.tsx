'use client';

import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useSessionContext } from '@livekit/components-react';

const LOCATION_TOPIC = 'jarvis.location';

export function LocationSharing() {
  const { isConnected, room } = useSessionContext();
  const requestedForSession = useRef(false);

  useEffect(() => {
    if (!isConnected) {
      requestedForSession.current = false;
      return;
    }

    if (requestedForSession.current) return;
    requestedForSession.current = true;

    if (!navigator.geolocation) {
      toast.error('Este navegador não oferece suporte à localização.');
      return;
    }

    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        try {
          await room.localParticipant.publishData(
            new TextEncoder().encode(
              JSON.stringify({
                type: 'jarvis_location',
                latitude: coords.latitude,
                longitude: coords.longitude,
              })
            ),
            { reliable: true, topic: LOCATION_TOPIC }
          );
          toast.success('Localização atual disponível nesta sessão.');
        } catch (error) {
          console.error('Falha ao compartilhar localização nesta sessão.', error);
          toast.error('Não foi possível compartilhar a localização com o Jarvis.');
        }
      },
      (error) => {
        if (error.code === error.PERMISSION_DENIED) {
          toast.error('Permita o acesso à localização no navegador para usar esse recurso.');
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          toast.error('O navegador não conseguiu determinar sua localização.');
        } else {
          toast.error('A localização demorou para responder. Tente reconectar o Jarvis.');
        }
      },
      { enableHighAccuracy: false, maximumAge: 0, timeout: 15_000 }
    );
  }, [isConnected, room]);

  return null;
}
