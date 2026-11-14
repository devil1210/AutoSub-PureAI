import re
import time
from autosub.config import SUPPORTED_TARGET_LANGUAGES
from autosub.engine.elephant import build_scene_brief
from autosub.providers.gemini import call_ai
from autosub.subtitles.parser import mask_ass_tags, unmask_ass_tags

def calculate_cps(text: str, duration_sec: float) -> float:
    """Calcula los Caracteres Por Segundo (CPS) de una línea de subtítulo, ignorando etiquetas y saltos."""
    if duration_sec <= 0:
        return 0.0
    clean = re.sub(r"\{.*?\}|<T\d+>", "", text).replace("\\N", " ").replace("\\n", " ").strip()
    return len(clean) / duration_sec

def ass_timestamp_to_seconds(ts: str) -> float:
    """Convierte un timestamp ASS (h:mm:ss.cs) a segundos."""
    try:
        parts = ts.strip().split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0

def srt_timestamp_to_seconds(ts: str) -> float:
    """Convierte un timestamp SRT (hh:mm:ss,ms) a segundos."""
    try:
        ts = ts.strip().replace(",", ".")
        parts = ts.split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0

def smart_boundary_slicing(items: list, min_chunk: int = 20, max_chunk: int = 35) -> list[list]:
    """
    Segmenta una lista de subtítulos (cues, diccionarios o cadenas) en lotes atómicos de 20 a 35 ítems.
    Busca límites naturales:
    1. Pausas en el diálogo entre subtítulos >= 2.0 segundos.
    2. Puntuación de cierre oracional (. ! ? 。 ！ ？ …).
    Si se alcanza max_chunk sin encontrar límite natural, corta estrictamente.
    """
    if not items:
        return []
    if len(items) <= max_chunk:
        return [list(items)]

    slices = []
    current_slice = []

    def get_text_and_timing(item):
        if hasattr(item, "raw_text") and hasattr(item, "start_time"):
            start = ass_timestamp_to_seconds(item.start_time)
            end = ass_timestamp_to_seconds(item.end_time)
            return item.raw_text, start, end
        elif isinstance(item, dict):
            text = "\\N".join(item.get("lines", [])) if "lines" in item else item.get("text", "")
            time_str = item.get("time", "")
            start, end = 0.0, 0.0
            if "-->" in time_str:
                parts = time_str.split("-->")
                start = srt_timestamp_to_seconds(parts[0])
                end = srt_timestamp_to_seconds(parts[1])
            return text, start, end
        else:
            return str(item), 0.0, 0.0

    i = 0
    while i < len(items):
        item = items[i]
        current_slice.append(item)
        remaining = len(items) - (i + 1)

        if len(current_slice) >= min_chunk:
            is_max = len(current_slice) >= max_chunk

            curr_text, _, curr_end = get_text_and_timing(item)
            clean_text = re.sub(r"\{.*?\}|<T\d+>", "", curr_text).strip()
            ends_with_punc = bool(re.search(r"[\.!\?。！？…]+[\"'\)\]\s]*$", clean_text))

            has_pause = False
            if i + 1 < len(items):
                _, next_start, _ = get_text_and_timing(items[i + 1])
                if next_start > 0 and curr_end > 0 and (next_start - curr_end) >= 2.0:
                    has_pause = True

            if is_max or (ends_with_punc or has_pause):
                if remaining == 0 or remaining >= (min_chunk // 2):
                    slices.append(current_slice)
                    current_slice = []
        i += 1

    if current_slice:
        slices.append(current_slice)

    return slices

def tier1_validate_batch(raw_texts: list[str], translated_texts: list[str], tag_maps: list[dict[str, str]] = None, durations: list[float] = None) -> list[dict]:
    """
    Tier 1: Validador Determinista Instantáneo en Python (0 ms, 0 tokens).
    Verifica:
    1. Invariante 1:1 de conteo de líneas.
    2. Preservación intacta de tokens de etiqueta AST (<T0>, <T1>, ...).
    3. Balance estricto de llaves ({ y }).
    4. Cálculo de CPS (límite 20 CPS para >1.5s, 25 CPS para <=1.5s).
    Retorna lista de defectos encontrados (vacía si es 100% válido).
    """
    defects = []
    if len(raw_texts) != len(translated_texts):
        defects.append({
            "line": -1,
            "type": "COUNT_MISMATCH",
            "detail": f"Conteo de líneas no coincide: esperadas {len(raw_texts)}, obtenidas {len(translated_texts)}"
        })
        return defects

    for idx, (raw, trans) in enumerate(zip(raw_texts, translated_texts)):
        # 1. Preservación de tokens de etiqueta AST <T#>
        if tag_maps and idx < len(tag_maps) and tag_maps[idx]:
            for tag_token in tag_maps[idx].keys():
                if tag_token not in trans:
                    defects.append({
                        "line": idx,
                        "type": "TAG_MISSING",
                        "detail": f"Falta etiqueta enmascarada {tag_token} en la traducción"
                    })

        # 2. Balance estricto de llaves
        if trans.count("{") != trans.count("}"):
            defects.append({
                "line": idx,
                "type": "BRACKET_UNBALANCED",
                "detail": f"Llaves desbalanceadas: {trans.count('{')} apertura vs {trans.count('}')} cierre"
            })

        # 3. Cálculo de CPS
        if durations and idx < len(durations):
            dur = durations[idx]
            if dur > 0:
                cps = calculate_cps(trans, dur)
                limit = 20.0 if dur > 1.5 else 25.0
                if cps > limit:
                    defects.append({
                        "line": idx,
                        "type": "CPS_EXCEEDED",
                        "detail": f"CPS excesivo: {cps:.1f} > límite {limit:.1f} (duración {dur:.2f}s)"
                    })

    return defects

def clean_split_dialogue_punctuation(texts: list[str]) -> list[str]:
    """
    Corrige determinísticamente la duplicación de signos de apertura (¿ o ¡)
    cuando una misma oración o pregunta se divide a través de subtítulos consecutivos.

    Ejemplo:
      Sub 1: '¿Te refieres a que...' (abre pregunta sin cerrarla)
      Sub 2: '¿guerreros de todo el reino...?' (reabre redundantemente)
      Resultado Sub 2: 'guerreros de todo el reino...?'
    """
    if not texts or len(texts) < 2:
        return texts

    cleaned = list(texts)
    for i in range(1, len(cleaned)):
        prev_txt = cleaned[i - 1]
        curr_txt = cleaned[i]

        clean_prev = re.sub(r"\{.*?\}|<T\d+>", "", prev_txt).strip()
        clean_curr = re.sub(r"\{.*?\}|<T\d+>", "", curr_txt).strip()

        # Caso 1: Pregunta abierta en línea anterior
        # ¿ aparece después del último ? en clean_prev
        prev_has_unclosed_q = clean_prev.rfind("¿") > clean_prev.rfind("?")
        if prev_has_unclosed_q:
            if clean_curr.startswith("¿"):
                # Elimina el ¿ redundante en el subtítulo actual preservando tags ASS iniciales
                cleaned[i] = re.sub(r"^(\s*(?:\{.*?\}|<T\d+>)*)\s*¿\s*", r"\1", curr_txt)
                continue

        # Caso 2: Exclamación abierta en línea anterior
        prev_has_unclosed_excl = clean_prev.rfind("¡") > clean_prev.rfind("!")
        if prev_has_unclosed_excl:
            if clean_curr.startswith("¡"):
                if re.search(r"^¡\s*[a-záéíóúñ]", clean_curr) or clean_prev.endswith("..."):
                    cleaned[i] = re.sub(r"^(\s*(?:\{.*?\}|<T\d+>)*)\s*¡\s*", r"\1", curr_txt)
                    continue

    return cleaned

def tier2_proofread_batch(raw_texts: list[str], translated_texts: list[str], profile_data: dict, defects: list[dict], target_lang: str = "es-419", max_retries: int = 2) -> list[str]:
    """
    Tier 2: Corrector/Crítico Condicional con LLM y Circuit Breaker (máximo 2 reintentos).
    Se activa únicamente si Tier 1 reporta defectos o si se requiere pulido editorial.
    """
    if not translated_texts or len(raw_texts) != len(translated_texts):
        return translated_texts

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    defect_summary = []
    for d in defects:
        line_no = d["line"] + 1 if d["line"] >= 0 else "General"
        defect_summary.append(f"- Línea [{line_no}]: {d['type']} ({d['detail']})")
    defects_str = "\n".join(defect_summary[:10])

    sys_prompt = f"""Eres un supervisor editorial senior de subtítulos al {prompt_lang}.
Tu tarea es corregir los defectos específicos detectados en las líneas traducidas manteniendo el sentido exacto y estilo.

REGLAS ESTRICTAS:
1. Devuelve EXACTAMENTE el mismo número de líneas [{len(raw_texts)}] en formato: [N] Texto corregido
2. Preserva tokens de formato solo si existen en el original. NUNCA inventes etiquetas ni pseudotags (<T0>, etc.) en líneas que no los contengan.
3. Respeta un ritmo de lectura conciso (máximo 20-25 caracteres por segundo).
4. No agregues comentarios ni notas explicativas."""

    working_texts = list(translated_texts)
    for attempt in range(max_retries):
        numbered_items = []
        for idx, (orig, trans) in enumerate(zip(raw_texts, working_texts)):
            clean_orig = re.sub(r"\{.*?\}", "", orig).replace("\\N", " ").strip()
            numbered_items.append(f"[{idx+1}] Original: {clean_orig} => Traducción actual: {trans}")

        user_prompt = f"""DEFECTOS REPORTADOS POR EL VALIDADOR:
{defects_str}

Corrige los defectos y devuelve la lista completa de {len(raw_texts)} líneas corregidas al {prompt_lang} en formato [1] a [{len(raw_texts)}]:
""" + "\n".join(numbered_items) + "\n/no_think"

        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=120)
        if not resp:
            continue

        parsed = {}
        for line in resp.strip().split("\n"):
            m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
            if m:
                idx = int(m.group(1)) - 1
                val = m.group(2).strip()
                val = re.sub(r"^.*?(?:\||=>)\s*(?:[A-Za-z0-9\-]+|Traducci[oó]n|Correcci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                val = re.sub(r"^(?:[A-Za-z0-9\-]+|Traducci[oó]n|Correcci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                if 0 <= idx < len(raw_texts) and val:
                    parsed[idx] = val

        if len(parsed) >= len(raw_texts) * 0.75:
            for i in range(len(raw_texts)):
                if i in parsed and parsed[i]:
                    working_texts[i] = parsed[i]
            # Validar de nuevo
            new_defects = [d for d in tier1_validate_batch(raw_texts, working_texts) if d["type"] != "CPS_EXCEEDED"]
            if not new_defects:
                break

    return working_texts

def translate_atomic_batch(batch_texts: list[str], profile_data: dict, prev_context: list[str] = None, target_lang: str = "es-419", durations: list[float] = None, proofread: bool = False) -> list[str]:
    """
    Traduce un lote atómico (The Goldfish, 20-35 cues) aplicando:
    1. Enmascaramiento de etiquetas AST (<T0>, <T1>).
    2. Briefing conciso de The Elephant (<= 350 tokens).
    3. Validación determinista Tier 1 (conteo 1:1, preservación de tags, llaves, CPS).
    4. Pulido condicional Tier 2 si se detectan defectos o proofread está activo.
    5. Desenmascarado exacto de etiquetas AST.
    """
    if not batch_texts:
        return []

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    # 1. AST Tag Masking
    masked_batch = []
    tag_maps = []
    for text in batch_texts:
        masked_txt, t_map = mask_ass_tags(text)
        masked_batch.append(masked_txt)
        tag_maps.append(t_map)

    # 2. Context Briefing de The Elephant (<= 350 tokens)
    elephant_brief = build_scene_brief(profile_data, batch_texts, prev_context=prev_context, target_lang=target_lang, max_tokens=350)
    tone_info = profile_data.get("tone_instructions", f"Tono natural en {target_meta['name']}.")

    has_tags = any(bool(tm) for tm in tag_maps)
    if has_tags:
        tag_rule = "1. MANTÉN INTACTOS los tokens de formato (<T0>, <T1>, etc.) en su posición exacta. NUNCA agregues ni inventes etiquetas en líneas que no las tengan."
    else:
        tag_rule = "1. MANTÉN los saltos de línea \\N en la posición adecuada. NO inventes etiquetas, pseudotags ni marcas de formato."

    sys_prompt = f"""Eres un traductor y adaptador profesional de subtítulos de anime al {prompt_lang}.
{elephant_brief}
ESTILO: {tone_info}

REGLAS CRÍTICAS:
{tag_rule}
2. PRESERVA EL SENTIDO EXACTO: NUNCA inviertas afirmaciones ni negaciones.
3. CONCORDANCIA Y NATURALIDAD: Respeta los géneros del contexto y evita calcos del inglés.
4. Devuelve EXACTAMENTE la misma cantidad [{len(batch_texts)}] de líneas numeradas en formato: [N] Texto traducido.
5. NO agregues notas, explicaciones ni saludos."""

    numbered_input = "\n".join([f"[{i+1}] {t}" for i, t in enumerate(masked_batch)])
    user_prompt = f"Traduce al {prompt_lang} respetando la numeración [1] a [{len(batch_texts)}] y los tokens de etiqueta:\n{numbered_input}\n/no_think"

    parsed_results = {}
    translated_masked = None

    for attempt in range(3):
        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=180)
        if resp:
            parsed_results = {}
            for line in resp.strip().split("\n"):
                m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
                if m:
                    idx = int(m.group(1)) - 1
                    if 0 <= idx < len(batch_texts) and m.group(2).strip():
                        parsed_results[idx] = m.group(2).strip()

            if len(parsed_results) >= len(batch_texts) * 0.7:
                translated_masked = [parsed_results.get(i, masked_batch[i]) for i in range(len(batch_texts))]
                break

        if attempt < 2:
            print(f"[Goldfish] ⚠️ Reintentando lote atómico ({attempt+1}/3)...", flush=True)
            time.sleep(3)

    if not translated_masked:
        return None

    # 3. Tier 1: Deterministic Quality Validation
    defects = tier1_validate_batch(masked_batch, translated_masked, tag_maps=tag_maps, durations=durations)

    # 4. Tier 2: Conditional Proofreading
    if defects or proofread:
        if defects:
            print(f"[Goldfish] ⚠️ Tier 1 detectó {len(defects)} defecto(s) en el lote. Activando Tier 2 Critic...", flush=True)
        translated_masked = tier2_proofread_batch(masked_batch, translated_masked, profile_data, defects, target_lang=target_lang)

    # 5. AST Tag Unmasking con salvaguarda
    final_texts = []
    for i, t_masked in enumerate(translated_masked):
        t_map = tag_maps[i]
        unmasked = unmask_ass_tags(t_masked, t_map)
        # Si por alguna razón algún token no fue desenmascarado porque el LLM lo mutiló,
        # asegurar que los originales no se pierdan completamente si había tags indispensables
        for tag_token, orig_tag in t_map.items():
            if orig_tag not in unmasked:
                unmasked = f"{orig_tag}{unmasked}"
        final_texts.append(unmasked)

    # 6. Limpieza determinista de puntuación dividida (ej. ¿...? continuado)
    final_texts = clean_split_dialogue_punctuation(final_texts)

    return final_texts

def translate_batch(text_list, profile_data, prev_context=None, target_lang="es-419", durations=None):
    """Fachada compatible que delega en el motor atómico translate_atomic_batch."""
    return translate_atomic_batch(text_list, profile_data, prev_context=prev_context, target_lang=target_lang, durations=durations)

def proofread_subtitles(raw_texts, translated_texts, profile_data, batch_size=100, episode_context=None, target_lang="es-419"):
    if not translated_texts or len(raw_texts) != len(translated_texts):
        return translated_texts

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    chars_list = []
    for c in profile_data.get("characters", []):
        name = c.get("name", "")
        gender = c.get("gender", "")
        pers = c.get("personality", "")
        g_tag = f" [{gender}]" if gender else ""
        chars_list.append(f"- {name}{g_tag}: {pers}")
    chars_info = "\n".join(chars_list) if chars_list else "Sin personajes específicos."

    glossary_info = ", ".join([f"'{k}' -> '{v}'" for k, v in profile_data.get("glossary", {}).items()])
    setting_info = profile_data.get("setting", "Anime")
    tone_info = profile_data.get("tone_instructions", f"Tono natural en {target_meta['name']}.")

    ep_context_str = ""
    if episode_context and isinstance(episode_context, dict) and episode_context.get("summary"):
        ep_context_str += f"CONTEXTO DEL EPISODIO ACTUAL ({episode_context.get('episode', 'Actual')}):\n{episode_context.get('summary')}\n"

    past_eps = profile_data.get("episodes", [])
    if past_eps and isinstance(past_eps, list):
        filtered_past = [
            e for e in past_eps
            if not episode_context or (e.get("episode") != episode_context.get("episode") and e.get("file_name") != episode_context.get("file_name"))
        ]
        if filtered_past:
            recent_past = filtered_past[-2:]
            p_lines = "\n".join([f"- {e.get('episode', 'Ep')}: {e.get('summary', '')[:160]}" for e in recent_past if e.get("summary")])
            if p_lines:
                ep_context_str += f"RESUMEN DE CAPÍTULOS ANTERIORES:\n{p_lines}\n"

    sys_prompt = f"""Eres un editor, corrector y revisor senior de subtítulos de anime al {prompt_lang}.
SERIE: {profile_data.get('series_title', 'Anime')} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {setting_info}
TONO Y ESTILO: {tone_info}
{ep_context_str}PERSONAJES Y GÉNEROS:
{chars_info}
GLOSARIO OBLIGATORIO: {glossary_info}

REGLAS CRÍTICAS:
1. NUNCA AGREGUES NI ELIMINES LÍNEAS.
2. Devuelve las líneas con su numeración exacta en formato: [N] Texto en {prompt_lang}
3. Mantén TODAS las etiquetas ASS entre llaves y saltos \\N intactos en la misma posición.
4. NO incluyas notas ni explicaciones."""

    corrected_texts = list(translated_texts)

    for i in range(0, len(raw_texts), batch_size):
        batch_raw = raw_texts[i:i + batch_size]
        batch_trans = translated_texts[i:i + batch_size]

        numbered_items = []
        for idx_in_batch, (orig_line, tr_line) in enumerate(zip(batch_raw, batch_trans)):
            item_num = idx_in_batch + 1
            clean_orig = re.sub(r"\{.*?\}", "", orig_line).replace("\\N", " ").replace("\\n", " ").strip()
            numbered_items.append(f"[{item_num}] Original: {clean_orig} => Traduccion: {tr_line}")

        user_prompt = f"Revisa y pule estas líneas. Devuelve ÚNICAMENTE la traducción final en {prompt_lang} sin prefijos en formato [1] a [{len(batch_raw)}]:\n" + "\n".join(numbered_items) + "\n/no_think"

        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=120)
        if not resp:
            continue

        parsed_corrections = {}
        for line in resp.strip().split("\n"):
            m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
            if m:
                num = int(m.group(1)) - 1
                val = m.group(2).strip()
                val = re.sub(r"^.*?(?:\||=>)\s*(?:[A-Za-z0-9\-]+|Traducci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                val = re.sub(r"^(?:[A-Za-z0-9\-]+|Traducci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                if 0 <= num < len(batch_raw) and val:
                    parsed_corrections[num] = val

        if len(parsed_corrections) >= len(batch_raw) * 0.75:
            for idx_in_batch in range(len(batch_raw)):
                global_idx = i + idx_in_batch
                if idx_in_batch in parsed_corrections:
                    corr = parsed_corrections[idx_in_batch]
                    if corr:
                        corrected_texts[global_idx] = corr

    if len(corrected_texts) != len(translated_texts):
        return translated_texts

    return corrected_texts

def audit_and_polish_subtitles(sub_texts, profile_data, batch_size=100, episode_context=None, target_lang="es-419"):
    """Audita un subtítulo preexistente contra el perfil narrativo de la serie."""
    if not sub_texts:
        return sub_texts

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    chars_list = []
    for c in profile_data.get("characters", []):
        name = c.get("name", "")
        gender = c.get("gender", "")
        pers = c.get("personality", "")
        g_tag = f" [{gender}]" if gender else ""
        chars_list.append(f"- {name}{g_tag}: {pers}")
    chars_info = "\n".join(chars_list) if chars_list else "Sin personajes específicos."

    glossary_info = ", ".join([f"'{k}' -> '{v}'" for k, v in profile_data.get("glossary", {}).items()])
    setting_info = profile_data.get("setting", "Anime")
    tone_info = profile_data.get("tone_instructions", f"Tono natural en {target_meta['name']}.")

    ep_context_str = ""
    if episode_context and isinstance(episode_context, dict) and episode_context.get("summary"):
        ep_context_str += f"CONTEXTO DEL EPISODIO ACTUAL ({episode_context.get('episode', 'Actual')}):\n{episode_context.get('summary')}\n"

    past_eps = profile_data.get("episodes", [])
    if past_eps and isinstance(past_eps, list):
        filtered_past = [
            e for e in past_eps
            if not episode_context or (e.get("episode") != episode_context.get("episode") and e.get("file_name") != episode_context.get("file_name"))
        ]
        if filtered_past:
            recent_past = filtered_past[-2:]
            p_lines = "\n".join([f"- {e.get('episode', 'Ep')}: {e.get('summary', '')[:160]}" for e in recent_past if e.get("summary")])
            if p_lines:
                ep_context_str += f"RESUMEN DE CAPÍTULOS ANTERIORES:\n{p_lines}\n"

    dialect_instruction = ""
    if target_lang == "es-419":
        dialect_instruction = "3. ADAPTACIÓN DIALECTAL: Si detectas modismos o conjugaciones peninsulares, adáptalos a español latinoamericano neutro natural.\n"

    sys_prompt = f"""Eres un supervisor editorial, auditor y corrector senior de subtítulos de anime al {prompt_lang}.
SERIE: {profile_data.get('series_title', 'Anime')} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {setting_info}
TONO Y ESTILO: {tone_info}
{ep_context_str}PERSONAJES Y GÉNEROS:
{chars_info}
GLOSARIO OBLIGATORIO: {glossary_info}

CRITERIOS:
1. CONCORDANCIA DE GÉNERO Y PERSONAJES.
2. GLOSARIO OBLIGATORIO.
{dialect_instruction}4. ERRORES TIPOGRÁFICOS / ORTOGRÁFICOS.
5. PRESERVAR LO QUE ESTÁ BIEN.

REGLAS CRÍTICAS:
1. NUNCA AGREGUES NI ELIMINES LÍNEAS.
2. Devuelve las líneas con su numeración exacta en formato: [N] Texto en {prompt_lang}
3. Mantén TODAS las etiquetas ASS entre llaves y saltos \\N intactos en la misma posición.
4. NO incluyas notas ni explicaciones."""

    corrected_texts = list(sub_texts)

    for i in range(0, len(sub_texts), batch_size):
        batch = sub_texts[i:i + batch_size]
        numbered_items = [f"[{idx + 1}] {line}" for idx, line in enumerate(batch)]
        user_prompt = f"Audita y pule estas líneas existentes al {prompt_lang}. Devuelve ÚNICAMENTE el texto final en formato [1] a [{len(batch)}]:\n" + "\n".join(numbered_items) + "\n/no_think"

        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=120)
        if not resp:
            continue

        parsed_corrections = {}
        for line in resp.strip().split("\n"):
            m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
            if m:
                num = int(m.group(1)) - 1
                val = m.group(2).strip()
                val = re.sub(r"^.*?(?:\||=>)\s*(?:[A-Za-z0-9\-]+|Traducci[oó]n|Revisi[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                val = re.sub(r"^(?:[A-Za-z0-9\-]+|Traducci[oó]n|Revisi[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                if 0 <= num < len(batch) and val:
                    parsed_corrections[num] = val

        if len(parsed_corrections) >= len(batch) * 0.75:
            for idx_in_batch in range(len(batch)):
                global_idx = i + idx_in_batch
                if idx_in_batch in parsed_corrections:
                    corr = parsed_corrections[idx_in_batch]
                    if corr:
                        corrected_texts[global_idx] = corr

    if len(corrected_texts) != len(sub_texts):
        return sub_texts

    return corrected_texts
