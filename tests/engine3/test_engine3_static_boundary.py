import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRODUCTION = REPO / "baec_app" / "engine3"


def _source():
    return "\\n".join(p.read_text(encoding="utf-8") for p in sorted(PRODUCTION.glob("*.py")))


def test_stage_d_production_has_no_corpus_or_oracle_dependency():
    src = _source()
    for pattern in (r"E3B-", r"expected_", r"case_id", r"CORPUS", r"ORACLE"):
        assert not re.search(pattern, src, re.I), pattern


def test_stage_d_production_has_no_ai_network_ui_persistence_or_external_action_sdk():
    src = _source()
    for pattern in (
        r"\\banthropic\\b", r"\\bopenai\\b", r"\\brequests\\b", r"\\burllib\\b",
        r"\\bsocket\\b", r"\\bstreamlit\\b", r"\\bsqlite3\\b", r"\\bsmtplib\\b",
        r"\\bsalesforce\\b", r"\\bselenium\\b",
    ):
        assert not re.search(pattern, src, re.I), pattern


def test_stage_d_does_not_import_engine1_or_engine2_modules():
    src = _source()
    assert "baec_app.engine2" not in src
    assert "baec_app.domain" not in src


def test_stage_d_has_no_runtime_clock_or_nondeterminism():
    src = _source()
    for pattern in (r"datetime\\.now", r"time\\.time", r"random\\.", r"uuid4"):
        assert not re.search(pattern, src), pattern
