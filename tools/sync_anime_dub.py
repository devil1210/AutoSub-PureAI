#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_anime_dub.py - Sincronizador Automático de Audio Latino para Anime

Permite transferir pistas de audio latino (por ejemplo ripeadas de Telegram a 720p)
a versiones de alta calidad (1080p / BDRip de Nyaa) calculando el desfase exacto
en milisegundos mediante correlación acústica MFCC y muxeando sin pérdida con MKVToolNix.
"""

import os
import sys
import json
import re
import shutil
import argparse
import subprocess
from pathlib import Path

try:
    from audio_offset_finder.audio_offset_finder import find_offset_between_files
except ImportError:
    print("[ERROR] Falta 'audio-offset-finder'. Instálalo con: pip install audio-offset-finder", file=sys.stderr)
    sys.exit(1)


def find_binary(name: str, fallback_paths: list = None) -> str:
    """Busca un ejecutable en PATH o en rutas típicas de Windows/Linux."""
    found = shutil.which(name)
    if found:
        return found
    if fallback_paths:
        for p in fallback_paths:
            if os.path.isfile(p):
                return p
    return None


MKVMERGE_BIN = find_binary("mkvmerge", [
    r"C:\Program Files\MKVToolNix\mkvmerge.exe",
    r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
    r"/usr/bin/mkvmerge",
    r"/usr/local/bin/mkvmerge",
])

FFMPEG_BIN = find_binary("ffmpeg", [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"/usr/bin/ffmpeg",
    r"/usr/local/bin/ffmpeg",
])


def check_prerequisites():
    """Verifica que mkvmerge y ffmpeg estén disponibles."""
    missing = []
    if not MKVMERGE_BIN:
        missing.append("MKVToolNix (mkvmerge) no encontrado. Descárgalo o instálalo con: winget install MoritzBunkus.MKVToolNix")
    if not FFMPEG_BIN:
        missing.append("FFmpeg no encontrado. Instálalo con: winget install Gyan.FFmpeg.Essentials")
    if missing:
        for m in missing:
            print(f"[!] {m}", file=sys.stderr)
        sys.exit(1)


def get_mkv_tracks(file_path: str) -> dict:
    """Inspecciona pistas usando mkvmerge -J."""
    cmd = [MKVMERGE_BIN, "-J", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"mkvmerge falló al identificar {file_path}:\n{result.stderr}")
    return json.loads(result.stdout)


def get_audio_track_id(file_path: str, prefer_lang: str = "spa") -> int:
    """Identifica el ID de la pista de audio (prioriza español si existe, o toma la primera)."""
    info = get_mkv_tracks(file_path)
    audio_tracks = [t for t in info.get("tracks", []) if t.get("type") == "audio"]
    if not audio_tracks:
        raise ValueError(f"No se encontraron pistas de audio en {file_path}")

    # Si hay una marcada con el idioma buscado, usar esa
    for t in audio_tracks:
        props = t.get("properties", {})
        lang = props.get("language", "") or props.get("language_ietf", "")
        if prefer_lang.lower() in lang.lower():
            return t["id"]

    # Por defecto, la primera pista de audio (en Telegram suele ser la única)
    return audio_tracks[0]["id"]


def calculate_offset(ref_file: str, dub_file: str, trim: int = 360) -> tuple[float, float]:
    """
    Calcula el desfase temporal entre el archivo de referencia (Nyaa) y el doblaje (Telegram).
    Retorna (offset_seconds, standard_score/confianza).
    """
    print(f"[*] Analizando huellas acústicas (primeros {trim}s)...")
    res = find_offset_between_files(
        ref_file,
        dub_file,
        trim=trim,
        fs=8000,
        hop_length=128,
        win_length=256,
        nfft=512,
        max_frames=4000  # ventana de búsqueda de hasta ~64s
    )
    offset = res["time_offset"]
    score = res["standard_score"]
    return offset, score


def sync_single_episode(nyaa_file: str, dub_file: str, output_file: str,
                        trim: int = 360, lang: str = "spa",
                        track_name: str = "Español Latino",
                        set_default: bool = False,
                        dry_run: bool = False) -> bool:
    """Sincroniza un episodio y crea el archivo final con audio dual."""
    nyaa_path = Path(nyaa_file).resolve()
    dub_path = Path(dub_file).resolve()
    out_path = Path(output_file).resolve()

    if not nyaa_path.exists():
        print(f"[ERROR] Archivo Nyaa no existe: {nyaa_path}", file=sys.stderr)
        return False
    if not dub_path.exists():
        print(f"[ERROR] Archivo Dub no existe: {dub_path}", file=sys.stderr)
        return False

    print("\n" + "=" * 60)
    print(f"Procesando:")
    print(f"  • Base (Nyaa 1080p): {nyaa_path.name}")
    print(f"  • Audio (Telegram):  {dub_path.name}")
    print("=" * 60)

    # 1. Obtener ID de la pista de audio del doblaje
    dub_audio_id = get_audio_track_id(str(dub_path), prefer_lang=lang)
    print(f"[*] Pista de audio detectada en Dub: ID #{dub_audio_id}")

    # 2. Calcular desfase
    offset_sec, score = calculate_offset(str(nyaa_path), str(dub_path), trim=trim)
    delay_ms = int(round(offset_sec * 1000))

    score_desc = "Excelente" if score >= 6.0 else ("Buena" if score >= 3.5 else "Baja (revisar resultado)")
    print(f"[+] Desfase detectado: {offset_sec:+.3f}s ({delay_ms:+d} ms)")
    print(f"[+] Confianza del algoritmo: {score:.2f} [{score_desc}]")

    if dry_run:
        print("[DRY-RUN] No se creará el archivo de salida.")
        return True

    # 3. Preparar comando mkvmerge
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        MKVMERGE_BIN,
        "-o", str(out_path),
        str(nyaa_path),  # Conserva video, audios existentes, subtítulos, fuentes y capítulos
        "--no-video",
        "--no-subtitles",
        "--no-buttons",
        "--no-track-tags",
        "--no-global-tags",
        "--audio-tracks", str(dub_audio_id),
        "--sync", f"{dub_audio_id}:{delay_ms}",
        "--language", f"{dub_audio_id}:{lang}",
        "--track-name", f"{dub_audio_id}:{track_name}",
    ]

    if set_default:
        cmd += ["--default-track-flag", f"{dub_audio_id}:yes"]

    cmd.append(str(dub_path))

    print(f"[*] Muxeando con MKVToolNix en: {out_path.name}...")
    run_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run_res.returncode not in (0, 1):  # 0=ok, 1=warnings (generalmente diferencias menores de headers)
        print(f"[ERROR] Error al muxear:\n{run_res.stderr}", file=sys.stderr)
        return False

    print(f"[✓] ¡Completado exitosamente! Archivo creado:")
    print(f"    -> {out_path} ({round(out_path.stat().st_size / (1024*1024), 1)} MB)\n")
    return True


def extract_episode_number(filename: str) -> int | None:
    """Extrae el número de episodio de un nombre de archivo."""
    patterns = [
        r"[sS]\d+[\.\- ]*[eE](\d{1,3})",   # S01E05, S1E5
        r"[eE](\d{1,3})",                  # E05, e12
        r"(?:cap|capitulo|ep|episodio)[\.\-_ ]*(\d{1,3})",  # Cap_05, Episodio 01
        r"[ -_](\d{1,3})[ -_v\.]",          # - 05 - , _05_
        r"\b(\d{1,3})\b"                   # 05 aislado
    ]
    name = Path(filename).stem
    for p in patterns:
        m = re.search(p, name, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def sync_batch_directory(nyaa_dir: str, dub_dir: str, output_dir: str,
                         trim: int = 360, lang: str = "spa",
                         track_name: str = "Español Latino",
                         set_default: bool = False,
                         dry_run: bool = False):
    """Sincroniza una temporada completa emparejando capítulos por número."""
    nyaa_path = Path(nyaa_dir).resolve()
    dub_path = Path(dub_dir).resolve()
    out_path = Path(output_dir).resolve()

    exts = {".mkv", ".mp4", ".m4v", ".webm", ".avi"}

    nyaa_files = [f for f in nyaa_path.iterdir() if f.is_file() and f.suffix.lower() in exts]
    dub_files = [f for f in dub_path.iterdir() if f.is_file() and f.suffix.lower() in exts]

    if not nyaa_files:
        print(f"[ERROR] No se encontraron videos en {nyaa_path}", file=sys.stderr)
        return
    if not dub_files:
        print(f"[ERROR] No se encontraron videos en {dub_path}", file=sys.stderr)
        return

    # Indexar por número de episodio
    nyaa_map = {}
    for f in nyaa_files:
        ep = extract_episode_number(f.name)
        if ep is not None:
            nyaa_map[ep] = f

    dub_map = {}
    for f in dub_files:
        ep = extract_episode_number(f.name)
        if ep is not None:
            dub_map[ep] = f

    common_eps = sorted(set(nyaa_map.keys()) & set(dub_map.keys()))

    if not common_eps:
        print("[!] No se encontraron episodios coincidentes entre ambas carpetas.", file=sys.stderr)
        print(f"    Nyaa detectados: {sorted(nyaa_map.keys())}")
        print(f"    Dub detectados:  {sorted(dub_map.keys())}")
        return

    print(f"\n[+] Se encontraron {len(common_eps)} episodios emparejados: {common_eps}")

    success_count = 0
    for ep in common_eps:
        n_file = nyaa_map[ep]
        d_file = dub_map[ep]
        
        # Nombre de salida: nombre original de Nyaa agregando [Dual Audio] antes de la extensión
        out_name = f"{n_file.stem} [Dual Audio].mkv"
        out_file = out_path / out_name

        ok = sync_single_episode(
            str(n_file), str(d_file), str(out_file),
            trim=trim, lang=lang, track_name=track_name,
            set_default=set_default, dry_run=dry_run
        )
        if ok:
            success_count += 1

    print("\n" + "=" * 60)
    print(f"[RESUMEN] {success_count} de {len(common_eps)} episodios procesados con éxito.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Sincroniza y transfiere audio latino (Telegram) a videos de alta calidad (Nyaa 1080p) automáticamente."
    )
    # Modo archivo único
    parser.add_argument("nyaa_video", nargs="?", help="Video de Nyaa (1080p / BD / Web-DL)")
    parser.add_argument("dub_video", nargs="?", help="Video o audio con doblaje latino (Telegram 720p)")
    
    # Salida
    parser.add_argument("-o", "--output", help="Archivo de salida (modo archivo) o directorio de salida (modo carpeta)")
    
    # Modo batch (temporada completa)
    parser.add_argument("--nyaa-dir", help="Directorio con videos de Nyaa")
    parser.add_argument("--dub-dir", help="Directorio con videos/audios de Telegram")
    
    # Opciones de audio y ajuste
    parser.add_argument("--trim", type=int, default=360, help="Segundos a analizar para correlación (defecto: 360s = 6 min)")
    parser.add_argument("--lang", default="spa", help="Código de idioma para mkvmerge (defecto: spa)")
    parser.add_argument("--track-name", default="Español Latino", help="Nombre descriptivo de la pista de audio")
    parser.add_argument("--default", action="store_true", help="Establecer la pista latina como audio predeterminado")
    parser.add_argument("--dry-run", action="store_true", help="Calcular el desfase sin generar el archivo final")

    args = parser.parse_args()

    check_prerequisites()

    if args.nyaa_dir and args.dub_dir:
        out_dir = args.output or os.path.join(args.nyaa_dir, "Dual_Audio_Synced")
        sync_batch_directory(
            args.nyaa_dir, args.dub_dir, out_dir,
            trim=args.trim, lang=args.lang,
            track_name=args.track_name, set_default=args.default,
            dry_run=args.dry_run
        )
    elif args.nyaa_video and args.dub_video:
        out_file = args.output
        if not out_file:
            p = Path(args.nyaa_video)
            out_file = str(p.parent / f"{p.stem} [Dual Audio].mkv")
        sync_single_episode(
            args.nyaa_video, args.dub_video, out_file,
            trim=args.trim, lang=args.lang,
            track_name=args.track_name, set_default=args.default,
            dry_run=args.dry_run
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
