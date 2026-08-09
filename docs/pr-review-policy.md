---
title: Handling PR-review feedback
status: living
audience: wakil design
---

# Handling PR-review feedback

## About

How to triage findings from the `pr-reviewer` agent
(`.github/workflows/pr-review.yml`). Written because the reviews are
thorough enough to be hard to act on: a single run can produce eight or nine
findings spanning "this deletes user data" and "this comment uses an em
dash", presented at similar length.

Applies to a human reading a review and to an agent working one. The default
is **not** to fix everything.

This is guidance for *responding* to a review. It is deliberately not
guidance for the reviewer: that agent should report everything it finds, and
its output format (a triage table proposing these same buckets) lives in
`.claude/agents/pr-reviewer.md`. Suppressing findings would lose information;
sorting them costs nothing.

## The three buckets

Judge each finding by one question: *what breaks if we ship without it?*

### 1. Fix in this PR

Not addressing it **directly** causes a regression, or the PR fails to
deliver what it set out to deliver. Typical shapes:

- Data loss, or silently wrong data written to the knowledge base.
- A user-facing statement that is false — an error naming the wrong branch,
  a README describing behaviour that doesn't exist, a message the code
  contradicts.
- The PR reintroduces, elsewhere, the defect it exists to fix.
- Documentation in the same diff that contradicts the code in the same diff.

### 2. File an issue

Real, but it only bites in limited situations, or fixing it needs a decision
rather than an edit. Typical shapes:

- An edge case with preconditions (a specific repo layout, a rare collision,
  a malformed override).
- A defect-class sibling: the same shape as the reported bug, in a path
  nobody has hit.
- A product or ADR question the diff exposes but doesn't create.
- Scope the PR never claimed.

File it with a link back to the review comment, so the reviewer's own
reproduction stays one click away. Don't paraphrase away the evidence.

### 3. Decline, and say so

Nits and very obscure edge cases. Commit-message wording, comment style,
naming preferences, hypotheticals with no reachable trigger. **Say in the PR
thread that you're declining and why** — an unanswered finding reads as an
oversight, and the next round will raise it again.

## Reading a review quickly

1. **Read the verdict line only.** No blocking findings usually means stop.
2. **For each blocking item, look for the reproduction.** This reviewer
   pastes real output (`LOCKS: ['index.lock']`, `RECOVERY rc: 128`). A
   finding with a paste is almost always real. A finding that is purely an
   argument is where it is most often wrong — those deserve the scrutiny,
   not the ones with evidence.
3. **Then ask bucket 1's question.** If stating the breakage takes three
   conditionals, it isn't bucket 1.

Downgrade on sight:

- "Worth at least a comment", "consider", "nice-to-have" — the reviewer is
  telling you it's optional. Believe it.
- Test-coverage observations: fold into whatever fix they attach to, never
  as a standalone change.
- Anything about commit messages or comment wording.

Escalate on sight:

- It quotes the PR's own docstring or docs contradicting the PR's own code.
- It says "reproduced" / "verified" and shows output.
- It names data loss, silent wrong data, or a user-facing lie.

## Why the bar exists: over-fixing generates defects

This is not only about reviewer time or API spend.

In PR #193, round 2 raised that a killed `git commit` leaves its signing
helper orphaned, and suggested "at least a comment acknowledging it" —
bucket 3 by its own wording. It was implemented as code instead:
`start_new_session=True` plus `os.killpg`. Round 3 found that
`subprocess.TimeoutExpired` carries no `.pid`, so the cleanup never ran, and
that `setsid()` drops the controlling terminal — breaking `pinentry-tty` and
ssh passphrase prompts on *every* checked git write. A PR whose purpose was
making interactive signing work had made terminal signing worse than the
status quo it replaced. Both changes were reverted in round 3 and the
original gap was recorded as a comment, which is what the review asked for.

One optional finding, implemented eagerly, produced two blocking findings
and a regression. Each unnecessary fix also grows the diff, which makes the
next review longer and more expensive.

## Practical notes

- A docs-only push retriggers a full review (~$2.50). Batch doc corrections
  into a code push where possible.
- Reviews bill per run regardless of concurrency; running four at once
  doesn't cost more, but on a thin credit balance all four die partway and
  you get nothing for the spend. A run reporting `"num_turns": 1` with
  `"total_cost_usd": 0` is a credential or credit failure, not a finding
  about the code — check `gh secret list` and the account balance before
  reading anything into it.
- Expect a round to find something. Across PRs #193/#195/#196, every round
  did — including rounds reviewing the previous round's fixes.
