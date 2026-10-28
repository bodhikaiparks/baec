"""Software tests for the LinkedIn product demo: every button path, restart, recording mode, and offline-only.

Run from the repository root:  pytest -q demo_linkedin
These are UI tests of a synthetic demo, not evidence about BAEC.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

DEMO = Path(__file__).resolve().parent
APP = str(DEMO / "app.py")

STATEMENT = (
    "If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, "
    "we would put the contract out for review."
)
FORBIDDEN_CLAIMS = (
    "is shopping", "wants to switch", "opportunity reopened", "has intent", "is evaluating", "contact is authorized",
)


def run(recording: bool = False) -> AppTest:
    at = AppTest.from_file(APP)
    if recording:
        at.query_params["recording"] = "1"
    return at.run()


def page_text(at: AppTest) -> str:
    """All visible text the run produced: html blocks, markdown, buttons, and tab labels, tags stripped."""
    parts = [element.proto.body for element in at.get("html")]
    parts += [m.value for m in at.markdown] + [b.label for b in at.button] + [t.label for t in at.tabs]
    text = re.sub(r"<[^>]+>", "", " ".join(parts))
    return (text.replace("&ldquo;", "“").replace("&rdquo;", "”").replace("&rsquo;", "’")
            .replace("&gt;", ">").replace("&middot;", "·").replace("&#x27;", "'").replace("&amp;", "&"))


def click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click()
    return at.run()


def buttons(at: AppTest) -> list[str]:
    return [b.key for b in at.button]


def to_recognize() -> AppTest:
    at = run()
    click(at, "start_monitoring")
    return click(at, "review_correspondence")


def test_initially_only_step_one_is_shown():
    at = run()
    assert not at.exception
    text = page_text(at)
    assert "Remember what the buyer actually said." in text
    assert STATEMENT in text
    for value in ("Price increase > 8%", "July renewal", "Contract review", "Alder Ridge Clinics",
                  "Director of Operations"):
        assert value in text
    assert "Something changed." not in text
    assert buttons(at) == ["restart", "start_monitoring"]


def test_happy_path_reaches_prepare_with_boundary_strip():
    at = run()
    click(at, "start_monitoring")
    text = page_text(at)
    assert "Something changed." in text and "Price change: +9%" in text
    assert "This evidence may correspond to a condition the buyer previously described." in text
    assert "Remember what the buyer actually said." not in text

    click(at, "review_correspondence")
    text = page_text(at)
    assert "You decide whether the evidence actually corresponds." in text
    assert "Does this evidence correspond to the buyer’s previously stated condition?" in text
    assert {"verify_yes", "verify_no"} <= set(buttons(at)) and "prepare" not in buttons(at)

    click(at, "verify_yes")
    text = page_text(at)
    assert "Human-verified correspondence" in text
    assert "This does not establish buyer intent or authorize outreach." in text
    assert "verify_yes" not in buttons(at)

    click(at, "prepare")
    text = page_text(at)
    assert [t.label for t in at.tabs] == ["Why now", "Email draft", "Call opening", "Discovery questions"]
    for expected in ("Walk into the conversation prepared.", "Checking back on your July renewal", "Hi Jordan,",
                     "Is the 8% threshold still meaningful for your team?",
                     "BAEC prepares the conversation. The salesperson decides whether to have it.",
                     "No buyer intent inferred · No automatic outreach · No opportunity created"):
        assert expected in text
    assert buttons(at) == ["restart"]
    assert not at.exception


def test_not_enough_evidence_does_not_progress_and_can_be_reconsidered():
    at = to_recognize()
    click(at, "verify_no")
    text = page_text(at)
    assert "No correspondence recorded" in text
    assert "Human-verified correspondence" not in text
    assert "prepare" not in buttons(at) and at.session_state.step == 3

    click(at, "reconsider")
    assert {"verify_yes", "verify_no"} <= set(buttons(at))
    assert at.session_state.decision is None


@pytest.mark.parametrize("clicks", [[], ["start_monitoring"], ["start_monitoring", "review_correspondence"],
                                    ["start_monitoring", "review_correspondence", "verify_no"],
                                    ["start_monitoring", "review_correspondence", "verify_yes", "prepare"]])
def test_restart_returns_to_step_one_from_any_point(clicks):
    at = run()
    for key in clicks:
        click(at, key)
    click(at, "restart")
    assert at.session_state.step == 1 and at.session_state.decision is None
    assert buttons(at) == ["restart", "start_monitoring"]


def test_progress_marks_completed_steps():
    at = to_recognize()
    click(at, "verify_yes")
    progress = next(e.proto.body for e in at.get("html") if "bt-progress" in e.proto.body)
    assert progress.count("bt-pstep is-done") == 3
    click(at, "prepare")
    progress = next(e.proto.body for e in at.get("html") if "bt-progress" in e.proto.body)
    assert progress.count("bt-pstep is-done") == 3 and "bt-pstep is-current\" data-step=\"4\"" in progress


def test_recording_mode_hides_chrome_sidebar_and_drawer():
    normal, recording = run(), run(recording=True)
    assert [t.label for t in normal.toggle] == ["Recording mode"] and len(normal.expander) == 1
    assert len(recording.toggle) == 0 and len(recording.expander) == 0
    css = next(e.proto.body for e in recording.get("html") if "<style>" in e.proto.body)
    for selector in ('header[data-testid="stHeader"]', "#MainMenu", "footer", '[data-testid="stSidebar"]'):
        assert selector in css
    assert 'header[data-testid="stHeader"], [data-testid="stToolbar"]' not in \
        next(e.proto.body for e in normal.get("html") if "<style>" in e.proto.body)
    # The whole flow still works while recording.
    for key in ("start_monitoring", "review_correspondence", "verify_yes", "prepare"):
        click(recording, key)
    assert "BAEC prepares the conversation." in page_text(recording)


def test_sidebar_toggle_switches_recording_mode_on():
    at = run()
    at.toggle(key="recording_toggle").set_value(True).run()
    assert at.query_params["recording"] == "1"
    assert len(at.toggle) == 0 and len(at.expander) == 0


def test_no_screen_uses_forbidden_intent_language():
    at = run()
    texts = [page_text(at)]
    for key in ("start_monitoring", "review_correspondence", "verify_no", "reconsider", "verify_yes", "prepare"):
        click(at, key)
        texts.append(page_text(at))
    combined = " ".join(texts).lower()
    for claim in FORBIDDEN_CLAIMS:
        assert claim not in combined


def test_demo_is_offline_and_self_contained():
    allowed = {"__future__", "sys", "html", "pathlib", "streamlit", "demo_data", "styles"}
    for name in ("app.py", "demo_data.py", "styles.py"):
        source = (DEMO / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        assert imported <= allowed, (name, imported - allowed)
        assert "http://" not in source and "https://" not in source and "@import" not in source
    config = (DEMO / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "gatherUsageStats = false" in config and 'address = "localhost"' in config
