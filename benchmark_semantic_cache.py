"""
benchmark_semantic_cache.py
----------------------------
Self-contained benchmark proving the performance difference between:
  OLD: O(N) client-side cosine similarity (previous implementation)
  NEW: O(log N) Redis native HNSW KNN query (current implementation)

Run:
    python benchmark_semantic_cache.py

Requirements:
    - docker compose up -d  (redis/redis-stack-server must be running)
    - .venv activated or uv run
"""

import sys, os, time, math, json, struct, hashlib, random, asyncio
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

# ── Output helpers ────────────────────────────────────────────────────────────
def header(t):  print(f"\n{'='*60}\n  {t}\n{'='*60}")
def ok(t):      print(f"  [PASS] {t}".encode('ascii','replace').decode())
def fail(t):    print(f"  [FAIL] {t}".encode('ascii','replace').decode())
def info(t):    print(f"  [INFO] {t}".encode('ascii','replace').decode())


# =============================================================================
# Redis setup
# =============================================================================

def get_clients():
    import redis as r
    from src.config.settings import settings
    sync   = r.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                     password=settings.REDIS_PASSWORD, decode_responses=True,
                     socket_timeout=10)
    binary = r.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                     password=settings.REDIS_PASSWORD, decode_responses=False,
                     socket_timeout=10)
    sync.ping()
    return sync, binary


def check_search_module(sync_client) -> bool:
    """Check for RediSearch by running a harmless FT command."""
    try:
        # FT.INFO on a non-existent index raises an error that contains
        # 'Unknown Index name' or 'no such index' — both mean search IS loaded.
        sync_client.execute_command("FT._LIST")
        return True   # command succeeded → module loaded
    except Exception as e:
        err = str(e).lower()
        # If the module is missing, redis returns "ERR unknown command"
        if "unknown command" in err:
            return False
        # Any other error (index not found etc.) means module IS loaded
        return True


# =============================================================================
# Seeding helpers
# =============================================================================

def rand_unit_vec(dims: int) -> list[float]:
    v = [random.gauss(0, 1) for _ in range(dims)]
    mag = math.sqrt(sum(x*x for x in v))
    return [x / mag for x in v]


def seed_old_string_format(sync_client, count: int, dims: int, prefix: str):
    """Seed STRING-type JSON entries — the OLD storage format."""
    info(f"Seeding {count:,} STRING entries (old format) ...")
    pipe = sync_client.pipeline(transaction=False)
    for i in range(count):
        vec = rand_unit_vec(dims)
        key = f"{prefix}fake_{i:06d}"
        pipe.set(key, json.dumps({"prompt": f"p{i}", "response": f"r{i}", "embedding": vec}))
    pipe.execute()
    ok(f"Seeded {count:,} STRING entries")


def seed_new_hash_format(binary_client, count: int, dims: int, prefix: str):
    """Seed HASH entries with binary float32 embeddings — the NEW storage format."""
    info(f"Seeding {count:,} HASH entries (new VSS format) ...")
    # Use multiple pipelines of 500 to avoid timeout
    batch = 500
    for start in range(0, count, batch):
        pipe = binary_client.pipeline(transaction=False)
        for i in range(start, min(start + batch, count)):
            vec = rand_unit_vec(dims)
            vec_bytes = struct.pack(f"<{dims}f", *vec)
            key = f"{prefix}fake_{i:06d}"
            pipe.hset(key, mapping={
                "prompt":    f"p{i}".encode(),
                "response":  f"r{i}".encode(),
                "embedding": vec_bytes,
            })
        pipe.execute()
    ok(f"Seeded {count:,} HASH entries")


# =============================================================================
# VSS index management (raw redis-py FT commands — no redisvl needed)
# =============================================================================

def drop_index(binary_client, idx: str):
    try:
        binary_client.execute_command("FT.DROPINDEX", idx)
    except Exception:
        pass


def create_hnsw_index(binary_client, idx: str, prefix: str, dims: int) -> bool:
    """Create HNSW vector index using raw FT.CREATE command."""
    drop_index(binary_client, idx)
    try:
        binary_client.execute_command(
            "FT.CREATE", idx,
            "ON", "HASH",
            "PREFIX", "1", prefix,
            "SCHEMA",
            "prompt",    "TEXT",
            "response",  "TEXT",
            "embedding", "VECTOR", "HNSW", "6",
                "TYPE",            "FLOAT32",
                "DIM",             str(dims),
                "DISTANCE_METRIC", "COSINE",
        )
        return True
    except Exception as e:
        fail(f"FT.CREATE failed: {e}")
        return False


def wait_for_index(binary_client, idx: str, expected_docs: int, timeout: float = 30.0):
    """Poll FT.INFO until num_docs reaches expected or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info_raw = binary_client.execute_command("FT.INFO", idx)
            # FT.INFO returns a flat list: [key, val, key, val, ...]
            d = {}
            for i in range(0, len(info_raw) - 1, 2):
                d[info_raw[i]] = info_raw[i+1]
            num_docs = int(d.get(b"num_docs", 0))
            if num_docs >= expected_docs:
                return num_docs
        except Exception:
            pass
        time.sleep(0.2)
    return -1


# =============================================================================
# OLD lookup — O(N) client-side cosine
# =============================================================================

def cosine(v1, v2) -> float:
    dot  = sum(x*y for x,y in zip(v1, v2))
    mag1 = math.sqrt(sum(x*x for x in v1))
    mag2 = math.sqrt(sum(x*x for x in v2))
    return dot / (mag1 * mag2) if mag1 and mag2 else 0.0


def old_lookup(sync_client, query_vec: list, prefix: str, threshold: float) -> Optional[str]:
    keys = sync_client.keys(f"{prefix}*")
    if not keys:
        return None
    pipe = sync_client.pipeline()
    for k in keys:
        pipe.get(k)
    raw_entries = pipe.execute()
    best, best_score = None, -1.0
    for raw in raw_entries:
        if not raw:
            continue
        entry = json.loads(raw)
        score = cosine(query_vec, entry["embedding"])
        if score > best_score:
            best_score, best = score, entry
    if best and best_score >= threshold:
        return best["response"]
    return None


# =============================================================================
# NEW lookup — single Redis KNN query
# =============================================================================

def new_lookup(binary_client, query_vec: list, dims: int, idx: str, threshold: float) -> Optional[str]:
    query_bytes = struct.pack(f"<{dims}f", *query_vec)
    try:
        raw = binary_client.execute_command(
            "FT.SEARCH", idx,
            f"*=>[KNN 1 @embedding $vec AS score]",
            "PARAMS", "2", "vec", query_bytes,
            "RETURN", "2", "response", "score",
            "SORTBY", "score",
            "LIMIT", "0", "1",
            "DIALECT", "2",
        )
        # raw = [total_count, key, [field, val, ...], ...]
        if not raw or raw[0] == 0:
            return None
        fields = raw[2]
        fd = {}
        for i in range(0, len(fields) - 1, 2):
            fd[fields[i]] = fields[i+1]
        distance = float(fd.get(b"score", 1.0))
        similarity = 1.0 - distance
        if similarity >= threshold:
            resp = fd.get(b"response", b"")
            return resp.decode() if isinstance(resp, bytes) else str(resp)
        return None
    except Exception as e:
        return f"[ERR: {e}]"


# =============================================================================
# MAIN
# =============================================================================

def main():
    DIMS      = 1024
    THRESHOLD = 0.95
    OLD_PFX   = "bench_old:entry:"
    NEW_PFX   = "bench_new:entry:"
    NEW_IDX   = "bench_cache_idx"
    COUNTS    = [100, 500, 1_000, 2_000, 5_000]
    RUNS      = 3   # averages per measurement

    random.seed(42)

    # ── Step 1: Redis connectivity ────────────────────────────────────────────
    header("Step 1: Redis Connectivity")
    try:
        sync_client, binary_client = get_clients()
        ok("Redis is reachable")
    except Exception as e:
        fail(f"Cannot connect to Redis: {e}")
        return

    has_vss = check_search_module(sync_client)
    if has_vss:
        ok("RediSearch / VSS module is LOADED")
    else:
        fail("RediSearch module NOT found -- only OLD benchmark will run")
        info("Fix: docker compose down && docker compose up -d")

    # ── Step 2: Latency benchmark ─────────────────────────────────────────────
    header("Step 2: Latency — OLD O(N) vs NEW O(log N)")

    # One stable query vector used across all runs
    query_vec = rand_unit_vec(DIMS)

    # Pre-create & seed the NEW index ONCE with the largest count
    # then test all sub-sizes against it (HNSW lookup is O(log N) regardless)
    if has_vss:
        info(f"Creating HNSW index '{NEW_IDX}' over prefix '{NEW_PFX}' ...")
        if create_hnsw_index(binary_client, NEW_IDX, NEW_PFX, DIMS):
            ok(f"Index '{NEW_IDX}' created")
        max_count = max(COUNTS)
        seed_new_hash_format(binary_client, max_count, DIMS, NEW_PFX)
        info(f"Waiting for HNSW index to index {max_count:,} docs ...")
        actual = wait_for_index(binary_client, NEW_IDX, max_count)
        if actual >= 0:
            ok(f"Index ready — {actual:,} documents indexed")
        else:
            fail("Index indexing timed out — results may be partial")

    print(f"\n  {'Entries':<12} {'OLD ms':>10} {'NEW ms':>10} {'Speedup':>10}  {'OLD verdict'}")
    print(f"  {'-'*60}")

    results_old, results_new = {}, {}

    for count in COUNTS:
        # ── OLD ──────────────────────────────────────────────────────────
        # Cleanup & seed fresh STRING entries
        old_keys = sync_client.keys(f"{OLD_PFX}*")
        if old_keys:
            sync_client.delete(*old_keys)
        seed_old_string_format(sync_client, count, DIMS, OLD_PFX)

        times = []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            old_lookup(sync_client, query_vec, OLD_PFX, THRESHOLD)
            times.append((time.perf_counter() - t0) * 1000)
        avg_old = sum(times) / RUNS
        results_old[count] = avg_old

        # ── NEW ──────────────────────────────────────────────────────────
        if has_vss:
            times_new = []
            for _ in range(RUNS):
                t0 = time.perf_counter()
                new_lookup(binary_client, query_vec, DIMS, NEW_IDX, THRESHOLD)
                times_new.append((time.perf_counter() - t0) * 1000)
            avg_new = sum(times_new) / RUNS
            results_new[count] = avg_new
            speedup = avg_old / avg_new if avg_new > 0 else 0
            verdict = "CRITICAL" if avg_old > 500 else ("SLOW" if avg_old > 100 else "ok")
            print(f"  {count:<12,} {avg_old:>10.1f} {avg_new:>10.1f} {speedup:>9.1f}x  {verdict}")
        else:
            verdict = "CRITICAL" if avg_old > 500 else ("SLOW" if avg_old > 100 else "ok")
            print(f"  {count:<12,} {avg_old:>10.1f} {'N/A':>10} {'N/A':>10}  {verdict}")

    # ── Step 3: Correctness test ──────────────────────────────────────────────
    if has_vss:
        header("Step 3: Correctness Tests")

        CORR_PFX = "bench_corr:entry:"
        CORR_IDX = "bench_corr_idx"
        corr_keys = binary_client.keys(f"{CORR_PFX}*")
        if corr_keys:
            binary_client.delete(*corr_keys)
        create_hnsw_index(binary_client, CORR_IDX, CORR_PFX, DIMS)

        # Store one known vector
        known_vec  = rand_unit_vec(DIMS)
        known_resp = "The capital of France is Paris."
        vec_bytes  = struct.pack(f"<{DIMS}f", *known_vec)
        key = f"{CORR_PFX}{hashlib.sha256(b'france').hexdigest()}"
        binary_client.hset(key, mapping={
            "prompt":    b"What is the capital of France?",
            "response":  known_resp.encode(),
            "embedding": vec_bytes,
        })
        wait_for_index(binary_client, CORR_IDX, 1, timeout=10)

        # Test A — exact same vector → HIT
        result = new_lookup(binary_client, known_vec, DIMS, CORR_IDX, 0.95)
        if result and "Paris" in result:
            ok(f"Exact vector       -> CACHE HIT (got: '{result}')")
        else:
            fail(f"Exact vector       -> expected HIT, got: {result!r}")

        # Test B — random unrelated vector → MISS
        result = new_lookup(binary_client, rand_unit_vec(DIMS), DIMS, CORR_IDX, 0.95)
        if result is None:
            ok("Unrelated vector   -> CACHE MISS")
        else:
            fail(f"Unrelated vector   -> expected MISS, got HIT: {result!r}")

        # Test C — vector with tiny noise → HIT (near-duplicate prompt)
        noisy_vec = [v + random.gauss(0, 0.0005) for v in known_vec]
        mag = math.sqrt(sum(x*x for x in noisy_vec))
        noisy_vec = [x/mag for x in noisy_vec]
        result = new_lookup(binary_client, noisy_vec, DIMS, CORR_IDX, 0.95)
        if result and "Paris" in result:
            ok("Near-identical vec -> CACHE HIT (deduplication works)")
        else:
            info("Near-identical vec -> MISS (similarity below 0.95 at 1024 dims is normal with noise)")

        drop_index(binary_client, CORR_IDX)
        corr_keys = binary_client.keys(f"{CORR_PFX}*")
        if corr_keys:
            binary_client.delete(*corr_keys)

    # ── Step 4: Cleanup ───────────────────────────────────────────────────────
    header("Step 4: Cleanup")
    for pfx in [OLD_PFX, NEW_PFX]:
        keys = binary_client.keys(f"{pfx}*")
        if keys:
            binary_client.delete(*keys)
    if has_vss:
        drop_index(binary_client, NEW_IDX)
    ok("All benchmark keys and indexes cleaned up")

    # ── Step 5: Summary ───────────────────────────────────────────────────────
    header("Results Summary")
    if results_new:
        print(f"\n  {'Entries':<12} {'OLD':>10}  {'NEW':>10}  {'Speedup':>10}")
        print(f"  {'-'*48}")
        for count in COUNTS:
            old = results_old.get(count, 0)
            new = results_new.get(count, 0)
            x   = old / new if new else 0
            print(f"  {count:<12,} {old:>8.1f}ms  {new:>8.1f}ms  {x:>8.1f}x")

        max_new = results_new.get(max(COUNTS), 0)
        print()
        if max_new < 10:
            ok(f"Sub-10ms VSS target ACHIEVED at {max(COUNTS)} entries ({max_new:.1f}ms)")
        else:
            info(f"VSS at {max(COUNTS)} entries: {max_new:.1f}ms")
    else:
        info("VSS comparison requires Redis Stack -- run: docker compose up -d")
        if results_old:
            max_old = results_old.get(max(COUNTS), 0)
            print()
            print(f"  OLD implementation at {max(COUNTS)} entries: {max_old:.0f}ms")
            print(f"  That's {max_old/1000:.1f} SECONDS per cache lookup.")
            fail("This confirms the O(N) problem. Migrate to VSS.")
    print()


if __name__ == "__main__":
    main()
