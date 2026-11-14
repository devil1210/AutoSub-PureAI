# Comprehension Audit: `docs/designs/autosub_v4_egm.md`

## What This Design Does

AutoSub PureAI v4.0 is an automated subtitle translation and media synchronization daemon designed for personal media server environments (such as Plex, Jellyfin, and Emby). The tool monitors designated media folders, extracts embedded subtitle tracks from Matroska (`.mkv`) video files, and translates dialogue into target languages using artificial intelligence models (cloud-based Google Gemini, local Ollama instances, or OpenRouter). Once translated, the system formats and saves the resulting subtitle files (`.ass` or `.srt`) directly alongside the video files and presents an interactive web interface for queue management, status inspection, and configuration.

From a software architecture perspective, this design proposes refactoring a monolithic 3,800-line Python script into modular subpackages for configuration, subtitle parsing, AI provider adapters, and web serving, while extracting embedded HTML/CSS/JavaScript strings into standalone static files and templates. Functionally, it introduces the "Elephant-Goldfish Model" (EGM) to the translation pipeline: a stateful "Elephant" coordinator maintains series-wide narrative context (character profiles, relationship dynamics, and specialized terminology) and generates compact scene briefs, while stateless "Goldfish" workers translate small batches of dialogue lines in isolated contexts, followed by a secondary critic pass that checks reading speed, subtitle styling tags, and dialogue tone.

## Why This Matters

This design addresses critical technical and operational limitations inherent in large-language-model (LLM) media localization: context degradation, prompt fatigue, and unmaintainable monolithic code. When translating full-length episodes in monolithic prompts, LLMs frequently hallucinate, skip lines, or lose subtitle-to-video synchronization; conversely, isolated line-by-line translation results in context starvation, producing inconsistent character genders, tone shifts, and corrupted terminology across episodes. Decoupling the narrative memory (Elephant) from the translation execution (Goldfish) preserves 1:1 subtitle timing and series-long terminology consistency while keeping prompt sizes within the strict VRAM and context limits of local GPU models.

Operationally and technically, decomposing the legacy monolithic script into cleanly separated packages enables automated testing via `pytest` with mocked LLM and MKVToolNix adapters. Engineers can verify subtitle parsing, style preservation, and error recovery without relying on manual test runs with live video files or paid API credits. Furthermore, separating web assets allows standard browser caching and frontend linting, significantly improving developer velocity and application responsiveness.

---

## Comprehension Findings

### 🔴 Blocker

1. **Unexplained Character-to-Cue Attribution Mechanism for Scene Briefs**
   * **Affected Scope**: Section 3.A, lines 160-165
   * **Discrepancy Description & Rationale**: The document states that the Elephant compiles a concise Scene Brief ($\le 350$ tokens) containing *"only the characters present in that specific line range, their current emotional register, and any glossary terms appearing in the text."* However, standard subtitle formats (SRT files, and the vast majority of ASS files where the `Actor`/`Name` field is blank) do not specify which character is speaking which line. The document never defines how the engine determines speaker identity from raw dialogue cues. Does it assume the presence of populated ASS `Actor` fields, require an unstated diarization pre-pass by an LLM, or employ heuristic text parsing? Without clarifying how speaker attribution occurs, the core mechanism of the Elephant cannot be understood or implemented.

2. **Ambiguous Critic Execution Model, Latency/Cost Impact, and Control Flow**
   * **Affected Scope**: Section 2 (lines 137-143), Section 3.B (lines 172-179), and Section Performance & Latency (lines 210-219)
   * **Discrepancy Description & Rationale**: 
     - *Nature of the Critic*: It is unclear whether the "Goldfish Blind Critic" is an automated programmatic validation script (e.g., Python regex and math for CPS and tag balancing) or a separate LLM prompt invocation. If it is an LLM call, every 20–35 line batch requires two sequential LLM completions (Translator + Critic), doubling API latency, token consumption, and operational cost. This is not factored into the $\le 3.5\text{s}$ latency target or technical specifications.
     - *Control Flow & Authority*: Line 175 states that the Critic *"flags or reformulates dialogue"*. If the Critic reformulates dialogue directly, does it bypass the Translator, and how does it guarantee line-count fidelity? If it flags errors, what is the feedback loop? The workflow diagram depicts a linear pipeline (`Translator --> Critic --> Serializer`) with no rejection arrow or retry loop back to the Translator.

3. **Undefined Origin and Lifecycle of Series Profiles ("Pre-Analysis")**
   * **Affected Scope**: Section 3.A (lines 160-165), CUJ 1 (lines 50-52), and Implementation Plan Item 10 (lines 266-269)
   * **Discrepancy Description & Rationale**: The Elephant's memory is powered by a `series_bible`, `relationship_graph`, `glossary`, and `episode_narrative` which are noted as *"extracted during pre-analysis"* and loaded from `.profiles/<series>.json`. However, the document never describes what "pre-analysis" is, when or how it is triggered, what component performs it, or whether profiles are generated automatically by an LLM, fetched from an external metadata database, or authored manually by the user. If a new series arrives in CUJ 1 without an existing profile, the document provides no explanation of how the system initializes the series profile.

---

### 🟡 Major

4. **Undefined Domain Acronyms and Technical Jargon**
   * **Affected Scope**: Throughout document (Lines 11, 32, 43, 49, 93, 191, 198, 240)
   * **Discrepancy Description & Rationale**: The document assumes deep prior knowledge of video containers, subtitle authoring, and web architecture without defining terms or providing reference links:
     - **ASS** (Advanced SubStation Alpha) and **SRT** (SubRip Subtitle): Mentioned throughout as formats, including internal ASS syntax structures (`[Script Info]`, `[V4+ Styles]`, `[Events]`, `{\pos}`, `{\fad}`, `\N`), without explaining what they are or what the headers configure.
     - **CPS**: Introduced as an acronym in Goal 3 (line 32) and the diagram (line 137), but only defined as "Characters Per Second" over 100 lines later in Section 3.B.
     - **RPM**: Used in lines 93 and 264 ("Token/RPM quota manager") without clarifying that it means "Requests Per Minute".
     - **MKV / Demuxing**: References "MKV containers" and "multi-track MKV demuxing" without defining Matroska video containers or demuxing.
     - **es-419**: Hardcoded across CUJs and configuration defaults (lines 49, 148, 232) without explaining that it denotes the BCP 47 / UN M.49 standard for Latin American Spanish.
     - **Ecosystem Acronyms**: ASGI, SPA, RBAC, JWT, VRAM, and LOC are introduced in lines 39, 43, 73, 191, and 198 without definition.

5. **Contradictory Real-time Telemetry Mechanism (WebSocket vs. SSE vs. Polling)**
   * **Affected Scope**: Open Question 1 (line 43), CUJ 2 (line 56), Section 3.C (line 184), and Implementation Items 17 & 19 (lines 298, 306)
   * **Discrepancy Description & Rationale**: The document presents conflicting descriptions of the live telemetry architecture:
     - Open Question 1 debates migrating to ASGI (`starlette`/`uvicorn`) specifically to support SSE (Server-Sent Events) live log streaming.
     - CUJ 2 states metrics are delivered via "API polling/SSE".
     - Section 3.C states `app.js` handles "WebSocket/SSE telemetry" (introducing WebSockets, which are fundamentally incompatible with standard library `http.server.ThreadingHTTPServer`).
     - Implementation Items 17 and 19 specify "real-time status polling" and simple REST endpoints (`/api/status`).
     The document must resolve whether the system uses HTTP short-polling, SSE, or WebSockets, and ensure the chosen transport is compatible with the selected web server architecture.

6. **Unstated External Prerequisites & Tooling Discovery**
   * **Affected Scope**: Section The Problem (line 21), Non-Goal 2 (line 38), and Implementation Item 2 (line 235)
   * **Discrepancy Description & Rationale**: The design relies strictly on MKVToolNix CLI binaries (`mkvmerge` and `mkvextract`). However, it never specifies prerequisites for host installation: does it require MKVToolNix on the system `PATH`, accept explicit binary paths in `config.py`, or bundle them? It also omits startup validation steps to alert the user if these external tools are missing or incompatible.

---

### 🟢 Nit

7. **Profile File Location Discrepancy**
   * **Affected Scope**: Section 2 Diagram (line 126) vs. Implementation Item 10 (line 268)
   * **Discrepancy Description & Rationale**: Line 126 references `.profiles/<series>.json & series.json`, whereas line 268 only references `.profiles/<series>.json`. Clarify whether `series.json` is a legacy global fallback or an active configuration file.

8. **Test Suite File Inventory Discrepancy**
   * **Affected Scope**: Section 1 Package Tree (line 111) vs. Section Detailed Implementation Plan (lines 316-323)
   * **Discrepancy Description & Rationale**: The directory tree lists `tests/test_egm_pipeline.py`, but the Implementation Plan items define `tests/test_elephant_memory.py` and `tests/test_goldfish_translator.py`, leaving `test_egm_pipeline.py` unlisted in the planned implementation files.

9. **Ambiguity Between "Cues", "Events", and "Lines"**
   * **Affected Scope**: Lines 121, 135, 136, 169, 170
   * **Discrepancy Description & Rationale**: The document interchanges "cues", "dialogue events", and "lines". In ASS and SRT subtitle files, a single timed cue/event often spans multiple physical lines separated by `\N`. The document should explicitly clarify whether the "20–35" batch slicing and "100% Line Count Invariant" measure timed dialogue events or newline-delimited strings.
