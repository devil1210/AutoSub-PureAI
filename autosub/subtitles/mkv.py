import shutil
from autosub.config import LANG_ALIASES

SUPPORTED_TEXT_CODECS = ["S_TEXT/ASS", "S_TEXT/SSA", "S_TEXT/UTF8"]

def check_mkvtoolnix():
    mkvmerge_bin = shutil.which("mkvmerge")
    mkvextract_bin = shutil.which("mkvextract")
    return bool(mkvmerge_bin and mkvextract_bin), mkvmerge_bin, mkvextract_bin

def is_forced_or_signs_track(t):
    """Detecta si una pista es forzada (signs/songs/letreros) y debe evitarse como diálogo principal."""
    if not t:
        return False
    props = t.get("properties", {})
    if props.get("forced_track") is True or props.get("forced") is True:
        return True
    t_name = (props.get("track_name") or "").lower()
    bad_keywords = ["signs", "songs", "song", "carteles", "forzados", "forzado", "forced"]
    return any(kw in t_name for kw in bad_keywords)

def is_supported_text_codec(t):
    """Verifica si el codec de subtítulos es de texto soportado y no una pista de mapa de bits (PGS/VobSub)."""
    if not t:
        return False
    codec = (t.get("codec") or t.get("properties", {}).get("codec_id") or "").upper()
    return any(c in codec for c in ["ASS", "SSA", "SUBSTATION", "UTF8", "SUBRIP", "TEXT"])

def find_matching_target_track(sub_tracks, target_lang):
    """Busca una pista completa no forzada que ya coincida con el idioma objetivo (ej. Spanish[LAT] para es-419)."""
    if not sub_tracks:
        return None
    t_lower = target_lang.lower()
    base_code = t_lower.split("-")[0]
    raw_aliases = set(LANG_ALIASES.get(t_lower, [])) | set(LANG_ALIASES.get(base_code, [base_code]))
    raw_aliases.add(t_lower)
    raw_aliases.add(base_code)
    aliases = [a.lower() for a in raw_aliases]

    candidates = []
    for t in sub_tracks:
        if is_forced_or_signs_track(t):
            continue
        props = t.get("properties", {})
        lang = (props.get("language") or props.get("language_ietf") or "").lower()
        t_name = (props.get("track_name") or "").lower()
        if any(alias in lang or alias in t_name for alias in aliases):
            score = 5
            if t_lower in ["es-419", "es"]:
                if any(w in t_name or w in lang for w in ["lat", "419", "latin"]):
                    score = 10 if t_lower == "es-419" else 4
                elif any(w in t_name for w in ["esp", "castellano", "spain"]):
                    score = 10 if t_lower == "es" else 4
            candidates.append((score, t))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def select_source_track(sub_tracks, source_priority):
    """Selecciona la mejor pista de origen prioritaria descartando pistas forzadas o de sólo carteles."""
    if not sub_tracks:
        return None
    for lang_code in source_priority:
        aliases = LANG_ALIASES.get(lang_code.lower(), [lang_code.lower()])
        for t in sub_tracks:
            if is_forced_or_signs_track(t) or not is_supported_text_codec(t):
                continue
            props = t.get("properties", {})
            lang = (props.get("language") or props.get("language_ietf") or "").lower()
            t_name = (props.get("track_name") or "").lower()
            if any(alias in lang or alias in t_name for alias in aliases):
                return t

    for t in sub_tracks:
        if not is_forced_or_signs_track(t) and is_supported_text_codec(t):
            return t

    return sub_tracks[0] if sub_tracks else None
