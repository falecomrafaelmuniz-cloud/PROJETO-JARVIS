'use client';

import React, { useEffect, useRef } from 'react';
import { useVoiceAssistant, useTrackVolume, useLocalParticipant } from '@livekit/components-react';

interface QuantumParticleOrbProps {
  color?: number;
}

interface Particle {
  x: number;
  y: number;
  z: number;
  baseRadius: number;
  orbitRing: number;
  orbitAngle: number;
  orbitSpeed: number;
  radialDistance: number;
  verticalOffset: number;
  baseAlpha: number;
  shimmerPhase: number;
  shimmerSpeed: number;
  isCluster: boolean;
}

// Converte número hexadecimal em HSL e RGB
function parseHexColor(numColor?: number) {
  const n = typeof numColor === 'number' && !isNaN(numColor) ? numColor : 0xff5500;
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;

  const rNorm = r / 255;
  const gNorm = g / 255;
  const bNorm = b / 255;
  const max = Math.max(rNorm, gNorm, bNorm);
  const min = Math.min(rNorm, gNorm, bNorm);
  let h = 0;
  let s = 0;
  const l = (max + min) / 2;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case rNorm:
        h = (gNorm - bNorm) / d + (gNorm < bNorm ? 6 : 0);
        break;
      case gNorm:
        h = (bNorm - rNorm) / d + 2;
        break;
      case bNorm:
        h = (rNorm - gNorm) / d + 4;
        break;
    }
    h = Math.round(h * 60);
  }

  return { r, g, b, h, s: Math.round(s * 100), l: Math.round(l * 100) };
}

// Sintetizador procedural de som sci-fi suave via Web Audio API
function playHolographicSearchChirp() {
  if (typeof window === 'undefined') return;
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    const oscHarmonic = ctx.createOscillator();
    const gain = ctx.createGain();
    const filter = ctx.createBiquadFilter();

    osc.type = 'sine';
    oscHarmonic.type = 'triangle';

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
    // áudio bloqueado
  }
}

/**
 * Esfera Cósmica Quântica Tridimensional (Estilo OSONE):
 * - Rotação orbital suave tridimensional com 520 partículas;
 * - Núcleo incandescente branco-dourado com halo na cor do tema;
 * - Reatividade dinâmica à voz do usuário e do Jarvis;
 * - Condensação holográfica ao pesquisar/processar.
 */
export const QuantumParticleOrb: React.FC<QuantumParticleOrbProps> = ({ color }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const { audioTrack, state: agentState } = useVoiceAssistant();
  const jarvisVol = useTrackVolume(audioTrack);

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

    const size = 520;
    const dpr = typeof window !== 'undefined' ? Math.min(2, window.devicePixelRatio || 1) : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const center = size / 2;
    const focalLength = 340;

    const { r: cr, g: cg, b: cb, h: hue } = parseHexColor(color);

    const particleCount = 520;
    const particles: Particle[] = [];

    for (let i = 0; i < particleCount; i++) {
      const ring = i % 4;
      const isCluster = Math.random() < 0.32;
      const baseDist = 125 + (Math.random() - 0.5) * (isCluster ? 20 : 38);

      particles.push({
        x: 0,
        y: 0,
        z: 0,
        baseRadius: isCluster ? Math.random() * 2.0 + 1.2 : Math.random() * 1.3 + 0.6,
        orbitRing: ring,
        orbitAngle: Math.random() * Math.PI * 2,
        orbitSpeed: (0.0035 + Math.random() * 0.004) * (ring % 2 === 0 ? 1 : -1),
        radialDistance: baseDist,
        verticalOffset: (Math.random() - 0.5) * (isCluster ? 24 : 50),
        baseAlpha: isCluster ? 0.8 + Math.random() * 0.2 : 0.35 + Math.random() * 0.45,
        shimmerPhase: Math.random() * Math.PI * 2,
        shimmerSpeed: 0.05 + Math.random() * 0.08,
        isCluster,
      });
    }

    let rotX = 0.22;
    let rotY = 0;
    let rotZ = 0.08;

    let smoothJarvis = 0;
    let smoothUser = 0;
    let condenseFactor = 1.0;

    const handleVisibility = () => {
      isPaused = document.hidden;
    };
    document.addEventListener('visibilitychange', handleVisibility);

    const render = () => {
      animId = requestAnimationFrame(render);
      if (isPaused) return;

      smoothJarvis += (jarvisVolRef.current - smoothJarvis) * 0.18;
      smoothUser += (userVolRef.current - smoothUser) * 0.25;

      const isThinking = agentStateRef.current === 'thinking';
      const targetCondense = isThinking ? 0.45 : 1.0;
      condenseFactor += (targetCondense - condenseFactor) * 0.12;

      const baseSpeed = isThinking ? 0.024 : 0.0055 + smoothJarvis * 0.012;
      rotY += baseSpeed;
      rotX += baseSpeed * 0.42;
      rotZ += baseSpeed * 0.22;

      const expansion = smoothJarvis * 80;
      const currentBaseRadius = (125 + expansion) * condenseFactor;

      ctx.clearRect(0, 0, size, size);

      // --- 1. Núcleo Solar / Quântico Incandescente (Glow OSONE) ---
      const corePulse = (18 + smoothJarvis * 40 + (isThinking ? 24 : 0)) * condenseFactor;
      const coreGrad = ctx.createRadialGradient(
        center,
        center,
        0,
        center,
        center,
        corePulse * 3.4
      );

      const coreAlpha = isThinking
        ? 0.85 + Math.sin(rotY * 8) * 0.15
        : 0.4 + smoothJarvis * 0.5 + smoothUser * 0.25;

      // Centro branco incandescente, transicionando para a cor neon e depois fade
      coreGrad.addColorStop(0, `rgba(255, 255, 255, ${Math.min(1.0, coreAlpha * 1.2)})`);
      coreGrad.addColorStop(0.2, `rgba(${Math.min(255, cr + 60)}, ${Math.min(255, cg + 80)}, ${Math.min(255, cb + 60)}, ${coreAlpha * 0.85})`);
      coreGrad.addColorStop(0.45, `rgba(${cr}, ${cg}, ${cb}, ${coreAlpha * 0.5})`);
      coreGrad.addColorStop(0.75, `rgba(${cr * 0.7}, ${cg * 0.7}, ${cb * 0.7}, ${coreAlpha * 0.15})`);
      coreGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

      ctx.fillStyle = coreGrad;
      ctx.beginPath();
      ctx.arc(center, center, corePulse * 3.4, 0, Math.PI * 2);
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
      const userSparkle = smoothUser * 3.5;

      for (let i = 0; i < particleCount; i++) {
        const p = particles[i];
        p.orbitAngle += p.orbitSpeed;
        p.shimmerPhase += p.shimmerSpeed + userSparkle * 0.2;

        const r = currentBaseRadius + (p.radialDistance - 125) * condenseFactor;
        const angle = p.orbitAngle;

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
          // Nuvem Quântica Difusa ao redor
          const phi = angle;
          const theta = p.verticalOffset * 0.08;
          lx = r * Math.sin(theta) * Math.cos(phi);
          ly = r * Math.sin(theta) * Math.sin(phi);
          lz = r * Math.cos(theta);
        }

        const x1 = lx * cosY - lz * sinY;
        const z1 = lz * cosY + lx * sinY;

        const y2 = ly * cosX - z1 * sinX;
        const z2 = z1 * cosX + ly * sinX;

        const x3 = x1 * cosZ - y2 * sinZ;
        const y3 = y2 * cosZ + x1 * sinZ;

        const perspective = focalLength / (focalLength + z2);
        const screenX = center + x3 * perspective;
        const screenY = center + y3 * perspective;

        const depthNormalized = (z2 + 160) / 320;
        const shimmer = Math.sin(p.shimmerPhase) * 0.35;
        let finalAlpha = (p.baseAlpha + shimmer) * Math.max(0.18, Math.min(1.0, depthNormalized));

        if (smoothUser > 0.02) {
          finalAlpha = Math.min(1.0, finalAlpha + userSparkle * 0.65);
        }

        let finalRadius = p.baseRadius * perspective;
        if (smoothUser > 0.02) {
          finalRadius *= 1 + smoothUser * 0.9;
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

      // --- 3. Filamentos Cósmicos Sutis ---
      ctx.lineWidth = 0.75;
      const linkDist = 34 * condenseFactor;

      for (let i = 0; i < particleCount; i += 2) {
        const p1 = projectedList[i];
        if (p1.pz < -30) continue;

        for (let j = i + 1; j < Math.min(i + 12, particleCount); j++) {
          const p2 = projectedList[j];
          if (p2.pz < -30) continue;

          const dx = p1.px - p2.px;
          const dy = p1.py - p2.py;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < linkDist * p1.scale) {
            const linkAlpha = (1 - dist / (linkDist * p1.scale)) * p1.alpha * p2.alpha * 0.42;
            if (linkAlpha > 0.03) {
              ctx.strokeStyle = `hsla(${hue}, 95%, 65%, ${linkAlpha.toFixed(3)})`;
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

        if (pt.isCluster || smoothUser > 0.04) {
          const glowMultiplier = smoothUser > 0.02 ? 1 + userSparkle : 1;
          ctx.fillStyle = `rgba(255, 255, 255, ${Math.min(1.0, pt.alpha * 1.3).toFixed(3)})`;
          ctx.shadowColor = `rgb(${cr}, ${cg}, ${cb})`;
          ctx.shadowBlur = (pt.isCluster ? 7 : 4) * pt.scale * glowMultiplier;
        } else if (pt.pz > 0) {
          ctx.fillStyle = `hsla(${hue}, 95%, 72%, ${pt.alpha.toFixed(3)})`;
          ctx.shadowColor = `rgb(${cr}, ${cg}, ${cb})`;
          ctx.shadowBlur = 3.5 * pt.scale;
        } else {
          ctx.fillStyle = `hsla(${hue}, 85%, 52%, ${(pt.alpha * 0.7).toFixed(3)})`;
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

  const { r: cr, g: cg, b: cb } = parseHexColor(color);

  return (
    <div className="relative flex size-[360px] items-center justify-center overflow-visible select-none">
      <canvas
        ref={canvasRef}
        style={{ width: 440, height: 440 }}
        className="pointer-events-none drop-shadow-[0_0_45px_rgba(255,85,0,0.35)]"
      />
    </div>
  );
};
