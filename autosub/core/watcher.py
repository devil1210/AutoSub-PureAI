import os
import time
from autosub.config import app_config

def get_watch_paths():
    libs = app_config.get("libraries")
    if isinstance(libs, list) and len(libs) > 0:
        extracted = []
        for l in libs:
            if isinstance(l, dict) and l.get("path"):
                extracted.append(l["path"])
            elif isinstance(l, str) and l.strip():
                extracted.append(l.strip())
        if extracted:
            return extracted
    paths = app_config.get("watch_paths", ["/data/tvshows", "/data/movies"])
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split("\n") if p.strip()]
    elif isinstance(paths, list):
        paths = [str(p).strip() for p in paths if str(p).strip()]
    return paths or ["/data/tvshows", "/data/movies"]

def get_parsed_watch_paths():
    """Retorna una lista de tuplas (directorio, nombre_categoria) soportando lista de diccionarios 'libraries' o 'watch_paths'"""
    libs = app_config.get("libraries")
    if isinstance(libs, list) and len(libs) > 0 and isinstance(libs[0], dict):
        result = []
        for l in libs:
            p = str(l.get("path", "")).strip()
            n = str(l.get("name", "")).strip() or ("Películas" if "movie" in p.lower() else "Series")
            if p:
                result.append((p, n))
        if result:
            return result

    raw_list = get_watch_paths()
    parsed = []
    for item in raw_list:
        if isinstance(item, dict):
            p = str(item.get("path", "")).strip()
            n = str(item.get("name", "")).strip() or ("Películas" if "movie" in p.lower() else "Series")
            if p:
                parsed.append((p, n))
        elif ";" in str(item):
            parts = str(item).split(";", 1)
            d_path = parts[0].strip()
            label = parts[1].strip() or ("Películas" if "movie" in d_path.lower() else "Series")
            parsed.append((d_path, label))
        else:
            d_path = str(item).strip()
            label = "Películas" if "movie" in d_path.lower() else "Series"
            parsed.append((d_path, label))
    return parsed or [("/data/tvshows", "Anime"), ("/data/movies", "Películas")]

def is_file_stable(filepath: str, wait_seconds: float = 1.0) -> bool:
    """Verifica que el archivo no esté siendo escrito activamente comparando su tamaño."""
    try:
        size1 = os.path.getsize(filepath)
        time.sleep(wait_seconds)
        size2 = os.path.getsize(filepath)
        return size1 == size2 and size1 > 0
    except OSError:
        return False
