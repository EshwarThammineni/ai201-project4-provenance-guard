"""
Provenance Guard — app.py
Milestone 5: Full transparency labels + POST /appeal + complete audit log

Changes from M4:
  - generate_label() now returns full label copy from planning.md (3 variants)
  - Added in-memory submissions store to track status for appeals
  - Added POST /appeal endpoint (lookup → status update → audit log → response)
  - Startup message updated to list all four endpoints
  - Audit log entries for appeals include event type, reasoning, and original classification

Endpoints:
  POST /submit   — run detection, return label
  POST /appeal   — file an appeal against a submission
  GET  /log      — view audit log entries
  GET  /status/<content_id> — look up a single submission's current status

Run:
    python app.py

Rate limits on /submit: 10 per minute, 100 per day (per IP).
Rationale: a writer submitting their own work rarely needs more than 10
submissions per minute. The daily cap prevents scripted bulk submission.
"""

import json
import math
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ─────────────────────────────────────────────
# App + Rate Limiter
# ─────────────────────────────────────────────

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# ─────────────────────────────────────────────
# In-Memory Submissions Store
# ─────────────────────────────────────────────
# Keyed by content_id. Stores the classification result and appeal status
# so POST /appeal can look up and update a submission without a database.
# Resets when the server restarts — acceptable for this project scope.

submissions = {}  # { content_id: { ...classification fields, appeal_status, appeal_reasoning } }


# ─────────────────────────────────────────────
# Audit Log (JSON Lines)
# ─────────────────────────────────────────────

LOG_PATH = "audit_log.jsonl"


def write_log(entry: dict) -> None:
    """Append a single structured entry to the audit log."""
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_log(limit: int = 50) -> list:
    """Return the most recent `limit` entries from the audit log."""
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines[-limit:]]


# ─────────────────────────────────────────────
# Signal 1: Type-Token Ratio (TTR)
# ─────────────────────────────────────────────
# Measures vocabulary diversity. Low diversity → AI-like.
# Unreliable under 80 words.

MIN_WORDS_FOR_RELIABLE_TTR = 80


def compute_ttr_score(text: str) -> dict:
    words = text.lower().split()
    word_count = len(words)

    if word_count == 0:
        return {"ttr_raw": 0.0, "signal_score": 0.5, "word_count": 0, "reliable": False}

    ttr_raw = len(set(words)) / word_count
    ttr_clamped = max(0.4, min(ttr_raw, 1.0))
    signal_score = 1.0 - ((ttr_clamped - 0.4) / 0.6)

    return {
        "ttr_raw": round(ttr_raw, 4),
        "signal_score": round(signal_score, 4),
        "word_count": word_count,
        "reliable": word_count >= MIN_WORDS_FOR_RELIABLE_TTR,
    }


# ─────────────────────────────────────────────
# Signal 2: Sentence Length Variance
# ─────────────────────────────────────────────
# Measures rhythmic variation. Uniform sentence lengths → AI-like.
# Unreliable under 3 sentences.

MIN_SENTENCES_FOR_RELIABLE_VARIANCE = 3
MAX_STDDEV = 15.0


def compute_sentence_variance_score(text: str) -> dict:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s for s in sentences if s.strip()]
    sentence_count = len(sentences)

    if sentence_count == 0:
        return {"sentence_count": 0, "stddev": 0.0, "signal_score": 0.5, "reliable": False}
    if sentence_count == 1:
        return {"sentence_count": 1, "stddev": 0.0, "signal_score": 0.5, "reliable": False}

    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    stddev = math.sqrt(variance)
    signal_score = 1.0 - min(stddev / MAX_STDDEV, 1.0)

    return {
        "sentence_count": sentence_count,
        "stddev": round(stddev, 4),
        "signal_score": round(signal_score, 4),
        "reliable": sentence_count >= MIN_SENTENCES_FOR_RELIABLE_VARIANCE,
    }


# ─────────────────────────────────────────────
# Confidence Scoring
# ─────────────────────────────────────────────
# Equal weighting (0.5 / 0.5) per planning.md.
# Threshold map:
#   >= 0.75  → likely_ai
#   <= 0.35  → likely_human
#   else     → uncertain  (wide band by design)

SIGNAL_1_WEIGHT = 0.5
SIGNAL_2_WEIGHT = 0.5

assert SIGNAL_1_WEIGHT + SIGNAL_2_WEIGHT == 1.0, "Weights must sum to 1.0"


def compute_confidence(ttr_score: float, variance_score: float) -> float:
    return round((ttr_score * SIGNAL_1_WEIGHT) + (variance_score * SIGNAL_2_WEIGHT), 4)


# ─────────────────────────────────────────────
# Transparency Label Generator — MILESTONE 5
# ─────────────────────────────────────────────
# Full label copy from planning.md. Three variants, matching thresholds exactly.
# Every label uses hedged language — the system never asserts certainty.

def generate_label(confidence: float) -> dict:
    """
    Map a confidence score to one of three label variants.

    Variant A — likely_ai    (confidence >= 0.75)
    Variant B — uncertain    (0.36 <= confidence <= 0.74)
    Variant C — likely_human (confidence <= 0.35)

    Returns: { attribution, label_header, label_body, action_prompt }
    """

    # ── Variant A: Likely AI-Generated ───────────────────────────────────
    if confidence >= 0.75:
        return {
            "attribution": "likely_ai",
            "label_header": "Likely AI-Generated",
            "label_body": (
                "Our analysis found patterns consistent with AI-generated text. "
                "This assessment is based on automated signals and may not be accurate."
            ),
            "action_prompt": (
                "If you believe this is incorrect, you may submit an appeal."
            ),
        }

    # ── Variant C: Likely Human-Written ──────────────────────────────────
    elif confidence <= 0.35:
        return {
            "attribution": "likely_human",
            "label_header": "Likely Human-Written",
            "label_body": (
                "Our analysis found patterns consistent with human-authored text."
            ),
            "action_prompt": None,
        }

    # ── Variant B: Uncertain ──────────────────────────────────────────────
    else:
        return {
            "attribution": "uncertain",
            "label_header": "Origin Uncertain",
            "label_body": (
                "Our system could not confidently determine whether this text was "
                "written by a human or generated by AI. This may reflect mixed signals, "
                "a short text sample, or a writing style that falls outside our "
                "detection range."
            ),
            "action_prompt": (
                "If you are the author, you may submit an appeal to provide "
                "additional context."
            ),
        }


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    """
    POST /submit
    Accepts: { "text": "...", "creator_id": "..." }
    Returns: { content_id, attribution, confidence, label_header, label_body,
               action_prompt, signals, warnings? }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "anonymous").strip()

    if not text:
        return jsonify({"error": "Field 'text' is required and cannot be empty."}), 400

    # ── Signals ───────────────────────────────────────────────────────────
    ttr_result = compute_ttr_score(text)
    variance_result = compute_sentence_variance_score(text)
    confidence = compute_confidence(ttr_result["signal_score"], variance_result["signal_score"])

    # ── Label ─────────────────────────────────────────────────────────────
    label_result = generate_label(confidence)

    # ── Store submission for appeal lookup ────────────────────────────────
    content_id = str(uuid.uuid4())
    submissions[content_id] = {
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": label_result["attribution"],
        "confidence": confidence,
        "ttr_score": ttr_result["signal_score"],
        "variance_score": variance_result["signal_score"],
        "appeal_status": None,
        "appeal_reasoning": None,
    }

    # ── Build response ────────────────────────────────────────────────────
    response_body = {
        "content_id": content_id,
        "attribution": label_result["attribution"],
        "confidence": confidence,
        "label_header": label_result["label_header"],
        "label_body": label_result["label_body"],
        "action_prompt": label_result["action_prompt"],
        "signals": {
            "ttr": {
                "raw": ttr_result["ttr_raw"],
                "score": ttr_result["signal_score"],
                "reliable": ttr_result["reliable"],
            },
            "sentence_variance": {
                "stddev": variance_result["stddev"],
                "score": variance_result["signal_score"],
                "sentence_count": variance_result["sentence_count"],
                "reliable": variance_result["reliable"],
            },
        },
    }

    # ── Reliability warnings ──────────────────────────────────────────────
    warnings = []
    if not ttr_result["reliable"]:
        warnings.append(
            f"TTR signal unreliable: only {ttr_result['word_count']} words "
            f"(minimum {MIN_WORDS_FOR_RELIABLE_TTR} for reliable scoring)."
        )
    if not variance_result["reliable"]:
        warnings.append(
            f"Sentence variance signal unreliable: only {variance_result['sentence_count']} "
            f"sentence(s) detected (minimum {MIN_SENTENCES_FOR_RELIABLE_VARIANCE})."
        )
    if warnings:
        response_body["warnings"] = warnings

    # ── Audit log ─────────────────────────────────────────────────────────
    write_log({
        "event": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": label_result["attribution"],
        "confidence": confidence,
        "ttr_score": ttr_result["signal_score"],
        "ttr_reliable": ttr_result["reliable"],
        "variance_score": variance_result["signal_score"],
        "variance_reliable": variance_result["reliable"],
        "word_count": ttr_result["word_count"],
        "sentence_count": variance_result["sentence_count"],
        "status": "classified",
    })

    return jsonify(response_body), 200


@app.route("/appeal", methods=["POST"])
def appeal():
    """
    POST /appeal
    Accepts: { "content_id": "...", "creator_reasoning": "..." }
    Returns: { content_id, status, message }

    Appeal flow (from planning.md):
      1. Look up submission by content_id
      2. Update appeal_status → "under_review"
      3. Write appeal event to audit log (preserves original classification)
      4. Return confirmation
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    content_id = data.get("content_id", "").strip()
    reasoning = data.get("creator_reasoning", "").strip()

    # ── Validation ────────────────────────────────────────────────────────
    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not reasoning:
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400
    if len(reasoning) > 1000:
        return jsonify({"error": "creator_reasoning must be 1000 characters or fewer."}), 400

    # ── Look up submission ────────────────────────────────────────────────
    submission = submissions.get(content_id)
    if not submission:
        return jsonify({
            "error": "content_id not found. Appeals can only be filed for submissions "
                     "made during this server session."
        }), 404

    # ── Update status ─────────────────────────────────────────────────────
    # Before: appeal_status = None
    # After:  appeal_status = "under_review"
    submissions[content_id]["appeal_status"] = "under_review"
    submissions[content_id]["appeal_reasoning"] = reasoning

    # ── Audit log — appeal event ──────────────────────────────────────────
    # Logs the appeal alongside the original classification so a human
    # reviewer sees both in context.
    write_log({
        "event": "appeal_received",
        "content_id": content_id,
        "creator_id": submission["creator_id"],
        "original_attribution": submission["attribution"],
        "original_confidence": submission["confidence"],
        "appeal_reasoning": reasoning,
        "status": "under_review",
    })

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal was received and is under review.",
    }), 200


@app.route("/status/<content_id>", methods=["GET"])
def get_status(content_id):
    """
    GET /status/<content_id>
    Returns the current classification and appeal status for a submission.
    """
    submission = submissions.get(content_id)
    if not submission:
        return jsonify({"error": "content_id not found."}), 404
    return jsonify(submission), 200


@app.route("/log", methods=["GET"])
def view_log():
    """
    GET /log
    Returns the most recent audit log entries as JSON.
    """
    entries = read_log(limit=50)
    return jsonify({"count": len(entries), "entries": entries}), 200


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Provenance Guard starting on http://localhost:5000")
    print("Endpoints:")
    print("  POST /submit               — submit text for analysis")
    print("  POST /appeal               — file an appeal")
    print("  GET  /status/<content_id>  — check submission status")
    print("  GET  /log                  — view audit log")
    app.run(port=5000, debug=True)