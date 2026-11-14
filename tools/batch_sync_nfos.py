#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_sync_nfos.py - Sincronizador masivo de NFOs para todo el catálogo de Anime, Series y Películas.

Recorre el disco de 8TB:
1. Asegura que los títulos de series (<title>, <showtitle>) estén en inglés oficial.
2. Traduce las sinopsis (<plot>, <outline>) que estén en inglés a español latino neutro usando Gemini Flash.
3. Respeta al 100% las que ya estén en español (cero costo de tokens, cero peticiones innecesarias).
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Añadir directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))
try:
    from nfo_manager import process_series_nfo, process_episode_nfo, process_movie_nfo, is_spanish_text
except ImportError:
    from tools.nfo_manager import process_series_nfo, process_episode_nfo, process_movie_nfo, is_spanish_text

def scan_and_sync_tvshows(root_dir: str = "/data/tvshows", max_episodes_per_series: int = 0):
    if not os.path.exists(root_dir):
        print(f"[!] Directorio no encontrado: {root_dir}")
        return

    series_folders = [
        f for f in sorted(os.listdir(root_dir))
        if os.path.isdir(os.path.join(root_dir, f)) and not f.startswith(".")
    ]

    print(f"[*] Iniciando sincronización de NFOs en {len(series_folders)} series en '{root_dir}'...\n")

    total_series_updated = 0
    total_episodes_updated = 0

    for idx, folder in enumerate(series_folders, 1):
        series_path = os.path.join(root_dir, folder)
        english_title = folder  # En Sonarr el nombre de la carpeta es el título oficial en inglés

        # 1. tvshow.nfo
        tvshow_nfo = os.path.join(series_path, "tvshow.nfo")
        if os.path.exists(tvshow_nfo):
            try:
                res = process_series_nfo(tvshow_nfo, english_title)
                if res:
                    print(f"[{idx}/{len(series_folders)}] ✔ Serie: '{english_title}' tvshow.nfo actualizado.")
                    total_series_updated += 1
                else:
                    # Ya estaba en español e inglés
                    pass
            except Exception as e:
                print(f"[!] Error en '{english_title}/tvshow.nfo': {e}")

        # 2. Episodios en temporadas
        ep_count = 0
        for root, dirs, files in os.walk(series_path):
            for file in sorted(files):
                if file.lower().endswith(".nfo") and file.lower() != "tvshow.nfo":
                    ep_nfo_path = os.path.join(root, file)
                    try:
                        ep_res = process_episode_nfo(ep_nfo_path, english_title)
                        if ep_res:
                            print(f"  [+] Episodio actualizado: {file}")
                            total_episodes_updated += 1
                            time.sleep(0.5)  # Evitar rate limit de Gemini
                        ep_count += 1
                        if max_episodes_per_series and ep_count >= max_episodes_per_series:
                            break
                    except Exception as ex:
                        print(f"  [!] Error en episodio '{file}': {ex}")

    print("\n" + "=" * 60)
    print(f"✔ Sincronización completada:")
    print(f"   - Series actualizadas: {total_series_updated}")
    print(f"   - Episodios actualizados: {total_episodes_updated}")
    print("=" * 60 + "\n")


def scan_and_sync_movies(root_dir: str = "/data/movies"):
    if not os.path.exists(root_dir):
        print(f"[!] Directorio de películas no encontrado: {root_dir}")
        return

    print(f"[*] Iniciando sincronización de NFOs de películas en '{root_dir}'...\n")
    movie_folders = [
        f for f in sorted(os.listdir(root_dir))
        if os.path.isdir(os.path.join(root_dir, f)) and not f.startswith(".")
    ]

    total_movies_updated = 0
    for idx, folder in enumerate(movie_folders, 1):
        movie_path = os.path.join(root_dir, folder)
        english_title = folder
        
        # Buscar .nfo en la carpeta de la película
        for file in os.listdir(movie_path):
            if file.lower().endswith(".nfo"):
                nfo_p = os.path.join(movie_path, file)
                try:
                    res = process_movie_nfo(nfo_p, english_title)
                    if res:
                        print(f"[{idx}/{len(movie_folders)}] ✔ Película: '{english_title}' actualizada.")
                        total_movies_updated += 1
                        time.sleep(0.5)
                except Exception as ex:
                    print(f"[!] Error en película '{english_title}': {ex}")

    print(f"\n✔ Total películas actualizadas: {total_movies_updated}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincronizador masivo de NFOs.")
    parser.add_argument("--tv", default="/data/tvshows", help="Ruta de series")
    parser.add_argument("--movies", default="/data/movies", help="Ruta de películas")
    parser.add_argument("--skip-movies", action="store_true", help="Omitir películas")
    parser.add_argument("--limit-eps", type=int, default=0, help="Límite de episodios por serie (0=sin límite)")
    args = parser.parse_args()

    scan_and_sync_tvshows(args.tv, max_episodes_per_series=args.limit_eps)
    if not args.skip_movies and os.path.exists(args.movies):
        scan_and_sync_movies(args.movies)

