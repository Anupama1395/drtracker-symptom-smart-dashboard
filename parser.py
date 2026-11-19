# parser.py — free-text patient report -> validated symptom events
import re
import json
from typing import Optional, List, Literal

import httpx
import ollama
from pydantic import BaseModel, Field, ValidationError, field_validator


# ---------------- part 1: output schema ----------------
Symptom = Literal["nausea", "vomiting", "mouth_sores", "pain",
                  "swallowing_difficulty", "fatigue", "other"]

class SymptomEvent(BaseModel):
    symptom: Symptom
    severity: Optional[int] = Field(None, ge=0, le=10)   # None if not stated
    frequency: Optional[int] = Field(None, ge=0)         # episodes, None if not stated
    onset_offset_days: int = 0        # 0 = same day as report, -1 = "last night"/"yesterday"
    related_med: Optional[str] = None # drug name only if the patient linked it
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: str                     # the exact words in the report that support this
    needs_review: bool = False        # set by rule_checks
    flags: List[str] = Field(default_factory=list)   # which checks fired

    @field_validator("related_med")
    @classmethod
    def lowercase_med(cls, v):
        return v.lower().strip() if v else None

class Extraction(BaseModel):
    events: List[SymptomEvent]


# ---------------- part 2: LLM call ----------------
SYSTEM = """You extract structured symptom data from a cancer patient's daily report.
Return ONLY JSON matching this shape:
{{"events": [{{"symptom": one of [nausea, vomiting, mouth_sores, pain, swallowing_difficulty, fatigue, other],
             "severity": 0-10 or null, "frequency": integer or null,
             "onset_offset_days": 0 or -1, "related_med": string or null,
             "confidence": 0.0-1.0, "evidence": exact quote from the report}}]}}
Rules:
- One event per distinct symptom mentioned.
- If the patient says a symptom did NOT happen ("no vomiting"), return it with severity 0 and frequency 0.
- Never invent a severity. If no number or clear word (mild/moderate/severe), use null.
- {severity_words} when the patient uses those words.
- "last night" or "yesterday" means onset_offset_days = -1.
- Set related_med only if a drug or "chemo"/"infusion"/"new pill" is explicitly linked to the symptom.
- Lower confidence when the wording is vague.
- If nothing clinical is reported, return {{"events": []}}."""

MODEL = "llama3.1:8b"
REVIEW_THRESHOLD = 0.6     # below this confidence an event goes to human review
CLIENT = ollama.Client(timeout=120)      # a hung server becomes a timeout -> fallback, not a frozen run

def call_llm(text: str) -> dict:
    resp = CLIENT.chat(
        model=MODEL,
        format="json",
        options={"temperature": 0},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": text}],
    )
    return json.loads(resp["message"]["content"])


# ---------------- part 3: rule-based checks ----------------
NEGATION = re.compile(r"\b(no|not|didn't|did not|without|zero)\b", re.I)
NUMBER_WORDS = {"once": 1, "twice": 2, "three times": 3, "thrice": 3}

# ONE severity-word mapping, used by the rule check, the regex fallback and the LLM prompt.
# "worse"/"better" are deliberately absent: they describe a change, not a level.
SEVERITY_WORDS = {"mild": 3, "moderate": 5, "bad": 5, "severe": 8, "terrible": 8}
SEV_WORD_RE = re.compile(r"\b(" + "|".join(SEVERITY_WORDS) + r")\b", re.I)
SEV_NUM_RE = re.compile(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b", re.I)


def _norm(s: str) -> str:
    """Lowercase, punctuation -> space, single spaces: for comparing a quote with the report."""
    return " ".join(re.sub(r"[^\w/]+", " ", str(s).lower()).split())


def evidence_in_text(evidence: str, text: str) -> bool:
    """Quote check that tolerates punctuation/spacing changes (the LLM often drops a comma)."""
    ev = _norm(evidence)
    return bool(ev) and ev in _norm(text)


def rule_checks(text: str, ev: SymptomEvent) -> SymptomEvent:
    """Cheap deterministic checks that catch common LLM slips."""
    flags = []

    # 1. Evidence must actually appear in the text (ignoring punctuation and spacing)
    if not evidence_in_text(ev.evidence, text):
        flags.append("evidence_not_in_text")
        ev.confidence = min(ev.confidence, 0.4)

    # 2. Severity must be backed by a number or a severity word in the evidence
    if ev.severity not in (None, 0):
        if not (SEV_NUM_RE.search(ev.evidence) or SEV_WORD_RE.search(ev.evidence)):
            flags.append("severity_unsupported")
            ev.severity = None

    # 3. Negation in evidence but severity > 0 is a contradiction
    if NEGATION.search(ev.evidence) and (ev.severity or 0) > 0:
        flags.append("negation_conflict")
        ev.confidence = min(ev.confidence, 0.3)

    # 4. Frequency sanity
    if ev.frequency is not None and ev.frequency > 20:
        flags.append("frequency_implausible")
        ev.frequency = None

    ev.needs_review = bool(flags) or ev.confidence < REVIEW_THRESHOLD or ev.symptom == "other"
    ev.flags = flags
    return ev


SYSTEM = SYSTEM.format(severity_words=", ".join(f"{w}={n}" for w, n in SEVERITY_WORDS.items()))


# ---------------- part 4: regex fallback ----------------
KEYWORDS = {
    "nausea": r"nause", "vomiting": r"vomit|threw up|throw up",
    "mouth_sores": r"mouth sore|sores", "pain": r"\bpain",
    "swallowing_difficulty": r"swallow|chok", "fatigue": r"tired|wiped out|exhaust|fatigue",
}
# Vague "something is wrong" phrases -> an "other" event, even when other symptoms are present
VAGUE = re.compile(r"felt off|feel(?:ing)? off|off all day|not (?:feeling )?(?:myself|right)|"
                   r"out of sorts|unwell|under the weather", re.I)
SENTENCE_END = ".!?;"


def _span(text: str, pos_start: int, pos_end: int, seps: str) -> str:
    """Substring around [pos_start, pos_end) bounded by any of the separator characters."""
    start = max(text.rfind(c, 0, pos_start) for c in seps) + 1
    ends = [i for i in (text.find(c, pos_end) for c in seps) if i != -1]
    return text[start: min(ends) if ends else len(text)]


def _number_owners(text: str) -> dict:
    """Which symptom each 'N/10' belongs to: the nearest symptom mentioned before it in the same
    sentence (or the first one after it, if none comes before). So in "Throat pain 8/10, worse when
    swallowing" the 8 belongs to pain, not to swallowing."""
    hits = sorted((m.start(), sym) for sym, pat in KEYWORDS.items() for m in re.finditer(pat, text, re.I))
    owners = {}
    for n in SEV_NUM_RE.finditer(text):
        s0 = max(text.rfind(c, 0, n.start()) for c in SENTENCE_END) + 1
        ends = [i for i in (text.find(c, n.end()) for c in SENTENCE_END) if i != -1]
        s1 = min(ends) if ends else len(text)
        in_sentence = [(pos, sym) for pos, sym in hits if s0 <= pos < s1]
        before = [h for h in in_sentence if h[0] < n.start()]
        owner = before[-1][1] if before else (in_sentence[0][1] if in_sentence else None)
        if owner and owner not in owners:
            owners[owner] = int(n.group(1))
    return owners


def regex_parse(text: str) -> Extraction:
    events = []
    owners = _number_owners(text)
    for sym, pat in KEYWORDS.items():
        m = re.search(pat, text, re.I)
        if not m:
            continue
        # Severity: a number this symptom owns, else a severity word in its own clause.
        # Negation and counts: only the clause, so "No mouth sores, but pain 5/10" doesn't zero the pain
        # and "Threw up 3 times, felt nauseous" doesn't give nausea a count.
        window = _span(text, m.start(), m.end(), SENTENCE_END)
        clause = _span(text, m.start(), m.end(), SENTENCE_END + ",")
        sev_word = SEV_WORD_RE.search(clause)
        freq = None
        for w, n in NUMBER_WORDS.items():
            if re.search(rf"\b{w}\b", clause, re.I):
                freq = n
        m2 = re.search(r"(\d+)\s*times", clause)
        if m2:
            freq = int(m2.group(1))
        negated = bool(NEGATION.search(clause))
        severity = owners.get(sym, SEVERITY_WORDS[sev_word.group(1).lower()] if sev_word else None)
        events.append(SymptomEvent(
            symptom=sym,
            severity=0 if negated else severity,
            frequency=0 if negated else freq,
            # only the symptom's own sentence: "Vomited yesterday. Pain 7/10." keeps pain on today
            onset_offset_days=-1 if re.search(r"last night|yesterday", window, re.I) else 0,
            related_med="cisplatin" if re.search(r"chemo|infusion", window, re.I) else None,
            confidence=0.5, evidence=window.strip(),
        ))
    if v := VAGUE.search(text):
        events.append(SymptomEvent(symptom="other", confidence=0.3,
                                   evidence=_span(text, v.start(), v.end(), SENTENCE_END).strip()))
    if not events:
        events.append(SymptomEvent(symptom="other", confidence=0.2, evidence=text[:80]))
    return Extraction(events=events)


# ---------------- part 5: entry point ----------------
# Any way the LLM can fail -> fall back instead of crashing the batch:
#   ValidationError / JSONDecodeError / KeyError  -> bad output
#   ConnectionError                               -> Ollama not running
#   ollama.ResponseError                          -> model not pulled / server error
#   httpx.HTTPError                               -> connection reset, timeout (e.g. Ollama crash-looping)
LLM_ERRORS = (ValidationError, json.JSONDecodeError, KeyError,
              ConnectionError, ollama.ResponseError, httpx.HTTPError)

def parse_report(text: str) -> tuple[Extraction, str]:
    """Returns (extraction, extracted_by). Falls back to regex if the LLM fails."""
    try:
        raw = call_llm(text)
        ext = Extraction.model_validate(raw)
        ext.events = [rule_checks(text, e) for e in ext.events]
        return ext, "llm"
    except LLM_ERRORS as err:
        ext = regex_parse(text)
        for e in ext.events:
            e.needs_review = True
            e.flags = [f"llm_failed:{type(err).__name__}"]
        return ext, "rule"
