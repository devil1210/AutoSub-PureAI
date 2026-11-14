#!/usr/bin/env python3
"""
Sync Official English Titles from Sonarr to Jellyfin while preserving Spanish Synopses.
Locks the 'Name' field in Jellyfin so future library refreshes keep clean English titles.
"""

import os
import time
import json
import urllib.request
from typing import Dict, Any, List

SONARR_URL = os.getenv("SONARR_URL", "http://192.168.1.131:8989")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "91528f758b7f42be9b10852ee230bf41")

JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://192.168.1.124:8096")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9")
JELLYFIN_USER_ID = os.getenv("JELLYFIN_USER_ID", "123e41c092994566b62c27fb5e7f526a")


def get_sonarr_titles() -> Dict[str, str]:
    """Fetches series from Sonarr mapped by TVDB ID, TMDB ID, and folder name."""
    url = f"{SONARR_URL}/api/v3/series?apikey={SONARR_API_KEY}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AutoSub-JellyfinSync/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            series = json.loads(r.read())
            
        by_tvdb = {str(s.get("tvdbId")): s.get("title") for s in series if s.get("tvdbId")}
        by_tmdb = {str(s.get("tmdbId")): s.get("title") for s in series if s.get("tmdbId")}
        by_path = {s.get("path", "").rstrip("/").split("/")[-1].lower(): s.get("title") for s in series if s.get("path")}
        return by_tvdb, by_tmdb, by_path
    except Exception as e:
        print(f"[!] Error conectando a Sonarr: {e}")
        return {}, {}, {}


def sync_titles_to_jellyfin() -> int:
    by_tvdb, by_tmdb, by_path = get_sonarr_titles()
    if not by_tvdb and not by_tmdb and not by_path:
        print("[!] No se pudieron obtener títulos de Sonarr.")
        return 0

    jf_url = f"{JELLYFIN_URL}/Users/{JELLYFIN_USER_ID}/Items?api_key={JELLYFIN_API_KEY}&IncludeItemTypes=Series&fields=Overview,ProviderIds,Path,LockedFields&limit=500"
    try:
        req = urllib.request.Request(jf_url, headers={"User-Agent": "AutoSub-JellyfinSync/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            jf_series = json.loads(r.read()).get("Items", [])
    except Exception as e:
        print(f"[!] Error obteniendo series de Jellyfin: {e}")
        return 0

    print(f"[*] Analizando {len(jf_series)} series en Jellyfin...")
    updated_count = 0

    items_to_process = []
    for it in jf_series:
        current_name = it.get("Name", "")
        item_id = it.get("Id")
        pids = it.get("ProviderIds", {})
        tvdb_id = str(pids.get("Tvdb") or "")
        tmdb_id = str(pids.get("Tmdb") or "")
        path = it.get("Path", "")
        folder_name = path.rstrip("/").split("/")[-1].lower() if path else ""

        english_title = (
            by_tvdb.get(tvdb_id) or
            by_tmdb.get(tmdb_id) or
            by_path.get(folder_name) or
            (path.rstrip("/").split("/")[-1] if path else "")
        )

        if not english_title:
            continue

        locked = it.get("LockedFields", []) or []
        needs_rename = (current_name != english_title)
        if needs_rename:
            items_to_process.append((item_id, current_name, english_title))

    print(f"[*] Procesando {len(items_to_process)} series que tienen títulos en japonés o español y deben cambiarse a inglés...")

    updated_count = 0
    for item_id, current_name, english_title in items_to_process:
        item_url = f"{JELLYFIN_URL}/Users/{JELLYFIN_USER_ID}/Items/{item_id}?api_key={JELLYFIN_API_KEY}"
        try:
            with urllib.request.urlopen(item_url, timeout=10) as ir:
                full_item = json.loads(ir.read())

            full_item["Name"] = english_title
            cur_locked = full_item.get("LockedFields", []) or []
            if "Name" not in cur_locked:
                cur_locked.append("Name")
            full_item["LockedFields"] = cur_locked

            post_url = f"{JELLYFIN_URL}/Items/{item_id}?api_key={JELLYFIN_API_KEY}"
            post_data = json.dumps(full_item).encode("utf-8")
            post_req = urllib.request.Request(
                post_url, data=post_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(post_req, timeout=10) as pr:
                if pr.status in (200, 204):
                    c_safe = current_name.encode('ascii', 'replace').decode('ascii')
                    e_safe = english_title.encode('ascii', 'replace').decode('ascii')
                    print(f"  [OK] '{c_safe}' -> '{e_safe}'", flush=True)
                    updated_count += 1
            time.sleep(0.15)
        except Exception as ex:
            c_safe = current_name.encode('ascii', 'replace').decode('ascii')
            print(f"  [!] Error en '{c_safe}': {ex}", flush=True)

    print(f"\n[+] Sincronización completada. Se renombraron {updated_count} series en Jellyfin manteniendo sus sinopsis en español y bloqueando el título.")
    return updated_count


if __name__ == "__main__":
    sync_titles_to_jellyfin()
