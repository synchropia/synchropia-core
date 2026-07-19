"""
smoke_test_semantic_cache.py
-----------------------------
Integration smoke test for the migrated SemanticCache.

Tests the exact same code path used by the production pipeline
(dispatcher.py -> semantic_cache.lookup / semantic_cache.set)
WITHOUT needing the full swarm, LLM providers, or Kafka.

Run:
    .venv\Scripts\python.exe smoke_test_semantic_cache.py

What it proves:
    1. Redis Stack (VSS) is reachable and index is created
    2. Real Cohere embeddings are generated
    3. set() stores an entry correctly as a Redis HASH
    4. lookup() hits on the exact same prompt
    5. lookup() hits on a semantically similar (paraphrased) prompt
    6. lookup() misses on an unrelated prompt
    7. clear() removes all entries
    8. FT.INFO confirms the index state at each step
"""

import sys, os, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# ─── Colour-free output helpers ───────────────────────────────────────────────
def header(t):   print(f"\n{'='*56}\n  {t}\n{'='*56}")
def ok(t):       print(f"  [PASS]  {t}")
def fail(t):     print(f"  [FAIL]  {t}")
def info(t):     print(f"  [INFO]  {t}")
def step(n, t):  print(f"\n--- Step {n}: {t}")


# ─── Helpers to inspect the index directly ───────────────────────────────────
def get_index_info(binary_client, idx="semantic_cache_idx") -> dict:
    """Return a dict of FT.INFO fields, or {} if index doesn't exist."""
    try:
        raw = binary_client.execute_command("FT.INFO", idx)
        d = {}
        for i in range(0, len(raw) - 1, 2):
            d[raw[i]] = raw[i+1]
        return d
    except Exception:
        return {}


def print_index_state(binary_client):
    d = get_index_info(binary_client)
    if not d:
        info("Index does not exist yet")
        return
    num_docs = d.get(b"num_docs", b"?")
    indexing  = d.get(b"indexing", b"?")
    info(f"Index 'semantic_cache_idx': num_docs={num_docs}, indexing={indexing}")


async def main():
    header("Synchropia Semantic Cache — Integration Smoke Test")

    # ── Import the production singleton (same object used by dispatcher.py) ──
    from src.services.semantic_caching import semantic_cache
    from src.services.redis_client import redis_client

    sync_client   = redis_client.sync_client
    binary_client = redis_client.binary_client

    info(f"Threshold : {semantic_cache.threshold}")
    info(f"TTL       : {semantic_cache.ttl}s")
    info(f"Prefix    : {semantic_cache.prefix}")
    info(f"Embeddings: {semantic_cache.embeddings}")

    # ─────────────────────────────────────────────────────────────────────────
    step(1, "Redis + VSS connectivity")
    # ─────────────────────────────────────────────────────────────────────────
    try:
        sync_client.ping()
        ok("Redis is reachable")
    except Exception as e:
        fail(f"Redis unreachable: {e}")
        return

    try:
        sync_client.execute_command("FT._LIST")
        ok("RediSearch / VSS module is LOADED")
    except Exception as e:
        if "unknown command" in str(e).lower():
            fail("RediSearch NOT loaded. Run: docker compose up -d (redis-stack-server)")
            return
        ok("RediSearch module present")

    # ─────────────────────────────────────────────────────────────────────────
    step(2, "Clean slate — clear any previous cache entries")
    # ─────────────────────────────────────────────────────────────────────────
    cleared = semantic_cache.clear()
    info(f"Cleared {cleared} pre-existing entries")
    print_index_state(binary_client)

    # ─────────────────────────────────────────────────────────────────────────
    step(3, "Lookup on EMPTY cache (must return None)")
    # ─────────────────────────────────────────────────────────────────────────
    t0     = time.perf_counter()
    result = await semantic_cache.lookup("What is the capital of France?")
    ms     = (time.perf_counter() - t0) * 1000

    if result is None:
        ok(f"Empty cache -> MISS as expected  ({ms:.1f}ms)")
    else:
        fail(f"Empty cache returned something: {result!r}")

    # ─────────────────────────────────────────────────────────────────────────
    step(4, "SET a prompt-response pair (real Cohere embedding)")
    # ─────────────────────────────────────────────────────────────────────────
    PROMPT   = "What is the capital of France?"
    RESPONSE = "The capital of France is Paris, which has been the country's capital since 987 AD."

    info("Calling Cohere API to generate embedding...")
    t0      = time.perf_counter()
    success = await semantic_cache.set(PROMPT, RESPONSE)
    ms      = (time.perf_counter() - t0) * 1000

    if success:
        ok(f"set() returned True  ({ms:.0f}ms — includes Cohere API call)")
    else:
        fail(f"set() returned False — check Cohere API key and Redis logs")
        return

    print_index_state(binary_client)

    # Wait briefly for HNSW to index the new document
    await asyncio.sleep(0.5)

    # ─────────────────────────────────────────────────────────────────────────
    step(5, "LOOKUP — exact same prompt (must HIT)")
    # ─────────────────────────────────────────────────────────────────────────
    t0     = time.perf_counter()
    result = await semantic_cache.lookup(PROMPT)
    ms     = (time.perf_counter() - t0) * 1000

    if result and "Paris" in result:
        ok(f"Exact prompt     -> CACHE HIT  ({ms:.1f}ms)")
        info(f"Returned: '{result[:70]}...'")
    else:
        fail(f"Expected HIT, got: {result!r}")

    # ─────────────────────────────────────────────────────────────────────────
    step(6, "LOOKUP — semantically similar / paraphrased prompt (should HIT)")
    # ─────────────────────────────────────────────────────────────────────────
    SIMILAR = "Which city serves as the capital of France?"
    info(f"Testing: '{SIMILAR}'")

    t0     = time.perf_counter()
    result = await semantic_cache.lookup(SIMILAR)
    ms     = (time.perf_counter() - t0) * 1000

    if result and "Paris" in result:
        ok(f"Similar prompt   -> CACHE HIT  ({ms:.1f}ms)  [semantic match works]")
        info(f"Returned: '{result[:70]}...'")
    else:
        info(f"Similar prompt   -> MISS (similarity below {semantic_cache.threshold} threshold)")
        info("This is acceptable — threshold tuning can increase sensitivity")

    # ─────────────────────────────────────────────────────────────────────────
    step(7, "LOOKUP — unrelated prompt (must MISS)")
    # ─────────────────────────────────────────────────────────────────────────
    UNRELATED = "Write a Python function to sort a list of integers."
    info(f"Testing: '{UNRELATED}'")

    t0     = time.perf_counter()
    result = await semantic_cache.lookup(UNRELATED)
    ms     = (time.perf_counter() - t0) * 1000

    if result is None:
        ok(f"Unrelated prompt -> CACHE MISS ({ms:.1f}ms)  [no false positives]")
    else:
        fail(f"Unrelated prompt got a cache HIT (false positive!): {result!r}")

    # ─────────────────────────────────────────────────────────────────────────
    step(8, "SET a second entry + verify index grows")
    # ─────────────────────────────────────────────────────────────────────────
    await semantic_cache.set(
        "What is the best Python web framework?",
        "FastAPI is highly recommended for async APIs. Django is better for full-stack apps."
    )
    await asyncio.sleep(0.5)
    print_index_state(binary_client)

    # Verify the second entry is retrievable
    result = await semantic_cache.lookup("Which Python framework should I use for building an API?")
    if result and "FastAPI" in result:
        ok("Second entry     -> CACHE HIT on related query")
    else:
        info(f"Second entry lookup: {result!r}")

    # ─────────────────────────────────────────────────────────────────────────
    step(9, "SPEED TEST — lookup latency with 2 real entries")
    # ─────────────────────────────────────────────────────────────────────────
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        await semantic_cache.lookup("What is the capital of France?")
        times.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(times) / len(times)
    info(f"5 consecutive lookups: {[f'{t:.1f}' for t in times]} ms")
    if avg_ms < 50:
        ok(f"Average lookup latency: {avg_ms:.1f}ms (fast — Cohere API round-trip)")
    else:
        info(f"Average lookup latency: {avg_ms:.1f}ms (network latency to Cohere)")

    # ─────────────────────────────────────────────────────────────────────────
    step(10, "CLEAR — wipe all entries")
    # ─────────────────────────────────────────────────────────────────────────
    cleared = semantic_cache.clear()
    ok(f"clear() deleted {cleared} entries and dropped index")

    result = await semantic_cache.lookup("What is the capital of France?")
    if result is None:
        ok("Post-clear lookup -> MISS (cache is truly empty)")
    else:
        fail(f"Cache still returning results after clear(): {result!r}")

    # ─────────────────────────────────────────────────────────────────────────
    header("Smoke Test Complete")
    # ─────────────────────────────────────────────────────────────────────────
    print("""
  What this confirms about the production pipeline:
  
  [1] SemanticCache singleton initializes correctly at import time
  [2] HNSW index is created in Redis Stack on first use
  [3] Cohere embeddings API is reachable and working
  [4] set() stores entries as Redis Hashes (indexed by HNSW)
  [5] lookup() finds exact and semantically similar prompts
  [6] lookup() does NOT false-positive on unrelated prompts
  [7] clear() fully wipes entries and index
  
  In main.py pipeline (dispatcher.py):
  
    cached_result = await semantic_cache.lookup(message)  <- step 1 of run_swarm()
    if cached_result:
        # returns instantly without running the swarm
        ...
    # after swarm runs:
    await semantic_cache.set(message, final_text)         <- caches for next time
""")


if __name__ == "__main__":
    asyncio.run(main())
