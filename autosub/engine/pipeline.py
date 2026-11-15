import datetime
import json
import os
import subprocess
import tempfile
import time
from autosub.config import app_config, SUPPORTED_TARGET_LANGUAGES
from autosub.core.context import (
    currently_processing,
    current_file_start_time,
    current_batch_num,
    current_total_batches,
    total_processing_time_sec,
    completed_jobs_count,
    cancel_current_flag,
    last_job_metrics,
    processing_lock,
    cancel_lock,
    queue_lock,
    retry_queue
)
from autosub.core.history import format_duration, append_history_record
from autosub.core.queue import enqueue_retry, save_persisted_queue
from autosub.engine.diarization import attribute_episode_speakers
from autosub.engine.elephant import extract_series_name, get_or_create_series_profile, generate_and_save_episode_context
from autosub.engine.goldfish import (
    translate_batch,
    translate_atomic_batch,
    smart_boundary_slicing,
    ass_timestamp_to_seconds,
    srt_timestamp_to_seconds,
    proofread_subtitles,
    audit_and_polish_subtitles,
    clean_split_dialogue_punctuation
)
from autosub.engine.qa import run_full_subtitle_qa
from autosub.providers.ollama import is_ollama_alive
from autosub.subtitles.mkv import find_matching_target_track, select_source_track
from autosub.subtitles.parser import is_translatable_ass_line
from autosub.subtitles.serializer import save_ass_and_srt_outputs, save_srt_output

def process_file(video_path, force=False, context_only=False, target_languages=None):
    import autosub.core.context as ctx

    start_t = time.time()
    with cancel_lock:
        ctx.cancel_current_flag = False
    with processing_lock:
        ctx.currently_processing = os.path.basename(video_path)
        ctx.current_file_start_time = start_t
        ctx.current_batch_num = 0
        ctx.current_total_batches = 0
        ctx.last_job_metrics = {}

    used_targets = target_languages or app_config.get("target_languages", ["es-419"])
    if isinstance(used_targets, str):
        used_targets = [used_targets]

    targets_label = ", ".join(used_targets)
    log_mode = "Extrayendo Solo Contexto" if context_only else f"Procesando [{targets_label}]"
    print(f"\n[AutoSub] {log_mode}: {video_path}", flush=True)
    success = False
    series_name = extract_series_name(video_path)
    try:
        success = _process_file_inner(video_path, force=force, context_only=context_only, target_languages=used_targets)
    finally:
        elapsed = time.time() - start_t
        with processing_lock:
            ctx.total_processing_time_sec += elapsed
            if success:
                ctx.completed_jobs_count += 1
                lines = ctx.last_job_metrics.get("lines_count", 0)
                fmt = "Contexto" if context_only else f"{ctx.last_job_metrics.get('format', 'SRT')} [{targets_label}]"
                model_used = app_config.get("local_model") if app_config.get("local_only") else app_config.get("cloud_model")
                status_txt = "Contexto Extraído" if context_only else "Completado"
                qa_info = ctx.last_job_metrics.get("qa_report") or {}
                record = {
                    "id": int(time.time() * 1000),
                    "file_name": os.path.basename(video_path),
                    "path": video_path,
                    "title": series_name,
                    "completed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_sec": round(elapsed, 1),
                    "duration_formatted": format_duration(elapsed),
                    "lines_count": lines,
                    "format": fmt,
                    "target_languages": used_targets,
                    "model": model_used,
                    "proofread": bool(app_config.get("proofread", True)),
                    "status": status_txt,
                    "qa_score": qa_info.get("overall_score"),
                    "qa_badge": qa_info.get("badge"),
                    "qa_summary": qa_info.get("summary_line"),
                    "qa_grade": qa_info.get("grade")
                }
                append_history_record(record)
            ctx.currently_processing = None
            ctx.current_file_start_time = 0.0
            ctx.current_batch_num = 0
            ctx.current_total_batches = 0
    return success

def _process_file_inner(video_path, force=False, context_only=False, target_languages=None):
    import autosub.core.context as ctx

    if not os.path.isfile(video_path):
        return False

    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    series_name = extract_series_name(video_path)

    if target_languages and isinstance(target_languages, list) and len(target_languages) > 0:
        target_langs = target_languages
    else:
        target_langs = app_config.get("target_languages", ["es-419"])

    if context_only:
        existing_sub = None
        for candidate_ext in [".es-419.ass", ".es.ass", ".es-419.srt", ".es.srt", ".spa.srt"]:
            cand_path = os.path.join(base_dir, f"{base_name}{candidate_ext}")
            if os.path.exists(cand_path) and os.path.getsize(cand_path) > 0:
                existing_sub = cand_path
                break

        if existing_sub:
            print(f"[AutoSub] Modo Solo Contexto: Leyendo subtítulo en español existente '{os.path.basename(existing_sub)}'...", flush=True)
            dialogues = []
            if existing_sub.endswith(".ass") or existing_sub.endswith(".ssa"):
                try:
                    with open(existing_sub, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if line.startswith("Dialogue:"):
                                parts = line.split(",", 9)
                                if len(parts) == 10:
                                    s_name = parts[3].strip()
                                    t_str = parts[9].rstrip("\r\n")
                                    if is_translatable_ass_line(s_name, t_str):
                                        dialogues.append(t_str)
                except Exception as e:
                    print(f"[AutoSub] Error leyendo {existing_sub}: {e}", flush=True)
            else:
                try:
                    with open(existing_sub, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    blocks = content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
                    for b in blocks:
                        lines = b.split("\n")
                        if len(lines) >= 3 and "-->" in lines[1]:
                            dialogues.append("\\N".join(lines[2:]))
                except Exception as e:
                    print(f"[AutoSub] Error leyendo {existing_sub}: {e}", flush=True)

            if dialogues:
                profile_data, profile_path = get_or_create_series_profile(series_name)
                raw_dialogues = []
                try:
                    p_info = subprocess.run(["mkvmerge", "-J", video_path], capture_output=True, text=True, check=True)
                    m_info = json.loads(p_info.stdout)
                    sub_trks = [t for t in m_info.get("tracks", []) if t.get("type") == "subtitles"]
                    source_priority = app_config.get("source_priority", ["ja", "en", "fr", "de", "es", "it", "pt", "ru", "zh"])
                    pref_trk = select_source_track(sub_trks, source_priority)
                    if pref_trk:
                        t_ext = ".ass" if "substation" in pref_trk.get("codec", "").lower() or "ass" in pref_trk.get("codec", "").lower() else ".srt"
                        t_tmp = os.path.join(tempfile.gettempdir(), f"autosub_ctx_{int(time.time()*1000)}_{base_name}{t_ext}")
                        subprocess.run(["mkvextract", "tracks", video_path, f"{pref_trk['id']}:{t_tmp}"], check=True, capture_output=True)
                        if os.path.exists(t_tmp):
                            if t_ext == ".ass":
                                with open(t_tmp, "r", encoding="utf-8", errors="ignore") as f_tmp:
                                    for l_tmp in f_tmp:
                                        if l_tmp.startswith("Dialogue:"):
                                            p_tmp = l_tmp.split(",", 9)
                                            if len(p_tmp) == 10 and is_translatable_ass_line(p_tmp[3].strip(), p_tmp[9].rstrip("\r\n")):
                                                raw_dialogues.append(p_tmp[9].rstrip("\r\n"))
                            else:
                                with open(t_tmp, "r", encoding="utf-8", errors="ignore") as f_tmp:
                                    c_tmp = f_tmp.read()
                                b_tmp = c_tmp.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
                                for bk in b_tmp:
                                    lks = bk.split("\n")
                                    if len(lks) >= 3 and "-->" in lks[1]:
                                        raw_dialogues.append("\\N".join(lks[2:]))
                            try:
                                os.remove(t_tmp)
                            except Exception:
                                pass
                except Exception:
                    pass

                passed_raw = raw_dialogues if len(raw_dialogues) == len(dialogues) else dialogues
                generate_and_save_episode_context(series_name, video_path, passed_raw, dialogues, profile_data, profile_path)
                ctx.last_job_metrics["lines_count"] = len(dialogues)
                ctx.last_job_metrics["format"] = "Contexto (Subtítulo Existente)"
                return True

    if app_config.get("local_only", True) and not is_ollama_alive(timeout=1.2):
        enqueue_retry(video_path, force, series_name, "PC apagada (esperando RTX 4070)", increment_attempt=False, context_only=context_only, target_languages=target_langs)
        return False

    if not force and not context_only:
        all_exist = True
        for tl in target_langs:
            ext_code = SUPPORTED_TARGET_LANGUAGES.get(tl, {}).get("ext", tl).lower()
            cands = [f".{ext_code}.ass", f".{ext_code}.srt"]
            if ext_code in ["es", "es-419"]:
                cands.extend([".es-419.ass", ".es.ass", ".es-419.srt", ".es.srt", ".spa.srt"])
            if not any(os.path.exists(os.path.join(base_dir, f"{base_name}{c}")) for c in cands):
                all_exist = False
                break
        if all_exist:
            return True

    try:
        proc = subprocess.run(["mkvmerge", "-J", video_path], capture_output=True, text=True, check=True)
        info = json.loads(proc.stdout)
    except Exception as e:
        enqueue_retry(video_path, force, series_name, f"Error mkvmerge: {e}", context_only=context_only, target_languages=target_langs)
        return False

    tracks = info.get("tracks", [])
    sub_tracks = [t for t in tracks if t.get("type") == "subtitles"]
    if not sub_tracks:
        return False

    profile_data, profile_path = get_or_create_series_profile(series_name)
    source_priority = app_config.get("source_priority", ["ja", "en", "fr", "de", "es", "it", "pt", "ru", "zh"])
    base_batch = app_config.get("batch_size", 100)
    audit_enabled = app_config.get("audit_existing", True)
    proofread_enabled = app_config.get("proofread", True)

    num_targets = max(1, len(target_langs))
    if num_targets > 1:
        batch_size = max(25, int(base_batch / (1 + 0.35 * (num_targets - 1))))
    else:
        batch_size = base_batch

    source_data = None
    created_temp_files = []
    any_success = False

    try:
        for lang_idx, target_lang in enumerate(target_langs):
            lang_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, {})
            lang_name = lang_meta.get("name", target_lang)
            ext_code = lang_meta.get("ext", target_lang).lower()
            out_ass = os.path.join(base_dir, f"{base_name}.{ext_code}.ass")
            out_srt = os.path.join(base_dir, f"{base_name}.{ext_code}.srt")

            existing_target_trk = find_matching_target_track(sub_tracks, target_lang) if audit_enabled else None
            if existing_target_trk:
                trk_id = existing_target_trk["id"]
                trk_codec = existing_target_trk.get("codec", "")
                trk_is_ass = "substation" in trk_codec.lower() or "ass" in trk_codec.lower() or "ssa" in trk_codec.lower()
                t_ext = ".ass" if trk_is_ass else ".srt"
                tmp_tgt = os.path.join(tempfile.gettempdir(), f"autosub_audit_{int(time.time()*1000)}_{base_name}{t_ext}")
                created_temp_files.append(tmp_tgt)
                try:
                    subprocess.run(["mkvextract", "tracks", video_path, f"{trk_id}:{tmp_tgt}"], check=True, capture_output=True)
                    if os.path.exists(tmp_tgt) and os.path.getsize(tmp_tgt) > 0:
                        if trk_is_ass:
                            with open(tmp_tgt, "r", encoding="utf-8", errors="ignore") as f_ass:
                                ass_lines = f_ass.readlines()
                            d_indices = []
                            d_texts = []
                            for idx, line in enumerate(ass_lines):
                                if line.startswith("Dialogue:"):
                                    parts = line.split(",", 9)
                                    if len(parts) == 10:
                                        s_name = parts[3].strip()
                                        t_val = parts[9].rstrip("\r\n")
                                        if is_translatable_ass_line(s_name, t_val):
                                            d_indices.append(idx)
                                            d_texts.append(t_val)

                            if not d_texts:
                                continue

                            if context_only:
                                generate_and_save_episode_context(series_name, video_path, d_texts, d_texts, profile_data, profile_path)
                                ctx.last_job_metrics["lines_count"] = len(d_texts)
                                ctx.last_job_metrics["format"] = "Contexto (Pista Extraída)"
                                return True

                            ctx.last_job_metrics["lines_count"] = len(d_texts)
                            ctx.last_job_metrics["format"] = f"ASS + SRT (Pista {trk_id} Auditada)"
                            trk_name_lbl = existing_target_trk.get("properties", {}).get("track_name", "")
                            print(f"[AutoSub] Pista preexistente encontrada para {target_lang} (Pista {trk_id}: {trk_name_lbl}). Auditando y puliendo con IA ({len(d_texts)} líneas)...", flush=True)

                            ep_context = generate_and_save_episode_context(series_name, video_path, d_texts, d_texts, profile_data, profile_path)
                            if proofread_enabled:
                                audited_texts = audit_and_polish_subtitles(d_texts, profile_data, batch_size=batch_size, episode_context=ep_context, target_lang=target_lang)
                            else:
                                audited_texts = d_texts

                            save_ass_and_srt_outputs(out_ass, out_srt, ass_lines, d_indices, audited_texts)
                            try:
                                qa_report = run_full_subtitle_qa(
                                    d_texts,
                                    audited_texts,
                                    profile_data=profile_data,
                                    run_llm_check=True
                                )
                                ctx.last_job_metrics["qa_report"] = qa_report
                                print(f"[QA Inspector] {qa_report['summary_line']}", flush=True)
                            except Exception as e_qa:
                                print(f"[QA Inspector] ⚠️ Error ejecutando auditoría QA: {e_qa}", flush=True)
                            any_success = True
                            continue
                        else:
                            with open(tmp_tgt, "r", encoding="utf-8", errors="ignore") as f_srt:
                                srt_content = f_srt.read()
                            blocks = srt_content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
                            srt_cues = []
                            for b in blocks:
                                blines = b.split("\n")
                                if len(blines) >= 3 and "-->" in blines[1]:
                                    srt_cues.append({
                                        "id": blines[0],
                                        "time": blines[1],
                                        "lines": blines[2:]
                                    })
                            srt_texts = ["\\N".join(c["lines"]) for c in srt_cues]
                            if not srt_texts:
                                continue

                            if context_only:
                                generate_and_save_episode_context(series_name, video_path, srt_texts, srt_texts, profile_data, profile_path)
                                ctx.last_job_metrics["lines_count"] = len(srt_texts)
                                ctx.last_job_metrics["format"] = "Contexto (Pista Extraída)"
                                return True

                            ctx.last_job_metrics["lines_count"] = len(srt_texts)
                            ctx.last_job_metrics["format"] = f"SRT (Pista {trk_id} Auditada)"
                            ep_context = generate_and_save_episode_context(series_name, video_path, srt_texts, srt_texts, profile_data, profile_path)
                            if proofread_enabled:
                                audited_texts = audit_and_polish_subtitles(srt_texts, profile_data, batch_size=batch_size, episode_context=ep_context, target_lang=target_lang)
                            else:
                                audited_texts = srt_texts

                            save_srt_output(out_srt, srt_cues, audited_texts)
                            try:
                                qa_report = run_full_subtitle_qa(
                                    srt_texts,
                                    audited_texts,
                                    profile_data=profile_data,
                                    run_llm_check=True
                                )
                                ctx.last_job_metrics["qa_report"] = qa_report
                                print(f"[QA Inspector] {qa_report['summary_line']}", flush=True)
                            except Exception as e_qa:
                                print(f"[QA Inspector] ⚠️ Error ejecutando auditoría QA: {e_qa}", flush=True)
                            any_success = True
                            continue
                except Exception as e_aud:
                    print(f"[AutoSub] ⚠️ Fallo al auditar pista preexistente ({e_aud}), recurriendo a traducción tradicional...", flush=True)

            if source_data is None:
                selected_track = select_source_track(sub_tracks, source_priority)
                if not selected_track:
                    continue
                track_id = selected_track["id"]
                codec = selected_track.get("codec", "")
                src_is_ass = "substation" in codec.lower() or "ass" in codec.lower() or "ssa" in codec.lower()
                temp_ext = ".ass" if src_is_ass else ".srt"
                temp_file = os.path.join(tempfile.gettempdir(), f"autosub_raw_{int(time.time()*1000)}_{base_name}{temp_ext}")
                created_temp_files.append(temp_file)
                subprocess.run(["mkvextract", "tracks", video_path, f"{track_id}:{temp_file}"], check=True, capture_output=True)
                if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
                    continue

                if src_is_ass:
                    with open(temp_file, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    dialogue_indices = []
                    raw_texts = []
                    durations = []
                    for idx, line in enumerate(lines):
                        if line.startswith("Dialogue:"):
                            parts = line.split(",", 9)
                            if len(parts) == 10:
                                style_name = parts[3].strip()
                                txt = parts[9].rstrip("\r\n")
                                if is_translatable_ass_line(style_name, txt):
                                    dialogue_indices.append(idx)
                                    raw_texts.append(txt)
                                    dur = ass_timestamp_to_seconds(parts[2]) - ass_timestamp_to_seconds(parts[1])
                                    durations.append(max(0.0, dur))
                    source_data = {
                        "is_ass": True,
                        "lines": lines,
                        "indices": dialogue_indices,
                        "texts": raw_texts,
                        "durations": durations,
                        "track": selected_track
                    }
                else:
                    with open(temp_file, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    blocks = content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
                    cues = []
                    durations = []
                    for b in blocks:
                        lines = b.split("\n")
                        if len(lines) >= 3 and "-->" in lines[1]:
                            cues.append({
                                "id": lines[0],
                                "time": lines[1],
                                "lines": lines[2:]
                            })
                            p = lines[1].split("-->")
                            dur = srt_timestamp_to_seconds(p[1]) - srt_timestamp_to_seconds(p[0])
                            durations.append(max(0.0, dur))
                    all_texts = ["\\N".join(c["lines"]) for c in cues]
                    source_data = {
                        "is_ass": False,
                        "cues": cues,
                        "texts": all_texts,
                        "durations": durations,
                        "track": selected_track
                    }

            if not source_data or not source_data["texts"]:
                continue

            src_texts = source_data["texts"]
            if context_only:
                generate_and_save_episode_context(series_name, video_path, src_texts, src_texts, profile_data, profile_path)
                ctx.last_job_metrics["lines_count"] = len(src_texts)
                ctx.last_job_metrics["format"] = "Contexto (Pista Extraída)"
                return True

            # Identificación y atribución de hablantes (Diarization)
            speakers = attribute_episode_speakers(video_path, src_texts, profile_data)
            ctx.last_job_metrics["lines_count"] = len(src_texts)
            ctx.last_job_metrics["format"] = "ASS + SRT" if source_data["is_ass"] else "SRT"
            if speakers:
                ctx.last_job_metrics["speakers_count"] = sum(1 for s in speakers if s)

            packaged_items = [
                {"text": txt, "duration": dur, "speaker": spk}
                for txt, dur, spk in zip(
                    source_data["texts"],
                    source_data.get("durations", [0.0] * len(source_data["texts"])),
                    speakers if (speakers and len(speakers) == len(source_data["texts"])) else [""] * len(source_data["texts"])
                )
            ]
            chunks = smart_boundary_slicing(packaged_items, min_chunk=20, max_chunk=35)
            total_batches = len(chunks)
            with processing_lock:
                ctx.current_total_batches = total_batches * num_targets

            print(f"[AutoSub] ({lang_idx+1}/{num_targets}) Traduciendo al {lang_name} ({target_lang}) en {total_batches} lotes atómicos EGM (20-35 cues)...", flush=True)

            translated_texts = []
            prev_ctx = None
            translation_success = True

            for b_idx, chunk in enumerate(chunks):
                batch_num = (lang_idx * total_batches) + (b_idx + 1)
                with processing_lock:
                    ctx.current_batch_num = batch_num

                with cancel_lock:
                    if ctx.cancel_current_flag:
                        translation_success = False
                        print(f"[AutoSub] Cancelado por el usuario: {video_path}", flush=True)
                        break

                batch_texts = [it["text"] for it in chunk]
                batch_durations = [it["duration"] for it in chunk]
                batch_speakers = [it.get("speaker", "") for it in chunk]
                trans = translate_atomic_batch(
                    batch_texts,
                    profile_data,
                    prev_context=prev_ctx,
                    target_lang=target_lang,
                    durations=batch_durations,
                    proofread=proofread_enabled,
                    speakers=batch_speakers
                )
                if trans is None:
                    translation_success = False
                    break
                translated_texts.extend(trans)
                prev_ctx = batch_texts[-2:]
                time.sleep(0.2)

            if not translation_success:
                enqueue_retry(video_path, force, series_name, f"Error IA traduciendo lote ({target_lang})", context_only=context_only, target_languages=target_langs)
                continue

            translated_texts = clean_split_dialogue_punctuation(translated_texts)
            ep_context = generate_and_save_episode_context(series_name, video_path, src_texts, translated_texts, profile_data, profile_path)

            if source_data["is_ass"]:
                save_ass_and_srt_outputs(out_ass, out_srt, source_data["lines"], source_data["indices"], translated_texts, speakers=speakers)
            else:
                save_srt_output(out_srt, source_data["cues"], translated_texts)

            # Control de Calidad (QA Inspector)
            try:
                qa_report = run_full_subtitle_qa(
                    src_texts,
                    translated_texts,
                    durations=source_data.get("durations"),
                    profile_data=profile_data,
                    run_llm_check=True
                )
                ctx.last_job_metrics["qa_report"] = qa_report
                print(f"[QA Inspector] {qa_report['summary_line']}", flush=True)
            except Exception as e_qa:
                print(f"[QA Inspector] ⚠️ Error ejecutando auditoría QA: {e_qa}", flush=True)

            any_success = True
            if num_targets > 1:
                time.sleep(0.3)

        if not any_success:
            return False

        with queue_lock:
            if video_path in retry_queue:
                del retry_queue[video_path]
                save_persisted_queue()

        if any_success and not context_only:
            try:
                try:
                    from tools.telegram_notifier import send_episode_rich_notification
                except ImportError:
                    from telegram_notifier import send_episode_rich_notification
                qa_report = ctx.last_job_metrics.get("qa_report")
                send_episode_rich_notification(video_path, qa_report=qa_report)
                print(f"[AutoSub] Notificación de Telegram enviada para {os.path.basename(video_path)}", flush=True)
            except Exception as ex_tg:
                print(f"[AutoSub] Error enviando notificación de Telegram: {ex_tg}", flush=True)

    except Exception as ex:
        enqueue_retry(video_path, force, series_name, str(ex), context_only=context_only, target_languages=target_langs)
        return False
    finally:
        for tf in created_temp_files:
            if tf and os.path.exists(tf):
                try:
                    os.remove(tf)
                except OSError:
                    pass
    return True
