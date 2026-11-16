'use client';

import React, { useEffect, useRef } from 'react';
import { useVoiceAssistant, useTrackVolume, useLocalParticipant } from '@livekit/components-react';

interface QuantumParticleOrbProps {
  color?: number;
}

interface Particle {
  // Posição no espaço tridimensional
  x: number;
  y: number;
  z: number;
  baseRadius: number;
  // Parâmetros orbitais
  orbitRing: number; // qual anel/faixa pertence
  orbitAngle: number;
  orbitSpeed: number;
  radialDistance: number;
  verticalOffset: number;
  // Brilho e cintilação
  baseAlpha: number;
  shimmerPhase: number;
  shimmerSpeed: number;
  isCluster: boolean; // se faz parte de um aglomerado denso de alta energia
}

// Sintetizador procedural de som sci-fi suave via Web Audio API (sem arquivos externos)
function playHolographicSearchChirp() {
  if (typeof window === 'undefined') return;
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;

    // Oscilador principal com sweep de frequência (pulso quântico holográfico)
    const osc = ctx.createOscillator();
    const oscHarmonic = ctx.createOscillator();
    const gain = ctx.createGain();
    const filter = ctx.createBiquadFilter();

    osc.type = 'sine';
    oscHarmonic.type = 'triangle';

    // Frequência suave: sobe rápido e ressoa como um computador futurista
    osc.frequency.setValueAtTime(440, now);
    osc.frequency.exponentialRampToValueAtTime(1180, now + 0.12);
    osc.frequency.exponentialRampToValueAtTime(740, now + 0.28);
    osc.frequency.exponentialRampToValueAtTime(1420, now + 0.44);

    oscHarmonic.frequency.setValueAtTime(880, now);
    oscHarmonic.frequency.exponentialRampToValueAtTime(1760, now + 0.22);
    oscHarmonic.frequency.exponentialRampToValueAtTime(990, now + 0.44);

    filter.type = 'bandpass';
    filter.frequency.setValueAtTime(1100, now);
    filter.Q.setValueAtTime(3.5, now);

    // Volume muito sutil e elegante (não assusta)
    gain.gain.setValueAtTime(0.001, now);
    gain.gain.exponentialRampToValueAtTime(0.07, now + 0.05);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.46);

    osc.connect(filter);
    oscHarmonic.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now);
    oscHarmonic.start(now);
    osc.stop(now + 0.47);
    oscHarmonic.stop(now + 0.47);
  } catch {
    // áudio bloqueado ou não inicializado
  }
}

/**
 * Constelação Quântica Holográfica (Estilo OSONE na paleta Ciano do JARVIS):
 * - Rotação constante em velocidade sutil;
 * - Quando o usuário fala: os pontos brilhantes reluzem e cintilam intensamente;
 * - Quando o Jarvis fala: a constelação inteira expande suavemente;
 * - Quando o Jarvis pesquisa na web ou processa dados: condensa em uma singularidade rápida e emite um som holográfico sutil.
 */
export const QuantumParticleOrb: React.FC<QuantumParticleOrbProps> = ({ color }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Áudio e estado do Jarvis
  const { audioTrack, state: agentState } = useVoiceAssistant();
  const jarvisVol = useTrackVolume(audioTrack);

  // Microfone do Usuário
  const { microphoneTrack } = useLocalParticipant();
  // @ts-expect-error microphoneTrack publication contains track
  const userVol = useTrackVolume(microphoneTrack?.track || microphoneTrack);

  const jarvisVolRef = useRef(jarvisVol);
  const userVolRef = useRef(userVol);
  const agentStateRef = useRef(agentState);

  useEffect(() => {
    jarvisVolRef.current = jarvisVol;
  }, [jarvisVol]);

  useEffect(() => {
    userVolRef.current = userVol;
  }, [userVol]);

  // Efeito sonoro acionado quando entra no modo de pesquisa/processamento (thinking)
  useEffect(() => {
    agentStateRef.current = agentState;
    if (agentState === 'thinking') {
      playHolographicSearchChirp();
    }
  }, [agentState]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let animId: number;
    let isPaused = false;

    // Resolução e nitidez
    const size = 520;
    const dpr = typeof window !== 'undefined' ? Math.min(2, window.devicePixelRatio || 1) : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const center = size / 2;
    const focalLength = 340;

    // Definição das partículas nos anéis e nuvens orbitais (estilo OSONE)
    const particleCount = 480;
    const particles: Particle[] = [];

    // Cria 4 anéis orbitais com inclinações e densidades variadas
    for (let i = 0; i < particleCount; i++) {
      const ring = i % 4;
      const isCluster = Math.random() < 0.28; // 28% fazem parte de aglomerados de luz brilhante
      const baseDist = 130 + (Math.random() - 0.5) * (isCluster ? 18 : 34);

      particles.push({
        x: 0,
        y: 0,
        z: 0,
        baseRadius: isCluster ? Math.random() * 1.8 + 1.2 : Math.random() * 1.2 + 0.7,
        orbitRing: ring,
        orbitAngle: Math.random() * Math.PI * 2,
        orbitSpeed: (0.0035 + Math.random() * 0.004) * (ring % 2 === 0 ? 1 : -1),
        radialDistance: baseDist,
        verticalOffset: (Math.random() - 0.5) * (isCluster ? 22 : 45),
        baseAlpha: isCluster ? 0.75 + Math.random() * 0.25 : 0.35 + Math.random() * 0.4,
        shimmerPhase: Math.random() * Math.PI * 2,
        shimmerSpeed: 0.05 + Math.random() * 0.08,
        isCluster,
      });
    }

    // Variáveis de animação
    let rotX = 0.25;
    let rotY = 0;
    let rotZ = 0.1;

    let smoothJarvis = 0;
    let smoothUser = 0;
    let condenseFactor = 1.0; // 1.0 normal, cai para ~0.45 quando condensado

    const isPink = color === 0xff69b4;
    // Matiz Ciano Jarvis: 188 (com toques de 195 e branco)
    const hue = isPink ? 320 : 188;

    const handleVisibility = () => {
      isPaused = document.hidden;
    };
    document.addEventListener('visibilitychange', handleVisibility);

    const render = () => {
      animId = requestAnimationFrame(render);
      if (isPaused) return;

      // Suavização das entradas de voz
      smoothJarvis += (jarvisVolRef.current - smoothJarvis) * 0.18;
      smoothUser += (userVolRef.current - smoothUser) * 0.25;

      const isThinking = agentStateRef.current === 'thinking';

      // Dinâmica de Condensação (quando pesquisa algo na web)
      const targetCondense = isThinking ? 0.48 : 1.0;
      condenseFactor += (targetCondense - condenseFactor) * 0.12;

      // Velocidade de rotação: constante e sutil, acelerando durante pesquisa
      const baseSpeed = isThinking ? 0.022 : 0.0055 + smoothJarvis * 0.01;
      rotY += baseSpeed;
      rotX += baseSpeed * 0.45;
      rotZ += baseSpeed * 0.25;

      // Dinâmica de Expansão (quando o Jarvis fala)
      // O raio expande significativamente com a voz do Jarvis
      const expansion = smoothJarvis * 75;
      const currentBaseRadius = (130 + expansion) * condenseFactor;

      ctx.clearRect(0, 0, size, size);

      // --- 1. Núcleo Cósmico de Energia (Glow Central) ---
      const corePulse = (16 + smoothJarvis * 35 + (isThinking ? 22 : 0)) * condenseFactor;
      const coreGrad = ctx.createRadialGradient(
        center,
        center,
        0,
        center,
        center,
        corePulse * 3.2
      );

      const coreAlpha = isThinking
        ? 0.75 + Math.sin(rotY * 8) * 0.2
        : 0.35 + smoothJarvis * 0.45 + smoothUser * 0.2;

      if (isPink) {
        coreGrad.addColorStop(0, `rgba(255, 105, 180, ${coreAlpha})`);
        coreGrad.addColorStop(0.35, `rgba(236, 72, 153, ${coreAlpha * 0.4})`);
        coreGrad.addColorStop(1, 'rgba(236, 72, 153, 0)');
      } else {
        coreGrad.addColorStop(0, `rgba(165, 243, 252, ${coreAlpha})`); // Branco-ciano incandescente
        coreGrad.addColorStop(0.25, `rgba(34, 211, 238, ${coreAlpha * 0.6})`);
        coreGrad.addColorStop(0.5, `rgba(6, 182, 212, ${coreAlpha * 0.25})`);
        coreGrad.addColorStop(1, 'rgba(6, 182, 212, 0)');
      }

      ctx.fillStyle = coreGrad;
      ctx.beginPath();
      ctx.arc(center, center, corePulse * 3.2, 0, Math.PI * 2);
      ctx.fill();

      // --- 2. Matrizes de Rotação 3D ---
      const cosX = Math.cos(rotX);
      const sinX = Math.sin(rotX);
      const cosY = Math.cos(rotY);
      const sinY = Math.sin(rotY);
      const cosZ = Math.cos(rotZ);
      const sinZ = Math.sin(rotZ);

      interface Projected {
        px: number;
        py: number;
        pz: number;
        scale: number;
        alpha: number;
        radius: number;
        isCluster: boolean;
      }

      const projectedList: Projected[] = [];

      // Efeito de brilho quântico quando o usuário fala
      const userSparkle = smoothUser * 3.5;

      for (let i = 0; i < particleCount; i++) {
        const p = particles[i];
        p.orbitAngle += p.orbitSpeed;
        p.shimmerPhase += p.shimmerSpeed + userSparkle * 0.2;

        // Raio e posição orbital
        const r = currentBaseRadius + (p.radialDistance - 130) * condenseFactor;
        const angle = p.orbitAngle;

        // Modelagem de Anéis Tridimensionais com inclinações diferentes (estilo OSONE)
        let lx = 0;
        let ly = 0;
        let lz = 0;

        if (p.orbitRing === 0) {
          // Anel Equatorial Principal
          lx = Math.cos(angle) * r;
          lz = Math.sin(angle) * r;
          ly = p.verticalOffset * condenseFactor + Math.sin(angle * 3) * 6;
        } else if (p.orbitRing === 1) {
          // Anel Inclinado a 45 Graus
          const baseC = Math.cos(angle) * r;
          const baseS = Math.sin(angle) * r;
          lx = baseC * 0.707 - p.verticalOffset * 0.707;
          ly = baseC * 0.707 + p.verticalOffset * 0.707;
          lz = baseS;
        } else if (p.orbitRing === 2) {
          // Anel Polar Inclinado
          const baseC = Math.cos(angle) * r;
          const baseS = Math.sin(angle) * r;
          lx = baseS;
          ly = baseC * 0.85;
          lz = -baseC * 0.52 + p.verticalOffset * 0.5;
        } else {
          // Nuvem Quântica Difusa ao redor da esfera
          const phi = angle;
          const theta = p.verticalOffset * 0.08;
          lx = r * Math.sin(theta) * Math.cos(phi);
          ly = r * Math.sin(theta) * Math.sin(phi);
          lz = r * Math.cos(theta);
        }

        // Rotação Y
        const x1 = lx * cosY - lz * sinY;
        const z1 = lz * cosY + lx * sinY;

        // Rotação X
        const y2 = ly * cosX - z1 * sinX;
        const z2 = z1 * cosX + ly * sinX;

        // Rotação Z
        const x3 = x1 * cosZ - y2 * sinZ;
        const y3 = y2 * cosZ + x1 * sinZ;

        // Projeção em Perspectiva
        const perspective = focalLength / (focalLength + z2);
        const screenX = center + x3 * perspective;
        const screenY = center + y3 * perspective;

        // Profundidade (-130 a +130)
        const depthNormalized = (z2 + 160) / 320;

        // Cintilação: quando o usuário fala, cintila com alta frequência
        const shimmer = Math.sin(p.shimmerPhase) * 0.35;
        let finalAlpha = (p.baseAlpha + shimmer) * Math.max(0.15, Math.min(1.0, depthNormalized));

        // INTENSIFICAÇÃO DO BRILHO QUANDO O USUÁRIO FALA:
        if (smoothUser > 0.02) {
          finalAlpha = Math.min(1.0, finalAlpha + userSparkle * 0.65);
        }

        // TAMANHO DA PARTÍCULA:
        let finalRadius = p.baseRadius * perspective;
        if (smoothUser > 0.02) {
          finalRadius *= 1 + smoothUser * 0.9; // cresce ligeiramente cintilando
        }
        if (smoothJarvis > 0.02) {
          finalRadius *= 1 + smoothJarvis * 0.4;
        }

        projectedList.push({
          px: screenX,
          py: screenY,
          pz: z2,
          scale: perspective,
          alpha: finalAlpha,
          radius: Math.max(0.6, finalRadius),
          isCluster: p.isCluster,
        });
      }

      // --- 3. Filamentos Quânticos Sutis entre Partículas de Aglomerados ---
      ctx.lineWidth = 0.75;
      const linkDist = 32 * condenseFactor;

      for (let i = 0; i < particleCount; i += 2) {
        const p1 = projectedList[i];
        if (p1.pz < -40) continue; // desenha conexões apenas nas camadas da frente

        for (let j = i + 1; j < Math.min(i + 12, particleCount); j++) {
          const p2 = projectedList[j];
          if (p2.pz < -40) continue;

          const dx = p1.px - p2.px;
          const dy = p1.py - p2.py;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < linkDist * p1.scale) {
            const linkAlpha = (1 - dist / (linkDist * p1.scale)) * p1.alpha * p2.alpha * 0.4;
            if (linkAlpha > 0.03) {
              ctx.strokeStyle = `hsla(${hue}, 90%, 65%, ${linkAlpha.toFixed(3)})`;
              ctx.beginPath();
              ctx.moveTo(p1.px, p1.py);
              ctx.lineTo(p2.px, p2.py);
              ctx.stroke();
            }
          }
        }
      }

      // --- 4. Renderização das Partículas (Estrelas / Nós de Energia) ---
      for (let i = 0; i < particleCount; i++) {
        const pt = projectedList[i];

        ctx.beginPath();
        ctx.arc(pt.px, pt.py, pt.radius, 0, Math.PI * 2);

        // Quando o usuário fala ou a partícula é de aglomerado frontal: brilha em branco puro com halo
        if (pt.isCluster || smoothUser > 0.04) {
          const glowMultiplier = smoothUser > 0.02 ? 1 + userSparkle : 1;
          ctx.fillStyle = `rgba(255, 255, 255, ${Math.min(1.0, pt.alpha * 1.25).toFixed(3)})`;
          ctx.shadowColor = isPink ? '#f472b6' : '#22d3ee';
          ctx.shadowBlur = (pt.isCluster ? 7 : 4) * pt.scale * glowMultiplier;
        } else if (pt.pz > 0) {
          ctx.fillStyle = `hsla(${hue}, 90%, 75%, ${pt.alpha.toFixed(3)})`;
          ctx.shadowColor = '#06b6d4';
          ctx.shadowBlur = 3 * pt.scale;
        } else {
          ctx.fillStyle = `hsla(${hue}, 80%, 55%, ${(pt.alpha * 0.7).toFixed(3)})`;
          ctx.shadowBlur = 0;
        }

        ctx.fill();
      }

      ctx.shadowBlur = 0;
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
        className="pointer-events-none drop-shadow-[0_0_35px_rgba(34,211,238,0.3)]"
      />
    </div>
  );
};
