import queue
import threading

# Thread-safe synchronization primitives
config_lock = threading.RLock()
history_lock = threading.RLock()
queue_lock = threading.RLock()
processing_lock = threading.RLock()
cancel_lock = threading.RLock()
next_gemini_lock = threading.Lock()
gemini_rate_lock = threading.Lock()

# Global work queues
job_queue = queue.Queue()
retry_queue = {}

# Active processing metrics and telemetry
currently_processing = None
current_file_start_time = 0.0
current_batch_num = 0
current_total_batches = 0
total_processing_time_sec = 0.0
completed_jobs_count = 0
cancel_current_flag = False
next_gemini_allowed = 0.0
last_job_metrics = {}

# Provider status caches
_ollama_cache = {"ts": 0.0, "online": False}
_ollama_models_cache = {"ts": 0.0, "models": []}
gemini_request_timestamps = []
