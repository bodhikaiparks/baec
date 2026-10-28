"""Phase 7D: the pure verification façade (baec_app/ai/verification.py) and the one read-only store method E8 uses.

The façade re-applies the locked Phase 6 contract, canonical form, and validator; it adds no rule of its own.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from baec_app.ai import verification
from baec_app.ai.canonical import canonical_json
from baec_app.ai.contracts import BaecExtractionOutput
from baec_app.ai.validation import VALIDATION_VERSION, validate_extraction
from baec_app.ai.verification import (
    VERIFICATION_CODES,
    ExtractionVerificationError,
    VerifiedExtraction,
    verifier_validation_version,
    verify_persisted_extraction,
)
from baec_app.data.database import make_read_only
from baec_app.data.proposal_bridge import ProposalBridgeStore
from tests.ai_builders import THRESHOLD_TEXT, hypotheses, output
from tests.mapping_builders import INT, mapping  # noqa: F401
from tests.persistence_builders import dump

MODULE = Path(verification.__file__)


def stored(value: dict) -> str:
    return canonical_json(BaecExtractionOutput.model_validate(value).model_dump(mode="json"))


# --- behaviour: the locked Phase 6 rules, re-applied ------------------------------------------------


def test_a_valid_persisted_extraction_verifies_to_the_exact_locked_output():
    text = stored(output(interaction_id=INT))
    verified = verify_persisted_extraction(text, source_interaction_id=INT, source_text=THRESHOLD_TEXT)
    assert type(verified) is VerifiedExtraction
    assert verified.output == BaecExtractionOutput.model_validate_json(text, strict=True)
    assert verified.validation_version == VALIDATION_VERSION == verifier_validation_version()


def test_a_real_stored_artifact_verifies(mapping):
    artifact_id = mapping.artifact()
    text = mapping.connection.execute("SELECT canonical_result FROM ai_artifacts WHERE artifact_id = ?",
                                      (artifact_id,)).fetchone()[0]
    before = dump(mapping.connection)
    assert verify_persisted_extraction(text, source_interaction_id=INT, source_text=THRESHOLD_TEXT).output
    assert dump(mapping.connection) == before  # verification writes nothing


@pytest.mark.parametrize("text", [
    "{", "[]", "null", json.dumps({"analysis_status": "possible_baec_language"}),
    stored(output(interaction_id=INT))[:-1] + ',"extra":1}',
    stored(output(interaction_id=INT)).replace('"possible_baec_language"', '"Possible_BAEC_Language"'),
], ids=["truncated", "array", "null", "missing fields", "extra field", "near-match token"])
def test_malformed_output_fails_through_the_locked_contract(text):
    with pytest.raises(ExtractionVerificationError) as raised:
        verify_persisted_extraction(text, source_interaction_id=INT, source_text=THRESHOLD_TEXT)
    assert raised.value.code == "output_not_parseable"


def test_valid_but_non_canonical_text_is_refused():
    text = json.dumps(BaecExtractionOutput.model_validate(output(interaction_id=INT)).model_dump(mode="json"))
    with pytest.raises(ExtractionVerificationError) as raised:
        verify_persisted_extraction(text, source_interaction_id=INT, source_text=THRESHOLD_TEXT)
    assert raised.value.code == "output_not_canonical"


@pytest.mark.parametrize("changes", [
    {"normalized_condition": "A price increase of more than 15% at renewal."},
    {"criterion_hypotheses": hypotheses()[:3]},
    {"excerpts": [{"excerpt_id": "e1", "source_interaction_id": INT, "text": "Not in the source.",
                   "attributed_speaker": "buyer"}]},
    {"uncertainties": [" "]},
], ids=["ungrounded number", "criterion set", "not verbatim", "blank uncertainty"])
def test_semantically_invalid_output_fails_through_the_locked_validator(changes):
    value = output(interaction_id=INT, **changes)
    text = stored(value)
    parsed = BaecExtractionOutput.model_validate_json(text, strict=True)
    assert validate_extraction(parsed, source_interaction_id=INT, source_text=THRESHOLD_TEXT) != ()
    with pytest.raises(ExtractionVerificationError) as raised:
        verify_persisted_extraction(text, source_interaction_id=INT, source_text=THRESHOLD_TEXT)
    assert raised.value.code == "output_semantically_invalid"


def test_a_result_for_another_interaction_or_source_is_refused():
    text = stored(output(interaction_id=INT))
    with pytest.raises(ExtractionVerificationError):
        verify_persisted_extraction(text, source_interaction_id="FIXTURE-INT-9", source_text=THRESHOLD_TEXT)
    with pytest.raises(ExtractionVerificationError):
        verify_persisted_extraction(text, source_interaction_id=INT, source_text="Buyer: something else.")


def test_codes_are_closed_and_carry_no_text():
    assert VERIFICATION_CODES == ("output_not_parseable", "output_not_canonical", "output_semantically_invalid")
    with pytest.raises(ValueError):
        ExtractionVerificationError("anything")
    with pytest.raises(TypeError):
        verify_persisted_extraction(b"{}", source_interaction_id=INT, source_text=THRESHOLD_TEXT)


# --- purity ----------------------------------------------------------------------------------------


def _tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def test_the_facade_imports_only_the_pure_phase6_contract_canonical_form_and_validator():
    imported = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "dataclasses", "pydantic", "baec_app.ai", "baec_app.ai.canonical",
                        "baec_app.ai.contracts"}
    from_ai = {alias.name for node in ast.walk(_tree()) if isinstance(node, ast.ImportFrom)
               and node.module == "baec_app.ai" for alias in node.names}
    assert from_ai == {"validation"}


def test_the_facade_reaches_no_data_provider_network_environment_clock_or_randomness():
    source = MODULE.read_text(encoding="utf-8")
    tree = _tree()
    for node in ast.walk(tree):  # code only, not docstring prose
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            body[0] = ast.Pass()
    code = ast.unparse(tree)
    for forbidden in ("baec_app.data", "baec_app.ai.service", "baec_app.ai.composition", "baec_app.ai.provider",
                      "anthropic", "baec_app.mcp", "os.environ", "getenv", "sqlite3", "socket", "urllib", "http",
                      "datetime", "time", "random", "uuid", "secrets", "open("):
        assert forbidden not in code, forbidden
    assert "re-applies them; it\nimplements no rule of its own" in source


# --- the ninth store method is a read and nothing else ------------------------------------------------


def test_confirmed_baec_for_artifact_only_reads(mapping):
    artifact_id = mapping.artifact()
    mapping.map(artifact_id)
    statements = []
    mapping.connection.set_trace_callback(statements.append)
    before = dump(mapping.connection)
    assert mapping.bridge.confirmed_baec_for_artifact(artifact_id) is None
    assert mapping.bridge.confirmed_baec_for_artifact("FIXTURE-ART-404") is None
    mapping.connection.set_trace_callback(None)
    assert dump(mapping.connection) == before
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)


def test_confirmed_baec_for_artifact_works_on_a_query_only_connection(mapping):
    artifact_id = mapping.artifact()
    make_read_only(mapping.connection)
    assert ProposalBridgeStore(mapping.connection).confirmed_baec_for_artifact(artifact_id) is None


def test_the_ninth_method_issues_consumes_and_confirms_nothing():
    import inspect
    source = inspect.getsource(ProposalBridgeStore.confirmed_baec_for_artifact)
    for forbidden in ("INSERT", "UPDATE", "DELETE", "REPLACE", "_insert", "_write", "transaction", "grant",
                      "commit"):
        assert forbidden not in source.split('"""')[-1], forbidden
