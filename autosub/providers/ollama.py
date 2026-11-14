import json
import re
import time
import urllib.request
from autosub.config import get_ollama_ip, get_ollama_port, get_ollama_url, get_ollama_tags_url, app_config
from autosub.core.context import _ollama_cache, _ollama_models_cache

def clean_think_tags(text):
    if not text:
        return ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

def is_ollama_alive(timeout=0.3, use_cache=True):
    now = time.time()
    if use_cache and now - _ollama_cache["ts"] < 10.0:
        return _ollama_cache["online"]
    alive = False
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect((get_ollama_ip(), get_ollama_port()))
        s.close()
        alive = True
    except Exception:
        alive = False
    _ollama_cache["online"] = alive
    _ollama_cache["ts"] = now
    return alive

def get_ollama_models(timeout=3.0):
    now = time.time()
    if now - _ollama_models_cache["ts"] < 30.0 and _ollama_models_cache["models"]:
        return _ollama_models_cache["models"]
    try:
        req = urllib.request.Request(get_ollama_tags_url())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", []) if m.get("name")]
            _ollama_models_cache["models"] = models
            _ollama_models_cache["ts"] = now
            return models
    except Exception:
        return _ollama_models_cache["models"] or []

def call_ollama(prompt, system_instruction="", timeout=180, temperature=0.3):
    if not is_ollama_alive(timeout=1.0, use_cache=True):
        return None
    model = app_config.get("local_model", "qwen3.5:9b")
    fast = app_config.get("fast_mode", False)
    messages = []
    
    if fast:
        fast_sys = (system_instruction + "\nIMPORTANTE: No uses monólogos internos ni razonamientos largos. Responde directamente la traducción numerada sin rodeos.").strip()
        messages.append({"role": "system", "content": fast_sys})
    elif system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    
    if fast:
        payload["options"] = {
            "num_predict": 1024,
            "top_k": 20,
            "top_p": 0.8
        }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(get_ollama_url(), data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            msg = res["choices"][0]["message"]
            content = clean_think_tags(msg.get("content") or msg.get("reasoning") or "")
            if content:
                return content
    except Exception:
        pass
    return None
