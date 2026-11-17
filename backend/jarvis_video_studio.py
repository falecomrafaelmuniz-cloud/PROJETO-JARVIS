"""
Jarvis Estúdio de Vídeos (Sob Demanda)
--------------------------------------
Motor 100% isolado e acionado exclusivamente sob demanda pelo painel Vídeos.
Não mantém nenhum loop em segundo plano e não é importado pelo agent.py.

Subcomandos:
  python jarvis_video_studio.py script --input <json_path>
  python jarvis_video_studio.py render --input <json_path>
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import imageio_ffmpeg
import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "video_studio_output"
JOBS_DIR = OUTPUT_DIR / "jobs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEN_CONTENT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

CATEGORY_THEMES: dict[str, dict[str, Any]] = {
    "alerta": {
        "badge": "ALERTA IMPORTANTE",
        "bg_top": (28, 10, 18),
        "bg_bottom": (10, 12, 22),
        "accent": (244, 63, 94),
        "accent_soft": (159, 18, 57),
        "glow": (251, 113, 133),
        "symbol": "!",
    },
    "risco": {
        "badge": "RISCO REAL",
        "bg_top": (32, 18, 8),
        "bg_bottom": (11, 13, 20),
        "accent": (245, 158, 11),
        "accent_soft": (146, 64, 14),
        "glow": (251, 191, 36),
        "symbol": "▲",
    },
    "dado": {
        "badge": "FATOS & IMPACTO",
        "bg_top": (10, 22, 38),
        "bg_bottom": (8, 12, 22),
        "accent": (34, 211, 238),
        "accent_soft": (14, 116, 144),
        "glow": (103, 232, 249),
        "symbol": "◆",
    },
    "solucao": {
        "badge": "SOLUÇÃO",
        "bg_top": (9, 30, 26),
        "bg_bottom": (8, 14, 20),
        "accent": (52, 211, 153),
        "accent_soft": (6, 95, 70),
        "glow": (110, 231, 183),
        "symbol": "✓",
    },
    "acao": {
        "badge": "HORA DE AGIR",
        "bg_top": (12, 34, 28),
        "bg_bottom": (6, 15, 22),
        "accent": (16, 185, 129),
        "accent_soft": (4, 120, 87),
        "glow": (52, 211, 153),
        "symbol": "→",
    },
}

VOICE_STYLES = {
    "Orus": "firm, clear, authoritative Brazilian Portuguese narrator with steady pacing",
    "Kore": "clear, warm, trustworthy Brazilian Portuguese narrator with crisp diction",
    "Aoede": "energetic, engaging Brazilian Portuguese narrator with persuasive tone",
}


def get_api_key() -> str:
    key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY não configurada em backend/.env")
    return key


def slugify(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return cleaned[:48] or "jarvis-video"


def write_job_status(job_id: str, data: dict[str, Any]) -> None:
    if not job_id:
        return
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOBS_DIR / f"{job_id}.json"
    tmp = JOBS_DIR / f"{job_id}.tmp"
    payload = {
        "job_id": job_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **data,
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def extract_interaction_text(resp_json: dict[str, Any]) -> str:
    # Compatibilidade entre formato steps[] e outputs[]
    for step in reversed(resp_json.get("steps", []) or []):
        if step.get("type") == "model_output":
            for item in step.get("content", []) or []:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    return str(item["text"])
    for out in reversed(resp_json.get("outputs", []) or []):
        if isinstance(out, dict) and out.get("type") == "text" and out.get("text"):
            return str(out["text"])
    raise RuntimeError(f"Resposta sem texto do modelo: {json.dumps(resp_json)[:400]}")


def parse_json_loose(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def generate_script(payload: dict[str, Any]) -> dict[str, Any]:
    idea = str(payload.get("idea") or "").strip()
    if not idea:
        raise ValueError("Informe uma ideia ou assunto para gerar o roteiro.")

    tone = str(payload.get("tone") or "urgente_convincente").strip()
    aspect_ratio = "16:9" if payload.get("aspect_ratio") == "16:9" else "9:16"
    target_scenes = int(payload.get("scene_count") or 5)
    target_scenes = max(3, min(7, target_scenes))
    ref_images = payload.get("reference_images") or []

    schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string"},
            "resumo": {"type": "string"},
            "cenas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "titulo_curto": {"type": "string"},
                        "categoria": {
                            "type": "string",
                            "enum": ["alerta", "risco", "dado", "solucao", "acao"],
                        },
                        "destaque_numero": {"type": "string"},
                        "texto_tela": {"type": "string"},
                        "narracao": {"type": "string"},
                        "prompt_imagem": {"type": "string"},
                        "duracao_seg": {"type": "number"},
                    },
                    "required": [
                        "titulo_curto",
                        "categoria",
                        "destaque_numero",
                        "texto_tela",
                        "narracao",
                        "prompt_imagem",
                        "duracao_seg",
                    ],
                },
            },
        },
        "required": ["titulo", "resumo", "cenas"],
    }

    tone_desc = {
        "urgente_convincente": "direto, transparente, persuasivo, mostrando consequências reais da inércia e chamando para ação imediata sem ser agressivo",
        "informativo": "claro, objetivo, didático e respeitoso, fácil de acompanhar no celular",
        "impactante": "forte, ritmo dinâmico de vídeo curto (Reels/WhatsApp), frases curtas de alto impacto",
    }.get(tone, "direto, claro e persuasivo em português do Brasil")

    prompt_text = f"""Você é um roteirista especialista em vídeos curtos de alto impacto para WhatsApp e redes sociais no Brasil.
Crie um roteiro completo em Português do Brasil dividido em exatamente {target_scenes} cenas sequenciais.

IDEIA / CONTEXTO DO USUÁRIO:
{idea}

TOM DESEJADO: {tone_desc}
FORMATO DO VÍDEO: {aspect_ratio} ({'vertical para WhatsApp/celular' if aspect_ratio == '9:16' else 'horizontal widescreen'})

REGRAS IMPORTANTES:
1. Cada `texto_tela` deve ser CURTO e GIGANTE na leitura (máximo 90 caracteres, ideal 2 a 3 linhas curtas, zero jargão jurídico complexo, letras fáceis de ler no celular).
2. Cada `narracao` deve soar natural na voz falada em português do Brasil (máximo 26 palavras por cena, ~6 a 10 segundos falados). Use pontuação natural (vírgulas e pontos) para dar ritmo à narração.
3. `destaque_numero` deve ser um rótulo curto (até 18 caracteres), como "ATENÇÃO", "SEM QUÓRUM", "RISCO ALTO", "2 MINUTOS", "VOTE AGORA", ou vazio "" se não precisar.
4. `categoria` deve seguir o arco narrativo: comece com `alerta`, passe por `risco` e `dado`, apresente `solucao`, e termine com `acao` (CTA claro).
5. `prompt_imagem` deve descrever uma cena visual fotorrealista em inglês (sem texto dentro da imagem) caso imagens de IA sejam geradas depois.
6. `duracao_seg` deve ficar entre 6 e 11 segundos por cena.
7. Se o usuário enviou imagens/prints anexados, extraia os fatos concretos e riscos reais das imagens para citar no roteiro e nas legendas!"""

    content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for item in ref_images[:4]:
        if isinstance(item, str) and "," in item and item.startswith("data:image/"):
            header, b64 = item.split(",", 1)
            mime = header.split(";")[0].replace("data:", "").strip() or "image/jpeg"
            content_parts.append({"type": "image", "data": b64, "mime_type": mime})

    key = get_api_key()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    last_err = ""
    req_timeout = httpx.Timeout(60.0, connect=10.0)
    for model in ("gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.8-flash"):
        for attempt in range(2):
            try:
                body: dict[str, Any] = {
                    "model": model,
                    "input": [{"type": "user_input", "content": content_parts}],
                    "response_format": {
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema,
                    },
                }
                resp = httpx.post(INTERACTIONS_URL, headers=headers, json=body, timeout=req_timeout)
                if resp.status_code == 200:
                    raw_text = extract_interaction_text(resp.json())
                    parsed = parse_json_loose(raw_text)
                    scenes = parsed.get("cenas") or []
                    normalized_scenes = []
                    for idx, sc in enumerate(scenes, start=1):
                        cat = str(sc.get("categoria") or "dado").lower()
                        if cat not in CATEGORY_THEMES:
                            cat = "dado"
                        normalized_scenes.append(
                            {
                                "id": f"cena-{idx}",
                                "titulo_curto": str(sc.get("titulo_curto") or f"Cena {idx}")[:60],
                                "categoria": cat,
                                "destaque_numero": str(sc.get("destaque_numero") or "")[:24],
                                "texto_tela": str(sc.get("texto_tela") or "")[:140],
                                "narracao": str(sc.get("narracao") or "")[:360],
                                "prompt_imagem": str(sc.get("prompt_imagem") or "")[:300],
                                "duracao_seg": float(max(4.0, min(14.0, float(sc.get("duracao_seg") or 7.0)))),
                                "imagem_base64": "",
                                "caption_mode": (
                                    str(payload.get("caption_mode") or "screen_box").strip()
                                    if str(payload.get("caption_mode") or "screen_box").strip() in CAPTION_MODES
                                    else "screen_box"
                                ),
                            }
                        )
                    return {
                        "titulo": str(parsed.get("titulo") or "Vídeo Jarvis")[:80],
                        "resumo": str(parsed.get("resumo") or "")[:200],
                        "aspect_ratio": aspect_ratio,
                        "cenas": normalized_scenes,
                    }
                last_err = f"{model} HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code in (429, 503) and attempt == 0:
                    time.sleep(1.5)
                    continue
                break
            except httpx.TimeoutException as exc:
                last_err = f"{model} err: {exc}"
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                break
            except Exception as exc:
                last_err = f"{model} err: {exc}"
                break

    raise RuntimeError(f"Não foi possível gerar o roteiro agora. Detalhe: {last_err}")


def decode_audio_to_pcm24k(audio_bytes: bytes, mime_type: str) -> bytes:
    """Converte WAV ou audio/L16 para PCM 24000Hz mono 16-bit signed little-endian."""
    if audio_bytes[:4] == b"RIFF":
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    else:
        ch = 1
        sw = 2
        m = re.search(r"rate=(\d+)", mime_type or "")
        sr = int(m.group(1)) if m else 24000
        frames = audio_bytes

    if ch == 1 and sw == 2 and sr == 24000:
        return frames

    # Fallback via ffmpeg para garantir 24kHz mono 16-bit s16le limpo
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
    if audio_bytes[:4] == b"RIFF":
        cmd += ["-f", "wav", "-i", "pipe:0"]
    else:
        cmd += ["-f", "s16le", "-ar", str(sr), "-ac", str(ch), "-i", "pipe:0"]
    cmd += ["-ar", "24000", "-ac", "1", "-f", "s16le", "pipe:1"]
    proc = subprocess.run(cmd, input=frames if audio_bytes[:4] != b"RIFF" else audio_bytes, capture_output=True, check=True)
    return proc.stdout


def synthesize_tts_pcm(text: str, voice: str, api_key: str) -> tuple[bytes, str]:
    """Gera áudio PCM 24kHz mono em PT-BR com fallback automático entre modelos TTS."""
    clean_text = text.strip()
    if not clean_text:
        return b"\x00" * (24000 * 2 * 4), "silent"

    style = VOICE_STYLES.get(voice, VOICE_STYLES["Orus"])
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    # 1) Gemini 3.8 Flash Lite TTS / Flash TTS via Interactions API
    for model in ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"):
        try:
            body = {
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": clean_text,
                                "annotations": [{"type": "speech_metadata", "style": style}],
                            }
                        ],
                    }
                ],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice}]},
            }
            resp = httpx.post(INTERACTIONS_URL, headers=headers, json=body, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                for step in data.get("steps", []) or []:
                    for item in step.get("content", []) or []:
                        if isinstance(item, dict) and item.get("type") == "audio" and item.get("data"):
                            raw = base64.b64decode(item["data"])
                            mime = str(item.get("mime_type") or "audio/wav")
                            return decode_audio_to_pcm24k(raw, mime), model
        except Exception:
            pass

    # 2) Fallback: Gemini 2.5 / 3.1 Flash TTS via generateContent
    for model in ("gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview"):
        try:
            url = f"{GEN_CONTENT_BASE}/{model}:generateContent?key={api_key}"
            body = {
                "contents": [{"parts": [{"text": f"Leia em português do Brasil de forma natural e clara: {clean_text}"}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
                },
            }
            resp = httpx.post(url, json=body, timeout=30.0)
            if resp.status_code == 200:
                parts = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                for p in parts:
                    inline = p.get("inlineData") or {}
                    if inline.get("data"):
                        raw = base64.b64decode(inline["data"])
                        mime = str(inline.get("mimeType") or "audio/L16;rate=24000")
                        return decode_audio_to_pcm24k(raw, mime), model
        except Exception:
            pass

    # Fallback silencioso se todos estiverem indisponíveis
    return b"\x00" * (24000 * 2 * 6), "silent-fallback"


def try_generate_ai_image(
    prompt: str, aspect_ratio: str, api_key: str
) -> tuple[Image.Image | None, bool]:
    """Tenta gerar imagem via Gemini Image API; retorna (img, quota_zero_permanente)."""
    if not prompt.strip():
        return None, False
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    for model in ("gemini-nano-banana-2.1", "gemini-3.1-flash-lite-image"):
        try:
            resp = httpx.post(
                INTERACTIONS_URL,
                headers=headers,
                json={
                    "model": model,
                    "input": f"{prompt}. Photorealistic editorial visual, dramatic lighting, strictly NO text, NO letters, NO watermarks.",
                    "response_format": {
                        "type": "image",
                        "aspect_ratio": aspect_ratio,
                        "image_size": "1K",
                    },
                },
                timeout=35.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                for step in data.get("steps", []) or []:
                    for item in step.get("content", []) or []:
                        if isinstance(item, dict) and item.get("type") == "image" and item.get("data"):
                            img = Image.open(io.BytesIO(base64.b64decode(item["data"]))).convert("RGB")
                            return img, False
            elif resp.status_code == 429 and "limit: 0" in resp.text:
                return None, True
        except Exception:
            pass
    return None, False


def decode_data_image(data_url: str) -> Image.Image | None:
    if not data_url or not isinstance(data_url, str):
        return None
    try:
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        img = ImageOps.exif_transpose(img).convert("RGB")
        return img
    except Exception:
        return None


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
        ]
        if bold
        else [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_motion_card_backdrop(
    width: int,
    height: int,
    scene: dict[str, Any],
    scene_idx: int,
    total_scenes: int,
) -> Image.Image:
    """Cria um fundo editorial escuro de alto impacto quando não há foto anexada."""
    cat = str(scene.get("categoria") or "dado")
    theme = CATEGORY_THEMES.get(cat, CATEGORY_THEMES["dado"])
    accent = theme["accent"]
    top_c = theme["bg_top"]
    bot_c = theme["bg_bottom"]

    # Gradiente vertical suave
    img = Image.new("RGB", (width, height), bot_c)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(top_c[0] * (1 - t) + bot_c[0] * t)
        g = int(top_c[1] * (1 - t) + bot_c[1] * t)
        b = int(top_c[2] * (1 - t) + bot_c[2] * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Grade HUD sutil
    grid_color = (
        min(255, bot_c[0] + 18),
        min(255, bot_c[1] + 24),
        min(255, bot_c[2] + 34),
    )
    step = 64
    for gx in range(0, width, step):
        draw.line([(gx, 0), (gx, height)], fill=grid_color, width=1)
    for gy in range(0, height, step):
        draw.line([(0, gy), (width, gy)], fill=grid_color, width=1)

    # Halo radial luminoso na área superior/central
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow_layer)
    cx = width // 2
    cy = int(height * (0.34 if height > width else 0.36))
    radius = int(min(width, height) * 0.42)
    g_draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(accent[0], accent[1], accent[2], 70),
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=75))
    img = Image.alpha_composite(img.convert("RGBA"), glow_layer)

    overlay = ImageDraw.Draw(img)
    # Emblema geométrico central limpo
    ring_r = int(min(width, height) * 0.14)
    overlay.ellipse(
        [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
        fill=(10, 14, 24, 220),
        outline=(accent[0], accent[1], accent[2], 235),
        width=6,
    )
    outer_r = ring_r + 16
    overlay.ellipse(
        [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
        outline=(accent[0], accent[1], accent[2], 95),
        width=2,
    )

    sym_font = load_font(int(ring_r * 1.08), bold=True)
    sym = theme["symbol"]
    sb = overlay.textbbox((0, 0), sym, font=sym_font)
    sw, sh = sb[2] - sb[0], sb[3] - sb[1]
    overlay.text((cx - sw / 2 - sb[0], cy - sh / 2 - sb[1]), sym, font=sym_font, fill=accent)

    # Rótulo de categoria + número de destaque abaixo do emblema
    destaque = str(scene.get("destaque_numero") or theme["badge"]).strip().upper()
    tag_font = load_font(34 if height > width else 30, bold=True)
    tb = overlay.textbbox((0, 0), destaque, font=tag_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pill_pad_x, pill_pad_y = 30, 14
    pill_y = cy + outer_r + 36
    pill_box = [
        cx - tw / 2 - pill_pad_x,
        pill_y,
        cx + tw / 2 + pill_pad_x,
        pill_y + th + pill_pad_y * 2,
    ]
    overlay.rounded_rectangle(
        pill_box,
        radius=26,
        fill=(accent[0], accent[1], accent[2], 45),
        outline=(accent[0], accent[1], accent[2], 210),
        width=3,
    )
    overlay.text(
        (cx - tw / 2 - tb[0], pill_y + pill_pad_y - tb[1]),
        destaque,
        font=tag_font,
        fill=(248, 250, 252, 255),
    )

    # Título curto discreto acima do card de legenda
    short_title = str(scene.get("titulo_curto") or f"Parte {scene_idx}").upper()
    st_font = load_font(24, bold=True)
    stb = overlay.textbbox((0, 0), short_title, font=st_font)
    overlay.text(
        (cx - (stb[2] - stb[0]) / 2 - stb[0], pill_box[3] + 22 - stb[1]),
        short_title,
        font=st_font,
        fill=(148, 163, 184, 240),
    )

    return img.convert("RGB")


def prepare_photo_base(photo: Image.Image, width: int, height: int) -> Image.Image:
    """Prepara imagem 10% maior para zoom Ken Burns suave sem bordas."""
    scale = 1.12
    tw, th = int(width * scale), int(height * scale)
    fitted = ImageOps.fit(photo, (tw, th), method=Image.Resampling.LANCZOS)
    return fitted


CAPTION_MODES = ("screen_box", "karaoke_narration", "none")


def build_caption_overlay(
    width: int,
    height: int,
    scene: dict[str, Any],
    scene_idx: int,
    total_scenes: int,
    has_photo: bool,
    caption_mode: str = "screen_box",
) -> Image.Image:
    """Constrói overlay RGBA estático por cena (barra superior e cartão de legenda quando ativo)."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    cat = str(scene.get("categoria") or "dado")
    theme = CATEGORY_THEMES.get(cat, CATEGORY_THEMES["dado"])
    accent = theme["accent"]
    badge_text = (str(scene.get("destaque_numero") or theme["badge"]).strip() or theme["badge"]).upper()

    is_vertical = height > width
    margin_x = 46 if is_vertical else 72

    # Sombra inferior gradiente para legibilidade sobre fotos reais
    if has_photo:
        grad_top = int(height * 0.48)
        for y in range(grad_top, height):
            alpha = int(215 * ((y - grad_top) / max(1, height - grad_top)) ** 1.15)
            draw.line([(0, y), (width, y)], fill=(5, 8, 16, alpha))

        # Faixa superior sutil com indicador de cena + selo
        draw.rounded_rectangle(
            [margin_x, 44, margin_x + 330, 94],
            radius=24,
            fill=(8, 12, 22, 200),
            outline=(accent[0], accent[1], accent[2], 210),
            width=2,
        )
        top_font = load_font(22, bold=True)
        draw.text((margin_x + 20, 56), f"CENA {scene_idx}/{total_scenes}  •  {badge_text}"[:28], font=top_font, fill=(241, 245, 249, 255))
    else:
        # Barra superior minimalista no modo motion card
        top_font = load_font(22, bold=True)
        draw.text((margin_x, 48), f"CENA {scene_idx}/{total_scenes}", font=top_font, fill=(148, 163, 184, 220))

    if caption_mode != "screen_box":
        return overlay

    # Caixa de legenda grande & nítida (pensada para leitura confortável no celular)
    caption = str(scene.get("texto_tela") or scene.get("narracao") or "").strip()
    if not caption:
        return overlay
    max_text_w = width - (margin_x * 2) - 56
    font_size = 50 if is_vertical else 44
    cap_font = load_font(font_size, bold=True)
    lines = wrap_text_lines(draw, caption, cap_font, max_text_w)
    while len(lines) > 5 and font_size > 32:
        font_size -= 4
        cap_font = load_font(font_size, bold=True)
        lines = wrap_text_lines(draw, caption, cap_font, max_text_w)

    line_height = int(font_size * 1.28)
    text_block_h = max(line_height, len(lines) * line_height)
    pad_y = 34
    box_h = text_block_h + pad_y * 2
    box_bottom = height - (110 if is_vertical else 68)
    box_top = box_bottom - box_h

    draw.rounded_rectangle(
        [margin_x, box_top, width - margin_x, box_bottom],
        radius=26,
        fill=(7, 11, 20, 228),
        outline=(accent[0], accent[1], accent[2], 215),
        width=3,
    )
    # Barra vertical colorida à esquerda do card de texto
    draw.rounded_rectangle(
        [margin_x + 14, box_top + 20, margin_x + 24, box_bottom - 20],
        radius=5,
        fill=(accent[0], accent[1], accent[2], 255),
    )

    cy = box_top + pad_y
    for line in lines:
        draw.text(
            (margin_x + 44, cy),
            line,
            font=cap_font,
            fill=(248, 250, 252, 255),
        )
        cy += line_height

    return overlay


def detect_speech_window(pcm_bytes: bytes, sample_rate: int = 24000) -> tuple[float, float]:
    """Detecta início e fim reais da fala no PCM16 mono via energia RMS em janelas de 20 ms."""
    raw_sec = max(0.4, len(pcm_bytes) / float(sample_rate * 2))
    if len(pcm_bytes) < 960:
        return 0.12, max(0.35, raw_sec - 0.08)

    samples = np.frombuffer(pcm_bytes[: (len(pcm_bytes) // 2) * 2], dtype=np.int16).astype(np.float32)
    chunk_len = max(1, int(sample_rate * 0.02))  # 20 ms
    n_chunks = len(samples) // chunk_len
    if n_chunks < 2:
        return 0.12, max(0.35, raw_sec - 0.08)

    reshaped = samples[: n_chunks * chunk_len].reshape(n_chunks, chunk_len)
    rms = np.sqrt(np.mean(reshaped * reshaped, axis=1))
    peak = float(np.max(rms)) if rms.size else 0.0
    if peak < 120.0:
        return 0.15, max(0.4, raw_sec - 0.1)

    threshold = max(200.0, peak * 0.065)
    active = np.where(rms >= threshold)[0]
    if active.size == 0:
        return 0.15, max(0.4, raw_sec - 0.1)

    start_sec = max(0.04, float(active[0]) * 0.02 - 0.03)
    end_sec = min(raw_sec, float(active[-1] + 1) * 0.02 + 0.05)
    if end_sec - start_sec < 0.3:
        end_sec = min(raw_sec, start_sec + 0.45)
    return start_sec, end_sec


def compute_karaoke_timeline(
    narracao: str,
    pcm_raw: bytes,
    width: int,
    is_vertical: bool,
) -> dict[str, Any]:
    """Calcula tempos por palavra e agrupa a narração em blocos legíveis de até 2 linhas."""
    words = [w for w in narracao.strip().split() if w]
    if not words:
        return {"words": [], "chunks": []}

    speech_start, speech_end = detect_speech_window(pcm_raw)
    speech_span = max(0.3, speech_end - speech_start)

    weights: list[float] = []
    for word in words:
        core = re.sub(r"\W+", "", word)
        w_val = max(2.2, float(len(core)))
        if word.endswith((".", "!", "?", "…")):
            w_val += 2.2
        elif word.endswith((",", ";", ":", "—")):
            w_val += 1.0
        weights.append(w_val)

    total_weight = sum(weights) or float(len(words))
    word_entries: list[dict[str, Any]] = []
    acc = 0.0
    for idx, (word, w_val) in enumerate(zip(words, weights)):
        t0 = speech_start + (acc / total_weight) * speech_span
        acc += w_val
        t1 = speech_start + (acc / total_weight) * speech_span
        word_entries.append({"idx": idx, "text": word, "start": t0, "end": t1})

    font_size = 42 if is_vertical else 38
    cap_font = load_font(font_size, bold=True)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
    margin_x = 56 if is_vertical else 96
    max_line_w = width - (margin_x * 2) - 64

    chunks: list[dict[str, Any]] = []
    cur_lines: list[list[dict[str, Any]]] = [[]]
    cur_line_w = 0.0
    space_w = probe.textlength(" ", font=cap_font)

    for entry in word_entries:
        word_w = probe.textlength(entry["text"], font=cap_font)
        add_w = word_w if not cur_lines[-1] else space_w + word_w
        if cur_lines[-1] and cur_line_w + add_w > max_line_w:
            if len(cur_lines) < 2:
                cur_lines.append([entry])
                cur_line_w = word_w
            else:
                flat = [w for ln in cur_lines for w in ln]
                chunks.append({
                    "lines": cur_lines,
                    "start": flat[0]["start"],
                    "end": flat[-1]["end"],
                })
                cur_lines = [[entry]]
                cur_line_w = word_w
        else:
            cur_lines[-1].append(entry)
            cur_line_w += add_w

    if any(cur_lines):
        flat = [w for ln in cur_lines for w in ln]
        if flat:
            chunks.append({
                "lines": cur_lines,
                "start": flat[0]["start"],
                "end": flat[-1]["end"],
            })

    return {"words": word_entries, "chunks": chunks, "font_size": font_size}


def resolve_karaoke_state(karaoke_data: dict[str, Any] | None, t_sec: float) -> tuple[int, int]:
    """Retorna (chunk_idx, active_word_idx) para o instante t_sec da cena."""
    if not karaoke_data:
        return -1, -1
    chunks = karaoke_data.get("chunks") or []
    if not chunks:
        return -1, -1

    chosen_chunk_idx = len(chunks) - 1
    for idx, ch in enumerate(chunks):
        if t_sec <= ch["end"] + 0.04:
            chosen_chunk_idx = idx
            break

    chunk = chunks[chosen_chunk_idx]
    flat_words = [w for ln in chunk["lines"] for w in ln]
    active_word_idx = flat_words[-1]["idx"]
    for w in flat_words:
        if t_sec < w["end"]:
            active_word_idx = w["idx"]
            break
    return chosen_chunk_idx, active_word_idx


def build_karaoke_caption_overlay(
    width: int,
    height: int,
    chunk: dict[str, Any],
    active_word_idx: int,
    accent: tuple[int, int, int],
    is_vertical: bool,
    font_size: int = 42,
) -> Image.Image:
    """Desenha bloco de legenda sincronizada destacando a palavra falada no momento."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cap_font = load_font(font_size, bold=True)
    lines = chunk["lines"]

    line_height = int(font_size * 1.36)
    pad_y = 26
    pad_x = 34
    margin_x = 56 if is_vertical else 96
    box_w = width - (margin_x * 2)
    box_h = len(lines) * line_height + pad_y * 2
    box_bottom = height - (96 if is_vertical else 58)
    box_top = box_bottom - box_h

    draw.rounded_rectangle(
        [margin_x, box_top, margin_x + box_w, box_bottom],
        radius=24,
        fill=(7, 11, 20, 232),
        outline=(accent[0], accent[1], accent[2], 215),
        width=3,
    )

    space_w = draw.textlength(" ", font=cap_font)
    cy = box_top + pad_y
    for ln in lines:
        word_widths = [draw.textlength(w["text"], font=cap_font) for w in ln]
        total_w = sum(word_widths) + space_w * max(0, len(ln) - 1)
        cx = margin_x + max(pad_x, (box_w - total_w) / 2.0)

        for w, ww in zip(ln, word_widths):
            w_idx = w["idx"]
            if w_idx == active_word_idx:
                draw.rounded_rectangle(
                    [cx - 8, cy - 4, cx + ww + 8, cy + font_size + 10],
                    radius=12,
                    fill=(250, 204, 21, 72),
                    outline=(253, 224, 71, 210),
                    width=2,
                )
                text_color = (253, 224, 71, 255)
            elif w_idx < active_word_idx:
                text_color = (248, 250, 252, 255)
            else:
                text_color = (203, 213, 225, 220)

            draw.text((cx, cy), w["text"], font=cap_font, fill=text_color)
            cx += ww + space_w

        cy += line_height

    return overlay


def write_pcm_with_padding(pcm_bytes: bytes, target_sec: float) -> tuple[bytes, float]:
    """Garante pelo menos +0.35s de respiro e duração mínima da cena."""
    bytes_per_sec = 24000 * 2
    pad_tail = b"\x00" * int(0.35 * bytes_per_sec)
    combined = pcm_bytes + pad_tail
    min_bytes = int(max(3.5, target_sec) * bytes_per_sec)
    if len(combined) < min_bytes:
        combined += b"\x00" * (min_bytes - len(combined))
    # Alinha para frame completo (multiplo de 2 bytes)
    if len(combined) % 2 != 0:
        combined += b"\x00"
    actual_sec = len(combined) / bytes_per_sec
    return combined, actual_sec


def render_video(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or f"job-{int(time.time())}")
    titulo = str(payload.get("titulo") or "Vídeo Jarvis").strip()
    aspect_ratio = "16:9" if payload.get("aspect_ratio") == "16:9" else "9:16"
    voice = str(payload.get("voice") or "Orus")
    if voice not in VOICE_STYLES:
        voice = "Orus"
    bg_mode = str(payload.get("bg_mode") or "auto")
    caption_mode = str(payload.get("caption_mode") or "screen_box").strip()
    if caption_mode not in CAPTION_MODES:
        caption_mode = "screen_box"
    scenes = payload.get("scenes") or []
    if not scenes:
        raise ValueError("Nenhuma cena enviada para renderização.")

    width, height = (1280, 720) if aspect_ratio == "16:9" else (720, 1280)
    fps = 24
    api_key = get_api_key()
    total = len(scenes)

    write_job_status(job_id, {
        "status": "running",
        "progress": 5,
        "step": "Preparando narração e visuais...",
    })

    prepared_scenes: list[dict[str, Any]] = []
    master_pcm = bytearray()
    skip_ai_images = bg_mode == "motion_cards"

    for idx, sc in enumerate(scenes, start=1):
        pct_base = 5 + int((idx - 1) / total * 55)
        write_job_status(job_id, {
            "status": "running",
            "progress": pct_base,
            "step": f"Cena {idx}/{total}: gerando narração em português...",
        })

        narracao = str(sc.get("narracao") or sc.get("texto_tela") or "").strip()
        pcm_raw, tts_model = synthesize_tts_pcm(narracao, voice, api_key)
        min_dur = float(sc.get("duracao_seg") or 5.0)
        # Limita silêncio mínimo ao tamanho da fala ou ~4.5s para não arrastar se fala for curta
        padded_pcm, dur_sec = write_pcm_with_padding(pcm_raw, min(min_dur, 5.5))
        master_pcm.extend(padded_pcm)

        # Resolve imagem da cena: foto do usuário > AI (se disponível) > motion graphic card
        user_photo = decode_data_image(str(sc.get("imagem_base64") or ""))
        ai_photo = None
        if user_photo is None and not skip_ai_images and bg_mode in ("auto", "ai_first"):
            write_job_status(job_id, {
                "status": "running",
                "progress": pct_base + 5,
                "step": f"Cena {idx}/{total}: preparando visual...",
            })
            ai_photo, quota_zero = try_generate_ai_image(
                str(sc.get("prompt_imagem") or ""), aspect_ratio, api_key
            )
            if quota_zero:
                skip_ai_images = True

        chosen_photo = user_photo or ai_photo
        sc_caption_mode = str(sc.get("caption_mode") or caption_mode).strip()
        if sc_caption_mode not in CAPTION_MODES:
            sc_caption_mode = caption_mode
        karaoke_data = (
            compute_karaoke_timeline(narracao, pcm_raw, width, is_vertical=(aspect_ratio == "9:16"))
            if sc_caption_mode == "karaoke_narration"
            else None
        )
        prepared_scenes.append({
            "scene": sc,
            "idx": idx,
            "dur_sec": dur_sec,
            "tts_model": tts_model,
            "has_photo": chosen_photo is not None,
            "photo_base": prepare_photo_base(chosen_photo, width, height) if chosen_photo else None,
            "card_base": None if chosen_photo else build_motion_card_backdrop(width, height, sc, idx, total),
            "overlay": build_caption_overlay(
                width,
                height,
                sc,
                idx,
                total,
                has_photo=chosen_photo is not None,
                caption_mode=sc_caption_mode,
            ),
            "karaoke": karaoke_data,
        })

    total_duration = len(master_pcm) / (24000 * 2)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_filename = f"{slugify(titulo)}_{stamp}.mp4"
    out_mp4_path = OUTPUT_DIR / out_filename

    write_job_status(job_id, {
        "status": "running",
        "progress": 65,
        "step": "Renderizando vídeo MP4 em alta definição...",
    })

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = tmp_wav.name

    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(bytes(master_pcm))

        ffmpeg_cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-i",
            wav_path,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out_mp4_path),
        ]

        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None

        total_frames_all = sum(max(1, int(round(item["dur_sec"] * fps))) for item in prepared_scenes)
        frames_done = 0

        for item in prepared_scenes:
            sc_frames = max(1, int(round(item["dur_sec"] * fps)))
            overlay = item["overlay"]
            karaoke_data = item["karaoke"]
            cat = str(item["scene"].get("categoria") or "dado")
            accent = CATEGORY_THEMES.get(cat, CATEGORY_THEMES["dado"])["accent"]
            karaoke_cache: dict[tuple[int, int], Image.Image] = {}
            is_vertical = aspect_ratio == "9:16"

            def _get_karaoke_overlay(frame_idx: int) -> Image.Image | None:
                if not karaoke_data or not karaoke_data.get("chunks"):
                    return None
                c_idx, w_idx = resolve_karaoke_state(karaoke_data, frame_idx / float(fps))
                if c_idx < 0:
                    return None
                key = (c_idx, w_idx)
                cached = karaoke_cache.get(key)
                if cached is None:
                    cached = build_karaoke_caption_overlay(
                        width,
                        height,
                        karaoke_data["chunks"][c_idx],
                        w_idx,
                        accent,
                        is_vertical=is_vertical,
                        font_size=int(karaoke_data.get("font_size") or 42),
                    )
                    karaoke_cache[key] = cached
                return cached

            if item["photo_base"] is not None:
                photo_big = item["photo_base"]
                bw, bh = photo_big.size
                # Anima zoom suave 1.00 -> 1.07
                for f in range(sc_frames):
                    progress_local = f / max(1, sc_frames - 1)
                    zoom = 1.0 + 0.07 * progress_local
                    crop_w = int(bw / zoom)
                    crop_h = int(bh / zoom)
                    x0 = (bw - crop_w) // 2
                    y0 = (bh - crop_h) // 2
                    frame_rgb = photo_big.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize(
                        (width, height), Image.Resampling.BILINEAR
                    )
                    frame_rgba = Image.alpha_composite(frame_rgb.convert("RGBA"), overlay)
                    k_ov = _get_karaoke_overlay(f)
                    if k_ov is not None:
                        frame_rgba = Image.alpha_composite(frame_rgba, k_ov)
                    # Barra de progresso discreta na base
                    f_draw = ImageDraw.Draw(frame_rgba)
                    global_prog = (frames_done + 1) / max(1, total_frames_all)
                    f_draw.rectangle([0, height - 10, int(width * global_prog), height], fill=(*accent, 235))
                    proc.stdin.write(frame_rgba.convert("RGB").tobytes())
                    frames_done += 1
                    if frames_done % 48 == 0:
                        pct = 65 + int((frames_done / max(1, total_frames_all)) * 32)
                        write_job_status(job_id, {
                            "status": "running",
                            "progress": min(97, pct),
                            "step": f"Codificando cena {item['idx']}/{total}...",
                        })
            else:
                # Motion graphic card: compõe 1x o frame e atualiza barra + karaoke (se ativo)
                base_rgba = Image.alpha_composite(item["card_base"].convert("RGBA"), overlay)
                for f in range(sc_frames):
                    k_ov = _get_karaoke_overlay(f)
                    frame_copy = Image.alpha_composite(base_rgba, k_ov) if k_ov is not None else base_rgba.copy()
                    f_draw = ImageDraw.Draw(frame_copy)
                    global_prog = (frames_done + 1) / max(1, total_frames_all)
                    f_draw.rectangle([0, height - 10, int(width * global_prog), height], fill=(*accent, 235))
                    proc.stdin.write(frame_copy.convert("RGB").tobytes())
                    frames_done += 1
                    if frames_done % 72 == 0:
                        pct = 65 + int((frames_done / max(1, total_frames_all)) * 32)
                        write_job_status(job_id, {
                            "status": "running",
                            "progress": min(97, pct),
                            "step": f"Codificando cena {item['idx']}/{total}...",
                        })

        proc.stdin.close()
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg retornou erro ({ret}): {stderr_bytes.decode('utf-8', errors='ignore')}")
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass

    meta = {
        "filename": out_filename,
        "titulo": titulo,
        "aspect_ratio": aspect_ratio,
        "voice": voice,
        "caption_mode": caption_mode,
        "duration_sec": round(total_duration, 1),
        "scenes_count": total,
        "size_bytes": out_mp4_path.stat().st_size,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_path = OUTPUT_DIR / f"{out_mp4_path.stem}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    write_job_status(job_id, {
        "status": "done",
        "progress": 100,
        "step": "Vídeo pronto!",
        "output_file": out_filename,
        "meta": meta,
    })
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis Estúdio de Vídeos CLI")
    parser.add_argument("action", choices=["script", "render"])
    parser.add_argument("--input", required=True, help="Caminho para arquivo JSON de entrada")
    args = parser.parse_args()

    input_path = Path(args.input)
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    if args.action == "script":
        result = generate_script(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0

    job_id = str(payload.get("job_id") or "")
    try:
        result = render_video(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        if job_id:
            write_job_status(job_id, {
                "status": "error",
                "progress": 0,
                "step": "Falha ao gerar vídeo",
                "error": str(exc),
            })
        sys.stderr.write(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
