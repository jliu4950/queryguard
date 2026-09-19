from app.service import TTLMemoryCache


def test_memory_cache_expires() -> None:
    cache = TTLMemoryCache(ttl_seconds=0)
    cache.set("key", object())
    assert cache.get("key") is None
