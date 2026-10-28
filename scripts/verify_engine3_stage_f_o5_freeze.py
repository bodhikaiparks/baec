"""Offline verifier for the Engine 3 Stage F O-5 frozen run configuration.

Checks, without any model call, credential read, or network access:

1. frozen-record status, pre-run statuses, and the no-execution record;
2. prompt, schema, and configuration hashes recomputed from the frozen record;
3. the freeze manifest's bindings, including every locked input recorded at freeze. The original hashes are
   always verified and reported; the only accepted divergences are the exact, owner-approved rebindings, in
   order: O5_POSTFREEZE_REBINDING_001 (HARNESS_AMENDMENT_002), O5_POSTFREEZE_REBINDING_002
   (HARNESS_AMENDMENT_003), and O5_POSTFREEZE_REBINDING_003 (HARNESS_AMENDMENT_004), whose permitted files
   and expected hashes are fixed below;
4. the review document's prompt copies are byte-identical to the frozen record;
5. the thinking instruction is present exactly once, immediately before the JSON instruction, and the
   schema has no reasoning field;
6. structured-output schema semantic neutrality (structural keywords only, full locked vocabularies,
   no cross-field rule);
7. protocol normative definitions copied verbatim into the prompt;
8. worked-example and whole-prompt contamination against every evaluation prose source;
9. the materialized pilot run order recomputed from the frozen algorithm E3F_RUN_ORDER_V1;
10. the human-reference contamination issue record exists, unchanged, with its original blocker status, and
    the later resolution record cites it.

Exit status 0 only if every check passes. This is a software check of documents. It is not a test of
any model and not evidence about annotation reliability.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from baec_app.engine3.enums import (  # noqa: E402
    ActionAttempt,
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ReferenceUseMode,
)
from baec_app.engine3.models import ClaimField  # noqa: E402
from experiments.engine3_stage_f.envelope import OUTPUT_SCHEMA_VERSION  # noqa: E402

DOCS = REPO / "docs" / "engine3"
FROZEN = DOCS / "ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json"
MANIFEST = DOCS / "ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json"
REVIEW = DOCS / "ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_REVIEW.md"
ISSUE = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_ISSUE.md"
RESOLUTION = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_RESOLUTION_001.md"
REBINDING = DOCS / "ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json"
REBINDING_002 = DOCS / "ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json"
REBINDING_003 = DOCS / "ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json"
PROTOCOL = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md"
SPEC = DOCS / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
O6_FROZEN = DOCS / "ENGINE3_STAGE_F_ARM_A_ADDITIONAL_ARTIFACTS_FROZEN.json"
STAGE_E_FIXTURE = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_E_PRERECORDED_PROPOSALS.json"
DRY_RUN_INPUTS = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_INPUTS.json"

STATUS = "O5_RESOLVED_FROZEN"
PRE_RUN = {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
ISSUE_STATUS = "PRE_HUMAN_ANNOTATION_BLOCKER"
THINKING_SENTENCE = "Think the problem through before you answer."
JSON_INSTRUCTION = "Respond with one JSON object that follows the output format below, and nothing else."

# Stage F design section 5: the 32 Stage E seed artifacts of Arm A.
ARM_A_SEED_CATEGORIES = ("VALID", "DISCLOSURE", "CLAIM", "STATE", "ALDER_RIDGE_NEGATIVE", "INJECTION")
ARM_A_SEED_EXCLUDED = ("E3E-I03", "E3E-N17")

# --- post-freeze rebinding (exact; not a generic amendment mechanism) -----------------------------
REBINDING_ID = "O5_POSTFREEZE_REBINDING_001"
REBINDING_AMENDMENT = "HARNESS_AMENDMENT_002"
ORIGINAL_MANIFEST_SHA256 = "4a28ed081ab4b5a9899ad3888cb695f7e366491302ff37a66134897f912355e3"
ORIGINAL_FROZEN_SHA256 = "e74c4a46fd771c12e48ab78f492026f97250a2d0a90ee49158ee544fb6c86237"
# The only locked inputs the rebinding may change: path -> (hash at O-5 freeze, hash after HARNESS_AMENDMENT_002).
REBOUND_LOCKED_INPUTS = {
    "experiments/engine3_stage_f/annotation.py": (
        "07f169b72d35872bf06bb1a06eb486974b5807bfbff66f61be62b1ef2a53e2fd",
        "951b4fb34a5bdfd01a3e8966db457e23296014b5f8e0791b280958798b1a5a01"),
    "tests/engine3/test_engine3_stage_f_annotation.py": (
        "8966bef36c84113ac77b7be148c6ab76eba85ac9350c67c3819aa05a84ac79b3",
        "c63a3aa14a760775d5a658eb368a1879ca2994bb6e8c358b417ea74714728b32"),
    "tests/engine3/test_engine3_stage_f_harness_manifest.py": (
        "9e7d62503eca140804dc5d2a835810abe1c48a3d3a89881803fef3b1b4a30efd",
        "69e9d8bec729324ca28be2023a6e6e0b140733fc361b3f6341adc03d3eaf4864"),
    "scripts/verify_engine3_stage_f_harness.py": (
        "ff8dd103084fbf7e260cb798e3865b4b4a29d6da340b045730e04e9a08847220",
        "cc6d57e7351ba3d15c5919843fc7fa0ae810511445ef70959fc73855c3f84e61"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md": (
        "d95d628948c9ff9468a5c9b677eddee987af24e49fe055f33bffd2c0dd440786",
        "b1d7445aeb0c5e2a15f7cac873e49780afda00e1b4a339a3eab6be5ce8bff6a6"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json": (
        "c16de526b2ddaa6160ed79f0140cf71e35519dabec45d2d196d47e4331233800",
        "c19780e7adf7464e6f8ee9c5cd96e9f06f551e4e395bf4fb495e7aadcac4a934"),
}
# Records the rebinding binds: name -> (path, hash).
REBINDING_RECORDS = {
    "harness_amendment_002": (
        "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_002.md",
        "76d20bce96d4ac2a1617e3a71c4cf96b278570e71da648fb1423f17dc76e2d87"),
    "operational_guide_freeze_manifest": (
        "docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_FREEZE_MANIFEST.json",
        "56952f30343a8844883e20b4d60bef1e3e07af8a4dafebd5a937870505cd2029"),
}
# O5_POSTFREEZE_REBINDING_001 is final: O5_POSTFREEZE_REBINDING_002 binds its exact bytes and the verifier hash it
# recorded.
REBINDING_PATH = "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json"
REBINDING_SHA256 = "76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4"
REBINDING_VERIFIER = ("3a6a7099d75551278a9cbd97989f45f377e836753cdda268ec129d2e09a6f065",
                      "86c744658835edf4297b7c2368b955a113fc9bb3ad9fb3dfaebc32a26732b3fd")

REBINDING_002_ID = "O5_POSTFREEZE_REBINDING_002"
REBINDING_002_AMENDMENT = "HARNESS_AMENDMENT_003"
# The only locked inputs REBINDING_002 may change: path -> (hash before HARNESS_AMENDMENT_003, hash after it).
REBOUND_LOCKED_INPUTS_002 = {
    "experiments/engine3_stage_f/annotation.py": (
        "951b4fb34a5bdfd01a3e8966db457e23296014b5f8e0791b280958798b1a5a01", "247ee4b00787393fbb47dbd7f564d6667bc398db669a93a73906b2b2bd926210"),
    "tests/engine3/stage_f_adapter.py": (
        "a030631228755de648671bc9d45b47df7d33479d07c111ec581bc169ea7623d7", "76a84c2bad3f2be8ebc58be8e73050fcd3c4f6f4a763603ca97e63d332953bff"),
    "tests/engine3/test_engine3_stage_f_annotation.py": (
        "c63a3aa14a760775d5a658eb368a1879ca2994bb6e8c358b417ea74714728b32", "d40e31d4ce37755496903ed2c1d3bf772bdd8476fa96307aea7e3b78424eb79d"),
    "tests/engine3/test_engine3_stage_f_harness_manifest.py": (
        "69e9d8bec729324ca28be2023a6e6e0b140733fc361b3f6341adc03d3eaf4864", "b87ac85f55866ac17702e6bd0ec18e6483551c73b81507da2f28acbbfcc63872"),
    "scripts/verify_engine3_stage_f_harness.py": (
        "cc6d57e7351ba3d15c5919843fc7fa0ae810511445ef70959fc73855c3f84e61", "2c0123fb46cbf71cc5295d6720c24dc84d5de4cf02b8082bd3e69f59a36c084d"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md": (
        "b1d7445aeb0c5e2a15f7cac873e49780afda00e1b4a339a3eab6be5ce8bff6a6", "270c82fb9a523c598e6b0d9c5394aba8f2f3e221d674787fc6818e2d80e15441"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json": (
        "c19780e7adf7464e6f8ee9c5cd96e9f06f551e4e395bf4fb495e7aadcac4a934", "245f2cabd2ad22e847e937cc089c0ecb2f19086aa1e1b6788cfaec3e232868d1"),
}
REBINDING_002_RECORDS = {
    "harness_amendment_003": ("docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_003.md", "48060bf60e323964a61fe1d012edc562dd0b67e2c934dbef8db1864c12c4991e"),
    "o3_eligibility_addendum_002": ("docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md",
                                    "9490a7213625a3e2e13cae1b98b57c47223ac60e56e69ff8f51af4f1ce72f5bd"),
    "operational_guide_freeze_manifest": REBINDING_RECORDS["operational_guide_freeze_manifest"],
}
# O5_POSTFREEZE_REBINDING_002 is final: O5_POSTFREEZE_REBINDING_003 binds its exact bytes and the verifier hash it
# recorded.
REBINDING_002_PATH = "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json"
REBINDING_002_SHA256 = "c8d5e8e8db0f007d9e96336557801c5f859a2f13b89d42bfa408839b82957910"
REBINDING_002_VERIFIER = ("86c744658835edf4297b7c2368b955a113fc9bb3ad9fb3dfaebc32a26732b3fd",
                          "bd8f8add185dd1bb6ba299541d26866c3ed7e444ecce53c42b4a4711b2184472")

REBINDING_003_ID = "O5_POSTFREEZE_REBINDING_003"
REBINDING_003_AMENDMENT = "HARNESS_AMENDMENT_004"
# The only locked inputs REBINDING_003 may change: path -> (hash before HARNESS_AMENDMENT_004, hash after it).
REBOUND_LOCKED_INPUTS_003 = {
    "tests/engine3/test_engine3_stage_f_harness_manifest.py": (
        "b87ac85f55866ac17702e6bd0ec18e6483551c73b81507da2f28acbbfcc63872", "a7b6360ae22be7c5535dc5c807ee339fce5b737d4ff5fbf6ed8d2b2ff10d1e45"),
    "scripts/verify_engine3_stage_f_harness.py": (
        "2c0123fb46cbf71cc5295d6720c24dc84d5de4cf02b8082bd3e69f59a36c084d", "df028084a69e26448adbc804f3483e073120af047a9f5cdc5988b820d036c055"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md": (
        "270c82fb9a523c598e6b0d9c5394aba8f2f3e221d674787fc6818e2d80e15441", "fca5fc37d693be55e06e9a524e5173b1fb122ecd65675421df14bcc5efe3dedf"),
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json": (
        "245f2cabd2ad22e847e937cc089c0ecb2f19086aa1e1b6788cfaec3e232868d1", "2b46815abbad41b08d0f5e0fb597234aa87456167bc12b0f9a7c1f6ae18aa098"),
}
REBINDING_003_RECORDS = {
    "harness_amendment_004": ("docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_004.md", "26db52bee5e9d552458d61d3e1b6cdce059cefc838cd1c47413bc37ff7a3d941"),
    "calibration_freeze_manifest": ("docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json",
                                    "a88600bb392f9d853b07c8948ee41ce8af597f5a3025697c56dc5e6e0fefef97"),
}

# --- run order (frozen algorithm) -----------------------------------------------------------------
RUN_ORDER_ALGORITHM = "E3F_RUN_ORDER_V1"
RUN_ORDER_SEPARATOR = "\n"

# --- near-duplicate rule (deterministic) ----------------------------------------------------------
MIN_TOKENS = 4
JACCARD_NEAR = 0.5
SHINGLE = 5
_TOKEN = re.compile(r"\$?[a-z0-9]+(?:\.[0-9]+)?%?")
_SENTENCE = re.compile(r"(?<=[.?!])\s+")
BANNED_TOKENS = ("alder", "ridge", "cobalt", "july")
BANNED_PATTERNS = (r"\b8\s*%", r"\b9(?:\.00)?\s*%", r"\bmore than 8\b", r"\babove 8\b")

# --- schema neutrality rule -----------------------------------------------------------------------
STRUCTURAL_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf"})
NULLABLE_STRING = [{"type": "string"}, {"type": "null"}]
VOCABULARIES = {
    "schema_version": [OUTPUT_SCHEMA_VERSION],
    "requested_action": [e.value for e in ActionAttempt],
    "artifact_type": [e.value for e in ArtifactType],
    "claim_type": [e.value for e in ClaimType],
    "semantic_class": [e.value for e in ClaimSemanticClass],
    "reference_use_mode": [e.value for e in ReferenceUseMode],
    "claim_field": [e.value for e in ClaimField],
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENTENCE.split(text)) if s]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- evaluation prose corpus ----------------------------------------------------------------------


def _artifact_texts(artifact: dict) -> list[str]:
    out = [s["text"] for s in artifact.get("segments", []) if isinstance(s, dict) and "text" in s]
    out += [s[1] for s in artifact.get("segments", []) if isinstance(s, list) and len(s) == 2]
    if isinstance(artifact.get("rendered_text"), str):
        out.append(artifact["rendered_text"])
    return out


def _dedupe(corpus: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen, unique = set(), []
    for item in corpus:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def arm_a_corpus() -> list[tuple[str, str]]:
    """(source label, sentence) for the 48 Arm A fixed-prose artifacts only."""
    corpus: list[tuple[str, str]] = []
    stage_e = _load(STAGE_E_FIXTURE)
    for rec in stage_e["recordings"]:
        if rec["category"] in ARM_A_SEED_CATEGORIES and rec["recording_id"] not in ARM_A_SEED_EXCLUDED:
            for art in rec["proposal"]["artifacts"]:
                for t in _artifact_texts(art):
                    corpus += [(f"Arm A seed {rec['recording_id']} {art['artifact_id']}", s) for s in sentences(t)]
    for art in _load(O6_FROZEN)["artifacts"]:
        for t in _artifact_texts(art["fixed_prose"]) + [art["rendered_text"]]:
            corpus += [(f"Arm A O-6 {art['artifact_id']}", s) for s in sentences(t)]
    return _dedupe(corpus)


def evaluation_corpus() -> list[tuple[str, str]]:
    """(source label, sentence) for every evaluation prose source named by the owner."""
    corpus: list[tuple[str, str]] = []

    def add(label: str, text: str) -> None:
        corpus.extend((label, s) for s in sentences(text))

    stage_e = _load(STAGE_E_FIXTURE)
    for context in stage_e["contexts"]:
        for item in context["grounding_items"]:
            add(f"Stage E context {context['context_id']} {item['grounding_item_id']}", item["exact_value"])
    for rec in stage_e["recordings"]:
        for art in rec["proposal"]["artifacts"]:
            for t in _artifact_texts(art):
                add(f"Stage E {rec['recording_id']} {art['artifact_id']}", t)
    for edit in stage_e["human_edits"]:
        edited = edit["edited"]
        for t in _artifact_texts(edited if isinstance(edited, dict) else {}):
            add(f"Stage E human edit {edit['edit_id']}", t)

    for art in _load(O6_FROZEN)["artifacts"]:
        for t in _artifact_texts(art["fixed_prose"]) + [art["rendered_text"]]:
            add(f"Arm A O-6 {art['artifact_id']}", t)

    spec = _load(SPEC)
    for variant in spec["contexts"]["stage_f_variants"]:
        item = variant["delta"].get("add_grounding_item")
        if item:
            add(f"Arm G context {variant['context_id']} {item['grounding_item_id']}", item["exact_value"])
    for scenario in spec["scenarios"]:
        for key in ("goal", "channel", "tone_preference"):
            if isinstance(scenario.get(key), str):
                add(f"Arm G scenario {scenario['id']} {key}", scenario[key])

    for case in _load(DRY_RUN_INPUTS)["cases"]:
        raw = case.get("raw_model_output")
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError:
            value = None
        for art in value.get("artifacts", []) if isinstance(value, dict) else []:
            for t in _artifact_texts(art):
                add(f"Stage F dry run {case['case_id']}", t)
    return _dedupe(corpus)


def compare(text: str, corpus: list[tuple[str, str]]) -> list[dict]:
    """Exact and near-duplicate findings for one sentence against the corpus."""
    a = tokens(text)
    if len(a) < MIN_TOKENS:
        return []
    a_set = set(a)
    a_sh = {tuple(a[i:i + SHINGLE]) for i in range(len(a) - SHINGLE + 1)}
    findings = []
    for label, s in corpus:
        b = tokens(s)
        if len(b) < MIN_TOKENS:
            continue
        if a == b:
            findings.append({"kind": "EXACT", "source": label, "evaluation_text": s})
            continue
        b_set = set(b)
        jac = len(a_set & b_set) / len(a_set | b_set)
        shared = a_sh & {tuple(b[i:i + SHINGLE]) for i in range(len(b) - SHINGLE + 1)}
        if jac >= JACCARD_NEAR or shared:
            findings.append({
                "kind": "NEAR",
                "source": label,
                "evaluation_text": s,
                "jaccard": round(jac, 3),
                "shared_5_token_sequences": sorted(" ".join(x) for x in shared),
            })
    return findings


def banned_terms(text: str) -> list[str]:
    toks = set(tokens(text))
    hits = [t for t in BANNED_TOKENS if t in toks]
    hits += [p for p in BANNED_PATTERNS if re.search(p, text.casefold())]
    return hits


# --- schema neutrality ----------------------------------------------------------------------------


def schema_findings(schema: dict) -> list[str]:
    problems: list[str] = []

    def walk(node: object, path: str, name: str | None) -> None:
        if not isinstance(node, dict):
            problems.append(f"{path}: schema node is not an object")
            return
        extra = sorted(set(node) - STRUCTURAL_KEYWORDS)
        if extra:
            problems.append(f"{path}: non-structural keyword(s) {extra}")
        if "anyOf" in node and node["anyOf"] != NULLABLE_STRING:
            problems.append(f"{path}: anyOf is allowed only as nullable string")
        if "enum" in node:
            if name not in VOCABULARIES:
                problems.append(f"{path}: enum on a field without a locked vocabulary")
            elif node["enum"] != VOCABULARIES[name]:
                problems.append(f"{path}: enum is not exactly the full locked vocabulary for {name}")
        if node.get("type") == "object":
            props = node.get("properties", {})
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if sorted(node.get("required", [])) != sorted(props):
                problems.append(f"{path}: required must list exactly the object's keys")
            for key, child in props.items():
                walk(child, f"{path}.{key}", key)
        if "items" in node:
            walk(node["items"], f"{path}[]", None)

    walk(schema, "$", None)
    return problems


def _flat(text: str) -> str:
    return " ".join(text.replace("`", "").replace("**", "").replace("*", "").split())


# --- run order ------------------------------------------------------------------------------------


def order_key(arm: str, unit_id: str, repeat_number: int, prompt_bundle_sha256: str, schema_sha256: str) -> str:
    text = RUN_ORDER_SEPARATOR.join(
        [RUN_ORDER_ALGORITHM, arm, unit_id, str(repeat_number), prompt_bundle_sha256, schema_sha256])
    return sha(text)


def materialize_run_order(configuration_id: str, run_kind: str, slots: list[tuple[str, str, int]],
                          prompt_bundle_sha256: str, schema_sha256: str) -> list[dict]:
    rows = []
    for arm, unit_id, repeat_number in slots:
        slot_id = "|".join([configuration_id, run_kind, arm, unit_id, str(repeat_number)])
        rows.append({
            "run_slot_id": slot_id,
            "run_id": sha(slot_id)[:32],
            "arm": arm,
            "unit_id": unit_id,
            "repeat_number": repeat_number,
            "order_key_sha256": order_key(arm, unit_id, repeat_number, prompt_bundle_sha256, schema_sha256),
        })
    rows.sort(key=lambda r: (r["order_key_sha256"], r["run_slot_id"].encode("utf-8")))
    for i, r in enumerate(rows, start=1):
        r["order_index"] = i
    return rows


def pilot_slots() -> list[tuple[str, str, int]]:
    """Locked pilot: Arm G only, every spec scenario, repeats 1 and 2 (design section 9.4)."""
    return [("G", s["id"], r) for s in _load(SPEC)["scenarios"] for r in (1, 2)]


# --- post-freeze rebinding -----------------------------------------------------------------------


def unchanged_configuration(f: dict) -> dict:
    """The O-5 settings the rebinding attests unchanged, read from the original frozen record."""
    cfg, p, ro = f["frozen_configuration"], f["prompt"], f["run_order"]
    return {
        "model_id": cfg["model"]["model_id"],
        "prompt_version": p["prompt_version"],
        "prompt_bundle_sha256": p["prompt_bundle_sha256"],
        "system_prompt_sha256": p["system_prompt_sha256"],
        "arm_g_user_template_sha256": p["arm_g_user_template_sha256"],
        "arm_a_user_template_sha256": p["arm_a_user_template_sha256"],
        "schema_sha256": f["output_format_json_schema_sha256"],
        "configuration_sha256": f["configuration_sha256"],
        "generation": cfg["generation"],
        "transport": cfg["transport"],
        "retry_policy_id": f["retry_policy"]["id"],
        "provenance_policy_id": f["provenance_policy"]["id"],
        "run_order_algorithm": ro["algorithm"],
        "pilot_run_order_size": len(ro["pilot_run_order"]),
        "pilot_run_order_sha256": ro["pilot_run_order_sha256"],
        "e3_e_sem_001_resolved": f["governing_limitation"]["resolved"],
    }


def _original_records() -> dict:
    return {"original_o5_freeze_manifest": {"path": MANIFEST.relative_to(REPO).as_posix(), "sha256": ORIGINAL_MANIFEST_SHA256},
            "original_o5_frozen_configuration": {"path": FROZEN.relative_to(REPO).as_posix(), "sha256": ORIGINAL_FROZEN_SHA256}}


def _rebinding_001_findings(m: dict, f: dict, verifier_path: str) -> list[str]:
    problems: list[str] = []
    rb = _load(REBINDING) if REBINDING.exists() else {}
    if rb.get("rebinding_id") != REBINDING_ID or rb.get("amendment") != REBINDING_AMENDMENT:
        problems.append(f"rebinding record must be exactly {REBINDING_ID} for {REBINDING_AMENDMENT}")
    if rb.get("original_o5_records_modified") is not False or rb.get("o5_status") != "RESOLVED_FROZEN":
        problems.append("rebinding must record the original O-5 records unmodified and O-5 RESOLVED_FROZEN")
    if any(rb.get(k) != v for k, v in _original_records().items()):
        problems.append(f"{REBINDING_ID} does not bind the original O-5 records")
    if rb.get("o5_verifier") != {"path": verifier_path, "original_sha256": m["verifier_sha256"],
                                 "post_sha256": REBINDING_VERIFIER[1]} or m["verifier_sha256"] != REBINDING_VERIFIER[0]:
        problems.append(f"{REBINDING_ID} verifier hashes differ from the approved pair")
    expected_inputs = {rel: {"original_sha256": o, "post_sha256": n} for rel, (o, n) in REBOUND_LOCKED_INPUTS.items()}
    if rb.get("rebound_locked_inputs") != expected_inputs:
        problems.append("rebinding locked-input list or hashes differ from the approved set")
    expected_records = {k: {"path": rel, "sha256": d} for k, (rel, d) in REBINDING_RECORDS.items()}
    if rb.get("bound_records") != expected_records:
        problems.append("rebinding bound records differ from the approved set")
    if rb.get("unchanged_o5_configuration") != unchanged_configuration(f):
        problems.append("rebinding's unchanged O-5 configuration does not match the original frozen record")
    if REBINDING.exists() and file_sha(REBINDING) != REBINDING_SHA256:
        problems.append(f"{REBINDING_ID} record changed after {REBINDING_002_ID} bound it")
    return problems


def _rebinding_002_findings(m: dict, f: dict, verifier_path: str) -> list[str]:
    problems: list[str] = []
    rb = _load(REBINDING_002) if REBINDING_002.exists() else {}
    if rb.get("rebinding_id") != REBINDING_002_ID or rb.get("amendment") != REBINDING_002_AMENDMENT:
        problems.append(f"rebinding record must be exactly {REBINDING_002_ID} for {REBINDING_002_AMENDMENT}")
    if (rb.get("original_o5_records_modified") is not False or rb.get("previous_rebinding_modified") is not False
            or rb.get("o5_status") != "RESOLVED_FROZEN"):
        problems.append(f"{REBINDING_002_ID} must record the original O-5 records and {REBINDING_ID} unmodified "
                        "and O-5 RESOLVED_FROZEN")
    if any(rb.get(k) != v for k, v in _original_records().items()):
        problems.append(f"{REBINDING_002_ID} does not bind the original O-5 records")
    if rb.get("previous_rebinding") != {"path": REBINDING_PATH, "sha256": REBINDING_SHA256}:
        problems.append(f"{REBINDING_002_ID} does not bind {REBINDING_ID} by its exact hash")
    if rb.get("o5_verifier") != {"path": verifier_path, "previous_sha256": REBINDING_VERIFIER[1],
                                 "post_sha256": REBINDING_002_VERIFIER[1]}:
        problems.append(f"{REBINDING_002_ID} verifier hashes differ from the approved pair")
    expected_inputs = {rel: {"freeze_sha256": m["locked_inputs_sha256"].get(rel), "previous_sha256": o, "post_sha256": n}
                       for rel, (o, n) in REBOUND_LOCKED_INPUTS_002.items()}
    if rb.get("rebound_locked_inputs") != expected_inputs:
        problems.append(f"{REBINDING_002_ID} locked-input list or hashes differ from the approved set")
    expected_records = {k: {"path": rel, "sha256": d} for k, (rel, d) in REBINDING_002_RECORDS.items()}
    if rb.get("bound_records") != expected_records:
        problems.append(f"{REBINDING_002_ID} bound records differ from the approved set")
    if rb.get("unchanged_o5_configuration") != unchanged_configuration(f):
        problems.append(f"{REBINDING_002_ID}'s unchanged O-5 configuration does not match the original frozen record")
    if REBINDING_002.exists() and file_sha(REBINDING_002) != REBINDING_002_SHA256:
        problems.append(f"{REBINDING_002_ID} record changed after {REBINDING_003_ID} bound it")
    return problems


def _rebinding_003_findings(m: dict, f: dict, verifier_path: str, verifier_sha: str) -> list[str]:
    problems: list[str] = []
    rb = _load(REBINDING_003) if REBINDING_003.exists() else {}
    if rb.get("rebinding_id") != REBINDING_003_ID or rb.get("amendment") != REBINDING_003_AMENDMENT:
        problems.append(f"rebinding record must be exactly {REBINDING_003_ID} for {REBINDING_003_AMENDMENT}")
    if (rb.get("original_o5_records_modified") is not False or rb.get("previous_rebindings_modified") is not False
            or rb.get("o5_status") != "RESOLVED_FROZEN"):
        problems.append(f"{REBINDING_003_ID} must record the original O-5 records and earlier rebindings unmodified "
                        "and O-5 RESOLVED_FROZEN")
    if any(rb.get(k) != v for k, v in _original_records().items()):
        problems.append(f"{REBINDING_003_ID} does not bind the original O-5 records")
    if rb.get("previous_rebinding") != {"path": REBINDING_002_PATH, "sha256": REBINDING_002_SHA256}:
        problems.append(f"{REBINDING_003_ID} does not bind {REBINDING_002_ID} by its exact hash")
    if rb.get("o5_verifier") != {"path": verifier_path, "previous_sha256": REBINDING_002_VERIFIER[1],
                                 "post_sha256": verifier_sha}:
        problems.append("O-5 verifier hash is neither the original nor the rebound hash")
    expected_inputs = {rel: {"freeze_sha256": m["locked_inputs_sha256"].get(rel), "previous_sha256": o, "post_sha256": n}
                       for rel, (o, n) in REBOUND_LOCKED_INPUTS_003.items()}
    if rb.get("rebound_locked_inputs") != expected_inputs:
        problems.append(f"{REBINDING_003_ID} locked-input list or hashes differ from the approved set")
    expected_records = {k: {"path": rel, "sha256": d} for k, (rel, d) in REBINDING_003_RECORDS.items()}
    if rb.get("bound_records") != expected_records:
        problems.append(f"{REBINDING_003_ID} bound records differ from the approved set")
    if rb.get("unchanged_o5_configuration") != unchanged_configuration(f):
        problems.append(f"{REBINDING_003_ID}'s unchanged O-5 configuration does not match the original frozen record")
    return problems


def rebinding_findings(m: dict, f: dict, verifier_sha: str) -> tuple[list[str], dict[str, int]]:
    """Exact check of both rebindings, in order, and of every locked input. Any other divergence fails."""
    problems: list[str] = []
    originals = {MANIFEST: ORIGINAL_MANIFEST_SHA256, FROZEN: ORIGINAL_FROZEN_SHA256}
    for path, digest in originals.items():
        if file_sha(path) != digest:
            problems.append(f"original O-5 record not byte-identical or not bound: {path.name}")
    if m["frozen_record_sha256"] != ORIGINAL_FROZEN_SHA256:
        problems.append("original freeze manifest no longer binds the original frozen record")
    verifier_path = Path(__file__).resolve().relative_to(REPO).as_posix()
    problems += _rebinding_001_findings(m, f, verifier_path)
    problems += _rebinding_002_findings(m, f, verifier_path)
    problems += _rebinding_003_findings(m, f, verifier_path, verifier_sha)
    bound = dict(list(REBINDING_RECORDS.values()) + list(REBINDING_002_RECORDS.values())
                 + list(REBINDING_003_RECORDS.values()))
    problems += [f"bound record hash mismatch: {rel}" for rel, d in bound.items()
                 if not (REPO / rel).exists() or file_sha(REPO / rel) != d]

    counts = {"unchanged": 0, "rebound": 0}
    for rel, original in m["locked_inputs_sha256"].items():
        # The approved chain for this file: freeze -> REBINDING_001 -> REBINDING_002 -> REBINDING_003. Each step
        # must start from the hash the previous step ended on.
        expected, rebound = original, False
        for chain in (REBOUND_LOCKED_INPUTS, REBOUND_LOCKED_INPUTS_002, REBOUND_LOCKED_INPUTS_003):
            if rel in chain:
                before, after = chain[rel]
                if before != expected:
                    problems.append(f"rebinding chain broken for {rel}")
                expected, rebound = after, True
        actual = file_sha(REPO / rel)
        if rebound and actual == original:
            problems.append(f"rebound locked input still at its freeze hash (amendment not applied): {rel}")
        elif actual != expected:
            problems.append(f"locked input changed since freeze: {rel}")
        elif rebound:
            counts["rebound"] += 1
        else:
            counts["unchanged"] += 1
    if (set(REBOUND_LOCKED_INPUTS) | set(REBOUND_LOCKED_INPUTS_002) | set(REBOUND_LOCKED_INPUTS_003)) - set(
            m["locked_inputs_sha256"]):
        problems.append("rebinding names a file that was not a locked input")
    return problems, counts


# --- main -----------------------------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    def report(ok: bool, line: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {line}")

    f = _load(FROZEN)
    m = _load(MANIFEST)
    p = f["prompt"]
    cfg = f["frozen_configuration"]
    texts = {"system_prompt": p["system_prompt"], "arm_g_user_template": p["arm_g_user_template"],
             "arm_a_user_template": p["arm_a_user_template"]}

    # 1. status
    n = len(failures)
    for rec, name in ((f, "frozen record"), (m, "freeze manifest")):
        if rec["status"] != STATUS or rec["pre_run_status"] != PRE_RUN or rec["pilot_readiness"] is not False:
            failures.append(f"status: {name}")
        if any(v not in (False, 0) for v in rec["execution_record"].values()):
            failures.append(f"status: {name} shows execution")
        if rec["governing_limitation"]["resolved"] is not False:
            failures.append(f"status: {name} marks E3-E-SEM-001 resolved")
    if f["frozen"] is not True:
        failures.append("status: frozen flag")
    report(len(failures) == n, f"status {STATUS}; O-3 {f['pre_run_status']['O-3']}, O-5 {f['pre_run_status']['O-5']}, O-6 {f['pre_run_status']['O-6']}; pilot_readiness false; no execution; E3-E-SEM-001 unresolved")

    # 2. hashes
    n = len(failures)
    for key, value in texts.items():
        if sha(value) != p[f"{key}_sha256"]:
            failures.append(f"hash: {key}")
    bundle = sha(canon(texts))
    schema_sha = sha(canon(f["output_format_json_schema"]))
    config_sha = sha(canon(cfg))
    if bundle != p["prompt_bundle_sha256"] or bundle != cfg["prompt"]["prompt_bundle_sha256"]:
        failures.append("hash: prompt bundle")
    if schema_sha != f["output_format_json_schema_sha256"] or schema_sha != cfg["structured_output"]["schema_sha256"]:
        failures.append("hash: output format schema")
    if config_sha != f["configuration_sha256"]:
        failures.append("hash: frozen configuration")
    report(len(failures) == n, f"hashes recomputed: bundle {bundle[:12]}, schema {schema_sha[:12]}, configuration {config_sha[:12]}")

    # 3. manifest bindings and locked inputs
    n = len(failures)
    verifier_sha = file_sha(Path(__file__).resolve())
    bindings = {
        "frozen_record_sha256": file_sha(FROZEN), "review_sha256": file_sha(REVIEW),
        "human_reference_issue_sha256": file_sha(ISSUE),
        "configuration_sha256": config_sha, "prompt_bundle_sha256": bundle, "schema_sha256": schema_sha,
        "pilot_run_order_sha256": f["run_order"]["pilot_run_order_sha256"],
    }
    for key, value in bindings.items():
        if m[key] != value:
            failures.append(f"manifest binding: {key}")
    problems, counts = rebinding_findings(m, f, verifier_sha)
    failures += problems
    report(len(failures) == n,
           f"original freeze manifest {ORIGINAL_MANIFEST_SHA256[:12]} and frozen record {ORIGINAL_FROZEN_SHA256[:12]} "
           f"byte-identical; bindings ({len(bindings) + 1}); locked inputs {counts['unchanged']} unchanged + "
           f"{counts['rebound']} rebound under {REBINDING_ID} ({REBINDING_AMENDMENT}), {REBINDING_002_ID} "
           f"({REBINDING_002_AMENDMENT}), and {REBINDING_003_ID} ({REBINDING_003_AMENDMENT}) of "
           f"{len(m['locked_inputs_sha256'])}; verifier original {m['verifier_sha256'][:12]} -> "
           f"{REBINDING_VERIFIER[1][:12]} -> {REBINDING_002_VERIFIER[1][:12]} -> {verifier_sha[:12]}")
    for rel, (old, new) in REBOUND_LOCKED_INPUTS.items():
        print(f"      {REBINDING_ID} {rel}: freeze {old[:12]} -> {new[:12]}")
    for rel, (old, new) in REBOUND_LOCKED_INPUTS_002.items():
        print(f"      {REBINDING_002_ID} {rel}: {old[:12]} -> {new[:12]}")
    for rel, (old, new) in REBOUND_LOCKED_INPUTS_003.items():
        print(f"      {REBINDING_003_ID} {rel}: {old[:12]} -> {new[:12]}")
    for x in problems:
        print(f"      {x}")

    # 4. review copies
    n = len(failures)
    blocks = [b.split("\n```", 1)[0] for b in REVIEW.read_text(encoding="utf-8").split("```text\n")[1:]]
    if blocks != list(texts.values()):
        failures.append("review: prompt copies differ from the frozen record")
    report(len(failures) == n, f"review prompt copies byte-identical ({len(blocks)} blocks)")

    # 5. thinking instruction
    n = len(failures)
    system = p["system_prompt"]
    if system.count(THINKING_SENTENCE) != 1 or (THINKING_SENTENCE + " " + JSON_INSTRUCTION) not in system:
        failures.append("thinking instruction: not exactly once immediately before the JSON instruction")
    seg_keys = set(f["output_format_json_schema"]["properties"]["artifacts"]["items"]["properties"]["segments"]["items"]["properties"])
    top_keys = set(f["output_format_json_schema"]["properties"])
    if any(re.search("reason|think|thought|rationale", k) for k in seg_keys | top_keys):
        failures.append("thinking instruction: schema contains a reasoning field")
    if re.search(r"(?i)(explain|show|reveal|describe) (your )?(reasoning|thinking)", system + p["arm_g_user_template"] + p["arm_a_user_template"]):
        failures.append("thinking instruction: prompt asks the model to reveal reasoning")
    report(len(failures) == n, "thinking instruction once, immediately before the JSON instruction; no reasoning field; no request to reveal reasoning")

    # 6. schema neutrality
    problems = schema_findings(f["output_format_json_schema"])
    failures += [f"schema: {x}" for x in problems]
    report(not problems, f"schema semantic neutrality: keywords {sorted(STRUCTURAL_KEYWORDS)} only; full locked vocabularies; no cross-field rule")
    for x in problems:
        print(f"      {x}")

    # 7. normative definitions verbatim
    protocol = _flat(PROTOCOL.read_text(encoding="utf-8"))
    prompt_flat = _flat(system + "\n" + p["arm_g_user_template"])
    missing = [s for s in p["normative_definitions_verbatim"] if _flat(s) not in protocol or _flat(s) not in prompt_flat]
    failures += [f"normative text not verbatim: {s}" for s in missing]
    report(not missing, f"protocol normative definitions verbatim in prompt: {len(p['normative_definitions_verbatim']) - len(missing)}/{len(p['normative_definitions_verbatim'])}")

    # 8. contamination
    corpus = evaluation_corpus()
    n = len(failures)
    example_findings = 0
    for ex in p["worked_examples"]:
        if ex["text"] not in system:
            failures.append(f"worked example not in system prompt: {ex['id']}")
        hits = banned_terms(ex["text"])
        found = [x for s in sentences(ex["text"]) for x in compare(s, corpus)]
        example_findings += len(found) + len(hits)
        if hits or found:
            failures.append(f"worked example contaminated: {ex['id']}")
    banned_any = {k: banned_terms(v) for k, v in texts.items() if banned_terms(v)}
    if banned_any:
        failures.append(f"banned terms in prompt: {banned_any}")
    allow = {a["prompt_sentence"] for a in p["prompt_scan_allowlist"]}
    unexplained, allowed = [], 0
    for key, value in texts.items():
        for line in value.splitlines():
            for s in sentences(line):
                if compare(s, corpus):
                    if s in allow:
                        allowed += 1
                    else:
                        unexplained.append(s)
    failures += [f"prompt sentence near evaluation prose: {s}" for s in unexplained]
    report(len(failures) == n, f"contamination: {len(p['worked_examples'])} worked examples vs {len(corpus)} evaluation sentences, {example_findings} findings; banned terms {banned_any or 'none'}; whole-prompt scan {allowed} allowlisted, {len(unexplained)} unexplained")
    for s in unexplained:
        print(f"      unexplained: {s}")

    # 9. pilot run order
    n = len(failures)
    ro = f["run_order"]
    rows = materialize_run_order(cfg["experiment_configuration_id"], "PILOT", pilot_slots(), bundle, schema_sha)
    if ro["algorithm"] != RUN_ORDER_ALGORITHM or ro["pilot_run_order"] != rows:
        failures.append("run order: pilot list does not match E3F_RUN_ORDER_V1")
    if sha(canon(rows)) != ro["pilot_run_order_sha256"] or len(rows) != 44:
        failures.append("run order: pilot list hash or size")
    report(len(failures) == n, f"pilot run order {RUN_ORDER_ALGORITHM}: {len(rows)} slots, sha256 {sha(canon(rows))[:12]}")

    # 10. human-reference issue
    n = len(failures)
    issue = ISSUE.read_text(encoding="utf-8")
    if f"`{ISSUE_STATUS}`" not in issue or m["human_reference_issue_status"] != ISSUE_STATUS:
        failures.append("issue record: status missing")
    resolution = RESOLUTION.read_text(encoding="utf-8") if RESOLUTION.exists() else ""
    if f"`{m['human_reference_issue_sha256']}`" not in resolution:
        failures.append("resolution record does not cite the original issue record")
    row = re.search(r"^\| `E3F-ISSUE-HR-001` \| `([A-Z_]+)`", resolution, re.MULTILINE)
    report(len(failures) == n, f"human-reference issue record unchanged ({ISSUE_STATUS} at O-5 freeze); "
                               f"resolution record status {row.group(1) if row else None}")

    print(f"\nRESULT {'FAIL' if failures else 'PASS'}: {len(failures)} failure(s). Offline document verification; no model, credential, or network used.")
    for x in failures:
        print(f"  - {x}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
