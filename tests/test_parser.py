# Parser tests. No Ollama needed: the LLM call is replaced with monkeypatch where it matters.
import json

import pytest

import parser
from ingest import event_rows
from parser import Extraction, SymptomEvent, parse_report, regex_parse, rule_checks


def by_symptom(extraction):
    return {e.symptom: e for e in extraction.events}


# ---------------- regex fallback ----------------
def test_negation_only_applies_to_its_own_sentence():
    ev = by_symptom(regex_parse("No vomiting today. Nausea mild."))
    assert (ev["vomiting"].severity, ev["vomiting"].frequency) == (0, 0)     # denied
    assert ev["nausea"].severity == 3                                         # mild, not negated


def test_negation_only_applies_to_its_own_clause():
    ev = by_symptom(regex_parse("No mouth sores, but pain about 6/10 when I swallow."))
    assert ev["mouth_sores"].severity == 0
    assert ev["pain"].severity == 6


def test_last_night_sets_onset_to_previous_day_and_links_chemo():
    ext = regex_parse("Vomited once last night after the chemo infusion.")
    e = by_symptom(ext)["vomiting"]
    assert e.onset_offset_days == -1 and e.frequency == 1 and e.related_med == "cisplatin"
    row = event_rows(1, "P001", "2026-09-10T08:15:00", ext, "rule", first_event_id=1)[0]
    assert row["onset"] == "2026-09-09"                                       # stored on the day it happened


@pytest.mark.parametrize("text, expected", [
    ("Throat pain is severe today.", 8),
    ("Pain is mild today.", 3),
    ("Moderate pain in the afternoon.", 5),
    ("Pain about 4/10.", 4),
    ("Pain maybe 7 out of 10.", 7),
])
def test_severity_words_and_numbers(text, expected):
    assert by_symptom(regex_parse(text))["pain"].severity == expected


def test_no_number_means_no_severity():
    assert by_symptom(regex_parse("Swallowing is getting harder."))["swallowing_difficulty"].severity is None


def test_counts_stay_with_their_clause():
    ev = by_symptom(regex_parse("Threw up 3 times after dinner, felt nauseous all morning."))
    assert ev["vomiting"].frequency == 3
    assert ev["nausea"].frequency is None


def test_vague_report_becomes_other():
    ext = regex_parse("Felt off all day, not sure why.")
    assert [e.symptom for e in ext.events] == ["other"]


# ---------------- rule checks ----------------
def ev(**kw):
    base = dict(symptom="nausea", severity=None, frequency=None, confidence=0.9, evidence="Nausea")
    return SymptomEvent(**{**base, **kw})


def test_severity_without_support_is_removed():
    out = rule_checks("Nausea all day.", ev(severity=7, evidence="Nausea all day"))
    assert out.severity is None and "severity_unsupported" in out.flags and out.needs_review


def test_negation_conflict_is_flagged():
    out = rule_checks("No nausea, 5/10.", ev(severity=5, evidence="No nausea, 5/10"))
    assert "negation_conflict" in out.flags and out.confidence <= 0.3


def test_evidence_must_be_in_text():
    out = rule_checks("Nausea about 4/10.", ev(severity=4, evidence="nausea was terrible 4/10"))
    assert "evidence_not_in_text" in out.flags and out.needs_review


def test_implausible_frequency_is_removed():
    out = rule_checks("Threw up 50 times", ev(symptom="vomiting", frequency=50, evidence="Threw up 50 times"))
    assert out.frequency is None and "frequency_implausible" in out.flags


def test_clean_llm_event_is_not_flagged():
    out = rule_checks("Nausea about 4/10.", ev(severity=4, evidence="Nausea about 4/10"))
    assert out.flags == [] and not out.needs_review


# ---------------- parse_report: validation + fallback ----------------
def test_llm_down_falls_back_to_regex_and_flags_everything(monkeypatch):
    def down(_):
        raise ConnectionError("ollama not running")
    monkeypatch.setattr(parser, "call_llm", down)
    ext, by = parse_report("Nausea about 6/10 today.")
    assert by == "rule"
    assert all(e.needs_review and e.flags == ["llm_failed:ConnectionError"] for e in ext.events)


def test_out_of_range_llm_output_is_rejected(monkeypatch):
    bad = {"events": [{"symptom": "nausea", "severity": 12, "confidence": 0.9, "evidence": "Nausea"}]}
    monkeypatch.setattr(parser, "call_llm", lambda _: bad)
    ext, by = parse_report("Nausea about 6/10 today.")
    assert by == "rule" and ext.events[0].flags == ["llm_failed:ValidationError"]


def test_invented_symptom_name_is_rejected(monkeypatch):
    bad = {"events": [{"symptom": "headache", "severity": 4, "confidence": 0.9, "evidence": "x"}]}
    monkeypatch.setattr(parser, "call_llm", lambda _: bad)
    assert parse_report("My head hurts 4/10")[1] == "rule"


def test_valid_llm_output_is_used_and_rule_checked(monkeypatch):
    good = {"events": [{"symptom": "nausea", "severity": 6, "confidence": 0.9, "evidence": "Nausea about 6/10"}]}
    monkeypatch.setattr(parser, "call_llm", lambda _: json.loads(json.dumps(good)))
    ext, by = parse_report("Nausea about 6/10 today.")
    assert by == "llm" and ext.events[0].severity == 6 and not ext.events[0].needs_review


def test_empty_extraction_is_valid():
    assert Extraction.model_validate({"events": []}).events == []
