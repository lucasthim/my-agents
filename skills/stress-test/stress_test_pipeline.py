#!/usr/bin/env python3
"""
Stress test pipeline for the Autopilot staging environment.

Sends concurrent HTTP requests to POST /autopilot/email across multiple phases
with increasing load, then verifies DB state and generates a report.

This script is standalone — it does NOT import from src/. It uses asyncpg
directly for DB access and urllib for HTTP requests.

Usage:
    uv run python .claude/skills/stress-test/stress_test_pipeline.py \
        --env-file .env.staging [--phase 1a|1b|1c|1d|2a|2b|2c|2] [--cleanup] [--pre-seed-only]
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import asyncpg
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
AUTOPILOT_ROOT = SCRIPT_DIR.parent.parent.parent  # .claude/skills/stress-test -> root
LOG_DIR = AUTOPILOT_ROOT / "data" / "stress_test_logs"
SCENARIO_FILE = SCRIPT_DIR / "stress_test_scenarios.json"

PHASE_DEFINITIONS: dict[str, dict] = {
    "1a": {
        "name": "1a",
        "email_count": 50,
        "pause_seconds": 300,
        "scenario_weights": {"same_merchant_shared_upc": 1.0},
        "description": "Deadlock scenario at moderate load (50 emails, 100% same merchant + shared UPC)",
    },
    "1b": {
        "name": "1b",
        "email_count": 100,
        "pause_seconds": 300,
        "scenario_weights": {
            "same_merchant_shared_upc": 0.70,
            "unique_merchant_unique_product": 0.30,
        },
        "description": "Contention + baseline mix (100 emails, 70/30)",
    },
    "1c": {
        "name": "1c",
        "email_count": 250,
        "pause_seconds": 600,
        "scenario_weights": {
            "same_merchant_shared_upc": 0.30,
            "same_merchant_unique_product": 0.25,
            "unique_merchant_unique_product": 0.25,
            "duplicate_order": 0.10,
            "cross_merchant_shared_upc": 0.10,
        },
        "description": "Realistic mixed workload (250 emails, full scenario mix)",
    },
    "1d": {
        "name": "1d",
        "email_count": 500,
        "pause_seconds": 900,
        "scenario_weights": {
            "same_merchant_shared_upc": 0.30,
            "same_merchant_unique_product": 0.25,
            "unique_merchant_unique_product": 0.25,
            "duplicate_order": 0.10,
            "cross_merchant_shared_upc": 0.10,
        },
        "description": "High load stress (500 emails, full scenario mix)",
    },
    # ---------------------------------------------------------------------------
    # Phase 2: True burst tests — mirrors the upstream email-ingestion service's
    # asyncio.gather-with-no-semaphore fan-out pattern.
    # ---------------------------------------------------------------------------
    "2a": {
        "name": "2a",
        "email_count": 500,
        "pause_seconds": 300,
        "burst": True,
        "scenario_weights": {
            "same_merchant_unique_product": 0.50,
            "unique_merchant_unique_product": 0.50,
        },
        "description": "Single-user worst case (500 simultaneous, 5x EMAIL_MAX_PER_USER)",
        "estimated_cost_usd": 5.0,
    },
    "2b": {
        "name": "2b",
        "email_count": 1000,
        "pause_seconds": 600,
        "burst": True,
        "scenario_weights": {
            "same_merchant_unique_product": 0.50,
            "unique_merchant_unique_product": 0.50,
        },
        "description": "10 concurrent users at max fan-out (1000 simultaneous)",
        "estimated_cost_usd": 10.0,
    },
    "2c": {
        "name": "2c",
        "email_count": 2000,
        "pause_seconds": 900,
        "burst": True,
        "scenario_weights": {
            "same_merchant_unique_product": 0.50,
            "unique_merchant_unique_product": 0.50,
        },
        "description": "Full peak — 20 workers x 100 emails (2000 simultaneous)",
        "estimated_cost_usd": 20.0,
    },
}

# DB connection pool settings (standalone — not using the app's PoolManager)
DB_POOL_MIN = 2
DB_POOL_MAX = 10

# Polling interval during pause windows
POLL_INTERVAL_SECONDS = 30

# Final wait timeout
FINAL_WAIT_TIMEOUT_SECONDS = 3600  # 60 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_env(env_file: str) -> tuple[str, str]:
    """Load environment variables and return (api_url, database_url)."""
    env_path = AUTOPILOT_ROOT / env_file
    if not env_path.exists():
        print(f"ERROR: Environment file not found: {env_path}")
        sys.exit(1)

    load_dotenv(env_path, override=True)

    api_url = os.getenv("AUTOPILOT_URL", "")
    db_url = os.getenv("DATABASE_URL", "")

    if not api_url:
        print("ERROR: AUTOPILOT_URL not set in environment file")
        sys.exit(1)
    if not db_url:
        print("ERROR: DATABASE_URL not set in environment file")
        sys.exit(1)

    return api_url, db_url


def safety_check(api_url: str) -> None:
    """Refuse to run if the API URL doesn't look like staging."""
    if "staging" not in api_url.lower():
        print(
            f"SAFETY CHECK FAILED: AUTOPILOT_URL does not contain 'staging'.\n"
            f"  URL: {api_url}\n"
            f"This script is designed for staging only. Aborting."
        )
        sys.exit(1)


def load_scenarios() -> dict:
    """Load and validate the scenario file."""
    if not SCENARIO_FILE.exists():
        print(f"ERROR: Scenario file not found: {SCENARIO_FILE}")
        print("Generate it first, then re-run this script.")
        sys.exit(1)

    with open(SCENARIO_FILE) as f:
        data = json.load(f)

    if "merchants" not in data or "scenarios" not in data:
        print("ERROR: Scenario file must have 'merchants' and 'scenarios' keys")
        sys.exit(1)

    scenario_names = {s["name"] for s in data["scenarios"]}
    for phase_def in PHASE_DEFINITIONS.values():
        for scenario_name in phase_def["scenario_weights"]:
            if scenario_name not in scenario_names:
                print(
                    f"ERROR: Phase {phase_def['name']} references scenario "
                    f"'{scenario_name}' not found in scenario file. "
                    f"Available: {scenario_names}"
                )
                sys.exit(1)

    return data


def build_payloads(
    scenario_data: dict,
    phase_def: dict,
    global_counter_start: int,
) -> tuple[list[dict], int]:
    """Build email payloads for a phase.

    Returns (payloads, next_counter_value).
    """
    scenarios_by_name = {s["name"]: s for s in scenario_data["scenarios"]}
    test_user = scenario_data.get("test_user")
    email_count = phase_def["email_count"]
    weights = phase_def["scenario_weights"]

    # Distribute emails across scenarios based on weights
    distribution: dict[str, int] = {}
    remaining = email_count
    sorted_scenarios = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    for i, (scenario_name, weight) in enumerate(sorted_scenarios):
        if i == len(sorted_scenarios) - 1:
            distribution[scenario_name] = remaining
        else:
            count = round(email_count * weight)
            distribution[scenario_name] = count
            remaining -= count

    payloads: list[dict] = []
    counter = global_counter_start

    for scenario_name, count in distribution.items():
        scenario = scenarios_by_name[scenario_name]
        email_templates = scenario["emails"]

        # For duplicate_order scenario, send pairs with same order number
        is_duplicate = scenario_name == "duplicate_order"

        i = 0
        while i < count:
            template_idx = i % len(email_templates)
            template = email_templates[template_idx]

            order_number = f"{scenario['order_prefix']}-{counter:05d}"

            payload = _build_single_payload(
                template=template,
                scenario=scenario,
                order_number=order_number,
                counter=counter,
                test_user=test_user,
            )
            payloads.append(payload)
            i += 1

            # For duplicate_order: send a second email with the SAME order number
            if is_duplicate and i < count:
                dup_payload = _build_single_payload(
                    template=template,
                    scenario=scenario,
                    order_number=order_number,  # same order number
                    counter=counter + 10000,  # different user/email ID
                    test_user=test_user,
                )
                payloads.append(dup_payload)
                i += 1

            counter += 1

    return payloads, counter


def _build_single_payload(
    template: dict,
    scenario: dict,
    order_number: str,
    counter: int,
    test_user: dict | None = None,
) -> dict:
    """Build a single email payload from a template.

    If *test_user* is provided, every payload shares the same user_id and
    email_address so the pre-seeded FinancialConnection (status=ACTIVE)
    bypasses the free_trial gate.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    # Support per-task unique merchant names via merchant_name_template
    merchant_name_template = scenario.get("merchant_name_template")
    if merchant_name_template:
        merchant_name = merchant_name_template.replace("{task_id}", f"{counter:05d}")
    else:
        merchant_name = scenario["merchant_name"]

    # Build items HTML rows and text
    items = template.get("items", [])
    items_html_rows = ""
    total_amount = 0.0
    for item in items:
        product_name = item["product_name"]
        upc = item.get("upc", "")
        price = item["price"]

        # Handle per-task unique items
        if item.get("unique_per_task"):
            product_name = f"{product_name} #{counter:05d}"
            if upc:
                upc = f"{upc}-{counter:05d}"

        total_amount += price
        items_html_rows += (
            f"<tr><td>{product_name}</td><td>{upc}</td><td>${price:.2f}</td></tr>"
        )

    # Fill templates
    subject = template["subject_template"].replace("{order_number}", order_number)
    body_template = template.get("body_template", "")
    if body_template:
        body = (
            body_template.replace("{order_number}", order_number)
            .replace("{merchant_name}", merchant_name)
            .replace("{items_html}", items_html_rows)
            .replace("{total_amount}", f"{total_amount:.2f}")
            .replace("{timestamp}", now_iso)
        )
    else:
        # Default body if no template provided
        body = (
            f"<html><body>"
            f"<h1>Order Confirmation</h1>"
            f"<p>Thank you for your purchase from {merchant_name}!</p>"
            f"<p>Order Number: {order_number}</p>"
            f"<table>"
            f"<tr><th>Product</th><th>UPC</th><th>Price</th></tr>"
            f"{items_html_rows}"
            f"</table>"
            f"<p>Order Total: ${total_amount:.2f}</p>"
            f"<p>Payment Method: Visa ending in 4242</p>"
            f"</body></html>"
        )

    # Use shared test user if provided, otherwise fall back to per-task user
    if test_user:
        user_id = test_user["user_id"]
        email_address = test_user["email_address"]
    else:
        user_id = f"stress-test-{counter:05d}"
        email_address = f"stress-test-{counter:05d}@stress.settlemate.io"

    return {
        "user_id": user_id,
        "email_address": email_address,
        "subject": subject,
        "snippet": f"Order {order_number} from {merchant_name}",
        "body": body,
        "sender": template.get(
            "sender", f"orders@{merchant_name.lower().replace(' ', '')}.com"
        ),
        "date": now_iso,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _send_single_request(api_url: str, payload: dict) -> dict:
    """Send a single POST /autopilot/email request (synchronous, stdlib)."""
    url = urljoin(api_url.rstrip("/") + "/", "autopilot/email")
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})

    start = time.monotonic()
    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
            elapsed = time.monotonic() - start
            return {
                "order_number": payload.get("subject", ""),
                "status_code": resp.status,
                "response": body,
                "elapsed": elapsed,
                "error": None,
            }
    except HTTPError as e:
        # urllib raises HTTPError for 4xx/5xx — capture the real status code
        # so burst-phase reporting can distinguish API rejects from network drops.
        elapsed = time.monotonic() - start
        return {
            "order_number": payload.get("subject", ""),
            "status_code": e.code,
            "response": None,
            "elapsed": elapsed,
            "error": str(e),
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "order_number": payload.get("subject", ""),
            "status_code": None,
            "response": None,
            "elapsed": elapsed,
            "error": str(e),
        }


async def send_concurrent_requests(
    api_url: str,
    payloads: list[dict],
    burst: bool = False,
) -> list[dict]:
    """Send all requests concurrently using asyncio thread pool.

    When *burst* is True we size the ThreadPoolExecutor to match the number of
    payloads so that all requests are dispatched simultaneously — mirroring the
    upstream email-ingestion service's asyncio.gather-with-no-semaphore pattern.

    When *burst* is False we use the default executor (capped at ~32 threads),
    which gives the sustained-ramp behaviour of phase 1.
    """
    loop = asyncio.get_running_loop()

    if burst:
        # One thread per request: no implicit throttle.  Python threads are
        # cheap when blocked on I/O; 2000 threads is well within OS limits.
        executor = ThreadPoolExecutor(max_workers=len(payloads))
        try:
            tasks = [
                loop.run_in_executor(executor, _send_single_request, api_url, payload)
                for payload in payloads
            ]
            return list(await asyncio.gather(*tasks))
        finally:
            executor.shutdown(wait=False)
    else:
        tasks = [
            loop.run_in_executor(None, _send_single_request, api_url, payload)
            for payload in payloads
        ]
        return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


async def create_db_pool(db_url: str) -> asyncpg.Pool:
    """Create a standalone asyncpg connection pool."""
    return await asyncpg.create_pool(
        db_url,
        min_size=DB_POOL_MIN,
        max_size=DB_POOL_MAX,
        command_timeout=30,
        statement_cache_size=0,
    )


async def preflight_checks(api_url: str, db_url: str) -> bool:
    """Run pre-flight checks. Returns True if all pass."""
    print("\n--- Pre-flight checks ---")
    all_ok = True

    # 1. Health check
    print(f"  Checking API health: {api_url}/health ...")
    try:
        health_url = urljoin(api_url.rstrip("/") + "/", "health")
        req = Request(health_url)
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"  [OK] API healthy (HTTP {resp.status})")
            else:
                print(f"  [FAIL] API returned HTTP {resp.status}")
                all_ok = False
    except Exception as e:
        print(f"  [FAIL] API health check failed: {e}")
        all_ok = False

    # 2. DB connectivity
    print(f"  Checking DB connectivity: {db_url[:50]}...")
    try:
        conn = await asyncpg.connect(db_url, timeout=10, statement_cache_size=0)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        print(f"  [OK] DB connected ({version[:40]}...)")
    except Exception as e:
        print(f"  [FAIL] DB connection failed: {e}")
        all_ok = False

    # 3. Scenario file
    print(f"  Checking scenario file: {SCENARIO_FILE}")
    if SCENARIO_FILE.exists():
        print("  [OK] Scenario file exists")
    else:
        print("  [FAIL] Scenario file not found")
        all_ok = False

    # 4. Log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  [OK] Log directory ready: {LOG_DIR}")

    return all_ok


async def pre_seed_merchants(pool: asyncpg.Pool, merchants: list[dict]) -> None:
    """Pre-seed merchants in the staging DB to avoid concurrent creation races."""
    print("\n--- Pre-seeding merchants ---")
    async with pool.acquire() as conn:
        for merchant in merchants:
            name = merchant["name"]
            normalized = merchant.get("normalized_name", name.lower().strip())
            result = await conn.execute(
                """
                INSERT INTO "Merchant" (id, name, "normalizedName", "createdAt", "updatedAt")
                VALUES (gen_random_uuid(), $1, $2, NOW(), NOW())
                ON CONFLICT ("normalizedName") DO NOTHING
                """,
                name,
                normalized,
            )
            if "INSERT 0 1" in result:
                print(f"  [CREATED] {name} (normalized: {normalized})")
            else:
                print(f"  [EXISTS]  {name} (normalized: {normalized})")


async def pre_seed_test_user(pool: asyncpg.Pool, test_user: dict) -> None:
    """Pre-seed the shared test user and a FinancialConnection with ACTIVE status.

    This ensures that all stress-test emails bypass the free_trial gate
    (which checks FinancialConnection.status = 'ACTIVE' for the userId)
    without consuming a DB query under contention.
    """
    print("\n--- Pre-seeding test user ---")
    user_id = test_user["user_id"]
    email_address = test_user["email_address"]
    name = test_user.get("name", "Stress Test User")

    async with pool.acquire() as conn:
        # Upsert User row (provider is required by Prisma schema)
        result = await conn.execute(
            """
            INSERT INTO "User" (id, email, name, provider, "createdAt", "updatedAt")
            VALUES ($1, $2, $3, 'stress-test', NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET "updatedAt" = NOW()
            """,
            user_id,
            email_address,
            name,
        )
        if "INSERT 0 1" in result:
            print(f"  [CREATED] User: {user_id} ({email_address})")
        else:
            print(f"  [EXISTS]  User: {user_id} ({email_address})")

        # Insert FinancialConnection (ACTIVE) if not already present
        # Required columns: id, upstreamId, accessToken, institution, status,
        #                    metadata, userId
        result = await conn.execute(
            """
            INSERT INTO "FinancialConnection"
                (id, "upstreamId", "accessToken", institution, status, metadata,
                 "userId", "syncedAt", "createdAt", "updatedAt")
            SELECT
                gen_random_uuid(),
                'stress-test-upstream',
                'stress-test-token',
                'StressTestBank',
                'ACTIVE',
                '{}'::jsonb,
                $1,
                NOW(), NOW(), NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM "FinancialConnection"
                WHERE "userId" = $1 AND status = 'ACTIVE'
            )
            """,
            user_id,
        )
        if "INSERT 0 1" in result:
            print(f"  [CREATED] FinancialConnection (ACTIVE) for user {user_id}")
        else:
            print(f"  [EXISTS]  FinancialConnection (ACTIVE) for user {user_id}")


async def cleanup_test_data(pool: asyncpg.Pool) -> dict[str, int]:
    """Delete all STRESS-* test data from the staging DB.

    Returns dict with counts of deleted rows per table.
    """
    print("\n--- Cleaning up test data ---")
    counts: dict[str, int] = {}

    async with pool.acquire() as conn:
        # FK-safe order: PurchaseItem -> Purchase -> Product -> Merchant

        # 1. PurchaseItems linked to STRESS purchases
        result = await conn.execute(
            """
            DELETE FROM "PurchaseItem"
            WHERE "purchaseId" IN (
                SELECT id FROM "Purchase"
                WHERE "orderNumber" LIKE 'STRESS-%'
            )
            """
        )
        counts["PurchaseItem"] = _parse_delete_count(result)

        # 2. Purchases with STRESS- order numbers
        result = await conn.execute(
            """DELETE FROM "Purchase" WHERE "orderNumber" LIKE 'STRESS-%'"""
        )
        counts["Purchase"] = _parse_delete_count(result)

        # 3. Products with StressScenario prefix
        result = await conn.execute(
            """DELETE FROM "Product" WHERE name LIKE 'StressScenario%'"""
        )
        counts["Product"] = _parse_delete_count(result)

        # 4. Products with STRESS- UPC prefix
        result = await conn.execute(
            """DELETE FROM "Product" WHERE upc LIKE 'STRESS-%'"""
        )
        counts["Product (by UPC)"] = _parse_delete_count(result)

        # 5. Merchants with stressscenario prefix
        result = await conn.execute(
            """DELETE FROM "Merchant" WHERE "normalizedName" LIKE 'stressscenario%'"""
        )
        counts["Merchant"] = _parse_delete_count(result)

        # 6. FinancialConnections for the stress-test user
        result = await conn.execute(
            """DELETE FROM "FinancialConnection" WHERE "userId" = 'stress-test-user'"""
        )
        counts["FinancialConnection"] = _parse_delete_count(result)

        # 7. The stress-test User row itself
        result = await conn.execute(
            """DELETE FROM "User" WHERE id = 'stress-test-user'"""
        )
        counts["User"] = _parse_delete_count(result)

    for table, count in counts.items():
        print(f"  Deleted {count} rows from {table}")

    return counts


def _parse_delete_count(result: str) -> int:
    """Parse 'DELETE N' result string to get count."""
    try:
        return int(result.split(" ")[-1])
    except (ValueError, IndexError):
        return 0


def flush_redis_queues(redis_url: str) -> None:
    """Flush the Redis DB to cancel any queued Celery tasks.

    Only safe to call in local/dev environments where Redis is not shared with
    other workloads. Uses redis-py (a transitive Celery dependency). If redis
    is not importable or the server is unreachable, prints a warning and
    continues — cleanup is best-effort.
    """
    print("\n--- Flushing Redis queues ---")
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=3)
        client.flushdb()
        client.close()
        print(f"  Flushed Redis at {redis_url}")
    except Exception as e:
        print(f"  [WARN] Could not flush Redis ({redis_url}): {e}")
        print("  Pending Celery tasks may continue running until the worker stops.")


async def poll_db_progress(pool: asyncpg.Pool) -> dict[str, int]:
    """Poll the DB for current stress test progress.

    Resilient to transient connection drops from the Supabase pooler — retries
    up to 3 times with short backoff before giving up. On failure, returns
    the last known counts (or zeros) instead of crashing the test.
    """
    last_error = None
    for attempt in range(3):
        try:
            async with pool.acquire() as conn:
                purchases = await conn.fetchval(
                    """SELECT COUNT(*) FROM "Purchase" WHERE "orderNumber" LIKE 'STRESS-%'"""
                )
                products = await conn.fetchval(
                    """SELECT COUNT(*) FROM "Product" WHERE name LIKE 'StressScenario%' OR upc LIKE 'STRESS-%'"""
                )
                # Count products whose updatedAt differs from createdAt (enrichment touched them).
                # Also count products referenced by a deduplicated PurchaseItem (enrichment merged them).
                enriched = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT p.id) FROM "Product" p
                    WHERE (p.name LIKE 'StressScenario%' OR p.upc LIKE 'STRESS-%')
                    AND (
                        p."updatedAt" > p."createdAt"
                        OR EXISTS (
                            SELECT 1 FROM "PurchaseItem" pi
                            WHERE pi."productId" = p.id
                            AND pi."updatedAt" > pi."createdAt"
                        )
                    )
                    """
                )
            return {
                "purchases": purchases,
                "products": products,
                "enriched": enriched,
            }
        except (
            asyncpg.exceptions.ConnectionDoesNotExistError,
            asyncpg.exceptions.InterfaceError,
            ConnectionResetError,
            OSError,
        ) as e:
            last_error = e
            print(f"    [WARN] DB poll failed (attempt {attempt + 1}/3): {e}")
            await asyncio.sleep(2)

    print(f"    [WARN] DB poll giving up after 3 attempts: {last_error}")
    return {
        "purchases": 0,
        "products": 0,
        "enriched": 0,
    }


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------


async def send_phase(
    api_url: str,
    pool: asyncpg.Pool,
    phase_def: dict,
    payloads: list[dict],
) -> dict:
    """Execute a single test phase: send emails, pause with polling."""
    phase_name = phase_def["name"]
    pause_seconds = phase_def["pause_seconds"]
    email_count = len(payloads)
    is_burst = phase_def.get("burst", False)

    print(f"\n{'=' * 60}")
    print(f"PHASE {phase_name}: {phase_def['description']}")

    if is_burst:
        estimated_cost = phase_def.get("estimated_cost_usd", 0)
        print("  *** BURST PHASE WARNING ***")
        print(
            f"  This will dispatch {email_count} concurrent HTTP requests "
            f"within a 1-3s window."
        )
        print(f"  Estimated LLM cost: ~${estimated_cost:.0f} USD for this phase alone.")
        print("  Ensure you are pointed at STAGING before proceeding.")

    print(f"  Sending {email_count} emails...")
    print(f"{'=' * 60}")

    # Send all requests concurrently (burst phases use a dedicated thread pool
    # sized to email_count so every request is in-flight simultaneously)
    send_start = time.monotonic()
    http_results = await send_concurrent_requests(api_url, payloads, burst=is_burst)
    send_elapsed = time.monotonic() - send_start

    # Status breakdown
    accepted = sum(1 for r in http_results if r["status_code"] in (200, 202))
    client_errors = sum(
        1 for r in http_results if r["status_code"] and 400 <= r["status_code"] < 500
    )
    server_errors = sum(
        1 for r in http_results if r["status_code"] and r["status_code"] >= 500
    )
    network_errors = sum(1 for r in http_results if r["error"] and not r["status_code"])

    # Per-request latency distribution (all requests that completed, success or not)
    latencies = [r["elapsed"] for r in http_results if r["elapsed"] is not None]
    latency_stats: dict = {}
    if latencies:
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)
        latency_stats = {
            "p50": latencies_sorted[int(n * 0.50)],
            "p95": latencies_sorted[int(n * 0.95)],
            "p99": latencies_sorted[min(int(n * 0.99), n - 1)],
            "avg": statistics.mean(latencies_sorted),
            "min": latencies_sorted[0],
            "max": latencies_sorted[-1],
        }

    print(f"  Sent {email_count} requests in {send_elapsed:.1f}s")
    print(
        f"  Accepted (2xx): {accepted} | 4xx: {client_errors} | "
        f"5xx: {server_errors} | Network errors: {network_errors}"
    )
    if latency_stats:
        print(
            f"  Latency — p50: {latency_stats['p50']:.2f}s | "
            f"p95: {latency_stats['p95']:.2f}s | "
            f"p99: {latency_stats['p99']:.2f}s | "
            f"max: {latency_stats['max']:.2f}s"
        )

    # Print sample of errors (cap at 10 to avoid flooding output)
    error_results = [r for r in http_results if r["error"]]
    if error_results:
        print(f"  Sample errors (showing up to 10 of {len(error_results)}):")
        for r in error_results[:10]:
            print(f"    {r['order_number']}: {r['error']}")

    # Pause with polling
    print(
        f"\n  Waiting {pause_seconds}s for processing (polling every {POLL_INTERVAL_SECONDS}s)..."
    )
    elapsed = 0
    while elapsed < pause_seconds:
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, pause_seconds - elapsed))
        elapsed += POLL_INTERVAL_SECONDS
        progress = await poll_db_progress(pool)
        remaining = max(0, pause_seconds - elapsed)
        print(
            f"    [{remaining:>4}s left] Purchases: {progress['purchases']} | "
            f"Products: {progress['products']} | Enriched: {progress['enriched']}"
        )

    # Final progress snapshot
    final_progress = await poll_db_progress(pool)

    return {
        "phase": phase_name,
        "description": phase_def["description"],
        "burst": is_burst,
        "emails_sent": email_count,
        "accepted": accepted,
        "client_errors": client_errors,
        "server_errors": server_errors,
        "network_errors": network_errors,
        "failed": client_errors + server_errors + network_errors,
        "send_elapsed": send_elapsed,
        "latency_stats": latency_stats,
        "pause_seconds": pause_seconds,
        "http_errors": [r["error"] for r in http_results if r["error"]],
        "progress_at_end": final_progress,
    }


async def wait_for_completion(
    pool: asyncpg.Pool,
    expected_purchases: int,
    timeout_seconds: int = FINAL_WAIT_TIMEOUT_SECONDS,
) -> dict:
    """Wait until all expected purchases appear in DB, or timeout."""
    print(
        f"\n--- Waiting for completion (expecting {expected_purchases} purchases, timeout {timeout_seconds}s) ---"
    )

    start = time.monotonic()
    last_count = 0
    stale_polls = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout_seconds:
            progress = await poll_db_progress(pool)
            print(f"\n  TIMEOUT after {timeout_seconds}s!")
            return {
                "timed_out": True,
                "elapsed": elapsed,
                "final_progress": progress,
            }

        progress = await poll_db_progress(pool)
        current = progress["purchases"]

        print(
            f"  [{elapsed:>6.0f}s] Purchases: {current}/{expected_purchases} | "
            f"Products: {progress['products']} | Enriched: {progress['enriched']}"
        )

        if current >= expected_purchases:
            # Wait one more cycle for enrichment to catch up
            print("  All purchases found! Waiting 60s for enrichment to finalize...")
            await asyncio.sleep(60)
            final = await poll_db_progress(pool)
            return {
                "timed_out": False,
                "elapsed": time.monotonic() - start,
                "final_progress": final,
            }

        # Detect stalling
        if current == last_count:
            stale_polls += 1
            if stale_polls >= 10:
                stale_seconds = stale_polls * POLL_INTERVAL_SECONDS
                print(f"  WARNING: No progress for {stale_seconds}s")
                # After 10 minutes of no progress, assume remaining emails
                # were routed away from RetailAgent (LLM routing issue)
                if stale_seconds >= 600:
                    print(
                        f"  Giving up after {stale_seconds}s stall. "
                        f"{current}/{expected_purchases} purchases found."
                    )
                    final = await poll_db_progress(pool)
                    return {
                        "timed_out": False,
                        "stalled": True,
                        "elapsed": time.monotonic() - start,
                        "final_progress": final,
                    }
        else:
            stale_polls = 0
        last_count = current

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


async def generate_report(
    pool: asyncpg.Pool,
    phase_results: list[dict],
    total_sent: int,
    completion_result: dict,
) -> str:
    """Generate a comprehensive markdown report from DB state and phase results."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Query detailed DB stats
    db_stats = await _query_db_stats(pool)

    # Build report
    lines: list[str] = []
    lines.append(f"# Stress Test Report - {timestamp}")
    lines.append("")

    # Executive summary
    total_accepted = sum(p["accepted"] for p in phase_results)
    total_failed = sum(p["failed"] for p in phase_results)
    purchases_created = db_stats["total_purchases"]
    conversion_rate = (
        (purchases_created / total_accepted * 100) if total_accepted > 0 else 0
    )

    lines.append("## Executive Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total emails sent | {total_sent} |")
    lines.append(f"| Accepted (HTTP 2xx) | {total_accepted} |")
    lines.append(f"| Rejected (HTTP errors) | {total_failed} |")
    lines.append(f"| Purchases created | {purchases_created} |")
    lines.append(f"| Conversion rate | {conversion_rate:.1f}% |")
    lines.append(f"| Total products | {db_stats['total_products']} |")
    lines.append(f"| Enriched products | {db_stats['enriched_products']} |")
    lines.append(f"| Duplicate merchants | {db_stats['duplicate_merchants']} |")
    lines.append(f"| Orphan purchases | {db_stats['orphan_purchases']} |")
    lines.append(
        f"| Timed out | {'Yes' if completion_result.get('timed_out') else 'No'} |"
    )
    lines.append(f"| Total elapsed | {completion_result.get('elapsed', 0):.0f}s |")
    lines.append("")

    # Per-phase results
    lines.append("## Per-Phase Results")
    lines.append("")
    lines.append(
        "| Phase | Description | Sent | Accepted | 4xx | 5xx | Net Err | Purchases | Send time |"
    )
    lines.append("|:---:|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for pr in phase_results:
        progress = pr.get("progress_at_end", {})
        lines.append(
            f"| {pr['phase']} | {pr['description'][:45]} | "
            f"{pr['emails_sent']} | {pr['accepted']} | "
            f"{pr.get('client_errors', '?')} | {pr.get('server_errors', '?')} | "
            f"{pr.get('network_errors', '?')} | "
            f"{progress.get('purchases', '?')} | "
            f"{pr['send_elapsed']:.1f}s |"
        )
    lines.append("")

    # Burst phase HTTP latency report (only shown when at least one burst phase ran)
    burst_phases = [pr for pr in phase_results if pr.get("burst")]
    if burst_phases:
        lines.append("## Burst Phase HTTP Latency")
        lines.append("")
        lines.append(
            "Time-to-accept distribution measures how long the API took to respond "
            "to each individual request — not total processing time. A high p99 "
            "indicates the API layer itself was choking under burst load."
        )
        lines.append("")
        lines.append(
            "| Phase | Concurrency | p50 | p95 | p99 | avg | min | max | Accepted | Queued cleanly |"
        )
        lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        for pr in burst_phases:
            ls = pr.get("latency_stats", {})
            if ls:
                accepted_pct = (
                    (pr["accepted"] / pr["emails_sent"] * 100)
                    if pr["emails_sent"] > 0
                    else 0
                )
                lines.append(
                    f"| {pr['phase']} | {pr['emails_sent']} | "
                    f"{ls['p50']:.2f}s | {ls['p95']:.2f}s | {ls['p99']:.2f}s | "
                    f"{ls['avg']:.2f}s | {ls['min']:.2f}s | {ls['max']:.2f}s | "
                    f"{pr['accepted']} | {accepted_pct:.1f}% |"
                )
            else:
                lines.append(
                    f"| {pr['phase']} | {pr['emails_sent']} | "
                    f"n/a | n/a | n/a | n/a | n/a | n/a | {pr['accepted']} | - |"
                )
        lines.append("")
        lines.append(
            "> **Interpreting results**: High 4xx/5xx during burst means the API layer "
            "or its upstream queue rejected overload. Network errors (connection reset, "
            "timeout) indicate the server closed connections before responding — a sign "
            "of connection pool exhaustion or OS-level backlog overflow. "
            "If accepted=sent but purchases << accepted, the workers dropped tasks after "
            "accepting them."
        )
        lines.append("")

    # Database verification
    lines.append("## Database Verification")
    lines.append("")

    # Merchant deduplication
    lines.append("### Merchant Deduplication")
    lines.append("")
    if db_stats["merchant_rows"]:
        lines.append("| Normalized Name | Count |")
        lines.append("|---|:---:|")
        for row in db_stats["merchant_rows"]:
            lines.append(f"| {row['normalizedName']} | {row['count']} |")
    else:
        lines.append("No stress test merchants found.")
    lines.append("")

    # Shared UPC deduplication
    lines.append("### Shared UPC Deduplication")
    lines.append("")
    if db_stats["shared_upc_rows"]:
        lines.append("| UPC | Product Count |")
        lines.append("|---|:---:|")
        for row in db_stats["shared_upc_rows"]:
            lines.append(f"| {row['upc']} | {row['count']} |")
    else:
        lines.append("No duplicate UPCs found (good -- dedup is working).")
    lines.append("")

    # Enrichment coverage (split by UPC type)
    lines.append("### Enrichment Coverage")
    lines.append("")
    enrichment_pct = (
        (db_stats["enriched_products"] / db_stats["total_products"] * 100)
        if db_stats["total_products"] > 0
        else 0
    )
    lines.append(
        f"- **Total products: {db_stats['total_products']}** (enriched: {db_stats['enriched_products']}, {enrichment_pct:.1f}%)"
    )
    lines.append("")

    # Unique-UPC products (1 task = 1 product, so enrichment ratio is meaningful)
    unique_pct = (
        (db_stats["enriched_unique_upc"] / db_stats["total_unique_upc"] * 100)
        if db_stats["total_unique_upc"] > 0
        else 0
    )
    lines.append(
        f"- Unique-UPC products: {db_stats['enriched_unique_upc']}/{db_stats['total_unique_upc']} "
        f"enriched ({unique_pct:.1f}%) -- **this is the reliable enrichment signal**"
    )

    # Shared-UPC products (dedup collapses many tasks onto one row, low % is expected)
    shared_pct = (
        (db_stats["enriched_shared_upc"] / db_stats["total_shared_upc"] * 100)
        if db_stats["total_shared_upc"] > 0
        else 0
    )
    lines.append(
        f"- Shared-UPC products: {db_stats['enriched_shared_upc']}/{db_stats['total_shared_upc']} "
        f"enriched ({shared_pct:.1f}%) -- low % expected (dedup collapses to canonical row)"
    )
    lines.append("")

    # Purchase integrity
    lines.append("### Purchase Integrity")
    lines.append("")
    lines.append(f"- Total purchases: {db_stats['total_purchases']}")
    lines.append(f"- Total purchase items: {db_stats['total_purchase_items']}")
    lines.append(f"- Orphan purchases (no items): {db_stats['orphan_purchases']}")
    lines.append("")

    # Warnings
    warnings = _generate_warnings(db_stats, phase_results, total_sent, total_accepted)
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- **{w['severity']}**: {w['message']}")
        lines.append("")

    # Cleanup SQL
    lines.append("## Cleanup SQL")
    lines.append("")
    lines.append("Run these queries to remove all stress test data:")
    lines.append("")
    lines.append("```sql")
    lines.append(
        'DELETE FROM "PurchaseItem" WHERE "purchaseId" IN '
        '(SELECT id FROM "Purchase" WHERE "orderNumber" LIKE \'STRESS-%\');'
    )
    lines.append('DELETE FROM "Purchase" WHERE "orderNumber" LIKE \'STRESS-%\';')
    lines.append("DELETE FROM \"Product\" WHERE name LIKE 'StressScenario%';")
    lines.append("DELETE FROM \"Product\" WHERE upc LIKE 'STRESS-%';")
    lines.append(
        'DELETE FROM "Merchant" WHERE "normalizedName" LIKE \'stressscenario%\';'
    )
    lines.append(
        'DELETE FROM "FinancialConnection" WHERE "userId" = \'stress-test-user\';'
    )
    lines.append("DELETE FROM \"User\" WHERE id = 'stress-test-user';")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


async def _query_db_stats(pool: asyncpg.Pool) -> dict:
    """Query comprehensive DB stats for the report."""
    async with pool.acquire() as conn:
        total_purchases = await conn.fetchval(
            """SELECT COUNT(*) FROM "Purchase" WHERE "orderNumber" LIKE 'STRESS-%'"""
        )

        total_products = await conn.fetchval(
            """
            SELECT COUNT(*) FROM "Product"
            WHERE name LIKE 'StressScenario%' OR upc LIKE 'STRESS-%'
            """
        )

        enriched_products = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT p.id) FROM "Product" p
            WHERE (p.name LIKE 'StressScenario%' OR p.upc LIKE 'STRESS-%')
            AND (
                p."updatedAt" > p."createdAt"
                OR EXISTS (
                    SELECT 1 FROM "PurchaseItem" pi
                    WHERE pi."productId" = p.id
                    AND pi."updatedAt" > pi."createdAt"
                )
            )
            """
        )

        # Enrichment split by scenario type, keyed off Purchase.orderNumber
        # prefix (always populated, unlike Product.upc which may be NULL).
        # Shared-UPC scenarios: same_merchant_shared_upc, duplicate_order,
        # cross_merchant_shared_upc (products deduplicated across purchases).
        # Unique scenarios: same_merchant_unique_product, unique_merchant_unique_product.
        shared_order_prefixes = [
            "STRESS-SAMEMERCH-UPC%",
            "STRESS-DUPE%",
            "STRESS-CROSSMERCH-UPC%",
        ]
        unique_order_prefixes = [
            "STRESS-SAMEMERCH-UNIQ%",
            "STRESS-UNIQMERCH%",
        ]

        total_shared_upc = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT p.id) FROM "Product" p
            JOIN "PurchaseItem" pi ON pi."productId" = p.id
            JOIN "Purchase" pur ON pi."purchaseId" = pur.id
            WHERE pur."orderNumber" LIKE ANY($1::text[])
            """,
            shared_order_prefixes,
        )
        enriched_shared_upc = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT p.id) FROM "Product" p
            JOIN "PurchaseItem" pi ON pi."productId" = p.id
            JOIN "Purchase" pur ON pi."purchaseId" = pur.id
            WHERE pur."orderNumber" LIKE ANY($1::text[])
            AND (
                p."updatedAt" > p."createdAt"
                OR pi."updatedAt" > pi."createdAt"
            )
            """,
            shared_order_prefixes,
        )
        total_unique_upc = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT p.id) FROM "Product" p
            JOIN "PurchaseItem" pi ON pi."productId" = p.id
            JOIN "Purchase" pur ON pi."purchaseId" = pur.id
            WHERE pur."orderNumber" LIKE ANY($1::text[])
            """,
            unique_order_prefixes,
        )
        enriched_unique_upc = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT p.id) FROM "Product" p
            JOIN "PurchaseItem" pi ON pi."productId" = p.id
            JOIN "Purchase" pur ON pi."purchaseId" = pur.id
            WHERE pur."orderNumber" LIKE ANY($1::text[])
            AND (
                p."updatedAt" > p."createdAt"
                OR pi."updatedAt" > pi."createdAt"
            )
            """,
            unique_order_prefixes,
        )

        # Merchant dedup
        merchant_rows = await conn.fetch(
            """
            SELECT "normalizedName", COUNT(*) as count
            FROM "Merchant"
            WHERE "normalizedName" LIKE 'stressscenario%'
            GROUP BY "normalizedName"
            ORDER BY "normalizedName"
            """
        )

        # Shared UPC dedup (UPCs that appear more than once)
        shared_upc_rows = await conn.fetch(
            """
            SELECT upc, COUNT(*) as count
            FROM "Product"
            WHERE upc LIKE 'STRESS-%'
            GROUP BY upc
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT 20
            """
        )

        # Duplicate merchants (normalizedName appearing more than once)
        duplicate_merchants = await conn.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT "normalizedName"
                FROM "Merchant"
                WHERE "normalizedName" LIKE 'stressscenario%'
                GROUP BY "normalizedName"
                HAVING COUNT(*) > 1
            ) sub
            """
        )

        # Purchase items
        total_purchase_items = await conn.fetchval(
            """
            SELECT COUNT(*) FROM "PurchaseItem" pi
            JOIN "Purchase" p ON pi."purchaseId" = p.id
            WHERE p."orderNumber" LIKE 'STRESS-%'
            """
        )

        # Orphan purchases (no items)
        orphan_purchases = await conn.fetchval(
            """
            SELECT COUNT(*) FROM "Purchase" p
            WHERE p."orderNumber" LIKE 'STRESS-%'
            AND NOT EXISTS (
                SELECT 1 FROM "PurchaseItem" pi WHERE pi."purchaseId" = p.id
            )
            """
        )

    return {
        "total_purchases": total_purchases,
        "total_products": total_products,
        "enriched_products": enriched_products,
        "total_shared_upc": total_shared_upc,
        "enriched_shared_upc": enriched_shared_upc,
        "total_unique_upc": total_unique_upc,
        "enriched_unique_upc": enriched_unique_upc,
        "merchant_rows": [dict(r) for r in merchant_rows],
        "shared_upc_rows": [dict(r) for r in shared_upc_rows],
        "duplicate_merchants": duplicate_merchants,
        "total_purchase_items": total_purchase_items,
        "orphan_purchases": orphan_purchases,
    }


def _generate_warnings(
    db_stats: dict,
    phase_results: list[dict],
    total_sent: int,
    total_accepted: int,
) -> list[dict]:
    """Generate warning messages based on results."""
    warnings: list[dict] = []

    # High rejection rate
    if total_sent > 0 and total_accepted < total_sent * 0.95:
        rejection_pct = ((total_sent - total_accepted) / total_sent) * 100
        warnings.append(
            {
                "severity": "HIGH",
                "message": f"{rejection_pct:.1f}% of HTTP requests were rejected. Check API logs for errors.",
            }
        )

    # Low conversion rate
    if total_accepted > 0 and db_stats["total_purchases"] < total_accepted * 0.80:
        conversion = (db_stats["total_purchases"] / total_accepted) * 100
        warnings.append(
            {
                "severity": "HIGH",
                "message": (
                    f"Only {conversion:.1f}% of accepted emails resulted in purchases. "
                    "Check worker logs for processing failures."
                ),
            }
        )

    # Duplicate merchants
    if db_stats["duplicate_merchants"] > 0:
        warnings.append(
            {
                "severity": "MEDIUM",
                "message": (
                    f"{db_stats['duplicate_merchants']} merchant name(s) have duplicate rows. "
                    "Merchant deduplication may have race conditions."
                ),
            }
        )

    # Shared UPC duplicates
    if db_stats["shared_upc_rows"]:
        warnings.append(
            {
                "severity": "MEDIUM",
                "message": (
                    f"{len(db_stats['shared_upc_rows'])} UPC(s) have duplicate Product rows. "
                    "Product UPC deduplication may have issues."
                ),
            }
        )

    # Orphan purchases
    if db_stats["orphan_purchases"] > 0:
        warnings.append(
            {
                "severity": "MEDIUM",
                "message": (
                    f"{db_stats['orphan_purchases']} purchase(s) have no items. "
                    "Check product extraction or DB save logic."
                ),
            }
        )

    # Low enrichment rate (only check unique-UPC products — shared-UPC ratio is
    # expected to be low because dedup collapses many tasks onto one canonical row)
    if db_stats["total_unique_upc"] > 0:
        enrichment_rate = db_stats["enriched_unique_upc"] / db_stats["total_unique_upc"]
        if enrichment_rate < 0.50:
            warnings.append(
                {
                    "severity": "MEDIUM",
                    "message": (
                        f"Only {enrichment_rate * 100:.1f}% of unique-UPC products were enriched. "
                        "Enrichment pipeline may be backlogged or failing."
                    ),
                }
            )

    # HTTP errors in phases
    for pr in phase_results:
        if pr["http_errors"]:
            unique_errors = set(pr["http_errors"])
            severity = "HIGH" if pr.get("burst") else "LOW"
            warnings.append(
                {
                    "severity": severity,
                    "message": (
                        f"Phase {pr['phase']} had {len(pr['http_errors'])} network errors. "
                        f"Unique: {', '.join(list(unique_errors)[:3])}"
                    ),
                }
            )

    # Burst-specific: warn if API rejected >5% at burst concurrency
    for pr in phase_results:
        if pr.get("burst") and pr["emails_sent"] > 0:
            rejection_pct = (pr["failed"] / pr["emails_sent"]) * 100
            if rejection_pct > 5:
                warnings.append(
                    {
                        "severity": "HIGH",
                        "message": (
                            f"Phase {pr['phase']} burst: {rejection_pct:.1f}% of "
                            f"{pr['emails_sent']} simultaneous requests were rejected. "
                            f"({pr.get('client_errors', 0)} 4xx, "
                            f"{pr.get('server_errors', 0)} 5xx, "
                            f"{pr.get('network_errors', 0)} network errors). "
                            "The API or queue is dropping requests under burst load."
                        ),
                    }
                )

        # Warn if p99 latency is very high for burst phases (API is slow to accept)
        if pr.get("burst"):
            ls = pr.get("latency_stats", {})
            if ls.get("p99", 0) > 10:
                warnings.append(
                    {
                        "severity": "MEDIUM",
                        "message": (
                            f"Phase {pr['phase']} burst: p99 HTTP accept latency is "
                            f"{ls['p99']:.1f}s. The API is slow to queue requests under "
                            "burst — check Uvicorn worker count and connection backlog."
                        ),
                    }
                )

    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    parser = argparse.ArgumentParser(
        description="Stress test pipeline for the Autopilot staging environment"
    )
    parser.add_argument(
        "--env-file",
        default=".env.staging",
        help="Path to env file relative to autopilot root (default: .env.staging)",
    )
    parser.add_argument(
        "--phase",
        choices=["1a", "1b", "1c", "1d", "2a", "2b", "2c", "2"],
        default=None,
        help=(
            "Run a specific phase only (default: all phases). "
            "Use '2' to run all phase-2 burst phases (2a, 2b, 2c)."
        ),
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Just cleanup test data and exit (no test run)",
    )
    parser.add_argument(
        "--pre-seed-only",
        action="store_true",
        help="Just pre-seed merchants and exit",
    )
    parser.add_argument(
        "--allow-local",
        action="store_true",
        help="Bypass the staging-only safety check (for local testing)",
    )
    parser.add_argument(
        "--unique-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Restrict to unique-product scenarios only (50/50 split). "
            "Use --no-unique-only to restore all 5 scenarios. Default: True."
        ),
    )
    parser.add_argument(
        "--cleanup-after",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Automatically clean up test data after the report is generated. "
            "Use --no-cleanup to keep data in the staging DB for manual inspection. "
            "Default: True (cleanup runs automatically)."
        ),
    )
    args = parser.parse_args()

    # Load environment
    api_url, db_url = load_env(args.env_file)
    if not args.allow_local:
        safety_check(api_url)

    print("Stress Test Pipeline")
    print(f"  API URL:  {api_url}")
    print(f"  DB URL:   {db_url[:50]}...")
    if args.cleanup_only:
        mode_label = "cleanup-only"
    elif args.pre_seed_only:
        mode_label = "pre-seed"
    elif args.phase == "2":
        mode_label = "all phase-2 burst phases (2a, 2b, 2c)"
    elif args.phase:
        mode_label = f"phase {args.phase}"
    else:
        mode_label = "all phases (1a, 1b, 1c, 1d)"
    print(f"  Mode:     {mode_label}")
    print(
        f"  Scenarios: {'unique-only (50/50)' if args.unique_only else 'full mix (5 scenarios)'}"
    )
    print(
        f"  Auto-cleanup: {'enabled (pass --no-cleanup to keep data)' if args.cleanup_after else 'disabled'}"
    )

    # Create DB pool
    pool = await create_db_pool(db_url)

    try:
        # Cleanup-only mode
        if args.cleanup_only:
            await cleanup_test_data(pool)
            # Flush Redis to cancel any pending Celery tasks (local/dev only).
            if args.allow_local:
                redis_url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL", "")
                if redis_url:
                    flush_redis_queues(redis_url)
            print("\nCleanup complete.")
            return True

        # Pre-seed mode
        if args.pre_seed_only:
            scenario_data = load_scenarios()
            await pre_seed_merchants(pool, scenario_data["merchants"])
            if scenario_data.get("test_user"):
                await pre_seed_test_user(pool, scenario_data["test_user"])
            print("\nPre-seed complete.")
            return True

        # Full test mode
        # Pre-flight
        ok = await preflight_checks(api_url, db_url)
        if not ok:
            print("\nPre-flight checks failed. Fix the issues above and retry.")
            return False

        # Load scenarios
        scenario_data = load_scenarios()

        # Pre-cleanup
        await cleanup_test_data(pool)

        # Pre-seed merchants and test user
        await pre_seed_merchants(pool, scenario_data["merchants"])
        if scenario_data.get("test_user"):
            await pre_seed_test_user(pool, scenario_data["test_user"])

        # Determine which phases to run
        if args.phase == "2":
            # Shorthand: run all phase-2 burst phases
            phases_to_run = ["2a", "2b", "2c"]
        elif args.phase:
            phases_to_run = [args.phase]
        else:
            phases_to_run = ["1a", "1b", "1c", "1d"]

        # Apply --unique-only override to non-burst phase-1 definitions.
        # Phase-2 definitions already use the unique-only 50/50 mix by default;
        # --no-unique-only does NOT affect phase 2 (burst phases always run unique-only
        # because the goal is raw concurrency measurement, not scenario diversity).
        if args.unique_only:
            unique_only_weights = {
                "same_merchant_unique_product": 0.50,
                "unique_merchant_unique_product": 0.50,
            }
            for phase_key in phases_to_run:
                if not PHASE_DEFINITIONS[phase_key].get("burst"):
                    PHASE_DEFINITIONS[phase_key] = {
                        **PHASE_DEFINITIONS[phase_key],
                        "scenario_weights": unique_only_weights,
                    }

        # Build payloads for all phases
        global_counter = 1
        phase_payloads: list[tuple[dict, list[dict]]] = []
        for phase_key in phases_to_run:
            phase_def = PHASE_DEFINITIONS[phase_key]
            payloads, global_counter = build_payloads(
                scenario_data, phase_def, global_counter
            )
            phase_payloads.append((phase_def, payloads))

        total_sent = sum(len(payloads) for _, payloads in phase_payloads)
        print(f"\n  Total emails to send across all phases: {total_sent}")

        # Execute phases
        phase_results: list[dict] = []
        for phase_def, payloads in phase_payloads:
            result = await send_phase(api_url, pool, phase_def, payloads)
            phase_results.append(result)

        # Wait for completion
        completion_result = await wait_for_completion(pool, total_sent)

        # Generate report
        report = await generate_report(
            pool, phase_results, total_sent, completion_result
        )

        # Save report
        report_filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        report_path = LOG_DIR / report_filename
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(report)

        # Print report
        print(f"\n{'=' * 70}")
        print(report)
        print(f"{'=' * 70}")
        print(f"\nReport saved to: {report_path}")

        # Auto-cleanup (default) or leave data for inspection
        if args.cleanup_after:
            await cleanup_test_data(pool)
            # Flush Redis to cancel any pending Celery tasks (local/dev only).
            if args.allow_local:
                redis_url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL", "")
                if redis_url:
                    flush_redis_queues(redis_url)
            print(
                "\nAuto-cleanup complete. Pass --no-cleanup-after to keep data for inspection."
            )
        else:
            print(
                "\nData left in staging DB for inspection. Run with --cleanup-only to remove."
            )

        # Return success based on whether we timed out
        return not completion_result.get("timed_out", False)

    finally:
        await pool.close()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
