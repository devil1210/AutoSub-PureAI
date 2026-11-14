# EGM Design Hostile Critic Review: `docs/designs/autosub_v4_egm.md` (Lines 1–221)

**Reviewer:** Senior Software Engineer & Hostile System Critic (Goldfish Model)  
**Evaluated Target:** `docs/designs/autosub_v4_egm.md` (Lines 1–221)  
**Status:** Action Required (Multiple Architectural Blockers & Failure Modes Identified)

---

## Executive Summary

The proposed modularization and Elephant-Goldfish Model (EGM) provide a compelling conceptual vision for subtitle translation. However, lines 1–221 present significant architectural omissions, unhandled race conditions, ungrounded cognitive assumptions, and brittle parsing pipelines. Without remediation, the system will face data corruption under web concurrency, endless rejection loops in the Critic pass, timing misalignment from LLM output fences, and crashes when processing standard media formats (e.g., bitmap/PGS subtitles).

---

## 🔴 Blocker Findings (Production Crashes, Race Conditions & Data Loss)

### 1. Unsynchronized Multi-Threaded State & Unsafe File Persistence
* **Affected Scope:** `autosub/core/queue.py`, `autosub/web/server.py`, `autosub/providers/rate_limiter.py`, and Lines 217–218.
* **Flaw Description:**
  1. The design proposes replacing monolithic globals with dedicated modules (`queue.py`, `server.py`), while keeping multi-threaded HTTP servicing (`ThreadingHTTPServer` or concurrent endpoints) and background workers. The specification mandates that `queue.json` be persisted on "every state transition".
  2. If an HTTP request mutates queue order or cancels a job (`/api/queue`) while the background worker updates task progress, concurrent uncoordinated writes to `queue.json` will occur. On Windows, this raises immediate `PermissionError` (sharing violation). On Linux/POSIX, non-atomic partial file writes corrupt the JSON payload, rendering the queue unreadable on the next reboot.
  3. `rate_limiter.py` has no defined thread-safe primitives (mutex/locks). If multiple batch workers or provider fallbacks query token/RPM quotas simultaneously, race conditions on timestamp/token counters will trigger burst violations and provider bans.
* **Remediation:**
  * Implement an explicit in-process mutex (`threading.RLock`) protecting all queue operations.
  * Guarantee disk persistence atomicity by writing updates to a temporary sibling file (`queue.json.tmp`) and executing an atomic replacement (`os.replace`) within an exclusive file lock.
  * Require `rate_limiter.py` to use atomic sliding-window token bucket algorithms backed by thread locks.

### 2. Blind Faith in LLM ASS Tag Preservation & Undefined Wire Format
* **Affected Scope:** `autosub/engine/goldfish.py`, `autosub/subtitles/parser.py`, Lines 168–171, Line 216.
* **Flaw Description:**
  1. Relying on LLM prompt adherence to reproduce complex ASS syntax byte-for-byte is fundamentally broken. LLMs frequently escape backslashes (`\\N`), lower-case hex color values, strip braces, translate style names, or drop tags entirely.
  2. The wire protocol between Python and the Goldfish LLM is completely undefined. LLMs routinely wrap batch outputs in markdown code fences or prepend conversational preambles. If raw text is returned, any blank dialogue line or mismatched line break permanently breaks cue indexing, shifting all subsequent lines out of synchronization with the video audio.
* **Remediation:**
  * **AST Tag Masking/Re-injection**: The subtitle parser must tokenize and strip ASS override tags *before* sending dialogue to the LLM (e.g., replacing `{\pos(100,200)}Hello` with `<T0>Hello`). After translation, a deterministic regex/AST injector restores `<T0>` to its exact original byte string. The LLM must never be allowed to touch or regenerate raw ASS syntax.
  * **Strict Structured Wire Schema**: Force structured JSON output (e.g., using Gemini Structured Outputs / Pydantic schema: `{"lines": [{"id": int, "text": str}]}`). Validate that `len(lines) == len(input_cues)` and that all IDs strictly match before passing to the next stage. Strip all markdown code fences defensively prior to JSON deserialization.

### 3. Critic Failure Mode: Infinite Loops, Feedback Absence, and Unvalidated Reformulation
* **Affected Scope:** `autosub/engine/goldfish.py`, `autosub/engine/pipeline.py`, Lines 137–144.
* **Flaw Description:**
  1. **Missing Feedback Edge & Deadlock:** The Mermaid diagram depicts a linear pipeline: `Translator --> Critic --> Serializer`. However, Section B states the Critic audits CPS, Naturalness, Formality, and Tag Integrity. If a batch is rejected, what happens? There is no return edge to the Translator, no error-correction prompt specification, and no maximum retry ceiling.
  2. If the Critic rejects a batch because rapid speech naturally exceeds 20 CPS (common in anime/action dialogue), a naive retry loop will trigger an **infinite retry storm**, burning LLM quotas, stalling the queue worker, and blocking downstream episodes.
  3. **Unvalidated Critic Mutation:** Line 173 states the Critic *"Flags or reformulates dialogue exceeding 20 CPS"*. If the Critic directly rewrites dialogue, the Critic itself is an LLM subject to hallucination, line dropping, and tag corruption! Who verifies the Critic's output? The 100% line count invariant is only specified for the Translator.
* **Remediation:**
  * Introduce an explicit State Machine for the Goldfish phase with a strict circuit breaker: `Max_Critic_Retries = 2`.
  * If the Critic reformulates dialogue, the output MUST pass through the exact same programmatic validation filter (1:1 line count, tag mask integrity, non-empty validation) as the Translator output.
  * If a batch fails Critic validation twice, the pipeline must log an advisory warning and gracefully fall back to the initial Translator pass rather than halting the pipeline.

### 4. Non-Text Subtitle Tracks & Demuxing Crash Vulnerabilities
* **Affected Scope:** `autosub/subtitles/mkv.py`, `autosub/subtitles/parser.py`, Lines 118–123.
* **Flaw Description:**
  1. In standard media servers (Plex/Jellyfin), a large percentage of MKV files contain **bitmap/image subtitles** (HDMV PGS / `subrip` bitmap / VobSub). `mkvextract` dumps PGS streams as binary `.sup` files.
  2. Ingesting a `.sup` file will trigger `UnicodeDecodeError` or unhandled parse crashes.
  3. No failure state, track validation logic, or dead-letter queue is defined for corrupted containers, unsupported codecs, or missing text subtitle streams.
* **Remediation:**
  * In `mkv.py`, parse `mkvmerge -J` track metadata and enforce track compatibility filters: only accept `codec_id` in `["S_TEXT/ASS", "S_TEXT/SSA", "S_TEXT/UTF8"]`.
  * If only bitmap (`S_IMAGE/PGS`) tracks or no subtitle tracks are detected, immediately flag the job as `UNSUPPORTED_TRACK_FORMAT`, write a diagnostic event to `history.json`, and bypass text extraction without crashing the worker.

---

## 🟡 Major Findings (Unmitigated Failure Modes, Scaling & Consistency)

### 5. Elephant Context Desynchronization Across Scene Cuts & Speaker Ambiguity
* **Affected Scope:** `autosub/engine/elephant.py`, Lines 158–166.
* **Remediation:** Acknowledge speaker uncertainty explicitly: if `cue.speaker` is absent, the Elephant emits a neutral register-safe context. Avoid assuming fine-grained dynamic emotional registers per line.

### 6. The Pre-Analysis Paradox: VRAM Contradiction & Offline Infeasibility
* **Affected Scope:** `autosub/engine/elephant.py`, Lines 164, 196–199.
* **Remediation:** For offline/local setups, pre-analysis must be purely deterministic (dictionary term scanning, glossary extraction, character name regex frequency).

### 7. Syntactic Fracturing from Arbitrary Batch Slicing (20–35 Cues)
* **Affected Scope:** `autosub/engine/goldfish.py`, Lines 135, 168.
* **Remediation:** Implement **Smart Boundary Slicing**: When slicing batches between cue 20 and 35, inspect time deltas (`end_time` of Cue $i$ vs `start_time` of Cue $i+1$) and punctuation (`.`, `!`, `?`, `。`).

### 8. Token Budget Ceiling Overflow for Ensemble Scenes
* **Affected Scope:** `autosub/engine/elephant.py`, Lines 165, 212.
* **Remediation:** Implement an explicit Priority Truncation Algorithm in `elephant.py` (Glossary matches > Active speaker > Tone/synopsis).

### 9. Intra-Episode Provider Fallback Leading to Lexical & Stylistic Drift
* **Affected Scope:** `autosub/providers/rate_limiter.py`, Line 218.
* **Remediation:** Fallback strategy must operate at the **Episode / Job level**, not the single-batch level.

### 10. Romance Language (es-419) Text Expansion vs. Hard 20 CPS Constraint
* **Affected Scope:** `autosub/engine/goldfish.py`, Line 174.
* **Remediation:** Calculate CPS with duration weighting: Apply 20 CPS strictly to cues $> 1.5$ seconds. For cues $< 1.5$ seconds, permit up to 25 CPS.

---

## 🟢 Nit Findings (Precision & Operational Polish)

### 11. Lack of Intermediate Batch Checkpointing
* **Remediation:** Persist partial batch outputs in `.cache/batches/<job_id>/` until final file assembly.

### 12. Open Question 1 Indecision Regarding Web Server Protocol
* **Remediation:** Officially retain `ThreadingHTTPServer` to protect zero-dependency footprint.

### 13. Absence of Corrupt Media Quarantine State
* **Remediation:** Add file stability check (file size constant across 2 intervals) to prevent picking up half-written files.
