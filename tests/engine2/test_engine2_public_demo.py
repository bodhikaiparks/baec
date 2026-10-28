"""BAEC Engine 2 public demo (post-v1.0 presentation): tested offline with Streamlit AppTest over the real entry point.

Sockets are blocked and credentials removed: no model, provider, or network is reachable. These are software tests of
interface behavior on synthetic data, not evidence for the theory.
"""

import ast
import re
import socket
import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import scripts.engine2_public_demo_seed as seed_module
import scripts.engine2_release_demo as release
from baec_app.data.database import open_database
from baec_app.engine2 import persistence as p
from baec_app.engine2 import review
from baec_app.engine2.domain import CorrespondenceDimension as D
from baec_app.engine2.domain import CrossCuttingCheckName
from baec_app.engine2.public_demo import content as copy

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "streamlit_engine2_demo.py"
PACKAGE = REPO / "baec_app" / "engine2" / "public_demo"
SEED = REPO / "scripts" / "engine2_public_demo_seed.py"
STAGE_J = ("baec_app/engine2/operator_ui", "streamlit_engine2_app.py", "tests/engine2/test_engine2_operator_ui.py")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)


class Visitor:
    """One visitor: one AppTest session over the real entry point, driven like a person would."""

    def __init__(self):
        self.at = AppTest.from_file(str(ENTRY), default_timeout=60)
        self.run()

    def run(self):
        self.at.run()
        assert not self.at.exception, [e.value for e in self.at.exception]
        return self

    def click(self, key):
        self.at.button(key=key).click()
        return self.run()

    def to_review(self):
        return self.click("pd2_start").click("pd2_to_2").click("pd2_to_3").click("pd2_to_4")

    def text(self):
        parts = [t.value for t in self.at.text] + [m.value for m in self.at.markdown] + [c.value for c in self.at.caption]
        parts += [x.value for x in (*self.at.info, *self.at.warning, *self.at.error)]
        parts += [h.value for h in (*self.at.title, *self.at.header, *self.at.subheader)]
        return "\n".join(parts)

    def answer_group(self, group, *, findings=None, skip=()):
        at, findings = self.at, findings or {}
        for d in group.dimensions:
            if d.name in skip:
                continue
            default = "SUPPORTED" if d in release.CHOICES.required_dimensions else "NOT_APPLICABLE"
            at.button_group(key=f"pd2_finding_{d.name}").set_value(findings.get(d, default))
        evidence = "DM-DEMO-02" if group.key == "threshold" else "OBS-DEMO-02"
        at.multiselect(key=f"pd2_evidence_{group.key}").select(evidence)
        at.text_area(key=f"pd2_reason_{group.key}").input(f"Visitor reasoning about {group.title.lower()}.")
        return self.run()

    def answer_quality(self, *, sufficiency="YES", revalidation="NOT_REQUIRED", alias="BP"):
        at = self.at
        for c in CrossCuttingCheckName:
            at.button_group(key=f"pd2_check_{c.name}").set_value("NO_CONCERN")
        if sufficiency:
            at.button_group(key="pd2_sufficiency").set_value(sufficiency)
        if revalidation:
            at.button_group(key="pd2_revalidation").set_value(revalidation)
        if alias:
            at.text_input(key="pd2_alias").input(alias)
        return self.run()

    def answer(self, *, findings=None, sufficiency="YES", revalidation="NOT_REQUIRED", alias="BP", skip=()):
        """Work through 4A to 4D in order, as a visitor would. Ends on 4D, ready to submit."""
        for group in copy.GROUPS:
            self.answer_group(group, findings=findings, skip=skip).click("pd2_sub_next")
        return self.answer_quality(sufficiency=sufficiency, revalidation=revalidation, alias=alias)

    def to_substep(self, index):
        while self.at.session_state["pd2_sub"] < index:
            self.click("pd2_sub_next")
        while self.at.session_state["pd2_sub"] > index:
            self.click("pd2_sub_back")
        return self

    def store(self):
        connection = p.open_engine2_database(":memory:")
        connection.deserialize(self.at.session_state["pd2_engine2_image"])
        return connection

    def seed(self):
        return self.at.session_state["pd2_seed"]


def packet_of(connection, seed):
    return review.build_review_packet(connection, monitoring_plan_id=seed.monitoring_plan_id,
                                      plan_version=seed.plan_version, baec_id=seed.handoff.baec_id,
                                      candidate_id=seed.candidate_id, activation_id=seed.activation_id)


# --- landing and the guided steps -----------------------------------------------------------------------------


def test_the_landing_page_positions_engine2_narrowly():
    visitor = Visitor()
    text = visitor.text()
    for line in (copy.TITLE, copy.SUBTITLE, copy.ENGINE1_LINE, copy.ENGINE2_LINE, copy.BOUNDARY, copy.FOOTER,
                 *copy.NOTICE):
        assert line in text, line
    assert [b.key for b in visitor.at.button] == ["pd2_start"] and visitor.at.button(key="pd2_start").label == "Start Demo"
    for jargon in ("schema", "Stage ", "test", "BAEC_ENGINE_2", "classifier"):
        assert jargon not in "\n".join(m.value for m in (*visitor.at.title, *visitor.at.subheader, *visitor.at.markdown))


def test_step_one_shows_the_confirmed_buyer_condition_exactly():
    visitor = Visitor().click("pd2_start")
    text = visitor.text()
    assert release.stage_j.BUYER_STATEMENT in [t.value for t in visitor.at.text]
    assert "**More than 8%**" in text and "**July renewal**" in text and ":green-badge[CONFIRMED]" in text
    assert "8 %" not in text  # presentation formatting; the stored unit is unchanged
    assert "Alder Ridge Clinics (synthetic)" in text and copy.CONDITION_NOT_INTENT in text
    assert visitor.at.button(key="pd2_to_2").label == "Continue to Monitoring"


def test_step_two_explains_authorized_monitoring():
    visitor = Visitor().click("pd2_start").click("pd2_to_2")
    text = visitor.text()
    assert copy.SOURCES_MATTER in text and "Supplier renewal notice" in text and "Supplier public announcement" in text
    assert "**A price increase above the buyer's 8% threshold at the July renewal.**" in text
    main = "\n".join(m.value for m in visitor.at.main.markdown)
    assert "Entity match" not in main  # the dimension list sits in Technical details, not on the main surface
    details = next(e for e in visitor.at.expander if e.label == "Technical details")
    assert "Entity match" in "\n".join(t.value for t in details.text) and "MP-RC-001" in "\n".join(t.value for t in details.text)
    assert visitor.at.button(key="pd2_to_3").label == "See New Evidence"


def test_step_three_separates_current_evidence_from_history():
    visitor = Visitor().click("pd2_start").click("pd2_to_2").click("pd2_to_3")
    current, history = visitor.at.tabs
    current_text = "\n".join([m.value for m in current.markdown] + [t.value for t in current.text])
    history_text = "\n".join([m.value for m in history.markdown] + [t.value for t in history.text]
                             + [c.value for c in history.caption])
    assert copy.HISTORY_LINE in visitor.text()
    assert "Price change: 9.00%" in current_text and "12.00" not in current_text
    first = [m.value for m in current.markdown]
    assert first[0] == "**Price change: 9.00%**"  # the usable measurement comes first
    assert current.text[1].value.startswith("Correction to our renewal notice")  # then its corrected source notice
    other = next(e for e in current.expander if e.label == "Other current context")
    assert {c.value for c in other.caption} >= {"OBS-DEMO-03", "OBS-DEMO-05"}
    assert "SUPERSEDED" not in current_text and "RETRACTED" not in current_text and "REFUSED" not in current_text
    for status in ("CURRENT / USABLE", "SUPERSEDED", "RETRACTED", "REFUSED", "INVALIDATED MEASUREMENT"):
        assert status in history_text, status
    assert "Price change: 12.00%" in history_text and "ITEM-DEMO-FORUM-01" in history_text
    text = visitor.text()
    for line in ("Buyer threshold", "**More than 8%**", "Current usable measurement", "**9.00%**", "Relevant timing",
                 "**July renewal**", "12.00% initially reported → corrected → **9.00% current**",
                 "12.00% is kept in the history as an invalidated measurement. It is not used.", copy.SYNTHETIC_DATES):
        assert line in text, line
    texts = [t.value for t in history.text]
    for _, _, _, _, content, _ in release.stage_j._ITEMS:
        assert content in texts  # exact stored text, unchanged
    assert visitor.at.button(key="pd2_to_4").label == "Review Possible Correspondence"


def test_step_three_lists_evidence_chronologically():
    visitor = Visitor().click("pd2_start").click("pd2_to_2").click("pd2_to_3")
    ids = [c.value for c in visitor.at.tabs[1].caption if re.fullmatch(r"OBS-DEMO-0\d", c.value)]
    assert ids == ["OBS-DEMO-04", "OBS-DEMO-05", "OBS-DEMO-03", "OBS-DEMO-01", "OBS-DEMO-02"]


# --- the human review ---------------------------------------------------------------------------------------------


def test_every_human_control_starts_unset_and_nothing_is_prefilled():
    visitor = Visitor().to_review()
    at = visitor.at
    for index, group in enumerate(copy.GROUPS):
        visitor.to_substep(index)
        for d in group.dimensions:
            assert at.button_group(key=f"pd2_finding_{d.name}").value is None
        assert at.multiselect(key=f"pd2_evidence_{group.key}").value == []
        assert not at.text_area(key=f"pd2_reason_{group.key}").value
    visitor.to_substep(3)
    for c in CrossCuttingCheckName:
        assert at.button_group(key=f"pd2_check_{c.name}").value is None
    assert at.button_group(key="pd2_sufficiency").value is None and at.button_group(key="pd2_revalidation").value is None
    assert not at.text_input(key="pd2_alias").value
    assert "Show walkthrough guidance" in [e.label for e in at.expander]
    assert visitor.at.session_state["pd2_answers"] == {k: v for k, v in visitor.at.session_state["pd2_answers"].items()
                                                       if v in (None, "", [])}


def test_walkthrough_guidance_never_sets_a_control():
    for line in copy.GUIDANCE:
        assert "pd2_" not in line
    code = re.sub(r'"""[\s\S]*?"""', "", (PACKAGE / "page.py").read_text(encoding="utf-8"))
    assigned = re.findall(r"(?<![\w.])(?:s|st\.session_state)\[[^\]]*\]\s*=(?!=)", code)
    assert not [a for a in assigned if "finding" in a or "evidence_" in a or "reason_" in a or "check_" in a]


def test_an_incomplete_review_cannot_submit():
    visitor = Visitor().to_review()
    assert "pd2_submit" not in [b.key for b in visitor.at.button]  # submission exists only on 4D
    visitor.to_substep(3).click("pd2_submit")
    assert len(visitor.at.session_state["pd2_missing"]) == 11 + 3 + 4 + 3
    visitor.to_substep(0).answer(sufficiency=None, skip=("UNIT_MATCH",)).click("pd2_submit")
    assert set(visitor.at.session_state["pd2_missing"]) == {"Unit match", "Evidence sufficiency"}
    assert "pd2_engine2_image" not in visitor.at.session_state and "pd2_result" not in visitor.at.session_state


@pytest.mark.parametrize("findings, sufficiency, expected", [
    ({}, "YES", "HUMAN_VERIFIED_CORRESPONDENCE"),
    ({}, "UNKNOWN", "POSSIBLE_CORRESPONDENCE"),
    ({D.UNIT_MATCH: "UNRESOLVED", D.THRESHOLD_MATCH: "UNRESOLVED"}, "YES", "POSSIBLE_CORRESPONDENCE"),
    ({D.ENTITY_MATCH: "CONTRADICTED"}, "YES", "NO_CORRESPONDENCE"),
])
def test_the_visitor_review_goes_through_stage_i_and_is_not_hard_coded(findings, sufficiency, expected):
    visitor = Visitor().to_review()
    digest = visitor.at.session_state["pd2_digest"]
    seed = visitor.seed()
    connection = p.open_engine2_database(":memory:")
    connection.deserialize(seed.engine2_image)
    assert packet_of(connection, seed).context_digest == digest  # the exact context the visitor opened
    connection.close()
    visitor.answer(findings=findings, sufficiency=sufficiency).click("pd2_submit")
    review_id, outcome = visitor.at.session_state["pd2_result"]
    assert outcome == expected and review_id == f"REV-{seed.candidate_id}-1"
    connection = visitor.store()
    stored = p.load_review(connection, review_id)
    assert stored.outcome.value == expected and stored.reviewer == "BP"
    assert {f.dimension: f.finding.value for f in stored.findings}[D.ENTITY_MATCH] == findings.get(D.ENTITY_MATCH,
                                                                                                    "SUPPORTED")
    assert [h.review.review_id for h in packet_of(connection, seed).review_history] == [review_id]
    p.verify_database(connection)
    connection.close()
    text = visitor.text()
    from baec_app.engine2.domain import CorrespondenceOutcome
    result = CorrespondenceOutcome(expected)
    assert [s.value for s in visitor.at.subheader] == [copy.OUTCOME_HEADLINES[result]]  # the public headline
    assert f"-badge[{expected}]" in text  # the locked value, shown secondarily and unchanged
    assert copy.OUTCOME_TEXT[result] in text
    for line in (*copy.NOT_MEAN, copy.BOUNDARY, "What this does NOT mean", copy.flow_line()):
        assert line in text, line
    assert copy.FLOW[-1] == "Verified correspondence"  # nothing after it implies an opportunity
    assert ("Why this matched" in text) is (result is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE)
    assert f"Recorded as {review_id}" not in "\n".join(t.value for t in visitor.at.main.text if t not in
                                                        [x for e in visitor.at.expander for x in e.text])


def test_a_review_the_locked_domain_refuses_is_explained_and_kept():
    visitor = Visitor().to_review().answer(findings={D.TIMING_MATCH: "SUPPORTED"}).click("pd2_submit")
    kind, text = visitor.at.session_state["pd2_error"]
    assert kind == "inconsistent" and "Timing match is not required by the plan" in text
    visitor.to_substep(2)
    assert visitor.at.button_group(key="pd2_finding_TIMING_MATCH").value == "SUPPORTED"  # kept for correction
    assert "pd2_result" not in visitor.at.session_state


def test_a_stale_context_is_refused_by_stage_i():
    visitor = Visitor().to_review().answer()
    visitor.at.session_state["pd2_digest"] = "0" * 64
    visitor.click("pd2_submit")
    assert visitor.at.session_state["pd2_error"][0] == "stale" and "pd2_result" not in visitor.at.session_state


def test_a_concern_requires_its_note_and_is_recorded():
    visitor = Visitor().to_review().answer()
    visitor.at.button_group(key="pd2_check_CONTRADICTORY_EVIDENCE").set_value("CONCERN")
    visitor.run().click("pd2_submit")
    assert visitor.at.session_state["pd2_missing"] == ["Contradictory evidence concern note"]
    visitor.at.text_area(key="pd2_check_note_CONTRADICTORY_EVIDENCE").input("The 3 May figure was corrected.")
    visitor.at.multiselect(key="pd2_check_evidence_CONTRADICTORY_EVIDENCE").select("OBS-DEMO-01")
    visitor.run().click("pd2_submit")
    connection = visitor.store()
    stored = p.load_review(connection, visitor.at.session_state["pd2_result"][0])
    connection.close()
    check = next(c for c in stored.checks if c.check is CrossCuttingCheckName.CONTRADICTORY_EVIDENCE)
    assert (check.note, check.evidence_refs) == ("The 3 May figure was corrected.", ("OBS-DEMO-01",))


def test_restart_produces_a_fresh_session():
    visitor = Visitor().to_review().answer().click("pd2_submit").click("pd2_restart")
    assert not [k for k in visitor.at.session_state if str(k).startswith("pd2_") and k != "pd2_start"]
    assert [b.key for b in visitor.at.button] == ["pd2_start"]
    visitor.click("pd2_start")
    seed = visitor.seed()
    connection = p.open_engine2_database(":memory:")
    connection.deserialize(seed.engine2_image)
    assert packet_of(connection, seed).review_history == ()
    connection.close()


def test_visitor_sessions_are_isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first, second = Visitor().to_review(), Visitor().to_review()
    first.answer().click("pd2_submit")
    assert "pd2_engine2_image" not in second.at.session_state
    assert second.seed().engine2_image == first.seed().engine2_image  # both started from the same fresh seed
    assert first.at.session_state["pd2_engine2_image"] != second.seed().engine2_image
    assert list(tmp_path.iterdir()) == []  # no database file anywhere


# --- seed: reuse of the released scenario, and Engine 1 isolation --------------------------------------------------


def test_the_seed_equals_the_release_demonstration_before_its_review():
    seed = seed_module.new_visitor_seed()
    engine1, engine2, reader = open_database(":memory:"), p.open_engine2_database(":memory:"), open_database(":memory:")
    try:
        baec_id = release.confirm_in_engine1(engine1)
        reader.deserialize(engine1.serialize())
        from baec_app.data.database import make_read_only
        make_read_only(reader)
        from baec_app.engine2.integration import engine1_handoff
        handoff = engine1_handoff.read_confirmed_baec(reader, baec_id)
        released = release.monitor_in_engine2(engine2, handoff)["packet"]
    finally:
        engine1.close(), engine2.close(), reader.close()
    connection = p.open_engine2_database(":memory:")
    connection.deserialize(seed.engine2_image)
    ours = packet_of(connection, seed)
    connection.close()
    assert ours == released and ours.context_digest == released.context_digest
    assert seed.handoff == handoff and seed.review_at == release.REVIEWED_AT


def test_engine1_is_unchanged_by_the_handoff_and_no_account_state_moves():
    seed = seed_module.new_visitor_seed()
    engine1 = open_database(":memory:")
    try:
        release.confirm_in_engine1(engine1)
        assert engine1.serialize() == seed.engine1_image
    finally:
        engine1.close()
    visitor = Visitor().to_review().answer().click("pd2_submit")
    assert visitor.seed().engine1_image == seed.engine1_image  # the walkthrough never touches Engine 1
    engine1 = open_database(":memory:")
    engine1.deserialize(seed.engine1_image)
    assert engine1.execute("SELECT COUNT(*) FROM account_state_transitions").fetchone()[0] == 0
    assert engine1.execute("SELECT state FROM accounts").fetchall() == [(None,)]
    engine1.close()


# --- static boundaries ------------------------------------------------------------------------------------------


def _sources():
    files = sorted(PACKAGE.glob("*.py")) + [SEED, ENTRY]
    return {path.name: path.read_text(encoding="utf-8") for path in files}


def _code(text):
    return re.sub(r'"""[\s\S]*?"""', "", text)


def test_the_page_reads_and_writes_only_through_stage_i():
    code = _code(_sources()["page.py"])
    assert "review.build_review_packet(" in code and "review.submit_human_review(" in code
    for forbidden in ("record_review", "classify_correspondence", "HumanCorrespondenceReview(", "EvidenceLedger(",
                      "execute(", "INSERT", "UPDATE", "DELETE", "attach_advisory_proposal", "ai_persistence",
                      "build_correspondence_ai_input"):
        assert forbidden not in code, forbidden
    assert set(re.findall(r"(?<!\w)store\.(\w+)\(", code)) == {"open_engine2_database", "require_engine2_schema",
                                                                "load_authorized_source"}


def test_no_upload_conversation_input_credential_network_or_ai_path():
    for name, text in _sources().items():
        code = _code(text)
        for forbidden in ("file_uploader", "chat_input", "camera_input", "audio_input", "os.environ", "getenv",
                          "API_KEY", "AUTH_TOKEN", "anthropic", "import socket", "requests", "urllib", "http",
                          "subprocess", "open(", "write_text", "write_bytes", "sqlite3.connect", "propose_correspondence",
                          "datetime.now", "utcnow", "uuid", "random", "latest", "HBR-CORR", "expected_", "case_id"):
            assert forbidden not in code, (name, forbidden)
    inputs = re.findall(r"st\.(text_input|text_area)\(\s*(\"[^\"]*\"|f\"[^\"]*\")", _code(_sources()["page.py"]))
    assert sorted(label for _, label in inputs) == sorted(['"Describe the concern"', '"Reviewer alias"', '"Why (one reason per line)"',
                                                           '"Your reason"'])


def test_no_account_state_opportunity_outreach_or_crm_path():
    for name, text in _sources().items():
        code = _code(text)
        for forbidden in ("AccountState", "account_state", "persist_transition", "move_to_", "create_opportunity",
                          "send_email", "outreach(", "crm", "notify", "authorize_confirmation", "proposal_bridge"):
            assert forbidden not in code, (name, forbidden)
    labels = [b for b in re.findall(r'st\.button\(\s*"([^"]+)"', _code(_sources()["page.py"]))]
    assert sorted(labels) == sorted(["Start Demo", "Continue to Monitoring", "Back", "See New Evidence",
                                     "Review Possible Correspondence", "Back", "Next", "Submit Human Review",
                                     "Restart Demo"])


def test_the_public_package_imports_only_engine2():
    for name in ("content.py", "page.py", "__init__.py"):
        tree = ast.parse(_sources()[name])
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert modules <= {"__future__", "dataclasses", "typing", "streamlit"}, (name, modules)


def test_public_text_makes_no_intent_likelihood_or_action_claim():
    sensitive = ("intent", "purchase", "likel", "probab", "confidence", "score", "lead", "opportunit", "contact",
                 "outreach", "qualified", "predict", "prospect")
    negation = re.compile(r"\b(does not|do not|not|never|no|nothing|cannot)\b", re.I)
    for name in ("content.py", "page.py"):
        tree = ast.parse(_sources()[name])
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docs]
        for s in strings:
            for sentence in re.split(r"(?<=[.!?])\s+", s):
                for word in sensitive:
                    if word in sentence.lower():
                        assert negation.search(sentence), (name, sentence)
    assert "—" not in "".join(_sources().values())


def test_the_stage_j_operator_workflow_and_released_files_are_unchanged():
    try:
        result = subprocess.run(["git", "diff", "--quiet", "baec-engine2-v1.0", "--", *STAGE_J, "baec_app/engine2/review.py",
                                 "baec_app/engine2/integration", "scripts/engine2_release_demo.py"],
                                cwd=REPO, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not available")
    if result.returncode not in (0, 1):
        pytest.skip("the v1.0 tag is not available in this checkout")
    assert result.returncode == 0


# --- presentation pass: substeps, drafts, progress, formatting ---------------------------------------------------


def test_answers_survive_moving_between_substeps_and_the_digest_never_changes():
    visitor = Visitor().to_review()
    digest = visitor.at.session_state["pd2_digest"]
    visitor.answer_group(copy.GROUPS[0], findings={D.ENTITY_MATCH: "UNRESOLVED"}).click("pd2_sub_next")
    visitor.answer_group(copy.GROUPS[1]).click("pd2_sub_next").click("pd2_sub_next")
    visitor.answer_quality(alias="AB")
    visitor.to_substep(0)
    at = visitor.at
    assert at.button_group(key="pd2_finding_ENTITY_MATCH").value == "UNRESOLVED"
    assert at.button_group(key="pd2_finding_RELATIONSHIP_MATCH").value == "NOT_APPLICABLE"
    assert at.multiselect(key="pd2_evidence_subject").value == ["OBS-DEMO-02"]
    assert at.text_area(key="pd2_reason_subject").value == "Visitor reasoning about supplier and event."
    visitor.to_substep(1)
    assert at.multiselect(key="pd2_evidence_threshold").value == ["DM-DEMO-02"]
    visitor.to_substep(2)
    assert at.button_group(key="pd2_finding_TIMING_MATCH").value is None  # 4C was skipped and stays unanswered
    visitor.to_substep(3)
    assert at.text_input(key="pd2_alias").value == "AB" and at.button_group(key="pd2_sufficiency").value == "YES"
    assert visitor.at.session_state["pd2_digest"] == digest and "pd2_result" not in visitor.at.session_state
    visitor.to_substep(2).answer_group(copy.GROUPS[2]).to_substep(3).click("pd2_submit")
    assert visitor.at.session_state["pd2_result"][1] == "POSSIBLE_CORRESPONDENCE"  # entity left unresolved


def test_the_progress_indicator_counts_answers_only():
    visitor = Visitor().to_review()

    def progress():
        (element,) = visitor.at.get("progress")
        return element.proto.text
    assert progress() == "0 of 11 findings completed · 0 of 4 quality checks"
    visitor.answer_group(copy.GROUPS[0]).click("pd2_sub_next")
    assert progress() == "4 of 11 findings completed · 0 of 4 quality checks"
    code = re.sub(r'"""[\s\S]*?"""', "", (PACKAGE / "page.py").read_text(encoding="utf-8"))
    progress = code[code.index("findings_done ="):code.index("st.progress(")]
    assert "submit" not in progress and "review." not in progress


def test_restoring_a_draft_uses_only_the_visitors_own_answers():
    code = re.sub(r'"""[\s\S]*?"""', "", (PACKAGE / "page.py").read_text(encoding="utf-8"))
    restore = code[code.index("def _restore("):code.index("def _substep(")]
    assert 'answers = st.session_state, st.session_state.get(KEY + "answers", {})' in restore
    for forbidden in ("GUIDANCE", "proposal", "SUPPORTED", "default", "packet"):
        assert forbidden not in restore, forbidden


def test_presentation_formatting_never_changes_stored_values():
    from decimal import Decimal
    assert copy.percent(Decimal("9.00"), "%") == "9.00%" and copy.percent(Decimal("12.00"), "%") == "12.00%"
    assert copy.threshold_phrase("GREATER_THAN", Decimal("8"), "%") == "More than 8%"
    assert copy.percent(Decimal("5"), "points") == "5 points"
    assert copy.watching_for("GREATER_THAN", Decimal("8"), "%", "at the July renewal") == (
        "A price increase above the buyer's 8% threshold at the July renewal.")
    assert copy.timing_label("at the July renewal") == "July renewal"
    seed = seed_module.new_visitor_seed()
    assert (seed.handoff.stringency.unit, str(seed.handoff.stringency.numeric_value)) == ("%", "8")


def test_the_footer_and_the_guidance_follow_the_presentation_pass():
    visitor = Visitor()
    assert "BAEC Trigger Intelligence | Research prototype | Bodhi Parks" in visitor.text()
    guidance = " ".join(copy.GUIDANCE)
    for point in ("corrected renewal notice", "9.00%", "more than 8%", "July renewal", "Not applicable",
                  "never answers a question for you"):
        assert point in guidance, point


# --- final presentation pass: completion states, labels, result summary ------------------------------------------


def _nav(visitor):
    return [m.value for m in visitor.at.markdown if re.search(r"4[A-D]", m.value)]


def test_a_substep_is_checked_only_when_fully_answered():
    visitor = Visitor().to_review()
    assert _nav(visitor) == [":blue[**● 4A Supplier**]", ":gray[○ 4B Threshold]", ":gray[○ 4C Timing]",
                             ":gray[○ 4D Conclusion]"]
    visitor.click("pd2_sub_next").click("pd2_sub_next")  # visited 4A and 4B without answering
    assert _nav(visitor) == [":gray[○ 4A Supplier]", ":gray[○ 4B Threshold]", ":blue[**● 4C Timing**]",
                             ":gray[○ 4D Conclusion]"]
    visitor.to_substep(0)
    group = copy.GROUPS[0]
    for d in group.dimensions:
        default = "SUPPORTED" if d in release.CHOICES.required_dimensions else "NOT_APPLICABLE"
        visitor.at.button_group(key=f"pd2_finding_{d.name}").set_value(default)
    visitor.at.text_area(key="pd2_reason_subject").input("A reason.")
    visitor.run()
    assert _nav(visitor)[0] == ":blue[**● 4A Supplier**]"  # decided findings still need cited evidence
    visitor.at.multiselect(key="pd2_evidence_subject").select("OBS-DEMO-02")
    visitor.run()
    assert _nav(visitor)[0] == ":green[**✓ 4A** Supplier]"
    visitor.to_substep(3).answer_quality(alias=None)
    assert _nav(visitor)[3] == ":blue[**● 4D Conclusion**]"  # the alias is still missing
    visitor.at.text_input(key="pd2_alias").input("BP")
    visitor.run()
    assert _nav(visitor)[3] == ":green[**✓ 4D** Conclusion]"


def test_substep_completion_is_display_only():
    values = {"finding:ENTITY_MATCH": "UNRESOLVED", "finding:RELATIONSHIP_MATCH": "NOT_APPLICABLE",
              "finding:SOURCE_RELEVANCE": "UNRESOLVED", "finding:EVENT_OR_CONDITION_TYPE": "UNRESOLVED",
              "reason:subject": "Unclear."}
    assert copy.substep_complete(values, 0)  # nothing decided, so no evidence is needed to count as answered
    assert not copy.substep_complete({**values, "finding:ENTITY_MATCH": "SUPPORTED"}, 0)
    code = re.sub(r'"""[\s\S]*?"""', "", (PACKAGE / "content.py").read_text(encoding="utf-8"))
    body = code[code.index("def substep_complete("):code.index("def comparator_phrase(")]
    assert "review." not in body and "submit" not in body


def test_navigation_labels_progress_heading_and_card_headings():
    visitor = Visitor().to_review()
    text = visitor.text()
    assert "**Review progress**" in text
    assert [s.value for s in visitor.at.subheader] == ["4A  Supplier and event"]
    for index, heading in enumerate(copy.SUBSTEPS):
        visitor.to_substep(index)
        assert heading in [s.value for s in visitor.at.subheader]
    assert [label.split(" ", 1)[1] for label in copy.SUBSTEP_NAV] == ["Supplier", "Threshold", "Timing", "Conclusion"]


def test_the_successful_result_explains_why_from_authoritative_values():
    visitor = Visitor().to_review().answer().click("pd2_submit")
    text = visitor.text()
    for line in ("**Why this matched**", "Buyer threshold", "**More than 8%**", "Current usable evidence",
                 "**9.00%**", "Relevant timing", "**July renewal**", copy.WHY_MATCHED):
        assert line in text, line
    details = next(e for e in visitor.at.expander if e.label == "Technical details")
    assert "Recorded as REV-CC-RC-001-1." in [t.value for t in details.text]
    flow = copy.flow_line()
    assert flow.endswith("Verified\u00a0correspondence") and flow.count(" → ") == len(copy.FLOW) - 1
    code = re.sub(r'"""[\s\S]*?"""', "", (PACKAGE / "page.py").read_text(encoding="utf-8"))
    result = code[code.index("def _result("):code.index("def main(")]
    for literal in ('"8"', "9.00", "July", "Decimal("):
        assert literal not in result, literal  # read from the handoff and the packet, never a second copy
