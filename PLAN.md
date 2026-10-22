# 8x8 AI Class Assessor — Development Plan & Phases

## Overview

A **Streamlit** web application that acts as a Senior QA Evaluator for 8x8 online children's education classes. It accepts a class recording (via file upload or URL), sends it to **Google Gemini** for AI-powered evaluation, and renders a professional assessment dashboard — all with zero data retention (ephemeral file handling).

---

## Tech Stack

| Layer | Technology |
|---|---|
| UI Framework | Streamlit |
| AI Model | Google Gemini 1.5 Flash (`gemini-1.5-flash`) |
| AI SDK | `google-generativeai` (`genai.Client`) |
| Data Validation | Pydantic (structured JSON schema) |
| Config / Secrets | `python-dotenv` + `.env` file |
| Video Ingest | `requests` + `tempfile` (ephemeral) |
| Logging | Python `logging` → `log.md` |
| Cleanup | `os.remove()` + `client.files.delete()` |

---

## Application Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit UI                         │
│  ┌──────────────┐   ┌──────────────────────────────┐   │
│  │   Tab 1      │   │   Tab 2                      │   │
│  │ File Upload  │   │ Video URL → requests download│   │
│  │ (MP4/MOV)   │   │                              │   │
│  └──────┬───────┘   └──────────────┬───────────────┘   │
│         └────────────────┬─────────┘                   │
│                          ▼                              │
│              tempfile (ephemeral storage)               │
│                          │                              │
│                          ▼                              │
│              client.files.upload() → Gemini API         │
│                          │                              │
│                          ▼                              │
│            generate_content (gemini-1.5-flash)         │
│            + System Prompt + Pydantic schema            │
│                          │                              │
│                          ▼                              │
│              Structured JSON Assessment                  │
│                          │                              │
│    ┌─────────────────────┼──────────────────────┐      │
│    │  st.metric (grade)  │  st.checkbox (checks) │      │
│    │  st.info (feedback) │  st.dataframe (log)   │      │
│    └─────────────────────┴──────────────────────┘      │
│                                                         │
│              finally: os.remove + files.delete          │
└─────────────────────────────────────────────────────────┘
```

---

## Pydantic Output Schema

The AI response will be enforced to match this schema:

```python
class KeyObservation(BaseModel):
    timestamp: str        # e.g. "[02:15]"
    rubric_item: str      # e.g. "Individualized Engagement"
    observation: str      # specific, quoted evidence

class Assessment(BaseModel):
    grade: Literal["Excellent", "Good", "Average", "Poor"]
    visual_technical_compliance: bool
    class_structure_flow: bool
    individualized_engagement: bool
    demeanor_energy: bool
    actionable_feedback: str   # 2-3 sentence improvement plan
    key_observations: list[KeyObservation]
```

---

## Evaluation Rubric (8x8)

| # | Criterion | Description |
|---|---|---|
| 1 | Visual & Technical Compliance | Official 8x8 background, well-lit, camera always on |
| 2 | Class Structure & Flow | Clear intro, rapport built BEFORE teaching, homework assigned at end |
| 3 | Individualized Engagement | Calls on students individually by name |
| 4 | Demeanor & Energy | Highly energetic, pleasant, visibly happy |

**Grades:** `Excellent` → `Good` → `Average` → `Poor`

---

## Development Phases

---

### Phase 1 — Project Setup & Configuration
**Goal:** Establish the project skeleton, dependencies, and environment.

#### Tasks
- [ ] Create `requirements.txt` with all dependencies
- [ ] Create `.env.example` with `GEMINI_API_KEY=your_key_here`
- [ ] Create `app.py` skeleton with imports
- [ ] Configure `logging` to write timestamped entries to `log.md`
- [ ] Load `.env` with `python-dotenv` and initialize `genai.Client`
- [ ] Verify `client` initializes correctly with a smoke test

#### Files
```
Reco_Class/
├── app.py
├── requirements.txt
├── .env              ← gitignored
├── .env.example      ← committed
└── log.md            ← auto-generated at runtime
```

#### Dependencies (`requirements.txt`)
```
streamlit
google-generativeai
python-dotenv
requests
pydantic
```

---

### Phase 2 — Pydantic Schema & System Prompt
**Goal:** Define the structured output contract and the AI evaluation prompt.

#### Tasks
- [ ] Define `KeyObservation` Pydantic model
- [ ] Define `Assessment` Pydantic model with all rubric fields
- [ ] Write the System Prompt string (from APP.md) as a Python constant
- [ ] Validate the schema can be serialized to JSON for Gemini's `response_schema`

#### Key Design Decision
The system prompt encapsulates:
1. The AI's **role** (Senior QA Evaluator)
2. **Observation writing rules** (quote teachers, describe actions with timestamps)
3. The **rubric** (4 criteria)
4. The **grading anchors** (Excellent / Good / Average / Poor)

---

### Phase 3 — Video Ingestion & Ephemeral Storage
**Goal:** Accept video via upload or URL, store temporarily, clean up after use.

#### Tasks
- [ ] Build Streamlit UI header (`8x8 AI Class Assessor`)
- [ ] Create two-tab layout: **Upload** and **URL**
- [ ] Tab 1: `st.file_uploader` restricted to `["mp4", "mov"]`
- [ ] Tab 2: `st.text_input` for URL + `requests.get()` download with streaming
- [ ] Save both to `tempfile.NamedTemporaryFile(delete=False)` 
- [ ] Log: start of upload/download process
- [ ] Upload file to Gemini via `client.files.upload(path, ...)`
- [ ] Log: success when Gemini accepts the file
- [ ] Handle errors gracefully with `st.error()` messages

#### Flow
```
User Input → tempfile → client.files.upload() → Gemini File Object
```

---

### Phase 4 — AI Inference
**Goal:** Call Gemini with the video and system prompt, receive structured JSON.

#### Tasks
- [ ] Log start of inference API call
- [ ] Call `client.models.generate_content()` with:
  - Model: `gemini-1.5-flash`
  - Contents: `[uploaded_file, system_prompt]`
  - Config: `temperature=0.1`, `response_mime_type="application/json"`, `response_schema=Assessment`
- [ ] Parse response JSON into `Assessment` Pydantic object
- [ ] Log successful receipt of assessment
- [ ] Log any exceptions with full traceback
- [ ] Store result in `st.session_state["assessment"]`

---

### Phase 5 — Output Dashboard UI
**Goal:** Render the assessment beautifully as a professional audit report.

#### UI Components

| Data | Component |
|---|---|
| `grade` | `st.metric` OR styled HTML badge with color coding |
| `visual_technical_compliance` | Disabled `st.checkbox` |
| `class_structure_flow` | Disabled `st.checkbox` |
| `individualized_engagement` | Disabled `st.checkbox` |
| `demeanor_energy` | Disabled `st.checkbox` |
| `actionable_feedback` | `st.info()` box |
| `key_observations` | `st.dataframe()` or `st.table()` (timestamp, rubric item, observation) |

#### Grade Color Coding
```
Excellent → 🟢 Green
Good      → 🔵 Blue
Average   → 🟡 Yellow
Poor      → 🔴 Red
```

#### Tasks
- [ ] Build grade display with color-coded badge using `st.markdown` + HTML
- [ ] Render 4 boolean checkboxes (disabled) in a 2×2 grid
- [ ] Render `actionable_feedback` in `st.info()`
- [ ] Render `key_observations` as a sortable `st.dataframe()`
- [ ] Use `st.session_state` to persist results across page interactions

---

### Phase 6 — Cleanup & Production Hardening
**Goal:** Ensure zero data retention and robust error handling.

#### Tasks
- [ ] Wrap all file-handling and inference logic in `try / finally`
- [ ] In `finally` block:
  - `os.remove(temp_file_path)` — delete local temp file
  - `client.files.delete(name=video_file.name)` — delete from Gemini
  - Log both successful deletions
- [ ] Handle cases where file never reached Gemini (skip remote delete)
- [ ] Add user-facing spinner: `st.spinner("Analyzing video...")`
- [ ] Test with a missing/invalid API key scenario
- [ ] Test with an invalid URL scenario
- [ ] Test with an unsupported file type scenario

---

### Phase 7 — Logging & Observability
**Goal:** Create a readable `log.md` audit trail for every session.

#### Log Format
```
2026-10-22 12:00:01 | INFO  | [UPLOAD] Starting video download from URL: https://...
2026-10-22 12:00:04 | INFO  | [UPLOAD] File accepted by Gemini. File name: files/abc123
2026-10-22 12:00:04 | INFO  | [INFERENCE] Calling gemini-1.5-flash...
2026-10-22 12:01:12 | INFO  | [INFERENCE] Assessment received. Grade: Good
2026-10-22 12:01:12 | INFO  | [CLEANUP] Deleted local temp file: /tmp/xyz.mp4
2026-10-22 12:01:13 | INFO  | [CLEANUP] Deleted Gemini file: files/abc123
2026-10-22 12:01:13 | ERROR | [INFERENCE] Exception: ...traceback...
```

#### Tasks
- [ ] Configure `logging.basicConfig` with file handler → `log.md`
- [ ] Add named logger tags: `[UPLOAD]`, `[INFERENCE]`, `[CLEANUP]`
- [ ] Ensure all `except` blocks log with `logging.exception()`

---

## File Deliverables

| File | Purpose |
|---|---|
| `app.py` | Complete Streamlit application |
| `requirements.txt` | Python dependencies |
| `.env.example` | API key template |
| `log.md` | Auto-generated runtime log (gitignored) |

---

## Risk & Edge Cases

| Risk | Mitigation |
|---|---|
| Gemini upload failure | `try/except` with `st.error()`, skip remote cleanup |
| URL is not a video file | Validate Content-Type header before saving |
| Large video (>100MB) | Stream `requests.get()` in chunks |
| API key missing | Check env var on startup, show clear error |
| Session reset wipes results | Persist in `st.session_state` |
| Children's privacy | Prompt instructs AI to use `Student A`, etc. |

---

## Implementation Order

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
Setup      Schema    Ingest    AI Call   UI Output  Cleanup   Logging
```

> All phases build on the previous. Phases 6 & 7 should be woven into Phase 3–5 as each feature is built, not left to the end.
