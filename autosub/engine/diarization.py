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
                    return speakers
        except Exception as e:
            print(f"[Diarization] Error leyendo caché de hablantes {cache_file}: {e}", flush=True)
    return None

def save_cached_speakers(video_path: str, speakers: List[str]) -> bool:
    """Guarda el mapa de hablantes en caché persistente en disco."""
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
    for c in chars[:12]:
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
    model_name: str = "gemini-4-hydrogen"
) -> List[str]:
    """
    Atribuye el personaje hablante para un bloque de 25-40 líneas usando Gemini 4 Hydrogen.
    Devuelve lista de nombres de longitud idéntica a chunk_texts.
    """
    count = len(chunk_texts)
    if count == 0:
        return []

    numbered_lines = []
    for i, t in enumerate(chunk_texts):
        clean_t = re.sub(r"\{.*?\}", "", t).replace("\\N", " ").replace("\\n", " ").strip()
        numbered_lines.append(f"[{i + 1}] {clean_t}")
    lines_block = "\n".join(numbered_lines)

    sys_prompt = f"""Eres un experto supervisor editorial de guiones de anime y atribución de hablantes (speaker diarization).
SERIE: {series_title}

ELENCO DE PERSONAJES:
{roster_summary}
- Extras / Multitud / Narrador

Tu tarea es identificar qué personaje dice cada línea de diálogo a partir del contexto de la conversación.
Reglas:
1. Usa el nombre exacto del personaje según el elenco cuando sea posible (ej: 'Nia Liston', 'Rinokis', 'Neil', 'Leliared', 'Hilde', 'Sanowil').
2. Si es un personaje secundario o multitud, usa 'Extra', 'Multitud' o 'Narrador'.
3. Si tienes duda razonable, usa el nombre más probable o déjalo vacío.
4. Responde ÚNICAMENTE en formato JSON estricto con un arreglo 'speakers':
{{"speakers": ["Personaje_1", "Personaje_2", ...]}}
El arreglo 'speakers' DEBE tener EXACTAMENTE {count} elementos correspondientes a las líneas [1] a [{count}].
/no_think"""

    user_prompt = f"Identifica el hablante para cada una de las {count} líneas:\n{lines_block}"

    for attempt in range(2):
        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=60, allow_cloud_override=True, preferred_model=model_name)
        if resp:
            try:
                m = re.search(r"\{.*\}", resp, flags=re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    spks = data.get("speakers", [])
                    if isinstance(spks, list) and len(spks) == count:
                        return [str(s).strip() if s else "" for s in spks]
                    elif isinstance(spks, list) and len(spks) > 0:
                        # Si difiere ligeramente en conteo, rellenar o truncar
                        res = [str(s).strip() if s else "" for s in spks[:count]]
                        while len(res) < count:
                            res.append("")
                        return res
            except Exception:
                pass
        time.sleep(1)

    # Fallback si la llamada no responde: lista vacía
    return ["" for _ in range(count)]

def attribute_episode_speakers(
    video_path: str,
    raw_texts: List[str],
    profile_data: Dict[str, Any]
) -> List[str]:
    """
    Ejecuta o recupera de caché la atribución integral de personajes para un episodio.
    Devuelve lista de nombres de personajes con len == len(raw_texts).
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
    elephant_model = app_config.get("elephant_model", "gemini-4-hydrogen")

    print(f"[Diarization] Identificando hablantes para {total} líneas con {elephant_model}...", flush=True)

    chunk_size = 35
    all_speakers = []

    for start_idx in range(0, total, chunk_size):
        chunk = raw_texts[start_idx:start_idx + chunk_size]
        chunk_speakers = attribute_dialogue_chunk(
            chunk,
            series_title=series_title,
            roster_summary=roster_summary,
            chunk_offset=start_idx,
            model_name=elephant_model
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
