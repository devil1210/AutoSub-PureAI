import json
import time
import threading
import urllib.error
import urllib.request
from autosub.config import GEMINI_MODELS, ELEPHANT_MODELS, DIARIZATION_MODELS, get_gemini_api_key, get_gemini_api_keys, app_config
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

# Pool de Rotación Automática y Failover de Claves Gemini
_key_cooldowns = {}   # {key: timestamp_until_available}
_key_index = 0
_key_lock = threading.Lock()

def get_next_gemini_key() -> str:
    global _key_index
    keys = get_gemini_api_keys()
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]
    now = time.time()
    with _key_lock:
        active = [k for k in keys if _key_cooldowns.get(k, 0) <= now]
        if not active:
            active = sorted(keys, key=lambda k: _key_cooldowns.get(k, 0))
        _key_index = (_key_index + 1) % len(active)
        return active[_key_index]

def mark_key_exhausted(key: str, delay_seconds: float = 3600):
    now = time.time()
    _key_cooldowns[key] = now + max(120.0, delay_seconds)
    mask = key[:7] + "..." + key[-4:] if len(key) > 12 else "***"
    print(f"[Gemini KeyPool] ⚠️ Cuenta ({mask}) con cuota diaria alcanzada (bloqueada por {delay_seconds:.0f}s). Rotando a siguiente cuenta activa...", flush=True)

def call_gemini_fallback(prompt, system_instruction="", max_retries=2, timeout=35, preferred_model=None):
    all_keys = get_gemini_api_keys()
    if not all_keys:
        return None
    api_key = get_next_gemini_key()
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

    is_heavy = bool(preferred_model and (preferred_model in ELEPHANT_MODELS or preferred_model in DIARIZATION_MODELS))
    wait_for_gemini_slot(is_elephant=is_heavy)

    if preferred_model and preferred_model in DIARIZATION_MODELS:
        model_pool = DIARIZATION_MODELS
    elif preferred_model and preferred_model in ELEPHANT_MODELS:
        model_pool = ELEPHANT_MODELS
    else:
        model_pool = GEMINI_MODELS
    preferred = preferred_model or app_config.get("cloud_model", model_pool[0])
    ordered = [preferred] + [m for m in model_pool if m != preferred]

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

        current_key = api_key
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}"
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
                    is_quota_exhausted = delay > 60 or "RESOURCE_EXHAUSTED" in body or "Quota exceeded" in body
                    if is_quota_exhausted:
                        mark_key_exhausted(current_key, delay if delay > 60 else 3600)
                        other_keys = [k for k in all_keys if k != current_key and _key_cooldowns.get(k, 0) <= time.time()]
                        if other_keys:
                            current_key = other_keys[0]
                            mask = current_key[:7] + "..." + current_key[-4:]
                            print(f"[Gemini KeyPool] 🔄 Failover automático: Reintentando inmediatamente con cuenta ({mask})...", flush=True)
                            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}"
                            continue
                        else:
                            set_model_cooldown(model_name, delay)
                            print(f"[Gemini Quota] ⚠️ Cuota diaria alcanzada en todas las cuentas para {model_name} ({delay}s). Saltando a siguiente modelo...", flush=True)
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
                set_model_cooldown(model_name, 300)
                break
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
