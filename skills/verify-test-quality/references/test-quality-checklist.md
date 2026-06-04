# Test-Quality Checklist

A test that runs without verifying anything is worse than no test: it produces a green check and false confidence. Review every in-scope test (existing and newly authored) against the categories below. Each finding gets a severity label and is routed to the right agent — test issues to `pytest-test-specialist`, design/seam issues to `python-backend-specialist`.

---

## 1. Tautologies and over-mocking

The single most common way a suite lies.

**Smells:**
- The test mocks the very unit under test (or patches the function it claims to verify).
- The only assertion is `mock.assert_called()` / `assert mock.called` with no check on arguments or result.
- Internal helpers, sibling functions, or private methods are mocked, so the test only exercises the mock's return value, not real logic.
- A patched dependency returns a canned value that the assertion then trivially re-checks (asserting the mock's own configuration back to itself).

**Rule — mock only true boundaries:** DB, HTTP/network, message queue, filesystem, clock/time, and other process-external dependencies. Everything inside the unit's own module graph should run for real. If you must mock an internal collaborator to make a test pass, that usually signals a missing seam — route to `python-backend-specialist`.

**Good shape:** real logic runs; boundaries are faked; the assertion checks the *return value*, the *emitted state*, and the *exact arguments* passed to the boundary.

---

## 2. Weak assertions

The line is covered, but the guarantee is thin.

**Smells:**
- `assert result` (truthiness) where the exact value matters.
- `assert isinstance(result, dict)` where the keys/values matter.
- "It didn't raise" as the entire test body.
- Asserting a collection's length but not its contents.
- Asserting that *something* was persisted but not *what*.

**Rule — assert behavior, not execution.** Pin exact return values, the precise state delta, and **exact call arguments** to mocked boundaries (`mock.assert_called_once_with(...)`, or inspect `call_args`). For dict/state outputs, assert the specific keys and values the behavior is contracted to produce.

---

## 3. Fixture isolation

Tests must not depend on each other or on order.

**Smells:**
- Module- or class-level mutable state mutated inside tests.
- A fixture that yields a shared object reused across tests without reset.
- Tests that pass together but fail in isolation (or vice versa).
- Global singletons / module caches not reset between tests.
- Reliance on a previous test having seeded data.

**Rule:** each test sets up its own state and tears it down. Prefer function-scoped fixtures for mutable state; reserve session/module scope for genuinely immutable or expensive read-only setup. If order-dependence is suspected, escalate to `pytest-randomly` (optional, off by default).

---

## 4. Brittleness

A test that breaks on benign change is noise that trains people to ignore failures.

**Smells:**
- Asserting on log message strings or full log output.
- Depending on dict/set iteration order.
- Hard-coded timestamps, UUIDs, or `datetime.now()` without freezing the clock.
- Asserting on incidental formatting (whitespace, repr details) rather than semantic content.
- Over-specified mocks that assert on call ordering that doesn't matter.

**Rule:** assert on stable, semantic outcomes. Freeze time at the boundary (inject a clock / patch `now`). Compare sets when order is irrelevant. Don't pin details the contract doesn't promise.

---

## 5. Edge and error gaps

Happy path covered, but the risky inputs aren't.

**Checklist per unit:**
- Empty inputs: `[]`, `""`, `{}`, `None` where accepted.
- Boundary values: 0, 1, max, off-by-one around every `<` / `<=` / range.
- Error scenarios: the dependency raises — is it retried, swallowed, re-raised, logged? Assert the chosen behavior.
- Duplicate / idempotency: calling twice — second call's behavior.
- Concurrency / ordering, where the unit makes guarantees.

A behavior with a happy-path test but no error-path test is a **Bucket 1** coverage gap even if the line shows as "covered".

---

## Severity mapping (for the report)

- **P0** — a test that *masks* a real bug (asserts the wrong thing as correct), or false confidence on money/state/security logic.
- **P1** — tautological / over-mocked test, or a genuine gap on important behavior.
- **P2** — weak assertions, brittleness, fixture-isolation risk, missing edge cases.
- **P3** — minor cleanup; trivial gaps not worth chasing.

For each finding give: severity + title, file:line / test name, the false-or-missing confidence it creates, and the recommended fix (which agent, what change).

---

## Optional escalation (off by default)

- **`pytest-randomly`** — run the suite under randomized order to expose hidden inter-test coupling found in category 3.
- **`hypothesis`** — replace hand-picked edge cases (category 5) with property-based generation for pure functions.
