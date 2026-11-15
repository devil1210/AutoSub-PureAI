import json
import time
import urllib.error
import urllib.request
from autosub.config import GEMINI_MODELS, get_gemini_api_key, app_config
from autosub.core.context import next_gemini_lock
from autosub.providers.ollama import clean_think_tags, call_ollama
from autosub.providers.openrouter import call_openrouter
from autosub.providers.rate_limiter import (
    get_next_gemini_allowed,
    set_gemini_cooldown,
    get_model_cooldown,
    set_model_cooldown,
    get_retry_delay_from_error,
    wait_for_gemini_slot
)

def call_gemini_fallback(prompt, system_instruction="", max_retries=2, timeout=35, preferred_model=None):
    api_key = get_gemini_api_key()
    if not api_key:
        return None
    with next_gemini_lock:
        remaining = get_next_gemini_allowed() - time.time()
        if remaining > 0:
            if remaining <= 75:
                print(f"[Gemini Cooldown] Esperando cooldown de {remaining:.1f}s...", flush=True)
                time.sleep(remaining + 0.5)
            else:
                return None

    # Control proactivo de frecuencia: máximo 10 peticiones por minuto
    wait_for_gemini_slot()

    preferred = preferred_model or app_config.get("cloud_model", GEMINI_MODELS[0])
    ordered = [preferred] + [m for m in GEMINI_MODELS if m != preferred]

    contents = []
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"INSTRUCCIONES:\n{system_instruction}\n\nConfirma."}]})
        contents.append({"role": "model", "parts": [{"text": "Entendido."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 8192,
            "temperature": 0.2
        }
    }
    data = json.dumps(payload).encode("utf-8")

    for model_name in ordered:
        cd = get_model_cooldown(model_name)
        if time.time() < cd:
            continue

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(max_retries):
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw = res["candidates"][0]["content"]["parts"][0]["text"]
                    return clean_think_tags(raw)
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="ignore")
                if err.code == 429:
                    delay = get_retry_delay_from_error(body) or 20
                    if delay > 120:
                        set_model_cooldown(model_name, delay)
                        print(f"[Gemini Quota] ⚠️ Cuota diaria/horaria alcanzada en {model_name} ({delay}s). Saltando a siguiente modelo...", flush=True)
                        break
                    else:
                        set_gemini_cooldown(delay)
                        wait_sec = min(delay, 25)
                        print(f"[Gemini RateLimit] ⚠️ 429 en {model_name}. Pausando {wait_sec}s antes de reintentar...", flush=True)
                        time.sleep(wait_sec)
                        continue
                print(f"[Gemini Error] ⚠️ {model_name} HTTP {err.code}: {body[:250]}", flush=True)
                time.sleep(2)
            except Exception as ex:
                print(f"[Gemini Error] ⚠️ {model_name} Excepción: {ex}", flush=True)
                time.sleep(2)
    return None

def call_ai(prompt, system_instruction="", timeout=180, allow_cloud_override=False, preferred_model=None):
    res = call_ollama(prompt, system_instruction=system_instruction, timeout=timeout)
    if res:
        return res
    if app_config.get("local_only", True) and not allow_cloud_override:
        return None
    if app_config.get("enable_openrouter", False):
        res = call_openrouter(prompt, system_instruction=system_instruction, timeout=timeout)
        if res:
            return res
    return call_gemini_fallback(prompt, system_instruction=system_instruction, timeout=timeout, preferred_model=preferred_model)
