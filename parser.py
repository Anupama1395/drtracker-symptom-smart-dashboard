# parser.py — free-text patient report -> validated symptom events
import re
import json
from typing import Optional, List, Literal

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
{"events": [{"symptom": one of [nausea, vomiting, mouth_sores, pain, swallowing_difficulty, fatigue, other],
             "severity": 0-10 or null, "frequency": integer or null,
             "onset_offset_days": 0 or -1, "related_med": string or null,
             "confidence": 0.0-1.0, "evidence": exact quote from the report}]}
Rules:
- One event per distinct symptom mentioned.
- If the patient says a symptom did NOT happen ("no vomiting"), return it with severity 0 and frequency 0.
- Never invent a severity. If no number or clear word (mild/moderate/severe), use null.
- mild=3, moderate=5, severe=8 when the patient uses those words.
- "last night" or "yesterday" means onset_offset_days = -1.
- Set related_med only if a drug or "chemo"/"infusion"/"new pill" is explicitly linked to the symptom.
- Lower confidence when the wording is vague.
- If nothing clinical is reported, return {"events": []}."""

MODEL = "llama3.1:8b"

def call_llm(text: str) -> dict:
    resp = ollama.chat(
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

def rule_checks(text: str, ev: SymptomEvent) -> SymptomEvent:
    """Cheap deterministic checks that catch common LLM slips."""
    flags = []

    # 1. Evidence must actually appear in the text
    if ev.evidence.lower() not in text.lower():
        flags.append("evidence_not_in_text")
        ev.confidence = min(ev.confidence, 0.4)

    # 2. Severity must be backed by a number or a severity word in the evidence
    if ev.severity not in (None, 0):
        has_num = re.search(r"\b\d{1,2}\s*(/|out of)\s*10\b", ev.evidence)
        has_word = re.search(r"\b(mild|moderate|severe|bad|terrible|worse)\b", ev.evidence, re.I)
        if not (has_num or has_word):
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

    ev.needs_review = bool(flags) or ev.confidence < 0.6 or ev.symptom == "other"
    ev.flags = flags
    return ev


# ---------------- part 4: regex fallback ----------------
KEYWORDS = {
    "nausea": r"nause", "vomiting": r"vomit|threw up|throw up",
    "mouth_sores": r"mouth sore|sores", "pain": r"\bpain",
    "swallowing_difficulty": r"swallow|chok", "fatigue": r"tired|wiped out|exhaust|fatigue",
}

SEVERITY_WORDS = {"mild": 3, "moderate": 5, "severe": 8}   # same mapping the LLM is told to use

def _span(text: str, pos_start: int, pos_end: int, seps: str) -> str:
    """Substring around [pos_start, pos_end) bounded by any of the separator characters."""
    start = max(text.rfind(c, 0, pos_start) for c in seps) + 1
    ends = [i for i in (text.find(c, pos_end) for c in seps) if i != -1]
    return text[start: min(ends) if ends else len(text)]

def regex_parse(text: str) -> Extraction:
    events = []
    for sym, pat in KEYWORDS.items():
        m = re.search(pat, text, re.I)
        if not m:
            continue
        # Severity: the sentence that mentions the symptom ("Mouth sores are worse, maybe 7 out of 10.")
        # Negation and counts: only the clause, so "No mouth sores, but pain 5/10" doesn't zero the pain
        # and "Threw up 3 times, felt nauseous" doesn't give nausea a count.
        window = _span(text, m.start(), m.end(), ".!?;")
        clause = _span(text, m.start(), m.end(), ".!?;,")
        sev = re.search(r"(\d{1,2})\s*(/|out of)\s*10", window)
        sev_word = re.search(r"\b(mild|moderate|severe)\b", clause, re.I)
        freq = None
        for w, n in NUMBER_WORDS.items():
            if re.search(rf"\b{w}\b", clause, re.I):
                freq = n
        m2 = re.search(r"(\d+)\s*times", clause)
        if m2:
            freq = int(m2.group(1))
        negated = bool(NEGATION.search(clause))
        severity = (int(sev.group(1)) if sev else
                    SEVERITY_WORDS[sev_word.group(1).lower()] if sev_word else None)
        events.append(SymptomEvent(
            symptom=sym,
            severity=0 if negated else severity,
            frequency=0 if negated else freq,
            onset_offset_days=-1 if re.search(r"last night|yesterday", text, re.I) else 0,
            related_med="cisplatin" if re.search(r"chemo|infusion", window, re.I) else None,
            confidence=0.5, evidence=window.strip(),
        ))
    if not events:
        events.append(SymptomEvent(symptom="other", confidence=0.2, evidence=text[:80]))
    return Extraction(events=events)


# ---------------- part 5: entry point ----------------
# ConnectionError / ResponseError = Ollama not running or model not pulled -> still fall back
LLM_ERRORS = (ValidationError, json.JSONDecodeError, KeyError,
              ConnectionError, ollama.ResponseError)

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
