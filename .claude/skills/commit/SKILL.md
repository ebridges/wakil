---
name: commit
description: Commit pending changes split into logical change sets, each with a Conventional Commits message prefixed by a relevant emoji. Use when the user asks to commit, group/stage changes into logical commits, or write conventional-commit messages with emoji (gitmoji-style).
---

# commit

Group the working tree's pending changes into one or more **logical change sets** and
create a commit for each, with a Conventional Commits message prefixed by a relevant
emoji.

Having a coherent history of logical changes implemented in sequence is a core value
of this system. As a result, you must analyze the working tree's pending changes to
group them appropriately. **Failing to do this analysis and decomposition of the changes
is a violation of a core value of the system**.

## When to use

The user asks to "commit", "commit these changes", "split into logical commits",
"commit in change sets", or similar. Default to this behavior whenever committing.

## Procedure

1. **Survey the changes.** Run these in parallel:
   - `git status --porcelain=v1` — see staged, unstaged, and untracked files.
   - `git diff` and `git diff --staged` — understand _what_ changed, not just which files.
   - `git log --oneline -10` — match the repo's existing message style/scopes.
   - `git branch --show-current` — if on the default branch (`main`/`master`) and the
     user has not said to commit there, branch first or ask.

2. **Group into logical change sets.** Each commit should be one coherent, reviewable
   unit — a single concern that leaves the tree in a working state. Split by intent, not
   by file. Guidelines:
   - One feature, fix, refactor, or doc change per commit.
   - Keep production code and its tests together when they form one change.
   - Separate unrelated concerns even if they touch the same file (use `git add -p` to
     stage hunks selectively when needed).
   - Put pure formatting/lint noise in its own commit, apart from behavioral changes.
   - Order commits so each one builds/passes on its own where practical.
   - If everything is genuinely one concern, a single commit is correct — don't
     manufacture splits.

3. **Check development-docs.** Apply the `development-docs` skill's judgment: does anything from this
   session belong in `docs/DEVELOPMENT.md` or `docs/TROUBLESHOOTING.md`? Default is no —
   only add an entry if it clearly clears that skill's bar. If so, fold the doc edit into
   one of the change sets below rather than treating it as a separate follow-up.

4. **Confirm the plan.** Briefly list the proposed commits (message + files) and let the
   user adjust before committing, unless they've said to just do it.

5. **Stage and commit each set.** For each change set: stage exactly its files/hunks
   (`git add <paths>` or `git add -p`), then commit. Verify with `git status` between
   commits that you're only including the intended files. Never `git add -A` blindly when
   splitting.

6. **Report.** Show the resulting `git log --oneline` for the new commits.

## Message format

```
<emoji> <type>(<optional scope>): <description>

<optional body>

<optional footer>
```

- **Conventional Commits** types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`,
  `test`, `build`, `ci`, `chore`, `revert`. Use a scope when it adds clarity, e.g.
  `feat(gates):`.
- **Description**: imperative mood, lowercase, no trailing period, ≤ ~72 chars on the
  subject line.
- **Breaking changes**: append `!` after the type/scope (`feat(api)!:`) and/or a
  `BREAKING CHANGE:` footer.
- **Body** (optional): explain _why_, wrapped at ~72 cols. Add it when the change isn't
  self-explanatory.
- Respect any repo conventions found in `git log` (e.g. an issue/ticket ID footer this
  repo uses — match it).

### Emoji per type (gitmoji-aligned)

| Type       | Emoji |                              |
| ---------- | ----- | ---------------------------- |
| `feat`     | ✨    | new feature                  |
| `fix`      | 🐛    | bug fix                      |
| `docs`     | 📝    | documentation                |
| `style`    | 🎨    | formatting / code style      |
| `refactor` | ♻️    | refactor, no behavior change |
| `perf`     | ⚡️    | performance                  |
| `test`     | ✅    | tests                        |
| `build`    | 📦    | build system / dependencies  |
| `ci`       | 👷    | CI config                    |
| `chore`    | 🔧    | tooling / config / misc      |
| `revert`   | ⏪️    | revert a change              |

Other useful ones: 🚀 deploy, 🔒️ security fix, ➕ add dependency, ➖ remove dependency,
🚚 move/rename files, 🔥 remove code/files, 🚧 work in progress.

### Examples

```
✨ feat(resolver): override blocked proposals to safest allowed route
🐛 fix(payout): cap reimbursement at the policy limit
📝 docs: add "how to run" section to README
♻️ refactor(gates): extract coverage check into pure helper
✅ test: cover exactly-$10k boundary fixture
🔧 chore: pin ruff and pyright in uv.lock
```

## Rules

- Do **not** push unless the user asks.
- Do **not** add `Co-Authored-By` or tool attribution unless the user/repo requires it.
- Commit only what the user intends — surface unexpected files (build artifacts,
  secrets, large blobs) instead of silently committing them.
- Keep each commit green: don't split such that an intermediate commit breaks the build
  when it's easy to avoid.
