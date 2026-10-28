"""Offline verifier for the Engine 3 Stage F human-annotator calibration corpus
(CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED).

Checks, without any model call, credential read, or network access:

1. corpus structure and training labels: every label parses under the harness's strict record parsers, cites
   only ids shown for its account, uses INTERNAL exactly in internal artifacts, and the corpus records that no
   calibration response was collected and no model label is used as authority;
2. field coverage: every value of every model-controlled field the protocol uses, both statuses, both flags,
   and every attestation both ways;
3. contamination: every calibration sentence (corpus and calibration guide) against six prohibited classes,
   using the O-5 comparison rule unchanged (exact, near, shared 5-token sequences, banned terms), plus shared
   distinctive 4-token sequences as in the operational guide verifier, plus calibration-specific banned terms;
4. the freeze: the freeze manifest binds the corpus, guide, and this verifier by SHA-256, records the results of
   this run, and keeps O-3 PRE_RUN_REQUIRED and pilot readiness false.

Exit status 0 only if every check passes. This is a software check of documents, not evidence about
annotation reliability.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import verify_engine3_stage_f_o5_freeze as o5  # noqa: E402
import verify_engine3_stage_f_operational_guide as og  # noqa: E402
from baec_app.engine3.enums import (  # noqa: E402
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    PreparationChannel,
    PreparationGoal,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from baec_app.engine3.errors import Engine3ValidationError  # noqa: E402
from baec_app.engine3.models import ClaimField  # noqa: E402
from experiments.engine3_stage_f.annotation import (  # noqa: E402
    POLICY_ATTESTATIONS,
    REFERENCE_FIELDS,
    SPECIFICITY_CODES,
    AnnotationStatus,
    attestation_from_dict,
    segment_annotation_from_dict,
)

DOCS = REPO / "docs" / "engine3"
CORPUS = DOCS / "ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json"
GUIDE = DOCS / "ENGINE3_STAGE_F_HUMAN_CALIBRATION_GUIDE.md"
FREEZE_MANIFEST = DOCS / "ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json"
DRY_RUN_ORACLE = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_ORACLE.json"

BLOCKER = "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED"
FROZEN = "RESOLVED_FROZEN"
CORPUS_STATUS = "FROZEN_CALIBRATION_CORPUS"
GUIDE_STATUS_LINE = ("**Status:** `FROZEN_CALIBRATION_GUIDE`; `OWNER_REVIEWED_APPROVED` (human project-owner review "
                     "completed 2026-10-15)\\")
FREEZE_STATUS = "CALIBRATION_CORPUS_FROZEN"
PRE_RUN = {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
PILOT_BLOCKERS = {"O-3": "PRE_RUN_REQUIRED"}

# Calibration-specific banned terms, added to the O-5 list (alder, ridge, cobalt, july, 8%, 9%, 9.00%, ...):
# the evaluation domain's renewal vocabulary and the operational guide's fictional account.
CALIBRATION_BANNED_TOKENS = ("lakemoor", "courier", "library", "renewal", "imaging", "fee", "fees")
CALIBRATION_BANNED_PATTERNS = (r"per-route", r"spring schedule", r"\$46", r"\$40", r"\$25")
_ID_LIKE = re.compile(r"^[A-Za-z0-9_./:-]+$")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _sentences(texts) -> list[str]:
    out: list[str] = []
    for text in texts:
        out += o5.sentences(text)
    return list(dict.fromkeys(out))


# --- prohibited corpora ---------------------------------------------------------------------------


def _dry_run_texts() -> list[str]:
    """Every prose string in the dry-run inputs (raw model output parsed) and the dry-run oracle."""
    texts: list[str] = []
    for case in o5._load(o5.DRY_RUN_INPUTS)["cases"]:
        for key, value in case.items():
            if key == "raw_model_output" and isinstance(value, str):
                try:
                    texts += list(_strings(json.loads(value)))
                except json.JSONDecodeError:
                    texts.append(value)
            else:
                texts += list(_strings(value))
    texts += list(_strings(o5._load(DRY_RUN_ORACLE)))
    return [t for t in texts if not _ID_LIKE.match(t)]


def prohibited_corpora() -> dict[str, list[tuple[str, str]]]:
    """Class name -> (source label, sentence). The six classes named by the resolution record, section 4.2."""
    evaluation = o5.evaluation_corpus()
    o5_examples = o5._load(o5.FROZEN)["prompt"]["worked_examples"]
    classes = {
        "arm_a_artifacts_48": o5.arm_a_corpus(),
        "arm_g_scenario_and_evaluation_material": [
            (label, s) for label, s in evaluation if label.startswith(("Arm G", "Stage E context"))],
        "stage_e_prerecorded_prose": [
            (label, s) for label, s in evaluation
            if label.startswith("Stage E ") and not label.startswith("Stage E context")],
        "stage_f_dry_run_prose": [("Stage F dry run", s) for s in _sentences(_dry_run_texts())],
        "operational_guide_worked_examples": [("operational guide example", s)
                                              for s in _sentences(og.worked_examples())],
        "o5_prompt_worked_examples": [(f"O-5 example {e['id']}", s) for e in o5_examples
                                      for s in o5.sentences(e["text"])],
    }
    return {name: o5._dedupe(rows) for name, rows in classes.items()}


# --- calibration prose ----------------------------------------------------------------------------


def calibration_sentences(corpus: dict, guide_text: str) -> list[tuple[str, str]]:
    """(where, sentence) for every prose string in the corpus and every content line of the guide."""
    out = [("corpus", s) for s in _sentences(t for t in _strings(corpus) if not _ID_LIKE.match(t))]
    out += [("guide", s) for s in _sentences(og.content_lines(guide_text))]
    return out


def banned(text: str) -> list[str]:
    toks = set(o5.tokens(text))
    hits = o5.banned_terms(text) + [t for t in CALIBRATION_BANNED_TOKENS if t in toks]
    return hits + [p for p in CALIBRATION_BANNED_PATTERNS if re.search(p, text.casefold())]


def contamination_audit(corpus: dict, guide_text: str) -> tuple[dict, list[str]]:
    classes = prohibited_corpora()
    calibration = calibration_sentences(corpus, guide_text)
    findings: list[str] = []
    result: dict = {"calibration_sentences": len(calibration), "classes": {}}
    for name, rows in classes.items():
        grams = og.corpus_ngram_set(rows)
        counts = {"exact": 0, "near": 0, f"distinctive_{og.NGRAM}_grams": 0}
        for where, s in calibration:
            # One finding per (calibration sentence, prohibited sentence) pair, however many sources repeat it.
            matched = {(f["kind"], f["evaluation_text"]) for f in o5.compare(s, rows)}
            for kind, text in sorted(matched):
                key = "exact" if kind == "EXACT" else "near"
                counts[key] += 1
                findings.append(f"{name}: {key} ({where}) {s!r} ~ {text!r}")
            for gram in og.distinctive_ngrams(s, grams):
                counts[f"distinctive_{og.NGRAM}_grams"] += 1
                findings.append(f"{name}: distinctive {og.NGRAM}-gram {gram!r} ({where}) in {s!r}")
        result["classes"][name] = {"sentences": len(rows), **counts}
    banned_hits = [(where, s, banned(s)) for where, s in calibration if banned(s)]
    findings += [f"banned term(s) {hits} ({where}) in {s!r}" for where, s, hits in banned_hits]
    totals = {k: sum(c[k] for c in result["classes"].values())
              for k in ("exact", "near", f"distinctive_{og.NGRAM}_grams")}
    result.update({"totals": totals, "banned_term_findings": len(banned_hits),
                   "unexplained_findings": len(findings), "allowlisted": 0, "passed": not findings})
    return result, findings


# --- labels and coverage --------------------------------------------------------------------------


def label_findings(corpus: dict) -> tuple[list[str], list[tuple]]:
    """Structural problems, and (item, segment annotation, explanation, boundary) rows for coverage."""
    problems: list[str] = []
    rows: list[tuple] = []
    if corpus.get("status") != CORPUS_STATUS or corpus.get("all_data_synthetic") is not True:
        problems.append(f"corpus must be {CORPUS_STATUS} and all-synthetic")
    if (corpus.get("evaluation_material") is not False or corpus.get("calibration_responses_collected") is not False
            or corpus.get("model_generated_labels_used_as_authority") is not False
            or corpus.get("label_authority") != "HUMAN_REFERENCE_TRAINING_LABELS_ONLY"):
        problems.append("corpus must be training-only material with no responses and no model labels as authority")
    accounts = {a["account_id"]: a for a in corpus.get("accounts", [])}
    for account in accounts.values():
        try:
            PreparationGoal(account["preparation_goal"])
            PreparationChannel(account["channel_scope"])
            for g in account["grounding_items"]:
                ExternalReferenceDisposition(g["disposition"])
                SourceIdentityDisclosure(g["source_identity_disclosure"])
        except (KeyError, ValueError) as exc:
            problems.append(f"{account.get('account_id')}: invalid account or grounding field: {exc}")
        ids = [g["grounding_item_id"] for g in account["grounding_items"]]
        if len(ids) != len(set(ids)) or not set(account.get("instruction_like_grounding_items", [])) <= set(ids):
            problems.append(f"{account['account_id']}: grounding ids not unique or instruction-like ids unknown")
    seen_items, seen_segments = set(), set()
    for item in corpus.get("items", []):
        item_id, packet, key = item["item_id"], item["packet"], item["training_key"]
        if item_id in seen_items:
            problems.append(f"duplicate item {item_id}")
        seen_items.add(item_id)
        account = accounts.get(item["account_id"])
        if account is None:
            problems.append(f"{item_id}: unknown account")
            continue
        shown = {g["grounding_item_id"] for g in account["grounding_items"]}
        internal = packet["artifact_type"] == ArtifactType.INTERNAL_WHY_NOW_SUMMARY.value
        if (packet["audience"] != ("INTERNAL" if internal else "BUYER_FACING")
                or packet["context_label"] != account["context_label"]
                or packet["preparation_goal"] != account["preparation_goal"]
                or packet["channel_scope"] != account["channel_scope"]):
            problems.append(f"{item_id}: packet audience, context, goal, or channel inconsistent")
        seg_ids = [s["packet_segment_id"] for s in packet["segments"]]
        if ([s["ordinal"] for s in packet["segments"]] != list(range(1, len(seg_ids) + 1))
                or packet["rendered_text"] != " ".join(s["text"] for s in packet["segments"])):
            problems.append(f"{item_id}: segment ordinals or rendered text inconsistent")
        if [s["packet_segment_id"] for s in key["segments"]] != seg_ids or seen_segments & set(seg_ids):
            problems.append(f"{item_id}: training key does not align with the packet segments")
        seen_segments |= set(seg_ids)
        try:
            attestation = attestation_from_dict(key["attestation"]["label"])
        except (Engine3ValidationError, KeyError, TypeError) as exc:
            problems.append(f"{item_id}: attestation label invalid: {exc}")
            attestation = None
        for k in key["segments"]:
            try:
                decision = segment_annotation_from_dict(k["packet_segment_id"], k["label"])
            except (Engine3ValidationError, KeyError, TypeError) as exc:
                problems.append(f"{k['packet_segment_id']}: label invalid: {exc}")
                continue
            if decision.status is AnnotationStatus.ADJUDICATION_REQUIRED:
                problems.append(f"{k['packet_segment_id']}: a training key gives a final status")
            fields = decision.fields
            if fields is not None:
                if not fields.cited_ids() <= shown:
                    problems.append(f"{k['packet_segment_id']}: cites ids not shown for its account")
                if (fields.reference_use_mode is ReferenceUseMode.INTERNAL) != internal:
                    problems.append(f"{k['packet_segment_id']}: INTERNAL must be used exactly in internal artifacts")
            if not k.get("explanation") or k.get("boundary_case") != ("Boundary:" in k["explanation"]):
                problems.append(f"{k['packet_segment_id']}: explanation missing or boundary flag inconsistent")
            rows.append((item, decision, attestation, k.get("boundary_case")))
    return problems, rows


def field_coverage(corpus: dict, rows: list[tuple]) -> tuple[dict, list[str]]:
    scorable = [d.fields for _, d, _, _ in rows if d.fields is not None]
    questions = [f for f in scorable if f.claim_type is ClaimType.QUESTION]
    attestations = {id(a): a for _, _, a, _ in rows if a is not None}.values()
    grounding = [g for a in corpus["accounts"] for g in a["grounding_items"]]
    observed = {
        "claim_type": {f.claim_type.value for f in scorable},
        "semantic_class": {f.semantic_class.value for f in scorable},
        "grounding_refs": {"empty" if not f.grounding_refs else "cited" for f in scorable},
        "reference_use_mode": {f.reference_use_mode.value for f in scorable},
        "source_identity_refs": {"empty" if not f.source_identity_refs else "cited" for f in scorable},
        "claim_field": {f.claim_field.value for f in scorable},
        "declared_value": {"null" if f.declared_value is None else "set" for f in scorable},
        "specificity": {f.specificity for f in scorable},
        "question_has_premise": {f.question_has_premise for f in questions},
        "source_instruction_ref": {"null" if f.source_instruction_ref is None else "set" for f in scorable},
        "prescribes_buyer_choice": {f.prescribes_buyer_choice for f in scorable},
        "status": {d.status.value for _, d, _, _ in rows},
        "formatting_variant": {d.formatting_variant for _, d, _, _ in rows if d.fields is not None},
        "mixed_reference_modes": {d.mixed_reference_modes for _, d, _, _ in rows if d.fields is not None},
        "artifact_type": {i["packet"]["artifact_type"] for i, _, _, _ in rows},
        "source_identity_disclosure": {g["source_identity_disclosure"] for g in grounding},
        "disposition": {g["disposition"] for g in grounding},
        **{f"attestation.{name}": {getattr(a, name) for a in attestations} for name in POLICY_ATTESTATIONS},
    }
    required = {
        "claim_type": {e.value for e in ClaimType},
        "semantic_class": {e.value for e in ClaimSemanticClass},
        "grounding_refs": {"empty", "cited"},
        "reference_use_mode": {e.value for e in ReferenceUseMode},
        "source_identity_refs": {"empty", "cited"},
        "claim_field": {e.value for e in ClaimField},
        "declared_value": {"null", "set"},
        "specificity": set(SPECIFICITY_CODES),
        "question_has_premise": {True, False},
        "source_instruction_ref": {"null", "set"},
        "prescribes_buyer_choice": {True, False},
        "status": {AnnotationStatus.SCORABLE.value, AnnotationStatus.UNSCORABLE_SEGMENT_STRUCTURE.value},
        "formatting_variant": {True, False},
        "mixed_reference_modes": {True, False},
        "artifact_type": {e.value for e in ArtifactType},
        "source_identity_disclosure": {SourceIdentityDisclosure.ALLOWED.value, SourceIdentityDisclosure.NOT_ALLOWED.value},
        "disposition": {ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED.value,
                        ExternalReferenceDisposition.PARAPHRASE_ALLOWED.value,
                        ExternalReferenceDisposition.INTERNAL_ONLY.value},
        **{f"attestation.{name}": {True, False} for name in POLICY_ATTESTATIONS},
    }
    problems = [f"coverage: {k} missing {sorted(map(str, required[k] - observed[k]))}"
                for k in required if required[k] - observed[k]]
    # The eleven fields, in locked order, must all be covered.
    problems += [f"coverage: model-controlled field {f} not covered" for f in REFERENCE_FIELDS if f not in observed]
    instruction_ids = {i for a in corpus["accounts"] for i in a.get("instruction_like_grounding_items", [])}
    neutral_notes = [f for f in scorable if f.source_instruction_ref is None and set(f.grounding_refs) & instruction_ids]
    if not instruction_ids or not neutral_notes:
        problems.append("coverage: instruction-like grounding and a neutral note of it are required")
    sizes = {
        "accounts": len(corpus["accounts"]),
        "grounding_items": len(grounding),
        "items": len(corpus["items"]),
        "segments": len(rows),
        "scorable_segments": len(scorable),
        "unscorable_segments": sum(d.status is AnnotationStatus.UNSCORABLE_SEGMENT_STRUCTURE for _, d, _, _ in rows),
        "boundary_case_segments": sum(bool(b) for _, _, _, b in rows),
        "question_segments": len(questions),
    }
    coverage = {k: sorted(observed[k], key=str) for k in required}
    coverage["eleven_model_controlled_fields_covered"] = not problems
    return {"corpus_size": sizes, "domains": [a["domain"] for a in corpus["accounts"]],
            "field_coverage": coverage, "passed": not problems}, problems


# --- main -----------------------------------------------------------------------------------------


def run_checks() -> tuple[list[str], dict]:
    failures: list[str] = []
    results: dict = {}

    def report(ok: bool, line: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {line}")

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    guide_text = GUIDE.read_text(encoding="utf-8")

    # 1. labels
    n = len(failures)
    problems, rows = label_findings(corpus)
    failures += problems
    results["label_validation"] = {"segments_parsed": len(rows), "problems": len(problems), "passed": not problems}
    report(len(failures) == n, f"training labels: {len(rows)} segment labels and {len(corpus['items'])} attestations "
                               f"parse under the harness record parsers; {len(problems)} problems")
    for x in problems:
        print(f"      {x}")

    # 2. coverage
    n = len(failures)
    coverage, problems = field_coverage(corpus, rows)
    failures += problems
    results["coverage"] = coverage
    report(len(failures) == n, f"field coverage: {coverage['corpus_size']}; all eleven model-controlled fields, both "
                               f"statuses, both flags, every attestation both ways")
    for x in problems:
        print(f"      {x}")

    # 3. contamination
    n = len(failures)
    audit, findings = contamination_audit(corpus, guide_text)
    failures += findings
    results["contamination_audit"] = audit
    report(len(failures) == n,
           f"contamination: {audit['calibration_sentences']} calibration sentences vs six prohibited classes: "
           f"exact {audit['totals']['exact']}, near {audit['totals']['near']}, distinctive {og.NGRAM}-grams "
           f"{audit['totals'][f'distinctive_{og.NGRAM}_grams']}, banned {audit['banned_term_findings']}; "
           f"{audit['unexplained_findings']} unexplained")
    for name, c in audit["classes"].items():
        print(f"      {name}: {c}")
    for x in findings:
        print(f"      {x}")

    # 4. freeze
    n = len(failures)
    freeze = json.loads(FREEZE_MANIFEST.read_text(encoding="utf-8")) if FREEZE_MANIFEST.exists() else {}
    if guide_text.count(GUIDE_STATUS_LINE) != 1:
        failures.append("calibration guide status line is not the frozen line")
    bound = {"corpus_sha256": CORPUS, "guide_sha256": GUIDE, "verifier_sha256": Path(__file__).resolve(),
             "operational_guide_sha256": og.GUIDE, "locked_protocol_sha256": o5.PROTOCOL}
    failures += [f"freeze manifest binding: {k}" for k, path in bound.items() if freeze.get(k) != file_sha(path)]
    if (freeze.get("status") != FREEZE_STATUS or freeze.get("blocker") != BLOCKER
            or freeze.get("blocker_status") != FROZEN or freeze.get("blocker_status_before") != "OPEN"):
        failures.append(f"freeze manifest must record {BLOCKER} {FROZEN} (was OPEN) with status {FREEZE_STATUS}")
    if (freeze.get("pre_run_status") != PRE_RUN or freeze.get("pilot_readiness") is not False
            or freeze.get("pilot_readiness_blockers") != PILOT_BLOCKERS):
        failures.append(f"freeze manifest must keep {PRE_RUN}, pilot readiness false, blockers {PILOT_BLOCKERS}")
    if (freeze.get("calibration_responses_collected") is not False
            or freeze.get("model_generated_labels_used_as_authority") is not False
            or any(v not in (False, 0) for v in freeze.get("execution_record", {"missing": True}).values())):
        failures.append("freeze manifest must record no responses, no model labels as authority, and no execution")
    for key in ("label_validation", "coverage", "contamination_audit"):
        if freeze.get(key) != results[key]:
            failures.append(f"freeze manifest {key} does not match this run")
    report(len(failures) == n, f"freeze: {FREEZE_STATUS}; {BLOCKER} {freeze.get('blocker_status')}; {len(bound)} "
                               f"hashes bound; recorded results match; O-3 {freeze.get('pre_run_status', {}).get('O-3')}; "
                               f"pilot readiness {freeze.get('pilot_readiness')}")
    return failures, results


def main() -> int:
    failures, _ = run_checks()
    print(f"\nRESULT {'FAIL' if failures else 'PASS'}: {len(failures)} failure(s). Offline document verification; "
          f"no model, credential, or network used.")
    for x in failures:
        print(f"  - {x}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
