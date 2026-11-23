import json
import re
import time
from autosub.config import MAX_GEMINI_RPM, MIN_GEMINI_INTERVAL
from autosub.core.context import next_gemini_lock, gemini_rate_lock, gemini_request_timestamps, elephant_request_timestamps

_next_gemini_allowed = 0.0
_model_cooldowns = {}
_audio_model_cooldowns = {}

def get_next_gemini_allowed():
    global _next_gemini_allowed
    return _next_gemini_allowed

def get_model_cooldown(model_name: str) -> float:
    return _model_cooldowns.get(model_name, 0.0)

def set_model_cooldown(model_name: str, delay_seconds: float):
    candidate = time.time() + delay_seconds
    if candidate > _model_cooldowns.get(model_name, 0.0):
        _model_cooldowns[model_name] = candidate
        print(f"[Gemini Cooldown] Modelo '{model_name}' bloqueado por {delay_seconds:.0f}s "
              f"(hasta {time.strftime('%H:%M:%S', time.localtime(candidate))})", flush=True)

def get_audio_cooldown(model_name: str) -> float:
    return _audio_model_cooldowns.get(model_name, 0.0)

def set_audio_cooldown(model_name: str, delay_seconds: float):
    candidate = time.time() + delay_seconds
    if candidate > _audio_model_cooldowns.get(model_name, 0.0):
        _audio_model_cooldowns[model_name] = candidate

def set_gemini_cooldown(delay_seconds):
    global _next_gemini_allowed
    # Solo aplicar cooldown global si es una pausa corta de RPM (<= 120s)
    if delay_seconds <= 120:
        with next_gemini_lock:
            candidate = time.time() + delay_seconds
            if candidate > _next_gemini_allowed:
                _next_gemini_allowed = candidate
                print(f"[Gemini Cooldown] Pausa global de {delay_seconds:.0f}s "
                      f"(hasta {time.strftime('%H:%M:%S', time.localtime(_next_gemini_allowed))})", flush=True)

def get_retry_delay_from_error(err_body):
    try:
        obj = json.loads(err_body)
        for detail in obj.get("error", {}).get("details", []):
            if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                delay_str = detail.get("retryDelay", "")
                m = re.match(r"(\d+)s", delay_str)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None

def wait_for_gemini_slot(is_elephant=False):
    """
    Garantiza que nunca se superen los límites de RPM:
    - The Elephant (modelos de 5 RPM máx): controlado a 4 RPM, intervalo mín. 13s.
    - The Goldfish (modelos de 15 RPM máx): controlado a 12 RPM, intervalo mín. 4.5s.
    """
    # The Elephant: Límite estricto de máximo 2 RPM (mínimo 32s de intervalo entre llamadas)
    # The Goldfish: Límite conservador de 10 RPM (mínimo 6s de intervalo)
    max_rpm = 2 if is_elephant else 10
    min_interval = 32.0 if is_elephant else 6.0
    ts_list = elephant_request_timestamps if is_elephant else gemini_request_timestamps

    with gemini_rate_lock:
        now = time.time()
        while ts_list and now - ts_list[0] >= 60.0:
            ts_list.pop(0)

        if len(ts_list) >= max_rpm:
            wait_time = 60.0 - (now - ts_list[0]) + 0.5
            if wait_time > 0:
                tag = "🐘 The Elephant" if is_elephant else "🐟 The Goldfish"
                print(f"[Gemini RateLimiter] 🛡️ {tag}: alcanzadas {len(ts_list)}/{max_rpm} RPM. Pausando {wait_time:.1f}s...", flush=True)
                time.sleep(wait_time)
                now = time.time()
                while ts_list and now - ts_list[0] >= 60.0:
                    ts_list.pop(0)

        if ts_list:
            elapsed = now - ts_list[-1]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
                now = time.time()

        ts_list.append(now)
