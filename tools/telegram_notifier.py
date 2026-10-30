#!/usr/bin/env python3
"""
Telegram RichMessage Notifier for Sonarr & AutoDub / AutoSub
Sends native Telegram Bot API 10.x+ sendRichMessage with:
- Top image capture (-thumb.jpg or poster.jpg)
- h2 Title and h5 Episode
- Genre tags
- Compact bordered striped table (Ficha Técnica) with bold left headers
- Collapsible details for Synopsis (<details><summary>📖 Ver Sinopsis</summary>)
- Collapsible details for File Tracks (<details><summary>📁 Ver Detalles del Archivo</summary>)
- Bottom hashtags (#Series_Title)
"""

import os
import re
import time
import json
import html
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

def load_telegram_config() -> Dict[str, Any]:
    cfg = {
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "topic_id": int(os.getenv("TELEGRAM_TOPIC_ID", "0")) if os.getenv("TELEGRAM_TOPIC_ID") else 0,
        "gemini_api_key": os.getenv("GEMINI_API_KEY", "").strip()
    }
    config_candidates = [
        Path(__file__).parent / "telegram_config.json",
        Path("/opt/telegram_config.json"),
        Path("/data/telegram_config.json"),
        Path("telegram_config.json")
    ]
    for cp in config_candidates:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    if not cfg["bot_token"] and file_cfg.get("bot_token"):
                        cfg["bot_token"] = str(file_cfg["bot_token"]).strip()
                    if not cfg["chat_id"] and file_cfg.get("chat_id"):
                        cfg["chat_id"] = str(file_cfg["chat_id"]).strip()
                    if not cfg["topic_id"] and file_cfg.get("topic_id"):
                        cfg["topic_id"] = int(file_cfg["topic_id"])
                    if not cfg["gemini_api_key"] and file_cfg.get("gemini_api_key"):
                        cfg["gemini_api_key"] = str(file_cfg["gemini_api_key"]).strip()
            except Exception:
                pass
    return cfg

_CONFIG = load_telegram_config()
TELEGRAM_BOT_TOKEN = _CONFIG.get("bot_token", "")
TELEGRAM_CHAT_ID = _CONFIG.get("chat_id", "")
TELEGRAM_TOPIC_ID = _CONFIG.get("topic_id", 0)

def format_duration(seconds: float) -> str:
    sec = int(round(seconds))
    if sec <= 0:
        return ""
    m = (sec % 3600) // 60
    s = sec % 60
    h = sec // 3600
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def extract_nfo_data(nfo_path: str) -> Dict[str, str]:
    data = {}
    if os.path.exists(nfo_path):
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            data["title"] = root.findtext("title", "")
            data["plot"] = root.findtext("plot", "")
            data["duration"] = root.findtext("fileinfo/streamdetails/video/durationinseconds", "")
        except Exception:
            pass
    return data

def get_track_details(file_path: str):
    """Obtains detailed tracks using mkvmerge -J."""
    try:
        p = subprocess.run(["mkvmerge", "-J", file_path], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode == 0:
            return json.loads(p.stdout)
    except Exception:
        pass
    return {}

def is_spanish(text: str) -> bool:
    if not text:
        return True
    lower = " " + text.lower() + " "
    es_words = [" de ", " que ", " en ", " el ", " la ", " los ", " las ", " por ", " para ", " con ", " una ", " un "]
    en_words = [" the ", " and ", " of ", " to ", " with ", " for ", " is ", " in ", " her ", " his ", " their ", " after "]
    es_count = sum(1 for w in es_words if w in lower)
    en_count = sum(1 for w in en_words if w in lower)
    return es_count > en_count

def get_gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    cfg = load_telegram_config()
    if cfg.get("gemini_api_key"):
        return cfg["gemini_api_key"]
    # Buscar en configuración de autosub
    for cfg_path in ["/opt/autosub_config.json", "/data/autosub_config.json", "autosub_config.json"]:
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    c = json.load(f)
                    k = c.get("gemini_api_key", "").strip()
                    if k:
                        return k
            except Exception:
                pass
    return ""

def translate_synopsis_with_gemini(text: str) -> Optional[str]:
    api_key = get_gemini_api_key()
    if not api_key:
        return None

    # Modelos en orden de preferencia (Gemini 3.8 Flash solicitado como principal)
    models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    payload = {
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Eres un traductor profesional de anime para Hispanoamérica y Chile (español latino neutro es-419). "
                    "Traduce con fluidez natural, redacción limpia y tono cinematográfico. "
                    "Evita modismos españoles o regionalismos marcados (como encontronazo, majo, chaval, etc.). "
                    "Usa vocabulario natural como 'encuentro', 'cruce', 'desahogarse'. "
                    "Devuelve ÚNICAMENTE la traducción final del texto, sin comillas ni notas adicionales."
                )
            }]
        },
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048
        }
    }

    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=9)
            if resp.status_code == 200:
                data = resp.json()
                translated = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if translated:
                    # Limpiar comillas iniciales/finales si el modelo las agregó
                    if translated.startswith('"') and translated.endswith('"'):
                        translated = translated[1:-1].strip()
                    return translated
        except Exception:
            continue

    return None

def translate_synopsis_to_spanish(text: str) -> str:
    if not text or is_spanish(text):
        return text

    # 1. Intentar con Gemini (Gemini 3.8 Flash con fallback a 3.5 Flash) para máxima naturalidad latina
    gemini_trans = translate_synopsis_with_gemini(text)
    if gemini_trans:
        return gemini_trans

    # 2. Fallback de emergencia si no hay conectividad con la API de Gemini
    try:
        import urllib.request
        import urllib.parse
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=es&dt=t&q=" + urllib.parse.quote(text)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            res = json.loads(r.read())
            translated = "".join([part[0] for part in res[0] if part[0]]).strip()
            if translated:
                return translated
    except Exception:
        pass

    return text

def send_episode_rich_notification(
    file_path: str,
    payload: Optional[Dict[str, Any]] = None,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    topic_id: Optional[int] = None
) -> bool:
    if not os.path.exists(file_path):
        print(f"[TelegramNotifier] Error: Archivo no existe: {file_path}")
        return False

    cfg = load_telegram_config()
    bot_token = bot_token or cfg.get("bot_token")
    chat_id = chat_id or cfg.get("chat_id")
    if topic_id is None:
        topic_id = cfg.get("topic_id")

    if not bot_token or not chat_id:
        print("[TelegramNotifier] [WARN] Telegram bot_token o chat_id no configurados.")
        return False

    payload = payload or {}
    series_info = payload.get("series", {})
    episodes_info = payload.get("episodes", [])
    ep0 = episodes_info[0] if episodes_info else {}

    file_name = os.path.basename(file_path)
    dir_path = os.path.dirname(file_path)
    base_name = os.path.splitext(file_name)[0]

    # 1. Metadatos de Serie y Episodio
    series_title = series_info.get("title") or Path(dir_path).parent.name
    # Limpiar si el nombre de carpeta es Season X
    if series_title.lower().startswith("season"):
        series_title = Path(dir_path).parent.parent.name

    season_num = ep0.get("seasonNumber")
    ep_num = ep0.get("episodeNumber")

    if season_num is None or ep_num is None:
        s_match = re.search(r's(\d{1,2})e(\d{1,3})', file_name, re.IGNORECASE)
        if s_match:
            season_num = int(s_match.group(1))
            ep_num = int(s_match.group(2))
        else:
            e_match = re.search(r'(?: - |e)(\d{1,3})\b', file_name, re.IGNORECASE)
            season_num = 1
            ep_num = int(e_match.group(1)) if e_match else 1

    ep_code = f"S{season_num:02d}E{ep_num:02d}"

    # Título de episodio
    nfo_path = os.path.join(dir_path, f"{base_name}.nfo")
    nfo_data = extract_nfo_data(nfo_path)

    ep_title = ep0.get("title") or nfo_data.get("title") or ""
    ep_display = f"📺 {ep_code}  — {ep_title}" if ep_title else f"📺 {ep_code}"

    # Romaji o nombre alternativo entre paréntesis
    romaji_match = re.search(r'\((.*?)\)', file_name)
    series_romaji = romaji_match.group(1) if romaji_match else ""

    # 2. Sinopsis (traducida a español si viene en inglés)
    synopsis = ep0.get("overview") or nfo_data.get("plot") or ""
    if synopsis:
        synopsis = translate_synopsis_to_spanish(synopsis)
    else:
        synopsis = "Episodio disponible con sincronización completa."

    # 3. Etiquetas de género (arriba)
    genres = series_info.get("genres", [])
    if genres:
        genre_tags = "🏷️ " + " ".join(f"#{g.replace(' ', '_')}" for g in genres[:5])
    else:
        genre_tags = "🏷️ #Anime #Serie"

    # 4. Hashtag único de la serie según Sonarr (abajo)
    bottom_hashtags = "#" + re.sub(r'[^a-zA-Z0-9]+', '_', series_title).strip('_')

    # 5. Análisis técnico del archivo MKV
    mkv_info = get_track_details(file_path)
    tracks = mkv_info.get("tracks", [])

    audio_tracks = [t for t in tracks if t.get("type") == "audio"]
    sub_tracks = [t for t in tracks if t.get("type") == "subtitles"]
    vid_tracks = [t for t in tracks if t.get("type") == "video"]

    # Duración
    duration_str = ""
    for t in tracks:
        dur_ns = t.get("properties", {}).get("tag_duration")
        if dur_ns:
            # Format HH:MM:SS.nnn
            parts = str(dur_ns).split(":")
            if len(parts) >= 2:
                duration_str = f"{parts[1]}:{parts[2].split('.')[0]}"
                break
    if not duration_str and nfo_data.get("duration"):
        try:
            duration_str = format_duration(float(nfo_data["duration"]))
        except Exception:
            pass
    if not duration_str:
        duration_str = "24:00"

    # Audio info
    has_lat = False
    has_jp = False
    for a in audio_tracks:
        props = a.get("properties", {})
        l = (props.get("language") or props.get("language_ietf") or "").lower()
        n = (props.get("track_name") or "").lower()
        if "lat" in n or "latino" in n or l in ("es-419", "spa", "es"):
            has_lat = True
        if "jpn" in l or "jap" in n or "japanese" in n:
            has_jp = True

    if has_lat and has_jp:
        audio_status = "Dual Audio (JP + Latino)"
    elif has_lat:
        audio_status = "Español Latino (Doblaje Oficial)"
    elif has_jp:
        audio_status = "Japonés (Original)"
    else:
        audio_status = f"{len(audio_tracks)} Pista(s) de Audio"

    # Subtítulos info
    # Comprobar si hay .es-419.ass o .srt externo en la carpeta
    has_lat_sub = any(os.path.exists(os.path.join(dir_path, f"{base_name}.{ext}")) for ext in ("es-419.ass", "es-419.srt", "es.ass", "es.srt"))
    for s in sub_tracks:
        props = s.get("properties", {})
        l = (props.get("language") or props.get("language_ietf") or "").lower()
        n = (props.get("track_name") or "").lower()
        if "lat" in n or "latino" in n or "es-419" in l or "spa" in l:
            has_lat_sub = True

    if has_lat_sub and len(sub_tracks) > 1:
        sub_status = "Español Latino (es-419) + Multi-Subs"
    elif has_lat_sub:
        sub_status = "Español Latino (es-419 ASS/SRT)"
    elif sub_tracks:
        sub_status = f"Subtitulado ({len(sub_tracks)} pistas)"
    else:
        sub_status = "Sin subtítulos"

    # Release group y calidad
    group_match = re.search(r'^\s*\[([^\]]+)\]', file_name)
    group_name = f"[{group_match.group(1)}]" if group_match else (payload.get("release", {}).get("releaseGroup") or "RAW")

    res_match = re.search(r'\b(1080p|720p|2160p|4k)\b', file_name, re.IGNORECASE)
    res_str = res_match.group(1) if res_match else "1080p"
    codec_str = "HEVC x265" if any(c in file_name.lower() for c in ("x265", "hevc")) else "AVC x264"
    bit_str = "10bit" if "10bit" in file_name.lower() else ""
    release_str = f"{res_str} {codec_str} {bit_str}".strip()

    file_sz_mb = round(os.path.getsize(file_path) / (1024 * 1024), 1)

    # 6. Imagen de portada / captura
    # Prioridad: <base_name>-thumb.jpg -> seasonXX-poster.jpg -> poster.jpg -> fanart.jpg
    thumb_candidates = [
        os.path.join(dir_path, f"{base_name}-thumb.jpg"),
        os.path.join(dir_path, f"season{season_num:02d}-poster.jpg"),
        os.path.join(Path(dir_path).parent, f"season{season_num:02d}-poster.jpg"),
        os.path.join(dir_path, "poster.jpg"),
        os.path.join(Path(dir_path).parent, "poster.jpg"),
        os.path.join(dir_path, "fanart.jpg"),
        os.path.join(Path(dir_path).parent, "fanart.jpg"),
    ]

    selected_thumb = None
    for tc in thumb_candidates:
        if os.path.exists(tc) and os.path.getsize(tc) > 1000:
            selected_thumb = tc
            break

    # Si no hay imagen, extraer fotograma rápido con ffmpeg
    temp_thumb = None
    if not selected_thumb:
        temp_thumb = f"/tmp/thumb_{os.getpid()}_{int(time.time())}.jpg"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", "00:03:00", "-i", file_path,
                "-vframes", "1", "-q:v", "2", temp_thumb
            ], capture_output=True, timeout=10)
            if os.path.exists(temp_thumb) and os.path.getsize(temp_thumb) > 1000:
                selected_thumb = temp_thumb
        except Exception:
            pass

    # 7. Construcción de la tabla detallada de pistas
    track_rows = []
    # Video
    for v in vid_tracks:
        codec = v.get("properties", {}).get("codec_id", "V_MPEG4/ISO/AVC").replace("V_MPEGH/ISO/", "").replace("V_MPEG4/ISO/", "")
        w = v.get("properties", {}).get("pixel_dimensions", "").split("x")[0] if "pixel_dimensions" in v.get("properties", {}) else res_str
        track_rows.append(f'    <tr><th align="left"><b>Video</b></th><td>{res_str}</td><td>{codec}</td></tr>')

    for i, a in enumerate(audio_tracks, 1):
        props = a.get("properties", {})
        l = props.get("language") or props.get("language_ietf") or "und"
        codec = a.get("codec", "AAC")
        name = props.get("track_name") or ("Japonés" if l == "jpn" else "Español Latino" if "spa" in l or "es" in l else l)
        track_rows.append(f'    <tr><th align="left"><b>Audio {i}</b></th><td>{html.escape(name)}</td><td>{codec}</td></tr>')

    for i, s in enumerate(sub_tracks, 1):
        props = s.get("properties", {})
        l = props.get("language") or props.get("language_ietf") or "und"
        codec = "ASS" if "substation" in s.get("codec", "").lower() or "ass" in s.get("codec", "").lower() else "SRT"
        name = props.get("track_name") or ("Español Latino" if "spa" in l or "es" in l else l)
        track_rows.append(f'    <tr><th align="left"><b>Sub {i}</b></th><td>{html.escape(name)}</td><td>{codec}</td></tr>')

    # Si hay subtítulo externo .es-419
    if any(os.path.exists(os.path.join(dir_path, f"{base_name}.{ext}")) for ext in ("es-419.ass", "es.ass")):
        track_rows.append('    <tr><th align="left"><b>Sub Ext</b></th><td>Español Latino</td><td>ASS (AutoSub)</td></tr>')
    elif any(os.path.exists(os.path.join(dir_path, f"{base_name}.{ext}")) for ext in ("es-419.srt", "es.srt")):
        track_rows.append('    <tr><th align="left"><b>Sub Ext</b></th><td>Español Latino</td><td>SRT (AutoSub)</td></tr>')

    tracks_table_html = "\n".join(track_rows)

    # 8. Ensamblaje del HTML RichMessage
    html_content = f"""<img src="tg://photo?id=cover" />
<h2>🎬 {html.escape(series_title)}</h2>
<h5>{html.escape(ep_display)}</h5>
<p>{genre_tags}</p>

<details open>
  <summary>📋 Ficha Técnica</summary>
  <table bordered striped compact>
    <tr><th align="left">🎧 <b>Audio</b></th><td>{audio_status}</td></tr>
    <tr><th align="left">💬 <b>Subtítulos</b></th><td>{sub_status}</td></tr>
    <tr><th align="left">📦 <b>Release</b></th><td>{release_str}</td></tr>
    <tr><th align="left">🏷️ <b>Grupo</b></th><td>{html.escape(group_name)}</td></tr>
    <tr><th align="left">💾 <b>Tamaño</b></th><td>{file_sz_mb} MB</td></tr>
    <tr><th align="left">🕒 <b>Duración</b></th><td>{duration_str}</td></tr>
  </table>
</details>

<details>
  <summary>📖 Ver Sinopsis</summary>
  <blockquote>{html.escape(synopsis)}</blockquote>
</details>

<details>
  <summary>📁 Ver Detalles del Archivo</summary>
  <table bordered striped compact>
    <tr><th align="center">Pista</th><th align="center">Idioma</th><th align="center">Códec</th></tr>
{tracks_table_html}
  </table>
</details>

<p>{bottom_hashtags}</p>"""

    rich_obj = {
        "html": html_content,
        "media": [{
            "id": "cover",
            "media": {
                "type": "photo",
                "media": "attach://cover"
            }
        }]
    }

    rich_url = f"https://api.telegram.org/bot{bot_token}/sendRichMessage"
    payload_data = {
        "chat_id": chat_id,
        "rich_message": json.dumps(rich_obj)
    }
    if topic_id:
        payload_data["message_thread_id"] = str(topic_id)

    files = {}
    if selected_thumb and os.path.exists(selected_thumb):
        with open(selected_thumb, "rb") as f:
            files["cover"] = ("cover.jpg", f.read(), "image/jpeg")

    try:
        resp = requests.post(rich_url, data=payload_data, files=files if files else None, timeout=20)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("ok"):
            print(f"[TelegramNotifier] [OK] RichMessage enviado con exito a {chat_id} (Topic {topic_id}) para {file_name}")
            return True
        else:
            print(f"[TelegramNotifier] [WARN] Error de Telegram: {res_json.get('description')}")
    except Exception as e:
        print(f"[TelegramNotifier] [ERROR] Error enviando RichMessage: {e}")
    finally:
        if temp_thumb and os.path.exists(temp_thumb):
            try:
                os.remove(temp_thumb)
            except Exception:
                pass

    return False
