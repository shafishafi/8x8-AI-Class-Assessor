# =============================================================================
# 8x8 AI Class Assessor — app.py
# Streamlit application that evaluates online children's class recordings
# using Google Gemini 1.5 Flash and the 8x8 QA Rubric.
# =============================================================================

# ── Phase 1: Imports ──────────────────────────────────────────────────────────
import json
import logging
import os
import random
import re
import tempfile
import time

import requests
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import Literal

# ── Phase 1: Environment & Logging Setup ─────────────────────────────────────

# Load API key from .env file
load_dotenv()

# Configure logging → writes to log.md with timestamps and log levels
logging.basicConfig(
    filename="log.md",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)

# Initialise Gemini client (fails loudly if key is missing)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Path to shared local quota lock file across sessions
QUOTA_LOCK_FILE = os.path.join(tempfile.gettempdir(), "gemini_quota_lock.json")


def get_gemini_api_key() -> str | None:
    """Returns active Gemini API key from environment."""
    return os.environ.get("GEMINI_API_KEY")


def get_quota_unlock_time() -> float | None:
    """Returns target unlock timestamp if quota limit is active, otherwise None."""
    # Check session state first
    if "gemini_quota_unlock_time" in st.session_state:
        t = st.session_state["gemini_quota_unlock_time"]
        if t > time.time():
            return t
        st.session_state.pop("gemini_quota_unlock_time", None)

    # Check shared disk lock file
    if os.path.exists(QUOTA_LOCK_FILE):
        try:
            with open(QUOTA_LOCK_FILE, "r") as f:
                data = json.load(f)
                unlock_time = data.get("unlock_time", 0)
                if unlock_time > time.time():
                    st.session_state["gemini_quota_unlock_time"] = unlock_time
                    return unlock_time
                else:
                    os.remove(QUOTA_LOCK_FILE)
        except Exception:
            pass
    return None


def set_quota_unlock_time(retry_seconds: float, error_msg: str = "") -> None:
    """Stores quota unlock timestamp in session state and lock file."""
    unlock_time = time.time() + retry_seconds
    st.session_state["gemini_quota_unlock_time"] = unlock_time
    st.session_state["gemini_quota_retry_seconds"] = retry_seconds
    st.session_state["gemini_quota_error_msg"] = error_msg
    try:
        with open(QUOTA_LOCK_FILE, "w") as f:
            json.dump({"unlock_time": unlock_time, "error_msg": error_msg}, f)
    except Exception:
        pass


def clear_quota_unlock_time() -> None:
    """Clears quota unlock timestamp from session state and lock file."""
    st.session_state.pop("gemini_quota_unlock_time", None)
    st.session_state.pop("gemini_quota_retry_seconds", None)
    st.session_state.pop("gemini_quota_error_msg", None)
    if os.path.exists(QUOTA_LOCK_FILE):
        try:
            os.remove(QUOTA_LOCK_FILE)
        except Exception:
            pass


class QuotaExceededError(Exception):
    """Raised when the Gemini API returns a 429 RESOURCE_EXHAUSTED daily quota limit."""
    def __init__(self, retry_seconds: float, message: str):
        super().__init__(message)
        self.retry_seconds = retry_seconds
        self.unlock_timestamp = time.time() + retry_seconds
        self.message = message


def extract_retry_delay_from_error(err_text: str) -> float:
    """Extract retry delay in seconds from Gemini 429 error messages or details."""
    m_delay = re.search(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+)s?['\"]", err_text)
    if m_delay:
        return float(m_delay.group(1))

    m_text = re.search(
        r"retry in\s+(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:([0-9\.]+)s)?",
        err_text,
        re.IGNORECASE,
    )
    if m_text:
        d = int(m_text.group(1) or 0)
        h = int(m_text.group(2) or 0)
        m = int(m_text.group(3) or 0)
        s = float(m_text.group(4) or 0)
        total = d * 86400 + h * 3600 + m * 60 + s
        if total > 0:
            return total

    return 3600.0

# ── Phase 2: Pydantic Output Schema ──────────────────────────────────────────

class KeyObservation(BaseModel):
    """A single timestamped, evidence-based observation from the recording."""
    timestamp: str        # e.g. "[02:15]"
    rubric_item: str      # one of the four 8x8 rubric criteria
    observation: str      # specific, quoted, evidence-based statement


class Assessment(BaseModel):
    """
    Structured QA assessment produced by Gemini.
    All boolean fields map directly to the 8x8 Evaluation Rubric criteria.
    """
    grade: Literal["Excellent", "Good", "Average", "Poor"]
    visual_technical_compliance: bool   # Rubric 1
    class_structure_flow: bool          # Rubric 2
    individualized_engagement: bool     # Rubric 3
    demeanor_energy: bool               # Rubric 4
    actionable_feedback: str            # 2-3 sentence improvement plan
    key_observations: list[KeyObservation]


# ── Phase 2: System Prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """
ROLE AND OBJECTIVE:
You are a Senior Quality Assurance Evaluator for 8x8 online children's education classes.
Your objective is to watch and listen to the class recording and evaluate the teacher's
instructional quality, engagement, and technical compliance.

INSTRUCTIONS & CONSTRAINTS:
1. Analyze the provided video and audio simultaneously.
2. Evaluate the teacher strictly against the "8x8 Evaluation Rubric" defined below.
3. Base your observations ONLY on the visual and audio evidence in the recording. Do not invent context.
4. Maintain strict privacy: Never use the names or identifying features of the children
   in your output. Refer to them as "Student A", "Student B", etc.
5. Utilize Any Available Transcription: If the video contains embedded subtitles, closed captions,
   on-screen text, or an attached transcript, prioritize and cross-reference its verbatim speech
   to ensure teacher quotes and timestamps are 100% accurate.

OBSERVATION WRITING RULES (CRITICAL):
- Quote the teacher exactly when evaluating interaction.
- Describe exact facial expressions, posture, or tone with timestamps.
  BAD:  "The teacher had good one-to-one interaction."
  GOOD: "[02:15] The teacher demonstrated one-to-one interaction by saying,
         'Student A, what do you think?' and waiting 4 seconds."

THE 8x8 EVALUATION RUBRIC:
1. Visual & Technical Compliance: Teacher uses the official 8x8 background,
   is well-lit, and camera is always on.
2. Class Structure & Flow: Teacher gives a clear intro, builds rapport BEFORE
   teaching, and assigns homework at the end.
3. Individualized Engagement: Teacher calls on students individually by name.
4. Demeanor & Energy: Communication is highly energetic, pleasant, and visibly happy.

GRADING ANCHORS (CLASSIFICATION):
- "Excellent": Flawless execution. Good one-to-one interaction, background present,
  individual questioning, high energy, and homework assigned.
- "Good": Strong performance with minor gaps (e.g., followed structure and used
  background, but missed homework or had slightly lower energy).
- "Average": Meets technical requirements (background, individual questioning) but
  lacks energetic communication, or had minor lighting/clarity issues.
- "Poor": Skipped rapport building entirely, rushed into revision, missed homework,
  poor introduction, turned the camera off, or failed to use the 8x8 background.

Respond ONLY with valid JSON matching the provided schema. Do not add any prose outside the JSON.
"""

# ── Grade display config ───────────────────────────────────────────────────────

GRADE_CONFIG = {
    "Excellent": {"emoji": "🌟", "color": "#22c55e", "bg": "#dcfce7"},
    "Good":      {"emoji": "✅", "color": "#3b82f6", "bg": "#dbeafe"},
    "Average":   {"emoji": "⚠️",  "color": "#eab308", "bg": "#fef9c3"},
    "Poor":      {"emoji": "❌", "color": "#ef4444", "bg": "#fee2e2"},
}

# ── Streamlit Page Config ─────────────────────────────────────────────────────

st.set_page_config(
    page_title="8x8 AI Class Assessor",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  .grade-badge {
    display: inline-block;
    padding: 0.5rem 1.5rem;
    border-radius: 999px;
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    margin-bottom: 1rem;
  }
  .section-header {
    font-size: 1.1rem;
    font-weight: 700;
    color: #1e293b;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 0.4rem;
    margin: 1.5rem 0 0.8rem 0;
  }
  .stDataFrame { border-radius: 10px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Phase 5 Helper: Render Assessment Dashboard ───────────────────────────────

def render_dashboard(
    assessment: Assessment,
    key_prefix: str = "dash",
    transcript_text: str | None = None,
    transcript_source: str | None = None,
) -> None:
    """Render the complete assessment UI from a parsed Assessment object."""

    cfg = GRADE_CONFIG[assessment.grade]

    # ── Grade Badge ──────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="grade-badge" style="background:{cfg["bg"]};color:{cfg["color"]};">'
        f'{cfg["emoji"]} {assessment.grade}'
        f'</div>',
        unsafe_allow_html=True,
    )

    if transcript_text:
        source = transcript_source or "Verified"
        st.markdown(
            f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:0.6rem 1rem;margin-bottom:1rem;color:#166534;font-size:0.9rem;">'
            f'📝 <strong>Transcription Utilized ({source}):</strong> Spoken dialogue transcript was synchronized and verified for this QA evaluation.'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"📄 View Class Transcript ({source})", expanded=False):
            st.text_area(
                label="Transcript Text",
                value=transcript_text,
                height=220,
                disabled=True,
                label_visibility="collapsed",
                key=f"{key_prefix}_transcript_area",
            )
            st.download_button(
                label="💾 Download Transcript (.txt)",
                data=transcript_text,
                file_name=f"class_transcript_{key_prefix}.txt",
                mime="text/plain",
                key=f"{key_prefix}_download_transcript",
            )

    col1, col2 = st.columns(2)

    # ── Rubric Checkboxes ────────────────────────────────────────────────────
    with col1:
        st.markdown('<div class="section-header">📋 Rubric Checklist</div>', unsafe_allow_html=True)
        rubric_items = [
            ("🎥 Visual & Technical Compliance", assessment.visual_technical_compliance),
            ("📚 Class Structure & Flow",         assessment.class_structure_flow),
            ("🙋 Individualized Engagement",       assessment.individualized_engagement),
            ("⚡ Demeanor & Energy",               assessment.demeanor_energy),
        ]
        for idx, (label, value) in enumerate(rubric_items):
            st.checkbox(label, value=value, disabled=True, key=f"{key_prefix}_rubric_{idx}")

    # ── Actionable Feedback ──────────────────────────────────────────────────
    with col2:
        st.markdown('<div class="section-header">💡 Actionable Feedback</div>', unsafe_allow_html=True)
        st.info(assessment.actionable_feedback)

    # ── Key Observations Table ────────────────────────────────────────────────
    st.markdown('<div class="section-header">🔍 Key Observations (Evidence Log)</div>', unsafe_allow_html=True)

    if assessment.key_observations:
        obs_data = [
            {
                "Timestamp":   obs.timestamp,
                "Rubric Item": obs.rubric_item,
                "Observation": obs.observation,
            }
            for obs in assessment.key_observations
        ]
        st.dataframe(
            obs_data,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Timestamp":   st.column_config.TextColumn("🕐 Timestamp",   width="small"),
                "Rubric Item": st.column_config.TextColumn("📌 Rubric Item",  width="medium"),
                "Observation": st.column_config.TextColumn("📝 Observation",  width="large"),
            },
        )
    else:
        st.warning("No key observations were returned by the model.")


def extract_embedded_transcription(file_path: str) -> str | None:
    """
    Scans MP4/MOV container for embedded subtitle tracks, closed captions,
    or timed text (tx3g, wvtt, c608, c708, sbtl, text) and metadata.
    Returns extracted dialogue text if found, else None.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(25 * 1024 * 1024)  # Read up to 25MB for container atoms

            sub_markers = [b"tx3g", b"wvtt", b"c608", b"c708", b"sbtl", b"text"]
            if not any(marker in chunk for marker in sub_markers):
                return None

            pattern = re.compile(rb"[\x20-\x7E\n\r\t]{6,}")
            extracted_lines = []
            for match in pattern.finditer(chunk):
                raw = match.group()
                try:
                    text = raw.decode("utf-8", errors="ignore").strip()
                    lower = text.lower()
                    if any(bad in lower for bad in [
                        "isom", "mp42", "vide", "soun", "appl", "avc1", "mp4a",
                        "handler", "ftyp", "moov", "mdat", "stsd", "stts", "ctts",
                        "free", "wide", "uuid", "edts", "elst", "mdhd", "hdlr"
                    ]):
                        continue
                    words = text.split()
                    if len(words) >= 3 and any(c.isalpha() for c in text):
                        extracted_lines.append(text)
                except Exception:
                    continue

            if extracted_lines:
                clean_lines = []
                for line in extracted_lines:
                    if not clean_lines or clean_lines[-1] != line:
                        clean_lines.append(line)
                return "\n".join(clean_lines[:150])
    except Exception as exc:
        logger.warning(f"[TRANSCRIPT] Could not extract embedded subtitles: {exc}")
    return None


def parse_sidecar_transcription(file_bytes: bytes, filename: str) -> str | None:
    """Parses uploaded SRT, VTT, or plain text transcript files."""
    try:
        content = file_bytes.decode("utf-8", errors="ignore").strip()
        if not content:
            return None
        lines = []
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str or line_str.isdigit() or line_str.startswith("WEBVTT") or line_str.startswith("NOTE"):
                continue
            lines.append(line_str)
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"[TRANSCRIPT] Error parsing sidecar transcript '{filename}': {exc}")
        return None


def generate_content_with_backoff(
    client: genai.Client,
    model: str,
    contents: list,
    config: types.GenerateContentConfig,
    max_retries: int = 5,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0,
    max_delay: float = 32.0,
):
    """
    Calls client.models.generate_content with exponential backoff and jitter.
    Handles 503 (UNAVAILABLE / High Demand) and transient rate limits automatically.
    If daily quota is exhausted (RESOURCE_EXHAUSTED / 429 with long retryDelay), raises QuotaExceededError.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            err_msg = str(exc)

            # Check if this is a daily quota exhaustion limit
            if any(term in err_msg for term in ["RESOURCE_EXHAUSTED", "429", "Quota exceeded", "quotaMetric"]):
                retry_sec = extract_retry_delay_from_error(err_msg)
                if retry_sec > 60:
                    logger.error(
                        f"[QUOTA] Gemini daily request quota exhausted. Reset in {retry_sec}s. Aborting retries."
                    )
                    raise QuotaExceededError(retry_seconds=retry_sec, message=err_msg)

            # Check for transient error codes or phrases (e.g. temporary 503 spike)
            is_transient = any(
                code in err_msg
                for code in ["503", "UNAVAILABLE", "high demand", "overloaded"]
            )

            if not is_transient or attempt == max_retries:
                logger.error(f"[INFERENCE] Request failed permanently on attempt {attempt}/{max_retries}: {exc}")
                raise exc

            # Calculate exponential backoff with full jitter
            base_delay = min(initial_delay * (backoff_factor ** (attempt - 1)), max_delay)
            jitter = random.uniform(0.5, 1.5)
            delay = round(base_delay + jitter, 1)

            logger.warning(
                f"[RETRY] Model {model} returned transient error (attempt {attempt}/{max_retries}): {exc}. "
                f"Retrying in {delay}s..."
            )
            with st.spinner(f"⏳ Gemini is experiencing high demand (503). Retrying in {delay}s (Attempt {attempt}/{max_retries})..."):
                time.sleep(delay)


def generate_transcription_from_video(client: genai.Client, video_file) -> str | None:
    """
    Uses Gemini to generate a verbatim timestamped dialogue transcript
    from the video's audio track when none is provided by the user.
    """
    prompt = """
Listen carefully to the audio and watch the video of this online class recording.
Generate a complete, timestamped verbatim transcription of all spoken dialogue.

Format each dialogue line:
[MM:SS] Speaker (Teacher / Student A / Student B): Spoken dialogue

Rules:
1. Capture all teacher questions, explanations, student responses, and homework instructions.
2. Protect privacy: Refer to children only as "Student A", "Student B", etc. Never use real names.
3. Include timestamps at the start of each dialogue segment.
4. Output only the transcript text. Do not add introductory or concluding prose.
"""
    try:
        response = generate_content_with_backoff(
            client=client,
            model="gemini-3.8-flash",
            contents=[video_file, prompt],
            config=types.GenerateContentConfig(
                temperature=0.1,
            ),
            max_retries=3,
            initial_delay=2.0,
            backoff_factor=2.0,
        )
        if response and response.text:
            return response.text.strip()
    except QuotaExceededError:
        raise
    except Exception as exc:
        logger.warning(f"[TRANSCRIPT] Automatic transcription failed: {exc}")
    return None


def render_quota_countdown_and_button(tab_key: str, button_label: str = "🚀 Analyse Recording") -> bool:
    """
    Renders a live countdown timer when Gemini 429 quota is active,
    disables the analyse button until the timer reaches zero,
    and automatically makes the button available when the timer expires.
    """
    unlock_time = get_quota_unlock_time()
    now = time.time()

    if unlock_time is not None:
        remaining_sec = int(unlock_time - now)

        if remaining_sec <= 0:
            clear_quota_unlock_time()
            st.success("🎉 **Quota reset timer has completed!** The Analyse button is now active.")
            unlock_time = None
        else:
            h = remaining_sec // 3600
            m = (remaining_sec % 3600) // 60
            s = remaining_sec % 60
            initial_display = f"{h:02d}h : {m:02d}m : {s:02d}s"
            unlock_ms = int(unlock_time * 1000)

            st.markdown(f"""
            <div style="background:#fff1f2;border:2px solid #fecdd3;border-radius:12px;padding:1.2rem;margin:1rem 0;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
              <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.6rem;">
                <span style="font-size:1.8rem;">⏳</span>
                <div>
                  <h3 style="margin:0;color:#9f1239;font-size:1.1rem;font-weight:700;">Gemini API Quota Limit Exceeded (429)</h3>
                  <p style="margin:0.2rem 0 0 0;color:#881337;font-size:0.88rem;">
                    Google Gemini Free Tier daily limit reached (20 requests/day per model). The Analyse button is locked until the countdown timer expires.
                  </p>
                </div>
              </div>
              <div style="background:#ffffff;border:1px solid #fda4af;border-radius:8px;padding:1rem;text-align:center;margin:0.6rem 0;">
                <div style="font-size:0.8rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Quota Reset Countdown</div>
                <div id="countdown_{tab_key}" style="font-size:2.2rem;font-weight:800;color:#e11d48;font-family:monospace;margin:0.3rem 0;">
                  {initial_display}
                </div>
                <div id="status_{tab_key}" style="font-size:0.85rem;color:#64748b;">
                  Ticking live in real-time. Button unlocks automatically at 00h : 00m : 00s.
                </div>
              </div>
            </div>
            <script>
              (function() {{
                var target = {unlock_ms};
                function tick() {{
                  var now = new Date().getTime();
                  var diff = target - now;
                  var elTimer = document.getElementById("countdown_{tab_key}");
                  var elStatus = document.getElementById("status_{tab_key}");
                  if (!elTimer) return;
                  if (diff <= 0) {{
                    elTimer.innerHTML = "00h : 00m : 00s";
                    if (elStatus) elStatus.innerHTML = "🎉 Quota timer completed! Unlocking button now...";
                    setTimeout(function() {{
                      window.parent.location.reload();
                    }}, 1500);
                    return;
                  }}
                  var hours = Math.floor(diff / 3600000);
                  var mins = Math.floor((diff % 3600000) / 60000);
                  var secs = Math.floor((diff % 60000) / 1000);
                  var pad = function(n) {{ return n < 10 ? '0' + n : n; }};
                  elTimer.innerHTML = pad(hours) + "h : " + pad(mins) + "m : " + pad(secs) + "s";
                }}
                tick();
                setInterval(tick, 1000);
              }})();
            </script>
            """, unsafe_allow_html=True)

            col_opt1, col_opt2 = st.columns([2, 1.5])
            with col_opt2:
                if st.button("🔄 Test Quota / Reset Timer", key=f"reset_timer_{tab_key}", use_container_width=True):
                    clear_quota_unlock_time()
                    st.rerun()


            # Render disabled button while timer is active
            st.button(f"⏳ {button_label} (Locked by Quota Timer)", key=f"btn_{tab_key}", disabled=True)
            return False

    # Timer not active or expired: render primary interactive button
    return st.button(button_label, key=f"btn_{tab_key}", type="primary")


def process_video(
    tmp_path: str,
    original_name: str,
    sidecar_bytes: bytes | None = None,
    sidecar_name: str | None = None,
    tab_key: str = "upload",
) -> None:
    """
    Upload a local video file to Gemini, run inference, render results.
    Inspects video for embedded transcription or processes sidecar subtitles.
    Guarantees cleanup of local temp file and Gemini-hosted file in finally block.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        st.error("❌ `GEMINI_API_KEY` is not set. Add it to your `.env` file or Streamlit Secrets.")
        logger.error("[CONFIG] GEMINI_API_KEY is missing. Aborting.")
        return

    client = genai.Client(api_key=api_key)
    video_file = None  # track so finally block knows whether to call delete

    try:
        # ── Phase 3: Upload to Gemini ─────────────────────────────────────
        with st.spinner("☁️ Uploading video to Gemini..."):
            logger.info(f"[UPLOAD] Starting upload of '{original_name}' to Gemini Files API.")
            video_file = client.files.upload(
                file=tmp_path,
                config=types.UploadFileConfig(
                    display_name=original_name,
                    mime_type="video/mp4",
                ),
            )
            logger.info(f"[UPLOAD] File accepted by Gemini. File name: {video_file.name}")

        # Wait for Gemini to finish processing the video (ACTIVE state)
        with st.spinner("⏳ Gemini is processing the video..."):
            while video_file.state.name == "PROCESSING":
                time.sleep(3)
                video_file = client.files.get(name=video_file.name)

            if video_file.state.name == "FAILED":
                raise RuntimeError(
                    f"Gemini video processing failed for file: {video_file.name}"
                )

        # ── Check for or Generate Transcription ───────────────────────────
        transcript_text = None
        transcript_source = None

        if sidecar_bytes and sidecar_name:
            transcript_text = parse_sidecar_transcription(sidecar_bytes, sidecar_name)
            if transcript_text:
                transcript_source = f"Uploaded File ({sidecar_name})"
                logger.info(f"[TRANSCRIPT] Sidecar transcript loaded from '{sidecar_name}' ({len(transcript_text)} chars).")

        if not transcript_text:
            transcript_text = extract_embedded_transcription(tmp_path)
            if transcript_text:
                transcript_source = "Embedded Subtitle Track"
                logger.info(f"[TRANSCRIPT] Embedded subtitle track discovered in '{original_name}' ({len(transcript_text)} chars).")

        # If no transcript exists, generate our own with AI!
        if not transcript_text:
            with st.spinner("🎙️ Generating timestamped transcript from video with AI..."):
                logger.info(f"[TRANSCRIPT] Generating automatic AI transcript for file: {video_file.name}")
                transcript_text = generate_transcription_from_video(client, video_file)
                if transcript_text:
                    transcript_source = "AI Auto-Generated"
                    logger.info(f"[TRANSCRIPT] AI transcription completed ({len(transcript_text)} chars).")

        st.session_state[f"transcript_text_{tab_key}"] = transcript_text
        st.session_state[f"transcript_source_{tab_key}"] = transcript_source

        if transcript_text:
            st.info(f"📝 **Transcription Active ({transcript_source})**: Spoken dialogue transcript was verified and integrated into QA analysis.")
            prompt_to_use = SYSTEM_PROMPT + f"""

---
VERIFIED TRANSCRIPTION / CLOSED CAPTIONS OF RECORDING:
The following dialogue and timing transcript was extracted/generated for the class recording:
\"\"\"
{transcript_text}
\"\"\"
MANDATORY INSTRUCTION: Cross-reference your evaluation with the transcript above to ensure all teacher quotes are 100% exact and verbatim, student interactions are precisely verified, and timestamps match the spoken dialogue.
---
"""
        else:
            prompt_to_use = SYSTEM_PROMPT
            logger.info(f"[TRANSCRIPT] Proceeding without separate transcript text for '{original_name}'.")

        # ── Phase 4: AI Inference ─────────────────────────────────────────
        with st.spinner("🤖 Evaluating class with AI..."):
            logger.info(f"[INFERENCE] Calling gemini-3.8-flash for file: {video_file.name}")

            response = generate_content_with_backoff(
                client=client,
                model="gemini-3.8-flash",
                contents=[video_file, prompt_to_use],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=Assessment,
                ),
                max_retries=5,
                initial_delay=2.0,
                backoff_factor=2.0,
            )

            # Parse the structured JSON into our Pydantic model
            assessment = Assessment.model_validate_json(response.text)
            logger.info(
                f"[INFERENCE] Assessment received. "
                f"Grade: {assessment.grade} | "
                f"Observations: {len(assessment.key_observations)}"
            )

        # Persist result in tab session state so other tabs don't see it & tab switches don't wipe it
        st.session_state[f"assessment_{tab_key}"] = assessment
        st.session_state[f"just_completed_{tab_key}"] = True

    except QuotaExceededError as q_err:
        logger.error(f"[QUOTA] Daily quota limit reached: unlock in {q_err.retry_seconds}s")
        set_quota_unlock_time(q_err.retry_seconds, q_err.message)
        st.rerun()

    except Exception as exc:
        err_msg = str(exc)
        if any(term in err_msg for term in ["RESOURCE_EXHAUSTED", "429", "Quota exceeded", "quotaMetric"]):
            retry_sec = extract_retry_delay_from_error(err_msg)
            logger.error(f"[QUOTA] Daily quota limit reached: unlock in {retry_sec}s")
            set_quota_unlock_time(retry_sec, err_msg)
            st.rerun()
        else:
            logger.exception(f"[ERROR] An error occurred during processing: {exc}")
            st.error(f"❌ An error occurred: {exc}")

    finally:
        # ── Phase 6: Guaranteed Cleanup ───────────────────────────────────
        # Delete local temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info(f"[CLEANUP] Deleted local temp file: {tmp_path}")

        # Delete Gemini-hosted file
        if video_file is not None:
            try:
                client.files.delete(name=video_file.name)
                logger.info(f"[CLEANUP] Deleted Gemini file: {video_file.name}")
            except Exception as cleanup_exc:
                logger.warning(
                    f"[CLEANUP] Could not delete Gemini file {video_file.name}: {cleanup_exc}"
                )


# ── Phase 5: UI — Header ──────────────────────────────────────────────────────

st.markdown(
    "<h1 style='font-size:2.2rem;font-weight:800;margin-bottom:0;'>🎓 8x8 AI Class Assessor</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color:#64748b;font-size:1rem;margin-top:0.2rem;'>"
    "Upload or link a class recording and receive an instant, evidence-based QA evaluation "
    "against the 8x8 Evaluation Rubric."
    "</p>",
    unsafe_allow_html=True,
)
st.divider()

# ── Phase 3: UI — Input Tabs ──────────────────────────────────────────────────

# Clean up any legacy single-tab keys if present from earlier runs
for legacy_k in ["assessment", "transcript_text", "transcript_source", "just_completed"]:
    st.session_state.pop(legacy_k, None)

tab_upload, tab_url = st.tabs(["📁 Upload Video File", "🔗 Video URL"])

# ── Tab 1: File Upload ────────────────────────────────────────────────────────
with tab_upload:
    col_v, col_t = st.columns([1.2, 1.0])
    with col_v:
        st.markdown("**📹 1. Class Recording** *(Required · MP4, MOV)*")
        uploaded_file = st.file_uploader(
            label="Drop your class recording here",
            type=["mp4", "mov"],
            key="upload_video_file",
            label_visibility="collapsed",
        )
    with col_t:
        st.markdown("**📝 2. Transcript / Subtitles** *(Optional · .srt, .vtt, .txt)*")
        st.caption("✨ *If not provided, AI will automatically generate a timestamped transcript from the video.*")
        sub_file = st.file_uploader(
            label="Drop transcript file here (optional)",
            type=["srt", "vtt", "txt"],
            key="upload_subtitles",
            label_visibility="collapsed",
        )

    if uploaded_file is not None:
        st.video(uploaded_file)

    if render_quota_countdown_and_button(tab_key="upload", button_label="🚀 Analyse Recording"):
        if uploaded_file is None:
            st.warning("⚠️ Please select and upload a video file before submitting.")
        else:
            logger.info(f"[UPLOAD] User submitted file upload: '{uploaded_file.name}'")

            # Save to a named temp file (delete=False so we control cleanup)
            suffix = os.path.splitext(uploaded_file.name)[-1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.getbuffer())
                tmp_path = tmp.name

            sidecar_bytes = sub_file.getvalue() if sub_file is not None else None
            sidecar_name = sub_file.name if sub_file is not None else None
            process_video(
                tmp_path,
                uploaded_file.name,
                sidecar_bytes=sidecar_bytes,
                sidecar_name=sidecar_name,
                tab_key="upload",
            )

    # Isolated results for Upload tab — persists across tab switching until Clear is clicked
    if "assessment_upload" in st.session_state:
        st.divider()
        c_status, c_clear = st.columns([3, 1])
        with c_status:
            if st.session_state.pop("just_completed_upload", False):
                st.success(f"✅ Assessment complete — **{st.session_state['assessment_upload'].grade}**")
            else:
                st.markdown(
                    "<p style='color:#64748b;font-size:0.9rem;margin-top:0.4rem;'>📂 Showing saved results for this tab.</p>",
                    unsafe_allow_html=True,
                )
        with c_clear:
            if st.button("🗑️ Clear Results", key="clear_upload", type="secondary", use_container_width=True):
                for k in ["assessment_upload", "transcript_text_upload", "transcript_source_upload", "just_completed_upload"]:
                    st.session_state.pop(k, None)
                st.rerun()

        render_dashboard(
            st.session_state["assessment_upload"],
            key_prefix="upload",
            transcript_text=st.session_state.get("transcript_text_upload"),
            transcript_source=st.session_state.get("transcript_source_upload"),
        )

# ── Tab 2: URL Input ──────────────────────────────────────────────────────────
with tab_url:
    col_uv, col_ut = st.columns([1.2, 1.0])
    with col_uv:
        st.markdown("**🔗 1. Video URL** *(Required · MP4, MOV or Google Drive)*")
        video_url = st.text_input(
            label="Video URL",
            placeholder="https://example.com/class-recording.mp4",
            label_visibility="collapsed",
            key="input_video_url",
        )
    with col_ut:
        st.markdown("**📝 2. Transcript / Subtitles** *(Optional · .srt, .vtt, .txt)*")
        st.caption("✨ *If not provided, AI will automatically generate a timestamped transcript from the video.*")
        sub_file_url = st.file_uploader(
            label="Drop transcript file here (optional)",
            type=["srt", "vtt", "txt"],
            key="url_subtitles",
            label_visibility="collapsed",
        )

    if render_quota_countdown_and_button(tab_key="url", button_label="🚀 Analyse Recording"):
        if not video_url.strip():
            st.warning("⚠️ Please enter a video URL before submitting.")
        else:
            logger.info(f"[UPLOAD] User submitted URL: {video_url}")

            with st.spinner("⬇️ Downloading video from URL..."):
                try:
                    target_url = video_url.strip()
                    # Automatically convert Google Drive sharing links to direct download links
                    if "drive.google.com" in target_url:
                        match = re.search(r"/d/([a-zA-Z0-9_-]+)", target_url)
                        if match:
                            file_id = match.group(1)
                            target_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                            logger.info(f"[UPLOAD] Converted Google Drive URL to direct link for ID: {file_id}")

                    session = requests.Session()
                    headers = {"User-Agent": "Mozilla/5.0"}
                    resp = session.get(target_url, headers=headers, stream=True, timeout=120)
                    resp.raise_for_status()

                    # Handle Google Drive large file virus-scan interstitial warning
                    if ("drive.google.com" in target_url or "drive.usercontent.google.com" in target_url) and "text/html" in resp.headers.get("Content-Type", ""):
                        html_text = resp.text
                        match_action = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html_text)
                        inputs = dict(re.findall(r'<input[^>]*name=["\']([^"\']*)["\'][^>]*value=["\']([^"\']*)["\']', html_text))
                        if match_action and inputs:
                            action_url = match_action.group(1)
                            from urllib.parse import urljoin
                            action_url = urljoin(target_url, action_url)
                            logger.info(f"[UPLOAD] Bypassing Google Drive virus scan prompt to: {action_url}")
                            resp = session.get(action_url, params=inputs, headers=headers, stream=True, timeout=180)
                            resp.raise_for_status()

                    # Basic MIME type check
                    content_type = resp.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        st.error(
                            "❌ The provided URL returned a webpage (HTML) rather than a direct video stream. "
                            "If using Google Drive, ensure link access is set to 'Anyone with the link can view'."
                        )
                        st.stop()

                    # Extract original filename from Content-Disposition if present
                    disposition = resp.headers.get("Content-Disposition", "")
                    cd_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)["\']?', disposition)
                    if cd_match:
                        downloaded_filename = cd_match.group(1).strip()
                    else:
                        downloaded_filename = video_url.split("/")[-1].split("?")[0] or "url_video.mp4"

                    # Determine file extension from URL or content-type
                    ext = ".mp4"
                    if downloaded_filename.lower().endswith(".mov") or "quicktime" in content_type:
                        ext = ".mov"

                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                        for chunk in resp.iter_content(chunk_size=65536):
                            tmp.write(chunk)
                        tmp_path = tmp.name

                    logger.info(f"[UPLOAD] Video downloaded from URL ({downloaded_filename}) to temp file: {tmp_path}")

                except requests.RequestException as req_err:
                    logger.exception(f"[UPLOAD] Failed to download URL: {req_err}")
                    st.error(f"❌ Could not download the video: {req_err}")
                    st.stop()

            sidecar_bytes = sub_file_url.getvalue() if sub_file_url is not None else None
            sidecar_name = sub_file_url.name if sub_file_url is not None else None
            process_video(
                tmp_path,
                downloaded_filename,
                sidecar_bytes=sidecar_bytes,
                sidecar_name=sidecar_name,
                tab_key="url",
            )

    # Isolated results for URL tab — persists across tab switching until Clear is clicked
    if "assessment_url" in st.session_state:
        st.divider()
        c_status, c_clear = st.columns([3, 1])
        with c_status:
            if st.session_state.pop("just_completed_url", False):
                st.success(f"✅ Assessment complete — **{st.session_state['assessment_url'].grade}**")
            else:
                st.markdown(
                    "<p style='color:#64748b;font-size:0.9rem;margin-top:0.4rem;'>📂 Showing saved results for this tab.</p>",
                    unsafe_allow_html=True,
                )
        with c_clear:
            if st.button("🗑️ Clear Results", key="clear_url", type="secondary", use_container_width=True):
                for k in ["assessment_url", "transcript_text_url", "transcript_source_url", "just_completed_url"]:
                    st.session_state.pop(k, None)
                st.rerun()

        render_dashboard(
            st.session_state["assessment_url"],
            key_prefix="url",
            transcript_text=st.session_state.get("transcript_text_url"),
            transcript_source=st.session_state.get("transcript_source_url"),
        )
