# Phase 8: the dashboard renders every page without errors (what CI runs on every push).
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import pipeline_runner

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("smoke")
    pipeline_runner.main(["--folder", str(d / "data")])
    shutil.copytree(ROOT / ".streamlit", d / ".streamlit")       # same theme config as the real app
    return d


def test_home_and_every_patient_render(app_dir, monkeypatch):
    monkeypatch.chdir(app_dir)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception and at.title[0].value == "Patients"
    patients = at.sidebar.selectbox[0].options
    assert len(patients) == 5
    for p in patients:
        at.sidebar.selectbox[0].set_value(p).run()
        assert not at.exception, (p, [e.message for e in at.exception])
        assert len(at.metric) == 6 and len(at.tabs) == 3
        at.toggle[0].set_value(True).run()                          # full-history chart
        assert not at.exception
        at.toggle[0].set_value(False).run()


def test_review_click_updates_counts(app_dir, monkeypatch):
    monkeypatch.chdir(app_dir)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.sidebar.selectbox[0].set_value([o for o in at.sidebar.selectbox[0].options if "P001" in o][0]).run()
    at.sidebar.text_input(key="reviewer").set_value("CI").run()
    before = at.tabs[2].label
    [b for b in at.button if b.key and b.key.startswith("acc_")][0].click().run()
    assert not at.exception and at.tabs[2].label != before         # cache refreshed after the write
