# Technical Lead & Implementation Readiness Review: AutoSub v4.0 (Lines 222–352)

**Target Document**: `docs/designs/autosub_v4_egm.md` (Lines 222–352: Detailed Implementation Plan & Verification Plan)  
**Evaluator**: Implementation Readiness Goldfish (Pragmatic Execution & Tech Lead Audit)  
**Status**: ⚠️ **REVISION REQUIRED (Implementation Blockers Present)**

---

### Executive Summary

Lines 222–352 outline a well-structured architectural decomposition across 23 target files. However, an engineer attempting to write code immediately from this specification **cannot proceed without making arbitrary architectural and design decisions**. 

Critical domain schemas (`SubtitleCue`, `ParsedSubtitle`, `SeriesProfile`) are omitted, public function signatures lack return types and exception models, and conflicting file path conventions (`settings.json` vs. existing `autosub_config.json`; `queue.json` vs. `autosub_queue.json`) introduce a severe risk of data loss. Furthermore, the plan contains zero backward-compatibility or schema migration logic for existing production deployments (`.env`, `autosub_config.json`, and `.profiles/*.json`).

---

## 🔴 Blocker Findings (Missing Architectural Contracts & Unresolved Decisions)

### 1. Missing Dataclass Contracts for Core Subtitle & Cue Models
* **Affected Scope**: Target Files 3, 4, 10, 11 (`autosub/subtitles/parser.py`, `serializer.py`, `autosub/engine/elephant.py`, `goldfish.py`)
* **Finding**: The subtitle parser, serializer, Elephant context engine, and Goldfish batch translator all pass subtitle data among themselves. However, the plan defines no data structures for cues, header templates, or parsed streams.
* **Remediation**: Define the canonical dataclasses in `autosub/subtitles/models.py`:
  ```python
  @dataclass
  class SubtitleCue:
      index: int
      start_time: str
      end_time: str
      style: str
      speaker: str
      raw_line: str
      raw_text: str
      clean_text: str
      duration_sec: float
      is_translatable: bool

  @dataclass
  class ParsedSubtitle:
      format: str
      headers: list[str]
      cues: list[SubtitleCue]
  ```

### 2. Profile Schema Divergence & Storage Contract Discrepancy
* **Affected Scope**: Target File 10 (`autosub/engine/elephant.py`)
* **Finding**: Ambiguity between `.profiles/<series>.json` and `series.json`, and unhandled schema divergence from existing v3 profiles.
* **Remediation**: Eliminate `series.json` reference; standardize on `.profiles/<clean_series_name>.json` with backward-compatible dataclass default values.

### 3. Blind Critic Execution Model & CPS Correction Contract Undefined
* **Affected Scope**: Target File 11 (`autosub/engine/goldfish.py`)
* **Remediation**: Specify a two-tier hybrid critic model in `goldfish.py`:
  - **Tier 1 (Programmatic)**: Validate line counts (1:1 invariant), tag bracket balance (`{` / `}`), and compute exact CPS (`len(clean_text) / duration_sec`).
  - **Tier 2 (Conditional LLM)**: Only if `config.proofread` is True AND (CPS > 20 or styling tag corruption detected), dispatch a concise correction prompt for the offending line indices.

### 4. Scene Brief Token Budget Enforcement & Truncation Hierarchy
* **Affected Scope**: Target File 10 (`autosub/engine/elephant.py`)
* **Remediation**: Token estimation formula: `len(brief_text) // 4`. Strict deterministic truncation hierarchy:
  1. Active characters
  2. Matched glossary terms
  3. Direct dialogue dynamics
  4. Episode synopsis (dropped first).

---

## 🟡 Major Findings (Unspecified Signatures, Schema Ambiguity & Omissions)

### 5. Critical Persistence File Path Collisions & Missing Migration Handler
* **Affected Scope**: Target Files 1, 13, 15 (`autosub/config.py`, `queue.py`, `history.py`)
* **Remediation**: Retain existing canonical filenames (`autosub_config.json`, `autosub_queue.json`, `autosub_history.json`) or add explicit startup migration helpers.

### 6. Unresolved Web Server Architecture (Open Question 1)
* **Affected Scope**: Target File 19 (`autosub/web/server.py`)
* **Remediation**: Formally resolve Open Question 1: keep `http.server.ThreadingHTTPServer` to avoid pip dependencies.

### 7. Incomplete LLM Provider Abstraction & Telemetry Contract
* **Affected Scope**: Target Files 5, 15 (`autosub/providers/base.py`, `autosub/core/history.py`)
* **Remediation**: Standardize `LLMResponse` dataclass with `text`, `prompt_tokens`, `completion_tokens`, `latency_sec`, `model_used`.

### 8. Pipeline Concurrency, Progress & Cancellation Contract Missing
* **Affected Scope**: Target Files 12, 13 (`autosub/engine/pipeline.py`, `autosub/core/queue.py`)
* **Remediation**: Define thread-safe `JobContext` with `cancel_event: threading.Event` and `progress_callback`.

### 9. Discrepancy Between Directory Tree and Target Test Suite Breakdown
* **Affected Scope**: Directory tree vs. Target Files 21–23
* **Remediation**: Standardize test suite into 5 explicit test files:
  1. `tests/test_mkv_tracks.py`
  2. `tests/test_subtitle_parser.py`
  3. `tests/test_rate_limiter.py`
  4. `tests/test_elephant_memory.py`
  5. `tests/test_goldfish_translator.py`

### 10. Non-Deterministic Verification Commands
* **Remediation**: Use `python -m compileall autosub autosub.py` (cross-platform).

---

## 🟢 Nit Findings

### 11. CLI Entry Point Parameter Specification
* Specify required CLI flags (`--port`, `--config-dir`, `--scan-now`, `--single-file <path>`, `--local-only`, `--headless`).

### 12. Dialogue Sanitization Responsibility Boundary
* Assign line break formatting to `autosub/subtitles/serializer.py:sanitize_dialogue_text()`.
