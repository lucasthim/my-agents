---
name: verify-test-quality
description: Strengthen tests and then prove they actually catch bugs. Use this skill whenever the user asks to write or harden tests, raise or assess coverage, check whether tests are real or tautological, verify that a suite would catch a regression, run mutation sanity-checks, triage what is untested in a branch or file, review test quality (weak assertions, over-mocking, fixture leakage, brittleness), or get a confidence read on a test suite before merge — even when phrased as "are these tests any good", "do our tests actually test anything", "make sure this is covered", "harden the tests for X", or "would this break if I broke the code". It only orchestrates — delegating test authoring to `pytest-test-specialist` and source fixes to `python-backend-specialist` — and never writes application code itself.
argument-hint: "<directory, file, branch, or conversation context>"
---

# Verify Test Quality

A test-confidence pass that does two things in order: first **strengthen** the tests for a target, then **prove** they actually catch bugs. It delegates all writing — tests to `pytest-test-specialist`, source fixes to `python-backend-specialist` — and confines itself to orchestration, measurement, triage, and judgment. It never edits application code or test code by hand (the one exception is applying and immediately reverting throwaway mutations during the sanity-check phase; see `references/mutation-protocol.md`).

The guiding principle: **a coverage number is not confidence.** A line can be 100% covered by a test that asserts nothing. This skill chases *genuine gaps and tautologies*, never a percentage.

## Step 0 — Detect or Confirm Repo Specifics (do this first)

Nothing repo-specific is hardcoded in this skill's logic. Before touching anything, resolve the project's conventions and echo them back.

Resolve each value by priority: **explicit user value > auto-detected from config > sensible default**. Auto-detect by reading whatever exists: `pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`, `Makefile`, `noxfile.py`, CI workflow files, `conftest.py`, and the test directory layout.

| Item | What to resolve | Common detections | Fallback default |
|------|----------------|-------------------|------------------|
| Test runner / install | how tests are invoked | `uv run pytest`, `poetry run pytest`, `pytest`, `tox` | `pytest` |
| Lint / format | the lint+format command | `ruff check . --fix && ruff format .`, `black . && flake8` | `ruff check . --fix && ruff format .` |
| Fast suite | how to run the quick unit tests | a marker like `-m "not slow and not integration"`, or a `tests/unit` path | run everything not marked slow/integration |
| Integration markers | markers/paths for non-fast tests | `-m integration`, `-m e2e`, `tests/e2e/`, `tests/integration/` | scan markers + dir names |
| Coverage command | branch-coverage invocation, scoped to the target | prefer `--cov-branch --cov=<source_root> --cov-report=term-missing` | same, with detected source root |
| Test layout | where tests live & naming | `tests/`, `test_*.py`, mirror rules | `tests/`, `test_*.py` |
| Source root | the importable package(s) under test | `src/`, package name from `pyproject.toml` | top-level package dir |

Present the resolved table to the user and **confirm before any file-touching step**. If detection is ambiguous (e.g. two plausible source roots), ask rather than guess.

## Step 1 — Scope Intake

Accept any of four target shapes from `$ARGUMENTS`:

- **Directory** — harden the tests covering that directory.
- **File** — harden the tests covering that file.
- **Branch** — diff against the merge base and target the changed source.
- **Conversation context** — no explicit path. Infer the target from the current working diff (`git status`, `git diff`) and what has been discussed in this conversation. State your inferred target and confirm it before proceeding.

Establish the source-under-test set and the corresponding test set. If a branch, infer the base from upstream/main/dev/master candidates and use `git merge-base`. Echo back exactly which source files and which test files are in scope.

## Step 2 — Strengthen Tests (delegate)

**Delegate test authoring/strengthening to the `pytest-test-specialist` agent.** Brief it with:

1. The exact target (source files in scope) and the resolved conventions from Step 0 (runner, layout, markers, fixtures, where test files belong).
2. The non-negotiable test-quality rules — pass these verbatim:
   - **Assert behavior, not execution.** Assert on return values, emitted state, and *exact call arguments* — not merely that a function ran or a mock was "called".
   - **Mock only true boundaries** — DB, HTTP, message queue, filesystem, clock/time, and other process-external dependencies. **Never mock internal helpers, sibling functions, or the unit under test.** A test that mocks the thing it's testing is a tautology.
   - Cover happy path, edge cases (empty/null/boundary), and error scenarios.
   - Keep fixtures isolated — no shared mutable state bleeding across tests.
3. The current coverage misses and quality findings once they exist (Steps 4–5 loop back here).

If, while writing tests, the specialist finds a **source bug** or **untestable design** (hidden side effects, no seam to inject a fake, hard-coded clock, etc.), that is not a test problem. **Delegate the source fix to the `python-backend-specialist` agent**, with the failing expectation and the reason the code resists testing. Then loop back to test authoring.

The skill itself writes no code in this step.

## Step 3 — Run Checks

Run, in order, using the Step 0 commands:

1. **Lint + format.** If it reports unfixable errors, stop and report them before testing. If it auto-fixes, do **not** commit — just note it.
2. **Fast suite.** Must be green before proceeding.
3. **Integration / non-fast suite** — gated by the infra protocol below.
4. **Branch coverage**, scoped to the in-scope source, preferring `--cov-branch --cov-report=term-missing`.

### Infra Handling for Non-Fast Tests (important — not auto-start, not blanket skip)

Non-fast tests are **not** uniformly "needs Docker." Classify each by its *actual* external dependency before deciding.

**Classify** every non-fast test into one of: `db`, `cache/redis`, `network/http`, or `none-external` (marked integration/e2e but actually runs in-process against mocked boundaries — needs no real infra).

- **Primary signal:** explicit pytest markers.
- **Secondary signal (light heuristic only — do NOT build a static analyzer):** scan the test's fixtures and imports for tells — a real `db_pool` / `asyncpg.connect` / SQLAlchemy engine → `db`; a Redis client → `cache/redis`; live `httpx`/`requests` to a real URL → `network/http`. Mocked/patched boundaries → `none-external`.

Then:

1. **Run the `none-external` tests immediately** — no starter needed.
2. **For genuinely infra-bound tests, present the classification AND the reasoning to the user** before running anything — e.g. *"`test_save_event` looks **db**-bound because it uses the `db_pool` fixture and opens `asyncpg.connect`."* This detection is heuristic and **will sometimes be wrong**, so the user must be able to correct the buckets.
3. Ask whether to run the infra-bound tests. If yes, **the user points to the environment starter** — a docker-compose file, a Dockerfile, a `.sh` script, or a raw terminal command. **Never hardcode or assume a starter.** Run it only with confirmation.
4. **After starting infra, verify reachability** of each required dependency *before* running the tests (e.g. probe the DB port / Redis ping / endpoint health). 
5. **Run the infra-bound tests**, then **report any test that still couldn't connect** — do not assume one starter covered every dependency (a compose file may bring up Postgres but not Redis). Name the specific tests and the missing dependency.
6. If the user **declines or can't supply a starter**, **skip those tests with a clear note** listing exactly which tests were skipped and what each one needs.

## Step 4 — Coverage Triage (buckets, never a number)

Read `references/coverage-triage.md`. Take the branch-coverage misses and sort every missed line/branch into exactly one of **five buckets**:

1. **Genuine gap** — real behavior that no test exercises. → loop back to **Step 2** and have the specialist cover it.
2. **Defensive / unreachable** — guards that can't trigger given real inputs, or dead code. → note; consider flagging the dead code to `python-backend-specialist`, don't force a test.
3. **Boundary-owned** — only runs against real DB/HTTP/etc.; belongs to integration, not unit. → route to the integration suite, not a mock.
4. **Trivial / no-logic** — pure pass-throughs, simple property accessors, `__repr__`. → low value; skip unless cheap.
5. **Should-not-exist** — covered only because of over-broad code or a tautological test. → flag for removal/refactor, not for more tests.

Only bucket 1 drives new tests. **Never write a test purely to move the percentage.** Report the bucket breakdown.

## Step 5 — Test-Quality Review

Read `references/test-quality-checklist.md` and review the in-scope tests (existing + newly written) against it. Hunt for:

- **Tautologies / over-mocking** — the test mocks the unit under test, or asserts only that a mock was called.
- **Weak assertions** — asserts truthiness, type, or "no exception" where it should assert exact values / call args / emitted state.
- **Fixture isolation failures** — shared mutable state, order-dependence, leaked globals.
- **Brittleness** — asserting on incidental detail (log strings, dict ordering, timestamps) that breaks on benign change.
- **Edge gaps** — missing empty/null/boundary/error cases for behavior that is otherwise covered.

Each finding gets a severity label (see Output). Fixes go back through `pytest-test-specialist` (test issues) or `python-backend-specialist` (design issues), then re-verify.

## Step 6 — Mutation Sanity-Checks

Read `references/mutation-protocol.md` in full and follow it exactly. This is **manual** mutation testing (not a tool), with a bulletproof source-restore protocol mechanized by `scripts/restore_guard.sh`.

Pick the **3–5 riskiest lines** in the in-scope source (highest branching, money/state/security-relevant, recently changed) — use judgment, no rigid cap. For **each** mutation, strictly one at a time:

1. **Pre-flight:** assert the target file is git-clean via `restore_guard.sh <path>`. Never mutate uncommitted source.
2. Record the pristine reference (git blob / stash, or a copy stored outside the tree).
3. Apply **one** semantically meaningful mutation via Edit — drop a forwarded argument, return a constant, flip a boundary (`<` ↔ `<=`), or skip a cleanup/commit call.
4. Run the **narrowest relevant test(s)**. Expect **RED**. A mutant that survives (stays GREEN) is a **tautology finding** — the tests don't actually pin that behavior.
5. **Restore** (`git checkout -- <path>` or copy-back), then **prove pristine** with `restore_guard.sh <path>`. **Hard-stop if not clean.**
6. Repeat for the next mutation.

**Final gate:** whole-tree `git status` clean and full in-scope suite green. The skill must never end with a dirty tree.

## Step 7 — Re-verify and Report

Re-verify the end state:

- Fast suite (and any run integration tests) green.
- Lint clean.
- **Tree pristine** — `git status` shows no stray mutation residue.

Then produce a severity-labeled report (see Output Format).

## Single-Agent Fallback

If specialist sub-agents are unavailable (smaller harness, or the user wants a quick pass), do the work sequentially in this order, still writing no app code unless the user explicitly authorizes it: detect/confirm conventions → scope intake → run lint+fast+ (gated) integration + branch coverage → coverage triage into the 5 buckets → test-quality review → mutation sanity-checks → re-verify + report. When new tests or source fixes are needed and no specialist exists, present the specific change to the user for authorization rather than silently editing.

## Output Format

Lead with findings. Severity labels:

- **P0** — a tested-but-broken guarantee: a mutation survived on money/state/security logic, or a test actively masks a bug.
- **P1** — genuine coverage gap on important behavior, or a tautological/over-mocked test giving false confidence.
- **P2** — weak assertions, brittleness, fixture-isolation risk, missing edge cases.
- **P3** — minor cleanup; trivial gaps not worth chasing.

For each finding: severity + title, **Evidence** (file:line / test name), **Problem** (why it gives false or missing confidence), **Recommended action** (which agent, what change). 

Close with:

- **Scope** — files/branch/inferred context under test.
- **Resolved conventions** — the Step 0 table actually used.
- **Coverage triage** — the 5-bucket breakdown (counts + notable lines), explicitly *not* a pass/fail percentage.
- **Mutation results** — each mutation, the test(s) run, killed vs survived.
- **Infra** — what was classified, what was run, what was skipped and why, anything that couldn't connect.
- **Tree state** — confirmation `git status` is clean.

If there are no findings, say so plainly and list residual risks and anything not exercised (e.g. infra-bound tests skipped).

## Optional / Escalation (off by default)

Document and offer these only when warranted; do not enable them by default. Details in the reference files.

- **`pytest-randomly`** — when test-order or fixture-isolation contamination is suspected (Step 5 turns up order-dependence).
- **`mutmut` full sweep** — an automated mutation run on **one hot file** after manual sanity-checks already found something, when deeper assurance is wanted.
- **`hypothesis`** — property-based testing for pure functions with rich input domains.

## Error Handling Reference

| Situation | Action |
|-----------|--------|
| No `$ARGUMENTS` | Treat as **conversation context**: infer target from the working diff + discussion, state it, and confirm before proceeding. |
| Repo specifics ambiguous | Ask; never guess the runner, source root, or markers. |
| Lint reports unfixable errors | Stop and report before running any tests. |
| Fast suite red before strengthening | Report failures first; delegate source fixes to `python-backend-specialist` before adding tests. |
| Infra-bound tests detected | Present classification + reasoning, ask to run, request a starter; never auto-start, never blanket-skip. |
| Infra starter fails or dependency unreachable | Report which dependency is unreachable; skip the affected tests with a clear note. |
| `restore_guard.sh` reports the tree is dirty | **Hard-stop the mutation phase.** Do not apply or leave any mutation. Restore manually and re-prove clean before continuing. |
| A mutation survives (stays green) | Record as a tautology finding (P0/P1 by risk); restore, prove clean, continue. |
| Specialist can't resolve an issue after a few iterations | Stop and hand off to the user with full context. |
