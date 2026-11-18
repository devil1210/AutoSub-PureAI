import json
import os
import re
import time
from typing import Dict, List, Any, Optional

from autosub.config import app_config
from autosub.providers.gemini import call_ai

def get_speaker_cache_path(video_path: str) -> str:
    """Retorna la ruta del archivo de caché de hablantes para un archivo de video."""
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    speakers_dir = os.path.join(base_dir, ".speakers")
    return os.path.join(speakers_dir, f"{base_name}.speakers.json")

def load_cached_speakers(video_path: str, expected_count: int) -> Optional[List[str]]:
    """Carga el mapa de hablantes en caché si existe y coincide con la cantidad esperada."""
    cache_file = get_speaker_cache_path(video_path)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                speakers = data.get("speakers", [])
                if isinstance(speakers, list) and len(speakers) == expected_count:
                    valid_count = sum(1 for s in speakers if s and str(s).strip())
                    # Descartar caché corrupta si no tiene al menos un porcentaje mínimo de hablantes reales (> 20%)
                    if valid_count >= max(1, int(expected_count * 0.2)):
                        return speakers
                    print(f"[Diarization] ⚠️ Caché descartada: insuficiente identificación ({valid_count}/{expected_count}). Re-diarizando con IA...", flush=True)
        except Exception as e:
            print(f"[Diarization] Error leyendo caché de hablantes {cache_file}: {e}", flush=True)
    return None

def save_cached_speakers(video_path: str, speakers: List[str]) -> bool:
    """Guarda el mapa de hablantes en caché persistente en disco solo si es válido."""
    if not speakers:
        return False
    valid_count = sum(1 for s in speakers if s and str(s).strip())
    if valid_count < max(1, int(len(speakers) * 0.15)):
        print(f"[Diarization] ⚠️ No se guarda en caché: hablantes identificados insuficientes ({valid_count}/{len(speakers)}).", flush=True)
        return False
    cache_file = get_speaker_cache_path(video_path)
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "count": len(speakers),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "speakers": speakers
            }, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[Diarization] Error guardando caché de hablantes {cache_file}: {e}", flush=True)
        return False

def build_character_roster_summary(profile_data: Dict[str, Any]) -> str:
    """Construye un resumen conciso de personajes para el prompt de diarización."""
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
        g_info = f" ({gender})" if gender else ""
        p_info = f": {pers[:70]}" if pers else ""
        lines.append(f"- {name}{g_info}{p_info}")
    return "\n".join(lines)

def attribute_dialogue_chunk(
    chunk_texts: List[str],
    series_title: str,
    roster_summary: str,
    chunk_offset: int = 0,
    model_name: str = "gemini-3.5-flash-lite"
) -> List[str]:
    """
    Atribuye el personaje hablante para el guion completo (o bloque grande) usando Gemini 4 Hydrogen.
    Utiliza indexación explícita [N] Nombre para evitar cualquier desfase por omisión.
    """
    count = len(chunk_texts)
    if count == 0:
        return []

    numbered_lines = []
    for i, t in enumerate(chunk_texts):
        clean_t = re.sub(r"\{.*?\}", "", t).replace("\\N", " ").replace("\\n", " ").strip()
        numbered_lines.append(f"[{chunk_offset + i + 1}] {clean_t}")
    lines_block = "\n".join(numbered_lines)

    first_no = chunk_offset + 1
    last_no = chunk_offset + count

    sys_prompt = f"""Eres un supervisor editorial experto en subtitulaje de anime y atribución de hablantes (speaker diarization).
SERIE: {series_title}

ELENCO DE PERSONAJES:
{roster_summary}
- Extras / Multitud / Narrador / Canción

Tu tarea es identificar qué personaje dice cada línea de diálogo a partir del contexto continuo de toda la conversación del episodio.
Reglas:
1. Usa el nombre exacto del personaje según el elenco cuando sea posible.
2. Si es un personaje secundario o multitud, usa 'Extra', 'Multitud' o 'Narrador'.
3. Si la línea es canción o música, usa 'Canción' o déjalo vacío.
4. Si la línea no es diálogo (es un cartel, título o sonido ambiente), responde vacío.
5. Responde estrictamente línea por línea:
[{first_no}] Nombre_Personaje
[{first_no + 1}] Nombre_Personaje
...
[{last_no}] Nombre_Personaje
NO agregues explicaciones, notas ni saludos.
/no_think"""

    user_prompt = f"Identifica el hablante para las líneas [{first_no}] a [{last_no}]:\n{lines_block}"

    for attempt in range(2):
        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=90, allow_cloud_override=True, preferred_model=model_name)
        if resp:
            parsed = {}
            for line in resp.strip().split("\n"):
                clean_l = line.strip()
                if not clean_l:
                    continue
                # Soporta tanto [1] Nombre como rangos [1-5] Nombre o **[1-5] Nombre**
                m = re.search(r"\[?(\d+)(?:\s*[-–]\s*(\d+))?\]?[\.\:\)]?\s*(.*)$", clean_l)
                if m:
                    start_no = int(m.group(1))
                    end_no = int(m.group(2)) if m.group(2) else start_no
                    raw_spk = m.group(3).strip()
                    raw_spk = re.sub(r"\(.*?\)", "", raw_spk).strip()
                    raw_spk = re.sub(r"^\*+|\*+$", "", raw_spk).strip()
                    raw_spk = re.sub(r"^.*?:\s*", "", raw_spk).strip()
                    raw_spk = re.sub(r"^[-–—]\s*", "", raw_spk).strip()
                    if raw_spk.lower() in ["ninguno", "n/a", "none", "-", "cartel", "letrero", "título", "titulo"]:
                        raw_spk = ""

                    for line_no in range(start_no, end_no + 1):
                        rel_idx = line_no - chunk_offset - 1
                        if 0 <= rel_idx < count:
                            parsed[rel_idx] = raw_spk

            # Si el modelo respondió en JSON fallback
            if len(parsed) < count * 0.4:
                try:
                    m_json = re.search(r"\{.*\}", resp, flags=re.DOTALL)
                    if m_json:
                        data = json.loads(m_json.group(0))
                        spks = data.get("speakers", [])
                        if isinstance(spks, list):
                            for idx, s in enumerate(spks[:count]):
                                if idx not in parsed or not parsed[idx]:
                                    parsed[idx] = str(s).strip() if s else ""
                except Exception:
                    pass

            if len(parsed) >= count * 0.4:
                return [parsed.get(i, "") for i in range(count)]
        time.sleep(2)

    # Fallback si la llamada no responde: lista vacía
    return ["" for _ in range(count)]

def attribute_episode_speakers(
    video_path: str,
    raw_texts: List[str],
    profile_data: Dict[str, Any]
) -> List[str]:
    """
    Ejecuta o recupera de caché la atribución integral de personajes para un episodio.
    Envía el guion completo de corrido en 1 sola llamada (chunk_size=450) para máxima precisión
    y mínimo consumo de cuota de The Elephant.
    """
    total = len(raw_texts)
    if total == 0:
        return []

    # 1. Verificar caché
    cached = load_cached_speakers(video_path, total)
    if cached is not None:
        valid_count = sum(1 for s in cached if s)
        print(f"[Diarization] Mapa de hablantes cargado de caché ({valid_count}/{total} identificados).", flush=True)
        return cached

    series_title = profile_data.get("series_title", "Anime") if profile_data else "Anime"
    roster_summary = build_character_roster_summary(profile_data)
    diarization_model = app_config.get("diarization_model", "gemini-3.5-flash-lite")

    print(f"[Diarization] Identificando hablantes para {total} líneas en llamada global con {diarization_model}...", flush=True)

    # Enviar en bloques de ~200 líneas para máxima estabilidad y evitar timeouts
    chunk_size = 200
    all_speakers = []

    for start_idx in range(0, total, chunk_size):
        chunk = raw_texts[start_idx:start_idx + chunk_size]
        chunk_speakers = attribute_dialogue_chunk(
            chunk,
            series_title=series_title,
            roster_summary=roster_summary,
            chunk_offset=start_idx,
            model_name=diarization_model
        )
        all_speakers.extend(chunk_speakers)

    # Asegurar longitud exacta
    if len(all_speakers) < total:
        all_speakers.extend([""] * (total - len(all_speakers)))
    elif len(all_speakers) > total:
        all_speakers = all_speakers[:total]

    valid_identified = sum(1 for s in all_speakers if s)
    pct = round((valid_identified / total) * 100, 1) if total > 0 else 0.0
    print(f"[Diarization] Hablantes asignados: {valid_identified}/{total} líneas ({pct}%).", flush=True)

    # 3. Guardar en caché
    save_cached_speakers(video_path, all_speakers)

    return all_speakers
