---
name: stress-test
description: Run a full-pipeline stress test against the Autopilot staging environment to validate email processing, purchase saving, product enrichment, and system stability under concurrent load.
argument-hint: "[phase] [--cleanup-only] [--no-unique-only] [--no-cleanup-after]  # phase: 1a|1b|1c|1d|2a|2b|2c|2"
category: testing
---

# stress-test

Run a full-pipeline stress test against the Autopilot staging environment to validate email processing, purchase saving, product enrichment, and system stability under concurrent load.

## Usage

```
/stress-test              # Run full test (all phase-1 phases), auto-cleanup after
/stress-test 1a           # Run only phase 1a (50 emails)
/stress-test 1b           # Run only phase 1b (100 emails)
/stress-test 1c           # Run only phase 1c (250 emails)
/stress-test 1d           # Run only phase 1d (500 emails)
/stress-test 2            # Run all phase-2 burst phases (2a, 2b, 2c)
/stress-test 2a           # Run only phase 2a (500 simultaneous)
/stress-test 2b           # Run only phase 2b (1000 simultaneous)
/stress-test 2c           # Run only phase 2c (2000 simultaneous)
/stress-test --cleanup-only        # Just cleanup test data from staging DB (no test run)
/stress-test --no-unique-only      # Run with all 5 scenarios (shared UPC, duplicates, etc.)
/stress-test --no-cleanup-after    # Run test but keep data in staging DB for inspection
```

**Arguments:**
- `phase` (optional): Run a specific phase only. Values: `1a`, `1b`, `1c`, `1d`, `2a`, `2b`, `2c`, `2`. If omitted, runs all phase-1 phases sequentially. Use `2` as shorthand for all phase-2 burst phases.
- `--cleanup-only`: Skip the test and just delete all stress test data from the staging DB.
- `--unique-only` / `--no-unique-only` (optional): When active (default), overrides phase-1 scenario weights to 50/50 `same_merchant_unique_product` + `unique_merchant_unique_product`. Pass `--no-unique-only` to restore the original 5-scenario weights. Phase-2 always runs unique-only regardless of this flag.
- `--cleanup-after` / `--no-cleanup-after` (optional): Controls whether test data is automatically cleaned up after the report is generated. Default: cleanup runs automatically. Pass `--no-cleanup-after` to keep data in the staging DB for manual inspection.

## Prerequisites

1. **`.env.staging`** file exists in the autopilot root with `AUTOPILOT_URL` and `DATABASE_URL`
2. **Scenario file** exists at `.claude/skills/stress-test/stress_test_scenarios.json` (generated separately)
3. **Railway CLI** installed and logged in (optional, for live log streaming)
4. **Python venv** activated with `asyncpg` and `dotenv` available
5. **Staging environment** is running and healthy

## What this tests

| Component | What could break | How we detect it |
|---|---|---|
| Redis queue | Backpressure, memory | Queue depth over time |
| LLM calls | Rate limiting (OpenAI, OpenRouter) | HTTP errors, retries |
| Router | Fallback chain exhaustion | Routing distribution in results |
| DB save | Deadlocks, connection pool exhaustion | Deadlock count, connection errors |
| Product enrichment | Queue starvation | Enrichment delay, unenriched products |
| Worker stability | OOM, crashes | Tasks that never complete |
| Celery | Lost tasks, idempotency | Queued vs completed count |
| API layer (burst) | Connection backlog overflow, Uvicorn worker saturation | 5xx/4xx rate, p99 HTTP accept latency |
| Redis queue (burst) | Task enqueue backpressure | Network errors + accepted-but-not-processed gap |

## Phase definitions

### Phase 1 — Ramp load

| Phase | Emails | Scenario mix | Pause | Purpose |
|:---:|:---:|---|:---:|---|
| 1a | 50 | 100% same_merchant_shared_upc | 5 min | Deadlock scenario at moderate load |
| 1b | 100 | 70% same_merchant_shared_upc, 30% unique | 5 min | Contention + baseline mix |
| 1c | 250 | Full mix (5 scenarios, weighted) | 10 min | Realistic mixed workload |
| 1d | 500 | Full mix (5 scenarios, weighted) | 15 min | High load stress |

**Note:** By default, all phase-1 phases run with `--unique-only` (50/50 `same_merchant_unique_product` + `unique_merchant_unique_product`). The weights below only apply when `--no-unique-only` is passed.

### Phase 2 — Burst load (mirrors email-ingestion fan-out)

Phase 2 simulates the upstream email-ingestion service which calls `POST /autopilot/email` with `asyncio.gather` and no semaphore — meaning a single user with N unprocessed emails fans out N concurrent HTTP requests simultaneously. All N requests are dispatched within a 1-3s window using a dedicated thread pool sized to N.

| Phase | Total emails | Burst pattern | Pause | Purpose |
|:---:|:---:|---|:---:|---|
| 2a | 500 | 500 simultaneous (within ~1-2s) | 5 min | Single-user worst case |
| 2b | 1000 | 1000 simultaneous (within ~2-3s) | 10 min | 10 concurrent users at max fan-out |
| 2c | 2000 | 2000 simultaneous (within ~3-5s) | 15 min | Full peak load |

Phase 2 always uses the 50/50 unique-product scenario mix regardless of `--no-unique-only` — the goal is raw concurrency measurement, not scenario diversity.

**Pre-flight warning**: Each phase 2 run prints an estimated LLM cost and concurrency count before sending. The script enforces staging-only as usual.

**Scenario weights (for `--no-unique-only` mixed phases):**
- `same_merchant_shared_upc` (30%): Same merchant + same product UPCs -- the original deadlock scenario
- `same_merchant_unique_product` (25%): Same merchant + unique products -- merchant lock contention
- `unique_merchant_unique_product` (25%): Unique merchants + unique products -- no contention baseline
- `duplicate_order` (10%): Same order number sent twice -- idempotency check
- `cross_merchant_shared_upc` (10%): Different merchants + shared UPCs -- cross-merchant product dedup

## Execution steps

### Step 0 -- Parse arguments

Parse `$ARGUMENTS` for optional phase selector and flags.

- If `$ARGUMENTS` contains `--cleanup-only`, set cleanup-only mode (no test run).
- If `$ARGUMENTS` contains `1a`, `1b`, `1c`, or `1d`, set single-phase mode.
- If `$ARGUMENTS` contains `2a`, `2b`, or `2c`, set single burst-phase mode.
- If `$ARGUMENTS` contains just `2` (no letter suffix), run all phase-2 burst phases (2a, 2b, 2c).
- If `$ARGUMENTS` contains `--no-unique-only`, use all 5 scenarios with original weights for phase 1; otherwise use unique-only mode (default). Phase 2 always uses unique-only regardless.
- If `$ARGUMENTS` contains `--no-cleanup-after`, disable post-test auto-cleanup (data is kept for inspection); otherwise auto-cleanup is enabled by default.
- Otherwise, run all phase-1 phases.

### Step 1 -- Pre-flight checks

1. Load `.env.staging` for `AUTOPILOT_URL` and `DATABASE_URL`
2. `curl -s {AUTOPILOT_URL}/health` -- confirm 200
3. Test DB connectivity via asyncpg to `DATABASE_URL`
4. Validate `.claude/skills/stress-test/stress_test_scenarios.json` exists and has correct structure
5. Create `data/stress_test_logs/` directory if it doesn't exist
6. Pre-cleanup: delete any leftover `STRESS-*` test data from staging DB

If `--cleanup-only` was passed, run only the cleanup step and stop.

### Step 2 -- Start Railway log streaming

Instruct the user to run in a **separate terminal**:

```bash
cd /Users/lucasthim/projects/settlemate/settlemate/apps/autopilot
railway logs --service autopilot --environment staging 2>&1 | tee data/stress_test_logs/railway_$(date +%Y%m%d_%H%M%S).log
```

If Railway CLI is not available, direct the user to monitor logs from:
`https://railway.app/project/ae620a01-d3a8-4dca-a2b5-93af7c8cd912` -> autopilot service -> staging -> Logs tab

### Step 3 -- Pre-seed merchants and test user

Run the pipeline script with the `--pre-seed-only` flag:

```bash
source .venv/bin/activate
uv run python .claude/skills/stress-test/stress_test_pipeline.py --env-file .env.staging --pre-seed-only
```

This connects to staging DB and performs two pre-seeding operations:

1. **Merchants**: Inserts merchants from the scenario file's `merchants` array to avoid the known concurrent merchant creation race condition.
2. **Test user**: Inserts a shared `User` row and an `ACTIVE` `FinancialConnection` row from the scenario file's `test_user` object. All stress-test emails use this single user ID so the `free_trial` node's financial-connection check passes immediately, bypassing the free-trial credit gate without consuming DB queries under contention.

### Step 4 -- Execute test phases

Run the stress test pipeline script:

```bash
source .venv/bin/activate
uv run python .claude/skills/stress-test/stress_test_pipeline.py --env-file .env.staging [--phase 1a|1b|1c|1d|2a|2b|2c|2] [--no-unique-only]
```

The script handles:
- Loading scenarios from the JSON file
- Building payloads with unique order numbers, timestamps, task IDs
- Sending concurrent HTTP POST requests to `{AUTOPILOT_URL}/autopilot/email`
- Polling staging DB every 30s during pause windows
- Printing live progress lines

### Step 5 -- Wait for completion

After all phases, the script polls DB until all expected purchases appear or 60-minute timeout.

### Step 6 -- Generate report

The script queries staging DB and generates a comprehensive markdown report saved to `data/stress_test_logs/report_YYYYMMDD_HHMMSS.md`. The report includes:

- Executive summary (totals, conversion rate, deadlocks)
- Per-phase results (sent, accepted, purchases, enrichments, duration)
- Database verification (merchant dedup, product dedup, enrichment coverage, data integrity)
- Potential weak spots and warnings (auto-generated from results)
- Investigation guides for common issues
- Cleanup SQL

### Step 7 -- Report and cleanup

Print the report to stdout. Tell the user where log files and report are saved:
- Railway logs: `data/stress_test_logs/railway_*.log`
- Report: `data/stress_test_logs/report_*.md`

Data is automatically cleaned up after the report. Pass `--no-cleanup-after` to keep data for manual inspection, or run `/stress-test --cleanup-only` to clean up later.

**Redis flush:** When `--allow-local` is active, cleanup also flushes the Redis DB (`FLUSHDB`) to cancel any pending Celery tasks (e.g. price-extraction jobs queued during the test). This is skipped on staging to avoid disrupting real workloads sharing the Redis instance.

## Scenario file format

The skill expects `.claude/skills/stress-test/stress_test_scenarios.json` with this structure:

```json
{
  "test_user": {
    "user_id": "stress-test-user",
    "email_address": "stress-test@stress.settlemate.io",
    "name": "Stress Test User"
  },
  "merchants": [
    {"name": "StressScenario Electronics Hub", "normalized_name": "stressscenario electronics hub"},
    {"name": "StressScenario Fashion Outlet", "normalized_name": "stressscenario fashion outlet"}
  ],
  "scenarios": [
    {
      "name": "same_merchant_shared_upc",
      "weight": 0.30,
      "merchant_name": "StressScenario Electronics Hub",
      "order_prefix": "STRESS-SAMEMERCH-UPC",
      "emails": [{
        "subject_template": "Your order #{order_number} has shipped!",
        "sender": "orders@stressscenario-electronics.com",
        "body_template": "...HTML...",
        "items": [{"product_name": "...", "upc": "...", "price": 79.99}]
      }]
    }
  ]
}
```

## Logs and reports location

All output goes to `data/stress_test_logs/` (gitignored):
- `railway_YYYYMMDD_HHMMSS.log` -- Railway log stream
- `report_YYYYMMDD_HHMMSS.md` -- Test report

## Important notes

- **Staging only**: The script refuses to run if the API URL doesn't contain "staging"
- **Test data prefix**: All test data uses `STRESS-` prefix in order numbers and `StressScenario` in product/merchant names
- **Worker capacity**: With 16 staging workers, expect ~28 emails/min throughput
- **Cost (phase 1)**: ~$5-15 in LLM API credits for a full 900-email phase-1 run
- **Cost (phase 2)**: ~$5/500 emails (2a), ~$10/1000 emails (2b), ~$20/2000 emails (2c). Each phase prints an estimated cost warning before sending.
- **Phase 2 thread count**: Phase 2 spawns N OS threads (one per request) to achieve true simultaneous dispatch. 2000 threads is within normal OS limits but will spike CPU briefly at send time on the test machine.
