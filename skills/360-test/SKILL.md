---
name: 360-test
description: Comprehensive 4-phase testing workflow (lint, unit, e2e, manual) with automatic failure delegation to specialist agents and iterative re-runs until full green.
argument-hint: "<description of feature or changes to test>"
---

# 360-test

Orchestrate a full 360-degree testing cycle across unit tests, end-to-end tests, and manual verification. On failure, delegate to the appropriate specialist agent, then re-run all phases until 100% pass.

## Usage

```
/360-test <description of feature or changes to test>
```

**Examples:**

```
/360-test Added email classification node for inbound dispute responses
/360-test Refactored settlement calculation logic in the offers service
/360-test New Celery task for async document parsing
```

---

## Execution Steps

### Phase 0 -- Lint & Format (ruff)

Run linting with auto-fix and formatting before any tests:

```bash
uv run ruff check . --fix && uv run ruff format .
```

- If ruff fails with **unfixable errors**, stop and report the errors to the user. Do NOT proceed to testing until lint is clean.
- If ruff introduces changes, **do NOT commit them**. Just inform the user that ruff applied fixes. The user decides when to commit.

---

### Phase 1 -- Unit Tests

**Delegate to the `pytest-test-specialist` agent.**

Instruct the pytest-test-specialist with the following context and rules:

1. Identify source files related to the feature described in `$ARGUMENTS`.
2. Check if unit tests already exist using the **project directory placement rules** below.
3. **If tests do not exist, CREATE them** -- the specialist must write comprehensive unit tests covering happy paths, edge cases, and error scenarios.
4. Run the unit tests with **scoped coverage** (only measure the source files being tested):

```bash
uv run pytest <test_file_or_directory> -v --cov=<source_module_path> --cov-report=term-missing
```

For example, if testing `src/nodes/router.py`:
```bash
uv run pytest tests/test_router.py -v --cov=src/nodes/router --cov-report=term-missing
```

5. Verify all unit tests pass and review coverage gaps for the tested module.

#### Unit Test Directory Placement Rules

Place test files to mirror the source file's layer. There is **NO `tests/unit/` directory** -- do not create one.

| Source location | Test location |
|----------------|--------------|
| `src/nodes/<domain>/` | `tests/` root (e.g. `tests/test_<node_name>.py`) |
| `src/services/<domain>/` | `tests/services/<domain>/` |
| `src/database/repositories/` | `tests/database/repositories/` |
| `src/database/services/` | `tests/database/services/` |
| `src/agents/<agent>/` | `tests/agents/<agent>/` |
| `src/workflows/<domain>/` | `tests/workflows/` |
| `src/utils/` | `tests/utils/` |
| `src/llms/` | `tests/llms/` |
| `src/api/controllers/` | `tests/` root (e.g. `tests/test_<controller>.py`) |
| `src/observability/` | `tests/observability/` |

#### Unit Test Conventions (project-specific)

- **Async**: All async functions must use `@pytest.mark.asyncio` + `AsyncMock`. Async mode is auto-detected via `asyncio: mode=Mode.AUTO` in pytest.ini.
- **Mocking Langfuse prompts**: Root `tests/conftest.py` pre-injects `sys.modules` mocks for all `src.prompts.*` packages. Do NOT re-mock these -- they are handled globally.
- **Patching dependencies**: Always patch at the usage site: `patch("src.nodes.my_node.dependency")`, never at the dependency's own module.
- **Reuse conftest fixtures**: Check `tests/conftest.py` for shared fixtures (`mock_refund_repository`, `mock_email_service`, `mock_retail_state`, `mock_airtickets_state`, `mock_purchase_data`, `mock_purchase_items`, etc.) before creating new ones.
- **Subdirectory conftest.py**: If a test subdirectory needs additional heavy-dep mocking, add it to a local `conftest.py` (e.g. `tests/services/notifications/conftest.py`).
- **Class-based grouping**: Use `class TestFeatureName:` with descriptive docstrings.
- **Parametrize**: Use `@pytest.mark.parametrize` when testing the same logic with multiple inputs.
- **tiktoken gotcha**: When importing modules that trigger the transformers/langchain import chain, use `types.ModuleType` with `ModuleSpec` for stubbing, not plain `MagicMock`.

If any test fails or coverage is below 80%, proceed to the **Iteration Rules** below before moving to Phase 2.

---

### Phase 2 -- End-to-End Tests

**Delegate to the `pytest-test-specialist` agent.**

Instruct the pytest-test-specialist with the following context and rules:

1. Identify the workflows and API endpoints related to the feature described in `$ARGUMENTS`.
2. Check if e2e tests already exist under `tests/e2e/`.
3. **If tests do not exist, CREATE them** -- the specialist must write e2e tests that exercise the full request/response cycle.
4. Run the e2e tests:

```bash
uv run pytest tests/e2e/ -v
```

5. Verify all e2e tests pass.

#### E2E Test Conventions (project-specific)

- **E2E conftest**: `tests/e2e/conftest.py` overrides the root conftest -- it loads `.env`, points to the **real local DB** (`postgresql://postgres:example@localhost:5432/postgres`), removes Langfuse prompt mocks, and reloads service modules that need real prompts.
- **File naming**: E2E test files use the `test_*_e2e.py` suffix.
- **DB fixtures**: Seed data directly via asyncpg, clean up in FK-safe order after tests.
- **Mock LLM calls**: Use `monkeypatch.setattr` to swap agent/workflow functions to avoid actual LLM calls during e2e.
- **DB table names**: Use quoted PascalCase: `"Merchant"`, `"Purchase"`, `"RefundRequest"`, `"EmailMessage"`.
- **EmailDirection enum**: Cast via `$N::"EmailDirection"` in SQL.

If any test fails, proceed to the **Iteration Rules** below before moving to Phase 3.

---

### Phase 3 -- Manual Testing

Boot the backend locally and verify the feature works end-to-end against a running server.

#### Step 3.1 -- Start the development environment

```bash
./dev/start-dev.sh
```

Before running with `--all`, check which services are actually needed for the feature being tested. Only add `--all` if the feature spans multiple services or you are unsure of the dependency graph.

#### Step 3.2 -- Make endpoint calls

Use `curl` (or equivalent) to call the endpoints relevant to the changes described in `$ARGUMENTS` against `localhost:8000`.

- Test happy-path requests with valid payloads.
- Test error cases with invalid or missing data.
- Test edge cases relevant to the feature.

#### Step 3.3 -- Verify correctness

Check all three verification surfaces:

| Surface | How to check | What to look for |
|---------|-------------|-----------------|
| **API responses** | Inspect curl output | Correct status codes, response body matches expected contract, proper error messages |
| **Logs** | Read stdout/stderr from the running server | No unexpected errors or warnings, expected log flow for the operation |
| **Database state** | Query PostgreSQL directly | Data was created/updated/deleted correctly per business logic |

PostgreSQL connection:

```
postgresql://postgres:example@localhost:5432/postgres
```

Use `psql` or a query via bash to inspect tables affected by the feature.

If any manual verification fails, proceed to the **Iteration Rules** below.

---

## Iteration Rules

### Failure Delegation

On failure at any phase, delegate to the appropriate specialist agent based on the failure type:

| Failure Type | Delegate To | Examples |
|---|---|---|
| Backend logic, API errors, DB issues, service layer bugs | **python-backend-specialist** | Wrong query result, missing validation, incorrect status code, ORM errors |
| LLM integration, agentic workflow, tool calling, chain logic | **agentic-ai-architect** | Broken tool call, incorrect agent state transition, LangGraph node failure |
| Prompt quality, LLM output parsing, response formatting | **prompt-engineering-specialist** | Bad LLM output structure, hallucinated fields, prompt injection edge case |

When delegating, always provide:

1. The failing test name and file path.
2. Full error output / traceback.
3. Relevant source code context (file paths and key snippets).
4. Expected vs actual behavior.

### Re-run Policy

After any specialist makes changes:

1. **Re-run ALL phases from Phase 0** -- not just the failing test.
2. If new failures appear, delegate again using the table above.
3. Repeat until every phase is fully green.

### Test Modification Policy

If the tests themselves are modified (by any specialist, including the pytest-test-specialist):

1. Re-run the entire 360 cycle from Phase 1.
2. Verify that test modifications do not mask real bugs -- the test should be stricter or more correct, never weaker.
3. Confirm all previously passing tests still pass.
4. Continue iterating until full green.

---

## Success Criteria

ALL of the following must be true before reporting success:

- All unit tests pass with good coverage on the tested source modules.
- All e2e tests pass.
- API responses match expected contracts and status codes.
- Database state reflects correct business logic after manual test calls.
- No errors or unexpected warnings in server logs.
- Zero failures after the final iteration.

---

## Error Handling Reference

| Situation | Action |
|-----------|--------|
| No `$ARGUMENTS` provided | Warn the user that a feature description helps focus testing, then proceed with broad test discovery |
| No existing tests for the feature | Have the pytest-test-specialist create tests in the correct directory per placement rules |
| E2E test directory `tests/e2e/` missing | Create it and have the pytest-test-specialist generate tests from scratch |
| `./dev/start-dev.sh` fails to boot | Report the error, check Docker/service dependencies, retry once, then abort Phase 3 with details |
| Database unreachable | Report connection error and skip DB verification with a warning |
| Ruff reports unfixable errors | Stop and report lint errors before running any tests |
| Specialist agent fails to fix the issue after 3 iterations | Stop and report the unresolved failure to the user with full context for manual intervention |
