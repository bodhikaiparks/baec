"""Stage J: the Engine 2 operator workflow (baec_app/engine2/operator_ui), tested offline with Streamlit AppTest.

Every run uses the fixed synthetic demo store in memory. Real sockets are blocked, credentials are removed, and no
model, provider, or network is reachable. AI advisory artifacts are predetermined replay text recorded through the
locked Stage G store. These are software tests of interface behavior, not evidence for the theory.
"""

import ast
import json
import re
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from baec_app.engine2 import ai_persistence as a
from baec_app.engine2 import persistence as p
from baec_app.engine2 import review
from baec_app.engine2.ai import (OUTPUT_SCHEMA_VERSION, PROHIBITED_INFERENCE_ACKNOWLEDGEMENT, ModelResponse,
                                 build_correspondence_ai_input)
from baec_app.engine2.domain import CorrespondenceDimension as D
from baec_app.engine2.domain import CrossCuttingCheckName
from baec_app.engine2.operator_ui import demo, page, views

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "baec_app" / "engine2" / "operator_ui"
ENTRY = REPO / "streamlit_engine2_app.py"
T = datetime(2027, 6, 2, tzinfo=timezone.utc)
REQUIRED = [d for d in D if d in demo.PLAN.required_dimensions]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _script(advisory=None):
    from datetime import datetime, timezone

    from baec_app.engine2.operator_ui.page import main
    main(advisory=advisory, clock=lambda: datetime(2027, 6, 2, 12, 0, tzinfo=timezone.utc))


class Operator:
    """One operator session over the real page, driven like a person would."""

    def __init__(self, advisory=None):
        self.at = AppTest.from_function(_script, kwargs={"advisory": advisory}, default_timeout=60)
        self.run()

    def run(self):
        self.at.run()
        assert not self.at.exception, [e.value for e in self.at.exception]
        return self

    def go(self, step):
        self.at.sidebar.radio(key="e2_step").set_value(step)
        return self.run()

    def texts(self):
        return [t.value for t in self.at.text]

    def all_text(self):
        parts = [t.value for t in self.at.text] + [m.value for m in self.at.markdown] + [c.value for c in self.at.caption]
        parts += [e.value for e in self.at.error] + [w.value for w in self.at.warning] + [i.value for i in self.at.info]
        parts += [h.value for h in self.at.header] + [s.value for s in self.at.subheader]
        return "\n".join(parts)

    def fill(self, *, findings=None, sufficiency="YES", revalidation="Not required", reviewer="Dana Reviewer (synthetic)",
             skip=()):
        at = self.at
        findings = findings or {}
        for d in D:
            if d.name in skip:
                continue
            value = findings.get(d, "SUPPORTED" if d in REQUIRED else "NOT_APPLICABLE")
            at.selectbox(key=f"e2_finding_{d.name}").select(value)
            if value in ("SUPPORTED", "CONTRADICTED"):
                at.multiselect(key=f"e2_evidence_{d.name}").select("OBS-DEMO-02")
                if d in (D.NUMERIC_MAGNITUDE, D.THRESHOLD_MATCH):
                    at.multiselect(key=f"e2_evidence_{d.name}").select("DM-DEMO-02")
            at.text_area(key=f"e2_reason_{d.name}").input(f"Reviewer reasoning for {d.label}.")
        for c in CrossCuttingCheckName:
            at.text_area(key=f"e2_check_note_{c.name}").input(f"No {c.label.lower()} concern noted.")
        if sufficiency:
            at.radio(key="e2_sufficiency").set_value(sufficiency)
        if revalidation:
            at.radio(key="e2_revalidation").set_value(revalidation)
        if reviewer:
            at.text_input(key="e2_reviewer").input(reviewer)
        return self.run()

    def submit(self):
        self.at.button(key="e2_submit").click()
        return self.run()

    def store(self):
        return demo.open_image(self.at.session_state["e2_image"])


def demo_packet(connection):
    s = demo.SELECTION
    return review.build_review_packet(connection, monitoring_plan_id=s.monitoring_plan_id, plan_version=s.plan_version,
                                      baec_id=s.baec_id, candidate_id=s.candidate_id, activation_id=s.activation_id)


def proposal_text(ai_input):
    proposals = []
    for d in D:
        if d not in demo.PLAN.required_dimensions:
            proposals.append({"dimension": d.label, "proposal": "PROPOSED_NOT_APPLICABLE", "unresolved_basis": None,
                              "evidence": [{"kind": "plan_dimension", "dimension": d.label}],
                              "rationale": "The plan marks this dimension not applicable."})
        elif d is D.NUMERIC_MAGNITUDE:
            proposals.append({"dimension": d.label, "proposal": "PROPOSED_SUPPORTED", "unresolved_basis": None,
                              "evidence": [{"kind": "measurement", "measurement_id": "DM-DEMO-02",
                                            "observation_ids": ["OBS-DEMO-02", "OBS-DEMO-02"]}],
                              "rationale": "The corrected notice yields a 9.00 percent change."})
        else:
            proposals.append({"dimension": d.label, "proposal": "PROPOSED_UNRESOLVED", "unresolved_basis": "MISSING_EVIDENCE",
                              "evidence": [], "rationale": "No accepted evidence establishes this dimension."})
    return json.dumps({"schema_version": OUTPUT_SCHEMA_VERSION, "monitoring_plan_id": ai_input.monitoring_plan_id,
                       "plan_version": ai_input.plan_version, "baec_id": ai_input.baec_id,
                       "candidate_id": ai_input.candidate_id, "input_digest": ai_input.input_digest,
                       "dimension_proposals": proposals, "cross_cutting_observations": [],
                       "unresolved_questions": ["Does the corrected fee apply to the whole contract?"],
                       "prohibited_inference_acknowledgement": PROHIBITED_INFERENCE_ACKNOWLEDGEMENT})


def advisory_image(*, raw=None, condition=None):
    connection = demo.open_image(demo.new_demo_image())
    packet = demo_packet(connection)
    connection.close()
    ai_input = build_correspondence_ai_input(condition or demo.CONDITION, packet.candidate, packet.ledger)
    ai = a.open_ai_audit_database(":memory:")
    a.record_ai_input(ai, ai_input, recorded_at=T, recorded_by="FIXTURE")
    a.record_ai_attempt(ai, attempt_id="ATT-DEMO-1", input_digest=ai_input.input_digest, provider="replay",
                        model="fixture-replay/v1", requested_at=T, requested_by="FIXTURE")
    a.record_ai_response(ai, "ATT-DEMO-1", ModelResponse(proposal_text(ai_input) if raw is None else raw, "replay",
                                                         "fixture-replay/v1"), received_at=T, recorded_by="FIXTURE")
    status = a.record_ai_validation(ai, "ATT-DEMO-1", artifact_id="AIP-DEMO-1", created_at=T, validated_at=T,
                                    validated_by="FIXTURE").status
    image = ai.serialize()
    ai.close()
    return page.AdvisorySource(image, "ATT-DEMO-1", "AIP-DEMO-1"), status


# --- overview, context, evidence, candidate ------------------------------------------------------------------


def test_the_overview_explains_engine2_narrowly():
    op = Operator()
    text = op.all_text()
    assert views.QUESTION in text and views.BOUNDARY in text and "synthetic" in text
    assert "does not rank or rate accounts, infer buyer intent, predict purchases, or contact anyone" in text
    assert [b.key for b in op.at.button] == ["e2_start"]


def test_the_monitoring_context_renders_the_locked_plan():
    op = Operator().go("Monitoring context")
    text = op.all_text()
    for expected in (demo.BAEC_ID, demo.PLAN_ID, "version 1", demo.ACTIVATION_ID, "ACTIVATED", demo.BUYER_STATEMENT,
                     "GREATER_THAN 8 %", "At the contract renewal on 1 July 2027", "FACT-DEMO-RENEWAL-DATE-2027-07-01",
                     "RULE-DEMO-CONTRACT-NOTICE-OVER-ANNOUNCEMENT", demo.NOTICES, demo.NEWS,
                     "It has not shown that the buyer's condition occurred", "Not applicable: Relationship match"):
        assert expected in text, expected


def test_exact_observation_text_renders_unchanged_and_history_is_distinguished():
    op = Operator().go("Evidence")
    texts = op.texts()
    for _, _, _, _, content, _ in demo._ITEMS:
        assert texts.count(content) == 2 or content in texts  # usable ones appear twice (current and historical)
    statuses = "\n".join(texts)
    assert "OBS-DEMO-01 [SUPERSEDED]; superseded by OBS-DEMO-02" in statuses
    assert "OBS-DEMO-04 [RETRACTED]; retracted by OBS-DEMO-05" in statuses
    for usable in ("OBS-DEMO-02 [USABLE]", "OBS-DEMO-03 [USABLE]", "OBS-DEMO-05 [USABLE]"):
        assert usable in statuses
    headings = [s.value for s in op.at.subheader]
    assert headings.index("Currently usable evidence") < headings.index("Stored historical record")
    current = texts[:texts.index(next(t for t in texts if t.startswith("OBS-DEMO-01")))]
    assert not any("[SUPERSEDED]" in t or "[RETRACTED]" in t for t in current)


def test_usability_labels_come_from_the_stage_c_ledger():
    connection = demo.open_image(demo.new_demo_image())
    packet = demo_packet(connection)
    connection.close()
    rows = views.evidence_rows(packet)
    assert {r.observation_id for r in rows if r.status == "USABLE"} == packet.ledger.usable_observation_ids
    assert {m.measurement_id for m in views.measurement_rows(packet) if m.status == "USABLE"} == \
        packet.ledger.usable_measurement_ids


def test_derived_measurements_inputs_and_refusals_render():
    text = "\n".join(Operator().go("Evidence").texts())
    assert "DM-DEMO-01 [INVALIDATED]: 12.00 %" in text and "DM-DEMO-02 [USABLE]: 9.00 %" in text
    assert "input baseline_price = 40000 USD per year from OBS-DEMO-02" in text
    assert "input new_price = 43600 USD per year from OBS-DEMO-02" in text
    assert "ITEM-DEMO-FORUM-01: source SRC-DEMO-INDUSTRY-FORUM is not authorized for this plan" in text


def test_the_candidate_renders_without_opportunity_or_intent_claims():
    op = Operator().go("Candidate")
    text = op.all_text()
    assert views.CANDIDATE_MEANING in text and "SC-DEMO-001" in text and "OBS-DEMO-02, OBS-DEMO-03" in text
    assert "(not findings)" in text
    body = text.replace(views.BOUNDARY, "")
    for claim in ("opportunity", "intent", "qualified", "likely", "outreach", "ready"):
        assert claim not in body.lower(), claim


# --- packet, AI separation -------------------------------------------------------------------------------------


def test_the_review_packet_renders_from_stage_i_with_its_context_token():
    op = Operator().go("Review packet")
    connection = op.store()
    packet = demo_packet(connection)
    connection.close()
    assert op.at.session_state["e2_packet_digest"] == packet.context_digest
    text = op.all_text()
    assert f"Context token: {packet.context_digest}" in text
    assert "Threshold match: required by the plan" in text and "Timing match: not applicable under the plan" in text
    assert "Freshness, Contradictory evidence, Missing evidence, Provenance quality" in text
    assert "No human review has been recorded for this candidate." in text
    assert views.NO_ADVISORY in text  # no AI store at all: the workflow is complete without one


def test_a_valid_advisory_renders_separately_and_never_prefills_human_fields():
    advisory, status = advisory_image()
    assert status == "ACCEPTED"
    op = Operator(advisory).go("Review packet")
    text = op.all_text()
    assert views.AI_PANEL_TITLE in text and views.AI_PANEL_NOTE in text
    assert "AI proposal | Numeric magnitude: PROPOSED_SUPPORTED; cites DM-DEMO-02" in text
    assert "Origin AI_INFERENCE" in text and PROHIBITED_INFERENCE_ACKNOWLEDGEMENT in text
    op.go("Human review")
    for d in D:
        assert op.at.selectbox(key=f"e2_finding_{d.name}").value is None
        assert op.at.multiselect(key=f"e2_evidence_{d.name}").value == []
        assert not op.at.text_area(key=f"e2_reason_{d.name}").value
    assert op.at.radio(key="e2_sufficiency").value is None and op.at.radio(key="e2_revalidation").value is None
    assert "PROPOSED_" not in "".join(views.FINDING_OPTIONS)
    assert not [b for b in op.at.button if re.search(r"ai|accept", b.key or "", re.I)]


@pytest.mark.parametrize("variant", ["rejected", "mismatched", "corrupt store"])
def test_rejected_mismatched_or_corrupt_advice_is_never_shown_as_accepted(variant):
    if variant == "rejected":
        advisory, status = advisory_image(raw="{}")
        assert status == "REJECTED"
    elif variant == "mismatched":
        advisory, _ = advisory_image(condition=replace(demo.CONDITION, normalized_condition="another reading"))
    else:
        advisory = page.AdvisorySource(b"not an AI audit database", "ATT-DEMO-1", "AIP-DEMO-1")
    op = Operator(advisory).go("Review packet")
    text = op.all_text()
    assert "AI proposal |" not in text and "Origin AI_INFERENCE" not in text
    if variant == "corrupt store":
        assert "INTEGRITY FAILURE in the AI audit store" in text
    else:
        assert views.NO_ADVISORY in text
    op.go("Human review").fill().submit()  # the human workflow still completes
    assert op.at.session_state["e2_result"][1] == "HUMAN_VERIFIED_CORRESPONDENCE"


# --- human judgments and submission --------------------------------------------------------------------------


def test_every_required_answer_is_required_before_anything_is_submitted():
    op = Operator().go("Human review")
    op.submit()
    missing = op.at.session_state["e2_missing"]
    assert len(missing) == 11 * 2 + 4 + 3
    op.fill(sufficiency=None, skip=("ENTITY_MATCH",)).submit()
    assert set(op.at.session_state["e2_missing"]) == {"Entity match: finding", "Entity match: reason",
                                                      "Evidence sufficiency"}
    connection = op.store()
    assert p.review_ids_for_candidate(connection, demo.CANDIDATE_ID) == []
    connection.close()


def test_the_human_review_controls_start_unselected():
    op = Operator().go("Human review")
    assert all(op.at.selectbox(key=f"e2_finding_{d.name}").value is None for d in D)
    assert op.at.radio(key="e2_sufficiency").value is None and op.at.radio(key="e2_revalidation").value is None
    assert not op.at.text_input(key="e2_reviewer").value


@pytest.mark.parametrize("findings, sufficiency, expected", [
    ({}, "YES", "HUMAN_VERIFIED_CORRESPONDENCE"),
    ({}, "UNKNOWN", "POSSIBLE_CORRESPONDENCE"),
    ({D.ENTITY_MATCH: "CONTRADICTED"}, "YES", "NO_CORRESPONDENCE"),
])
def test_a_valid_submission_persists_through_stage_i_and_shows_its_result(findings, sufficiency, expected):
    op = Operator().go("Review packet")
    digest = op.at.session_state["e2_packet_digest"]
    op.go("Human review").fill(findings=findings, sufficiency=sufficiency).submit()
    assert op.at.session_state["e2_packet_digest"] == digest
    review_id, outcome = op.at.session_state["e2_result"]
    assert (review_id, outcome) == (f"REV-{demo.CANDIDATE_ID}-1", expected)
    connection = op.store()
    stored = p.load_review(connection, review_id)
    packet = demo_packet(connection)
    connection.close()
    assert stored.outcome.value == outcome and stored.reviewer == "Dana Reviewer (synthetic)"
    assert [h.review.review_id for h in packet.review_history] == [review_id]
    assert op.at.sidebar.radio(key="e2_step").value == "Result"
    text = op.all_text()
    assert expected in [s.value for s in op.at.subheader]
    assert views.OUTCOME_EXPLANATIONS[stored.outcome] in text and views.RESULT_BOUNDARY in text
    assert f"{review_id} by Dana Reviewer (synthetic)" in text


def test_reviews_accumulate_as_history():
    op = Operator().go("Human review").fill().submit()
    op.go("Human review")
    assert op.at.selectbox(key="e2_finding_ENTITY_MATCH").value is None  # a fresh form: nothing carried over
    op.fill(sufficiency="UNKNOWN").submit()  # a second, separate review
    connection = op.store()
    assert p.review_ids_for_candidate(connection, demo.CANDIDATE_ID) == [f"REV-{demo.CANDIDATE_ID}-1",
                                                                         f"REV-{demo.CANDIDATE_ID}-2"]
    connection.close()
    text = op.go("Result").all_text()
    assert f"REV-{demo.CANDIDATE_ID}-1 by" in text and f"REV-{demo.CANDIDATE_ID}-2 by" in text


def add_late_observation(op):
    """Another capture lands in the operator's store: a new immutable record, so a new context_digest."""
    connection = op.store()
    extra = replace(p.load_observation(connection, "OBS-DEMO-03"), observation_id="OBS-DEMO-LATE-ARRIVAL",
                    evidence_event_id="EVENT-LATE-ARRIVAL")
    p.record_observation(connection, extra, activation_id=demo.ACTIVATION_ID, recorded_at=T, recorded_by="FIXTURE")
    op.at.session_state["e2_image"] = connection.serialize()
    connection.close()


def stored_reviews(op):
    connection = op.store()
    ids = p.review_ids_for_candidate(connection, demo.CANDIDATE_ID)
    connection.close()
    return ids


def test_a_stale_context_token_is_refused_cleanly():
    op = Operator().go("Human review").fill()
    add_late_observation(op)  # the record changes after the draft was written, before it is submitted
    op.submit()
    assert op.at.session_state["e2_error"][0] == "stale"
    assert "changed after you opened this review packet. Nothing was recorded." in op.all_text()
    assert stored_reviews(op) == [] and "e2_result" not in op.at.session_state


def test_an_inconsistent_human_review_is_explained_and_not_recorded():
    op = Operator().go("Human review").fill(findings={D.TIMING_MATCH: "SUPPORTED"}).submit()
    kind, text = op.at.session_state["e2_error"]
    assert kind == "inconsistent" and "Timing match is not required by the plan" in text
    connection = op.store()
    assert p.review_ids_for_candidate(connection, demo.CANDIDATE_ID) == []
    connection.close()


def test_no_control_submits_an_outcome_and_no_seller_action_follows_any_result():
    for findings, sufficiency in (({}, "YES"), ({}, "UNKNOWN"), ({D.ENTITY_MATCH: "CONTRADICTED"}, "YES")):
        op = Operator().go("Human review").fill(findings=findings, sufficiency=sufficiency).submit()
        keys = [w.key for w in [*op.at.button, *op.at.radio, *op.at.selectbox, *op.at.text_input, *op.at.text_area]]
        assert not [k for k in keys if k and re.search(r"outcome|classif|result|verdict", k)]
        assert not [b.label for b in op.at.button
                    if re.search(r"contact|email|call|opportunit|crm|stage|notify|sequence|outreach|reactivat", b.label, re.I)]
        assert views.RESULT_BOUNDARY in op.all_text()


# --- negative paths ------------------------------------------------------------------------------------------


def test_a_corrupted_store_is_reported_as_an_integrity_failure():
    op = Operator()
    connection = op.store()
    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'observations'"
                                      ).fetchall():
        connection.execute(f"DROP TRIGGER {name}")
    connection.execute("UPDATE observations SET exact_evidence_content = 'altered' WHERE observation_id = 'OBS-DEMO-02'")
    op.at.session_state["e2_image"] = connection.serialize()
    connection.close()
    for step in ("Evidence", "Review packet", "Human review", "Result"):
        text = op.go(step).all_text()
        assert "INTEGRITY FAILURE: a stored record failed verification. Nothing was recorded, and this is not a review " \
               "result." in text
        assert "altered" not in op.texts()
        assert not [s.value for s in op.at.subheader if s.value.endswith("CORRESPONDENCE")]


@pytest.mark.parametrize("change", [{"candidate_id": "CC-DEMO-UNKNOWN"}, {"plan_version": 2},
                                    {"monitoring_plan_id": "MP-DEMO-OTHER"}, {"baec_id": "BAEC-DEMO-OTHER"},
                                    {"activation_id": "ACT-DEMO-UNKNOWN"}])
def test_a_wrong_selection_fails_closed_with_an_operator_message(monkeypatch, change):
    monkeypatch.setattr(page, "SELECTION", replace(demo.SELECTION, **change))
    op = Operator().go("Review packet")
    assert "The review context could not be opened. Nothing was recorded." in op.all_text()
    assert "e2_packet_digest" not in op.at.session_state


def test_the_entry_point_runs_the_real_page():
    at = AppTest.from_file(str(ENTRY), default_timeout=60).run()
    assert not at.exception and views.QUESTION in [s.value for s in at.subheader]


def test_each_session_gets_its_own_deterministic_in_memory_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first, second = Operator(), Operator()
    assert first.at.session_state["e2_image"] == second.at.session_state["e2_image"] == demo.new_demo_image()
    first.go("Human review").fill().submit()
    assert second.at.session_state["e2_image"] == demo.new_demo_image()
    assert list(tmp_path.iterdir()) == []  # nothing is written to disk


# --- static boundaries and text audit ------------------------------------------------------------------------


def _sources():
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(PACKAGE.glob("*.py"))} | \
        {ENTRY.name: ENTRY.read_text(encoding="utf-8")}


def _code(text):
    return re.sub(r'"""[\s\S]*?"""', "", text)


def _imports(text):
    tree = ast.parse(text)
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return names | {("." * n.level) + (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}


def test_the_page_reads_and_writes_only_through_stage_i():
    code = _code(_sources()["page.py"])
    assert "review.build_review_packet(" in code and "review.submit_human_review(" in code
    assert "review.attach_advisory_proposal(" in code
    for forbidden in ("record_review", "store.record_", "classify_correspondence", ".outcome(", "EvidenceLedger(",
                      "HumanCorrespondenceReview(", "load_review", "load_observation", "execute(", "INSERT", "UPDATE",
                      "DELETE", "ai_store.record_", "propose_correspondence", "validate_proposal_output"):
        assert forbidden not in code, forbidden
    assert re.findall(r"(?<!\w)store\.(\w+)\(", code) == ["load_authorized_source"]
    assert set(re.findall(r"ai_store\.(\w+)\(", code)) == {"open_ai_audit_database", "require_ai_schema"}


def test_views_restate_no_rule_and_write_nothing():
    code = _code(_sources()["views.py"])
    for forbidden in ("record_", "classify_correspondence", "required_dimensions -", "usable_observation_ids -",
                      "superseded_observation_id not in", "execute(", "streamlit"):
        assert forbidden not in code, forbidden
    assert "packet.usable_observation_ids" in code and "packet.usable_measurement_ids" in code


def test_the_demo_seed_writes_only_through_stage_d_recorders():
    code = _code(_sources()["demo.py"])
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE)\b", code)
    assert re.findall(r"execute\(([^)]*)\)", code) == ['"PRAGMA foreign_keys"']  # one read-only check
    assert set(re.findall(r"store\.(record_\w+)\(", code)) == {
        "record_authorized_source", "record_monitoring_plan", "record_plan_activation", "record_observation",
        "record_source_refusal", "record_supersession", "record_derived_measurement", "record_signal_candidate",
        "record_correspondence_candidate"}
    for oracle in ("HBR-CORR", "expected_", "case_id", "corpus", "tests."):
        assert oracle not in code, oracle


def test_no_provider_network_credential_account_state_or_outreach_path():
    for name, text in _sources().items():
        code = _code(text)
        imports = _imports(text)
        assert not {i for i in imports if i.split(".")[0] in ("anthropic", "httpx", "httpx2", "requests", "urllib",
                                                              "socket", "http", "subprocess", "mcp")}, name
        assert not {i for i in imports if i.startswith(("baec_app.ai", "baec_app.data", "baec_app.application",
                                                        "baec_app.mcp", "baec_app.interfaces", "tests"))}, name
        for forbidden in ("os.environ", "getenv", "API_KEY", "AUTH_TOKEN", "AccountState", "account_state",
                          "permission_to_contact", "latest", "eval(", "exec(", "float(", "time.time", "utcnow"):
            assert forbidden not in code, (name, forbidden)
    assert _code(_sources()["page.py"]).count("datetime.now(") == 1  # the caller-side clock, in _utc_now only
    assert "datetime.now(" not in _code(_sources()["views.py"]) + _code(_sources()["demo.py"])


def test_product_text_makes_no_intent_likelihood_or_action_claim():
    """Every user-facing string; a sensitive word may appear only inside a sentence that negates it."""
    sensitive = ("intent", "purchase", "likel", "probab", "confidence", "score", "lead", "opportunit", "contact",
                 "outreach", "qualified", "predict", "prospect", "hot ", "warm ")
    negation = re.compile(r"\b(does not|do not|not|never|no|nothing)\b", re.I)
    for name, text in _sources().items():
        tree = ast.parse(text)
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docstrings]
        strings += [p.value for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for p in n.values
                    if isinstance(p, ast.Constant)]
        for s in strings:
            for sentence in re.split(r"(?<=[.!?])\s+", s):
                for word in sensitive:
                    if word in sentence.lower():
                        assert negation.search(sentence), (name, sentence)
    assert "—" not in "".join(_sources().values())  # no em dashes in public copy


def test_the_engine2_ui_stays_inside_engine2_and_engine1_is_untouched():
    for path in (REPO / "baec_app").rglob("*.py"):
        if (REPO / "baec_app" / "engine2") not in path.parents:
            assert "engine2" not in path.read_text(encoding="utf-8"), path
    assert "operator_ui" not in (REPO / "streamlit_app.py").read_text(encoding="utf-8")


# --- review drafts (presentation state only) -------------------------------------------------------------------


PARTIAL = {D.ENTITY_MATCH: "SUPPORTED", D.TIMING_MATCH: "NOT_APPLICABLE"}


def answer_partially(op):
    at = op.at
    at.selectbox(key="e2_finding_ENTITY_MATCH").select("SUPPORTED")
    at.multiselect(key="e2_evidence_ENTITY_MATCH").select("OBS-DEMO-02").select("FACT-DEMO-RENEWAL-DATE-2027-07-01")
    at.text_area(key="e2_reason_ENTITY_MATCH").input("The notice names contract AR-2207 for this account.")
    at.selectbox(key="e2_finding_TIMING_MATCH").select("NOT_APPLICABLE")
    at.text_area(key="e2_check_note_FRESHNESS").input("Both notices are from May 2027.")
    at.multiselect(key="e2_check_evidence_FRESHNESS").select("OBS-DEMO-02")
    at.multiselect(key="e2_check_dimensions_FRESHNESS").select("NUMERIC_MAGNITUDE")
    at.radio(key="e2_sufficiency").set_value("UNKNOWN")
    at.radio(key="e2_revalidation").set_value("Required")
    at.text_area(key="e2_triggers").input("  The renewal date moved.\n\nThe buyer named a new supplier.")
    at.text_input(key="e2_reviewer").input("Dana Reviewer (synthetic)")
    return op.run()


EXPECTED_DRAFT = {
    "e2_finding_ENTITY_MATCH": "SUPPORTED",
    "e2_evidence_ENTITY_MATCH": ["OBS-DEMO-02", "FACT-DEMO-RENEWAL-DATE-2027-07-01"],
    "e2_reason_ENTITY_MATCH": "The notice names contract AR-2207 for this account.",
    "e2_finding_TIMING_MATCH": "NOT_APPLICABLE",
    "e2_check_note_FRESHNESS": "Both notices are from May 2027.",
    "e2_check_evidence_FRESHNESS": ["OBS-DEMO-02"],
    "e2_check_dimensions_FRESHNESS": ["NUMERIC_MAGNITUDE"],
    "e2_sufficiency": "UNKNOWN",
    "e2_revalidation": "Required",
    "e2_triggers": "  The renewal date moved.\n\nThe buyer named a new supplier.",
    "e2_reviewer": "Dana Reviewer (synthetic)",
}


def widget_values(op):
    at = op.at
    values = {}
    for d in D:
        values[f"e2_finding_{d.name}"] = at.selectbox(key=f"e2_finding_{d.name}").value
        values[f"e2_evidence_{d.name}"] = at.multiselect(key=f"e2_evidence_{d.name}").value
        values[f"e2_reason_{d.name}"] = at.text_area(key=f"e2_reason_{d.name}").value
    for c in CrossCuttingCheckName:
        values[f"e2_check_note_{c.name}"] = at.text_area(key=f"e2_check_note_{c.name}").value
        values[f"e2_check_evidence_{c.name}"] = at.multiselect(key=f"e2_check_evidence_{c.name}").value
        values[f"e2_check_dimensions_{c.name}"] = at.multiselect(key=f"e2_check_dimensions_{c.name}").value
    values["e2_sufficiency"] = at.radio(key="e2_sufficiency").value
    values["e2_revalidation"] = at.radio(key="e2_revalidation").value
    values["e2_triggers"] = at.text_area(key="e2_triggers").value
    values["e2_reviewer"] = at.text_input(key="e2_reviewer").value
    return values


def entered(values):
    return {k: v for k, v in values.items() if v not in (None, "", [])}


def test_a_partial_draft_survives_navigation_through_every_step():
    op = answer_partially(Operator().go("Human review"))
    assert entered(widget_values(op)) == EXPECTED_DRAFT
    for step in ("Overview", "Monitoring context", "Evidence", "Candidate", "Review packet", "Result", "Human review"):
        op.go(step)
    assert entered(widget_values(op)) == EXPECTED_DRAFT  # every major field, exactly as entered
    assert stored_reviews(op) == []


def test_a_completed_draft_survives_navigation_and_then_submits():
    op = Operator().go("Human review").fill(sufficiency="UNKNOWN")
    before = widget_values(op)
    op.go("Evidence").go("Candidate").go("Human review")
    assert widget_values(op) == before
    op.submit()
    assert op.at.session_state["e2_result"] == (f"REV-{demo.CANDIDATE_ID}-1", "POSSIBLE_CORRESPONDENCE")


def test_an_incomplete_submission_keeps_the_draft_for_correction():
    op = answer_partially(Operator().go("Human review")).submit()
    assert op.at.session_state["e2_missing"]
    assert entered(widget_values(op)) == EXPECTED_DRAFT
    op.go("Overview").go("Human review")
    assert entered(widget_values(op)) == EXPECTED_DRAFT


def test_a_malformed_submission_keeps_the_draft_for_correction():
    op = Operator().go("Human review").fill(findings={D.TIMING_MATCH: "SUPPORTED"}).submit()
    assert op.at.session_state["e2_error"][0] == "inconsistent"
    assert op.at.selectbox(key="e2_finding_TIMING_MATCH").value == "SUPPORTED"
    op.at.selectbox(key="e2_finding_TIMING_MATCH").select("NOT_APPLICABLE")
    op.at.multiselect(key="e2_evidence_TIMING_MATCH").unselect("OBS-DEMO-02")
    op.run().submit()
    assert op.at.session_state["e2_result"][1] == "HUMAN_VERIFIED_CORRESPONDENCE"


def test_a_changed_context_never_inherits_the_old_draft_and_says_so():
    op = answer_partially(Operator().go("Human review"))
    old_context = op.at.session_state["e2_form_context"]
    op.go("Evidence")
    add_late_observation(op)
    op.go("Human review")
    assert "The evidence context changed since your draft was started." in op.all_text()
    assert entered(widget_values(op)) == {}  # a fresh draft: nothing transferred
    new_context = op.at.session_state["e2_form_context"]
    assert new_context[:5] == old_context[:5] and new_context[5] != old_context[5]
    assert op.at.session_state["e2_drafts"][old_context]["e2_reason_ENTITY_MATCH"].startswith("The notice")  # isolated
    op.go("Overview").go("Human review")
    assert entered(widget_values(op)) == {}  # the old draft never returns into the changed context


def test_the_stale_draft_cannot_be_submitted_and_the_new_context_starts_blank():
    op = Operator().go("Human review").fill()
    add_late_observation(op)
    op.submit()
    assert op.at.session_state["e2_error"][0] == "stale" and stored_reviews(op) == []
    assert entered(widget_values(op)) == {}  # the form re-bound to the current record, blank
    op.fill().submit()  # answered afresh against the current record, under its own digest
    assert stored_reviews(op) == [f"REV-{demo.CANDIDATE_ID}-1"]


def test_a_successful_submission_retires_its_draft_and_the_next_review_is_independent():
    op = Operator().go("Human review").fill()
    context = op.at.session_state["e2_form_context"]
    op.submit()
    assert context not in op.at.session_state["e2_drafts"] and "e2_form_context" not in op.at.session_state
    assert stored_reviews(op) == [f"REV-{demo.CANDIDATE_ID}-1"]
    op.go("Result")
    assert f"REV-{demo.CANDIDATE_ID}-1 by Dana Reviewer (synthetic)" in op.all_text()
    op.go("Human review")
    assert entered(widget_values(op)) == {}  # nothing to resubmit
    op.submit()
    assert op.at.session_state["e2_missing"] and stored_reviews(op) == [f"REV-{demo.CANDIDATE_ID}-1"]
    op.fill(sufficiency="NO").submit()
    connection = op.store()
    first, second = (p.load_review(connection, f"REV-{demo.CANDIDATE_ID}-{n}") for n in (1, 2))
    connection.close()
    assert first.sufficiency.value == "YES" and second.sufficiency.value == "NO"  # the earlier review is unchanged


def test_drafts_are_isolated_by_candidate(monkeypatch):
    op = answer_partially(Operator().go("Human review"))
    connection = op.store()
    signal = p.load_signal_candidate(connection, "SC-DEMO-001")
    second = replace(p.load_correspondence_candidate(connection, demo.CANDIDATE_ID), candidate_id="CC-DEMO-002")
    p.record_correspondence_candidate(connection, second, recorded_at=T, recorded_by="FIXTURE")
    assert signal in second.signal_candidates
    op.at.session_state["e2_image"] = connection.serialize()
    connection.close()
    monkeypatch.setattr(page, "SELECTION", replace(demo.SELECTION, candidate_id="CC-DEMO-002"))
    op.go("Overview").go("Human review")
    assert entered(widget_values(op)) == {}
    monkeypatch.setattr(page, "SELECTION", demo.SELECTION)
    op.go("Overview").go("Human review")
    assert entered(widget_values(op)) == EXPECTED_DRAFT


def test_draft_contexts_bind_every_identity_field():
    connection = demo.open_image(demo.new_demo_image())
    packet = demo_packet(connection)
    connection.close()
    assert views.draft_context(packet, packet.context_digest) == (
        demo.PLAN_ID, demo.PLAN_VERSION, demo.BAEC_ID, demo.CANDIDATE_ID, demo.ACTIVATION_ID, packet.context_digest)
    assert views.draft_context(packet, "0" * 64) != views.draft_context(packet, packet.context_digest)
    assert len(views.DRAFT_KEYS) == 11 * 3 + 4 * 3 + 4 and len(set(views.DRAFT_KEYS)) == len(views.DRAFT_KEYS)


def test_no_draft_value_comes_from_ai_advice():
    advisory, _ = advisory_image()
    op = Operator(advisory).go("Review packet").go("Human review").go("Review packet").go("Human review")
    assert entered(widget_values(op)) == {}
    for draft in op.at.session_state["e2_drafts"].values():
        assert entered(draft) == {}
    code = _code(_sources()["page.py"])
    assert not re.search(r"state\[[^\]]*\]\s*=\s*[^\n]*(proposal|attached|advisory)", code)
    saved = re.search(r"def _save_draft[\s\S]*?\n\n\n", code).group(0)
    assert "views.DRAFT_KEYS" in saved and "proposal" not in saved and "advisory" not in saved


def test_stage_j_changes_no_domain_persistence_or_ai_module():
    import subprocess
    locked = [f"baec_app/engine2/{name}.py" for name in ("__init__", "errors", "domain", "measurement", "classification",
                                                         "persistence", "ai", "ai_persistence", "review")]
    try:
        result = subprocess.run(["git", "diff", "--quiet", "5501bec02c77cd9726689755bb5714360f98d09e", "--", *locked],
                                cwd=REPO, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not available")
    if result.returncode not in (0, 1):
        pytest.skip("the Stage I commit is not available in this checkout")
    assert result.returncode == 0, result.stderr
