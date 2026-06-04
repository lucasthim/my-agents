# Coverage Triage — The Five Buckets

Coverage tells you what *ran*, never what was *verified*. A `--cov-branch` report is an input to judgment, not a verdict. This skill **never chases a percentage** and **never emits a pass/fail %-gate**. It sorts misses into buckets and acts only on the ones that represent real, missing confidence.

Always run coverage with branch coverage on and scoped to the source under test, e.g.:

```
<runner> -m "<fast-marker>" --cov-branch --cov=<source_root> --cov-report=term-missing
```

`term-missing` lists the exact line and branch numbers that never executed. Take each missed line/branch and place it in **exactly one** bucket below.

---

## Bucket 1 — Genuine gap

Real behavior with real consequences that no test exercises.

- A conditional branch that changes output, persistence, or a side effect.
- An error path that should be handled a specific way (retry, raise, fallback).
- A code path reachable from real inputs that simply has no test.

**Action:** loop back to Step 2. Brief `pytest-test-specialist` to cover the specific behavior, asserting on the *result* of taking that branch — not merely that the line ran. This is the **only** bucket that should generate new tests.

---

## Bucket 2 — Defensive / unreachable

Guards that cannot trigger given the real type contract, or genuinely dead code.

- `if x is None:` where `x` is non-optional by construction and every caller passes a value.
- `else: raise AssertionError("unreachable")` sentinels.
- Code behind a feature flag that is compile-time off.

**Action:** do not force a test — a contrived test here is itself a tautology. If the line is truly dead, flag it to `python-backend-specialist` for removal. If it is a deliberate defensive guard, note it as intentionally uncovered.

---

## Bucket 3 — Boundary-owned

Code that only does meaningful work against a real external dependency (DB, HTTP, MQ, filesystem, clock). Covering it with a mock proves nothing about the real interaction.

- The actual SQL execution inside a repository method.
- The serialization handed to a real HTTP client.
- The commit/rollback against a live transaction.

**Action:** route to the **integration suite**, not to a mocked unit test. See the infra-handling protocol in SKILL.md — classify, confirm with the user, run with a user-supplied starter, verify reachability. Do not satisfy this bucket by mocking the boundary and asserting the mock was called.

---

## Bucket 4 — Trivial / no-logic

Lines with no branching and no behavior worth pinning.

- Pure pass-throughs / one-line delegations.
- Simple property getters, `__repr__`, `__str__`, dataclass boilerplate.
- Constants and module-level assignments.

**Action:** low value. Skip unless a test is essentially free and improves readability. Never write tests here just to move the number.

---

## Bucket 5 — Should-not-exist

The line is covered *only* because of a problem.

- Over-broad code: a branch that exists because the function does too much; the fix is to narrow the code, not to test the branch.
- A tautological test that exercises the line while asserting nothing meaningful — coverage looks satisfied but confidence is zero.

**Action:** flag for removal or refactor. Test issues → `pytest-test-specialist`. Design issues → `python-backend-specialist`. Do not "cover" it harder.

---

## Reporting the triage

Report the breakdown as counts plus notable lines per bucket, e.g.:

```
Coverage triage (scoped to <source_root>, branch coverage on):
  Bucket 1 — Genuine gap:        4  (routing.py:88 error branch; delivery_service.py:142 retry path; ...)
  Bucket 2 — Defensive:          2  (consumer_config.py:31 None-guard on non-optional)
  Bucket 3 — Boundary-owned:     6  (repository.py SQL execution — routed to integration)
  Bucket 4 — Trivial:            9  (property accessors, __repr__)
  Bucket 5 — Should-not-exist:   1  (test_routing asserts only mock.called — tautology, flagged)
```

This is descriptive triage, **not** a grade. Do not append "coverage: 82% — PASS". The signal is *which behaviors lack genuine verification*, which is what Bucket 1 and Bucket 5 capture.

---

## Optional escalation

- **`hypothesis`** (off by default) — when a Bucket 1 gap is a *pure function with a rich input domain* (parsers, encoders, math, normalizers), property-based testing covers the input space far better than hand-picked cases. Offer it; let the user opt in.
