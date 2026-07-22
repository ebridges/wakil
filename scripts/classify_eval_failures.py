"""Classify the eval suite's remaining failures into deterministic content
gaps, generation variance, or grading variance -- the classification table
the council's debugging triad (Feynman/Socrates/Ada) agreed must exist
before touching any more SKILL.md files.

For each target scenario: generate 5 fresh transcripts (each graded once)
to measure generation-pass-rate, then freeze one transcript and grade it
5 more times to measure frozen-regrade-pass-rate. Bucket:
  (a) deterministic  -- fails 5/5 generation AND 5/5 frozen-regrade
  (b) generation      -- generation-pass-rate is neither 0/5 nor 5/5
  (c) grading         -- generation is stable but frozen-regrade isn't

Not part of the test suite -- a throwaway investigation tool.
Run with: uv run python scripts/classify_eval_failures.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from evals.runner import grade_transcript, materialize_workspace, run_scenario
from evals.test_skill_evals import BUILTIN_ROOT, _builtin_context
from wakil.llm.client import resolve_client
from wakil.llm.skill_loader import load_skill
from wakil.skills.evals import load_eval_file
from wakil.skills.resolver import discover_skill_names

REPEATS = 5

# The 10 scenarios failing on the latest full-suite run, as
# "<skill>-<scenario_id>" combined names (pytest's own -k id form).
TARGET_COMBINED_IDS = [
    "entity-enrichment-confident-match-plus-create-candidate",
    "entity-enrichment-unresolved-mention-defers-to-entity-resolution",
    "entity-resolution-handoff-boundary-linking-decision",
    "ingest-source-resume-declined-enrichment-by-source-id",
    "kb-commit-schema-invalid-note-handoff-to-conformance",
    "kb-commit-unrelated-edits-must-split-into-separate-commits",
    "knowledge-research-jane-doe-lookup-is-knowledge-query-territory",
    "knowledge-research-verify-acme-series-a-claim",
    "note-conformance-happy-path-mechanical-plus-manual-fixes",
    "note-routing-resolved-entity-defer-shaping-to-conformance",
]


def _resolve_targets() -> list[tuple[str, str]]:
    """Match TARGET_COMBINED_IDS against the real (skill, scenario_id)
    catalog rather than hand-splitting hyphenated strings -- skill names
    themselves contain hyphens, so string-splitting is fragile."""
    names = discover_skill_names(_builtin_context())
    catalog: list[tuple[str, str]] = []
    for skill_name in names:
        eval_path = BUILTIN_ROOT / skill_name / "eval.json"
        if not eval_path.is_file():
            continue
        eval_file = load_eval_file(BUILTIN_ROOT / skill_name)
        for scenario in eval_file.scenarios:
            catalog.append((skill_name, scenario.id))

    resolved = []
    for combined in TARGET_COMBINED_IDS:
        matches = [(s, sid) for s, sid in catalog if f"{s}-{sid}" == combined]
        if len(matches) != 1:
            print(f"WARNING: {combined!r} matched {len(matches)} catalog entries, skipping")
            continue
        resolved.append(matches[0])
    return resolved


def _pass_rate(client, scenario, transcripts: list[str], *, label: str) -> tuple[int, list[str]]:
    """Grade each transcript and print the full result immediately -- a
    mid-run crash (rate limit, API outage) must still leave every already-
    graded run's item/reason detail in the log, not just a pass/fail tally
    deferred to a final summary the run might never reach."""
    passed = 0
    failed_items: list[str] = []
    for i, transcript in enumerate(transcripts, start=1):
        result = grade_transcript(client, scenario, transcript)
        items_failed = [(it.item, it.reason) for it in result.items if not it.passed]
        if not items_failed:
            passed += 1
            print(f"    [{label} {i}/{len(transcripts)}] PASS")
        else:
            failed_items.extend(item for item, _reason in items_failed)
            print(f"    [{label} {i}/{len(transcripts)}] FAIL:")
            for item, reason in items_failed:
                print(f"        - {item}")
                print(f"          reason: {reason}")
    return passed, failed_items


def main() -> None:
    client = resolve_client()
    if client is None:
        print("No model provider configured.")
        return

    targets = _resolve_targets()
    print(f"Classifying {len(targets)} scenarios, {REPEATS}x each axis.\n")

    rows = []
    for skill_name, scenario_id in targets:
        eval_file = load_eval_file(BUILTIN_ROOT / skill_name)
        scenario = next(s for s in eval_file.scenarios if s.id == scenario_id)
        print(f"=== {skill_name}/{scenario_id} ===")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = materialize_workspace(tmp_path, scenario)
            skill = load_skill(skill_name, workspace)

            print("  generating 5 fresh transcripts...")
            fresh_transcripts = [
                run_scenario(client, skill, scenario, workspace) for _ in range(REPEATS)
            ]
            print("  grading each fresh transcript once (generation-pass-rate):")
            gen_passed, gen_failed_items = _pass_rate(
                client, scenario, fresh_transcripts, label="gen"
            )
            print(f"  generation-pass-rate: {gen_passed}/{REPEATS}")

            frozen_transcript = fresh_transcripts[0]
            print("  regrading one frozen transcript 5x (frozen-regrade-pass-rate):")
            regrade_passed, regrade_failed_items = _pass_rate(
                client, scenario, [frozen_transcript] * REPEATS, label="regrade"
            )
            print(f"  frozen-regrade-pass-rate: {regrade_passed}/{REPEATS}")

            if gen_passed == 0 and regrade_passed == 0:
                bucket = "(a) deterministic"
            elif 0 < gen_passed < REPEATS:
                bucket = "(b) generation variance"
            elif 0 < regrade_passed < REPEATS:
                bucket = "(c) grading variance"
            elif gen_passed == REPEATS:
                bucket = "PASSING NOW (flaky-clean run)"
            else:
                bucket = "(a) deterministic, grading stable on failure"
            print(f"  bucket: {bucket}\n")

            rows.append(
                {
                    "scenario": f"{skill_name}/{scenario_id}",
                    "gen_pass_rate": f"{gen_passed}/{REPEATS}",
                    "regrade_pass_rate": f"{regrade_passed}/{REPEATS}",
                    "bucket": bucket,
                    "gen_failed_items": sorted(set(gen_failed_items)),
                    "regrade_failed_items": sorted(set(regrade_failed_items)),
                }
            )

    print("\n" + "=" * 100)
    print("CLASSIFICATION TABLE (summary -- see per-scenario output above for reasons)")
    print("=" * 100)
    for row in rows:
        print(f"\n{row['scenario']}")
        print(f"  generation-pass-rate:     {row['gen_pass_rate']}")
        print(f"  frozen-regrade-pass-rate: {row['regrade_pass_rate']}")
        print(f"  bucket:                   {row['bucket']}")
        if row["gen_failed_items"]:
            print(f"  failed items (generation): {row['gen_failed_items']}")
        if row["regrade_failed_items"]:
            print(f"  failed items (regrade):    {row['regrade_failed_items']}")


if __name__ == "__main__":
    main()
