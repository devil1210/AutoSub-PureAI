import datetime
import json
import os
import re
from autosub.config import PROFILES_DIR
from autosub.core.metadata import fetch_anime_web_context
from autosub.core.watcher import get_parsed_watch_paths
from autosub.providers.gemini import call_ai

def extract_series_name(video_path):
    parts = os.path.normpath(video_path).split(os.sep)
    watch_dirs = [os.path.normpath(d) for d, _ in get_parsed_watch_paths()]
    for w in watch_dirs:
        w_parts = w.split(os.sep)
        if len(parts) > len(w_parts) and parts[:len(w_parts)] == w_parts:
            return parts[len(w_parts)]
    if "tvshows" in parts:
        idx = parts.index("tvshows")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    parent = os.path.basename(os.path.dirname(video_path))
    if parent.lower().startswith("season") or parent.lower().startswith("specials"):
        return os.path.basename(os.path.dirname(os.path.dirname(video_path)))
    return parent

def extract_episode_label(video_path):
    base = os.path.splitext(os.path.basename(video_path))[0]
    m = re.search(r"(\b[sS]\d{1,2}[eE]\d{1,3}\b)", base)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(?:[eE][pP]?\.?\s*)(\d{1,3})\b", base)
    if m:
        return f"Episodio {int(m.group(1)):02d}"
    m = re.search(r"(?:[-_\s\[])(\d{2,3})(?:[-_\s\]\)])", base)
    if m:
        return f"Episodio {int(m.group(1)):02d}"
    clean = re.sub(r"\[.*?\]|\(.*?\)", "", base).strip()
    return clean[:35] if clean else base[:35]

def get_or_create_series_profile(series_name, force_refresh=False):
    clean_name = re.sub(r'[\\/*?:"<>|]', "", series_name).strip()
    profile_path = os.path.join(PROFILES_DIR, f"{clean_name}.json")

    existing_profile = None
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                existing_profile = json.load(f)
                if not force_refresh:
                    return existing_profile, profile_path
        except Exception as e:
            print(f"[Profile] Error leyendo perfil existente: {e}", flush=True)

    print(f"[Profile] Generando perfil inteligente para '{clean_name}'...", flush=True)
    web_info = fetch_anime_web_context(clean_name)

    genres_str = ", ".join(web_info["genres"]) if web_info["genres"] else "Anime"
    chars_web_list = "\n".join([f"- {c['name']} ({c['role']})" for c in web_info["characters_raw"]]) or "Dedúcelos de la sinopsis o tu conocimiento."

    sys_prompt = """Eres un experto director de doblaje y adaptación de anime al español latinoamericano neutro.
Tu labor es crear un perfil de traducción exhaustivo en formato JSON estricto, sin explicaciones adicionales."""

    prompt = f"""Crea un perfil de traducción profesional para el anime/serie:
TÍTULO: {clean_name}
TÍTULO OFICIAL ENCONTRADO: {web_info['official_title']}
GÉNEROS: {genres_str}
SINOPSIS: {web_info['synopsis'][:600]}
PERSONAJES RELEVANTES:
{chars_web_list}

Genera un JSON con esta estructura exacta:
1. "series_title": "{clean_name}"
2. "official_title": Título oficial internacional
3. "setting": Resumen conciso del universo y premisa (2 a 4 líneas)
4. "characters": Lista de 5 a 12 personajes con:
   - "name": Nombre del personaje
   - "gender": "Femenino" o "Masculino"
   - "personality": Rol y forma de hablar detallada (formalidad, tuteo, muletillas, tono)
5. "glossary": Diccionario de 8 a 20 términos clave (títulos, habilidades, organizaciones, rangos) "Inglés": "Español Latino"
6. "tone_instructions": Instrucciones de tono para subtítulos (latino neutro, ritmo, modismos)

Devuelve ÚNICAMENTE el bloque JSON.
/no_think"""

    resp = call_ai(prompt, system_instruction=sys_prompt, timeout=120, allow_cloud_override=True)
    profile_data = None
    if resp:
        try:
            json_match = re.search(r"\{.*\}", resp, flags=re.DOTALL)
            if json_match:
                profile_data = json.loads(json_match.group(0))
        except Exception as e:
            print(f"[Profile] Error parseando JSON de IA: {e}", flush=True)

    if not profile_data or not isinstance(profile_data, dict):
        profile_data = existing_profile or {
            "series_title": clean_name,
            "official_title": web_info["official_title"],
            "setting": web_info["synopsis"][:300] or "Serie de TV / Anime",
            "characters": [{"name": c["name"], "gender": "Desconocido", "personality": c["role"]} for c in web_info["characters_raw"][:8]],
            "glossary": {},
            "tone_instructions": "Español latinoamericano neutro, natural y fluido.",
            "episodes": []
        }

    if existing_profile and "episodes" in existing_profile and "episodes" not in profile_data:
        profile_data["episodes"] = existing_profile["episodes"]

    try:
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, ensure_ascii=False, indent=2)
        print(f"[Profile] Guardado exitoso en {profile_path}", flush=True)
    except Exception as e:
        print(f"[Profile] Error escribiendo perfil: {e}", flush=True)

    return profile_data, profile_path

def generate_and_save_episode_context(series_name, video_path, raw_texts, translated_texts, profile_data, profile_path):
    if not translated_texts or len(translated_texts) < 5:
        return None

    ep_label = extract_episode_label(video_path)
    file_name = os.path.basename(video_path)
    total_lines = len(translated_texts)

    sample_size = min(70, total_lines)
    step = max(1, total_lines // sample_size)
    sampled = []
    has_distinct_raw = raw_texts and len(raw_texts) == total_lines and raw_texts != translated_texts

    for idx in range(0, total_lines, step):
        t_trans = translated_texts[idx].strip()
        clean_trans = re.sub(r"\{.*?\}", "", t_trans).replace("\\N", " ").replace("\\n", " ").strip()
        if has_distinct_raw:
            t_raw = raw_texts[idx].strip()
            clean_raw = re.sub(r"\{.*?\}", "", t_raw).replace("\\N", " ").replace("\\n", " ").strip()
            if clean_trans or clean_raw:
                sampled.append(f"[{idx+1}] [ORIGINAL/EN]: {clean_raw} | [ESPAÑOL]: {clean_trans}")
        else:
            if clean_trans:
                sampled.append(f"[{idx+1}] {clean_trans}")
        if len(sampled) >= sample_size:
            break

    dialogue_summary_input = "\n".join(sampled)
    known_chars = [c.get("name") for c in profile_data.get("characters", []) if c.get("name")]
    known_chars_str = ", ".join(known_chars) if known_chars else "Ninguno registrado aún."

    sys_prompt = f"""Eres un supervisor de continuidad narrativa para subtitulaje de anime y series al español latino neutro.
SERIE: {profile_data.get('series_title', series_name)} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {profile_data.get('setting', '')}
CAPÍTULO: {ep_label} ({file_name})
PERSONAJES YA REGISTRADOS EN EL PERFIL: {known_chars_str}

Tu objetivo es analizar los diálogos del capítulo y generar un JSON con:
1. "summary": Resumen conciso de continuidad narrativa (máximo 140-180 palabras) cubriendo trama principal, eventos clave, dinámicas y revelaciones.
2. "new_characters": Lista de personajes NUEVOS que debuten o hablen en este episodio y NO estén en la lista de ya registrados. Cada uno con:
   - "name": Nombre del personaje
   - "gender": "Femenino" o "Masculino" o "Desconocido"
   - "personality": Breve rol o relación
3. "updated_characters": Lista de personajes YA EXISTENTES con giros de trama importantes.
4. "new_glossary": Diccionario de términos clave nuevos introducidos en este episodio ("Inglés/Jap": "Español Latino").

Devuelve ÚNICAMENTE un objeto JSON válido con las claves: "summary", "new_characters", "updated_characters", "new_glossary"."""

    prompt = f"""Analiza estos diálogos del capítulo '{ep_label}':
--- DIÁLOGOS ---
{dialogue_summary_input}
--- FIN ---

Genera el JSON con el resumen, nuevos personajes, personajes con giros de trama actualizados y nuevos términos:
/no_think"""

    print(f"[Continuity] Analizando continuidad, personajes y giros de trama para {series_name} - {ep_label}...", flush=True)
    raw_resp = call_ai(prompt, system_instruction=sys_prompt, timeout=90)

    summary_text = ""
    new_chars = []
    updated_chars = []
    new_glossary = {}

    if raw_resp:
        try:
            m = re.search(r"\{.*\}", raw_resp, flags=re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                summary_text = parsed.get("summary", "").strip()
                new_chars = parsed.get("new_characters", [])
                updated_chars = parsed.get("updated_characters", [])
                new_glossary = parsed.get("new_glossary", {})
        except Exception:
            pass

    if not summary_text or len(summary_text) < 15:
        if raw_resp and len(raw_resp.strip()) >= 15 and not raw_resp.strip().startswith("{"):
            summary_text = raw_resp.strip()
        else:
            summary_text = f"Capítulo procesado con {total_lines} líneas de diálogo."

    if "characters" not in profile_data or not isinstance(profile_data["characters"], list):
        profile_data["characters"] = []

    existing_char_names_lower = {
        c.get("name", "").strip().lower() for c in profile_data["characters"] if c.get("name")
    }

    added_chars_count = 0
    if isinstance(new_chars, list):
        for nc in new_chars:
            if not isinstance(nc, dict):
                continue
            c_name = str(nc.get("name", "")).strip()
            if not c_name or len(c_name) < 2:
                continue
            if c_name.lower() not in existing_char_names_lower:
                gender = nc.get("gender", "Desconocido")
                if gender not in ["Femenino", "Masculino", "Desconocido"]:
                    gender = "Desconocido"
                pers = str(nc.get("personality", f"Introducido en {ep_label}")).strip()
                profile_data["characters"].append({
                    "name": c_name,
                    "gender": gender,
                    "personality": pers
                })
                existing_char_names_lower.add(c_name.lower())
                added_chars_count += 1
                print(f"[Continuity] 👤 Nuevo personaje añadido a '{series_name}': {c_name} [{gender}] - {pers}", flush=True)

    if isinstance(updated_chars, list):
        for uc in updated_chars:
            if not isinstance(uc, dict):
                continue
            u_name = str(uc.get("name", "")).strip()
            new_pers = str(uc.get("new_personality", "")).strip()
            reason = str(uc.get("reason", "")).strip()
            if not u_name or not new_pers:
                continue
            for char_obj in profile_data["characters"]:
                if char_obj.get("name", "").strip().lower() == u_name.lower():
                    old_pers = char_obj.get("personality", "")
                    reason_suffix = f" ({reason})" if reason else ""
                    char_obj["personality"] = f"{new_pers}{reason_suffix}"
                    print(f"[Continuity] 🔄 Giro de trama en '{u_name}': '{old_pers}' -> '{char_obj['personality']}'", flush=True)
                    break

    if "glossary" not in profile_data or not isinstance(profile_data["glossary"], dict):
        profile_data["glossary"] = {}

    existing_glossary_lower = {k.strip().lower() for k in profile_data["glossary"]}
    added_terms_count = 0
    if isinstance(new_glossary, dict):
        for gk, gv in new_glossary.items():
            gk_str = str(gk).strip()
            gv_str = str(gv).strip()
            if not gk_str or not gv_str:
                continue
            if gk_str.lower() == gv_str.lower():
                continue
            if len(gv_str) > 50 or "\n" in gv_str:
                continue
            if gk_str.lower() not in existing_glossary_lower:
                profile_data["glossary"][gk_str] = gv_str
                existing_glossary_lower.add(gk_str.lower())
                added_terms_count += 1
                print(f"[Continuity] 📖 Nuevo término añadido a '{series_name}': {gk_str} -> {gv_str}", flush=True)

    ep_record = {
        "episode": ep_label,
        "file_name": file_name,
        "summary": summary_text,
        "lines": total_lines,
        "new_characters": [c.get("name") for c in new_chars if isinstance(c, dict) and c.get("name")],
        "updated_characters": [c.get("name") for c in updated_chars if isinstance(c, dict) and c.get("name")],
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    if "episodes" not in profile_data or not isinstance(profile_data["episodes"], list):
        profile_data["episodes"] = []

    found = False
    for i, existing in enumerate(profile_data["episodes"]):
        if existing.get("episode") == ep_label or existing.get("file_name") == file_name:
            profile_data["episodes"][i] = ep_record
            found = True
            break
    if not found:
        profile_data["episodes"].append(ep_record)

    try:
        if profile_path and os.path.exists(os.path.dirname(profile_path)):
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, ensure_ascii=False, indent=2)
            print(f"[Continuity] Perfil actualizado (nuevos personajes: {added_chars_count}, nuevos términos: {added_terms_count}) en {profile_path}", flush=True)
    except Exception as e:
        print(f"[Continuity] Error guardando contexto en perfil: {e}", flush=True)

    return ep_record

def estimate_tokens(text: str) -> int:
    """Estimación conservadora de tokens para LLMs multilingües (~3.8 caracteres por token)."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.8))

def build_scene_brief(profile_data: dict, raw_batch_texts: list[str], prev_context: list[str] = None, target_lang: str = "es-419", max_tokens: int = 350) -> str:
    """
    Construye un Context Packet conciso (The Elephant) acotado estrictamente a <= max_tokens.
    Incluye:
    1. Título y ambientación concisa (1-2 líneas).
    2. Roster de personajes con género y registro de habla (~150 tokens).
    3. Términos de glosario filtrados a los que realmente ocurren en raw_batch_texts.
    4. Hasta 2 líneas previas de diálogo para coherencia conversacional.
    Aplica jerarquía de truncamiento estricta si excede max_tokens.
    """
    if not profile_data:
        profile_data = {}

    series_title = profile_data.get("series_title", "Serie")
    official_title = profile_data.get("official_title", "")
    title_line = f"SERIE: {series_title}" + (f" ({official_title})" if official_title and official_title != series_title else "")
    setting = (profile_data.get("setting", "") or "Anime").strip()
    if len(setting) > 120:
        setting = setting[:117] + "..."
    setting_line = f"AMBIENTACIÓN: {setting}"

    # 1. Glosario filtrado
    glossary = profile_data.get("glossary", {})
    matched_glossary = {}
    if glossary and raw_batch_texts:
        batch_corpus = " ".join(raw_batch_texts).lower()
        for k, v in glossary.items():
            if k.lower() in batch_corpus:
                matched_glossary[k] = v

    if matched_glossary:
        glossary_items = [f"'{k}' -> '{v}'" for k, v in matched_glossary.items()]
        glossary_line = "GLOSARIO ESCENA: " + ", ".join(glossary_items)
    else:
        glossary_line = ""

    # 2. Contexto previo (hasta 2 líneas limpias)
    prev_lines_block = ""
    if prev_context:
        clean_prev = []
        for p in prev_context[-2:]:
            cp = re.sub(r"\{.*?\}", "", str(p)).replace("\\N", " ").strip()
            if cp:
                clean_prev.append(f"- {cp[:80]}")
        if clean_prev:
            prev_lines_block = "LÍNEAS PREVIAS (REFERENCIA):\n" + "\n".join(clean_prev)

    # 3. Personajes (Roster)
    chars = profile_data.get("characters", [])
    char_lines = []
    for c in chars[:10]:
        c_name = c.get("name", "").strip()
        if not c_name:
            continue
        gender = c.get("gender", "")
        pers = c.get("personality", "").strip()
        if len(pers) > 70:
            pers = pers[:67] + "..."
        g_tag = f" [{gender}]" if gender and gender != "Desconocido" else ""
        char_lines.append(f"- {c_name}{g_tag}: {pers}" if pers else f"- {c_name}{g_tag}")

    chars_block = "PERSONAJES:\n" + "\n".join(char_lines) if char_lines else ""

    # Ensamblar brief preliminar
    sections = [s for s in [title_line, setting_line, chars_block, glossary_line, prev_lines_block] if s]
    brief = "\n".join(sections)

    # Jerarquía de truncamiento si excede max_tokens
    if estimate_tokens(brief) > max_tokens:
        # Paso 1: Eliminar líneas previas de referencia
        sections = [s for s in [title_line, setting_line, chars_block, glossary_line] if s]
        brief = "\n".join(sections)

    if estimate_tokens(brief) > max_tokens:
        # Paso 2: Compactar personajes (solo nombres y géneros, sin descripción larga)
        compact_chars = []
        for c in chars[:8]:
            c_name = c.get("name", "").strip()
            if not c_name:
                continue
            gender = c.get("gender", "")
            g_tag = f" [{gender}]" if gender and gender != "Desconocido" else ""
            compact_chars.append(f"- {c_name}{g_tag}")
        chars_block = "PERSONAJES:\n" + "\n".join(compact_chars) if compact_chars else ""
        sections = [s for s in [title_line, setting_line, chars_block, glossary_line] if s]
        brief = "\n".join(sections)

    if estimate_tokens(brief) > max_tokens and len(char_lines) > 5:
        # Paso 3: Reducir personajes a los 4 principales
        chars_block = "PERSONAJES:\n" + "\n".join(compact_chars[:4])
        sections = [s for s in [title_line, chars_block, glossary_line] if s]
        brief = "\n".join(sections)

    return brief
