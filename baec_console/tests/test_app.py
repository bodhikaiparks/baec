"""The Streamlit shell renders every page and workspace section without error (in-process, no browser, no server)."""

from __future__ import annotations

from datetime import timedelta
from html import escape
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _texts(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def _section(at: AppTest, label: str) -> None:
    matches = [
        b for b in at.button
        if b.label == label
        and (
            (b.key or "").startswith("section-nav-")
            or (b.key or "").startswith("record-nav-")
        )
    ]
    assert len(matches) == 1, (label, [(b.label, b.key) for b in at.button])
    matches[0].click().run()
    assert not at.exception, at.exception


def test_empty_today_renders(app):
    assert app.session_state["page"] == "Today"

    text = _texts(app)

    for label in (
        "Due today",
        "Overdue",
        "Human reviews",
        "Preparation work",
        "Accounts",
        "Tasks that need attention",
        "Decisions waiting on you",
        "Accounts to move",
    ):
        assert label in text

    assert any(
        c.value == "No live accounts yet."
        for c in app.caption
    )


@pytest.mark.parametrize("page", ["Accounts", "Review Queue", "Preparation", "Activity", "Dashboard"])
def test_every_page_renders_with_the_completed_example(app, page):
    _click(app, "seed-completed")
    _click(app, f"nav-{page}")


def test_completed_example_workspace_sections_render(app):
    _click(app, "seed-completed")
    text = _texts(app)
    assert "Prepared" in text
    for label in ("Send", "Ready to send", "Approved for outreach"):
        assert label not in [b.label for b in app.button]
    for section in ("Remember", "Monitor", "Recognize", "Prepare", "Timeline", "Account state"):
        _section(app, section)


def test_start_from_beginning_shows_capture(app):
    _click(app, "seed-beginning")
    assert "Buyer interactions" in _texts(app)
    _section(app, "Monitor")
    assert "Condition not confirmed" in _texts(app)
    _section(app, "Prepare")
    assert "Preparation not eligible" in _texts(app)


def test_start_from_call_notes_opens_the_account_with_no_interaction(app):
    _click(app, "seed-call-notes")
    assert "Riverside Outpatient Surgery Center" in _texts(app)
    _section(app, "Monitor")
    assert "Condition not confirmed" in _texts(app)


def test_reset_returns_to_an_empty_dashboard(app):
    _click(app, "seed-completed")
    _click(app, "seed-reset")
    assert "No accounts yet" in _texts(app)


def _open(tmp_path, monkeypatch, account_id: str, section: str) -> AppTest:
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = section
    at.run()
    assert not at.exception, at.exception
    return at


def test_ui_preview_then_human_confirmation(tmp_path, monkeypatch):
    from baec_console import demo_seed
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, add_interaction, new_account, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    at = _open(tmp_path, monkeypatch, account_id, "Remember")
    assert "Captured · not yet confirmed" in _texts(at)
    assert not [b for b in at.button if b.label == "Confirm buyer-articulated condition"]  # preview comes first
    _click(at, f"pv-{draft_id}")
    assert "Exactly what will be recorded" in _texts(at)
    _click(at, f"cf-{draft_id}")
    assert console.local.account_view(account_id).current_baec is not None


def test_ui_not_enough_evidence_records_a_review_that_does_not_verify(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, monitored, observe, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id = monitored(console)
    candidate_id = observe(console, account_id, baec_id).candidate_id
    at = _open(tmp_path, monkeypatch, account_id, "Recognize")
    assert at.button(key=f"{candidate_id}-verify").disabled  # nothing answered: cannot verify
    _click(at, f"{candidate_id}-insufficient")
    (review, outcome), = console.local.account_view(account_id).candidates[0]["reviews"]
    assert outcome.value == "POSSIBLE_CORRESPONDENCE"
    assert {f.finding.value for f in review.findings} <= {"UNRESOLVED", "NOT_APPLICABLE"}
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert "Preparation not eligible" in _texts(at)


def test_ui_refused_draft_is_shown_with_reasons_and_can_be_re_requested(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, permitted_request, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, _, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result, dispositions={}))
    console.local.generate_preparation(request_id)
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    selectors = [
        s for s in at.segmented_control
        if s.label == "Prepared artifact"
    ]
    assert len(selectors) == 1
    selectors[0].set_value("EMAIL DRAFT").run()
    assert not at.exception
    text = _texts(at)
    assert "Engine 3 validation refused this draft" in text
    assert escape("Refers to the buyer's statement without permission to do so.") in text
    assert any(x.label == "Request preparation again with different choices" for x in at.expander)
    assert not [b for b in at.button if b.label == "Accept as drafted" and "EMAIL" in (b.key or "")]


def test_today_gives_each_pending_artifact_review_its_own_control(tmp_path, monkeypatch):
    # Regression: one preparation's artifacts shared the key today-review-<account>-<preparation>,
    # which raised StreamlitDuplicateElementKey on Today.
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, permitted_request, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, _, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result))
    preparation_id = console.local.generate_preparation(request_id)
    pending = [r for r in console.review_queue()["pending"] if r["type"] == "Draft review"]
    assert len(pending) > 1
    assert {r["ref"] for r in pending} == {preparation_id}

    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["page"] == "Today"
    shown = [m.value for m in at.markdown if "Draft review · Awaiting human review" in m.value]
    assert len(shown) == len(pending)

    keys = [b.key for b in at.button if (b.key or "").startswith("today-review-")]
    assert len(keys) == len(set(keys)) == len(pending)
    expected = {f"today-review-{account_id}-{preparation_id}-{r['artifact_id']}": r for r in pending}
    assert set(keys) == set(expected)

    target = pending[-1]  # not the first pending artifact, which Prepare would select by default
    _click(at, f"today-review-{account_id}-{preparation_id}-{target['artifact_id']}")
    assert at.session_state["page"] == "Workspace"
    assert at.query_params["section"] == "Prepare"
    (selector,) = [s for s in at.segmented_control if s.label == "Prepared artifact"]
    assert selector.value == target["summary"].split(":")[0].upper()
    assert [b for b in at.button if (b.key or "") == f"{preparation_id}-{target['artifact_id']}-accept"]


def _two_prepared_correspondences(tmp_path):
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, observe, permitted_request, review, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id, older, older_review = verified(console)
    newer = observe(console, account_id, baec_id, new_price="44000")
    newer_review = review(console, newer.candidate_id).review.review_id
    preparations = {}
    for result, review_id in ((older, older_review), (newer, newer_review)):
        request_id = console.local.request_preparation(review_id, permitted_request(result))
        preparations[review_id] = console.local.generate_preparation(request_id)
    console.results = {older_review: older, newer_review: newer}
    return console, account_id, older_review, newer_review, preparations


def _assert_opened(at, account_id, review_id, preparation_id, other_preparation_ids, row):
    assert not at.exception, at.exception
    assert at.query_params["section"] == "Prepare"
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert box.value == review_id
    (selector,) = [s for s in at.segmented_control if s.label == "Prepared artifact"]
    assert selector.value == row["summary"].split(":")[0].upper()
    keys = {b.key for b in at.button}
    for control in ("accept", "edit", "reject"):
        assert f"{preparation_id}-{row['artifact_id']}-{control}" in keys
    assert not [k for k in keys for other in other_preparation_ids if (k or "").startswith(f"{other}-")]
    assert at.session_state["focus"] is None  # the navigation target is consumed


def test_today_review_opens_an_artifact_from_an_older_correspondence(tmp_path, monkeypatch):
    console, account_id, older_review, newer_review, preps = _two_prepared_correspondences(tmp_path)
    older_pid, newer_pid = preps[older_review], preps[newer_review]
    rows = [r for r in console.review_queue()["pending"] if r["ref"] == older_pid]
    target = rows[-1]  # not the artifact Prepare would select by default
    assert target["artifact_id"] in {r["artifact_id"] for r in console.review_queue()["pending"]
                                     if r["ref"] == newer_pid}  # same artifact ID exists in the newer preparation

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")  # no target: the newest correspondence is shown
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert box.value == newer_review

    _click(at, "nav-Today")
    _click(at, f"today-review-{account_id}-{older_pid}-{target['artifact_id']}")
    _assert_opened(at, account_id, older_review, older_pid, [newer_pid], target)


def test_review_queue_target_opens_an_artifact_from_an_older_correspondence(tmp_path, monkeypatch):
    # Review Queue opens rows through a dataframe selection, which AppTest cannot click; this drives the same
    # go(...) call _open_on_select makes, with the target the page builds for that row.
    from baec_console.ui_common import review_target
    from baec_console.ui_pages import SECTION_FOR_QUEUE
    console, account_id, older_review, newer_review, preps = _two_prepared_correspondences(tmp_path)
    older_pid, newer_pid = preps[older_review], preps[newer_review]
    target = [r for r in console.review_queue()["pending"] if r["ref"] == older_pid][1]
    assert review_target(target) == (older_pid, target["artifact_id"])

    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    _click(at, "nav-Review Queue")
    at.session_state["page"] = "Workspace"
    at.session_state["account_id"] = account_id
    at.session_state["section"] = SECTION_FOR_QUEUE[target["stage"]]
    at.session_state["focus"] = review_target(target)
    at.run()
    _assert_opened(at, account_id, older_review, older_pid, [newer_pid], target)


def _metric(at: AppTest, label: str) -> int:
    import re
    (value,) = [int(m) for x in at.markdown
                for m in re.findall(rf'bc-metric-value">(\d+)</div><div class="bc-metric-label">{label}<', x.value)]
    return value


def _human_reviews_metric(at: AppTest) -> int:
    return _metric(at, "Human reviews")


def test_a_newer_request_for_the_same_correspondence_replaces_the_earlier_preparation_in_the_queue(
        tmp_path, monkeypatch):
    from baec_app.engine3.enums import HumanReviewDecision
    from baec_console.console_service import Console, ConsoleError
    from .conftest import ACTOR, ATTEST, StepClock, permitted_request, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, _, result, review_id = verified(console)
    request_a = console.local.request_preparation(review_id, permitted_request(result, dispositions={}))
    prep_a = console.local.generate_preparation(request_a)
    a_pending = [r["artifact_id"] for r in console.review_queue()["pending"] if r["ref"] == prep_a]
    assert a_pending  # A has reviewable drafts until it is replaced

    request_b = console.local.request_preparation(review_id, permitted_request(result))
    prep_b = console.local.generate_preparation(request_b)
    queue = console.review_queue()
    assert {r["ref"] for r in queue["pending"]} == {prep_b}
    assert {r["artifact_id"] for r in queue["pending"]} == {"WHY-NOW", "EMAIL", "CALL", "QUESTIONS"}
    replaced = [r for r in queue["completed"] if r["ref"] == prep_a]
    assert {r["artifact_id"] for r in replaced if r["status"] == "Replaced by a newer Preparation Request"} \
        == set(a_pending)
    assert {r["status"] for r in replaced if r["artifact_id"] not in a_pending} == {"Refused by Engine 3 validation"}
    view = console.local.account_view(account_id)
    assert prep_a in {p["preparation_id"] for p in view.preparations}  # history is kept, nothing deleted
    # The existing Engine 3 rule this mirrors: A can no longer be accepted.
    with pytest.raises(ConsoleError, match="newer Preparation Request"):
        console.local.review_artifact(prep_a, a_pending[0], HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")  # no target: the current preparation is shown
    keys = {b.key or "" for b in at.button}
    assert any(k.startswith(f"{prep_b}-") for k in keys)
    assert not any(k.startswith(f"{prep_a}-") for k in keys)

    _click(at, "nav-Today")
    review_keys = [b.key for b in at.button if (b.key or "").startswith("today-review-")]
    assert _human_reviews_metric(at) == len(review_keys) == len(queue["pending"])
    assert all(f"-{prep_b}-" in k for k in review_keys)
    _click(at, "nav-Review Queue")
    assert "Pending (4)" in _texts(at)


def test_every_pending_review_row_opens_its_exact_artifact(tmp_path, monkeypatch):
    from .conftest import ACTOR, permitted_request
    console, account_id, older_review, newer_review, preps = _two_prepared_correspondences(tmp_path)
    request_id = console.local.request_preparation(  # re-requested: the older correspondence's first one is replaced
        older_review, permitted_request(console.results[older_review]))
    replacement = console.local.generate_preparation(request_id)
    review_of = {preps[older_review]: older_review, replacement: older_review, preps[newer_review]: newer_review}

    pending = console.review_queue()["pending"]
    assert {r["ref"] for r in pending} == {replacement, preps[newer_review]}
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    for row in pending:
        at = AppTest.from_file(APP, default_timeout=30)
        at.run()
        assert _human_reviews_metric(at) == len(pending)
        _click(at, f"today-review-{account_id}-{row['ref']}-{row['artifact_id']}")
        _assert_opened(at, account_id, review_of[row["ref"]], row["ref"],
                       [pid for pid in review_of if pid != row["ref"]], row)


def _replaced_by_b(tmp_path, b_requested_at=None, before_b=None):
    """One correspondence: request A -> preparation A (undecided), then request B -> preparation B.

    b_requested_at pins B's stored requested_at, to model a clock that moved backward or repeated a timestamp.
    """
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, permitted_request, verified, make_console

    class PinnableClock(StepClock):
        pinned = None

        def now(self):
            return self.pinned if self.pinned is not None else super().now()

    clock = PinnableClock()
    console = make_console(tmp_path / "data", clock)
    account_id, _, result, review_id = verified(console)
    request_a = console.local.request_preparation(review_id, permitted_request(result))
    prep_a = console.local.generate_preparation(request_a)
    a_at = console.local.account_view(account_id).requests[0]["requested_at"]
    if before_b is not None:
        before_b(console, prep_a)
    if b_requested_at is not None:
        clock.pinned = b_requested_at(a_at)
    request_b = console.local.request_preparation(review_id, permitted_request(result))
    clock.pinned = None
    prep_b = console.local.generate_preparation(request_b)  # refused if B were not current
    return console, account_id, review_id, (request_a, prep_a), (request_b, prep_b)


def test_preparation_page_shows_a_replaced_preparation_as_history_not_work(tmp_path, monkeypatch):
    from baec_console.console_service import REPLACED
    console, account_id, _, (_, prep_a), (_, prep_b) = _replaced_by_b(tmp_path)
    assert console.local.preparation_view(prep_a)["status"] == REPLACED
    assert console.local.preparation_view(prep_b)["status"] == "Needs review"
    assert {p["preparation_id"] for p in console.local.account_view(account_id).preparations} == {prep_a, prep_b}

    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert _metric(at, "Human reviews") == 4  # B's drafts only
    assert _metric(at, "Preparation work") == 1
    _click(at, "nav-Preparation")
    text = _texts(at)
    assert "Needs review (1)" in text
    assert f"{REPLACED} (1)" in text


def test_workflow_stage_and_next_move_follow_the_current_request(tmp_path, monkeypatch):
    from baec_app.engine3.enums import HumanReviewDecision
    from .conftest import ACTOR, ATTEST
    console, account_id, _, (_, prep_a), (_, prep_b) = _replaced_by_b(tmp_path)
    for artifact_id in ("WHY-NOW", "EMAIL", "CALL", "QUESTIONS"):
        console.local.review_artifact(prep_b, artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    view = console.local.account_view(account_id)  # A is still undecided, and must not pull the account back
    assert (view.stage, view.next_action) == ("PREPARED", "Prepared for human use. The salesperson decides.")
    assert not console.review_queue()["pending"]

    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert _metric(at, "Human reviews") == 0
    assert _metric(at, "Preparation work") == 0


@pytest.mark.parametrize("b_requested_at", [
    pytest.param(lambda a_at: a_at - timedelta(hours=1), id="clock-moved-backward"),
    # Equal timestamps: the request stored later is current. (The earlier >= comparison made both replaced.)
    pytest.param(lambda a_at: a_at, id="identical-timestamp"),
])
def test_every_consumer_agrees_on_the_current_request(tmp_path, monkeypatch, b_requested_at):
    from baec_app.engine3.enums import HumanReviewDecision
    from baec_console.console_service import REPLACED, ConsoleError, current_requests
    from .conftest import ACTOR, ATTEST
    console, account_id, review_id, (request_a, prep_a), (request_b, prep_b) = _replaced_by_b(
        tmp_path, b_requested_at)
    view = console.local.account_view(account_id)
    a, b = view.requests
    assert b["requested_at"] <= a["requested_at"]  # timestamps alone would not pick B
    assert current_requests(view.requests)[review_id]["request_id"] == request_b
    assert view.current_request(review_id)["request_id"] == request_b
    assert view.preparation_replaced(next(p for p in view.preparations if p["preparation_id"] == prep_a))
    assert console.local.preparation_view(prep_a)["status"] == REPLACED
    assert console.local.preparation_view(prep_b)["status"] == "Needs review"
    assert {r["ref"] for r in console.review_queue()["pending"]} == {prep_b}
    assert view.next_action == "Review prepared drafts"

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    keys = {b.key or "" for b in at.button}
    assert f"{prep_b}-WHY-NOW-accept" in keys
    assert not any(k.startswith(f"{prep_a}-") for k in keys)

    # Engine 3's current context: A is refused as replaced, B is accepted.
    with pytest.raises(ConsoleError, match="newer Preparation Request"):
        console.local.review_artifact(prep_a, "WHY-NOW", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    console.local.review_artifact(prep_b, "WHY-NOW", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                  attestations=ATTEST)


def _open_preparation_row(tmp_path, monkeypatch, account_id, preparation_id) -> AppTest:
    # The Preparation page opens rows through a dataframe selection, which AppTest cannot click; this makes the
    # same go(...) call _open_on_select makes, with the target the page builds for that row.
    from baec_console.ui_common import preparation_target
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    _click(at, "nav-Preparation")
    at.session_state["page"] = "Workspace"
    at.session_state["account_id"] = account_id
    at.session_state["section"] = "Prepare"
    at.session_state["focus"] = preparation_target(preparation_id)
    at.session_state["prep_history"] = None
    at.run()
    assert not at.exception, at.exception
    return at


def _decision_keys(at: AppTest) -> set[str]:
    return {b.key for b in at.button if (b.key or "").endswith(("-accept", "-edit", "-reject"))}


def _assert_current(at, preparation_id, others):
    from baec_console.console_service import REPLACED
    keys = _decision_keys(at)
    assert f"{preparation_id}-WHY-NOW-accept" in keys
    assert not [k for k in keys for other in others if k.startswith(f"{other}-")]
    assert REPLACED not in _texts(at)


def _assert_history(at, preparation_id):
    from baec_console.console_service import REPLACED
    assert not _decision_keys(at)  # nothing on screen can record a decision
    text = _texts(at)
    assert REPLACED in text and "kept as history" in text
    assert preparation_id in [c.value for c in at.code]  # the technical details name the preparation shown
    assert any(b.label == "Show the current preparation" for b in at.button)


def test_preparation_page_rows_open_the_preparation_clicked(tmp_path, monkeypatch):
    console, account_id, _, (_, prep_a), (_, prep_b) = _replaced_by_b(tmp_path)

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")  # no target: the current preparation
    _assert_current(at, prep_b, [prep_a])

    _assert_current(_open_preparation_row(tmp_path, monkeypatch, account_id, prep_b), prep_b, [prep_a])

    at = _open_preparation_row(tmp_path, monkeypatch, account_id, prep_a)
    _assert_history(at, prep_a)
    (selector,) = [s for s in at.segmented_control if s.label == "Prepared artifact"]
    selector.set_value("EMAIL DRAFT").run()  # moving between drafts stays on the preparation clicked
    assert not at.exception, at.exception
    _assert_history(at, prep_a)
    (back,) = [b for b in at.button if b.label == "Show the current preparation"]
    back.click().run()
    assert not at.exception, at.exception
    _assert_current(at, prep_b, [prep_a])


def test_preparation_page_rows_open_preparations_of_an_older_correspondence(tmp_path, monkeypatch):
    from .conftest import ACTOR, permitted_request
    console, account_id, older_review, newer_review, preps = _two_prepared_correspondences(tmp_path)
    request_id = console.local.request_preparation(older_review, permitted_request(console.results[older_review]))
    replacement = console.local.generate_preparation(request_id)
    replaced, newer = preps[older_review], preps[newer_review]

    def box(at):
        (b,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
        return b.value

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert box(at) == newer_review
    _assert_current(at, newer, [replaced, replacement])

    at = _open_preparation_row(tmp_path, monkeypatch, account_id, replacement)
    assert box(at) == older_review
    _assert_current(at, replacement, [replaced, newer])

    at = _open_preparation_row(tmp_path, monkeypatch, account_id, replaced)
    assert box(at) == older_review
    _assert_history(at, replaced)

    at = _open_preparation_row(tmp_path, monkeypatch, account_id, newer)
    assert box(at) == newer_review
    _assert_current(at, newer, [replaced, replacement])


def test_no_decision_or_edit_is_recorded_against_a_replaced_preparation(tmp_path):
    from baec_app.engine3.enums import HumanReviewDecision
    from baec_console.console_service import ConsoleError
    from .conftest import ACTOR, ATTEST

    def reject_email(console, prep_a):  # decided before A was replaced; must stay exactly as recorded
        console.local.review_artifact(prep_a, "EMAIL", HumanReviewDecision.REJECT, attestations={},
                                      reason="Not now")

    console, account_id, _, (_, prep_a), (_, prep_b) = _replaced_by_b(tmp_path, before_b=reject_email)
    before = console.local.preparation_view(prep_a)
    events = len(console.local.timeline(account_id))
    replaced = "A newer Preparation Request replaced this one"
    with pytest.raises(ConsoleError, match=replaced):
        console.local.review_artifact(prep_a, "WHY-NOW", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    with pytest.raises(ConsoleError, match=replaced):
        console.local.edit_artifact(prep_a, "WHY-NOW", [])
    with pytest.raises(ConsoleError, match=replaced):
        console.local.review_artifact(prep_a, "CALL", HumanReviewDecision.REJECT, attestations={},
                                      reason="")
    after = console.local.preparation_view(prep_a)
    assert after["reviews"] == before["reviews"] and set(after["reviews"]) == {"EMAIL"}
    assert after["edits"] == before["edits"] == []
    assert len(console.local.timeline(account_id)) == events

    console.local.review_artifact(prep_b, "CALL", HumanReviewDecision.REJECT, attestations={}, reason="")
    console.local.review_artifact(prep_b, "WHY-NOW", HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=ATTEST)
    assert set(console.local.preparation_view(prep_b)["reviews"]) == {"CALL", "WHY-NOW"}


def test_ui_block_control_records_a_human_block_and_stops_preparation(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, _, _, review_id = verified(console)
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert any(b.label == "Prepare from this correspondence" for b in at.button)
    _click(at, f"block-{review_id}")
    assert console.local.account_view(account_id).blocks[review_id][-1]["action"] == "BLOCK"
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    assert "Preparation is blocked by human decision" in _texts(at)
    assert not any(b.label == "Prepare from this correspondence" for b in at.button)
    _click(at, f"unblock-{review_id}")
    assert [d["action"] for d in console.local.account_view(account_id).blocks[review_id]] == ["BLOCK", "UNBLOCK"]


def test_ui_lists_each_verified_correspondence_for_explicit_selection(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, observe, review, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id, _, first_review = verified(console)
    second = observe(console, account_id, baec_id, new_price="44000")
    second_review = review(console, second.candidate_id).review.review_id
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert len(box.options) == 2
    assert box.value == second_review  # newest shown first; nothing is recorded until the human requests
    box.set_value(first_review).run()
    assert not at.exception
    assert console.local.account_view(account_id).requests == []


def test_void_action_is_a_successful_attempt():
    from baec_console.ui_common import attempt
    assert attempt(lambda: None) is True


def test_account_overview_has_crm_interaction_intake(
    tmp_path,
    monkeypatch,
):
    from .conftest import StepClock, make_console

    console = make_console(
        tmp_path / "data",
        StepClock(),
    )

    account_id = console.local.create_account(
        "Northstar Surgical",
        industry="ASC",
    )

    at = _open(
        tmp_path,
        monkeypatch,
        account_id,
        "Overview",
    )

    text = _texts(at)

    assert "Log interaction" in text
    assert any(
        x.label == "Call, email, meeting, or field notes"
        for x in at.text_area
    )

    assert any(
        b.label == "Analyze with AI"
        for b in at.button
    )

    assert any(
        b.label == "Save activity without AI"
        for b in at.button
    )


# --- remediation from the first live acceptance run --------------------------------------------------------------


def test_verified_correspondence_offers_continue_to_prepare_without_requesting(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, _, _, review_id = verified(console)
    at = _open(tmp_path, monkeypatch, account_id, "Recognize")
    text = _texts(at)
    assert "Correspondence verified" in text
    assert escape("This does not establish buyer intent or authorize outreach.") in text
    cta = at.button(key=f"continue-prepare-{review_id}")
    assert cta.label == "Continue to Prepare →" and cta.proto.type == "primary"
    _click(at, f"continue-prepare-{review_id}")
    assert at.session_state[f"section-{account_id}"] == "Prepare"
    assert any(b.label == "Prepare from this correspondence" for b in at.button)
    view = console.local.account_view(account_id)
    assert view.requests == [] and view.preparations == [] and view.account.state is None  # navigation only


def test_continue_to_prepare_selects_the_correspondence_just_verified(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, observe, review, verified, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id, _, first_review = verified(console)
    second = observe(console, account_id, baec_id, new_price="44000")
    review(console, second.candidate_id)
    at = _open(tmp_path, monkeypatch, account_id, "Recognize")
    _click(at, f"continue-prepare-{first_review}")  # the older one: Prepare would otherwise default to the newest
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert box.value == first_review


def test_continue_to_prepare_still_shows_when_an_older_correspondence_was_prepared(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, observe, review, verified, make_console
    from .test_prepare import _prepare_and_accept
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id, result, first_review = verified(console)
    _prepare_and_accept(console, result, first_review)
    second = observe(console, account_id, baec_id, new_price="44000")
    second_review = review(console, second.candidate_id).review.review_id
    at = _open(tmp_path, monkeypatch, account_id, "Recognize")
    assert at.button(key=f"continue-prepare-{second_review}").label == "Continue to Prepare →"
    assert not [b for b in at.button if b.key == f"continue-prepare-{first_review}"]  # it has its own request


def test_preparation_complete_is_scoped_to_the_correspondence_on_screen(tmp_path, monkeypatch):
    from baec_console.console_service import Console
    from .conftest import StepClock, observe, review, verified, make_console
    from .test_prepare import _prepare_and_accept
    console = make_console(tmp_path / "data", StepClock())
    account_id, baec_id, result, older_review = verified(console)
    _prepare_and_accept(console, result, older_review)
    newer = observe(console, account_id, baec_id, new_price="44000")
    newer_review = review(console, newer.candidate_id).review.review_id

    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert box.value == newer_review
    assert "Preparation complete" not in _texts(at)
    assert "Preparation requested" not in _texts(at)
    assert any(b.label == "Prepare from this correspondence" for b in at.button)

    box.set_value(older_review).run()  # the older correspondence keeps its own history
    assert "Preparation complete" in _texts(at)

    _prepare_and_accept(console, newer, newer_review)
    at = _open(tmp_path, monkeypatch, account_id, "Prepare")
    (box,) = [s for s in at.selectbox if s.label == "Verified correspondence"]
    assert box.value == newer_review
    assert "Preparation complete" in _texts(at)


def _overview_with_analysis(tmp_path, monkeypatch, raw: str, analysis) -> tuple[AppTest, str]:
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, make_console
    console = make_console(tmp_path / "data", StepClock())
    account_id = console.local.create_account("Harborview Surgical Center (synthetic)", industry="ASC")
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = "Overview"
    at.session_state[f"crm-intake-raw-{account_id}"] = raw
    at.session_state[f"crm-analysis-{account_id}"] = analysis
    at.session_state[f"crm-analysis-source-{account_id}"] = raw
    at.run()
    assert not at.exception, at.exception
    return at, account_id


def test_possible_baec_makes_review_the_primary_choice_and_crm_only_secondary(tmp_path, monkeypatch):
    from .test_crm_ai import _baec_analysis
    raw = ("We are not reviewing vendors now, but if pricing increases by more than 8% at March renewal, "
           "we would compare alternatives.")  # the exact passages _baec_analysis cites
    at, account_id = _overview_with_analysis(tmp_path, monkeypatch, raw, _baec_analysis(raw))
    promote = at.button(key=f"crm-promote-{account_id}")
    crm_only = at.button(key=f"crm-save-ai-{account_id}")
    assert promote.label == "Save & review BAEC condition →"
    assert promote.proto.type == "primary" and not promote.disabled
    assert crm_only.label == "Save to CRM only" and crm_only.proto.type == "secondary" and not crm_only.disabled
    keys = [b.key for b in at.button]
    assert keys.index(promote.key) < keys.index(crm_only.key)
    assert not any(b.label in ("Save AI-assisted CRM activity", "Save activity & review as BAEC condition")
                   for b in at.button)


def test_unavailable_baec_review_keeps_crm_save_primary_and_explains_why(tmp_path, monkeypatch):
    from .test_crm_ai import _baec_analysis
    raw = ("We are not reviewing vendors now, but if pricing increases by more than 8% at March renewal, "
           "we would compare alternatives.")  # the exact passages _baec_analysis cites
    analysis = _baec_analysis(raw)
    analysis = analysis.model_copy(update={"buyer_statement": "The buyer may compare suppliers."})  # not verbatim
    at, account_id = _overview_with_analysis(tmp_path, monkeypatch, raw, analysis)
    promote = at.button(key=f"crm-promote-{account_id}")
    crm_only = at.button(key=f"crm-save-ai-{account_id}")
    assert promote.disabled and promote.proto.type == "secondary"
    assert crm_only.proto.type == "primary" and not crm_only.disabled
    assert any(c.value.startswith("BAEC review unavailable: ") for c in at.caption)


def test_overview_next_move_has_no_literal_backslash_n(app):
    _click(app, "seed-completed")
    _section(app, "Overview")
    (move,) = [m.value for m in app.markdown if "BAEC recommends the step" in m.value]
    assert "\\n" not in move
    assert "Prepared for human use. The salesperson decides." in move
    assert not any("\\n" in m.value for m in app.markdown)


def test_claude_credential_is_session_only_and_can_be_disconnected(app, tmp_path):
    (field,) = [t for t in app.text_input if t.key == "baec-ai-token"]
    assert field.label == "Claude API key"
    assert field.placeholder == "Enter Claude API key"
    assert field.proto.type == field.proto.PASSWORD
    assert any(c.value == "Uses your own Claude API account. The key is kept only for this session."
               for c in app.caption)
    credential = "synthetic-test-credential-0000"
    field.input(credential).run()
    assert not app.exception
    assert any(b.label == "Disconnect Claude" for b in app.button)
    _click(app, "seed-completed")  # exercise writes to every store while connected
    assert credential not in _texts(app)
    for path in (tmp_path / "data").rglob("*"):
        if path.is_file():
            assert credential.encode() not in path.read_bytes(), path
    _click(app, "disconnect-claude")
    assert app.session_state["baec-ai-token"] == ""
    assert not any(b.label == "Disconnect Claude" for b in app.button)


def test_selected_crm_contact_title_is_the_buyer_role_through_review_and_promotion(tmp_path, monkeypatch):
    # Live acceptance failure: Morgan Lee · Materials Manager was selected, Claude returned a blank buyer role.
    from baec_console import crm_ai
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, make_console
    from .test_crm_ai import RIVERSIDE_RAW, RIVERSIDE_STATEMENT, _riverside_analysis
    console = make_console(tmp_path / "data", StepClock())
    account_id = console.local.create_account("Riverside Surgical Center (synthetic)", industry="ASC")
    contact_id = console.local.crm_add_contact(account_id, name="Morgan Lee", title="Materials Manager")
    sent = []
    monkeypatch.setattr(crm_ai, "_parse", lambda *a, **kw: sent.append(kw["payload"]) or _riverside_analysis(""))
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = "Overview"
    at.session_state["baec-ai-token"] = "synthetic-test-credential-0000"
    at.run()
    assert not at.exception, at.exception
    at.selectbox(key=f"crm-intake-contact-{account_id}").select(contact_id).run()
    at.text_area(key=f"crm-intake-raw-{account_id}").input(RIVERSIDE_RAW).run()
    _click(at, f"crm-analyze-{account_id}")
    assert sent[0]["known_contact"] == {"name": "Morgan Lee", "title": "Materials Manager"}

    role = at.text_input(key=f"crm-ai-role-{account_id}")
    assert (role.label, role.value) == ("Buyer role", "Materials Manager")
    assert at.text_area(key=f"crm-ai-statement-{account_id}").value == RIVERSIDE_STATEMENT

    _click(at, f"crm-promote-{account_id}")
    (activity,) = console.local.crm_activities(account_id)
    assert activity["contact_id"] == contact_id
    assert activity["buyer_role"] == "Materials Manager"
    assert activity["buyer_statement"] == RIVERSIDE_STATEMENT
    interaction_id = activity["engine1_interaction_id"]
    assert interaction_id
    with console.local._cs() as cs:
        meta = cs.interaction_meta(interaction_id)
    assert meta["buyer_role"] == "Materials Manager"
    assert meta["buyer_statement"] == RIVERSIDE_STATEMENT  # CRM metadata stays out of buyer evidence


def test_seller_context_does_not_contradict_the_selected_crm_contact(tmp_path, monkeypatch):
    from baec_console import crm_ai
    from baec_console.console_service import Console
    from .conftest import ACTOR, StepClock, make_console
    from .test_crm_ai import RIVERSIDE_RAW, RIVERSIDE_SELLER_CONTEXT, RIVERSIDE_STATEMENT, _with_seller_notes
    console = make_console(tmp_path / "data", StepClock())
    account_id = console.local.create_account("Riverside Surgical Center (synthetic)", industry="ASC")
    contact_id = console.local.crm_add_contact(account_id, name="Morgan Lee", title="Materials Manager")
    model = _with_seller_notes(RIVERSIDE_SELLER_CONTEXT + " Morgan's title and role were not provided.")
    monkeypatch.setattr(crm_ai, "_parse", lambda *a, **kw: model)
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = "Overview"
    at.session_state["baec-ai-token"] = "synthetic-test-credential-0000"
    at.run()
    at.selectbox(key=f"crm-intake-contact-{account_id}").select(contact_id).run()
    at.text_area(key=f"crm-intake-raw-{account_id}").input(RIVERSIDE_RAW).run()
    _click(at, f"crm-analyze-{account_id}")

    assert at.text_input(key=f"crm-ai-role-{account_id}").value == "Materials Manager"
    assert at.text_area(key=f"crm-ai-seller-{account_id}").value == RIVERSIDE_SELLER_CONTEXT
    promote = at.button(key=f"crm-promote-{account_id}")
    assert promote.proto.type == "primary" and not promote.disabled  # promotion behavior unchanged

    _click(at, f"crm-promote-{account_id}")
    (activity,) = console.local.crm_activities(account_id)
    assert activity["buyer_role"] == "Materials Manager"
    assert activity["seller_notes"] == RIVERSIDE_SELLER_CONTEXT
    assert activity["buyer_statement"] == RIVERSIDE_STATEMENT
    assert activity["engine1_interaction_id"]
