---
name: autosub-ops
description: Operations, deployment, API reference, and troubleshooting guide for AutoSub AI (pure AI subtitle translator and control center).
---

# AutoSub AI Operations & Architecture Guide

This skill documents the deployment runbooks, architecture, API surface, and maintenance workflows for **AutoSub AI v3.5**.

## 1. Architecture Overview

- **Service File:** `autosub_translator_v3.5.py`
- **Remote Production Target:** `root@192.168.1.125:/opt/translator.py`
- **Local Runtime / Port:** Port `5055` (`http://192.168.1.125:5055/`)
- **Configuration File:** `/opt/autosub_config.json`
- **Persistent Queue:** `/opt/autosub_queue.json`
- **Persistent History:** `/opt/autosub_history.json`
- **Series Narrative Profiles:** `/data/tvshows/.profiles/{series_name}.json`

---

## 2. Remote Deployment & Service Lifecycle

### Deploy Code to Server
```bash
scp autosub_translator_v3.5.py root@192.168.1.125:/opt/translator.py
```

### Restart Service
```bash
ssh root@192.168.1.125 "killall -9 python3; sleep 1; nohup python3 /opt/translator.py > /opt/autosub.log 2>&1 &"
```

### Check Process & Live Logs
```bash
ssh root@192.168.1.125 "ps aux | grep translator.py | grep -v grep; tail -n 50 /opt/autosub.log"
```

---

## 3. Supported Translation Models & Quota Safety

| Provider | Model | Tier / Daily Quota | Notes |
| :--- | :--- | :--- | :--- |
| **Google Gemini** | `gemini-3.5-flash-lite` | ~500 RPD | Primary cloud model. Ultra-fast (~2.6s per batch). |
| **Google Gemini** | `gemini-3.1-flash-lite` | ~500 RPD | Fast secondary fallback. |
| **Google Gemma** | `gemma-4-31b-it` | ~14,400 RPD | High-volume cloud fallback. |
| **Local GPU (Ollama)** | `qwen3.5:9b` / `qwen3:14b` | Unlimited | Runs on LAN RTX 4070 (`192.168.1.12:11434`). |
| **OpenRouter** | Free models | Variable | Optional emergency fallback. |

### Adaptive Batching Quota Protection
When multiple target languages ($N > 1$) are enqueued simultaneously:
$$\text{batch\_size} = \max\left(25, \left\lfloor \frac{\text{base\_batch}}{1 + 0.35 \times (N - 1)} \right\rfloor\right)$$
A pacing delay of `0.3s` is added between each target language pass.

---

## 4. Subtitle Specifications & Languages

AutoSub outputs Plex/Jellyfin standard subtitle naming:
- `es-419` (`.es-419.ass` / `.es-419.srt`): Español Latinoamericano neutro
- `es` (`.es.ass` / `.es.srt`): Español de España (Castellano)
- `en` (`.en.ass` / `.en.srt`): English
- `fr` (`.fr.ass` / `.fr.srt`): Français
- `de` (`.de.ass` / `.de.srt`): Deutsch
- `pt-BR` (`.pt-br.ass` / `.pt-br.srt`): Português brasileiro
- `it` (`.it.ass` / `.it.srt`): Italiano
- `ja` (`.ja.ass` / `.ja.srt`): 日本語
- `ru` (`.ru.ass` / `.ru.srt`): Русский
- `zh-CN` (`.zh.ass` / `.zh.srt`): 简体中文

---

## 5. REST API Endpoints

- `GET /api/status`: JSON summary of active workers, queue items, and memory stats.
- `GET /api/history`: Recent completed and failed translation records.
- `GET /api/series_list`: Catalog of media items grouped by custom library names.
- `GET /api/series_files?path=...`: Files inside a specific series folder with subtitle status badge.
- `POST /api/batch_enqueue`:
  ```json
  {
    "paths": ["/data/tvshows/Anime/S01E01.mkv"],
    "force": true,
    "context_only": false,
    "target_languages": ["es-419", "en"]
  }
  ```
- `POST /api/config`: Save app configuration (`libraries`, `source_priority`, `cloud_model`, etc.).
- `POST /api/cancel`: Abort current translating job.
