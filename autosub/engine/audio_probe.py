import json
import os
import re
import subprocess
import time
from typing import Dict, List, Any, Optional, Tuple

from autosub.config import app_config
from autosub.providers.gemini import call_gemini_audio

# Patrones en inglés con fuerte ambigüedad de género en primera persona
GENDER_AMBIGUOUS_PATTERNS = [
    r"\b(i'?m|i am|i was|i feel)\s+(ready|glad|tired|surprised|happy|proud|alone|worried|sure|sorry|grateful|shocked|confused|impressed|exhausted|honored|relieved|interested|disappointed)\b",
    r"\b(being honest|to be honest|honestly speaking)\b",
    r"\b(none of you|all of you|both of you)\b",
    r"\b(welcome back|thank you very much|excuse me|pardon me)\b",
    r"\b(master|sensei|sir|lady|maid|servant|demon)\b",
    r"\b(i'?ll do my best|i did it|it'?s my fault)\b"
]

def is_audio_spotcheck_enabled() -> bool:
    """Verifica si el spot-check quirúrgico de audio está habilitado en la configuración."""
    return bool(app_config.get("audio_spotcheck_enabled", True))

def extract_audio_slice(video_path: str, start_sec: float, end_sec: float, padding: float = 0.25) -> Optional[bytes]:
    """
    Corta un fragmento exacto de audio directamente a memoria (stdout) vía FFmpeg en formato WAV (16kHz mono).
    No escribe archivos temporales en disco. Tarda ~0.1 - 0.2 segundos.
    """
    if not os.path.isfile(video_path):
        return None

    # Ajuste de bordes para no cortar inicios/finales de palabras
    pad_start = max(0.0, start_sec - padding)
    pad_end = end_sec + padding
    duration = max(1.0, pad_end - pad_start)
    if duration > 10.0:
        duration = 10.0  # Limitar a máximo 10s para ahorrar tokens y tiempo

    start_str = f"{pad_start:.3f}"
    dur_str = f"{duration:.3f}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", start_str,
        "-t", dur_str,
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        "-"
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
        if res.returncode == 0 and len(res.stdout) > 2000:
            return res.stdout
    except Exception as e:
        print(f"[Audio Probe] ⚠️ Error ejecutando FFmpeg para extraer slice ({start_str}s -> {dur_str}s): {e}", flush=True)

    return None

def build_roster_summary_for_audio(profile_data: Dict[str, Any]) -> str:
    """Construye un resumen conciso de personajes con su género y rango de voz para el prompt multimodal."""
    if not profile_data:
        return "Personajes principales de la serie."

    chars = profile_data.get("characters", [])
    if not chars:
        return "Personajes principales de la serie."

    lines = []
    for c in chars:
        name = c.get("name", "")
        gender = c.get("gender", "")
        pers = c.get("personality", "")
        g_str = f" ({gender})" if gender else ""
        desc_str = f": {pers[:60]}" if pers else ""
        lines.append(f"- {name}{g_str}{desc_str}")
    return "\n".join(lines)

def detect_ambiguous_cues(
    raw_texts: List[str],
    speakers: List[str],
    max_count: int = 20
) -> List[int]:
    """
    Identifica de forma heurística las líneas con mayor riesgo de error de género o contexto
    que ameritan una comprobación auditiva quirúrgica contra el audio japonés.
    """
    scored_cues = []
    total = len(raw_texts)

    for i in range(total):
        txt = raw_texts[i].strip()
        spk = speakers[i].strip() if i < len(speakers) else ""
        score = 0
        clean_txt = re.sub(r"\{.*?\}", "", txt).replace("\\N", " ").strip()

        # Criterio 1: Palabras o construcciones ambiguas de género en primera persona
        for pat in GENDER_AMBIGUOUS_PATTERNS:
            if re.search(pat, clean_txt, re.IGNORECASE):
                score += 50
                break

        # Criterio 2: Hablante no identificado o genérico ("Extra", "Desconocido")
        if not spk or spk.lower() in ["extra", "desconocido", "unknown", "hombre", "mujer", "persona", ""]:
            score += 30

        # Criterio 3: Diálogo corto pero significativo (exclamaciones / respuestas directas)
        word_count = len(clean_txt.split())
        if 1 <= word_count <= 4 and score > 0:
            score += 20

        if score >= 40:
            scored_cues.append((score, i))

    # Ordenar de mayor prioridad a menor y recortar a max_count
    scored_cues.sort(key=lambda x: x[0], reverse=True)
    selected = [idx for _, idx in scored_cues[:max_count]]
    selected.sort()
    return selected

def probe_cue_ground_truth(
    video_path: str,
    start_sec: float,
    end_sec: float,
    cue_text: str,
    series_title: str,
    roster_summary: str,
    model_name: str = None
) -> Optional[Dict[str, Any]]:
    """
    Extrae el fragmento de audio y consulta a Gemini multimodal (gemini-3.5-flash-lite)
    para obtener el ground-truth del hablante, género y transcripción en japonés.
    """
    audio_bytes = extract_audio_slice(video_path, start_sec, end_sec)
    if not audio_bytes:
        return None

    clean_cue = re.sub(r"\{.*?\}", "", cue_text).replace("\\N", " ").strip()

    prompt = f"""Escucha atentamente este fragmento de audio en japonés de anime.
SERIE: {series_title}
TEXTO EN SUBTÍTULO (referencia en inglés): "{clean_cue}"

ELENCO DE PERSONAJES:
{roster_summary}

Determina con precisión quién habla, su género y lo que realmente se escucha en japonés.
Responde estrictamente en JSON con la siguiente estructura:
{{
  "speaker_name": "Nombre exacto del personaje según el elenco (o 'Extra')",
  "speaker_gender": "masculino | femenino | indeterminado",
  "transcript_ja": "transcripción de lo que dice en japonés",
  "translation_es": "traducción fidedigna al español latino neutro adaptada al género y tono",
  "confidence": "alta | media | baja"
}}"""

    model = model_name or app_config.get("audio_spotcheck_model", "gemini-3.5-flash-lite")
    resp = call_gemini_audio(audio_bytes, prompt, preferred_model=model, response_schema_json=True, timeout=25)
    if not resp:
        return None

    try:
        m = re.search(r"\{.*\}", resp, flags=re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            return data
    except Exception as e:
        print(f"[Audio Probe] ⚠️ Error parseando respuesta JSON de audio: {e}", flush=True)

    return None

def refine_dialogue_with_audio_spotcheck(
    video_path: str,
    cues_timestamps: List[Tuple[float, float]],
    raw_texts: List[str],
    speakers: List[str],
    profile_data: Dict[str, Any],
    max_spotchecks: int = None
) -> Tuple[List[str], Dict[int, Dict[str, Any]]]:
    """
    Ejecuta el spot-check auditivo quirúrgico sobre las líneas de mayor ambigüedad.
    Actualiza la lista de hablantes identificados y genera un mapa de notas de ground-truth
    para alimentar al motor de traducción (The Goldfish).
    """
    if not is_audio_spotcheck_enabled() or not os.path.isfile(video_path):
        return speakers, {}

    max_cues = max_spotchecks or int(app_config.get("audio_spotcheck_max_per_episode", 20))
    ambiguous_indices = detect_ambiguous_cues(raw_texts, speakers, max_count=max_cues)

    if not ambiguous_indices:
        print("[Audio Probe] 🎧 No se detectaron líneas con ambigüedad crítica de género/hablante.", flush=True)
        return speakers, {}

    series_title = profile_data.get("series_title", "Anime") if profile_data else "Anime"
    roster_summary = build_roster_summary_for_audio(profile_data)
    updated_speakers = list(speakers)
    ground_truths = {}

    print(f"[Audio Probe] 🎧 Iniciando verificación quirúrgica de audio para {len(ambiguous_indices)} líneas dudosas con gemini-3.5-flash-lite...", flush=True)

    success_count = 0
    for idx in ambiguous_indices:
        if idx >= len(cues_timestamps) or idx >= len(raw_texts):
            continue

        start_sec, end_sec = cues_timestamps[idx]
        cue_text = raw_texts[idx]

        res = probe_cue_ground_truth(
            video_path,
            start_sec,
            end_sec,
            cue_text,
            series_title,
            roster_summary
        )

        if res and isinstance(res, dict):
            spk_name = res.get("speaker_name", "").strip()
            spk_gender = res.get("speaker_gender", "").strip().lower()
            ja_text = res.get("transcript_ja", "").strip()
            es_trans = res.get("translation_es", "").strip()

            # Si identificó un personaje válido del elenco o un género claro
            if spk_name and spk_name.lower() not in ["extra", "desconocido", "indeterminado", ""]:
                updated_speakers[idx] = spk_name
            elif spk_gender in ["masculino", "femenino"] and not updated_speakers[idx]:
                updated_speakers[idx] = f"Voz {spk_gender.capitalize()}"

            ground_truths[idx] = {
                "speaker": updated_speakers[idx],
                "gender": spk_gender,
                "ja": ja_text,
                "suggested_es": es_trans
            }
            success_count += 1
            print(f"  [Audio Probe Línea #{idx+1}] 🎙️ Hablante: {updated_speakers[idx]} ({spk_gender}) | JA: 「{ja_text}」 -> ES: «{es_trans}»", flush=True)

        # Pausa suave de 0.5s para respetar cuota RPM
        time.sleep(0.5)

    print(f"[Audio Probe] 🎧 Verificación completada: {success_count}/{len(ambiguous_indices)} líneas afinadas con audio real.", flush=True)
    return updated_speakers, ground_truths
