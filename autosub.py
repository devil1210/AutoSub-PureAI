import collections
import datetime
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5055

# === Configuración y Persistencia ===
CONFIG_FILE = "/opt/autosub_config.json"
QUEUE_FILE = "/opt/autosub_queue.json"
HISTORY_FILE = "/opt/autosub_history.json"
PROFILES_DIR = "/data/tvshows/.profiles"

DEFAULT_CONFIG = {
    "local_only": False,
    "local_model": "qwen3.5:9b",
    "cloud_model": "gemini-3.5-flash-lite",
    "enable_openrouter": False,
    "batch_size": 100,
    "fast_mode": False,
    "proofread": True,
    "ollama_ip": os.getenv("OLLAMA_IP", "127.0.0.1"),
    "ollama_port": int(os.getenv("OLLAMA_PORT", "11434")),
    "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
    "libraries": [
        {"name": "Anime", "path": "/data/tvshows"},
        {"name": "Películas", "path": "/data/movies"}
    ],
    "watch_paths": ["/data/tvshows", "/data/movies"],
    "source_priority": ["ja", "en", "fr", "de", "es", "it", "pt", "ru", "zh"],
    "target_languages": ["es-419"]
}

SUPPORTED_TARGET_LANGUAGES = {
    "es-419": {"name": "Español Latinoamericano", "ext": "es-419", "prompt_name": "español latinoamericano neutro"},
    "es":     {"name": "Español España",          "ext": "es",     "prompt_name": "español de España (castellano neutro)"},
    "en":     {"name": "Inglés",                  "ext": "en",     "prompt_name": "inglés natural y fluido (English)"},
    "fr":     {"name": "Francés",                 "ext": "fr",     "prompt_name": "francés estándar (Français)"},
    "de":     {"name": "Alemán",                  "ext": "de",     "prompt_name": "alemán estándar (Deutsch)"},
    "pt-BR":  {"name": "Portugués Brasil",         "ext": "pt-br",  "prompt_name": "portugués de Brasil (Português brasileiro)"},
    "it":     {"name": "Italiano",                "ext": "it",     "prompt_name": "italiano estándar (Italiano)"},
    "ja":     {"name": "Japonés",                 "ext": "ja",     "prompt_name": "japonés natural (日本語)"},
    "ru":     {"name": "Ruso",                    "ext": "ru",     "prompt_name": "ruso estándar (Русский)"},
    "zh-CN":  {"name": "Chino Simplificado",      "ext": "zh",     "prompt_name": "chino simplificado (简体中文)"},
}

LANG_ALIASES = {
    "ja": ["ja", "jpn", "japanese", "jap", "jp"],
    "en": ["en", "eng", "english"],
    "fr": ["fr", "fre", "fra", "french"],
    "de": ["de", "ger", "deu", "german"],
    "es": ["es", "spa", "spanish", "es-419", "es-es"],
    "it": ["it", "ita", "italian"],
    "pt": ["pt", "por", "portuguese", "pt-br"],
    "ru": ["ru", "rus", "russian"],
    "zh": ["zh", "chi", "zho", "chinese"],
}

config_lock = threading.RLock()
history_lock = threading.RLock()
queue_lock = threading.RLock()
processing_lock = threading.RLock()
cancel_lock = threading.RLock()
next_gemini_lock = threading.Lock()

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
                return {**DEFAULT_CONFIG, **c}
        except Exception as e:
            print(f"[Warn] Error leyendo config: {e}", flush=True)
    return dict(DEFAULT_CONFIG)

def save_config(new_conf):
    with config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(new_conf, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Warn] No se pudo guardar config: {e}", flush=True)

app_config = load_config()

# Getters dinámicos para configuración
def get_ollama_ip():
    return str(app_config.get("ollama_ip", "192.168.1.12")).strip()

def get_ollama_port():
    try:
        return int(app_config.get("ollama_port", 11434))
    except Exception:
        return 11434

def get_ollama_url():
    return f"http://{get_ollama_ip()}:{get_ollama_port()}/v1/chat/completions"

def get_ollama_tags_url():
    return f"http://{get_ollama_ip()}:{get_ollama_port()}/api/tags"

def get_gemini_api_key():
    return str(app_config.get("gemini_api_key", DEFAULT_CONFIG["gemini_api_key"])).strip()

def get_openrouter_api_key():
    return str(app_config.get("openrouter_api_key", DEFAULT_CONFIG["openrouter_api_key"])).strip()

def get_watch_paths():
    libs = app_config.get("libraries")
    if isinstance(libs, list) and len(libs) > 0:
        extracted = []
        for l in libs:
            if isinstance(l, dict) and l.get("path"):
                extracted.append(l["path"])
            elif isinstance(l, str) and l.strip():
                extracted.append(l.strip())
        if extracted:
            return extracted
    paths = app_config.get("watch_paths", ["/data/tvshows", "/data/movies"])
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split("\n") if p.strip()]
    elif isinstance(paths, list):
        paths = [str(p).strip() for p in paths if str(p).strip()]
    return paths or ["/data/tvshows", "/data/movies"]

def get_parsed_watch_paths():
    """Retorna una lista de tuplas (directorio, nombre_categoria) soportando lista de diccionarios 'libraries' o 'watch_paths'"""
    libs = app_config.get("libraries")
    if isinstance(libs, list) and len(libs) > 0 and isinstance(libs[0], dict):
        result = []
        for l in libs:
            p = str(l.get("path", "")).strip()
            n = str(l.get("name", "")).strip() or ("Películas" if "movie" in p.lower() else "Series")
            if p:
                result.append((p, n))
        if result:
            return result

    raw_list = get_watch_paths()
    parsed = []
    for item in raw_list:
        if isinstance(item, dict):
            p = str(item.get("path", "")).strip()
            n = str(item.get("name", "")).strip() or ("Películas" if "movie" in p.lower() else "Series")
            if p:
                parsed.append((p, n))
        elif ";" in str(item):
            parts = str(item).split(";", 1)
            d_path = parts[0].strip()
            label = parts[1].strip() or ("Películas" if "movie" in d_path.lower() else "Series")
            parsed.append((d_path, label))
        else:
            d_path = str(item).strip()
            label = "Películas" if "movie" in d_path.lower() else "Series"
            parsed.append((d_path, label))
    return parsed or [("/data/tvshows", "Anime"), ("/data/movies", "Películas")]

# === Historial Persistente ===
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, list):
                    return d
        except Exception as e:
            print(f"[History] Error leyendo {HISTORY_FILE}: {e}", flush=True)
    return []

def save_history(records):
    with history_lock:
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(records[:1000], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[History] Error guardando {HISTORY_FILE}: {e}", flush=True)

def append_history_record(record):
    with history_lock:
        hist = load_history()
        hist.insert(0, record)
        save_history(hist)

# Modelos disponibles
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",    # 500 req/día (15 RPM) - Principal
    "gemini-3.1-flash-lite",    # 500 req/día (15 RPM) - Secundario
    "gemma-4-31b-it",           # 14.4K req/día (30 RPM) - Respaldo masivo
    "gemma-4-26b-a4b-it",       # 14.4K req/día (30 RPM) - Respaldo masivo
    "gemini-flash-lite-latest", # Fallback adicional
]

os.makedirs(PROFILES_DIR, exist_ok=True)

# Colas de trabajo y persistencia
job_queue = queue.Queue()
retry_queue = {}

def load_persisted_queue():
    global retry_queue
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    retry_queue = data
        except Exception:
            pass

def save_persisted_queue():
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(retry_queue, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

load_persisted_queue()

# Estado de telemetría y métricas
currently_processing = None
current_file_start_time = 0.0
current_batch_num = 0
current_total_batches = 0
total_processing_time_sec = 0.0
completed_jobs_count = 0
cancel_current_flag = False
next_gemini_allowed = 0.0
last_job_metrics = {}

_ollama_cache = {"ts": 0.0, "online": False}
_ollama_models_cache = {"ts": 0.0, "models": []}

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

def set_gemini_cooldown(delay_seconds):
    global next_gemini_allowed
    with next_gemini_lock:
        candidate = time.time() + delay_seconds
        if candidate > next_gemini_allowed:
            next_gemini_allowed = candidate
            print(f"[Gemini Cooldown] Bloqueado por {delay_seconds:.0f}s "
                  f"(hasta {time.strftime('%H:%M:%S', time.localtime(next_gemini_allowed))})", flush=True)

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

# === Control Proactivo de Frecuencia (Rate Limiter: Máx 10 RPM) ===
MAX_GEMINI_RPM = 10  # Límite gratuito es 15 RPM. Establecemos 10 RPM como tope seguro (67% de uso).
MIN_GEMINI_INTERVAL = 4.0  # Espaciado mínimo de 4.0s entre llamadas consecutivas a Gemini
gemini_request_timestamps = []
gemini_rate_lock = threading.Lock()

def wait_for_gemini_slot():
    """Garantiza que nunca se superen 10 RPM en ninguna ventana móvil de 60 segundos."""
    with gemini_rate_lock:
        now = time.time()
        # Limpiar llamadas de hace más de 60s
        while gemini_request_timestamps and now - gemini_request_timestamps[0] >= 60.0:
            gemini_request_timestamps.pop(0)

        # Si alcanzamos 10 peticiones en los últimos 60 segundos, dormir hasta que se libere un slot
        if len(gemini_request_timestamps) >= MAX_GEMINI_RPM:
            wait_time = 60.0 - (now - gemini_request_timestamps[0]) + 0.5
            if wait_time > 0:
                print(f"[Gemini RateLimiter] 🛡️ Protección de Cuota: alcanzadas {len(gemini_request_timestamps)}/{MAX_GEMINI_RPM} RPM. Pausando {wait_time:.1f}s para no saturar el límite...", flush=True)
                time.sleep(wait_time)
                now = time.time()
                while gemini_request_timestamps and now - gemini_request_timestamps[0] >= 60.0:
                    gemini_request_timestamps.pop(0)

        # Espaciado mínimo entre llamadas para evitar ráfagas instantáneas
        if gemini_request_timestamps:
            elapsed = now - gemini_request_timestamps[-1]
            if elapsed < MIN_GEMINI_INTERVAL:
                time.sleep(MIN_GEMINI_INTERVAL - elapsed)
                now = time.time()

        gemini_request_timestamps.append(now)

def clean_think_tags(text):
    if not text:
        return ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

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

def call_gemini_fallback(prompt, system_instruction="", max_retries=2, timeout=35):
    api_key = get_gemini_api_key()
    if not api_key:
        return None
    with next_gemini_lock:
        remaining = next_gemini_allowed - time.time()
        if remaining > 0:
            if remaining <= 75:
                print(f"[Gemini Cooldown] Esperando cooldown de {remaining:.1f}s...", flush=True)
                time.sleep(remaining + 0.5)
            else:
                return None

    # Control proactivo de frecuencia: máximo 10 peticiones por minuto
    wait_for_gemini_slot()

    preferred = app_config.get("cloud_model", GEMINI_MODELS[0])
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for _ in range(max_retries):
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw = res["candidates"][0]["content"]["parts"][0]["text"]
                    return clean_think_tags(raw)
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="ignore")
                if err.code == 429:
                    delay = get_retry_delay_from_error(body) or 60
                    set_gemini_cooldown(delay)
                    return None
                time.sleep(2)
            except Exception:
                time.sleep(2)
    return None

def call_ai(prompt, system_instruction="", timeout=180, allow_cloud_override=False):
    res = call_ollama(prompt, system_instruction=system_instruction, timeout=timeout)
    if res:
        return res
    if app_config.get("local_only", True) and not allow_cloud_override:
        return None
    if app_config.get("enable_openrouter", False):
        res = call_openrouter(prompt, system_instruction=system_instruction, timeout=timeout)
        if res:
            return res
    return call_gemini_fallback(prompt, system_instruction=system_instruction, timeout=timeout)

# === Contexto Web para Perfiles ===
def fetch_anime_web_context(series_name):
    search_query = re.sub(r"\(\d{4}\)|\[.*?\]", "", series_name).strip()
    headers = {"User-Agent": "SP-AutoSub/3.5 (Homelab Anime Subtitle Adapter)"}
    web_info = {
        "found": False,
        "official_title": series_name,
        "japanese_title": "",
        "genres": [],
        "synopsis": "",
        "characters_raw": []
    }

    try:
        q_enc = urllib.parse.quote(search_query)
        jikan_search_url = f"https://api.jikan.moe/v4/anime?q={q_enc}&limit=1"
        req = urllib.request.Request(jikan_search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            if results:
                anime = results[0]
                mal_id = anime.get("mal_id")
                web_info["found"] = True
                web_info["official_title"] = anime.get("title_english") or anime.get("title") or series_name
                web_info["japanese_title"] = anime.get("title_japanese") or ""
                web_info["synopsis"] = anime.get("synopsis") or ""
                web_info["genres"] = [g.get("name") for g in anime.get("genres", []) + anime.get("themes", []) if g.get("name")]

                if mal_id:
                    time.sleep(0.4)
                    chars_url = f"https://api.jikan.moe/v4/anime/{mal_id}/characters"
                    req_c = urllib.request.Request(chars_url, headers=headers)
                    with urllib.request.urlopen(req_c, timeout=5) as resp_c:
                        c_data = json.loads(resp_c.read().decode("utf-8")).get("data", [])
                        c_data.sort(key=lambda x: (0 if x.get("role") == "Main" else 1, -(x.get("favorites") or 0)))
                        for c_entry in c_data[:12]:
                            char_obj = c_entry.get("character", {})
                            raw_name = char_obj.get("name", "")
                            if "," in raw_name:
                                parts = [p.strip() for p in raw_name.split(",", 1)]
                                clean_char_name = f"{parts[1]} {parts[0]}"
                            else:
                                clean_char_name = raw_name
                            web_info["characters_raw"].append({
                                "name": clean_char_name,
                                "role": c_entry.get("role", "Supporting")
                            })
    except Exception as e:
        print(f"[WebContext] Jikan API omitido para '{search_query}': {e}", flush=True)

    if not web_info["found"]:
        try:
            wiki_search = f"https://es.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(search_query)}&format=json"
            req_w = urllib.request.Request(wiki_search, headers=headers)
            with urllib.request.urlopen(req_w, timeout=4) as resp_w:
                w_data = json.loads(resp_w.read().decode("utf-8"))
                search_hits = w_data.get("query", {}).get("search", [])
                if search_hits:
                    page_title = urllib.parse.quote(search_hits[0]["title"].replace(" ", "_"))
                    extract_url = f"https://es.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&titles={page_title}&format=json"
                    req_ex = urllib.request.Request(extract_url, headers=headers)
                    with urllib.request.urlopen(req_ex, timeout=4) as resp_ex:
                        ex_data = json.loads(resp_ex.read().decode("utf-8"))
                        pages = ex_data.get("query", {}).get("pages", {})
                        for _, p_val in pages.items():
                            snippet = p_val.get("extract", "")
                            if snippet:
                                web_info["found"] = True
                                web_info["synopsis"] = snippet[:500]
                                break
        except Exception:
            pass

    return web_info

def get_or_create_series_profile(series_name, force_refresh=False):
    clean_name = re.sub(r'[\\/*?:"<>|]', "", series_name).strip()
    profile_path = os.path.join(PROFILES_DIR, f"{clean_name}.json")

    existing_profile = None
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                existing_profile = json.load(f)
                if not force_refresh:
                    return existing_profile, profile_path
        except Exception as e:
            print(f"[Profile] Error leyendo perfil existente: {e}", flush=True)

    print(f"[Profile] Generando perfil inteligente para '{clean_name}'...", flush=True)
    web_info = fetch_anime_web_context(clean_name)

    genres_str = ", ".join(web_info["genres"]) if web_info["genres"] else "Anime"
    chars_web_list = "\n".join([f"- {c['name']} ({c['role']})" for c in web_info["characters_raw"]]) or "Dedúcelos de la sinopsis o tu conocimiento."

    sys_prompt = """Eres un experto director de doblaje y adaptación de anime al español latinoamericano neutro.
Tu labor es crear un perfil de traducción exhaustivo en formato JSON estricto, sin explicaciones adicionales."""

    prompt = f"""Crea un perfil de traducción profesional para el anime/serie:
TÍTULO: {clean_name}
TÍTULO OFICIAL ENCONTRADO: {web_info['official_title']}
GÉNEROS: {genres_str}
SINOPSIS: {web_info['synopsis'][:600]}
PERSONAJES RELEVANTES:
{chars_web_list}

Genera un JSON con esta estructura exacta:
1. "series_title": "{clean_name}"
2. "official_title": Título oficial internacional
3. "setting": Resumen conciso del universo y premisa (2 a 4 líneas)
4. "characters": Lista de 5 a 12 personajes con:
   - "name": Nombre del personaje
   - "gender": "Femenino" o "Masculino"
   - "personality": Rol y forma de hablar detallada (formalidad, tuteo, muletillas, tono)
5. "glossary": Diccionario de 8 a 20 términos clave (títulos, habilidades, organizaciones, rangos) "Inglés": "Español Latino"
6. "tone_instructions": Instrucciones de tono para subtítulos (latino neutro, ritmo, modismos)

Devuelve ÚNICAMENTE el bloque JSON.
/no_think"""

    resp = call_ai(prompt, system_instruction=sys_prompt, timeout=120, allow_cloud_override=True)
    profile_data = None
    if resp:
        try:
            json_match = re.search(r"\{.*\}", resp, flags=re.DOTALL)
            if json_match:
                profile_data = json.loads(json_match.group(0))
        except Exception as e:
            print(f"[Profile] Error parseando JSON de IA: {e}", flush=True)

    if not profile_data or not isinstance(profile_data, dict):
        profile_data = {
            "series_title": clean_name,
            "official_title": web_info["official_title"],
            "setting": (web_info["synopsis"][:350] + "...") if web_info["synopsis"] else f"Serie de {genres_str}.",
            "characters": [],
            "glossary": {"Young Mistress": "Señorita", "My Lord": "Mi Señor", "Master": "Maestro"},
            "tone_instructions": f"Español latino neutro natural. Géneros: {genres_str}.",
            "episodes": []
        }
    if isinstance(profile_data, dict) and "episodes" not in profile_data:
        profile_data["episodes"] = []

    if not profile_data.get("characters") and web_info["characters_raw"]:
        profile_data["characters"] = [
            {"name": c["name"], "gender": "Desconocido", "personality": f"Rol en la serie: {c['role']}"}
            for c in web_info["characters_raw"]
        ]

    # Preservar glosario personalizado y episodios si ya existía un perfil previo
    if existing_profile and isinstance(existing_profile, dict):
        old_glossary = existing_profile.get("glossary", {})
        if old_glossary and isinstance(old_glossary, dict):
            merged_glossary = dict(profile_data.get("glossary", {}))
            merged_glossary.update(old_glossary)
            profile_data["glossary"] = merged_glossary

        old_eps = existing_profile.get("episodes", [])
        if old_eps and not profile_data.get("episodes"):
            profile_data["episodes"] = old_eps

        old_chars_map = {c.get("name", "").strip().lower(): c for c in existing_profile.get("characters", []) if c.get("name")}
        for c in profile_data.get("characters", []):
            k_name = c.get("name", "").strip().lower()
            if k_name in old_chars_map:
                old_c = old_chars_map[k_name]
                if old_c.get("personality"):
                    c["personality"] = old_c["personality"]
                if old_c.get("gender") and old_c.get("gender") != "Desconocido":
                    c["gender"] = old_c["gender"]

    try:
        os.makedirs(PROFILES_DIR, exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, ensure_ascii=False, indent=2)
        print(f"[Profile] Guardado exitoso: {profile_path}", flush=True)
    except Exception as e:
        print(f"[Profile] Error escribiendo perfil: {e}", flush=True)

    return profile_data, profile_path

def extract_episode_label(video_path):
    base = os.path.splitext(os.path.basename(video_path))[0]
    m = re.search(r"(\b[sS]\d{1,2}[eE]\d{1,3}\b)", base)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(?:[eE][pP]?\.?\s*)(\d{1,3})\b", base)
    if m:
        return f"Episodio {int(m.group(1)):02d}"
    m = re.search(r"(?:[-_\s\[])(\d{2,3})(?:[-_\s\]\)])", base)
    if m:
        return f"Episodio {int(m.group(1)):02d}"
    clean = re.sub(r"\[.*?\]|\(.*?\)", "", base).strip()
    return clean[:35] if clean else base[:35]

def generate_and_save_episode_context(series_name, video_path, raw_texts, translated_texts, profile_data, profile_path):
    if not translated_texts or len(translated_texts) < 5:
        return None

    ep_label = extract_episode_label(video_path)
    file_name = os.path.basename(video_path)
    total_lines = len(translated_texts)

    sample_size = min(70, total_lines)
    step = max(1, total_lines // sample_size)
    sampled = []
    has_distinct_raw = raw_texts and len(raw_texts) == total_lines and raw_texts != translated_texts

    for idx in range(0, total_lines, step):
        t_trans = translated_texts[idx].strip()
        clean_trans = re.sub(r"\{.*?\}", "", t_trans).replace("\\N", " ").replace("\\n", " ").strip()
        if has_distinct_raw:
            t_raw = raw_texts[idx].strip()
            clean_raw = re.sub(r"\{.*?\}", "", t_raw).replace("\\N", " ").replace("\\n", " ").strip()
            if clean_trans or clean_raw:
                sampled.append(f"[{idx+1}] [ORIGINAL/EN]: {clean_raw} | [ESPAÑOL]: {clean_trans}")
        else:
            if clean_trans:
                sampled.append(f"[{idx+1}] {clean_trans}")
        if len(sampled) >= sample_size:
            break

    dialogue_summary_input = "\n".join(sampled)

    known_chars = [c.get("name") for c in profile_data.get("characters", []) if c.get("name")]
    known_chars_str = ", ".join(known_chars) if known_chars else "Ninguno registrado aún."

    sys_prompt = f"""Eres un supervisor de continuidad narrativa para subtitulaje de anime y series al español latino neutro.
SERIE: {profile_data.get('series_title', series_name)} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {profile_data.get('setting', '')}
CAPÍTULO: {ep_label} ({file_name})
PERSONAJES YA REGISTRADOS EN EL PERFIL: {known_chars_str}

Tu objetivo es analizar los diálogos del capítulo y generar un JSON con:
1. "summary": Resumen conciso de continuidad narrativa (máximo 140-180 palabras) cubriendo trama principal, eventos clave, dinámicas y revelaciones.
2. "new_characters": Lista de personajes NUEVOS que debuten o hablen en este episodio y NO estén en la lista de ya registrados. Cada uno con:
   - "name": Nombre del personaje
   - "gender": "Femenino" o "Masculino" o "Desconocido" (clave para la concordancia de adjetivos en español)
   - "personality": Breve rol o relación (ej. "Compañero de escuadrón", "Médica del gremio")
   (Si no hay personajes nuevos con diálogo relevante, devuelve lista vacía: [])
3. "updated_characters": Lista de personajes YA EXISTENTES que hayan sufrido un giro de trama importante, cambio de bando, revelación de verdadera identidad o cambio drástico de actitud en este episodio. Cada uno con:
   - "name": Nombre del personaje existente
   - "new_personality": Nueva descripción de rol, verdadera personalidad y tono de habla que debe usar a partir de ahora
   - "reason": Breve motivo del cambio
   (Si no hubo giros drásticos en personajes existentes, devuelve lista vacía: [])
4. "new_glossary": Diccionario de términos clave nuevos introducidos en este episodio (nombres de técnicas, magia, títulos nobiliarios, facciones, rangos, lugares, conceptos ficticios del mundo).
   REGLAS ESTRICTAS PARA EL GLOSARIO:
   - Cada entrada DEBE ser: "Término en Inglés o Japonés (clave)": "Traducción al Español Latino (valor)".
   - Ejemplos correctos: "Kingdom of Altoire": "Reino de Altoire", "The Explosive Angel": "El Ángel Explosivo", "House Liston": "Casa Liston", "Magivision": "Magivisión".
   - ESTÁ TOTALMENTE PROHIBIDO poner el mismo término en español en ambos lados (ej: NUNCA 'Reino de Altoire': 'Reino de Altoire'). La CLAVE debe ser el término en INGLÉS o JAPONÉS tal como viene en subtítulos en inglés o en la novela ligera. Si el diálogo analizado solo está en español, debes DEDUCIR o RECONSTRUIR el término original en inglés/japonés para la clave.
   - El valor debe ser ÚNICAMENTE el término traducido (máximo 4 palabras), NUNCA una explicación o definición larga.
   - Si no hay términos nuevos, devuelve diccionario vacío: {{}}

Devuelve ÚNICAMENTE un objeto JSON válido con las claves: "summary", "new_characters", "updated_characters", "new_glossary"."""

    prompt = f"""Analiza estos diálogos del capítulo '{ep_label}':
--- DIÁLOGOS ---
{dialogue_summary_input}
--- FIN ---

Genera el JSON con el resumen, nuevos personajes, personajes con giros de trama actualizados y nuevos términos:
/no_think"""

    print(f"[Continuity] Analizando continuidad, personajes y giros de trama para {series_name} - {ep_label}...", flush=True)
    raw_resp = call_ai(prompt, system_instruction=sys_prompt, timeout=90)

    summary_text = ""
    new_chars = []
    updated_chars = []
    new_glossary = {}

    if raw_resp:
        try:
            m = re.search(r"\{.*\}", raw_resp, flags=re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                summary_text = parsed.get("summary", "").strip()
                new_chars = parsed.get("new_characters", [])
                updated_chars = parsed.get("updated_characters", [])
                new_glossary = parsed.get("new_glossary", {})
        except Exception:
            pass

    if not summary_text or len(summary_text) < 15:
        if raw_resp and len(raw_resp.strip()) >= 15 and not raw_resp.strip().startswith("{"):
            summary_text = raw_resp.strip()
        else:
            summary_text = f"Capítulo procesado con {total_lines} líneas de diálogo."

    # Procesar y añadir nuevos personajes al perfil
    if "characters" not in profile_data or not isinstance(profile_data["characters"], list):
        profile_data["characters"] = []

    existing_char_names_lower = {
        c.get("name", "").strip().lower() for c in profile_data["characters"] if c.get("name")
    }

    added_chars_count = 0
    if isinstance(new_chars, list):
        for nc in new_chars:
            if not isinstance(nc, dict):
                continue
            c_name = str(nc.get("name", "")).strip()
            if not c_name or len(c_name) < 2:
                continue
            if c_name.lower() not in existing_char_names_lower:
                gender = nc.get("gender", "Desconocido")
                if gender not in ["Femenino", "Masculino", "Desconocido"]:
                    gender = "Desconocido"
                pers = str(nc.get("personality", f"Introducido en {ep_label}")).strip()
                profile_data["characters"].append({
                    "name": c_name,
                    "gender": gender,
                    "personality": pers
                })
                existing_char_names_lower.add(c_name.lower())
                added_chars_count += 1
                print(f"[Continuity] 👤 Nuevo personaje añadido a '{series_name}': {c_name} [{gender}] - {pers}", flush=True)

    # Procesar actualizaciones de personajes existentes (giros de trama / cambio de bando)
    updated_chars_count = 0
    if isinstance(updated_chars, list):
        for uc in updated_chars:
            if not isinstance(uc, dict):
                continue
            u_name = str(uc.get("name", "")).strip()
            new_pers = str(uc.get("new_personality", "")).strip()
            reason = str(uc.get("reason", "")).strip()
            if not u_name or not new_pers:
                continue
            for char_obj in profile_data["characters"]:
                if char_obj.get("name", "").strip().lower() == u_name.lower():
                    old_pers = char_obj.get("personality", "")
                    reason_suffix = f" ({reason})" if reason else ""
                    char_obj["personality"] = f"{new_pers}{reason_suffix}"
                    updated_chars_count += 1
                    print(f"[Continuity] 🔄 Giro de trama en '{u_name}': '{old_pers}' -> '{char_obj['personality']}'", flush=True)
                    break

    # Procesar y añadir nuevos términos al glosario
    if "glossary" not in profile_data or not isinstance(profile_data["glossary"], dict):
        profile_data["glossary"] = {}

    existing_glossary_lower = {k.strip().lower() for k in profile_data["glossary"]}
    added_terms_count = 0
    if isinstance(new_glossary, dict):
        for gk, gv in new_glossary.items():
            gk_str = str(gk).strip()
            gv_str = str(gv).strip()
            if not gk_str or not gv_str:
                continue
            # Rechazar si clave y valor son idénticos
            if gk_str.lower() == gv_str.lower():
                continue
            # Rechazar si el valor es una definición larga en vez de un término traducido
            if len(gv_str) > 50 or "\n" in gv_str:
                continue
            if gk_str.lower() not in existing_glossary_lower:
                profile_data["glossary"][gk_str] = gv_str
                existing_glossary_lower.add(gk_str.lower())
                added_terms_count += 1
                print(f"[Continuity] 📖 Nuevo término añadido a '{series_name}': {gk_str} -> {gv_str}", flush=True)

    ep_record = {
        "episode": ep_label,
        "file_name": file_name,
        "summary": summary_text,
        "lines": total_lines,
        "new_characters": [c.get("name") for c in new_chars if isinstance(c, dict) and c.get("name")],
        "updated_characters": [c.get("name") for c in updated_chars if isinstance(c, dict) and c.get("name")],
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    if "episodes" not in profile_data or not isinstance(profile_data["episodes"], list):
        profile_data["episodes"] = []

    found = False
    for i, existing in enumerate(profile_data["episodes"]):
        if existing.get("episode") == ep_label or existing.get("file_name") == file_name:
            profile_data["episodes"][i] = ep_record
            found = True
            break
    if not found:
        profile_data["episodes"].append(ep_record)

    try:
        if profile_path and os.path.exists(os.path.dirname(profile_path)):
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, ensure_ascii=False, indent=2)
            print(f"[Continuity] Perfil actualizado (nuevos personajes: {added_chars_count}, nuevos términos: {added_terms_count}) en {profile_path}", flush=True)
    except Exception as e:
        print(f"[Continuity] Error guardando contexto en perfil: {e}", flush=True)

    return ep_record

def is_translatable_ass_line(style_name, raw_text):
    if re.search(r"\{\\p[1-9]", raw_text):
        return False
    clean = re.sub(r"\{.*?\}", "", raw_text).replace("\\N", " ").replace("\\n", " ").strip()
    if not clean:
        return False
    return bool(re.search(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ]", clean))

def translate_batch(text_list, profile_data, prev_context=None):
    if not text_list:
        return []

def translate_batch(text_list, profile_data, prev_context=None, target_lang="es-419"):
    if not text_list:
        return []

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    chars_list = []
    for c in profile_data.get("characters", []):
        name = c.get("name", "")
        gender = c.get("gender", "")
        pers = c.get("personality", "")
        g_tag = f" [{gender}]" if gender else ""
        chars_list.append(f"- {name}{g_tag}: {pers}")
    chars_info = "\n".join(chars_list) if chars_list else "Sin personajes específicos."

    glossary_info = ", ".join([f"'{k}' -> '{v}'" for k, v in profile_data.get("glossary", {}).items()])
    setting_info = profile_data.get("setting", "Anime")
    tone_info = profile_data.get("tone_instructions", f"Tono natural en {target_meta['name']}.")

    continuity_str = ""
    past_eps = profile_data.get("episodes", [])
    if past_eps and isinstance(past_eps, list):
        recent = past_eps[-3:]
        recent_summaries = "\n".join([f"- {ep.get('episode', 'Capítulo')}: {ep.get('summary', '')[:200]}" for ep in recent if ep.get("summary")])
        if recent_summaries:
            continuity_str = f"CONTINUIDAD NARRATIVA (Capítulos previos):\n{recent_summaries}\n"

    sys_prompt = f"""Eres un traductor y adaptador profesional de subtítulos de anime al {prompt_lang}.
SERIE: {profile_data.get('series_title')} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {setting_info}
TONO Y ESTILO: {tone_info}
{continuity_str}PERSONAJES Y GÉNEROS (respeta el género en adjetivos y concordancias gramaticales):
{chars_info}
GLOSARIO OBLIGATORIO: {glossary_info}
REGLAS ESTRICTAS:
1. Mantén TODAS las etiquetas ASS entre llaves (ej. {{\\i1}}, {{\\pos(x,y)}}) y saltos \\N intactos.
2. Si una línea tras un salto \\N empieza con la palabra 'No' o con una palabra que empieza por 'N', NUNCA unas la 'N' de la palabra con el salto \\N (debe ser '\\N No' o '\\NNo', jamás '\\No puedo' que se comería la 'N').
3. Devuelve EXACTAMENTE la misma cantidad de líneas numeradas con el formato [N] Texto traducido.
4. No agregues explicaciones ni notas."""

    context_block = ""
    if prev_context:
        ctx_lines = "\n".join([f"(Previa) {t}" for t in prev_context[-2:]])
        context_block = f"CONTEXTO PREVIO (SOLO REFERENCIA, NO TRADUCIR):\n{ctx_lines}\n\n"

    numbered_input = "\n".join([f"[{i+1}] {text}" for i, text in enumerate(text_list)])
    user_prompt = f"{context_block}Traduce al {prompt_lang} respetando la numeración exacta [1] a [{len(text_list)}]. Devuelve cada línea numerada sin omitir ninguna:\n{numbered_input}\n/no_think"

    resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=180)
    if resp:
        parsed_results = {}
        for line in resp.strip().split("\n"):
            m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(text_list) and m.group(2).strip():
                    parsed_results[idx] = m.group(2).strip()
        if len(parsed_results) >= len(text_list) * 0.75:
            return [parsed_results.get(i, text_list[i]) for i in range(len(text_list))]
    return None

def proofread_subtitles(raw_texts, translated_texts, profile_data, batch_size=100, episode_context=None, target_lang="es-419"):
    if not translated_texts or len(raw_texts) != len(translated_texts):
        return translated_texts

    target_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, SUPPORTED_TARGET_LANGUAGES["es-419"])
    prompt_lang = target_meta["prompt_name"]

    chars_list = []
    for c in profile_data.get("characters", []):
        name = c.get("name", "")
        gender = c.get("gender", "")
        pers = c.get("personality", "")
        g_tag = f" [{gender}]" if gender else ""
        chars_list.append(f"- {name}{g_tag}: {pers}")
    chars_info = "\n".join(chars_list) if chars_list else "Sin personajes específicos."

    glossary_info = ", ".join([f"'{k}' -> '{v}'" for k, v in profile_data.get("glossary", {}).items()])
    setting_info = profile_data.get("setting", "Anime")
    tone_info = profile_data.get("tone_instructions", f"Tono natural en {target_meta['name']}.")

    ep_context_str = ""
    if episode_context and isinstance(episode_context, dict) and episode_context.get("summary"):
        ep_context_str += f"CONTEXTO DEL EPISODIO ACTUAL ({episode_context.get('episode', 'Actual')}):\n{episode_context.get('summary')}\n"

    past_eps = profile_data.get("episodes", [])
    if past_eps and isinstance(past_eps, list):
        filtered_past = [
            e for e in past_eps
            if not episode_context or (e.get("episode") != episode_context.get("episode") and e.get("file_name") != episode_context.get("file_name"))
        ]
        if filtered_past:
            recent_past = filtered_past[-2:]
            p_lines = "\n".join([f"- {e.get('episode', 'Ep')}: {e.get('summary', '')[:160]}" for e in recent_past if e.get("summary")])
            if p_lines:
                ep_context_str += f"RESUMEN DE CAPÍTULOS ANTERIORES:\n{p_lines}\n"

    sys_prompt = f"""Eres un editor, corrector y revisor senior de subtítulos de anime al {prompt_lang}.
Debes aplicar EXACTAMENTE las mismas reglas de traducción y consistencia:
SERIE: {profile_data.get('series_title', 'Anime')} ({profile_data.get('official_title', '')})
AMBIENTACIÓN: {setting_info}
TONO Y ESTILO: {tone_info}
{ep_context_str}PERSONAJES Y GÉNEROS (respeta el género en adjetivos):
{chars_info}
GLOSARIO OBLIGATORIO: {glossary_info}

Tu tarea es revisar las líneas traducidas ({target_meta['ext'].upper()}) comparándolas con el diálogo original para detectar y corregir errores puntuales:
1. Palabras deformadas, alucinadas o inventadas por el modelo.
2. Palabras residuales del idioma de origen que debieron traducirse al {prompt_lang}.
3. Errores ortográficos o de concordancia de género según el personaje que habla.
4. Asegurar que los términos del GLOSARIO OBLIGATORIO se respeten.
5. Palabras mutiladas o letras comidas tras saltos \\N o etiquetas: Compara cada término clave con el original en inglés. Es un error crítico de tokenización que una palabra pierda su primera consonante al estar pegada a \\N (ejemplos reales: 'seasoned' traducido erróneamente como '\\Nazonada' -> DEBES RESTAURARLO como '\\Nsazonada'; 'can\\'t' traducido como '\\No puedo' -> DEBE SER '\\N No puedo'; '\\Nervida' -> '\\Nservida'; '\\Nuvia' -> '\\Nlluvia'). Si ves una palabra tras \\N a la que le falta una letra para concordar con el sentido del diálogo original, RESTAURA la palabra completa de inmediato.

REGLAS CRÍTICAS E INQUEBRANTABLES:
1. NUNCA AGREGUES NI ELIMINES LÍNEAS. Debe haber exactamente una línea de salida por cada línea de entrada.
2. Devuelve las líneas con su numeración exacta en formato: [N] Texto en {prompt_lang}
3. IMPORTANTE: NUNCA incluyas prefijos como 'EN:', 'Original:', 'Traducción:', 'ES:' ni el diálogo original en tu respuesta. Tu salida debe contener ÚNICAMENTE el texto final traducido al {prompt_lang}.
4. Si una línea ya está bien traducida y no tiene errores ni alucinaciones, DEVUELVE ÚNICAMENTE LA TRADUCCIÓN TAL CUAL: [N] Texto traducido. NOTA: Corregir palabras mutiladas, letras faltantes o términos de glosario NO se considera un cambio subjetivo de estilo; es una corrección OBLIGATORIA.
5. Mantén TODAS las etiquetas ASS entre llaves (ej. {{\\i1}}, {{\\pos(x,y)}}) y saltos \\N intactos en la misma posición.
6. NO incluyas notas, comentarios, explicaciones ni texto adicional antes o después."""

    corrected_texts = list(translated_texts)

    for i in range(0, len(raw_texts), batch_size):
        batch_raw = raw_texts[i:i + batch_size]
        batch_trans = translated_texts[i:i + batch_size]

        numbered_items = []
        for idx_in_batch, (orig_line, tr_line) in enumerate(zip(batch_raw, batch_trans)):
            item_num = idx_in_batch + 1
            clean_orig = re.sub(r"\{.*?\}", "", orig_line).replace("\\N", " ").replace("\\n", " ").strip()
            numbered_items.append(f"[{item_num}] Original: {clean_orig} => Traduccion: {tr_line}")

        user_prompt = f"Revisa y pule estas líneas. Devuelve ÚNICAMENTE la traducción final en {prompt_lang} sin prefijos en formato [1] a [{len(batch_raw)}]:\n" + "\n".join(numbered_items) + "\n/no_think"

        resp = call_ai(user_prompt, system_instruction=sys_prompt, timeout=120)
        if not resp:
            continue

        parsed_corrections = {}
        for line in resp.strip().split("\n"):
            m = re.match(r"^\[?(\d+)\]?[\.\:\)]?\s*(.*)$", line.strip())
            if m:
                num = int(m.group(1)) - 1
                val = m.group(2).strip()
                # Limpiar cualquier eco accidental de etiquetas o prefijos de formato (ej: "EN: ... | ES: ...", "Original: ... => Traduccion: ...")
                val = re.sub(r"^.*?(?:\||=>)\s*(?:[A-Za-z0-9\-]+|Traducci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                val = re.sub(r"^(?:[A-Za-z0-9\-]+|Traducci[oó]n):\s*", "", val, flags=re.IGNORECASE).strip()
                if 0 <= num < len(batch_raw) and val:
                    parsed_corrections[num] = val

        if len(parsed_corrections) >= len(batch_raw) * 0.75:
            for idx_in_batch in range(len(batch_raw)):
                global_idx = i + idx_in_batch
                if idx_in_batch in parsed_corrections:
                    corr = parsed_corrections[idx_in_batch]
                    if corr:
                        corrected_texts[global_idx] = corr

    if len(corrected_texts) != len(translated_texts):
        return translated_texts

    return corrected_texts

def extract_series_name(video_path):
    parts = os.path.normpath(video_path).split(os.sep)
    watch_dirs = [os.path.normpath(d) for d, _ in get_parsed_watch_paths()]
    for w in watch_dirs:
        w_parts = w.split(os.sep)
        if len(parts) > len(w_parts) and parts[:len(w_parts)] == w_parts:
            return parts[len(w_parts)]
    if "tvshows" in parts:
        idx = parts.index("tvshows")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    parent = os.path.basename(os.path.dirname(video_path))
    if parent.lower().startswith("season") or parent.lower().startswith("specials"):
        return os.path.basename(os.path.dirname(os.path.dirname(video_path)))
    return parent

def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s"

def enqueue_retry(video_path, force, series_name, error_reason="", increment_attempt=True):
    with queue_lock:
        if video_path not in retry_queue:
            retry_queue[video_path] = {
                "path": video_path,
                "force": force,
                "series_name": series_name,
                "attempts": 1 if increment_attempt else 0,
                "last_error": error_reason,
                "next_retry_at": time.time() + (60 if increment_attempt else 15)
            }
        else:
            if increment_attempt:
                retry_queue[video_path]["attempts"] += 1
            retry_queue[video_path]["last_error"] = error_reason
            att = retry_queue[video_path]["attempts"]
            backoff = 15 if not increment_attempt else min(300, 30 * (2 ** min(att, 4)))
            retry_queue[video_path]["next_retry_at"] = time.time() + backoff
        save_persisted_queue()

def process_file(video_path, force=False, context_only=False, target_languages=None):
    global currently_processing, current_file_start_time, current_batch_num, current_total_batches
    global total_processing_time_sec, completed_jobs_count, cancel_current_flag, last_job_metrics

    start_t = time.time()
    with cancel_lock:
        cancel_current_flag = False
    with processing_lock:
        currently_processing = os.path.basename(video_path)
        current_file_start_time = start_t
        current_batch_num = 0
        current_total_batches = 0
        last_job_metrics = {}

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
            total_processing_time_sec += elapsed
            if success:
                completed_jobs_count += 1
                lines = last_job_metrics.get("lines_count", 0)
                fmt = "Contexto" if context_only else f"{last_job_metrics.get('format', 'SRT')} [{targets_label}]"
                model_used = app_config.get("local_model") if app_config.get("local_only") else app_config.get("cloud_model")
                status_txt = "Contexto Extraído" if context_only else "Completado"
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
                    "status": status_txt
                }
                append_history_record(record)
            currently_processing = None
            current_file_start_time = 0.0
            current_batch_num = 0
            current_total_batches = 0
    return success

def sanitize_subtitle_text(text):
    if not text:
        return ""
    # Corregir palabras mutiladas comunes tras saltos \N o etiquetas de control
    # 1. 'azonada' -> 'sazonada'
    text = re.sub(r'\\N\s*azonad([a-zñáéíóú]*)', r'\\Nsazonad\1', text, flags=re.IGNORECASE)
    # 2. '\No puedo', '\No es', '\No hay', etc. (donde el salto \N se comió el espacio de 'No')
    text = re.sub(r'\\No\s+(pued|sé|se\b|te\b|me\b|le\b|lo\b|la\b|los\b|las\b|nos\b|es\b|son\b|era\b|hay\b|teng|tien|quier|voy|vas|va|vam|van|import|pas|deb|est|hag|dig|cre|val|qued|tem|olvid)', r'\\N No \1', text, flags=re.IGNORECASE)
    # 3. '\No' pegado sin espacio a verbos de negación
    text = re.sub(r'\\No(puedo|pude|podemos|podrá|sabía|sé|tengo|tiene|tenemos|quiero|quiere|queremos|voy|vamos|importa|pasa|debo|debe|debemos|estoy|está|estamos|hago|hace|hacemos|digo|dice|creo|queda|temas)', r'\\N No \1', text, flags=re.IGNORECASE)
    return text

def format_srt_text(raw_text):
    clean = sanitize_subtitle_text(raw_text.strip())
    clean = re.sub(r'\{.*?\}', '', clean)
    clean = clean.replace('\\N', '\n').replace('\\n', '\n').strip()
    return clean

def _process_file_inner(video_path, force=False, context_only=False, target_languages=None):
    global current_batch_num, current_total_batches, cancel_current_flag, last_job_metrics
    if not os.path.isfile(video_path):
        return False

    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    series_name = extract_series_name(video_path)

    if target_languages and isinstance(target_languages, list) and len(target_languages) > 0:
        target_langs = target_languages
    else:
        target_langs = app_config.get("target_languages", ["es-419"])

    # Si es solo contexto, verificamos si ya existe algún subtítulo en español en disco
    if context_only:
        spanish_exts = [".es-419.ass", ".es.ass", ".es-419.srt", ".es.srt", ".spa.srt"]
        existing_sub = None
        for ext in spanish_exts:
            cand = os.path.join(base_dir, f"{base_name}{ext}")
            if os.path.exists(cand) and os.path.getsize(cand) > 0:
                existing_sub = cand
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
                    pref_trk = None
                    for lang_code in source_priority:
                        aliases = LANG_ALIASES.get(lang_code.lower(), [lang_code.lower()])
                        for t in sub_trks:
                            props = t.get("properties", {})
                            lang = (props.get("language") or props.get("language_ietf") or "").lower()
                            t_name = (props.get("track_name") or "").lower()
                            if any(alias in lang or alias in t_name for alias in aliases):
                                pref_trk = t
                                break
                        if pref_trk:
                            break
                    if not pref_trk and sub_trks:
                        pref_trk = sub_trks[0]
                    if pref_trk:
                        t_ext = ".ass" if "substation" in pref_trk.get("codec", "").lower() or "ass" in pref_trk.get("codec", "").lower() else ".srt"
                        t_tmp = os.path.join(base_dir, f"{base_name}_ctx_raw{t_ext}")
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
                last_job_metrics["lines_count"] = len(dialogues)
                last_job_metrics["format"] = "Contexto (Subtítulo Existente)"
                return True

    if app_config.get("local_only", True) and not is_ollama_alive(timeout=1.2):
        enqueue_retry(video_path, force, series_name, "PC apagada (esperando RTX 4070)", increment_attempt=False)
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
        enqueue_retry(video_path, force, series_name, f"Error mkvmerge: {e}")
        return False

    tracks = info.get("tracks", [])
    sub_tracks = [t for t in tracks if t.get("type") == "subtitles"]
    selected_track = None
    source_priority = app_config.get("source_priority", ["ja", "en", "fr", "de", "es", "it", "pt", "ru", "zh"])
    for lang_code in source_priority:
        aliases = LANG_ALIASES.get(lang_code.lower(), [lang_code.lower()])
        for t in sub_tracks:
            props = t.get("properties", {})
            lang = (props.get("language") or props.get("language_ietf") or "").lower()
            t_name = (props.get("track_name") or "").lower()
            if any(alias in lang or alias in t_name for alias in aliases):
                selected_track = t
                break
        if selected_track:
            break
    if not selected_track and sub_tracks:
        selected_track = sub_tracks[0]
    if not selected_track:
        return False

    track_id = selected_track["id"]
    codec = selected_track.get("codec", "")
    is_ass = "substation" in codec.lower() or "ass" in codec.lower() or "ssa" in codec.lower()
    temp_ext = ".ass" if is_ass else ".srt"
    temp_file = os.path.join(base_dir, f"{base_name}_temp{temp_ext}")

    try:
        subprocess.run(["mkvextract", "tracks", video_path, f"{track_id}:{temp_file}"], check=True, capture_output=True)
        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
            enqueue_retry(video_path, force, series_name, "Fallo al extraer subtítulo con mkvextract")
            return False

        profile_data, profile_path = get_or_create_series_profile(series_name)

        if is_ass:
            with open(temp_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            dialogue_indices = []
            raw_texts = []
            for idx, line in enumerate(lines):
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    if len(parts) == 10:
                        style_name = parts[3].strip()
                        txt = parts[9].rstrip("\r\n")
                        if is_translatable_ass_line(style_name, txt):
                            dialogue_indices.append(idx)
                            raw_texts.append(txt)

            if not raw_texts:
                return True

            if context_only:
                print(f"[AutoSub] Modo Solo Contexto: Analizando {len(raw_texts)} líneas extraídas del video...", flush=True)
                generate_and_save_episode_context(series_name, video_path, raw_texts, raw_texts, profile_data, profile_path)
                last_job_metrics["lines_count"] = len(raw_texts)
                last_job_metrics["format"] = "Contexto (Pista Extraída)"
                return True

            last_job_metrics["lines_count"] = len(raw_texts)
            last_job_metrics["format"] = "ASS + SRT"

            # Adaptive batching quota protection:
            # If translating to multiple target languages, scale batch size down and pace
            num_targets = max(1, len(target_langs))
            base_batch = app_config.get("batch_size", 100)
            if num_targets > 1:
                batch_size = max(25, int(base_batch / (1 + 0.35 * (num_targets - 1))))
            else:
                batch_size = base_batch

            total_batches_per_lang = (len(raw_texts) + batch_size - 1) // batch_size
            with processing_lock:
                current_total_batches = total_batches_per_lang * num_targets

            ep_context = None
            any_success = False

            for lang_idx, target_lang in enumerate(target_langs):
                lang_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, {})
                lang_name = lang_meta.get("name", target_lang)
                ext_code = lang_meta.get("ext", target_lang).lower()
                out_ass = os.path.join(base_dir, f"{base_name}.{ext_code}.ass")
                out_srt = os.path.join(base_dir, f"{base_name}.{ext_code}.srt")

                print(f"[AutoSub] ({lang_idx+1}/{num_targets}) Traduciendo al {lang_name} ({target_lang}) en lotes de {batch_size} líneas...", flush=True)

                translated_texts = []
                prev_ctx = None
                translation_success = True

                for i in range(0, len(raw_texts), batch_size):
                    batch_num = (lang_idx * total_batches_per_lang) + (i // batch_size + 1)
                    with processing_lock:
                        current_batch_num = batch_num

                    with cancel_lock:
                        if cancel_current_flag:
                            translation_success = False
                            print(f"[AutoSub] Cancelado por el usuario: {video_path}", flush=True)
                            break

                    batch = raw_texts[i:i + batch_size]
                    trans = translate_batch(batch, profile_data, prev_context=prev_ctx, target_lang=target_lang)
                    if trans is None:
                        translation_success = False
                        break
                    translated_texts.extend(trans)
                    prev_ctx = batch[-2:]
                    time.sleep(0.2)

                if not translation_success:
                    enqueue_retry(video_path, force, series_name, f"Error IA traduciendo lote ASS ({target_lang})")
                    continue

                if ep_context is None:
                    ep_context = generate_and_save_episode_context(series_name, video_path, raw_texts, translated_texts, profile_data, profile_path)

                if app_config.get("proofread", True):
                    print(f"[AutoSub] Realizando revisión final y pulido para {lang_name} ({len(translated_texts)} líneas)...", flush=True)
                    translated_texts = proofread_subtitles(raw_texts, translated_texts, profile_data, batch_size=batch_size, episode_context=ep_context, target_lang=target_lang)

                new_lines = list(lines)
                for idx_dialogue, real_line_idx in enumerate(dialogue_indices):
                    if idx_dialogue < len(translated_texts):
                        parts = new_lines[real_line_idx].split(",", 9)
                        if len(parts) == 10:
                            new_lines[real_line_idx] = ",".join(parts[:9]) + "," + sanitize_subtitle_text(translated_texts[idx_dialogue]) + "\n"

                with open(out_ass, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

                with open(out_srt, "w", encoding="utf-8") as f:
                    c_idx = 1
                    for real_line_idx in dialogue_indices:
                        line = new_lines[real_line_idx]
                        parts = line.split(",", 9)
                        start, end = parts[1], parts[2]
                        srt_txt = format_srt_text(parts[9])
                        if not srt_txt:
                            continue
                        m1 = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", start)
                        m2 = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", end)
                        if m1 and m2:
                            s_fmt = f"{int(m1.group(1)):02d}:{m1.group(2)}:{m1.group(3)},{int(m1.group(4))*10:03d}"
                            e_fmt = f"{int(m2.group(1)):02d}:{m2.group(2)}:{m2.group(3)},{int(m2.group(4))*10:03d}"
                            f.write(f"{c_idx}\n{s_fmt} --> {e_fmt}\n{srt_txt}\n\n")
                            c_idx += 1

                any_success = True
                if num_targets > 1:
                    time.sleep(0.3)

            if not any_success:
                return False

        else:
            with open(temp_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            blocks = content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n\n")
            cues = []
            for b in blocks:
                lines = b.split("\n")
                if len(lines) >= 3 and "-->" in lines[1]:
                    cues.append({
                        "id": lines[0],
                        "time": lines[1],
                        "lines": lines[2:]
                    })

            all_texts = ["\\N".join(c["lines"]) for c in cues]
            if not all_texts:
                return True

            if context_only:
                print(f"[AutoSub] Modo Solo Contexto: Analizando {len(all_texts)} líneas extraídas del video...", flush=True)
                generate_and_save_episode_context(series_name, video_path, all_texts, all_texts, profile_data, profile_path)
                last_job_metrics["lines_count"] = len(all_texts)
                last_job_metrics["format"] = "Contexto (Pista Extraída)"
                return True

            last_job_metrics["lines_count"] = len(all_texts)
            last_job_metrics["format"] = "SRT"

            num_targets = max(1, len(target_langs))
            base_batch = app_config.get("batch_size", 100)
            if num_targets > 1:
                batch_size = max(25, int(base_batch / (1 + 0.35 * (num_targets - 1))))
            else:
                batch_size = base_batch

            total_batches_per_lang = (len(all_texts) + batch_size - 1) // batch_size
            with processing_lock:
                current_total_batches = total_batches_per_lang * num_targets

            ep_context = None
            any_success = False

            for lang_idx, target_lang in enumerate(target_langs):
                lang_meta = SUPPORTED_TARGET_LANGUAGES.get(target_lang, {})
                lang_name = lang_meta.get("name", target_lang)
                ext_code = lang_meta.get("ext", target_lang).lower()
                out_srt = os.path.join(base_dir, f"{base_name}.{ext_code}.srt")

                print(f"[AutoSub] ({lang_idx+1}/{num_targets}) Traduciendo al {lang_name} ({target_lang}) en lotes de {batch_size} líneas...", flush=True)

                translated = []
                prev_ctx = None
                translation_success = True

                for i in range(0, len(all_texts), batch_size):
                    batch_num = (lang_idx * total_batches_per_lang) + (i // batch_size + 1)
                    with processing_lock:
                        current_batch_num = batch_num

                    with cancel_lock:
                        if cancel_current_flag:
                            translation_success = False
                            print(f"[AutoSub] Cancelado por el usuario: {video_path}", flush=True)
                            break

                    batch = all_texts[i:i + batch_size]
                    trans = translate_batch(batch, profile_data, prev_context=prev_ctx, target_lang=target_lang)
                    if trans is None:
                        translation_success = False
                        break
                    translated.extend(trans)
                    prev_ctx = batch[-2:]
                    time.sleep(0.2)

                if not translation_success:
                    enqueue_retry(video_path, force, series_name, f"Error IA traduciendo lote SRT ({target_lang})")
                    continue

                if ep_context is None:
                    ep_context = generate_and_save_episode_context(series_name, video_path, all_texts, translated, profile_data, profile_path)

                if app_config.get("proofread", True):
                    print(f"[AutoSub] Realizando revisión final y pulido para {lang_name} ({len(translated)} líneas)...", flush=True)
                    translated = proofread_subtitles(all_texts, translated, profile_data, batch_size=batch_size, episode_context=ep_context, target_lang=target_lang)

                with open(out_srt, "w", encoding="utf-8") as f:
                    for idx, c in enumerate(cues):
                        text = translated[idx] if idx < len(translated) else "\\N".join(c["lines"])
                        text = format_srt_text(text)
                        f.write(f"{idx + 1}\n{c['time']}\n{text}\n\n")

                any_success = True
                if num_targets > 1:
                    time.sleep(0.3)

            if not any_success:
                return False

        with queue_lock:
            if video_path in retry_queue:
                del retry_queue[video_path]
                save_persisted_queue()

    except Exception as ex:
        enqueue_retry(video_path, force, series_name, str(ex))
        return False
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
    return True

def queue_worker():
    while True:
        try:
            item = job_queue.get()
            if len(item) == 4:
                v_path, force, context_only, target_langs = item
            elif len(item) == 3:
                v_path, force, context_only = item
                target_langs = None
            else:
                v_path, force = item
                context_only = False
                target_langs = None
            process_file(v_path, force=force, context_only=context_only, target_languages=target_langs)
            job_queue.task_done()
        except Exception as e:
            print(f"[Worker Error] {e}", flush=True)
        time.sleep(0.5)

def retry_worker():
    while True:
        try:
            now = time.time()
            due_items = []
            with queue_lock:
                for path, item in list(retry_queue.items()):
                    if now >= item.get("next_retry_at", 0):
                        due_items.append((path, item.get("force", False)))

            for path, force in due_items:
                if app_config.get("local_only", True) and not is_ollama_alive(timeout=1.0):
                    break
                in_job_q = any(path == item[0] for item in list(job_queue.queue))
                if not in_job_q and currently_processing != os.path.basename(path):
                    job_queue.put((path, force))
        except Exception as e:
            print(f"[Retry Worker Error] {e}", flush=True)
        time.sleep(5)

def build_status_dict():
    with processing_lock:
        current = currently_processing
        st = current_file_start_time
        b_num = current_batch_num
        t_batches = current_total_batches
        tot_time = total_processing_time_sec
        jobs_done = completed_jobs_count

    elapsed_current = time.time() - st if current and st > 0 else 0.0
    progress_pct = int((b_num / t_batches) * 100) if t_batches > 0 else 0

    pc_online = is_ollama_alive(timeout=0.6, use_cache=True)

    with queue_lock:
        retry_items = list(retry_queue.values())

    active_jobs_list = []
    with job_queue.mutex:
        for idx, item in enumerate(job_queue.queue):
            c_only = item[2] if len(item) > 2 else False
            t_langs = item[3] if len(item) > 3 else None
            active_jobs_list.append({
                "index": idx + 1,
                "file_name": os.path.basename(item[0]),
                "path": item[0],
                "force": item[1],
                "context_only": c_only,
                "target_languages": t_langs
            })

    profiles_list = []
    if os.path.exists(PROFILES_DIR):
        for fn in sorted(os.listdir(PROFILES_DIR)):
            if fn.endswith(".json"):
                profiles_list.append(fn[:-5])

    with cancel_lock:
        is_cancelling = cancel_current_flag

    history_list = load_history()

    return {
        "status": "online",
        "service": "AutoSub Pure AI v3.5",
        "config": app_config,
        "pc_gpu": {
            "online": pc_online,
            "active_model": app_config.get("local_model", "qwen3.5:9b"),
            "ip": get_ollama_ip(),
        },
        "metrics": {
            "currently_processing": current or "Ninguno",
            "current_elapsed_sec": elapsed_current,
            "current_elapsed_formatted": format_duration(elapsed_current),
            "current_batch": b_num,
            "total_batches": t_batches,
            "progress_percent": progress_pct,
            "total_processing_time_sec": tot_time,
            "total_processing_time_formatted": format_duration(tot_time),
            "completed_jobs_count": jobs_done,
            "active_queue_count": job_queue.qsize(),
            "profiles_count": len(profiles_list),
            "profiles": profiles_list,
            "cancelling": is_cancelling,
            "history_count": len(history_list)
        },
        "active_jobs_queue": active_jobs_list,
        "pending_retry_count": len(retry_items),
        "pending_retry_queue": retry_items,
    }

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoSub AI — Control Center v3.5</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: rgba(17, 24, 39, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --accent-glow: rgba(99, 102, 241, 0.25);
      --green: #10b981;
      --green-glow: rgba(16, 185, 129, 0.2);
      --amber: #f59e0b;
      --red: #ef4444;
      --cyan: #06b6d4;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 20px 24px 60px 24px;
      line-height: 1.5;
    }
    .container { max-width: 1300px; margin: 0 auto; }

    /* Header & Navigation */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 16px;
    }
    .header-left { display: flex; align-items: center; gap: 14px; }
    .brand-icon {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, #6366f1, #3b82f6);
      border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 14px var(--accent-glow);
    }
    .brand-title { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
    .brand-subtitle { font-size: 13px; color: var(--text-muted); }

    /* Navigation Tabs */
    .nav-tabs {
      display: flex;
      gap: 8px;
      background: rgba(15, 23, 42, 0.8);
      padding: 6px;
      border-radius: 14px;
      border: 1px solid var(--card-border);
    }
    .nav-tab {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 8px 16px;
      font-size: 13px;
      font-weight: 600;
      border-radius: 10px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s ease;
    }
    .nav-tab:hover { color: #fff; background: rgba(255, 255, 255, 0.05); }
    .nav-tab.active {
      color: #fff;
      background: linear-gradient(135deg, #6366f1, #3b82f6);
      box-shadow: 0 4px 12px var(--accent-glow);
    }
    .tab-badge {
      background: rgba(255, 255, 255, 0.2);
      padding: 2px 7px;
      border-radius: 10px;
      font-size: 11px;
      font-weight: 700;
    }

    /* Tab Content Panes */
    .tab-content { display: none; }
    .tab-content.active { display: block; animation: fadeIn 0.25s ease-out; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    /* Shared Card Styles */
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      backdrop-filter: blur(16px);
      margin-bottom: 20px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    }
    .card-title {
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      color: #fff;
    }

    /* Dashboard: Metrics Strip */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }
    .metric-card {
      background: rgba(17, 24, 39, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .metric-label { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-value { font-size: 20px; font-weight: 800; color: #fff; }
    .metric-sub { font-size: 12px; color: var(--text-muted); }

    /* Controls Strip */
    .controls-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
      background: rgba(15, 23, 42, 0.7);
      padding: 14px 18px;
      border-radius: 14px;
      border: 1px solid var(--card-border);
      margin-bottom: 20px;
    }
    .control-item { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }

    /* Switch Component */
    .switch {
      position: relative; display: inline-block; width: 44px; height: 24px;
    }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: #374151; transition: .3s; border-radius: 24px;
    }
    .slider:before {
      position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px;
      background-color: white; transition: .3s; border-radius: 50%;
    }
    input:checked + .slider { background-color: var(--accent); }
    input:checked + .slider:before { transform: translateX(20px); }

    /* Progress bar */
    .progress-box {
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 20px;
    }
    .progress-header { display: flex; justify-content: space-between; font-size: 13px; font-weight: 700; margin-bottom: 8px; }
    .progress-track {
      height: 10px; width: 100%; background: #1f2937; border-radius: 5px; overflow: hidden;
    }
    .progress-fill {
      height: 100%; width: 0%;
      background: linear-gradient(90deg, #6366f1, #06b6d4);
      transition: width 0.3s ease;
    }

    /* Form Inputs & Selects */
    input[type="text"], input[type="number"], input[type="password"], select, textarea {
      background: #0f172a;
      border: 1px solid #334155;
      color: #fff;
      padding: 9px 12px;
      border-radius: 10px;
      font-size: 13px;
      font-family: inherit;
      outline: none;
      transition: border-color 0.2s;
      width: 100%;
    }
    input[type="text"]:focus, input[type="number"]:focus, input[type="password"]:focus, select:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-glow);
    }
    .code-textarea {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      line-height: 1.6;
      tab-size: 2;
    }

    /* Buttons */
    .btn {
      padding: 8px 16px;
      font-size: 13px;
      font-weight: 600;
      border-radius: 10px;
      cursor: pointer;
      border: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }
    .btn-primary {
      background: linear-gradient(135deg, #6366f1, #3b82f6);
      color: #fff;
      box-shadow: 0 2px 8px var(--accent-glow);
    }
    .btn-primary:hover { opacity: 0.92; transform: translateY(-1px); }
    .btn-success {
      background: linear-gradient(135deg, #10b981, #059669);
      color: #fff;
      box-shadow: 0 2px 8px var(--green-glow);
    }
    .btn-success:hover { opacity: 0.92; transform: translateY(-1px); }
    .btn-danger {
      background: #ef4444; color: #fff;
    }
    .btn-danger:hover { background: #dc2626; }
    .btn-secondary {
      background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
    }
    .btn-secondary:hover { background: #334155; }
    .btn-sm { padding: 5px 10px; font-size: 12px; border-radius: 8px; }

    /* Tables */
    .table-container {
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--card-border);
      border-radius: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    th {
      background: rgba(15, 23, 42, 0.95);
      color: var(--text-muted);
      font-weight: 700;
      padding: 12px 14px;
      border-bottom: 1px solid var(--card-border);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.5px;
    }
    td {
      padding: 12px 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      color: #e5e7eb;
    }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
    }
    .badge-green { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-blue { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }
    .badge-amber { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-gray { background: rgba(156, 163, 175, 0.15); color: #d1d5db; border: 1px solid rgba(156, 163, 175, 0.3); }

    /* Media Checklist in Dashboard */
    .checklist-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 10px;
      max-height: 380px;
      overflow-y: auto;
      padding: 10px;
      background: #0f172a;
      border-radius: 12px;
      border: 1px solid #1e293b;
    }
    .check-item {
      display: flex;
      align-items: center;
      gap: 10px;
      background: #1e293b;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 12px;
      border: 1px solid transparent;
      cursor: pointer;
    }
    .check-item:hover { border-color: var(--accent); }
    .check-item input[type="checkbox"] { width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent); }
    .check-item-name { flex: 1; word-break: break-all; }

    /* Settings Grid */
    .settings-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
    }
    .setting-group { margin-bottom: 14px; }
    .setting-group label { display: block; font-size: 12px; font-weight: 700; color: #cbd5e1; margin-bottom: 6px; }
    .setting-help { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

    /* Toast Notification */
    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #1e293b;
      color: #fff;
      padding: 12px 20px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      border: 1px solid var(--accent);
      box-shadow: 0 10px 25px rgba(0,0,0,0.5);
      display: none;
      z-index: 9999;
      animation: slideIn .2s ease-out;
    }
    @keyframes slideIn { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
  </style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <header>
    <div class="header-left">
      <div class="brand-icon">⚡</div>
      <div>
        <div class="brand-title">AutoSub Pure AI v3.5</div>
        <div class="brand-subtitle">Traducción de Subtítulos con Lore Web, Revisión y Control Total</div>
      </div>
    </div>

    <!-- Nav Tabs -->
    <nav class="nav-tabs">
      <button class="nav-tab active" id="tab-btn-dashboard" onclick="switchTab('dashboard')">
        <span>📊</span> Traductor y Cola
      </button>
      <button class="nav-tab" id="tab-btn-history" onclick="switchTab('history')">
        <span>📜</span> Historial
        <span class="tab-badge" id="badge-history-count">0</span>
      </button>
      <button class="nav-tab" id="tab-btn-profiles" onclick="switchTab('profiles')">
        <span>🎭</span> Perfiles
        <span class="tab-badge" id="badge-profiles-count">0</span>
      </button>
      <button class="nav-tab" id="tab-btn-settings" onclick="switchTab('settings')">
        <span>⚙️</span> Configuración
      </button>
    </nav>
  </header>

  <!-- ==================== TAB 1: DASHBOARD ==================== -->
  <div id="tab-dashboard" class="tab-content active">
    <!-- GPU & Status Strip -->
    <div class="metrics-grid">
      <div class="metric-card">
        <span class="metric-label">GPU Local (RTX 4070)</span>
        <div style="display:flex; align-items:center; gap:8px;">
          <span id="gpu-dot" style="width:10px; height:10px; border-radius:50%; background:#ef4444;"></span>
          <span class="metric-value" id="gpu-status-text" style="font-size:16px;">Comprobando...</span>
        </div>
        <span class="metric-sub" id="gpu-model-sub">Ollama: qwen3.5:9b</span>
      </div>

      <div class="metric-card">
        <span class="metric-label">Procesando Ahora</span>
        <div class="metric-value" id="current-file-text" style="font-size:15px; word-break:break-word;">Ninguno</div>
        <span class="metric-sub" id="current-time-sub">0s</span>
      </div>

      <div class="metric-card">
        <span class="metric-label">Completados & Tiempo</span>
        <div class="metric-value" id="jobs-done-text">0</div>
        <span class="metric-sub" id="total-time-sub">Tiempo acumulado: 0s</span>
      </div>

      <div class="metric-card">
        <span class="metric-label">Cola de Espera</span>
        <div class="metric-value" id="queue-count-text">0</div>
        <span class="metric-sub" id="retry-count-sub">0 reintentos</span>
      </div>
    </div>

    <!-- Quick Controls Strip -->
    <div class="controls-strip">
      <div class="control-item">
        <span>Modo Local:</span>
        <label class="switch">
          <input type="checkbox" id="local-only-switch" onchange="toggleQuickConfig('local_only')">
          <span class="slider"></span>
        </label>
        <span id="local-mode-label" class="badge badge-blue">Local</span>
      </div>

      <div class="control-item">
        <span>Revisión Final:</span>
        <label class="switch">
          <input type="checkbox" id="proofread-switch" onchange="toggleQuickConfig('proofread')">
          <span class="slider"></span>
        </label>
      </div>

      <div class="control-item">
        <span>Modo Rápido:</span>
        <label class="switch">
          <input type="checkbox" id="fast-mode-switch" onchange="toggleQuickConfig('fast_mode')">
          <span class="slider"></span>
        </label>
      </div>

      <div class="control-item">
        <span>Lote:</span>
        <select id="quick-batch-size" onchange="updateBatchSize(this.value)" style="width:110px; padding:4px 8px;">
          <option value="50">50 líneas</option>
          <option value="100">100 líneas</option>
          <option value="150">150 líneas</option>
          <option value="9999">Todas (Full)</option>
        </select>
      </div>

      <div class="control-item" style="margin-left:auto;">
        <button class="btn btn-danger btn-sm" id="btn-cancel" onclick="cancelCurrentJob()">
          🛑 Cancelar Traducción Actual
        </button>
      </div>
    </div>

    <!-- Progress box -->
    <div class="progress-box" id="progress-box" style="display:none;">
      <div class="progress-header">
        <span id="progress-title">Traduciendo...</span>
        <span id="progress-percentage">0%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progress-bar"></div>
      </div>
    </div>

    <!-- Media Selector for Reprocessing -->
    <div class="card">
      <div class="card-title">
        <span>📂 Selector de Archivos (Series y Películas)</span>
        <button class="btn btn-secondary btn-sm" onclick="loadMediaCatalog()" style="margin-left:auto;">🔄 Refrescar Catálogo</button>
      </div>
      <div style="display:flex; gap:12px; margin-bottom:12px; flex-wrap:wrap;">
        <div style="flex:1; min-width:240px;">
          <select id="media-catalog-select" onchange="onCatalogItemSelected()">
            <option value="">-- Seleccionar Serie o Película --</option>
          </select>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="selectChecklist(true)">Seleccionar Todo</button>
        <button class="btn btn-secondary btn-sm" onclick="selectChecklist(false)">Deseleccionar</button>
        <button class="btn btn-primary btn-sm" onclick="enqueueSelectedMedia(false)">🚀 Encolar para Traducir/Reprocesar</button>
        <button class="btn btn-success btn-sm" onclick="enqueueSelectedMedia(true)" title="Extrae el contexto del episodio (resumen narrativo, personajes y glosario) sin traducir ni modificar los subtítulos">🧠 Solo Extraer Contexto</button>
      </div>
      <div style="background:rgba(15, 23, 42, 0.45); border:1px solid var(--border); border-radius:6px; padding:10px 14px; margin-bottom:12px; display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
        <span style="font-size:12px; font-weight:700; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">🌐 Idioma(s) Destino:</span>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="es-419" checked> 🇲🇽 Español Latino</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="es"> 🇪🇸 Español España</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="en"> 🇬🇧 English</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="fr"> 🇫🇷 Français</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="de"> 🇩🇪 Deutsch</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="pt-BR"> 🇧🇷 Português (BR)</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="it"> 🇮🇹 Italiano</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="ja"> 🇯🇵 日本語</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="ru"> 🇷🇺 Русский</label>
        <label style="font-size:12px; display:inline-flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" class="enqueue-target-chk" value="zh-CN"> 🇨🇳 简体中文</label>
      </div>
      <div id="media-files-list" class="checklist-grid">
        <div style="color:var(--text-muted); font-size:13px; padding:10px;">Selecciona una serie o película arriba para ver sus archivos de video.</div>
      </div>
    </div>

    <!-- Active Queue Table -->
    <div class="card">
      <div class="card-title">📋 Cola de Espera Activa</div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th style="width:60px;">#</th>
              <th>Archivo de Video</th>
              <th>Ruta Completa</th>
              <th style="width:100px;">Acción</th>
            </tr>
          </thead>
          <tbody id="active-queue-tbody">
            <tr><td colspan="4" style="text-align:center; color:var(--text-muted);">No hay archivos pendientes en la cola.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ==================== TAB 2: HISTORIAL ==================== -->
  <div id="tab-history" class="tab-content">
    <div class="card">
      <div class="card-title" style="justify-content:space-between; flex-wrap:wrap; gap:12px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <span>📜 Historial de Traducciones</span>
          <span class="badge badge-purple" id="hist-total-badge">0 registros</span>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
          <input type="text" id="hist-search" placeholder="🔍 Filtrar por serie, película o archivo..." oninput="filterHistory()" style="width:260px;">
          <button class="btn btn-danger btn-sm" onclick="clearHistory()">🗑️ Limpiar Historial</button>
        </div>
      </div>

      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th style="width:160px;">Fecha y Hora</th>
              <th>Serie / Película</th>
              <th>Archivo</th>
              <th style="width:110px;">Duración</th>
              <th style="width:80px;">Líneas</th>
              <th style="width:140px;">Modelo</th>
              <th style="width:90px;">Revisión</th>
              <th style="width:120px;">Acción</th>
            </tr>
          </thead>
          <tbody id="history-tbody">
            <tr><td colspan="8" style="text-align:center; color:var(--text-muted);">Cargando historial...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ==================== TAB 3: PERFILES ==================== -->
  <div id="tab-profiles" class="tab-content">
    <div class="card">
      <div class="card-title" style="justify-content:space-between; flex-wrap:wrap; gap:12px;">
        <div style="display:flex; align-items:center; gap:12px;">
          <span>🎭 Visor y Editor de Perfiles</span>
          <select id="profile-selector" onchange="onProfileSelected()" style="width:260px;">
            <option value="">-- Seleccionar Perfil --</option>
          </select>
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-secondary btn-sm" onclick="startNewProfile()">➕ Nuevo</button>
          <button class="btn btn-secondary btn-sm" onclick="promptGenerateProfile()">🌐 Generar con IA</button>
          <button class="btn btn-secondary btn-sm" id="btn-toggle-profile-mode" onclick="toggleProfileMode()">💻 Modo JSON</button>
          <button class="btn btn-success btn-sm" onclick="saveCurrentProfile()">💾 Guardar Perfil</button>
          <button class="btn btn-danger btn-sm" onclick="deleteCurrentProfile()">🗑️ Eliminar</button>
        </div>
      </div>

      <!-- Modo Visual -->
      <div id="profile-visual-container">
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:14px; margin-bottom:16px;">
          <div>
            <label style="font-size:12px; font-weight:700; color:#cbd5e1; display:block; margin-bottom:4px;">Título de la Serie / Clave</label>
            <input type="text" id="prof-series-title" placeholder="Ej. Reincarnated as a Sword">
          </div>
          <div>
            <label style="font-size:12px; font-weight:700; color:#cbd5e1; display:block; margin-bottom:4px;">Título Oficial</label>
            <input type="text" id="prof-official-title" placeholder="Ej. Tensei Shitara Ken Deshita">
          </div>
        </div>

        <div style="margin-bottom:14px;">
          <label style="font-size:12px; font-weight:700; color:#cbd5e1; display:block; margin-bottom:4px;">Ambientación & Premisa</label>
          <textarea id="prof-setting" rows="3" placeholder="Resumen del universo y premisa para guiar a la IA..."></textarea>
        </div>

        <div style="margin-bottom:20px;">
          <label style="font-size:12px; font-weight:700; color:#cbd5e1; display:block; margin-bottom:4px;">Instrucciones de Tono y Estilo</label>
          <textarea id="prof-tone" rows="2" placeholder="Tono español latino neutro, manejo de tuteo, muletillas..."></textarea>
        </div>

        <!-- Personajes Table -->
        <div style="margin-bottom:24px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span style="font-size:14px; font-weight:700; color:#cbd5e1;">👥 Personajes y Géneros</span>
            <button class="btn btn-secondary btn-sm" onclick="addCharacterRow()">➕ Agregar Personaje</button>
          </div>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th style="width:200px;">Nombre</th>
                  <th style="width:140px;">Género</th>
                  <th>Personalidad y Forma de Hablar</th>
                  <th style="width:70px;">Acción</th>
                </tr>
              </thead>
              <tbody id="prof-characters-tbody">
              </tbody>
            </table>
          </div>
        </div>

        <!-- Glosario Table -->
        <div>
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span style="font-size:14px; font-weight:700; color:#cbd5e1;">📖 Glosario Obligatorio</span>
            <button class="btn btn-secondary btn-sm" onclick="addGlossaryRow()">➕ Agregar Término</button>
          </div>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th style="width:280px;">Término Original (Inglés/JP)</th>
                  <th>Traducción Oficial (Español Latino)</th>
                  <th style="width:70px;">Acción</th>
                </tr>
              </thead>
              <tbody id="prof-glossary-tbody">
              </tbody>
            </table>
          </div>
        </div>

        <!-- Continuidad y Contexto por Episodio -->
        <div style="margin-top:28px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
            <div>
              <span style="font-size:14px; font-weight:700; color:#cbd5e1;">🎬 Contexto Narrativo y Continuidad por Capítulo</span>
              <span style="font-size:12px; color:#94a3b8; margin-left:8px;">(Auto-generado tras procesar cada capítulo o editable a mano)</span>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="addEpisodeContextRow()">➕ Agregar Capítulo Manual</button>
          </div>
          <div id="prof-episodes-container" style="display:flex; flex-direction:column; gap:10px;">
          </div>
        </div>
      </div>

      <!-- Modo JSON Raw -->
      <div id="profile-json-container" style="display:none;">
        <textarea id="prof-json-raw" class="code-textarea" rows="24" placeholder="{ ... }"></textarea>
      </div>
    </div>
  </div>

  <!-- ==================== TAB 4: CONFIGURACIÓN ==================== -->
  <div id="tab-settings" class="tab-content">
    <div class="card">
      <div class="card-title">⚙️ Ajustes del Sistema y Claves API</div>
      <div class="settings-grid">

        <!-- Card 1: Red y Ollama -->
        <div class="card" style="margin-bottom:0; background:rgba(15, 23, 42, 0.6);">
          <div class="card-title" style="font-size:14px;">🖥️ GPU Local (Ollama)</div>
          <div class="setting-group">
            <label>IP del Servidor Ollama</label>
            <input type="text" id="cfg-ollama-ip" placeholder="192.168.1.12">
            <div class="setting-help">IP del PC con la RTX 4070 ejecutando Ollama.</div>
          </div>
          <div class="setting-group">
            <label>Puerto Ollama</label>
            <input type="number" id="cfg-ollama-port" placeholder="11434">
          </div>
          <div class="setting-group">
            <label>Modelo Local</label>
            <div style="display:flex; gap:6px;">
              <select id="cfg-local-model"></select>
              <button class="btn btn-secondary btn-sm" onclick="loadOllamaModels()">🔄</button>
            </div>
          </div>
        </div>

        <!-- Card 2: Nube y API Keys -->
        <div class="card" style="margin-bottom:0; background:rgba(15, 23, 42, 0.6);">
          <div class="card-title" style="font-size:14px;">☁️ Proveedores de Nube</div>
          <div class="setting-group">
            <label>Google Gemini API Key</label>
            <div style="display:flex; gap:6px;">
              <input type="password" id="cfg-gemini-key" placeholder="AIzaSy...">
              <button class="btn btn-secondary btn-sm" onclick="togglePasswordVisibility('cfg-gemini-key')">👁️</button>
            </div>
          </div>
          <div class="setting-group">
            <label>Modelo Nube Preferido</label>
            <select id="cfg-cloud-model">
              <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Recomendado)</option>
              <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              <option value="gemma-4-31b-it">gemma-4-31b-it (14.4k req/día)</option>
              <option value="gemma-4-26b-a4b-it">gemma-4-26b-a4b-it</option>
              <option value="gemini-2.5-flash">gemini-2.5-flash</option>
              <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite</option>
            </select>
          </div>
          <div class="setting-group">
            <label>OpenRouter API Key</label>
            <div style="display:flex; gap:6px;">
              <input type="password" id="cfg-or-key" placeholder="sk-or-v1-...">
              <button class="btn btn-secondary btn-sm" onclick="togglePasswordVisibility('cfg-or-key')">👁️</button>
            </div>
          </div>
          <div class="setting-group" style="display:flex; align-items:center; gap:10px;">
            <input type="checkbox" id="cfg-enable-or" style="width:18px; height:18px;">
            <label for="cfg-enable-or" style="margin-bottom:0; cursor:pointer;">Habilitar OpenRouter como fallback secundario</label>
          </div>
        </div>

        <!-- Card 3: Prioridad de Idiomas y Salida -->
        <div class="card" style="margin-bottom:0; background:rgba(15, 23, 42, 0.6); grid-column: 1 / -1;">
          <div class="card-title" style="font-size:14px;">🌐 Idiomas y Prioridad de Selección</div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px;">
            <div class="setting-group" style="margin-bottom:0;">
              <label>Prioridad de Pistas Fuente en MKV (Separados por coma)</label>
              <input type="text" id="cfg-source-priority" placeholder="ja, en, fr, de, es, it, pt, ru, zh">
              <div class="setting-help">Orden de preferencia para extraer el subtítulo original del video (ej. 1° japonés <code>ja</code>, 2° inglés <code>en</code>, 3° francés <code>fr</code>). Se buscan códigos ISO y nombres de pista.</div>
            </div>
            <div class="setting-group" style="margin-bottom:0;">
              <label>Idiomas de Salida Predeterminados</label>
              <div id="cfg-target-languages-grid" style="display:flex; gap:10px; flex-wrap:wrap; margin-top:6px;">
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="es-419"> 🇲🇽 Español Latino (es-419)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="es"> 🇪🇸 Español España (es)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="en"> 🇬🇧 English (en)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="fr"> 🇫🇷 Français (fr)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="de"> 🇩🇪 Deutsch (de)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="pt-BR"> 🇧🇷 Português (pt-BR)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="it"> 🇮🇹 Italiano (it)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="ja"> 🇯🇵 日本語 (ja)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="ru"> 🇷🇺 Русский (ru)</label>
                <label style="font-size:12px; display:inline-flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" class="cfg-target-chk" value="zh-CN"> 🇨🇳 简体中文 (zh-CN)</label>
              </div>
              <div class="setting-help">Si seleccionas varios idiomas, AutoSub adaptará automáticamente el tamaño de los lotes para no saturar tu cuota diaria/minuto.</div>
            </div>
          </div>
        </div>

        <!-- Card 4: Bibliotecas y Rutas de Medios -->
        <div class="card" style="margin-bottom:0; background:rgba(15, 23, 42, 0.6); grid-column: 1 / -1;">
          <div class="card-title" style="font-size:14px; display:flex; justify-content:space-between; align-items:center;">
            <span>📁 Bibliotecas y Rutas de Medios</span>
            <button type="button" class="btn btn-secondary btn-sm" onclick="addLibraryRow('', '')">➕ Agregar Biblioteca</button>
          </div>
          <div style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">
            Configura el nombre visible de tus bibliotecas (ej: <strong>Anime</strong>, <strong>Películas</strong>, <strong>Doramas</strong>) y la ruta física en el servidor. En el selector de medios aparecerán organizadas con este nombre.
          </div>
          <div id="cfg-libraries-list" style="display:flex; flex-direction:column; gap:8px;"></div>
        </div>
      </div>

      <div style="margin-top:20px; display:flex; justify-content:flex-end;">
        <button class="btn btn-primary" onclick="saveAllSettings()" style="padding:10px 24px;">💾 Guardar Toda la Configuración</button>
      </div>
    </div>
  </div>

</div>

<!-- Toast Popup -->
<div id="toast">Notificación</div>

<script>
let currentConfig = {};
let allHistoryRecords = [];
let _seriesCatalog = [];
let currentProfileData = null;
let profileMode = 'visual';

function showToast(msg) {
  const t = document.getElementById('toast');
  t.innerText = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3200);
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + tabId).classList.add('active');
  document.getElementById('tab-btn-' + tabId).classList.add('active');

  if (tabId === 'history') loadHistory();
  if (tabId === 'profiles') loadProfilesCatalog();
  if (tabId === 'settings') loadSettingsForm();
}

function togglePasswordVisibility(elemId) {
  const el = document.getElementById(elemId);
  el.type = el.type === 'password' ? 'text' : 'password';
}

// === TELEMETRÍA Y POLLING ===
async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    currentConfig = data.config || {};

    // GPU status
    const pc = data.pc_gpu || {};
    const gpuDot = document.getElementById('gpu-dot');
    const gpuText = document.getElementById('gpu-status-text');
    if (pc.online) {
      gpuDot.style.background = '#10b981';
      gpuText.innerText = 'Online (' + pc.active_model + ')';
    } else {
      gpuDot.style.background = '#ef4444';
      gpuText.innerText = 'Apagada / Offline';
    }
    document.getElementById('gpu-model-sub').innerText = 'IP: ' + (pc.ip || '192.168.1.12');

    // Metrics
    const m = data.metrics || {};
    document.getElementById('current-file-text').innerText = m.currently_processing || 'Ninguno';
    document.getElementById('current-time-sub').innerText = m.current_elapsed_formatted || '0s';
    document.getElementById('jobs-done-text').innerText = m.completed_jobs_count || 0;
    document.getElementById('total-time-sub').innerText = 'Tiempo total: ' + (m.total_processing_time_formatted || '0s');
    document.getElementById('queue-count-text').innerText = m.active_queue_count || 0;
    document.getElementById('retry-count-sub').innerText = (data.pending_retry_count || 0) + ' reintentos';

    // Badges
    document.getElementById('badge-history-count').innerText = m.history_count || 0;
    document.getElementById('badge-profiles-count').innerText = m.profiles_count || 0;

    // Progress Bar
    const pBox = document.getElementById('progress-box');
    if (m.currently_processing && m.currently_processing !== 'Ninguno') {
      pBox.style.display = 'block';
      document.getElementById('progress-title').innerText = 'Lote ' + m.current_batch + ' de ' + m.total_batches;
      document.getElementById('progress-percentage').innerText = m.progress_percent + '%';
      document.getElementById('progress-bar').style.width = m.progress_percent + '%';
    } else {
      pBox.style.display = 'none';
    }

    // Quick switches
    document.getElementById('local-only-switch').checked = currentConfig.local_only !== false;
    document.getElementById('proofread-switch').checked = currentConfig.proofread !== false;
    document.getElementById('fast-mode-switch').checked = currentConfig.fast_mode !== false;
    document.getElementById('quick-batch-size').value = currentConfig.batch_size || 100;

    const modeLbl = document.getElementById('local-mode-label');
    if (currentConfig.local_only) {
      modeLbl.className = 'badge badge-blue';
      modeLbl.innerText = 'Solo Local';
    } else {
      modeLbl.className = 'badge badge-green';
      modeLbl.innerText = 'Fallback Nube';
    }

    // Active Queue
    renderActiveQueue(data.active_jobs_queue || []);

  } catch (err) {
    console.warn("Poll status error", err);
  }
}

function renderActiveQueue(queueItems) {
  const tbody = document.getElementById('active-queue-tbody');
  if (!queueItems || queueItems.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:16px;">No hay archivos en la cola.</td></tr>';
    return;
  }
  tbody.innerHTML = queueItems.map(item => `
    <tr>
      <td><span class="badge badge-purple">#${item.index}</span></td>
      <td>
        <strong>${item.file_name}</strong>
        ${item.context_only ? '<span class="badge badge-blue" style="margin-left:6px; font-size:10px;">🧠 Solo Contexto</span>' : (item.target_languages ? `<span class="badge badge-green" style="margin-left:6px; font-size:10px;">🌐 ${item.target_languages.join(', ')}</span>` : '')}
      </td>
      <td style="color:var(--text-muted); font-size:12px;">${item.path}</td>
      <td>
        <button class="btn btn-danger btn-sm" onclick="cancelQueueItem('${item.path.replace(/'/g, "\\'")}')">Eliminar</button>
      </td>
    </tr>
  `).join('');
}

async function cancelQueueItem(path) {
  await fetch('/api/cancel_queue_item', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: path})
  });
  showToast('Elemento eliminado de la cola');
  pollStatus();
}

async function cancelCurrentJob() {
  if (confirm('¿Seguro que deseas cancelar la traducción en curso?')) {
    await fetch('/api/cancel', {method: 'POST'});
    showToast('Cancelación solicitada...');
  }
}

async function toggleQuickConfig(key) {
  const val = document.getElementById(key.replace('_', '-') + '-switch').checked;
  const payload = {};
  payload[key] = val;
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  showToast('Configuración actualizada');
  pollStatus();
}

async function updateBatchSize(val) {
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({batch_size: parseInt(val)})
  });
  showToast('Tamaño de lote cambiado a ' + val);
}

// === MEDIA CATALOG (Series & Películas) ===
async function loadMediaCatalog() {
  try {
    const res = await fetch('/api/series_list');
    const data = await res.json();
    _seriesCatalog = data.catalog || [];
    const sel = document.getElementById('media-catalog-select');
    sel.innerHTML = '<option value="">-- Seleccionar Serie o Película --</option>' +
      _seriesCatalog.map(item => `<option value="${item.path.replace(/"/g, '&quot;')}">[${item.category}] ${item.name}</option>`).join('');
  } catch (err) {
    console.error(err);
  }
}

async function onCatalogItemSelected() {
  const path = document.getElementById('media-catalog-select').value;
  const container = document.getElementById('media-files-list');
  if (!path) {
    container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">Selecciona una serie o película arriba.</div>';
    return;
  }
  container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">Cargando archivos...</div>';
  try {
    const res = await fetch('/api/series_files?path=' + encodeURIComponent(path));
    const data = await res.json();
    const files = data.files || [];
    if (files.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">No se encontraron videos .mkv/.mp4.</div>';
      return;
    }
    container.innerHTML = files.map((f, i) => {
      const subBadge = f.has_sub
        ? '<span class="badge badge-green" style="font-size:10px;">Tiene Sub</span>'
        : '<span class="badge badge-amber" style="font-size:10px;">Sin Sub</span>';
      return `
        <label class="check-item">
          <input type="checkbox" class="media-file-chk" value="${f.path.replace(/"/g, '&quot;')}">
          <div class="check-item-name">
            <div>${f.name}</div>
            <div style="font-size:10px; color:var(--text-muted); display:flex; gap:6px; margin-top:2px;">
              <span>${f.size_mb} MB</span> ${subBadge}
            </div>
          </div>
        </label>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = '<div style="color:red; font-size:13px; padding:10px;">Error cargando archivos: ' + err + '</div>';
  }
}

function selectChecklist(select) {
  document.querySelectorAll('.media-file-chk').forEach(c => c.checked = select);
}

async function enqueueSelectedMedia(contextOnly = false) {
  const chks = Array.from(document.querySelectorAll('.media-file-chk:checked')).map(c => c.value);
  if (chks.length === 0) {
    alert('Por favor selecciona al menos un archivo de la lista.');
    return;
  }
  const targetLangs = Array.from(document.querySelectorAll('.enqueue-target-chk:checked')).map(c => c.value);
  if (!contextOnly && targetLangs.length === 0) {
    alert('Por favor selecciona al menos un idioma destino para la traducción.');
    return;
  }
  const res = await fetch('/api/batch_enqueue', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      paths: chks,
      force: true,
      context_only: !!contextOnly,
      target_languages: targetLangs
    })
  });
  const data = await res.json();
  showToast(contextOnly
    ? `¡${data.enqueued_count || chks.length} archivo(s) encolados para Solo Contexto!`
    : `¡${data.enqueued_count || chks.length} archivo(s) agregados a la cola (${targetLangs.join(', ')})!`);
  pollStatus();
}

// === TAB 2: HISTORIAL ===
async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    allHistoryRecords = data.history || [];
    document.getElementById('hist-total-badge').innerText = allHistoryRecords.length + ' registros';
    renderHistoryTable(allHistoryRecords);
  } catch (err) {
    console.error(err);
  }
}

function renderHistoryTable(records) {
  const tbody = document.getElementById('history-tbody');
  if (!records || records.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:16px;">No hay registros en el historial todavía.</td></tr>';
    return;
  }
  tbody.innerHTML = records.map(r => {
    let durColor = 'badge-green';
    if (r.duration_sec > 180) durColor = 'badge-purple';
    else if (r.duration_sec > 60) durColor = 'badge-blue';

    const isContextOnly = (r.format === 'Contexto' || r.status === 'Contexto Extraído' || (r.format && r.format.startsWith('Contexto')));
    const proofBadge = isContextOnly
      ? '<span class="badge badge-purple">🧠 Contexto</span>'
      : (r.proofread ? '<span class="badge badge-green">Sí</span>' : '<span class="badge badge-gray">No</span>');

    return `
      <tr>
        <td style="font-size:12px; color:var(--text-muted);">${r.completed_at || '-'}</td>
        <td><strong>${r.title || '-'}</strong></td>
        <td title="${r.path || ''}" style="word-break:break-all;">
          ${r.file_name}
          ${isContextOnly ? '<span class="badge badge-blue" style="font-size:10px; margin-left:4px;">Solo Contexto</span>' : ''}
        </td>
        <td><span class="badge ${durColor}">${r.duration_formatted || r.duration_sec + 's'}</span></td>
        <td>${r.lines_count || 0}</td>
        <td style="font-size:12px;">${r.model || 'Auto'}</td>
        <td>${proofBadge}</td>
        <td>
          <button class="btn btn-primary btn-sm" onclick="reprocessSingleFile('${(r.path || '').replace(/'/g, "\\'")}')">
            🔄 Reprocesar
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function filterHistory() {
  const q = document.getElementById('hist-search').value.toLowerCase().trim();
  if (!q) {
    renderHistoryTable(allHistoryRecords);
    return;
  }
  const filtered = allHistoryRecords.filter(r =>
    (r.title && r.title.toLowerCase().includes(q)) ||
    (r.file_name && r.file_name.toLowerCase().includes(q))
  );
  renderHistoryTable(filtered);
}

async function reprocessSingleFile(filePath) {
  if (!filePath) return;
  const res = await fetch('/api/batch_enqueue', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({paths: [filePath], force: true})
  });
  showToast('¡Archivo reenviado a la cola para reprocesar!');
  pollStatus();
}

async function clearHistory() {
  if (confirm('¿Deseas vaciar todo el registro de historial?')) {
    await fetch('/api/history/clear', {method: 'POST'});
    allHistoryRecords = [];
    renderHistoryTable([]);
    document.getElementById('hist-total-badge').innerText = '0 registros';
    showToast('Historial vaciado');
  }
}

// === TAB 3: PERFILES ===
async function loadProfilesCatalog() {
  try {
    const res = await fetch('/api/profiles');
    const data = await res.json();
    const profiles = data.profiles || [];
    const sel = document.getElementById('profile-selector');
    sel.innerHTML = '<option value="">-- Seleccionar Perfil --</option>' +
      profiles.map(p => `<option value="${p.name}">[${p.characters_count} chars] ${p.name}</option>`).join('');
  } catch (err) {
    console.error(err);
  }
}

async function onProfileSelected() {
  const name = document.getElementById('profile-selector').value;
  if (!name) return;
  try {
    const res = await fetch('/api/profile?name=' + encodeURIComponent(name));
    const data = await res.json();
    if (data.status === 'ok') {
      currentProfileData = data.profile || {};
      renderProfileForm(currentProfileData);
    }
  } catch (err) {
    console.error(err);
  }
}

function renderProfileForm(p) {
  document.getElementById('prof-series-title').value = p.series_title || '';
  document.getElementById('prof-official-title').value = p.official_title || '';
  document.getElementById('prof-setting').value = p.setting || '';
  document.getElementById('prof-tone').value = p.tone_instructions || '';

  // Personajes
  const cTbody = document.getElementById('prof-characters-tbody');
  const chars = p.characters || [];
  cTbody.innerHTML = chars.map((c, idx) => `
    <tr>
      <td><input type="text" class="char-name" value="${(c.name || '').replace(/"/g, '&quot;')}"></td>
      <td>
        <select class="char-gender">
          <option value="Femenino" ${c.gender === 'Femenino' ? 'selected' : ''}>Femenino</option>
          <option value="Masculino" ${c.gender === 'Masculino' ? 'selected' : ''}>Masculino</option>
          <option value="Desconocido" ${c.gender === 'Desconocido' ? 'selected' : ''}>Desconocido</option>
        </select>
      </td>
      <td><input type="text" class="char-pers" value="${(c.personality || '').replace(/"/g, '&quot;')}"></td>
      <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">🗑️</button></td>
    </tr>
  `).join('');

  // Glosario
  const gTbody = document.getElementById('prof-glossary-tbody');
  const glossary = p.glossary || {};
  gTbody.innerHTML = Object.entries(glossary).map(([k, v]) => `
    <tr>
      <td><input type="text" class="gloss-key" value="${k.replace(/"/g, '&quot;')}"></td>
      <td><input type="text" class="gloss-val" value="${String(v).replace(/"/g, '&quot;')}"></td>
      <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">🗑️</button></td>
    </tr>
  `).join('');

  // Episodios y Continuidad
  const epContainer = document.getElementById('prof-episodes-container');
  const episodes = p.episodes || [];
  if (episodes.length === 0) {
    epContainer.innerHTML = '<div style="text-align:center; padding:18px; color:#64748b; font-size:12px; background:#0f172a; border:1px dashed #334155; border-radius:8px;">No hay capítulos registrados todavía para este perfil. Al procesar episodios de esta serie, su contexto narrativo se sintetizará y guardará aquí automáticamente para alimentar los capítulos siguientes y la revisión final.</div>';
  } else {
    epContainer.innerHTML = episodes.map((ep, idx) => `
      <div class="episode-context-card" style="background:#0f172a; border:1px solid #334155; border-radius:8px; padding:12px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:8px; flex-wrap:wrap;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:14px; font-weight:700; color:#38bdf8;">📺</span>
            <input type="text" class="ep-name" value="${(ep.episode || '').replace(/"/g, '&quot;')}" style="font-weight:700; width:130px; padding:4px 8px; font-size:12px;" placeholder="Episodio">
            <input type="text" class="ep-file" value="${(ep.file_name || '').replace(/"/g, '&quot;')}" style="width:230px; padding:4px 8px; font-size:11px; color:#94a3b8;" placeholder="Archivo">
            <span class="ep-date" style="font-size:11px; color:#64748b;">${ep.updated_at || ep.processed_at || ''}</span>
          </div>
          <button class="btn btn-danger btn-sm" onclick="this.closest('.episode-context-card').remove()">🗑️</button>
        </div>
        <textarea class="ep-summary" rows="3" style="width:100%; font-size:12px; resize:vertical;" placeholder="Resumen del capítulo, giros de trama, dinámicas de personajes...">${(ep.summary || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</textarea>
      </div>
    `).join('');
  }

  // JSON Raw
  document.getElementById('prof-json-raw').value = JSON.stringify(p, null, 2);
}

function addEpisodeContextRow() {
  const container = document.getElementById('prof-episodes-container');
  if (container.querySelector('div[style*="dashed"]')) {
    container.innerHTML = '';
  }
  const card = document.createElement('div');
  card.className = 'episode-context-card';
  card.style = 'background:#0f172a; border:1px solid #334155; border-radius:8px; padding:12px;';
  const now = new Date().toISOString().replace('T', ' ').substring(0, 16);
  card.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:8px; flex-wrap:wrap;">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:14px; font-weight:700; color:#38bdf8;">📺</span>
        <input type="text" class="ep-name" placeholder="Ej. S01E01" style="font-weight:700; width:130px; padding:4px 8px; font-size:12px;">
        <input type="text" class="ep-file" placeholder="Nombre de archivo (opcional)" style="width:230px; padding:4px 8px; font-size:11px; color:#94a3b8;">
        <span class="ep-date" style="font-size:11px; color:#64748b;">${now}</span>
      </div>
      <button class="btn btn-danger btn-sm" onclick="this.closest('.episode-context-card').remove()">🗑️</button>
    </div>
    <textarea class="ep-summary" rows="3" style="width:100%; font-size:12px; resize:vertical;" placeholder="Escribe el resumen del capítulo o contexto clave para los siguientes..."></textarea>
  `;
  container.prepend(card);
}

function addCharacterRow() {
  const tbody = document.getElementById('prof-characters-tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="char-name" placeholder="Nombre"></td>
    <td>
      <select class="char-gender">
        <option value="Femenino">Femenino</option>
        <option value="Masculino">Masculino</option>
        <option value="Desconocido">Desconocido</option>
      </select>
    </td>
    <td><input type="text" class="char-pers" placeholder="Rol y estilo de habla"></td>
    <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">🗑️</button></td>
  `;
  tbody.appendChild(tr);
}

function addGlossaryRow() {
  const tbody = document.getElementById('prof-glossary-tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="gloss-key" placeholder="Término en Inglés"></td>
    <td><input type="text" class="gloss-val" placeholder="Traducción Español Latino"></td>
    <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">🗑️</button></td>
  `;
  tbody.appendChild(tr);
}

function toggleProfileMode() {
  const vis = document.getElementById('profile-visual-container');
  const jsn = document.getElementById('profile-json-container');
  const btn = document.getElementById('btn-toggle-profile-mode');

  if (profileMode === 'visual') {
    // Sincronizar de visual a JSON
    collectProfileFromVisual();
    document.getElementById('prof-json-raw').value = JSON.stringify(currentProfileData, null, 2);
    vis.style.display = 'none';
    jsn.style.display = 'block';
    btn.innerText = '🎨 Modo Guiado';
    profileMode = 'json';
  } else {
    // Sincronizar de JSON a visual
    try {
      currentProfileData = JSON.parse(document.getElementById('prof-json-raw').value);
      renderProfileForm(currentProfileData);
      jsn.style.display = 'none';
      vis.style.display = 'block';
      btn.innerText = '💻 Modo JSON';
      profileMode = 'visual';
    } catch (e) {
      alert('Error en formato JSON: ' + e.message);
    }
  }
}

function collectProfileFromVisual() {
  const seriesTitle = document.getElementById('prof-series-title').value.trim();
  const officialTitle = document.getElementById('prof-official-title').value.trim();
  const setting = document.getElementById('prof-setting').value.trim();
  const tone = document.getElementById('prof-tone').value.trim();

  const characters = [];
  document.querySelectorAll('#prof-characters-tbody tr').forEach(tr => {
    const name = tr.querySelector('.char-name').value.trim();
    const gender = tr.querySelector('.char-gender').value;
    const pers = tr.querySelector('.char-pers').value.trim();
    if (name) characters.push({name, gender, personality: pers});
  });

  const glossary = {};
  document.querySelectorAll('#prof-glossary-tbody tr').forEach(tr => {
    const k = tr.querySelector('.gloss-key').value.trim();
    const v = tr.querySelector('.gloss-val').value.trim();
    if (k) glossary[k] = v;
  });

  const episodes = [];
  document.querySelectorAll('#prof-episodes-container .episode-context-card').forEach(card => {
    const epName = card.querySelector('.ep-name').value.trim();
    const epFile = card.querySelector('.ep-file').value.trim();
    const epDate = card.querySelector('.ep-date').innerText.trim();
    const epSummary = card.querySelector('.ep-summary').value.trim();
    if (epName || epSummary) {
      episodes.push({
        episode: epName || 'Capítulo',
        file_name: epFile,
        summary: epSummary,
        updated_at: epDate || new Date().toISOString().replace('T', ' ').substring(0, 16)
      });
    }
  });

  currentProfileData = {
    series_title: seriesTitle,
    official_title: officialTitle,
    setting: setting,
    characters: characters,
    glossary: glossary,
    tone_instructions: tone,
    episodes: episodes
  };
}

async function saveCurrentProfile() {
  if (profileMode === 'visual') {
    collectProfileFromVisual();
  } else {
    try {
      currentProfileData = JSON.parse(document.getElementById('prof-json-raw').value);
    } catch (e) {
      alert('Error de sintaxis en JSON: ' + e.message);
      return;
    }
  }

  const name = currentProfileData.series_title;
  if (!name) {
    alert('Por favor especifica un título para el perfil.');
    return;
  }

  const res = await fetch('/api/profile/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, profile: currentProfileData})
  });
  const data = await res.json();
  if (data.status === 'ok') {
    showToast(`¡Perfil "${name}" guardado exitosamente!`);
    loadProfilesCatalog();
  } else {
    alert('Error guardando perfil');
  }
}

async function deleteCurrentProfile() {
  const sel = document.getElementById('profile-selector');
  const name = sel.value;
  if (!name) return;
  if (confirm(`¿Estás seguro de eliminar el perfil "${name}"?`)) {
    await fetch('/api/profile/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    showToast(`Perfil "${name}" eliminado`);
    loadProfilesCatalog();
    startNewProfile();
  }
}

function startNewProfile() {
  document.getElementById('profile-selector').value = '';
  currentProfileData = {
    series_title: 'Nueva Serie',
    official_title: '',
    setting: '',
    characters: [],
    glossary: {},
    tone_instructions: 'Español latino neutro.'
  };
  renderProfileForm(currentProfileData);
}

async function promptGenerateProfile() {
  const title = prompt('Ingresa el nombre del Anime o Película a consultar en Internet (Jikan/MyAnimeList):');
  if (!title) return;
  showToast('Generando perfil con IA y lore web...');
  const res = await fetch('/api/refresh_profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({series: title})
  });
  const data = await res.json();
  if (data.status === 'ok') {
    showToast('¡Perfil generado exitosamente!');
    await loadProfilesCatalog();
    document.getElementById('profile-selector').value = title;
    onProfileSelected();
  }
}

// === TAB 4: CONFIGURACIÓN ===
function renderLibrariesList(libs) {
  const container = document.getElementById('cfg-libraries-list');
  if (!container) return;
  container.innerHTML = '';
  if (!libs || libs.length === 0) {
    libs = [
      {name: 'Anime', path: '/data/tvshows'},
      {name: 'Películas', path: '/data/movies'}
    ];
  }
  libs.forEach(l => {
    addLibraryRow(l.name || '', l.path || '');
  });
}

function addLibraryRow(name = '', path = '') {
  const container = document.getElementById('cfg-libraries-list');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'library-row';
  div.style.cssText = 'display:flex; gap:10px; align-items:center; background:rgba(255,255,255,0.03); padding:8px 10px; border-radius:6px; border:1px solid var(--border);';
  div.innerHTML = `
    <div style="flex:1; max-width:220px;">
      <input type="text" class="lib-name-input" placeholder="Nombre (Ej: Anime)" value="${name.replace(/"/g, '&quot;')}" style="width:100%; font-weight:600;">
    </div>
    <div style="flex:2;">
      <input type="text" class="lib-path-input" placeholder="Ruta en servidor (Ej: /data/tvshows)" value="${path.replace(/"/g, '&quot;')}" style="width:100%;">
    </div>
    <button type="button" class="btn btn-danger btn-sm" onclick="removeLibraryRow(this)" title="Eliminar esta biblioteca" style="padding:6px 10px;">🗑️</button>
  `;
  container.appendChild(div);
}

function removeLibraryRow(btn) {
  const row = btn.closest('.library-row');
  const allRows = document.querySelectorAll('.library-row');
  if (allRows.length <= 1) {
    alert('Debes mantener al menos una biblioteca configurada.');
    return;
  }
  if (row) row.remove();
}

function loadSettingsForm() {
  document.getElementById('cfg-ollama-ip').value = currentConfig.ollama_ip || '';
  document.getElementById('cfg-ollama-port').value = currentConfig.ollama_port || 11434;
  document.getElementById('cfg-gemini-key').value = currentConfig.gemini_api_key || '';
  document.getElementById('cfg-cloud-model').value = currentConfig.cloud_model || 'gemini-3.5-flash-lite';
  document.getElementById('cfg-or-key').value = currentConfig.openrouter_api_key || '';
  document.getElementById('cfg-enable-or').checked = currentConfig.enable_openrouter === true;

  const srcPrio = currentConfig.source_priority || ['ja', 'en', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'zh'];
  document.getElementById('cfg-source-priority').value = Array.isArray(srcPrio) ? srcPrio.join(', ') : srcPrio;

  const targetLangs = currentConfig.target_languages || ['es-419'];
  document.querySelectorAll('.cfg-target-chk').forEach(chk => {
    chk.checked = targetLangs.includes(chk.value);
  });

  let libs = currentConfig.libraries;
  if (!libs || !Array.isArray(libs) || libs.length === 0) {
    const rawPaths = currentConfig.watch_paths || ['/data/tvshows', '/data/movies'];
    libs = [];
    rawPaths.forEach(p => {
      if (typeof p === 'string' && p.includes(';')) {
        const parts = p.split(';');
        libs.push({path: parts[0].trim(), name: parts[1].trim()});
      } else if (typeof p === 'object' && p.path) {
        libs.push(p);
      } else {
        const sPath = String(p).trim();
        const sName = sPath.toLowerCase().includes('movie') ? 'Películas' : 'Anime';
        libs.push({path: sPath, name: sName});
      }
    });
  }
  renderLibrariesList(libs);

  loadOllamaModels();
}

async function loadOllamaModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    const models = data.models || ['qwen3.5:9b', 'qwen3:14b', 'qwen3:8b'];
    const sel = document.getElementById('cfg-local-model');
    sel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
    if (currentConfig.local_model) {
      sel.value = currentConfig.local_model;
    }
  } catch (err) {
    console.warn(err);
  }
}

async function saveAllSettings() {
  const libRows = document.querySelectorAll('.library-row');
  const libraries = [];
  const watchPaths = [];
  libRows.forEach(r => {
    const n = r.querySelector('.lib-name-input').value.trim();
    const p = r.querySelector('.lib-path-input').value.trim();
    if (p) {
      const finalName = n || (p.toLowerCase().includes('movie') ? 'Películas' : 'Series');
      libraries.push({name: finalName, path: p});
      watchPaths.push(`${p};${finalName}`);
    }
  });

  const sourcePriorityRaw = document.getElementById('cfg-source-priority').value;
  const sourcePriority = sourcePriorityRaw.split(',').map(s => s.trim().toLowerCase()).filter(s => s.length > 0);
  const targetLanguages = Array.from(document.querySelectorAll('.cfg-target-chk:checked')).map(c => c.value);

  const payload = {
    ollama_ip: document.getElementById('cfg-ollama-ip').value.trim(),
    ollama_port: parseInt(document.getElementById('cfg-ollama-port').value) || 11434,
    local_model: document.getElementById('cfg-local-model').value,
    gemini_api_key: document.getElementById('cfg-gemini-key').value.trim(),
    cloud_model: document.getElementById('cfg-cloud-model').value,
    openrouter_api_key: document.getElementById('cfg-or-key').value.trim(),
    enable_openrouter: document.getElementById('cfg-enable-or').checked,
    libraries: libraries,
    watch_paths: watchPaths,
    source_priority: sourcePriority.length > 0 ? sourcePriority : ['ja', 'en', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'zh'],
    target_languages: targetLanguages.length > 0 ? targetLanguages : ['es-419']
  };

  const res = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.status === 'ok') {
    currentConfig = data.config;
    showToast('¡Configuración guardada y aplicada!');
    loadMediaCatalog();
  }
}

// Inicialización
window.addEventListener('DOMContentLoaded', () => {
  pollStatus();
  setInterval(pollStatus, 3000);
  loadMediaCatalog();
  loadProfilesCatalog();
});
</script>
</body>
</html>
"""

class AutoSubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        accept = self.headers.get("Accept", "")
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        if clean_path == "/api/status" or "application/json" in accept or "?format=json" in self.path:
            status_data = build_status_dict()
            self.send_json(status_data)
            return

        if clean_path == "/api/history":
            records = load_history()
            self.send_json({"history": records})
            return

        if clean_path == "/api/profiles":
            profiles = []
            if os.path.exists(PROFILES_DIR):
                for fn in sorted(os.listdir(PROFILES_DIR)):
                    if fn.endswith(".json"):
                        p_name = fn[:-5]
                        f_path = os.path.join(PROFILES_DIR, fn)
                        try:
                            with open(f_path, "r", encoding="utf-8") as f:
                                p_data = json.load(f)
                                profiles.append({
                                    "name": p_name,
                                    "official_title": p_data.get("official_title") or p_name,
                                    "characters_count": len(p_data.get("characters", [])),
                                    "glossary_count": len(p_data.get("glossary", {})),
                                    "modified": datetime.datetime.fromtimestamp(os.path.getmtime(f_path)).strftime("%Y-%m-%d %H:%M")
                                })
                        except Exception:
                            profiles.append({"name": p_name, "official_title": p_name, "characters_count": 0, "glossary_count": 0})
            self.send_json({"profiles": profiles})
            return

        if clean_path == "/api/profile":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("name", [""])[0].strip()
            safe_name = os.path.basename(name).replace(".json", "")
            p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
            if os.path.exists(p_path):
                with open(p_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "name": safe_name, "profile": data}, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "not_found", "name": safe_name}, ensure_ascii=False).encode("utf-8"))
            return

        if clean_path == "/api/models":
            models = get_ollama_models()
            self.send_json({"models": models})
            return

        if clean_path.startswith("/api/series_list"):
            catalog = []
            for w_dir, cat_name in get_parsed_watch_paths():
                if os.path.exists(w_dir):
                    for entry in sorted(os.listdir(w_dir)):
                        full_p = os.path.join(w_dir, entry)
                        if os.path.isdir(full_p) and not entry.startswith("."):
                            catalog.append({
                                "name": entry,
                                "category": cat_name,
                                "path": full_p
                            })
                        elif os.path.isfile(full_p) and entry.lower().endswith((".mkv", ".mp4")):
                            catalog.append({
                                "name": entry,
                                "category": cat_name,
                                "path": full_p,
                                "is_single_file": True
                            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"catalog": catalog, "total": len(catalog)}, ensure_ascii=False).encode("utf-8"))
            return

        if clean_path.startswith("/api/series_files"):
            params = urllib.parse.parse_qs(parsed.query)
            target_path = params.get("path", [""])[0].strip()
            series_name = params.get("series", [""])[0].strip()

            files_list = []
            if not target_path and series_name:
                for w_dir, _ in get_parsed_watch_paths():
                    cand = os.path.join(w_dir, series_name)
                    if os.path.exists(cand):
                        target_path = cand
                        break

            if target_path and os.path.exists(target_path):
                if os.path.isfile(target_path):
                    base_no_ext, _ = os.path.splitext(target_path)
                    has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es.srt", ".es-419.srt", ".es.ass"])
                    try:
                        sz_mb = round(os.path.getsize(target_path) / (1024 * 1024), 1)
                    except Exception:
                        sz_mb = 0
                    files_list.append({
                        "name": os.path.basename(target_path),
                        "rel_path": os.path.basename(target_path),
                        "path": target_path,
                        "has_sub": has_sub,
                        "size_mb": sz_mb
                    })
                else:
                    for root, _, files in os.walk(target_path):
                        for f in sorted(files):
                            if f.lower().endswith((".mkv", ".mp4")):
                                f_full = os.path.join(root, f)
                                base_no_ext, _ = os.path.splitext(f_full)
                                has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es.srt", ".es-419.srt", ".es.ass"])
                                rel_path = os.path.relpath(f_full, target_path)
                                try:
                                    sz_mb = round(os.path.getsize(f_full) / (1024 * 1024), 1)
                                except Exception:
                                    sz_mb = 0
                                files_list.append({
                                    "name": f,
                                    "rel_path": rel_path,
                                    "path": f_full,
                                    "has_sub": has_sub,
                                    "size_mb": sz_mb
                                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"files": files_list}, ensure_ascii=False).encode("utf-8"))
            return

        html_bytes = HTML_DASHBOARD.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        clean_path = urllib.parse.urlparse(self.path).path

        if clean_path == "/api/cancel":
            global cancel_current_flag
            with cancel_lock:
                cancel_current_flag = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"cancelling"}')
            return

        if clean_path == "/api/cancel_queue_item":
            path_to_remove = payload.get("path", "").strip()
            if path_to_remove:
                with queue_lock:
                    if path_to_remove in retry_queue:
                        del retry_queue[path_to_remove]
                        save_persisted_queue()
                with job_queue.mutex:
                    job_queue.queue = collections.deque(item for item in job_queue.queue if item[0] != path_to_remove)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/history/clear":
            save_history([])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/config":
            with config_lock:
                for k in ["local_only", "local_model", "cloud_model", "enable_openrouter", "batch_size",
                          "fast_mode", "proofread", "ollama_ip", "ollama_port", "gemini_api_key",
                          "openrouter_api_key", "watch_paths", "libraries", "source_priority", "target_languages"]:
                    if k in payload:
                        app_config[k] = payload[k]
                save_config(app_config)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "config": app_config}).encode("utf-8"))
            return

        if clean_path == "/api/profile/save":
            name = payload.get("name", "").strip()
            p_data = payload.get("profile", {})
            if name and isinstance(p_data, dict):
                safe_name = os.path.basename(name).replace(".json", "")
                p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
                with open(p_path, "w", encoding="utf-8") as f:
                    json.dump(p_data, f, ensure_ascii=False, indent=2)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "name": safe_name}).encode("utf-8"))
                return
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"error","message":"Nombre o datos invalidos"}')
            return

        if clean_path == "/api/profile/delete":
            name = payload.get("name", "").strip()
            if name:
                safe_name = os.path.basename(name).replace(".json", "")
                p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
                if os.path.exists(p_path):
                    try:
                        os.remove(p_path)
                    except Exception:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/refresh_profile":
            series = payload.get("series", "").strip()
            if series:
                prof, p_path = get_or_create_series_profile(series, force_refresh=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "path": p_path,
                    "characters_count": len(prof.get("characters", [])),
                    "glossary_count": len(prof.get("glossary", {}))
                }, ensure_ascii=False).encode("utf-8"))
                return

        if clean_path == "/api/batch_enqueue":
            paths = payload.get("paths", [])
            force = bool(payload.get("force", True))
            context_only = bool(payload.get("context_only", False))
            target_languages = payload.get("target_languages")
            if not target_languages or not isinstance(target_languages, list):
                target_languages = None
            enqueued = 0
            if isinstance(paths, list):
                for p in paths:
                    p_clean = str(p).strip()
                    if p_clean and os.path.exists(p_clean):
                        job_queue.put((p_clean, force, context_only, target_languages))
                        enqueued += 1
            mode_lbl = "Solo Contexto" if context_only else f"Traducción {target_languages or app_config.get('target_languages', ['es-419'])}"
            print(f"[AutoSub] Encolados masivamente {enqueued} archivos ({mode_lbl}, force={force}).", flush=True)
            self.send_json({"status": "ok", "enqueued_count": enqueued, "context_only": context_only})
            return

        event_type = payload.get("eventType")
        if event_type == "Test":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","message":"AutoSub v3.5 operativo"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"received"}')

        force = payload.get("force", False)

        if event_type in ["Download", "Upgrade", "EpisodeFileImport", "FileImport"]:
            file_path = payload.get("episodeFile", {}).get("path")
            if file_path:
                job_queue.put((file_path, force))

        elif "path" in payload:
            path = payload["path"]
            def enqueue_path(p, f):
                try:
                    if os.path.isdir(p):
                        for root, _, files in os.walk(p):
                            for file in sorted(files):
                                if file.lower().endswith((".mkv", ".mp4")):
                                    job_queue.put((os.path.join(root, file), f))
                    else:
                        job_queue.put((p, f))
                except Exception as ex:
                    print(f"[AutoSub] Error encolando path '{p}': {ex}", flush=True)
            threading.Thread(target=enqueue_path, args=(path, force), daemon=True).start()

if __name__ == "__main__":
    print(f"=== AutoSub Pure AI v3.5 (Control Center + Historial + Editor Perfiles) - Puerto {PORT} ===", flush=True)
    threading.Thread(target=queue_worker, daemon=True).start()
    threading.Thread(target=retry_worker, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AutoSubHandler)
    server.serve_forever()
