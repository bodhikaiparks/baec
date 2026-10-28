"""Offline verifier for the Engine 3 Stage F human-reference operational guide (E3F-ISSUE-HR-001).

Checks, without any model call, credential read, or network access:

1. the locked protocol and the original issue record are unchanged since the O-5 freeze;
2. semantic equivalence, against the registered changes in
   docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_CHANGES.json: every annotation-rule line
   of the locked protocol (sections 2, 4 to 8, 10) is copied verbatim into the guide, changed only by a
   registered example substitution, or omitted with a registered reason; every guide line is a protocol
   line, a substitution result, or a registered addition;
3. contamination of the guide's worked examples and of every guide sentence, using the O-5 audit's
   evaluation corpus and comparison rule unchanged (exact, near, shared 5-token sequences, banned
   terms), plus shared distinctive 4-token sequences;
4. the packet guide (ANNOTATION_GUIDE) emitted in every blinded packet carries no evaluation-derived
   text, and carries exactly the HARNESS_AMENDMENT_002 example values;
5. the guide freeze: the frozen guide equals the reviewed candidate except its status line, and the
   freeze manifest's hashes and recorded results match;
6. the resolution record and the O-3 eligibility addendum: E3F-ISSUE-HR-001 may read resolved only if
   every resolution criterion holds in this run.

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
from experiments.engine3_stage_f.annotation import ANNOTATION_GUIDE  # noqa: E402

DOCS = REPO / "docs" / "engine3"
GUIDE = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md"
CHANGES_PATH = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_CHANGES.json"
REVIEW = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_DECONTAMINATION_REVIEW.md"
FREEZE_MANIFEST = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_FREEZE_MANIFEST.json"
RESOLUTION = DOCS / "ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_RESOLUTION_001.md"
ADDENDUM = DOCS / "ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM.md"
O3_CANDIDATE = DOCS / "ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_CANDIDATE.json"
HARNESS_MANIFEST = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"

GUIDE_STATUS = "FROZEN_OPERATIONAL_GUIDE"
FREEZE_STATUS = "OPERATIONAL_GUIDE_FROZEN"
FROZEN_STATUS_LINE = "**Status:** `FROZEN_OPERATIONAL_GUIDE` (owner-approved and frozen 2026-10-15)\\"
CANDIDATE_STATUS_LINE = "**Status:** `CANDIDATE_DECONTAMINATED_GUIDE` (not owner-approved; not frozen)\\"
ISSUE_OPEN = "PRE_HUMAN_ANNOTATION_BLOCKER"
ISSUE_RESOLVED = "RESOLVED_DECONTAMINATED_OPERATIONAL_PATH"
AMENDMENT_APPLIED = "APPLIED"
CALIBRATION_BLOCKER = "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED"
RECORD_HASH_FIELDS = ("locked_protocol_sha256", "operational_guide_sha256", "annotation_packet_sha256")

# Registered changes (substitutions, omissions, additions, allowlist, proposed packet text) live in a
# reviewable JSON record next to the guide; this script holds only the method.
CHANGES = json.loads(CHANGES_PATH.read_text(encoding="utf-8"))
COVERED_SECTIONS = tuple(CHANGES["covered_protocol_sections"])
# Protocol line -> (old fragment, new fragment) pairs. Applying them must give a guide line exactly.
SUBSTITUTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    e["protocol_line"]: tuple((old, new) for old, new in e["pairs"]) for e in CHANGES["example_substitutions"]}
# Protocol line -> guide line, for lines that describe example provenance (no annotation rule).
PROVENANCE_REPLACEMENTS = {e["protocol_line"]: e["guide_line"] for e in CHANGES["provenance_replacements"]}
# Protocol lines in covered sections that are study administration, not annotation rules.
OMITTED = {e["protocol_line"]: e["reason"] for e in CHANGES["omitted_protocol_lines"]}
# Guide lines that are not protocol lines: header, orientation, and the fictional account.
ADDITIONS_PREFIXES = tuple(CHANGES["guide_addition_prefixes"])
# The fictional account's own example text (taught through the X- items).
ACCOUNT_EXAMPLES = tuple(CHANGES["account_examples"])
# Guide sentences that overlap evaluation prose, with the reason the overlap is unavoidable.
ALLOWLIST = {e["guide_sentence"]: e["reason"] for e in CHANGES["allowlist"]}
# HARNESS_AMENDMENT_002 packet guide examples: path -> (old fragment, new fragment). Proposed in the reviewed
# change record (whose wording is kept as reviewed) and applied by the owner-approved amendment.
PROPOSED_AMENDMENT = CHANGES["proposed_harness_amendment_002"]
PROPOSED_PACKET_SUBSTITUTIONS = {
    tuple(e["path"]): (e["old"], e["new"]) for e in PROPOSED_AMENDMENT["packet_guide_substitutions"]}

# Distinctive n-grams: shared 4-token sequences with at least two tokens outside this function-word list.
NGRAM = 4
FUNCTION_WORDS = frozenset(
    "a an the of to in on at for and or but is are was were be been it its that this these those you your we our "
    "us i me my if as by with from any not no what which so has have had would could should will can do does did "
    "there their they them he she his her who whom how when where than then also just about into over".split())


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_content(line: str) -> bool:
    """A line carrying text: not blank, a heading, a table separator, or a rule."""
    s = line.strip()
    return bool(s) and not s.startswith("#") and not s.startswith("|---") and s != "---"


def content_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if is_content(line)]


def covered_protocol_lines(text: str) -> list[str]:
    out, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line
        if current and current.startswith(COVERED_SECTIONS) and is_content(line):
            out.append(line)
    return out


def apply_substitutions(line: str, pairs: tuple[tuple[str, str], ...]) -> str | None:
    for old, new in pairs:
        if line.count(old) != 1:
            return None
        line = line.replace(old, new)
    return line


def worked_examples() -> list[str]:
    """Every example fragment shown to annotators: substitution results and the account text."""
    return [new for pairs in SUBSTITUTIONS.values() for _, new in pairs] + list(ACCOUNT_EXAMPLES)


def distinctive_ngrams(text: str, corpus_ngrams: frozenset[tuple[str, ...]]) -> list[str]:
    toks = o5.tokens(text)
    found = set()
    for i in range(len(toks) - NGRAM + 1):
        gram = tuple(toks[i:i + NGRAM])
        if gram in corpus_ngrams and sum(t not in FUNCTION_WORDS for t in gram) >= 2:
            found.add(" ".join(gram))
    return sorted(found)


def corpus_ngram_set(corpus: list[tuple[str, str]]) -> frozenset[tuple[str, ...]]:
    grams = set()
    for _, s in corpus:
        toks = o5.tokens(s)
        grams |= {tuple(toks[i:i + NGRAM]) for i in range(len(toks) - NGRAM + 1)}
    return frozenset(grams)


def audit_text(text: str, corpus, grams) -> dict:
    """Exact, near, distinctive n-gram, and banned-term findings for one piece of text."""
    found = [f for s in o5.sentences(text) for f in o5.compare(s, corpus)]
    return {
        "exact": [f for f in found if f["kind"] == "EXACT"],
        "near": [f for f in found if f["kind"] == "NEAR"],
        "ngrams": distinctive_ngrams(text, grams),
        "banned": o5.banned_terms(text),
    }


def _strings(value: object, path: tuple = ()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, path + (i,))


def packet_guide_findings(guide: dict, corpus, grams) -> dict[tuple, dict]:
    """Instructional strings in the packet guide that carry evaluation-derived content."""
    out = {}
    for path, text in _strings(guide):
        result = audit_text(text, corpus, grams)
        if any(result.values()):
            out[path] = result
    return out


# --- main -----------------------------------------------------------------------------------------


def _status(text: str, item: str) -> str | None:
    row = re.search(rf"^\| `{re.escape(item)}` \| `([A-Z_]+)`", text, re.MULTILINE)
    return row.group(1) if row else None


def run_checks() -> tuple[list[str], dict]:
    """Run every check, print one line per check, and return (failures, results)."""
    failures: list[str] = []
    results: dict = {}

    def report(ok: bool, line: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {line}")

    protocol_text = o5.PROTOCOL.read_text(encoding="utf-8")
    guide_text = GUIDE.read_text(encoding="utf-8")
    o5_manifest = json.loads(o5.MANIFEST.read_text(encoding="utf-8"))

    # 1. locked records preserved
    n = len(failures)
    protocol_rel = o5.PROTOCOL.relative_to(REPO).as_posix()
    if file_sha(o5.PROTOCOL) != o5_manifest["locked_inputs_sha256"][protocol_rel]:
        failures.append("locked protocol changed since the O-5 freeze")
    if file_sha(o5.ISSUE) != o5_manifest["human_reference_issue_sha256"]:
        failures.append("original issue record changed since the O-5 freeze")
    results["protocol_preserved"] = len(failures) == n
    report(len(failures) == n, "locked protocol and original issue record unchanged since the O-5 freeze")

    # 2. semantic equivalence (both directions)
    n = len(failures)
    guide_lines = content_lines(guide_text)
    guide_set = set(guide_lines)
    protocol_lines = covered_protocol_lines(protocol_text)
    explained: set[str] = set()
    verbatim = substituted = 0
    for line in protocol_lines:
        if line in guide_set:
            verbatim += 1
            explained.add(line)
        elif line in SUBSTITUTIONS:
            result = apply_substitutions(line, SUBSTITUTIONS[line])
            if result is None or result not in guide_set:
                failures.append(f"substitution does not produce a guide line: {line[:80]}")
            else:
                substituted += 1
                explained.add(result)
        elif line in PROVENANCE_REPLACEMENTS:
            if PROVENANCE_REPLACEMENTS[line] not in guide_set:
                failures.append(f"provenance replacement missing from guide: {line[:80]}")
            explained.add(PROVENANCE_REPLACEMENTS[line])
        elif line not in OMITTED:
            failures.append(f"protocol rule line dropped or altered without registration: {line[:100]}")
    stale = [k for k in list(SUBSTITUTIONS) + list(PROVENANCE_REPLACEMENTS) + list(OMITTED) if k not in protocol_lines]
    failures += [f"registered protocol line not found in covered sections: {k[:80]}" for k in stale]
    unexplained = [g for g in guide_lines if g not in explained and not g.startswith(ADDITIONS_PREFIXES)]
    failures += [f"guide line is neither a protocol line nor a registered change: {g[:100]}" for g in unexplained]
    results["semantic_equivalence"] = {
        "protocol_rule_lines": len(protocol_lines), "verbatim": verbatim, "example_substitutions": substituted,
        "provenance_replacements": len(PROVENANCE_REPLACEMENTS), "omitted_administration": len(OMITTED),
        "unexplained_guide_lines": len(unexplained), "passed": len(failures) == n}
    report(len(failures) == n,
           f"semantic equivalence: {len(protocol_lines)} protocol rule lines = {verbatim} verbatim + {substituted} example "
           f"substitutions + {len(PROVENANCE_REPLACEMENTS)} provenance + {len(OMITTED)} omitted (administration); "
           f"{len(unexplained)} unexplained guide lines")
    for x in failures[n:]:
        print(f"      {x}")

    # 3. contamination of the guide
    corpus = o5.evaluation_corpus()
    grams = corpus_ngram_set(corpus)
    n = len(failures)
    totals = {"exact": 0, "near": 0, "ngrams": 0, "banned": 0}
    examples = worked_examples()
    for ex in examples:
        result = audit_text(ex, corpus, grams)
        for k in totals:
            totals[k] += len(result[k])
        if any(result.values()):
            failures.append(f"worked example contaminated: {ex} -> {result}")
    guide_banned = o5.banned_terms(guide_text)
    if guide_banned:
        failures.append(f"banned evaluation terms in guide: {guide_banned}")
    allowed, sentence_findings = 0, []
    for line in guide_lines:
        for s in o5.sentences(line):
            result = audit_text(s, corpus, grams)
            if result["exact"] or result["near"] or result["ngrams"]:
                if s in ALLOWLIST:
                    allowed += 1
                else:
                    sentence_findings.append((s, result))
    failures += [f"guide sentence overlaps evaluation prose: {s} -> {r}" for s, r in sentence_findings]
    results["contamination_audit"] = {
        "evaluation_sentences": len(corpus), "worked_examples": len(examples), "exact": totals["exact"],
        "near": totals["near"], f"distinctive_{NGRAM}_grams": totals["ngrams"], "banned_in_examples": totals["banned"],
        "banned_in_guide": len(guide_banned), "allowlisted_sentences": allowed,
        "unexplained_sentences": len(sentence_findings), "passed": len(failures) == n}
    report(len(failures) == n,
           f"contamination: {len(examples)} worked examples vs {len(corpus)} evaluation sentences: exact {totals['exact']}, "
           f"near {totals['near']}, distinctive {NGRAM}-grams {totals['ngrams']}, banned {totals['banned']}; "
           f"whole guide: banned {guide_banned or 'none'}, {allowed} allowlisted, {len(sentence_findings)} unexplained")
    for s, r in sentence_findings:
        print(f"      unexplained: {s} -> {r}")

    # 4. packet guide after HARNESS_AMENDMENT_002
    n = len(failures)
    exposed = packet_guide_findings(ANNOTATION_GUIDE, corpus, grams)
    failures += [f"packet guide exposes evaluation-derived text at {'.'.join(map(str, p))}: {r}" for p, r in exposed.items()]
    banned_packet = o5.banned_terms(json.dumps(ANNOTATION_GUIDE, ensure_ascii=False))
    if banned_packet:
        failures.append(f"banned evaluation terms in the packet guide: {banned_packet}")
    for (section, key), (old, new) in PROPOSED_PACKET_SUBSTITUTIONS.items():
        value = ANNOTATION_GUIDE[section][key]
        if value.count(new) != 1 or old in value:
            failures.append(f"packet guide {section}.{key} does not carry exactly the approved example {new!r}")
    results["packet_guide_clean"] = len(failures) == n
    report(len(failures) == n,
           f"packet guide: {len(exposed)} evaluation-derived strings, banned terms {banned_packet or 'none'}; "
           f"{len(PROPOSED_PACKET_SUBSTITUTIONS)} HARNESS_AMENDMENT_002 example values present")

    # 5. guide freeze
    n = len(failures)
    review = REVIEW.read_text(encoding="utf-8")
    freeze = json.loads(FREEZE_MANIFEST.read_text(encoding="utf-8")) if FREEZE_MANIFEST.exists() else {}
    if guide_text.count(FROZEN_STATUS_LINE) != 1:
        failures.append(f"guide status line is not the frozen line ({GUIDE_STATUS})")
    candidate = guide_text.replace(FROZEN_STATUS_LINE, CANDIDATE_STATUS_LINE)
    candidate_sha = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    if candidate_sha != freeze.get("candidate_guide_sha256") or f"`{candidate_sha}`" not in review:
        failures.append("frozen guide differs from the reviewed candidate beyond its status line")
    if freeze.get("status") != FREEZE_STATUS or freeze.get("guide_status") != GUIDE_STATUS:
        failures.append(f"freeze manifest status must be {FREEZE_STATUS} / {GUIDE_STATUS}")
    bound = {
        "locked_protocol_sha256": o5.PROTOCOL, "operational_guide_sha256": GUIDE, "change_record_sha256": CHANGES_PATH,
        "verifier_sha256": Path(__file__).resolve(), "candidate_review_sha256": REVIEW,
        "original_issue_record_sha256": o5.ISSUE,
    }
    failures += [f"freeze manifest binding: {k}" for k, path in bound.items() if freeze.get(k) != file_sha(path)]
    if f"`{file_sha(CHANGES_PATH)}`" not in review:
        failures.append("change record differs from the reviewed version")
    for key in ("semantic_equivalence", "contamination_audit"):
        if freeze.get(key) != results[key]:
            failures.append(f"freeze manifest {key} does not match this run")
    o3 = json.loads(O3_CANDIDATE.read_text(encoding="utf-8"))
    no_annotations = (o3["annotation_performed"] is False
                      and o5_manifest["execution_record"]["experiment_results_exist"] is False)
    if freeze.get("no_human_annotations_collected") is not True or not no_annotations:
        failures.append("records show human annotation performed or results existing")
    results["no_human_annotations_collected"] = no_annotations
    results["guide_frozen"] = len(failures) == n
    report(len(failures) == n,
           f"guide frozen ({GUIDE_STATUS}); equals reviewed candidate except status line; "
           f"{len(bound)} freeze-manifest hashes bound; recorded results match; no annotation recorded")

    # 6. resolution record and O-3 addendum
    n = len(failures)
    resolution = RESOLUTION.read_text(encoding="utf-8") if RESOLUTION.exists() else ""
    addendum = ADDENDUM.read_text(encoding="utf-8") if ADDENDUM.exists() else ""
    harness = json.loads(HARNESS_MANIFEST.read_text(encoding="utf-8"))
    applied = {a["id"]: a for a in harness.get("harness_amendments", [])}.get("HARNESS_AMENDMENT_002", {})
    directed = (GUIDE.name in addendum and "presumptively ineligible" in addendum
                and "- Use this guide." in guide_text)
    criteria = {
        "operational guide frozen": results["guide_frozen"],
        "semantic rules preserved": results["semantic_equivalence"]["passed"],
        "worked examples contamination-clean": results["contamination_audit"]["passed"],
        "packet instructions contamination-clean": results["packet_guide_clean"] and applied.get("applied") is True,
        "historical protocol preserved": results["protocol_preserved"],
        "future annotators directed to the operational guide": directed,
        "no human annotations collected": results["no_human_annotations_collected"],
    }
    issue_status = _status(resolution, "E3F-ISSUE-HR-001")
    if issue_status == ISSUE_RESOLVED and not all(criteria.values()):
        failures.append(f"resolution claims {ISSUE_RESOLVED} but criteria fail: "
                        f"{[k for k, ok in criteria.items() if not ok]}")
    if issue_status not in (ISSUE_RESOLVED, ISSUE_OPEN):
        failures.append(f"resolution record must state E3F-ISSUE-HR-001 as {ISSUE_RESOLVED} or {ISSUE_OPEN}")
    if f"`{o5_manifest['human_reference_issue_sha256']}`" not in resolution:
        failures.append("resolution record does not cite the original issue record SHA-256")
    if _status(resolution, "HARNESS_AMENDMENT_002") != AMENDMENT_APPLIED or applied.get("applied") is not True:
        failures.append("HARNESS_AMENDMENT_002 must be recorded APPLIED in the resolution record and harness manifest")
    if _status(resolution, CALIBRATION_BLOCKER) != "OPEN":
        failures.append(f"{CALIBRATION_BLOCKER} must remain OPEN: no calibration corpus has been audited")
    missing = [f for f in RECORD_HASH_FIELDS if f not in resolution or f not in addendum]
    failures += [f"annotation-record provenance field not required: {f}" for f in missing]
    report(len(failures) == n,
           f"resolution: E3F-ISSUE-HR-001 {issue_status} ({sum(criteria.values())}/{len(criteria)} criteria met); "
           f"HARNESS_AMENDMENT_002 {_status(resolution, 'HARNESS_AMENDMENT_002')}; "
           f"{CALIBRATION_BLOCKER} {_status(resolution, CALIBRATION_BLOCKER)}; O-3 addendum directs to the guide: {directed}")
    for name, ok in criteria.items():
        print(f"      {'met    ' if ok else 'NOT MET'} {name}")
    results["resolution_criteria"] = criteria
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
