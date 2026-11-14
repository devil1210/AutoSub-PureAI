import json
import time
import urllib.request
from autosub.config import OR_URL, OR_MODELS, get_openrouter_api_key, app_config
from autosub.providers.ollama import clean_think_tags

def call_openrouter(prompt, system_instruction="", max_retries=2, timeout=45):
    if not app_config.get("enable_openrouter", False):
        return None
    api_key = get_openrouter_api_key()
    if not api_key:
        return None
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    for model_name in OR_MODELS:
        payload = {"model": model_name, "messages": messages, "max_tokens": 4096}
        if "nemotron" in model_name:
            payload["reasoning"] = {"effort": "none"}
        for _ in range(max_retries):
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(OR_URL, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "http://autosub.local",
                "X-Title": "SP-AutoSub",
            })
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    msg = res["choices"][0]["message"]
                    content = clean_think_tags(msg.get("content") or msg.get("reasoning") or "")
                    if content:
                        return content
            except Exception:
                time.sleep(2)
    return None
