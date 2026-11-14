import argparse
from http.server import ThreadingHTTPServer
import os
import sys
import threading

# Asegurar que el directorio base esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autosub.config import PORT, app_config
from autosub.core.queue import queue_worker, retry_worker, load_persisted_queue
from autosub.engine.pipeline import process_file
from autosub.subtitles.mkv import check_mkvtoolnix
from autosub.web.server import AutoSubHandler

def parse_args():
    parser = argparse.ArgumentParser(description="AutoSub PureAI v4.0 - Cognitive Subtitle Engine & Web Dashboard")
    parser.add_argument("--port", type=int, default=PORT, help=f"Puerto del servidor web (default: {PORT})")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host de enlace para el servidor web (default: 0.0.0.0)")
    parser.add_argument("--single-file", type=str, default=None, help="Procesa un único archivo de video directamente")
    parser.add_argument("--force", action="store_true", help="Fuerza la regeneración de subtítulos existentes")
    parser.add_argument("--context-only", action="store_true", help="Solo extrae y guarda contexto narrativo sin traducir")
    parser.add_argument("--local-only", action="store_true", help="Fuerza el uso exclusivo de modelos locales (Ollama)")
    parser.add_argument("--headless", action="store_true", help="Ejecuta en modo headless sin logs interactivos adicionales")
    return parser.parse_args()

def main():
    args = parse_args()

    if args.local_only:
        app_config["local_only"] = True

    mkv_ok, mkvmerge_path, mkvextract_path = check_mkvtoolnix()
    print(f"=== AutoSub Pure AI v4.0 (Elephant-Goldfish Subtitle Engine) - Puerto {args.port} ===", flush=True)
    if mkv_ok:
        print(f"[AutoSub] MKVToolNix detectado: mkvmerge={mkvmerge_path}, mkvextract={mkvextract_path}", flush=True)
    else:
        print("[AutoSub] ⚠️ ADVERTENCIA: mkvmerge o mkvextract no encontrados en PATH. La extracción de pistas fallará a menos que se instalen.", flush=True)

    load_persisted_queue()

    if args.single_file:
        file_path = os.path.abspath(args.single_file)
        if not os.path.isfile(file_path):
            print(f"[AutoSub] Error: Archivo no encontrado '{file_path}'", file=sys.stderr)
            sys.exit(1)
        print(f"[AutoSub] Procesando archivo individual: {file_path}", flush=True)
        success = process_file(file_path, force=args.force, context_only=args.context_only)
        sys.exit(0 if success else 1)

    # Iniciar workers en segundo plano
    threading.Thread(target=queue_worker, args=(process_file,), daemon=True).start()
    threading.Thread(target=retry_worker, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), AutoSubHandler)
    print(f"[AutoSub] Servidor web iniciado en http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[AutoSub] Deteniendo servidor...", flush=True)
        server.server_close()

if __name__ == "__main__":
    main()
