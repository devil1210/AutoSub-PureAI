---
name: autosub-qa
description: Subtitle quality assurance, contextual narrative consistency audit, dialect harmonization, and track selection runbook for AutoSub AI.
---

# AutoSub AI Quality Assurance & Contextual Polishing (AutoSub-QA)

This skill provides guidelines, best practices, and troubleshooting procedures for auditing, harmonizing, and ensuring the quality of subtitles processed by **AutoSub AI**.

---

## 1. Quality Assurance Philosophy: Hybrid Human-AI Pipeline

AutoSub follows a **Preserve & Correct** philosophy when dealing with subtitles:

1. **Human Localization Value:** Official or reputable fansub tracks (like Crunchyroll, Netflix, Amazon or Judas releases) often contain cultural adaptations and otaku/gamer terminology (*"eroge"*, *"mal tercio"*, *"el prota"*) that should **never be scrubbed or over-literalized**.
2. **AI Consistency Superpower:** Human translations often suffer from inter-episode divergence (e.g. Episode 1 translates a place name one way, Episode 4 translates it differently; or translators switch between Spain and Latin American Spanish). The AI acts as a **Senior Editorial Supervisor**, ensuring:
   - Consistency with the persistent Series Narrative Profile (`/data/tvshows/.profiles/{series_name}.json`).
   - Character gender agreement in adjectives and pronouns.
   - Mandatory Glossary enforcement.
   - Dialect harmonization to `es-419` (Latin American Neutral).

---

## 2. Track Selection Rules

To avoid truncating subtitles to signs/songs:

| Track Condition | Status | Action |
| :--- | :--- | :--- |
| `forced_track == True` or `forced == True` | ❌ Rejected as dialogue | Skip; only contains on-screen text or songs. |
| Track name contains `signs`, `songs`, `carteles`, `forzados` | ❌ Rejected as dialogue | Skip. |
| Target language matches `sub_tracks` (non-forced) | 🟢 Preferred for QA | Extract directly and run AI Contextual Audit. |
| Target language not in container | 🟡 Translate from source | Select best source track from `source_priority` (non-forced). |

---

## 3. Dialect Harmonization Rules (Castilian -> Latin American Neutral)

When auditing an existing track in `es` (Spain) targeted for `es-419`:
- Pronouns & verbs: *vosotros* $\to$ *ustedes* (e.g., *vosotros tenéis* $\to$ *ustedes tienen*).
- Vocabulary:
  - *ordenador* $\to$ *computadora* / *computador*
  - *vale* $\to$ *de acuerdo* / *está bien*
  - *tío* / *tía* (colloquial) $\to$ *amigo* / *chico* / *chica* / *oye*
  - *gilipollas* $\to$ *idiota* / *estúpido*
  - *coche* $\to$ *auto* / *carro*
  - *aparcar* $\to$ *estacionar*

---

## 4. Manual Verification & Comparison Runbook

To compare existing embedded tracks with generated subtitles:

```bash
# 1. Inspect all subtitle tracks with IDs, codecs, languages, and forced flags
mkvmerge -J "video.mkv" | jq '.tracks[] | select(.type=="subtitles")'

# 2. Extract specific tracks for side-by-side inspection
mkvextract tracks "video.mkv" 3:/tmp/source.ass 10:/tmp/target_official.ass

# 3. Trigger manual batch re-processing with force=True
curl -X POST http://192.168.1.125:5055/api/batch_enqueue \
  -H "Content-Type: application/json" \
  -d '{"paths": ["/data/tvshows/Series/S01E03.mkv"], "force": true, "target_languages": ["es-419"]}'
```
