"""Phase 6C-B: baec-canonical-json/v1, its digests, the canonical input, and AiRequestSpec immutability."""

import json
import subprocess
import sys
from decimal import Decimal

import pytest

from baec_app.ai.canonical import (
    CANONICALIZATION_VERSION,
    CanonicalizationError,
    canonical_digest,
    canonical_json,
    sha256_text,
)
from baec_app.ai.provider import SENT_FIELDS, AiRequestSpec
from baec_app.ai.service import canonical_input, text_audit
from tests.ai_builders import MODEL, THRESHOLD_TEXT, FakeProvider

GOLDEN = {
    "sorted keys at every depth": ({"b": 1, "a": {"d": [3, 2], "c": None}}, '{"a":{"c":null,"d":[3,2]},"b":1}'),
    "non-ASCII written directly": ({"t": "café — ✓"}, '{"t":"café — ✓"}'),
    "no Unicode normalization": ({"t": "é"}, '{"t":"é"}'),
    "JSON escapes only": ({"t": 'a"b\\c\nd\u0000'}, '{"t":"a\\"b\\\\c\\nd\\u0000"}'),
    "literals": ([True, False, None, 0, -1, 1.5], "[true,false,null,0,-1,1.5]"),
    "tuples as arrays": ((1, "x"), '[1,"x"]'),
    "empty containers": ({"a": [], "b": {}}, '{"a":[],"b":{}}'),
}


@pytest.mark.parametrize("value,expected", GOLDEN.values(), ids=GOLDEN.keys())
def test_golden_canonical_vectors(value, expected):
    assert canonical_json(value) == expected


def test_the_version_and_digests():
    assert CANONICALIZATION_VERSION == "baec-canonical-json/v1"
    assert sha256_text("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert canonical_digest({"b": 1, "a": 2}) == sha256_text('{"a":2,"b":1}')
    assert sha256_text("é") != sha256_text("é")  # NFC and NFD stay distinct


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), Decimal("1.0"), {1: "x"}, {"a": {1, 2}}, b"bytes", object(), {"a": [Decimal(1)]},
     type("S", (str,), {})("subclass")],
    ids=["nan", "inf", "decimal", "int-key", "set", "bytes", "object", "nested-decimal", "str-subclass"],
)
def test_unsupported_values_are_refused_not_stringified(value):
    with pytest.raises(CanonicalizationError):
        canonical_json(value)


def test_digests_are_identical_across_processes_with_different_hash_seeds():
    code = ("from baec_app.ai.canonical import canonical_digest; "
            "print(canonical_digest({'z': [1, {'y': 'é', 'x': None}], 'a': True}))")
    digests = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                       env={"PYTHONHASHSEED": seed, "PATH": ""}, cwd=".").stdout.strip()
        for seed in ("0", "1", "12345")
    }
    assert len(digests) == 1


# --- canonical input -------------------------------------------------------------------------


def test_the_canonical_input_is_exactly_four_fields_in_sorted_order():
    content = canonical_input(account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)
    assert content.startswith('{"account_id":"ACC-1","input_version":"baec-extraction-input/v1","interaction_id":"INT-T",'
                              '"interaction_text":"')
    decoded = json.loads(content)
    assert decoded == {"account_id": "ACC-1", "input_version": "baec-extraction-input/v1", "interaction_id": "INT-T",
                       "interaction_text": THRESHOLD_TEXT}
    assert decoded["interaction_text"] == THRESHOLD_TEXT  # the exact stored source string


def test_golden_input_digest():
    content = canonical_input(account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)
    assert sha256_text(content) == "d2abe6256bda86333548416efe753db30db516aa8bcb4957f95790f426c38dfd"


def test_unicode_and_control_characters_survive_only_with_json_escaping():
    text = 'Buyer: “café” é ‮ tab\tend "quoted" \\ back'
    decoded = json.loads(canonical_input(account_id="A", interaction_id="I", interaction_text=text))
    assert decoded["interaction_text"] == text


def test_text_audit_representation():
    assert text_audit(()) is None
    assert text_audit(("  {raw}\n",)) == "  {raw}\n"  # one block: unchanged
    assert text_audit(("a", "b\n")) == '["a","b\\n"]'  # several: a canonical JSON array, in order


# --- AiRequestSpec: deep immutability --------------------------------------------------------------


def _spec():
    content = canonical_input(account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)
    return FakeProvider().prepare_request(
        model=MODEL, max_tokens=4096, system="SYSTEM", user_content=content,
        prompt_version="baec-extraction-prompt/v1", input_version="baec-extraction-input/v1",
        output_schema_version="baec-extraction-output/v1",
    )


def test_the_spec_cannot_be_mutated_through_any_accessor():
    spec = _spec()
    digest, canonical = spec.digest(), spec.canonical_json()
    spec.messages.append({"role": "user", "content": "smuggled"})
    spec.messages[0]["content"] = "changed"
    spec.output_config["format"]["schema"]["properties"] = {}
    spec.to_json_object()["model"] = "other"
    arguments = spec.sdk_arguments()
    arguments["messages"][0]["content"] = "changed"
    arguments["output_config"]["format"]["type"] = "text"
    arguments["tools"] = [{"name": "confirm_baec"}]
    assert spec.digest() == digest and spec.canonical_json() == canonical
    assert spec.sdk_arguments() == {field: spec.to_json_object()[field] for field in SENT_FIELDS}
    with pytest.raises(AttributeError):
        spec.model = "other"


def test_sdk_arguments_are_fresh_and_exactly_the_sent_fields():
    spec = _spec()
    first, second = spec.sdk_arguments(), spec.sdk_arguments()
    assert first == second and first is not second and first["messages"] is not second["messages"]
    assert list(first) == ["model", "max_tokens", "system", "messages", "output_config"]


def test_the_request_digest_is_of_the_semantic_json_object():
    spec = _spec()
    assert spec.digest() == sha256_text(canonical_json(spec.to_json_object()))
    assert set(spec.to_json_object()) == {
        "request_spec_version", "provider", "api_method", "model", "max_tokens", "system", "messages",
        "output_config", "prompt_version", "input_version", "output_schema_version"}


@pytest.mark.parametrize("field", ["request_spec_version", "provider", "api_method", "model", "max_tokens", "system",
                                   "messages_json", "prompt_version", "input_version", "output_schema_version"])
def test_every_application_controlled_field_changes_the_request_digest(field):
    from dataclasses import replace

    spec = _spec()
    changed = {"max_tokens": 4095, "messages_json": '[{"content":"x","role":"user"}]'}.get(field, "changed")
    assert replace(spec, **{field: changed}).digest() != spec.digest()


def test_non_canonical_nested_json_is_refused():
    spec = _spec()
    from dataclasses import replace

    for bad in ('[{"role": "user"}]', '{"role":"user"}', "not json", '[{"b":1,"a":2}]'):
        with pytest.raises(ValueError):
            replace(spec, messages_json=bad)
