# Changelog

All notable changes to this project are documented in this file.

## [0.4.0] - 2026-08-15

### ✨ Features

- ✨ feat(transcript): capture every class of material, not just decisions (#239)

## [0.3.0] - 2026-08-10

### ✨ Features

- ✨ feat(schema): allow "contact" as a person relationship value (#169)
- ✨ feat(git): advisory lock around the checkout→commit→return sequence (#196)
- ✨ feat(sources): follow renamed captures, and archive dead ones (#201)

### 🐛 Bug Fixes

- 🐛 fix(git): give `git commit` a timeout that fits interactive signing (#193)
- 🐛 fix(git): make landing observe reality instead of asserting it (#195)
- 🐛 fix(ingest): honor an input file's own frontmatter and H1 (#194)
- 🐛 fix(ingest): derive user-visible dates from the local timezone (#197)
- 🐛 fix(ingest): refuse a destination collision instead of silently suffixing (#198)
- 🐛 fix(enrich): auto-correct proposal routing, dedupe pages by identity (#199)
- 🐛 fix(enrich): fail loudly when every update target is off this branch (#200)
- 🐛 fix(enrich): stop truncating sources silently; tighten synthesis rules (#202)

### 📝 Documentation

- 📝 docs(readme): mention the automated pr-reviewer CI comment (#185)
- 📝 docs(adr): add 0000-template.md for new ADRs (#191)
- 📝 docs(process): add a triage policy for pr-reviewer feedback (#222)
- 📝 docs(changelog): v0.3.0

## [0.2.1] - 2026-07-30

### 📝 Documentation

- 📝 docs(readme): rewrite with upfront use cases, full command reference, mcp-coordinator walkthrough
- 📝 docs(claude): add repo map, dev workflow, and mandatory dev-docs guidance
- 📝 docs: preserve KB directory/routing conventions from PROMPT.md
- 📝 docs(readme): link and explain the two page-shape templates
- 📝 docs(changelog): v0.2.1

## [0.2.0] - 2026-07-30

### ♻️ Refactoring

- ♻️ refactor(ingest): split apply_enrichment into write/update/persist helpers (#109)
- ♻️ refactor(ingest): extract duplicate/stub-build branches from _build_stub_entities (#110)
- ♻️ refactor(ingest): split validate_proposal into per-collection validators (#111)
- ♻️ refactor(cli): extract oversized-compiled-truth menu loop from entities_compile (#108)
- ♻️ refactor(graph): extract validate/resolve/build-sql/map helpers from traverse (#112)
- ♻️ refactor(mcp): split build_server into per-category registration helpers (#113)
- ♻️ refactor(cli): split enrich into prepare/confirm-and-apply helpers (#114)
- ♻️ refactor(ingest): split prepare_enrichment into related-notes/model-population helpers (#115)

### ✨ Features

- ✨ feat(cli): add --version flag alongside the version subcommand (#107)

### 🐛 Bug Fixes

- 🐛 fix(release): the publish job's gh release create had no repo context
- 🐛 fix(release): escape `@mentions` in generated changelog/release notes

### 📝 Documentation

- 📝 docs(troubleshooting): record the gh release create missing-repo-context gotcha
- 📝 docs(troubleshooting): record the `@mention-parsing` release-page gotcha
- 📝 docs(readme): add CI, release, and license status badges
- 📝 docs(development): note that stacked same-file refactor PRs conflict on merge
- 📝 docs(changelog): v0.2.0

## [0.1.0] - 2026-07-28

### ♻️ Refactoring

- ♻️ refactor(workspace): handle Optional scalar() return in count helper
- ♻️ refactor: drop unneeded __future__ annotations imports
- ♻️ refactor(ingest)!: split ingest into capture and enrichment steps
- ♻️ refactor(skills): flatten skills/builtin/ into skills/

### ✨ Features

- ✨ feat(phase-1): scaffold wakil CLI with init, status, and note indexing
- ✨ feat(storage): add FTS5 indexes and QMD search wrapper
- ✨ feat(query): add search and query commands with model client
- ✨ feat(web): add article fetching and readable-text extraction
- ✨ feat(ingest): add transcript, text, and article ingest with review
- ✨ feat(cli): add global -w/--workspace option with name registry
- ✨ feat(git): add write operations to git wrapper and thin gh integration
- ✨ feat(git): branch/commit/PR flags for ingest and git summary commands
- ✨ feat(memory): add memory lifecycle commands and retrieval fading
- ✨ feat(ingest): add --context option, transcript cleanup, and KB-aware linking
- ✨ feat(schema): add entity schema layer (Phase A of ingestion refactor)
- ✨ feat(storage): add Alembic and the entity-model columns (Phase B)
- ✨ feat(schema): add wakil schema migrate cheap tier (Phase D-cheap)
- ✨ feat(enrich): restructure enrichment into the fixed two-call DAG (Phase C)
- ✨ feat(skills): add precedence-ordered skill resolver
- ✨ feat(cli): add wakil skills list/which/validate
- ✨ feat(skills): add the 12-skill builtin catalog
- ✨ feat(skills): apply Claude Skill authoring best practices to the catalog
- ✨ feat(skills): add wakil skills lint for content-quality checks
- ✨ feat(skills): add eval schema and runner for skill-catalog evals
- ✨ feat(skills): add emoji prefix to kb-commit's manual commit messages
- ✨ feat(skills): wire the resolver into wakil enrich's DAG
- ✨ feat(schema): resolve entity schemas and page-shape templates kb-local/user/built-in
- ✨ feat(ingest): correct proposed-note wikilinks against entity-resolution's answer
- ✨ feat(llm): add entity-revision DAG node — deterministic merge into existing notes
- ✨ feat(ingest): find existing entity pages by name, not just by relevance
- ✨ feat(ingest): parse Apple .whisper transcript archives natively
- ✨ feat(ingest): make raw capture paths workspace-relative and citation-linkable
- ✨ feat(skills): add wakil skills describe
- ✨ feat(qmd): manage workspace-scoped QMD collections and auto-refresh after ingest
- ✨ feat(git): default-on branch/commit/PR landing per source
- ✨ feat(workspace): share workspace identity across git worktrees
- ✨ feat(skills): add development-docs maintenance skill + pre-commit reminder
- ✨ feat(context): expand `@file`:/`@url`: references in context text
- ✨ feat(cli): support repeatable --context/--context-file with reference expansion
- ✨ feat(agents): add pr-reviewer subagent
- ✨ feat(cli): add wakil sources list/show (#31)
- ✨ feat: add wakil schema validate command (#41)
- ✨ feat(llm): add confidence signal to EntityRevision
- ✨ feat(enrich): warn visibly when a source produces zero files
- ✨ feat: warn when a still-stub entity gets skipped again (issue #45)
- ✨ feat(llm): add create-path confidence signal to EntityResolution
- ✨ feat(ingest): synthesize real content on entity create, not a placeholder
- ✨ feat: thread frontmatter confidence through extraction's proposed_note

### 🐛 Bug Fixes

- 🐛 fix(ci): pin setup-uv to v8 so python-version is actually honored
- 🐛 fix(ci): pin setup-uv to the exact v8.3.2 tag
- 🐛 fix(tests): update test_skills_list for the now-populated builtin catalog
- 🐛 fix(fts): use FTS5's best-matching column for snippet highlighting
- 🐛 fix(tests): add required description to skill test fixtures
- 🐛 fix(ingest): close the content-hash dedup check-then-insert race
- 🐛 fix(llm): detect truncated model responses and retry with a bigger budget
- 🐛 fix(ingest): accept bare "## Timeline" heading when merging entity updates
- 🐛 fix(ingest): always stamp updated on entity-note merges, not just bump it
- 🐛 fix(ingest): stop wiping the Compiled Truth section when compiled_truth is empty
- 🐛 fix(git): move commit-message emoji into commit_message() itself (#28)
- 🐛 fix(ingest): recognize plain-JSON diarized transcripts (#30)
- 🐛 fix: surface directory-less entity-create skips as warnings
- 🐛 fix: stop directory-less create resolutions from aborting apply
- 🐛 fix(schema): allow kb-local schemas to suppress built-in entity types
- 🐛 fix: suppress duplicate entity-resolution stubs sharing a subject
- 🐛 fix(ingest): validate primary note path against schema directory/slug
- 🐛 fix(ingest): suppress redundant type=source self-mirror creates
- 🐛 fix: suppress dated journal/meeting duplicates already merged via update
- 🐛 fix(ingest): normalize new embed paths to vault-root-absolute on update merge
- 🐛 fix(ingest): suppress proposed_note when redundant with entity update
- 🐛 fix: dump updated: as unquoted date, matching created:
- 🐛 fix: propagate entity-resolution's type decision into proposed_note
- 🐛 fix(ingest): fall back to source's captured date for undated Timeline headings
- 🐛 fix(ingest): stop double-stripping the leading date on raw capture slugs
- 🐛 fix: reconcile required fields when correcting a proposed note's type
- 🐛 fix(ingest): recognize personal-reflection shape in text extraction skill (#94)
- 🐛 fix(ingest): stop reflection backstop's dominant-subject veto misfiring
- 🔥 fix(ingest): remove reflection-shape code backstop, keep skill guidance fix
- 🐛 fix(release): trim commit bodies out of generated changelog entries

### 📝 Documentation

- 📃 docs(spec): add specs for memory, ingestion skills, and entity model
- 📝 docs(spec): add entity-resolution.md, a critical read of RESOLVER.md
- 📝 docs(spec): add entity-metadata.md, a critical read of schema.md §4
- 📝 docs(spec): update ingestion-model.md, add ingestion-refactor-spec.md
- 🔗 docs(spec): reconcile refactor spec with the shipped capture/enrich split
- 📝 docs(spec): add skill refactor goals and resolution specification
- 📝 docs(spec): expand skill-refactor.md core skills catalog to 12 skills
- 📝 docs(skills): add page-shape and citation checks to note-conformance
- 📝 docs(skills): add default judgment patterns to note-routing
- 📝 docs: server-database migration follow-up
- 📝 docs(adr): backfill architecture decision records
- 📝 docs: backfill troubleshooting log from historical sessions
- 📝 docs: backfill development patterns log
- 📝 docs(adr): normalize source citation paths to be portable
- 📝 docs: propose relationship/graph-traversal queries (finishes ADR 0006)
- 📝 docs: note mentions edges' per-worktree last-indexed-wins property
- 📝 docs(adr): propose periodic entity compilation and gated timeline-context trimming
- 📝 docs(adr): narrow ADR 0016 through adversarial review
- 📝 docs: shared candidate content spans prompt/merge/stale-guard
- 📝 docs(note-conformance): document embed argument order
- 📝 docs(skills): preserve attachments and raw URLs during synthesis
- 📝 docs(schema): ground Open threads against source, not a default framing
- 📝 docs(skills): attachment/URL fidelity on the note-revision path
- 📝 docs(entity-resolve): guard against name-variant duplicate entities
- 📝 docs(entity-resolve): weigh category context and sibling precedent for two valid kb-local types
- 📝 docs(entity-resolve): defer to existing dated entity across proposals
- 📝 docs(entity-resolve): route category-scoped lists off the index dead end
- 📝 docs(entity-resolve): add voice/perspective signal for whose build it is
- 📝 docs(entity-resolve): resolve first-person reflections as journal despite references
- 📝 docs: note the str-vs-date yaml quoting gotcha from #78
- 📝 docs(ingest): reword issue #92 comment and _populate_type_frontmatter docstring
- 📝 docs(readme): document installing from a tagged release
- 📝 docs(troubleshooting): record the git-cliff commit.message full-body gotcha
- 📝 docs(troubleshooting): record the upload/download-artifact v6 Node-24 mismatch
- 📝 docs(changelog): v0.1.0
