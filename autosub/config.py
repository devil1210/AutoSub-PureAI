import json
import os
import threading

def _load_env_file():
    for env_path in [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        ".env"
    ]:
        if os.path.isfile(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
                break
            except Exception:
                pass

_load_env_file()

PORT = int(os.getenv("PORT", 5055))

# Configuración y Persistencia (Portable en Linux, Windows y Docker)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_default_data_dir = "/opt" if (os.path.isdir("/opt") and os.access("/opt", os.W_OK)) else os.path.join(_project_root, "data")
DATA_DIR = os.getenv("AUTOSUB_DATA_DIR", _default_data_dir)
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "autosub_config.json")
QUEUE_FILE = os.path.join(DATA_DIR, "autosub_queue.json")
HISTORY_FILE = os.path.join(DATA_DIR, "autosub_history.json")

_default_profiles = "/data/tvshows/.profiles" if os.path.isdir("/data/tvshows") else os.path.join(DATA_DIR, "profiles")
PROFILES_DIR = os.getenv("AUTOSUB_PROFILES_DIR", _default_profiles)
os.makedirs(PROFILES_DIR, exist_ok=True)

CACHE_DIR = os.path.join(DATA_DIR, ".autosub_tmp")
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "local_only": False,
    "local_model": "qwen3.5:9b",
    "cloud_model": "gemini-3.5-flash-lite",
    "elephant_model": "gemini-4-hydrogen",
    "diarization_model": "gemini-3.8-flash",
    "enable_openrouter": False,
    "batch_size": 100,
    "fast_mode": False,
    "proofread": True,
    "audit_existing": True,
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
    "target_languages": ["es-419"],
    "curator_whitelist": ["Nia Liston - The Merciless Maiden"],
    "audio_spotcheck_enabled": True,
    "audio_spotcheck_model": "gemini-3.5-flash-lite",
    "audio_spotcheck_max_per_episode": 20,
    "sonarr_url": os.getenv("SONARR_URL", "http://192.168.1.131:8989"),
    "sonarr_api_key": os.getenv("SONARR_API_KEY", "91528f758b7f42be9b10852ee230bf41")
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
    "es-419": ["es-419", "es", "spa", "spanish", "lat", "latin", "latam", "spanish[lat]"],
    "pt-br": ["pt-br", "pt", "por", "portuguese"],
    "it": ["it", "ita", "italian"],
    "pt": ["pt", "por", "portuguese", "pt-br"],
    "ru": ["ru", "rus", "russian"],
    "zh": ["zh", "chi", "zho", "chinese"],
    "zh-cn": ["zh-cn", "zh", "chi", "zho", "chinese"],
}

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

ELEPHANT_MODELS = [
    "gemini-4-hydrogen",        # 20 RPD / 5 RPM - Máxima inteligencia para Continuidad y Perfil de Serie
    "gemini-3.8-flash",         # Respaldo alta gama
    "gemini-3.6-flash",         # Respaldo alta gama
    "gemini-3.5-flash",         # Respaldo alta fidelidad
    "gemini-3.5-flash-lite",    # Respaldo ultra-rápido (500 RPD)
]

DIARIZATION_MODELS = [
    "gemini-3.8-flash",         # 20 RPD / 5 RPM - Flash estructurado para Diarización
    "gemini-3.6-flash",         # 20 RPD / 5 RPM - Respaldo alta fidelidad
    "gemini-3.5-flash-lite",    # 500 RPD / 15 RPM - Respaldo de alta velocidad y 500 RPD
    "gemini-3.1-flash-lite",    # 500 RPD / 15 RPM - Respaldo de alta cuota
]

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",    # 500 RPD / 15 RPM - Rápido, alta cuota para The Goldfish
    "gemini-3.1-flash-lite",    # 500 RPD / 15 RPM - Segundo respaldo de alta cuota (nunca consume modelos de 20 RPD)
    "gemini-3.8-flash",         # Respaldo alta fidelidad
    "gemini-3.6-flash",         # Respaldo
]

AUDIO_PROBE_MODELS = [
    "gemini-3.5-flash-lite",    # 500 RPD / 15 RPM - Multimodal audio nativo ultra-rápido para verificación quirúrgica
    "gemini-3.1-flash-lite",    # 500 RPD / 15 RPM - Respaldo de alta cuota
    "gemini-3.8-flash",         # Respaldo
    "gemini-3.6-flash",         # Respaldo
]

MAX_GEMINI_RPM = 10
MIN_GEMINI_INTERVAL = 4.0

config_lock = threading.RLock()

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
                return {**DEFAULT_CONFIG, **c}
        except Exception as e:
            print(f"[Warn] Error leyendo config: {e}", flush=True)
    return dict(DEFAULT_CONFIG)

app_config = load_config()

def save_config(new_conf):
    global app_config
    with config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(new_conf, f, indent=2, ensure_ascii=False)
            app_config = {**DEFAULT_CONFIG, **new_conf}
        except Exception as e:
            print(f"[Warn] No se pudo guardar config: {e}", flush=True)

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

def get_gemini_api_keys() -> list[str]:
    raw = app_config.get("gemini_api_keys") or app_config.get("gemini_api_key", DEFAULT_CONFIG.get("gemini_api_key", ""))
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()]
    elif isinstance(raw, str):
        import re
        return [k.strip() for k in re.split(r"[,;\n]+", raw) if k.strip()]
    return []

def get_gemini_api_key():
    keys = get_gemini_api_keys()
    return keys[0] if keys else str(app_config.get("gemini_api_key", DEFAULT_CONFIG["gemini_api_key"])).strip()

def get_openrouter_api_key():
    return str(app_config.get("openrouter_api_key", DEFAULT_CONFIG["openrouter_api_key"])).strip()
