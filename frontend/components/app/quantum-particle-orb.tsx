'use client';

import React, { useEffect, useRef } from 'react';
import { useVoiceAssistant, useTrackVolume } from '@livekit/components-react';

interface QuantumParticleOrbProps {
  color?: number;
}

interface Particle3D {
  ox: number; // posição original na esfera
  oy: number;
  oz: number;
  x: number;
  y: number;
  z: number;
  baseRadius: number;
  pulsePhase: number;
  pulseSpeed: number;
}

/**
 * Rede de Partículas Quânticas Holográfica (Orb 2.0).
 * Esfera 3D de alta performance em Canvas 2D nativo, com filamentos quânticos interconectados
 * e reatividade instantânea ao volume da voz do Jarvis e do usuário.
 */
export const QuantumParticleOrb: React.FC<QuantumParticleOrbProps> = ({ color }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { audioTrack } = useVoiceAssistant();
  const volume = useTrackVolume(audioTrack);
  const volumeRef = useRef(volume);

  useEffect(() => {
    volumeRef.current = volume;
  }, [volume]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let animId: number;
    let isPaused = false;

    // Configuração de resolução do Canvas (HiDPI / Retina)
    const size = 500;
    const dpr = typeof window !== 'undefined' ? Math.min(2, window.devicePixelRatio || 1) : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const center = size / 2;
    const particleCount = 260; // Número ideal para beleza visual e 60 FPS cravados
    const baseSphereRadius = 135;
    const focalLength = 320;
    const maxLinkDistance = 42; // Distância máxima para conectar filamentos

    // Distribuição de Fibonacci na esfera (distribuição perfeitamente equilibrada)
    const particles: Particle3D[] = [];
    const phi = Math.PI * (3 - Math.sqrt(5)); // Golden angle

    for (let i = 0; i < particleCount; i++) {
      const y = 1 - (i / (particleCount - 1)) * 2; // de 1 a -1
      const radiusAtY = Math.sqrt(1 - y * y);
      const theta = phi * i;
      const x = Math.cos(theta) * radiusAtY;
      const z = Math.sin(theta) * radiusAtY;

      particles.push({
        ox: x,
        oy: y,
        oz: z,
        x: x * baseSphereRadius,
        y: y * baseSphereRadius,
        z: z * baseSphereRadius,
        baseRadius: Math.random() * 1.5 + 1.2,
        pulsePhase: Math.random() * Math.PI * 2,
        pulseSpeed: 0.04 + Math.random() * 0.04,
      });
    }

    let angleX = 0;
    let angleY = 0;
    let angleZ = 0;
    let smoothVolume = 0;

    const handleVisibility = () => {
      isPaused = document.hidden;
    };
    document.addEventListener('visibilitychange', handleVisibility);

    // Cor do Orb (Ciano Jarvis ou Rosa Neon Alice)
    const isPink = color === 0xff69b4;
    const mainHue = isPink ? 320 : 188; // HSL

    const render = () => {
      animId = requestAnimationFrame(render);
      if (isPaused) return;

      // Suavização do volume da voz
      smoothVolume += (volumeRef.current - smoothVolume) * 0.22;

      // Velocidade de rotação (acelera sutilmente quando fala)
      const rotSpeed = 0.007 + smoothVolume * 0.025;
      angleY += rotSpeed;
      angleX += rotSpeed * 0.65;
      angleZ += rotSpeed * 0.35;

      // Expansão do raio com a voz e pulsação suave
      const breath = Math.sin(angleY * 2) * 4;
      const currentRadius = baseSphereRadius + breath + smoothVolume * 65;

      ctx.clearRect(0, 0, size, size);

      // 1. Núcleo de Energia Quântica (Glow Central)
      const corePulse = 18 + smoothVolume * 40;
      const coreGradient = ctx.createRadialGradient(
        center,
        center,
        0,
        center,
        center,
        corePulse * 2.2
      );
      if (isPink) {
        coreGradient.addColorStop(0, `rgba(255, 105, 180, ${0.45 + smoothVolume * 0.5})`);
        coreGradient.addColorStop(0.4, `rgba(236, 72, 153, ${0.2 + smoothVolume * 0.3})`);
        coreGradient.addColorStop(1, 'rgba(236, 72, 153, 0)');
      } else {
        coreGradient.addColorStop(0, `rgba(34, 211, 238, ${0.5 + smoothVolume * 0.5})`);
        coreGradient.addColorStop(0.35, `rgba(6, 182, 212, ${0.22 + smoothVolume * 0.3})`);
        coreGradient.addColorStop(1, 'rgba(6, 182, 212, 0)');
      }
      ctx.fillStyle = coreGradient;
      ctx.beginPath();
      ctx.arc(center, center, corePulse * 2.2, 0, Math.PI * 2);
      ctx.fill();

      // 2. Rotação 3D e Projeção das Partículas
      const cosX = Math.cos(angleX);
      const sinX = Math.sin(angleX);
      const cosY = Math.cos(angleY);
      const sinY = Math.sin(angleY);
      const cosZ = Math.cos(angleZ);
      const sinZ = Math.sin(angleZ);

      interface ProjectedPoint {
        px: number;
        py: number;
        pz: number;
        scale: number;
        alpha: number;
        radius: number;
      }

      const projected: ProjectedPoint[] = [];

      for (let i = 0; i < particleCount; i++) {
        const p = particles[i];
        p.pulsePhase += p.pulseSpeed;

        // Variação quântica na distância do centro (vibração)
        const jitter = (Math.sin(p.pulsePhase) * 3 + (Math.random() - 0.5) * 4) * (1 + smoothVolume * 3);
        const r = currentRadius + jitter;

        let px = p.ox * r;
        let py = p.oy * r;
        let pz = p.oz * r;

        // Rotação Y
        const x1 = px * cosY - pz * sinY;
        const z1 = pz * cosY + px * sinY;

        // Rotação X
        const y2 = py * cosX - z1 * sinX;
        const z2 = z1 * cosX + py * sinX;

        // Rotação Z
        const x3 = x1 * cosZ - y2 * sinZ;
        const y3 = y2 * cosZ + x1 * sinZ;

        // Projeção em perspectiva 3D
        const perspective = focalLength / (focalLength + z2);
        const screenX = center + x3 * perspective;
        const screenY = center + y3 * perspective;

        // Alpha baseado em profundidade (-1 a +1)
        const depthNormalized = (z2 + baseSphereRadius) / (2 * baseSphereRadius);
        const alpha = Math.max(0.12, Math.min(1.0, 0.2 + depthNormalized * 0.75 + smoothVolume * 0.25));
        const finalRadius = Math.max(0.8, p.baseRadius * perspective * (1 + smoothVolume * 0.6));

        projected.push({
          px: screenX,
          py: screenY,
          pz: z2,
          scale: perspective,
          alpha,
          radius: finalRadius,
        });
      }

      // 3. Filamentos Quânticos (Linhas de Conexão entre Nós Próximos)
      ctx.lineWidth = 0.85;
      for (let i = 0; i < particleCount; i++) {
        const p1 = projected[i];
        // Conecta apenas partículas no hemisfério frontal ou médio para nitidez
        if (p1.pz < -baseSphereRadius * 0.4) continue;

        for (let j = i + 1; j < particleCount; j++) {
          const p2 = projected[j];
          if (p2.pz < -baseSphereRadius * 0.4) continue;

          const dx = p1.px - p2.px;
          const dy = p1.py - p2.py;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < maxLinkDistance * p1.scale) {
            const linkAlpha = (1 - dist / (maxLinkDistance * p1.scale)) * p1.alpha * p2.alpha * (0.45 + smoothVolume * 0.55);
            if (linkAlpha > 0.04) {
              ctx.strokeStyle = `hsla(${mainHue}, 90%, 65%, ${linkAlpha.toFixed(3)})`;
              ctx.beginPath();
              ctx.moveTo(p1.px, p1.py);
              ctx.lineTo(p2.px, p2.py);
              ctx.stroke();
            }
          }
        }
      }

      // 4. Desenho dos Nós de Partículas
      for (let i = 0; i < particleCount; i++) {
        const pt = projected[i];
        ctx.beginPath();
        ctx.arc(pt.px, pt.py, pt.radius, 0, Math.PI * 2);

        // Nós frontais brilham em branco-ciano com halo
        if (pt.pz > baseSphereRadius * 0.2) {
          ctx.fillStyle = `rgba(255, 255, 255, ${pt.alpha.toFixed(3)})`;
          ctx.shadowColor = isPink ? '#f472b6' : '#22d3ee';
          ctx.shadowBlur = 6 * pt.scale;
        } else {
          ctx.fillStyle = `hsla(${mainHue}, 85%, 60%, ${pt.alpha.toFixed(3)})`;
          ctx.shadowBlur = 0;
        }
        ctx.fill();
      }
      ctx.shadowBlur = 0; // reset
    };

    animId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [color]);

  return (
    <div className="relative flex size-[360px] items-center justify-center overflow-visible select-none">
      <canvas
        ref={canvasRef}
        style={{ width: 440, height: 440 }}
        className="pointer-events-none drop-shadow-[0_0_35px_rgba(34,211,238,0.35)]"
      />
    </div>
  );
};
