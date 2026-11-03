#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nfo_manager.py - Gestor de Metadatos NFO para AutoSub / AutoDub

Jerarquía de Sinopsis:
1. TheMovieDb (TMDb): Primera opción oficial (es-MX latino / es-ES). Rápido, humano y sin costo de tokens.
2. Gemini Flash (IA): Fallback cuando TMDb no tiene traducción al español para la serie o capítulo.

Mantiene los títulos (<title>, <showtitle>) en inglés oficial (coincidente con Sonarr/Radarr y los pósters)
e inyecta sinopsis en español latino neutro (sin modismos de España como "encontronazo" ni "chaval").
"""

import os
import re
import json
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any

# API Key de TheMovieDb (usada por Jellyfin)
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "4219e299c89411838049ab0dab19ebd5")

# Modelos de Gemini a probar en orden de preferencia (fallback)
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash"
]


def load_gemini_key() -> str:
    """Carga la API key de Gemini desde telegram_config.json o variable de entorno."""
    key = os.getenv("GEMINI_API_KEY", "")
    if key:
        return key

    config_paths = [
        Path("/opt/telegram_config.json"),
        Path(__file__).parent / "telegram_config.json",
        Path(__file__).parent.parent / "tools" / "telegram_config.json",
    ]
    for cp in config_paths:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    k = data.get("gemini_api_key")
                    if k:
                        return k
            except Exception:
                pass
    return ""


def is_spanish_text(text: str) -> bool:
    """Heurística rápida para saber si un texto ya está en español."""
    if not text or len(text.strip()) < 15:
        return False
    # Palabras comunes exclusivas del español
    es_words = {
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
        "en", "para", "por", "con", "su", "sus", "que", "y", "o", "pero",
        "cuando", "como", "esta", "este", "estos", "estas", "habia", "había",
        "despues", "después", "vida", "mundo", "años", "año", "hace"
    }
    words = [w.lower().strip(".,;:\"'!?()[]") for w in re.split(r'\s+', text)]
    hits = sum(1 for w in words if w in es_words)
    return hits >= 3


def fetch_tmdb_synopsis(endpoint: str, fallback_translate_en: bool = True) -> Optional[str]:
    """
    Primera opción: Consulta TheMovieDb en español latino (es-MX) y luego en español (es-ES).
    Si no está disponible en español y fallback_translate_en es True, consulta en inglés oficial (en-US)
    y traduce la sinopsis automáticamente al español latino con Gemini.
    Retorna la sinopsis oficial si está disponible, o None si no existe o está vacía.
    """
    if not TMDB_API_KEY:
        return None

    # 1. Buscar en español oficial (Latino o España)
    for lang in ["es-MX", "es-ES"]:
        url = f"https://api.themoviedb.org/3/{endpoint}?api_key={TMDB_API_KEY}&language={lang}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AutoDub-JellyfinSync/1.0"})
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))
                ov = data.get("overview", "").strip()
                if ov:
                    return ov
        except Exception:
            pass

    # 2. Si no hay sinopsis en español, consultar en inglés oficial y traducir con IA
    if fallback_translate_en:
        url_en = f"https://api.themoviedb.org/3/{endpoint}?api_key={TMDB_API_KEY}&language=en-US"
        try:
            req = urllib.request.Request(url_en, headers={"User-Agent": "AutoDub-JellyfinSync/1.0"})
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))
                ov_en = data.get("overview", "").strip()
                if ov_en:
                    print(f"  [TMDb] Sinopsis oficial encontrada en inglés ({len(ov_en)} caracteres). Traduciendo a español latino...")
                    trans = translate_with_gemini(ov_en)
                    if trans:
                        return trans
        except Exception as e:
            print(f"  [TMDb] Error consultando sinopsis en inglés: {e}")

    return None


def find_series_tmdb_id(nfo_path: str) -> Optional[str]:
    """Busca el tvshow.nfo en carpetas superiores para obtener el tmdbid de la serie."""
    curr = os.path.dirname(os.path.abspath(nfo_path))
    for _ in range(3):
        tvshow_p = os.path.join(curr, "tvshow.nfo")
        if os.path.exists(tvshow_p):
            try:
                tree = ET.parse(tvshow_p)
                tmdb_id = tree.getroot().findtext("tmdbid")
                if tmdb_id:
                    return tmdb_id.strip()
            except Exception:
                pass
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return None


def translate_with_gemini(text: str, api_key: Optional[str] = None) -> Optional[str]:
    """Segunda opción (Fallback): Traduce un texto de sinopsis al español latino neutro usando Gemini."""
    if not text or not text.strip():
        return None

    key = api_key or load_gemini_key()
    if not key:
        print("[!] No se encontró GEMINI_API_KEY para traducción.")
        return None

    prompt = (
        "Eres un traductor profesional de anime, series y películas para Latinoamérica.\n"
        "Traduce la siguiente sinopsis al español latino neutro y fluido (adecuado para Chile y resto de Latam).\n"
        "Reglas estrictas:\n"
        "- NO uses modismos o jerga de España (evita 'encontronazo', 'chaval', 'ordenador', 'vosotros', 'tío', etc.).\n"
        "- Preserva nombres propios en su forma original.\n"
        "- Devuelve ÚNICAMENTE el texto final traducido, sin comillas envolventes ni explicaciones adicionales.\n\n"
        f"Sinopsis a traducir:\n{text.strip()}"
    )

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.95,
                "maxOutputTokens": 1024
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                result = json.loads(response.read().decode("utf-8"))
                candidates = result.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        translated = parts[0].get("text", "").strip()
                        if (translated.startswith('"') and translated.endswith('"')) or (translated.startswith('“') and translated.endswith('”')):
                            translated = translated[1:-1].strip()
                        return translated
        except urllib.error.HTTPError as e:
            if e.code == 503:
                continue
            else:
                print(f"[!] Error de API Gemini ({model}): {e.code}")
        except Exception as ex:
            print(f"[!] Excepción conectando a Gemini ({model}): {ex}")
            continue

    # 3. Fallback de emergencia si Gemini no responde
    try:
        gt_url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=es&dt=t&q=" + urllib.parse.quote(text.strip())
        gt_req = urllib.request.Request(gt_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(gt_req, timeout=5) as r:
            gt_data = json.loads(r.read().decode("utf-8"))
            if gt_data and gt_data[0]:
                return "".join([frag[0] for frag in gt_data[0] if frag and frag[0]]).strip()
    except Exception:
        pass

    return None


def update_nfo_file(
    nfo_path: str,
    title: Optional[str] = None,
    showtitle: Optional[str] = None,
    spanish_plot: Optional[str] = None
) -> bool:
    """Actualiza campos específicos en un archivo NFO preservando el resto de metadatos intactos."""
    if not os.path.exists(nfo_path):
        return False

    try:
        try:
            tree = ET.parse(nfo_path)
        except Exception:
            with open(nfo_path, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            tree = ET.ElementTree(ET.fromstring(content))

        root = tree.getroot()
        modified = False

        # Título principal (para tvshow o movie)
        if title:
            t_elem = root.find("title")
            if t_elem is None:
                t_elem = ET.SubElement(root, "title")
            if t_elem.text != title:
                t_elem.text = title
                modified = True

        # Showtitle (para episodios de series)
        if showtitle:
            st_elem = root.find("showtitle")
            if st_elem is None:
                st_elem = ET.SubElement(root, "showtitle")
            if st_elem.text != showtitle:
                st_elem.text = showtitle
                modified = True

        # Sinopsis en español
        if spanish_plot:
            p_elem = root.find("plot")
            if p_elem is None:
                p_elem = ET.SubElement(root, "plot")
            if p_elem.text != spanish_plot:
                p_elem.text = spanish_plot
                modified = True

            # outline suele ser el mismo argumento en Kodi/Emby
            if root.tag in ("tvshow", "movie"):
                o_elem = root.find("outline")
                if o_elem is None:
                    o_elem = ET.SubElement(root, "outline")
                if o_elem.text != spanish_plot:
                    o_elem.text = spanish_plot
                    modified = True

        if modified:
            tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
            return True
        return False
    except Exception as e:
        print(f"[!] Error procesando archivo NFO '{nfo_path}': {e}")
        return False


def process_episode_nfo(nfo_path: str, series_english_title: Optional[str] = None) -> bool:
    """
    Procesa el NFO de un episodio:
    - Asegura que <showtitle> esté en inglés oficial de Sonarr.
    - Obtiene la sinopsis en español de TheMovieDb (Prioridad 1).
    - Si TMDb no la tiene, traduce la sinopsis en inglés con Gemini Flash (Prioridad 2).
    """
    if not os.path.exists(nfo_path):
        return False

    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        plot = (root.findtext("plot") or "").strip()
        showtitle = (root.findtext("showtitle") or "").strip()

        needs_plot = bool(not plot or not is_spanish_text(plot))
        needs_showtitle_fix = bool(series_english_title and showtitle != series_english_title)

        if not needs_plot and not needs_showtitle_fix:
            return False

        spanish_plot = None
        if needs_plot:
            # 1. Intentar consultar TheMovieDb
            season_num = root.findtext("season")
            episode_num = root.findtext("episode")
            series_tmdb_id = find_series_tmdb_id(nfo_path)

            if series_tmdb_id and season_num and episode_num:
                spanish_plot = fetch_tmdb_synopsis(f"tv/{series_tmdb_id}/season/{season_num}/episode/{episode_num}")
                if spanish_plot:
                    print(f"  [TMDb] ✔ Sinopsis S{season_num}E{episode_num} obtenida oficialmente de TheMovieDb.")

            # 2. Si TMDb no tiene sinopsis para este episodio, fallback a Gemini
            if not spanish_plot and plot:
                spanish_plot = translate_with_gemini(plot)
                if spanish_plot:
                    print(f"  [Gemini] ✔ Sinopsis traducida con IA (TMDb no tenía texto en español).")

        return update_nfo_file(
            nfo_path=nfo_path,
            showtitle=series_english_title if needs_showtitle_fix else None,
            spanish_plot=spanish_plot
        )
    except Exception as e:
        print(f"[!] Error en process_episode_nfo('{nfo_path}'): {e}")
        return False


def process_series_nfo(tvshow_nfo_path: str, series_english_title: str) -> bool:
    """
    Procesa el tvshow.nfo de una serie:
    - Asegura que <title> esté en inglés oficial de Sonarr.
    - Obtiene la sinopsis en español de TheMovieDb (Prioridad 1).
    - Si TMDb no la tiene, traduce la sinopsis en inglés con Gemini Flash (Prioridad 2).
    """
    if not os.path.exists(tvshow_nfo_path):
        return False

    try:
        tree = ET.parse(tvshow_nfo_path)
        root = tree.getroot()
        current_title = (root.findtext("title") or "").strip()
        plot = (root.findtext("plot") or "").strip()
        tmdb_id = (root.findtext("tmdbid") or "").strip()

        needs_title_fix = bool(series_english_title and current_title != series_english_title)
        needs_plot = bool(not plot or not is_spanish_text(plot))

        if not needs_title_fix and not needs_plot:
            return False

        spanish_plot = None
        if needs_plot:
            # 1. TheMovieDb primero
            if tmdb_id:
                spanish_plot = fetch_tmdb_synopsis(f"tv/{tmdb_id}")
                if spanish_plot:
                    print(f"[TMDb] ✔ Sinopsis de serie '{series_english_title}' obtenida de TheMovieDb.")

            # 2. Gemini fallback
            if not spanish_plot and plot:
                spanish_plot = translate_with_gemini(plot)
                if spanish_plot:
                    print(f"[Gemini] ✔ Sinopsis traducida con IA (TMDb no tenía texto en español).")

        return update_nfo_file(
            nfo_path=tvshow_nfo_path,
            title=series_english_title if needs_title_fix else None,
            spanish_plot=spanish_plot
        )
    except Exception as e:
        print(f"[!] Error en process_series_nfo('{tvshow_nfo_path}'): {e}")
        return False


def process_movie_nfo(movie_nfo_path: str, movie_english_title: str) -> bool:
    """
    Procesa el NFO de una película:
    - Asegura que <title> esté en inglés oficial de Radarr.
    - Obtiene la sinopsis en español de TheMovieDb (Prioridad 1).
    - Si TMDb no la tiene, traduce la sinopsis en inglés con Gemini Flash (Prioridad 2).
    """
    if not os.path.exists(movie_nfo_path):
        return False

    try:
        tree = ET.parse(movie_nfo_path)
        root = tree.getroot()
        current_title = (root.findtext("title") or "").strip()
        plot = (root.findtext("plot") or "").strip()
        tmdb_id = (root.findtext("tmdbid") or "").strip()

        needs_title_fix = bool(movie_english_title and current_title != movie_english_title)
        needs_plot = bool(not plot or not is_spanish_text(plot))

        if not needs_title_fix and not needs_plot:
            return False

        spanish_plot = None
        if needs_plot:
            # 1. TheMovieDb primero
            if tmdb_id:
                spanish_plot = fetch_tmdb_synopsis(f"movie/{tmdb_id}")
                if spanish_plot:
                    print(f"[TMDb] ✔ Sinopsis de película '{movie_english_title}' obtenida de TheMovieDb.")

            # 2. Gemini fallback
            if not spanish_plot and plot:
                spanish_plot = translate_with_gemini(plot)
                if spanish_plot:
                    print(f"[Gemini] ✔ Sinopsis de película traducida con IA (TMDb no tenía texto en español).")

        return update_nfo_file(
            nfo_path=movie_nfo_path,
            title=movie_english_title if needs_title_fix else None,
            spanish_plot=spanish_plot
        )
    except Exception as e:
        print(f"[!] Error en process_movie_nfo('{movie_nfo_path}'): {e}")
        return False
