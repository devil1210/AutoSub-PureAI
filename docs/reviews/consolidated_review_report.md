# Consolidated Review Report: AutoSub PureAI v4.0 (EGM Swarm Audit)

**Document Reviewed:** [`docs/designs/autosub_v4_egm.md`](file:///e:/Descargas/AutoSub-PureAI/docs/designs/autosub_v4_egm.md)  
**Evaluators:**  
- `egm-zoom-out` (Skeptical Architect & Simplicity Auditor)  
- `egm-comprehension` (Naive Reader & Specification Clarity Auditor)  
- `egm-design` (Hostile System Critic & Concurrency Auditor)  
- `egm-implementation-readiness` (Pragmatic Tech Lead & Execution Auditor)  
**Date:** 2026-11-13  
**Status:** ⚠️ **Revisions Required Prior to Implementation (Consensus Reached)**

---

## 1. Executive Synthesis & Reviewer Consensus

The independent Goldfish review swarm unanimously affirmed that the high-level vision—decoupling the 3,800-line monolithic `autosub.py` and introducing the **Elephant-Goldfish Model** to separate macro narrative canon from micro atomic translation—is structurally sound and addresses the core problems of subtitle AI localization.

However, the reviewers identified **five critical flaws** that would cause production failures, quota exhaustion, or implementation deadlocks if code were written immediately:

1. **The Blind Critic Quota & Latency Explosion**: Running an LLM pass for every 25-line batch doubles API round-trips (up to 36 calls per episode), burning free-tier rate limits and adding minutes of idle sleep. Furthermore, reading speed (CPS) and tag balance are deterministic calculations that Python can execute in 0 ms at zero cost.
2. **Brittle ASS Tag Handling**: Expecting LLMs to preserve raw ASS override tags (`{\pos(x,y)}`, `{\fad}`) byte-for-byte is unrealistic. Tags must be masked into tokens (`<T0>`) before the LLM and reinjected deterministically.
3. **Speculative Speaker Attribution**: Subtitles rarely have speaker tags. Expecting the Elephant to dynamically divine which character speaks which cue in a 25-line window is unworkable. Supplying the full character roster (~150 tokens) directly to the prompt is simpler and completely eliminates this problem.
4. **Missing Data Models & Domain Contracts**: Core dataclasses (`SubtitleCue`, `ParsedSubtitle`, `SeriesProfile`) and thread synchronization primitives were undefined.
5. **Architectural Ambiguity**: Web server technology (Open Question 1) was left undecided, and file path collisions threatened existing production configs.

---

## 2. Consolidated Action Items by Severity

### 🔴 Blockers (Must Be Fixed in Design Before Coding)

*   **ACTION-1: Re-architect the Blind Critic into a 2-Tier Validation Engine**
    *   *Mechanism*: Tier 1 executes instantaneous, deterministic Python validation on every batch: 1:1 line count, tag bracket balance, and CPS arithmetic ($\le 20$ CPS for cues $> 1.5$s, $\le 25$ CPS for short cues). Tier 2 (LLM Proofreader) is strictly conditional: invoked only if Tier 1 detects corruption or if the user explicitly enabled full proofreading in settings.
    *   *Traceability*: Resolves Zoom-Out B-1, Critic 3, Comprehension 2, Readiness 3.
*   **ACTION-2: Implement Tag Masking & Structured Output Schemas**
    *   *Mechanism*: Subtitle parser tokenizes and masks ASS tags into inert tokens (`<T0>`, `<T1>`) before sending dialogue to the LLM. An AST injector restores original tags byte-for-byte after translation. Wire format enforces structured JSON with line IDs.
    *   *Traceability*: Resolves Critic 2.
*   **ACTION-3: Pragmatic Elephant Canon Injection (Replace Dynamic Speaker Slicing)**
    *   *Mechanism*: Drop speculative dynamic speaker detection for speaker-less subtitles. Inject the complete series character card (~150 tokens) + matched glossary terms + preceding 2 dialogue lines directly into the Goldfish prompt.
    *   *Traceability*: Resolves Zoom-Out B-2, Comprehension 1, Critic 5.
*   **ACTION-4: Define Canonical Dataclass Contracts**
    *   *Mechanism*: Specify `SubtitleCue`, `ParsedSubtitle`, `CharacterProfile`, and `SeriesProfile` dataclasses in `autosub/subtitles/models.py` with backward-compatible defaults.
    *   *Traceability*: Resolves Readiness 1, Readiness 2.
*   **ACTION-5: Concurrency Safety & Atomic File Persistence**
    *   *Mechanism*: Protect queue and state mutations with `threading.RLock`. Persist state atomically using temporary files (`queue.json.tmp`) and `os.replace` to prevent Windows file-sharing crashes.
    *   *Traceability*: Resolves Critic 1.
*   **ACTION-6: Track Codec Compatibility Filter in MKV Extractor**
    *   *Mechanism*: Inspect `mkvmerge -J` codecs. Only accept `S_TEXT/ASS`, `S_TEXT/SSA`, `S_TEXT/UTF8`. Gracefully handle bitmap subtitles (`S_IMAGE/PGS`) without crashing the daemon.
    *   *Traceability*: Resolves Critic 4.

---

### 🟡 Major Improvements (Architectural Hardening)

*   **ACTION-7: Reaffirm Zero-Dependency Footprint (Resolve Open Question 1)**
    *   *Decision*: Formally reject ASGI (`starlette`/`uvicorn`). Retain Python standard library `http.server.ThreadingHTTPServer` serving extracted static assets with JSON REST endpoints (`/api/status`, `/api/queue`).
    *   *Traceability*: Resolves Zoom-Out M-3, Comprehension 5, Critic 12, Readiness 6.
*   **ACTION-8: Preserve Web Context Enrichment Module (`fetch_anime_web_context`)**
    *   *Mechanism*: Isolate Jikan Moe and Wikipedia lookup logic from `autosub.py:523-596` into a dedicated `autosub/subtitles/metadata.py` module.
    *   *Traceability*: Resolves Zoom-Out M-1.
*   **ACTION-9: Backward-Compatible File Storage & Migration**
    *   *Mechanism*: Preserve existing filenames (`autosub_config.json`, `autosub_queue.json`, `autosub_history.json`, `.profiles/*.json`) or execute zero-loss startup migrations.
    *   *Traceability*: Resolves Readiness 5, Comprehension 7.
*   **ACTION-10: Smart Boundary Slicing for Dialogue Batches**
    *   *Mechanism*: Slice batches between cue 20 and 35 at natural dialogue pauses (time gap $> 2.0$s or terminal punctuation) to prevent mid-sentence cuts.
    *   *Traceability*: Resolves Critic 7.
*   **ACTION-11: Job-Level LLM Failover & Standardized Response Dataclass**
    *   *Mechanism*: Define `LLMResponse` dataclass. On rate limits, retry primary provider with backoff; only switch providers at the job level to prevent mid-episode style drift.
    *   *Traceability*: Resolves Critic 9, Readiness 7.
*   **ACTION-12: Harmonize Test Suite Breakdown & Verification Commands**
    *   *Mechanism*: Consolidate test plan to 5 explicit pytest modules and standardize cross-platform compilation with `python -m compileall autosub autosub.py`.
    *   *Traceability*: Resolves Zoom-Out N-3, Readiness 9, Readiness 10.

---

### 🟢 Nits & Refinements

*   **ACTION-13: Define Domain Glossary & Acronyms**: Define ASS, SRT, CPS, RPM, and es-419 in document overview. (Resolves Comprehension 4).
*   **ACTION-14: CLI Argument Specification**: Explicitly document CLI flags (`--port`, `--scan-now`, `--single-file`, `--headless`). (Resolves Readiness 11).
*   **ACTION-15: Temporary Work Directory**: Add `.autosub_tmp/batches/` for intermediate batch checkpointing. (Resolves Critic 11).

---

## 3. Traceability Matrix

| Reviewer Finding | Stage | Severity | Consolidated Action Item |
| :--- | :--- | :--- | :--- |
| **B-1: Latency & Quota Explosion from Critic** | Zoom-Out (Stage 5) | 🔴 Blocker | **ACTION-1** (2-Tier Validation) |
| **B-2: Speculative Scene Briefing without Speakers** | Zoom-Out (Stage 5) | 🔴 Blocker | **ACTION-3** (Pragmatic Canon Injection) |
| **M-1: Omission of Web Context Scrapers** | Zoom-Out (Stage 5) | 🟡 Major | **ACTION-8** (`metadata.py`) |
| **M-2: Undefined Concurrency & State Pattern** | Zoom-Out (Stage 5) | 🟡 Major | **ACTION-5** (`threading.RLock`) |
| **M-3: Flirting with ASGI vs. Zero Dependencies** | Zoom-Out (Stage 5) | 🟡 Major | **ACTION-7** (Retain `ThreadingHTTPServer`) |
| **Comprehension 1: Unexplained Speaker Attribution** | Comprehension (Stage 6) | 🔴 Blocker | **ACTION-3** (Pragmatic Canon Injection) |
| **Comprehension 2: Ambiguous Critic Model & Loop** | Comprehension (Stage 6) | 🔴 Blocker | **ACTION-1** (2-Tier Validation) |
| **Comprehension 3: Undefined Pre-Analysis Lifecycle** | Comprehension (Stage 6) | 🔴 Blocker | **ACTION-8** (`metadata.py`) |
| **Comprehension 4: Undefined Acronyms** | Comprehension (Stage 6) | 🟡 Major | **ACTION-13** (Glossary Definitions) |
| **Comprehension 5: Telemetry Mechanism Conflict** | Comprehension (Stage 6) | 🟡 Major | **ACTION-7** (Confirm REST Polling) |
| **Critic 1: Unsynchronized Multi-Thread State** | Design Critic (Stage 7) | 🔴 Blocker | **ACTION-5** (RLock & Atomic Writes) |
| **Critic 2: Blind Faith in LLM ASS Tag Preservation** | Design Critic (Stage 7) | 🔴 Blocker | **ACTION-2** (AST Tag Masking) |
| **Critic 3: Infinite Loops in Critic Feedback** | Design Critic (Stage 7) | 🔴 Blocker | **ACTION-1** (Circuit Breaker $\le 2$) |
| **Critic 4: PGS/Bitmap Subtitle Crashes** | Design Critic (Stage 7) | 🔴 Blocker | **ACTION-6** (Codec Filtering) |
| **Critic 7: Syntactic Fracturing on Batch Borders** | Design Critic (Stage 7) | 🟡 Major | **ACTION-10** (Smart Boundary Slicing) |
| **Critic 9: Intra-Episode Style Drift on Fallback** | Design Critic (Stage 7) | 🟡 Major | **ACTION-11** (Job-Level Failover) |
| **Readiness 1: Missing Core Subtitle Dataclasses** | Readiness (Stage 9) | 🔴 Blocker | **ACTION-4** (`SubtitleCue`, `ParsedSubtitle`) |
| **Readiness 2: Profile Schema Divergence** | Readiness (Stage 9) | 🔴 Blocker | **ACTION-4** (`SeriesProfile` dataclass) |
| **Readiness 3: Undefined Critic Execution Model** | Readiness (Stage 9) | 🔴 Blocker | **ACTION-1** (2-Tier Validation) |
| **Readiness 5: File Path Collisions & Loss Risk** | Readiness (Stage 9) | 🟡 Major | **ACTION-9** (Preserve File Names) |
| **Readiness 9: Test Suite Inventory Discrepancy** | Readiness (Stage 9) | 🟡 Major | **ACTION-12** (Harmonize 5 Test Files) |

---

## 4. Next Steps

With consensus achieved across all four reviewers, the next step is applying these 15 remediations directly to `docs/designs/autosub_v4_egm.md` to transition its status to **Approved**, followed immediately by initiating Phase 1 of implementation.
