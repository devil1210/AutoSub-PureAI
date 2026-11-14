# Architectural Audit Report: AutoSub PureAI v4.0 (Zoom-Out & Simplicity Review)

**Target Document**: `docs/designs/autosub_v4_egm.md`  
**Evaluator**: Skeptical Architect (`egm-zoom-out`)  
**Standard**: Zoom-Out Architectural Standard & TRIZ Contradiction Analysis  

---

## 1. Executive Summary & Altitude Assessment

The design document at `docs/designs/autosub_v4_egm.md` takes an essential and much-needed step: decomposing the 3,827-line monolithic script `autosub.py` into modular packages (`autosub/core`, `autosub/subtitles`, `autosub/providers`, `autosub/engine`, `autosub/web`) and extracting over 1,600 lines of hardcoded HTML/CSS/JS strings into proper static assets.

At the high system level, the separation of **Macro-Canon (The Elephant)** from **Stateless Micro-Translation (The Goldfish)** addresses real problems of LLM context degradation and amnesia across long episodes.

However, a rigorous architectural "zoom out" reveals **critical over-engineering and latent bottlenecks** in the execution layer:
1. **API Quota & Latency Explosion**: Chunks of 20–35 lines combined with a mandatory synchronous "Blind Critic" LLM pass increase LLM API calls by 4× to 8× per episode. On free-tier Gemini (10 RPM limit, 500 req/day), this reduces daily capacity from ~60 episodes to ~13, while imposing minutes of rate-limit idle sleep per video.
2. **Deterministic Tasks Delegated to Non-Deterministic LLMs**: The Blind Critic is tasked with checking reading speed (CPS) and ASS styling tag integrity. Both are trivial, deterministic mathematical and regex operations in Python (0 ms, 0 API cost).
3. **Speculative Character-Briefing on Speaker-Less Subtitles (YAGNI)**: The Elephant promises a dynamic Scene Brief containing *only* active characters and their "emotional register" for each 25-line slice. But MKV/SRT subtitles typically lack speaker annotations, making pre-translation speaker isolation impossible without additional speculative LLM pre-passes.
4. **Omission of Battle-Tested Web Enrichment**: The design omits the existing Jikan Moe (MyAnimeList) and Wikipedia metadata scrapers (`fetch_anime_web_context`) that currently bootstrap series profiles.

---

## 2. TRIZ Contradiction Evaluation (Inventive Catalyst)

| Contradiction | Opposing Demands | Evaluation of Proposed Design | Pragmatic TRIZ Resolution |
| :--- | :--- | :--- | :--- |
| **1. Canon Depth vs. Context Window** | Needs rich series memory vs. avoiding prompt pollution and line omission. | **Mixed**. Decoupling macro-memory into the Elephant is strong (TRIZ Principle 2: Separation). However, dynamically extracting per-scene emotional registers without speaker metadata is speculative. | **TRIZ Principle 10 (Prior Action)**: Inject the concise series cast (~150 tokens) + matched glossary terms + prior 2 lines directly. Casts in anime are small enough to fit completely without dynamic filtering. |
| **2. Translation Quality vs. API Quota & Latency** | High naturalness & CPS compliance vs. free-tier rate limits (10 RPM, 500 req/day). | **Violated (Blocker)**. Doubling LLM calls via a mandatory per-batch Blind Critic exhausts quotas and multiplies latency by 8×. | **TRIZ Principle 23 (Feedback) & 19 (Periodic/Conditional Action)**: Run a deterministic Python filter first (CPS & tag parsing in 0 ms). Only trigger LLM proofreading if deterministic thresholds fail or as an optional batch post-pass. |
| **3. Modern Web UI vs. Zero Dependencies** | Clean decoupled dashboard vs. 100% Python Standard Library constraint. | **Unresolved (Open Question 1)**. Flirts with `starlette`/`uvicorn`, violating the project's zero-dependency mandate in `requirements.txt`. | **TRIZ Principle 2 (Taking Out)**: Retain standard library `ThreadingHTTPServer` serving extracted static files with REST polling. Zero new pip dependencies required. |

---

## 3. Specific Findings

### 🔴 Blocker (Fundamental Contradictions & Architectural Pitfalls)

#### Finding B-1: Latency & Quota Explosion from Mandatory Synchronous Blind Critic LLM Pass
* **Affected Scope**: Section 2 (Diagram), Section 3.B (Goldfish Subsystem), Section Detailed Implementation Plan (Items 11 & 12).
* **Simplicity & Architectural Rationale**:
  * In `autosub.py`, the current batch size is 100 lines. The proposal reduces this to 20–35 lines (average ~25 lines).
  * An average episode contains 450 lines $\rightarrow$ 18 batches.
  * With a mandatory sequential `Translator -> Blind Critic` pipeline, an episode requires **36 distinct LLM round-trips**.
  * At Gemini Free Tier's 10 RPM quota guard, 36 calls force **3.6 minutes of pure rate-limiting sleep** per episode. On a daily ceiling of 500 requests, a user can only process ~13 episodes per day.
  * On local Ollama (`qwen3.5:9b` taking ~12s per call), 36 calls = **432 seconds (7.2 minutes)** per episode.
  * **The Fallacy**: The Blind Critic is tasked with:
    1. *CPS Calculation*: `len(dialogue) / duration` is elementary arithmetic. Using an LLM to calculate reading speed is an extreme anti-pattern.
    2. *Tag Integrity*: Checking if `{\pos}`, `{\fad}`, `\N` and braces `{}` are balanced is a deterministic 5-line regex/bracket validation in Python.
* **Impact**: System throughput degrades by 400% to 800% for marginal grammatical polish.

#### Finding B-2: Speculative "Scene Briefing" on Speaker-Less Subtitles (YAGNI & Unfulfillable Contract)
* **Affected Scope**: Section 3.A (The Elephant), Section Detailed Implementation Plan (Item 10).
* **Simplicity & Architectural Rationale**:
  * Section 3.A states: *"the Elephant computes a deterministic, token-budgeted Scene Brief (≤ 350 tokens) containing only the characters present in that specific line range, their current emotional register..."*
  * Standard subtitle tracks (SRT completely, ASS in >90% of anime releases) **do not include character speaker names** in cue events (the `Actor` field is almost universally blank or "Default").
  * Without speaker tags, the Elephant cannot deterministically identify which characters are speaking or their emotional register without running an extra LLM inference step or fuzzy keyword matching.
  * In anime/TV profiles, the entire character roster in `.profiles/<series>.json` consists of 5 to 10 characters. At ~15 tokens per character, the **entire series character card is only ~150 tokens**.
  * Attempting to dynamically filter 150 tokens down to 70 tokens adds massive architectural complexity for zero token savings.
* **Impact**: Introduces an unworkable interface contract and premature optimization.

---

### 🟡 Major (Complexity, Boundary Leaks & Missed Reuse)

#### Finding M-1: Omission of External Web Context Enrichment (`fetch_anime_web_context`)
* **Affected Scope**: Section 1 (Component Boundaries), Section 3.A, Section Detailed Implementation Plan (Item 10).
* **Simplicity & Architectural Rationale**:
  * Current `autosub.py:523-596` contains battle-tested logic querying the Jikan Moe (MyAnimeList) API and Wikipedia API to extract official titles, synopses, genres, and the top 12 characters when a series is first encountered.
  * The v4.0 design completely omits this capability from the package structure.
  * If dropped, series profile generation is severely degraded. If buried inside `autosub/engine/elephant.py`, it couples long-term narrative canon management with external third-party HTTP web scraping.
* **Recommendation**: Create a dedicated module (`autosub/subtitles/metadata.py` or `autosub/core/metadata.py`) to isolate web scraping from cognitive translation memory.

#### Finding M-2: Undefined Concurrency & State Management Pattern
* **Affected Scope**: Section 1, Section Detailed Implementation Plan (Items 1, 13, 14, 15, 19).
* **Simplicity & Architectural Rationale**:
  * The document identifies "Monolithic entanglement... global mutable state (`QUEUE`, `CONFIG`, `CURRENT_TASK`)" as Problem #1, but the Technical Plan fails to specify how state is coordinated across the decomposed modules.
  * Will `autosub/web/server.py` reach into `autosub/core/queue.py` via module-level globals? How does `autosub/engine/pipeline.py` report real-time progress to the web server?
  * Without an explicit coordination model (e.g., an `AppContext` or `EngineCoordinator` instance passed via dependency injection), the refactoring will merely scatter mutable global variables across five files instead of one.
* **Recommendation**: Define a clean, centralized `AppContext` or runtime coordinator holding thread-safe references to configuration, queue, rate limiter, and telemetry.

#### Finding M-3: Flirting with Heavy Web Frameworks vs. Zero-Dependency Standard
* **Affected Scope**: Open Question 1, Section 3.C, Non-Goals 1.
* **Simplicity & Architectural Rationale**:
  * `requirements.txt` enforces: *"100% Python Standard Library (Python 3.10+) - No external pip packages required"*.
  * Open Question 1 considers adopting `starlette`/`uvicorn` for ASGI/SSE.
  * AutoSub is an appliance running in headless Docker/Linux/Windows homelabs. Python's built-in `http.server.ThreadingHTTPServer` already handles static asset delivery and REST JSON endpoints cleanly. Introducing an ASGI runtime breaks the project's zero-dependency philosophy for zero end-user gain.
* **Recommendation**: Close Open Question 1 definitively: preserve `ThreadingHTTPServer` and reject ASGI dependencies.

---

### 🟢 Nit (Naming & Minor Simplifications)

#### Finding N-1: Speculative `relationship_graph` Data Model
* **Affected Scope**: Section 3.A (Elephant Data Model).
* **Simplicity & Architectural Rationale**:
  * Modeling an $N \times N$ directed interpersonal relationship graph (*Marcille $\rightarrow$ Laios: familiar/annoyed*) is over-engineering. Spanish dialogue adaptation only requires knowing character gender (for adjective agreement) and address register (tú vs. usted), which is already handled by the `personality` string.
* **Recommendation**: Drop `relationship_graph` from the JSON schema.

#### Finding N-2: Document Inconsistency on Token Budgeting
* **Affected Scope**: Open Question 2 vs. Non-Functional Requirements (Section 1).
* **Simplicity & Architectural Rationale**:
  * Open Question 2 asks what token ceiling to allocate for local models, while NFR 1 already explicitly states: *"Scene Brief token ceiling must never exceed 350 tokens"*.
* **Recommendation**: Remove Open Question 2 as it is already answered by NFR 1.

#### Finding N-3: Dev-Only Test Dependencies
* **Affected Scope**: Section Detailed Implementation Plan (Items 21–23), Section Verification Plan.
* **Simplicity & Architectural Rationale**:
  * Running `pytest` requires a third-party package, which must not pollute the production zero-dependency contract.
* **Recommendation**: Explicitly specify a `requirements-dev.txt` for development testing.

---

## 4. Concrete Recommendations

1. **Re-architect the Blind Critic into a 2-Tier Validation Pipeline**:
   * **Tier 1 (Instant / Deterministic)**: Run a Python check on every batch (0 ms, 0 tokens) asserting:
     * 1:1 line count invariant.
     * Tag preservation and bracket balance (`{...}`).
     * Calculated CPS $\le 20$.
   * **Tier 2 (Conditional LLM Proofreading)**: Only dispatch lines to the LLM Critic if Tier 1 flags a CPS violation or tag corruption, OR if the user explicitly enabled full-episode proofreading via configuration.
2. **Pragmatic Elephant Briefing**:
   * Abandon dynamic speaker extraction for subtitle lines lacking actor metadata.
   * Provide the full character cast (~150 tokens) + matched glossary terms + previous 2 dialogue lines directly to the Goldfish prompt.
3. **Isolate Web Enrichment**:
   * Create `autosub/subtitles/metadata.py` containing the Jikan Moe and Wikipedia lookup functions from `autosub.py:523-596`.
4. **Reaffirm Zero-Dependency Mandate**:
   * Update Open Question 1 to confirm that `http.server.ThreadingHTTPServer` will be retained with static asset serving and JSON REST endpoints.

---
*Report submitted by Skeptical Architect (`egm-zoom-out`).*
