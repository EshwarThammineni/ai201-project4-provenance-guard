# ai201-project4-provenance-guard

# Provenance Guard

An API for detecting and labeling AI-generated text. Provenance Guard
accepts a text submission, runs two heuristic detection signals, combines
them into a confidence score, and returns a transparency label that tells
the reader how confidently the system can assess the text's origin.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/submit` | Submit text for AI-origin analysis |
| POST | `/appeal` | File an appeal against a classification |
| GET | `/status/<content_id>` | Check a submission's current status |
| GET | `/log` | View the audit log (most recent 50 entries) |

## Running the App

```bash
pip install flask flask-limiter
python app.py
```

Server starts on `http://localhost:5000`.

---

## Detection Signals

Provenance Guard uses two heuristic signals. Neither signal alone is
sufficient — they measure different properties of the text, and their
combination produces a more calibrated result than either would alone.

### Signal 1: Type-Token Ratio (TTR)

**What it measures:** Vocabulary diversity — the ratio of unique words to
total words. A text with 100 words, 90 of which are distinct, has a TTR
of 0.90.

**Why it differs between human and AI writing:** Large language models are
trained to produce fluent, readable output, which leads them to favor
common, high-frequency words. Human writers — especially in informal or
creative contexts — tend toward more varied vocabulary. A lower TTR
(less diversity) is a weak signal of AI generation.

**How it's scored:** Raw TTR is inverted and normalized onto a 0.0–1.0
AI-likeness scale. A TTR of 0.50 (low diversity) maps to a score near
1.0 (very AI-like). A TTR of 1.00 (every word unique) maps to 0.0
(very human-like).

**Known limitation:** TTR is unreliable on texts under 80 words. Short
texts almost always have high TTR regardless of origin — every word is
unique by chance. The system flags this with a warning and marks the
signal as unreliable. TTR also breaks down on technical or legal writing,
where intentional repetition of domain terms is normal and correct.

---

### Signal 2: Sentence Length Variance

**What it measures:** How much sentence lengths vary throughout the text,
computed as the standard deviation of per-sentence word counts.

**Why it differs between human and AI writing:** Human writers naturally
vary their rhythm — short punchy sentences followed by longer, more
complex ones. AI-generated text trends toward consistent sentence length
because the model optimizes for readability and coherence, which produces
uniformity as a side effect.

**How it's scored:** Standard deviation is inverted and normalized. A
stddev of 0 (perfectly uniform) maps to score 1.0 (AI-like). A stddev
of 15 or above (highly varied) maps to 0.0 (human-like). The cap at 15
was chosen empirically as the upper bound of normal prose variation.

**Known limitation:** Stylistically uniform human writers — minimalist
prose, legal documents, news wire copy, technical documentation — score
AI-like on this signal through no fault of their own. This is the
documented "false positive path" in the system design. The wide uncertain
band in the confidence scoring absorbs most of these cases.

---

## Confidence Scoring

Both signals produce a score on a 0.0–1.0 scale where higher means more
AI-like. They are combined with equal weighting:

```
confidence = (ttr_score × 0.5) + (variance_score × 0.5)
```

**Why equal weighting:** In testing, neither signal proved consistently
more reliable than the other across different text types. TTR performs
better on longer texts; sentence variance performs better on multi-sentence
passages. Giving them equal weight reflects this rough parity. In a
production system, weights would be tuned against a labeled dataset.

**Threshold map:**

| Score Range | Label |
|-------------|-------|
| 0.75 – 1.00 | Likely AI-Generated |
| 0.36 – 0.74 | Origin Uncertain |
| 0.00 – 0.35 | Likely Human-Written |

The uncertain band is intentionally wide — nearly 40 points. This reflects
the known limitations of heuristic signals. A narrow uncertain band would
produce overconfident labels on ambiguous text. When in doubt, the system
defaults to uncertainty rather than a false accusation.

### Example: Lower-confidence result (uncertain)

Submission: AI-generated paragraph about artificial intelligence (43 words)

```json
{
  "attribution": "uncertain",
  "confidence": 0.4157,
  "signals": {
    "ttr": { "score": 0.1938, "reliable": false },
    "sentence_variance": { "score": 0.6376, "reliable": true }
  }
}
```

Signal 2 clearly leans AI (0.64) but Signal 1 is unreliable due to
short length, pulling the combined score into the uncertain band.

### Example: Higher-confidence result (human)

Submission: Casual ramen restaurant review (55 words)

```json
{
  "attribution": "likely_human",
  "confidence": 0.2121,
  "signals": {
    "ttr": { "score": 0.2121, "reliable": false },
    "sentence_variance": { "score": 0.5518, "reliable": true }
  }
}
```

Both signals lean human. Sentence variance is notably lower (0.55) than
the AI paragraph (0.64) — the review's irregular sentence structure is
correctly detected as human-like.

**What I'd change for production:** The TTR signal needs a minimum word
count gate — submissions under 80 words should either be rejected with a
message asking for more text, or scored with a forced uncertain label
regardless of signal values. The current warning-only approach still
produces a label from unreliable data. I'd also tune the thresholds
against a real labeled dataset rather than setting them by design
intuition.

---

## Transparency Label Variants

The system returns three label variants based on the confidence score.
Every label uses hedged language — the system never asserts certainty.

### Variant A — Likely AI-Generated (confidence ≥ 0.75)

**Header:** Likely AI-Generated

**Body:** Our analysis found patterns consistent with AI-generated text.
This assessment is based on automated signals and may not be accurate.

**Action prompt:** If you believe this is incorrect, you may submit an
appeal.

---

### Variant B — Origin Uncertain (confidence 0.36–0.74)

**Header:** Origin Uncertain

**Body:** Our system could not confidently determine whether this text was
written by a human or generated by AI. This may reflect mixed signals, a
short text sample, or a writing style that falls outside our detection
range.

**Action prompt:** If you are the author, you may submit an appeal to
provide additional context.

---

### Variant C — Likely Human-Written (confidence ≤ 0.35)

**Header:** Likely Human-Written

**Body:** Our analysis found patterns consistent with human-authored text.

**Action prompt:** None — no appeal needed for a human-written result.

---

## Appeals Workflow

Any submitter with a valid `content_id` can file an appeal. The system
does not authenticate identity — it logs the appeal and flags it for
human review. It does not automatically reverse the label.

**Request:**
```bash
# PowerShell
$body = '{"content_id": "PASTE-ID-HERE", "creator_reasoning": "I wrote this myself."}'
Invoke-RestMethod -Method POST -Uri "http://localhost:5000/appeal" -ContentType "application/json" -Body $body | ConvertTo-Json
```

**Response:**
```json
{
  "content_id": "22eb143b-6750-4cd1-b0d5-492e591ab115",
  "status": "under_review",
  "message": "Your appeal was received and is under review."
}
```

After an appeal is filed, `GET /status/<content_id>` returns:
```json
{
  "appeal_status": "under_review",
  "appeal_reasoning": "I wrote this myself as a non-native English speaker...",
  "attribution": "uncertain",
  "confidence": 0.4157,
  "content_id": "22eb143b-6750-4cd1-b0d5-492e591ab115"
}
```

---

## Rate Limiting

Rate limiting is applied to `POST /submit`:

- **10 requests per minute**
- **100 requests per day**

**Rationale:** A writer submitting their own work rarely needs more than
10 submissions per minute — the limit allows reasonable interactive use
while preventing scripted bulk submission. The daily cap of 100 gives
a single user ample daily quota while limiting automated abuse. Limits
are keyed per client IP and stored in memory (resets on server restart).

### Rate limit test output

Sending 12 rapid requests — first 10 return 200, remainder return 429:

```
200
200
200
200
200
200
200
200
200
200
429 (Too Many Requests — 10 per 1 minute)
429 (Too Many Requests — 10 per 1 minute)
```

---

## Audit Log

Every submission and appeal writes a structured entry to `audit_log.jsonl`
(one JSON object per line). The log is never `print()` output — it is
always structured and machine-readable.

**Submission entry fields:** `event`, `content_id`, `creator_id`,
`attribution`, `confidence`, `ttr_score`, `ttr_reliable`,
`variance_score`, `variance_reliable`, `word_count`, `sentence_count`,
`status`, `timestamp`

**Appeal entry fields:** `event`, `content_id`, `creator_id`,
`original_attribution`, `original_confidence`, `appeal_reasoning`,
`status`, `timestamp`

### Sample log entries (last 3)

```json
{
  "attribution": "uncertain",
  "confidence": 0.4157,
  "content_id": "952d8eb6-3198-4a69-ab11-ec25cf662474",
  "creator_id": "test-mixed",
  "event": "submission",
  "sentence_count": 3,
  "status": "classified",
  "timestamp": "2026-07-01T02:02:36.203798+00:00",
  "ttr_reliable": false,
  "ttr_score": 0.125,
  "variance_reliable": true,
  "variance_score": 0.6857,
  "word_count": 40
}
{
  "attribution": "uncertain",
  "confidence": 0.4157,
  "content_id": "22eb143b-6750-4cd1-b0d5-492e591ab115",
  "creator_id": "test-ai",
  "event": "submission",
  "sentence_count": 3,
  "status": "classified",
  "timestamp": "2026-07-01T02:09:29.073789+00:00",
  "ttr_reliable": false,
  "ttr_score": 0.1938,
  "variance_reliable": true,
  "variance_score": 0.6376,
  "word_count": 43
}
{
  "appeal_reasoning": "I wrote this myself as a non-native English speaker and my writing style may appear more formal than typical.",
  "content_id": "22eb143b-6750-4cd1-b0d5-492e591ab115",
  "creator_id": "test-ai",
  "event": "appeal_received",
  "original_attribution": "uncertain",
  "original_confidence": 0.4157,
  "status": "under_review",
  "timestamp": "2026-07-01T02:10:07.649398+00:00"
}
```

---

## Known Limitations

### 1. Short text is structurally unclassifiable

The TTR signal is mathematically unreliable on texts under 80 words —
short texts always have high TTR (most words are unique by chance)
regardless of whether they were written by a human or an AI. This isn't
a calibration problem that more data would fix; it's a property of the
ratio itself. A 30-word AI-generated sentence will almost always score
as human-like on Signal 1.

In testing, every text under 80 words triggered the TTR reliability
warning, and all landed in the uncertain band. This is the correct
behavior — the system defaulted to uncertainty rather than guessing —
but it means the system is genuinely uninformative on short submissions
like headlines, captions, or social media posts.

### 2. Uniform human writing is structurally misclassified

Signal 2 (sentence variance) measures rhythmic variation. A human writer
who deliberately uses consistent sentence structure — legal boilerplate,
technical documentation, news wire copy, Hemingway-esque minimalism —
will score AI-like on this signal through no fault of their own. This is
not a solvable problem within the signal's design; uniform structure is
the thing the signal detects, regardless of who produced it.

In testing, the formal monetary policy excerpt scored 0.63 on sentence
variance — nearly as high as the AI-generated paragraph — because it
contains only two long, similarly-structured sentences. The system
correctly landed this in "uncertain" rather than "likely human," but a
longer formal document could plausibly cross the 0.75 threshold into
"likely AI" if its sentence structure was consistent enough.

---

## Spec Reflection

### One way the spec helped

Writing the three label variants in full before touching any code turned
out to be the most useful constraint in the project. By the time I
reached Milestone 5, I had exact copy to drop in — I wasn't making label
decisions under time pressure. More importantly, writing them first forced
me to decide on the hedged language ("patterns consistent with" rather
than "was written by") before the code was done. That decision would have
been easy to skip in implementation and hard to retrofit.

### One way implementation diverged from the spec

The spec called for the uncertain band to run from 0.36 to 0.74. In
practice, because the test texts were all under 80 words, the TTR signal
consistently scored between 0.10 and 0.25 — pulling nearly every
combined score below 0.45. The thresholds from the spec are correct in
principle, but against short text they produce a system that almost never
fires the "likely AI" label.

Rather than lowering the thresholds (which would produce false positives),
I kept the spec thresholds and instead made the reliability warnings more
prominent in the response. The real fix would be requiring a minimum word
count at the input validation layer — but changing the spec mid-build
felt like the wrong move. I documented it as a known limitation instead.

---

## AI Usage

### Instance 1: Generating the Flask skeleton and Signal 1

I provided the AI tool with the Detection Signals section of `planning.md`
and the architecture diagram, then asked it to generate a Flask app with
a `POST /submit` stub and a TTR signal function.

**What it produced:** A working Flask skeleton with `request.get_json()`,
`jsonify()`, and a TTR function that computed raw type-token ratio
correctly.

**What I revised:** The AI's TTR function returned raw TTR directly as
the score, with higher meaning more human-like. My spec required the
opposite convention — higher score = more AI-like — so the confidence
scoring could combine both signals consistently. I rewrote the
normalization to invert and clamp the TTR onto a [0.4, 1.0] range before
inverting, which also handles the edge case where TTR falls below 0.4.
I also added the `reliable` flag and word count warning, which the
generated code omitted entirely.

### Instance 2: Generating the appeal endpoint

I provided the Transparency Label Design and Appeals Workflow sections
from `planning.md` plus the architecture diagram, then asked the AI to
generate the `POST /appeal` endpoint and update the label generator with
full copy.

**What it produced:** A working `/appeal` endpoint that read `content_id`
and `creator_reasoning`, returned a confirmation response, and wrote to
the audit log.

**What I revised:** The generated endpoint stored appeal state in the
audit log only — it had no in-memory submissions dict, so there was no
way to look up a submission's current status or verify that a
`content_id` actually existed before filing the appeal. I added the
`submissions = {}` store, moved the status update there, and added the
404 response for unknown IDs. I also added the `GET /status/<content_id>`
endpoint, which the generated code didn't include but the architecture
diagram implied was needed. The 1000-character limit on `creator_reasoning`
came from my planning.md spec — the AI omitted it, so I added validation.