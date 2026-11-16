"""Smoke test for the web app: it loads, and the default shifts produce a result."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parent.parent / "streamlit_app.py")


def test_app_runs_a_check():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    at.button[0].click().run()
    assert not at.exception
    assert [m.label for m in at.metric] == ["Award minimum", "You were paid", "Difference"]


def test_every_award_loads():
    at = AppTest.from_file(APP, default_timeout=60).run()
    for award in at.selectbox[0].options:
        at.selectbox[0].set_value(award.split("[")[-1].rstrip("]")).run()
        assert not at.exception
