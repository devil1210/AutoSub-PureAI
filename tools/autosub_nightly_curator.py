#!/usr/bin/env python3
"""
AutoSub AI - Curador y Remasterizador Nocturno (03:00 AM)
Aprovecha de forma dinámica la cuota gratuita restante de Google AI Studio (Hydrogen / 3.8 Flash)
justo antes de que expire y se reinicie a las 05:00 AM local time.
Solo atiende las series activadas por el usuario en el Panel Web (curator_whitelist).
"""

import sys
import os
import json
import datetime
import urllib.request
import urllib.error

HISTORY_FILE = "/opt/autosub_history.json"
CONFIG_FILE = "/opt/autosub_config.json"
TELEGRAM_CONFIG = "/opt/telegram_config.json"
AUTOSUB_API_ENQUEUE = "http://127.0.0.1:5055/api/batch_enqueue"
DAILY_QUOTA_LIMIT = 20  # Hydrogen y Gemini 3.8 Flash tienen 20 RPD
SAFETY_BUFFER = 2       # Dejar siempre al menos 2 llamadas libres por si entra algo en Sonarr
MAX_NIGHTLY_BATCH = 14  # Máximo físico procesable en ~100 min a 2 RPM

def get_last_reset_datetime(now=None):
    """Calcula la fecha y hora del último reinicio de cuota de Google (05:00 AM local)."""
    now = now or datetime.datetime.now()
    reset_today = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now < reset_today:
        return reset_today - datetime.timedelta(days=1)
    else:
        return reset_today

def send_telegram(text):
    """Envía notificación al chat/tópico de Telegram si está configurado."""
    if not os.path.exists(TELEGRAM_CONFIG):
        return
    try:
        with open(TELEGRAM_CONFIG, "r", encoding="utf-8") as f:
            tcfg = json.load(f)
        token = tcfg.get("bot_token")
        chat_id = tcfg.get("chat_id")
        topic_id = tcfg.get("topic_id")
        if not token or not chat_id:
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if topic_id:
            payload["message_thread_id"] = int(topic_id)

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"[Curator] Error enviando Telegram: {e}", flush=True)

def has_real_speakers(ass_path):
    """Verifica si el archivo .ass tiene nombres de personajes en la columna Actor."""
    if not os.path.exists(ass_path):
        return False
    try:
        with open(ass_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    if len(parts) == 10:
                        spk = parts[3].strip()
                        if spk and spk.lower() not in ["subtitle", "song", "custom", "subtitle-alt", "default", "signs"]:
                            return True
    except Exception:
        pass
    return False

def calculate_budget(history):
    """Calcula cuántos episodios se pueden procesar de forma 100% segura esta noche considerando las claves disponibles."""
    last_reset = get_last_reset_datetime()
    last_reset_str = last_reset.strftime("%Y-%m-%d %H:%M:%S")

    used_today = 0
    for rec in history:
        comp_at = rec.get("completed_at", "")
        if comp_at and comp_at >= last_reset_str:
            if not rec.get("context_only", False):
                used_today += 1

    num_keys = 1
    try:
        from autosub.config import get_gemini_api_keys
        num_keys = max(1, len(get_gemini_api_keys()))
    except Exception:
        pass

    daily_quota_limit = DAILY_QUOTA_LIMIT * num_keys
    safety_buffer = SAFETY_BUFFER * num_keys
    max_nightly_batch = MAX_NIGHTLY_BATCH * num_keys

    remaining_quota = max(0, daily_quota_limit - used_today)
    usable_quota = max(0, remaining_quota - safety_buffer)
    budget = min(max_nightly_batch, usable_quota)

    return budget, used_today, remaining_quota, daily_quota_limit

def evaluate_existing_subtitle_qa(ass_path, profile_data=None):
    """Evalúa un subtítulo existente de forma 100% local (0 tokens) para obtener su nota QA."""
    if not os.path.isfile(ass_path):
        return None
    try:
        from autosub.engine.qa import run_full_subtitle_qa
        import re

        with open(ass_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        d_texts = []
        for line in lines:
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) == 10:
                    t = parts[9].strip()
                    t_clean = re.sub(r'\{[^}]*\}', '', t).replace(r'\N', ' ').strip()
                    if t_clean:
                        d_texts.append(t_clean)
        if not d_texts:
            return None
        return run_full_subtitle_qa(None, d_texts, profile_data=profile_data, run_llm_check=False)
    except Exception as e:
        return None

def find_candidates(history, whitelist=None, app_config=None, limit=10):
    """Encuentra y prioriza episodios para las series habilitadas en la lista blanca:
    1. Nuevos episodios sin subtítulo en español en disco (Prioridad 100).
    2. Episodios existentes pendientes de remasterización con QA bajo o legacy (Prioridad 30 a 99).
    """
    seen_paths = set()
    scored_candidates = []
    history_updated = False

    # 1. Búsqueda canónica asistida por Sonarr y disco de las series activas en whitelist
    if whitelist:
        watch_dirs = []
        try:
            from autosub.core.watcher import get_parsed_watch_paths
            watch_dirs = [p for p, _ in get_parsed_watch_paths()]
        except Exception:
            pass

        if not watch_dirs:
            watch_dirs = ["/data/tvshows", "/data/movies", "/data/doramas"]

        try:
            from autosub.core.sonarr import get_series_episodes_from_sonarr, extract_season_episode_fallback
        except Exception:
            get_series_episodes_from_sonarr = None
            extract_season_episode_fallback = None

        for series_name in whitelist:
            target_dir = None
            for w_dir in watch_dirs:
                cand = os.path.join(w_dir, series_name)
                if os.path.isdir(cand):
                    target_dir = cand
                    break
                # Intento case-insensitive
                if os.path.exists(w_dir):
                    for entry in os.listdir(w_dir):
                        if entry.lower() == series_name.lower():
                            full_cand = os.path.join(w_dir, entry)
                            if os.path.isdir(full_cand):
                                target_dir = full_cand
                                break
                if target_dir:
                    break

            # A. Consultar orden canónico de episodios directamente desde Sonarr
            series_episodes = None
            if get_series_episodes_from_sonarr:
                series_episodes = get_series_episodes_from_sonarr(series_name, series_dir=target_dir)

            if series_episodes:
                print(f"[Curator] [Sonarr] Obtenidos {len(series_episodes)} episodios ordenados canónicamente para '{series_name}'.", flush=True)
                for ep_info in series_episodes:
                    full_p = ep_info["path"]
                    if full_p in seen_paths:
                        continue
                    seen_paths.add(full_p)

                    base_no_ext, _ = os.path.splitext(full_p)
                    has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es-419.ass", ".es.ass", ".es-419.srt", ".es.srt"])
                    ass_path = f"{base_no_ext}.es-419.ass"

                    s_num = ep_info.get("season", 1)
                    e_num = ep_info.get("episode", 1)

                    if not has_sub:
                        # NUEVO EPISODIO: Prioridad máxima para traducir
                        scored_candidates.append({
                            "path": full_p,
                            "title": series_name,
                            "file_name": os.path.basename(full_p),
                            "priority": 100,
                            "season": s_num,
                            "episode": e_num,
                            "qa_score": None,
                            "reason": f"Nuevo S{s_num:02d}E{e_num:02d} sin subtítulo (Sonarr)"
                        })
                    else:
                        qa_score = None
                        for rec in history:
                            if rec.get("path") == full_p:
                                qa_score = rec.get("qa_score")
                                break

                        if qa_score is None and os.path.isfile(ass_path):
                            pre_report = evaluate_existing_subtitle_qa(ass_path)
                            if pre_report:
                                qa_score = pre_report.get("overall_score")

                        if qa_score is not None and qa_score >= 90.0:
                            continue

                        has_speakers = has_real_speakers(ass_path)
                        priority = 0
                        reason = ""
                        if qa_score is not None and qa_score < 80:
                            priority = 100 - qa_score
                            reason = f"Nota QA baja ({qa_score}/100)"
                        elif qa_score is not None and qa_score < 90:
                            priority = 50 + (90 - qa_score)
                            reason = f"Nota Aceptable ({qa_score}/100)"
                        elif not has_speakers:
                            priority = 60
                            reason = "Legacy: sin nota QA ni personajes identificados"
                        else:
                            priority = 30
                            reason = "Legacy: sin auditoría formal QA"

                        scored_candidates.append({
                            "path": full_p,
                            "title": series_name,
                            "file_name": os.path.basename(full_p),
                            "priority": priority,
                            "season": s_num,
                            "episode": e_num,
                            "qa_score": qa_score,
                            "reason": reason
                        })

            # B. Fallback a escaneo en disco si Sonarr no tiene la serie
            elif target_dir and os.path.isdir(target_dir):
                for root, _, files in os.walk(target_dir):
                    for f in sorted(files):
                        if f.lower().endswith((".mkv", ".mp4")):
                            full_p = os.path.join(root, f)
                            if full_p in seen_paths:
                                continue
                            seen_paths.add(full_p)

                            base_no_ext, _ = os.path.splitext(full_p)
                            has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es-419.ass", ".es.ass", ".es-419.srt", ".es.srt"])
                            ass_path = f"{base_no_ext}.es-419.ass"

                            s_num, e_num = (1, 9999)
                            if extract_season_episode_fallback:
                                s_num, e_num = extract_season_episode_fallback(full_p, series_name)

                            if not has_sub:
                                scored_candidates.append({
                                    "path": full_p,
                                    "title": series_name,
                                    "file_name": f,
                                    "priority": 100,
                                    "season": s_num,
                                    "episode": e_num,
                                    "qa_score": None,
                                    "reason": f"Nuevo S{s_num:02d}E{e_num:02d} sin subtítulo (Disco)"
                                })
                            else:
                                qa_score = None
                                for rec in history:
                                    if rec.get("path") == full_p:
                                        qa_score = rec.get("qa_score")
                                        break

                                if qa_score is None and os.path.isfile(ass_path):
                                    pre_report = evaluate_existing_subtitle_qa(ass_path)
                                    if pre_report:
                                        qa_score = pre_report.get("overall_score")

                                if qa_score is not None and qa_score >= 90.0:
                                    continue

                                has_speakers = has_real_speakers(ass_path)
                                priority = 0
                                reason = ""
                                if qa_score is not None and qa_score < 80:
                                    priority = 100 - qa_score
                                    reason = f"Nota QA baja ({qa_score}/100)"
                                elif qa_score is not None and qa_score < 90:
                                    priority = 50 + (90 - qa_score)
                                    reason = f"Nota Aceptable ({qa_score}/100)"
                                elif not has_speakers:
                                    priority = 60
                                    reason = "Legacy: sin nota QA ni personajes identificados"
                                else:
                                    priority = 30
                                    reason = "Legacy: sin auditoría formal QA"

                                scored_candidates.append({
                                    "path": full_p,
                                    "title": series_name,
                                    "file_name": f,
                                    "priority": priority,
                                    "season": s_num,
                                    "episode": e_num,
                                    "qa_score": qa_score,
                                    "reason": reason
                                })

    # 2. Barremos el historial para cualquier otro archivo no visto en disco
    for rec in history:
        path = rec.get("path", "")
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)

        if not os.path.isfile(path):
            continue

        title = rec.get("title") or os.path.basename(os.path.dirname(path))

        # Filtro estricto por lista blanca del panel web
        if whitelist is not None:
            if title not in whitelist:
                continue

        base_dir = os.path.dirname(path)
        base_name = os.path.splitext(os.path.basename(path))[0]
        ass_path = os.path.join(base_dir, f"{base_name}.es-419.ass")

        qa_score = rec.get("qa_score")

        # Triage QA Preventivo (0 tokens): Si no tiene nota pero existe el subtítulo en disco
        if qa_score is None and os.path.isfile(ass_path):
            pre_report = evaluate_existing_subtitle_qa(ass_path)
            if pre_report:
                qa_score = pre_report.get("overall_score")
                rec["qa_score"] = qa_score
                rec["qa_grade"] = pre_report.get("grade")
                rec["qa_badge"] = pre_report.get("badge")
                rec["qa_summary"] = pre_report.get("summary_line")
                history_updated = True

        # Si ya tiene nota y es >= 90 (Aprobado o Excelente), no se toca ni se gastan tokens
        if qa_score is not None and qa_score >= 90.0:
            continue

        has_speakers = has_real_speakers(ass_path)

        priority = 0
        reason = ""

        # Prioridad 1: Nota QA baja conocida (< 80)
        if qa_score is not None and qa_score < 80:
            priority = 100 - qa_score
            reason = f"Nota QA baja ({qa_score}/100: {rec.get('qa_grade')})"
        # Prioridad 2: Nota QA moderada (80 a 89.9, Aceptable)
        elif qa_score is not None and qa_score < 90:
            priority = 50 + (90 - qa_score)
            reason = f"Nota Aceptable ({qa_score}/100: {rec.get('qa_grade')})"
        # Prioridad 3: Subtítulo legacy sin QA y sin diarización de personajes
        elif qa_score is None and not has_speakers:
            priority = 60
            reason = "Legacy: sin nota QA ni personajes identificados"
        # Prioridad 4: Subtítulo legacy sin QA pero con algo de estructura
        elif qa_score is None:
            priority = 30
            reason = "Legacy: sin auditoría formal QA"
        else:
            continue

        s_num, e_num = (1, 9999)
        if extract_season_episode_fallback:
            s_num, e_num = extract_season_episode_fallback(path, title)

        scored_candidates.append({
            "path": path,
            "title": title,
            "file_name": os.path.basename(path),
            "priority": priority,
            "season": s_num,
            "episode": e_num,
            "qa_score": qa_score,
            "reason": reason
        })

    if history_updated and os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            print("[Curator] Historial actualizado con notas de pre-evaluación QA.", flush=True)
        except Exception as e:
            print(f"[Curator] Error actualizando historial con pre-QA: {e}", flush=True)

    # Ordenar estrictamente:
    # 1. Mayor prioridad (Nuevos episodios = 100, luego QA bajas)
    # 2. Temporada canónica (Season 1, Season 2...)
    # 3. Número de episodio canónico (E01, E02, E03...)
    # 4. Nombre de archivo
    scored_candidates.sort(key=lambda x: (-x["priority"], x.get("season", 1), x.get("episode", 1), x["file_name"]))
    return scored_candidates[:limit]

def main():
    dry_run = "--dry-run" in sys.argv
    force_run = "--force" in sys.argv

    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=======================================================", flush=True)
    print(f"[Curator] AutoSub Nightly Curator - {now_str}", flush=True)
    if dry_run:
        print(f"[Curator] Modo SIMULACIÓN (--dry-run) activado.", flush=True)

    # Cargar configuración activa de AutoSub
    app_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                app_config = json.load(f)
        except Exception as e:
            print(f"[Curator] Error leyendo {CONFIG_FILE}: {e}", flush=True)

    whitelist = app_config.get("curator_whitelist", [])
    print(f"[Curator] Series habilitadas en el panel web: {whitelist if whitelist else 'Ninguna (Inactivo)'}", flush=True)

    if not whitelist and not force_run:
        print("[Curator] ⚪ No hay series activadas en la lista blanca de la web. Operación omitida.", flush=True)
        return

    if not os.path.exists(HISTORY_FILE):
        print(f"[Curator] No se encontró archivo de historial en {HISTORY_FILE}. Abortando.", flush=True)
        return

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    budget, used_today, remaining_quota, daily_quota_limit = calculate_budget(history)
    print(f"[Curator] Estado de Cuota de Hoy (desde 05:00 AM):", flush=True)
    print(f"  - Episodios procesados hoy: {used_today} / {daily_quota_limit}", flush=True)
    print(f"  - Cuota restante a consumir: {remaining_quota} llamadas", flush=True)
    print(f"  - Presupuesto autorizado esta noche: {budget} episodios", flush=True)

    if budget <= 0 and not force_run:
        msg = f"🌙 <b>AutoSub Curador Nocturno</b>\nSe alcanzó el límite de seguridad diario ({used_today}/{daily_quota_limit} procesados hoy). Esta noche no se encolarán tareas para proteger la cuota."
        print(f"[Curator] {msg}", flush=True)
        if not dry_run and now.hour == 3:
            send_telegram(msg)
        return

    candidates = find_candidates(history, whitelist=whitelist, app_config=app_config, limit=budget)
    if not candidates:
        print(f"[Curator] No se encontraron episodios pendientes de traducir ni remasterizar para las series activas: {whitelist}.", flush=True)
        return

    print(f"[Curator] Candidatos seleccionados para procesar ({len(candidates)}):", flush=True)
    for idx, c in enumerate(candidates, 1):
        print(f"  [{idx}] {c['title']} - {c['file_name']}", flush=True)
        print(f"       Motivo: {c['reason']} (Prioridad {c['priority']})", flush=True)

    if dry_run:
        print(f"[Curator] Simulación completada con éxito. Se habrían encolado {len(candidates)} episodios.", flush=True)
        return

    payload = {
        "paths": [c["path"] for c in candidates],
        "force": True,
        "target_languages": ["es-419"]
    }

    try:
        req = urllib.request.Request(
            AUTOSUB_API_ENQUEUE,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            print(f"[Curator] Respuesta de AutoSub: {res_data}", flush=True)

        items_str = "\n".join([f"• <b>{c['title']}</b>: {c['file_name']}\n   <i>{c['reason']}</i>" for c in candidates[:5]])
        if len(candidates) > 5:
            items_str += f"\n• ...y {len(candidates) - 5} episodio(s) más."

        tg_msg = (
            f"🌙 <b>AutoSub Curador Nocturno (03:00 AM)</b>\n"
            f"Se han encolado <b>{len(candidates)} episodios</b> de las series en emisión activadas:\n"
            f"<i>(Cuota restante: {remaining_quota} RPD | Reset: 05:00 AM)</i>\n\n"
            f"<b>Episodios seleccionados:</b>\n{items_str}"
        )
        send_telegram(tg_msg)
        print(f"[Curator] Remasterización nocturna lanzada y notificada con éxito.", flush=True)

    except Exception as e:
        print(f"[Curator] Error al encolar en AutoSub: {e}", flush=True)

if __name__ == "__main__":
    main()
