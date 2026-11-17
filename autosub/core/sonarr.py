import os
import re
import json
import logging
import urllib.request
import urllib.parse
from autosub.config import app_config

logger = logging.getLogger("AutoSub.Sonarr")

def get_sonarr_config():
    """Retorna la URL y la API Key de Sonarr configuradas."""
    url = app_config.get("sonarr_url") or os.getenv("SONARR_URL", "http://192.168.1.131:8989")
    api_key = app_config.get("sonarr_api_key") or os.getenv("SONARR_API_KEY", "91528f758b7f42be9b10852ee230bf41")
    return url.rstrip("/"), api_key

def extract_season_episode_fallback(file_path, series_name=""):
    """
    Fallback heurístico inteligente para extraer (temporada, episodio) de nombres de archivo
    cuando Sonarr no esté disponible o la serie no esté indexada.
    Limpia el nombre de la serie para evitar colisiones con títulos que contienen números (ej: 3-gatsu no Lion).
    """
    filename = os.path.basename(file_path)
    clean = filename
    if series_name:
        clean = re.sub(re.escape(series_name), "", clean, flags=re.IGNORECASE)

    # 1. Patrón clásico S01E02 o s1e2
    m = re.search(r'[sS](\d+)[eE](\d+)', clean)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 2. Patrón 1x02 o 01x02
    m = re.search(r'(\d+)x(\d+)', clean)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 3. Número de episodio aislado: - 02, [02], Ep 02
    m = re.search(r'(?:[-_.\s]|^)(?:ep|e|cap|capítulo)?\s*(\d{1,4})(?:[-_.\s]|\.mkv|\.mp4)', clean, re.IGNORECASE)
    if m:
        return 1, int(m.group(1))

    return 9999, 9999

def get_series_episodes_from_sonarr(series_name, series_dir=None):
    """
    Consulta a Sonarr por la serie especificada y retorna una lista de episodios
    ordenados canónicamente por (seasonNumber, episodeNumber).
    Retorna None si Sonarr está offline o la serie no se encuentra.
    """
    sonarr_url, api_key = get_sonarr_config()
    if not sonarr_url or not api_key:
        return None

    try:
        headers = {"X-Api-Key": api_key}
        req_series = urllib.request.Request(f"{sonarr_url}/api/v3/series", headers=headers)
        with urllib.request.urlopen(req_series, timeout=5) as resp:
            series_list = json.loads(resp.read().decode())

        target_series = None
        s_name_clean = series_name.lower().strip()
        dir_name_clean = os.path.basename(series_dir).lower().strip() if series_dir else ""

        for s in series_list:
            title_clean = s.get("title", "").lower().strip()
            path_clean = os.path.basename(s.get("path", "")).lower().strip()
            if title_clean == s_name_clean or path_clean == s_name_clean or (dir_name_clean and path_clean == dir_name_clean):
                target_series = s
                break

        if not target_series:
            # Búsqueda difusa por inclusión
            for s in series_list:
                title_clean = s.get("title", "").lower().strip()
                if s_name_clean in title_clean or title_clean in s_name_clean:
                    target_series = s
                    break

        if not target_series:
            return None

        series_id = target_series["id"]

        # Obtener episodios y sus archivos asociados
        req_eps = urllib.request.Request(f"{sonarr_url}/api/v3/episode?seriesId={series_id}", headers=headers)
        with urllib.request.urlopen(req_eps, timeout=5) as resp:
            episodes = json.loads(resp.read().decode())

        req_files = urllib.request.Request(f"{sonarr_url}/api/v3/episodefile?seriesId={series_id}", headers=headers)
        with urllib.request.urlopen(req_files, timeout=5) as resp:
            efiles = {f["id"]: f for f in json.loads(resp.read().decode())}

        downloaded = [e for e in episodes if e.get("hasFile") and e.get("episodeFileId") in efiles]
        # Orden canónico estricto de Sonarr
        downloaded.sort(key=lambda x: (x.get("seasonNumber", 0), x.get("episodeNumber", 0)))

        ordered_files = []
        for e in downloaded:
            f_info = efiles[e["episodeFileId"]]
            f_path = f_info.get("path")
            if f_path and os.path.isfile(f_path):
                ordered_files.append({
                    "season": e.get("seasonNumber", 0),
                    "episode": e.get("episodeNumber", 0),
                    "episode_title": e.get("title", ""),
                    "path": f_path,
                    "file_name": os.path.basename(f_path)
                })

        return ordered_files
    except Exception as e:
        logger.debug(f"Sonarr no disponible o error al consultar episodios: {e}")
        return None
