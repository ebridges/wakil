"""One-off diagnostic: is eval flakiness coming from generation variance or
grading variance? For each sample scenario, generate ONE transcript, then
grade that SAME frozen transcript N times to see how much the verdict
moves on identical input. Separately, generate N fresh transcripts and
grade each once, to see how much the skill's own behavior varies.

Not part of the test suite -- a throwaway script for this investigation.
Run with: uv run python scripts/diagnose_eval_noise.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from evals.runner import grade_transcript, materialize_workspace, run_scenario
from wakil.llm.client import resolve_client
from wakil.llm.skill_loader import load_skill
from wakil.skills.evals import load_eval_file

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_ROOT = REPO_ROOT / "src" / "wakil" / "skills"

SAMPLE = [
    ("entity-resolution", "confident-match-by-alias"),
    ("knowledge-research", "jane-doe-lookup-is-knowledge-query-territory"),
    ("skill-authoring", "override-a-builtin-for-one-kb"),
    ("source-ingestion", "youtube-transcript-unavailable"),
]

REPEATS = 4


def _load(skill_name: str, scenario_id: str):
    eval_file = load_eval_file(BUILTIN_ROOT / skill_name)
    scenario = next(s for s in eval_file.scenarios if s.id == scenario_id)
    return scenario


def main() -> None:
    client = resolve_client()
    if client is None:
        print("No model provider configured.")
        return

    for skill_name, scenario_id in SAMPLE:
        scenario = _load(skill_name, scenario_id)
        print(f"\n{'=' * 80}\n{skill_name}/{scenario_id}\n{'=' * 80}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = materialize_workspace(tmp_path, scenario)
            skill = load_skill(skill_name, workspace)

            # --- Fixed generation, repeated grading ---
            transcript = run_scenario(client, skill, scenario, workspace)
            print(f"\n-- Fixed transcript, graded {REPEATS}x --")
            grading_verdicts = []
            for i in range(REPEATS):
                result = grade_transcript(client, scenario, transcript)
                failed = [it.item for it in result.items if not it.passed]
                grading_verdicts.append(tuple(sorted(failed)))
                print(f"  run {i + 1}: {len(failed)}/{len(result.items)} failed -> {failed}")
            stable_grading = len(set(grading_verdicts)) == 1
            print(f"  grading stable on identical input: {stable_grading}")

            # --- Repeated generation, one grade each ---
            print(f"\n-- Fresh transcript x{REPEATS}, graded once each --")
            gen_verdicts = []
            for i in range(REPEATS):
                fresh_transcript = run_scenario(client, skill, scenario, workspace)
                result = grade_transcript(client, scenario, fresh_transcript)
                failed = [it.item for it in result.items if not it.passed]
                gen_verdicts.append(tuple(sorted(failed)))
                print(f"  run {i + 1}: {len(failed)}/{len(result.items)} failed -> {failed}")
            stable_generation = len(set(gen_verdicts)) == 1
            print(f"  outcome stable across fresh generations: {stable_generation}")


if __name__ == "__main__":
    main()
