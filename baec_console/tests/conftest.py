"""Shared fixtures for BAEC Console tests. All data is synthetic; every database lives in pytest's tmp_path."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from baec_console import demo_seed
from baec_console.console_service import ATTESTATIONS, Console
from baec_console.deterministic_preparation import G_STATEMENT, G_THRESHOLD, G_TIMING, g_evidence, g_measurement
from baec_console.engine2_adapter import ObservationForm, SourceForm
from baec_app.engine2.domain import CorrespondenceDimension, DimensionFinding, SufficiencyFinding
from baec_app.engine2.measurement import Transformation

ACTOR = "Test salesperson"
D = CorrespondenceDimension
REQUIRED = frozenset({D.ENTITY_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE, D.THRESHOLD_MATCH,
                      D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH})
ATTEST = {key: True for key, _ in ATTESTATIONS}


class StepClock:
    """Advances one minute on every read, so recorded history has a strict order."""

    def __init__(self) -> None:
        self.at = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        self.at += timedelta(minutes=1)
        return self.at


@pytest.fixture(autouse=True)
def development_mode(monkeypatch):
    """Tests run the console in explicit development mode. The runtime has no default (deployment.py); a test that
    needs another mode, or no mode, sets or removes BAEC_DEPLOYMENT_MODE itself."""
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "development")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to open a network socket fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("BAEC Console attempted a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def make_console(root, clock=None) -> Console:
    """The local-development console, with the local user named ACTOR so recorded human actions carry ACTOR."""
    console = Console.local_development(root, clock or StepClock())
    if console.actor != ACTOR:
        console.set_actor(ACTOR)
    return console


@pytest.fixture
def console(tmp_path) -> Console:
    return make_console(tmp_path / "data", StepClock())


def new_account(console: Console, name: str = "Harbor Point Labs") -> str:
    return console.local.create_account(name, industry="Diagnostics (synthetic)")


def add_interaction(console: Console, account_id: str) -> str:
    return console.local.record_interaction(
        account_id, buyer_role=demo_seed.BUYER_ROLE, occurred_at=datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
        buyer_statement=demo_seed.BUYER_STATEMENT, seller_notes=demo_seed.SELLER_NOTES)


def confirmed(console: Console) -> tuple[str, str]:
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    fp = console.local.preview_condition(draft_id)["fingerprint"]
    return account_id, console.local.confirm_condition(draft_id, displayed_fingerprint=fp)


def monitored(console: Console) -> tuple[str, str]:
    account_id, baec_id = confirmed(console)
    console.local.start_monitoring(
        account_id, baec_id, target_entities=("Cobalt Imaging Supply (synthetic)",), required=REQUIRED,
        sources=(SourceForm("Supplier renewal notice", "Cobalt Imaging Supply (synthetic)", "Forwarded by the buyer",
                            "Email", "Notice text and dates"),))
    return account_id, baec_id


def observe(console: Console, account_id: str, baec_id: str, *, new_price: str = "43600", content: str | None = None,
            corrects: str = "", flag: bool = True):
    source_id = next(iter(console.local.account_view(account_id).sources))
    return console.local.add_observation(baec_id, ObservationForm(
        source_id=source_id, observed_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        content=content or f"Renewal notice: the annual fee changes from USD 40,000 to USD {new_price}.",
        transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value, baseline_value="40000",
        new_value=new_price, value_unit="USD per year", corrects_observation_id=corrects, flag_for_review=flag))


def review(console: Console, candidate_id: str, finding: DimensionFinding = DimensionFinding.SUPPORTED,
           sufficiency: SufficiencyFinding = SufficiencyFinding.YES, triggers: tuple[str, ...] = ()):
    packet = console.local.review_packet(candidate_id)
    return console.local.submit_correspondence_review(
        candidate_id, displayed_context_digest=packet.context_digest,
        findings={d: finding.value for d in packet.plan.required_dimensions}, notes={},
        sufficiency=sufficiency.value, revalidation_triggers=triggers)


def verified(console: Console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    recorded = review(console, result.candidate_id)
    return account_id, baec_id, result, recorded.review.review_id


def permitted_request(result, **overrides) -> dict:
    request = {
        "channel": "EMAIL_AND_CALL", "goal": "ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION",
        "statement_use": "PARAPHRASE", "paraphrase": demo_seed.PARAPHRASE,
        "dispositions": {G_STATEMENT: "PARAPHRASE_ALLOWED", G_THRESHOLD: "DIRECT_REFERENCE_ALLOWED",
                         G_TIMING: "DIRECT_REFERENCE_ALLOWED", g_evidence(result.observation_id): "INTERNAL_ONLY",
                         g_measurement(result.measurement_id): "PARAPHRASE_ALLOWED"},
        "source_identity": {}, "adversity": {},
    }
    request.update(overrides)
    return request
