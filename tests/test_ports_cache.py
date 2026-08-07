from assessor.ports.cache import CachePort, NullCache


def test_null_cache_always_misses():
    c = NullCache()
    c.put("m", "h", {"a": 1})
    assert c.get("m", "h") is None


def test_null_cache_satisfies_the_protocol():
    assert isinstance(NullCache(), CachePort)


class _Dict:
    """A minimal in-memory implementation, proving the port is implementable
    without inheriting anything."""
    def __init__(self):
        self.store = {}

    def get(self, model, prompt_sha256):
        return self.store.get((model, prompt_sha256))

    def put(self, model, prompt_sha256, response):
        self.store[(model, prompt_sha256)] = response


def test_a_third_party_implementation_satisfies_the_protocol():
    assert isinstance(_Dict(), CachePort)


def test_round_trip_through_a_real_implementation():
    c = _Dict()
    assert c.get("m", "h") is None
    c.put("m", "h", {"a": 1})
    assert c.get("m", "h") == {"a": 1}
