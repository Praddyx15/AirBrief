"""Sliding-window rate limiting of ``POST`` and ``DELETE /api/*`` per client (IPv6 per /64)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from api.clients import rate_key
from api.ratelimit import RateLimiter, client_ip


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window() -> None:
    clock = _Clock()
    limiter = RateLimiter(2, window_s=3600, clock=clock)
    assert limiter.hit("1.2.3.4") == (True, 0)
    clock.now += 10
    assert limiter.hit("1.2.3.4") == (True, 0)
    allowed, retry = limiter.hit("1.2.3.4")
    assert allowed is False
    assert retry == 3600 - 10
    assert limiter.remaining("1.2.3.4") == 0
    # other clients are independent
    assert limiter.hit("5.6.7.8") == (True, 0)
    # the first hit leaves the window
    clock.now = 1000.0 + 3600 + 0.5
    assert limiter.hit("1.2.3.4") == (True, 0)
    assert limiter.hit("1.2.3.4")[0] is False


def test_zero_means_unlimited() -> None:
    limiter = RateLimiter(0)
    assert limiter.enabled is False
    assert all(limiter.hit("x")[0] for _ in range(1000))


def test_post_endpoints_are_limited(make_client: Callable[..., TestClient]) -> None:
    client = make_client(rate_limit_per_hour=2)
    for _ in range(2):
        response = client.post("/api/flightmd/fetch/", json={"url": "http://127.0.0.1/"})
        assert response.status_code == 422
    limited = client.post("/api/samples/square_1km_equator/")
    assert limited.status_code == 429
    body = limited.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["details"]["retry_after_s"] > 0
    assert int(limited.headers["retry-after"]) > 0
    # reads have their own windows, off by default in development
    assert client.get("/api/samples/").status_code == 200
    assert client.get("/health").status_code == 200


def _scope(peer: str, forwarded: str | None = None) -> dict[str, object]:
    headers = [(b"x-forwarded-for", forwarded.encode("latin-1"))] if forwarded else []
    return {"type": "http", "client": (peer, 50000), "headers": headers}


def test_client_ip_is_the_peer_by_default() -> None:
    assert client_ip(_scope("10.0.0.1", "1.2.3.4")) == "10.0.0.1"  # header ignored without hops


def test_client_ip_trusts_only_entries_the_proxies_appended() -> None:
    # The client forged "6.6.6.6"; the proxy appended the real address on the right.
    assert client_ip(_scope("10.0.0.1", "6.6.6.6, 1.2.3.4"), 1) == "1.2.3.4"
    assert client_ip(_scope("10.0.0.1", "6.6.6.6, 1.2.3.4, 10.0.0.9"), 2) == "1.2.3.4"
    assert client_ip(_scope("10.0.0.1", "1.2.3.4"), 2) == "1.2.3.4"  # fewer entries than hops
    assert client_ip(_scope("10.0.0.1"), 1) == "10.0.0.1"  # no header: the peer


def test_a_port_on_the_forwarded_address_is_dropped() -> None:
    # Azure Application Gateway and App Service append "address:port"
    assert client_ip(_scope("10.0.0.1", "203.0.113.5:54321"), 1) == "203.0.113.5"
    assert rate_key(client_ip(_scope("10.0.0.1", "[2001:db8::1]:443"), 1)) == "2001:db8::/64"
    assert client_ip(_scope("10.0.0.1", "2001:db8::1"), 1) == "2001:db8::1"


# GET /api/context/ and /api/places/ (security review: unauthenticated reads that can return
# megabytes of pack data per request, so a public instance limits them per IP too)

_VIEW = {"bbox": "77.4,12.8,77.8,13.2", "layers": "aerodromes"}


def test_context_and_places_reads_are_limited_when_configured(make_client: Callable[..., TestClient]) -> None:
    client = make_client(context_rate_limit_per_hour=2)
    assert client.get("/api/context/", params=_VIEW).status_code == 200
    assert client.get("/api/places/", params={"q": "VOBL"}).status_code == 200
    for path, params in (("/api/context/", _VIEW), ("/api/places/", {"q": "VOBL"})):
        limited = client.get(path, params=params)
        assert limited.status_code == 429
        body = limited.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"]["limit_per_hour"] == 2
        assert int(limited.headers["retry-after"]) > 0
    # Other reads and the POST limit (off here) are not affected.
    assert client.get("/api/samples/").status_code == 200
    assert client.post("/api/geometry/parse/", json={"text": "12.97, 77.59"}).status_code == 200


def test_post_limit_does_not_count_context_reads(make_client: Callable[..., TestClient]) -> None:
    client = make_client(rate_limit_per_hour=1, context_rate_limit_per_hour=0)
    for _ in range(5):
        assert client.get("/api/places/", params={"q": "VOBL"}).status_code == 200
    assert client.post("/api/geometry/parse/", json={"text": "12.97, 77.59"}).status_code == 200
    assert client.post("/api/geometry/parse/", json={"text": "12.97, 77.59"}).status_code == 429


def test_context_limit_defaults_off_in_development_and_on_in_production() -> None:
    from api.config import PRODUCTION_CONTEXT_RATE_LIMIT_PER_HOUR, Settings

    assert Settings(_env_file=None).context_rate_limit == 0  # type: ignore[call-arg]
    production = {"app_env": "production", "cors_origins": "https://airbrief.example"}
    assert Settings(_env_file=None, **production).context_rate_limit == PRODUCTION_CONTEXT_RATE_LIMIT_PER_HOUR  # type: ignore[arg-type]
    assert PRODUCTION_CONTEXT_RATE_LIMIT_PER_HOUR > 0
    assert Settings(_env_file=None, context_rate_limit_per_hour=0, **production).context_rate_limit == 0  # type: ignore[arg-type]
    assert Settings(_env_file=None, context_rate_limit_per_hour="").context_rate_limit == 0  # type: ignore[arg-type]


def test_other_reads_have_their_own_budget(make_client: Callable[..., TestClient]) -> None:
    client = make_client(read_rate_limit_per_hour=2, context_rate_limit_per_hour=0)
    assert client.get("/api/samples/").status_code == 200
    assert client.get("/api/meta/").status_code == 200
    limited = client.get("/api/files/")
    assert limited.status_code == 429 and limited.json()["error"]["details"]["limit_per_hour"] == 2
    # context reads keep their own window, health is outside /api/, and writes are not counted
    assert client.get("/api/places/", params={"q": "VOBL"}).status_code == 200
    assert client.get("/health").status_code == 200
    assert client.post("/api/geometry/parse/", json={"text": "12.97, 77.59"}).status_code == 200


def test_read_limit_defaults_off_in_development_and_on_in_production() -> None:
    from api.config import PRODUCTION_READ_RATE_LIMIT_PER_HOUR, Settings

    assert Settings(_env_file=None).read_rate_limit == 0  # type: ignore[call-arg]
    production = {"app_env": "production", "cors_origins": "https://airbrief.example"}
    assert Settings(_env_file=None, **production).read_rate_limit == PRODUCTION_READ_RATE_LIMIT_PER_HOUR  # type: ignore[arg-type]
    assert Settings(_env_file=None, read_rate_limit_per_hour="", **production).read_rate_limit == PRODUCTION_READ_RATE_LIMIT_PER_HOUR  # type: ignore[arg-type]
    assert Settings(_env_file=None, read_rate_limit_per_hour=0, **production).read_rate_limit == 0  # type: ignore[arg-type]


def test_rate_key_groups_ipv6_by_64_and_unwraps_mapped_ipv4() -> None:
    assert rate_key("2a00:1450:4001:80b::1") == "2a00:1450:4001:80b::/64"
    assert rate_key("2a00:1450:4001:80b:ffff:ffff:ffff:ffff") == "2a00:1450:4001:80b::/64"
    assert rate_key("2a00:1450:4001:80c::1") == "2a00:1450:4001:80c::/64"
    assert rate_key("::ffff:8.8.8.8") == "8.8.8.8"
    assert rate_key("8.8.8.8") == "8.8.8.8"
    assert rate_key("8.8.8.9") == "8.8.8.9"
    assert rate_key("testclient") == "testclient"


def test_addresses_in_one_ipv6_64_share_a_budget(make_client: Callable[..., TestClient]) -> None:
    client = make_client(app_env="production", rate_limit_per_hour=1, trusted_proxy_hops=1)
    body = {"text": "12.97, 77.59"}

    def post(address: str) -> int:
        return client.post("/api/geometry/parse/", json=body, headers={"X-Forwarded-For": address}).status_code

    assert post("2a00:1450:4001:80b::1") == 200
    assert post("2a00:1450:4001:80b::2") == 429
    assert post("2a00:1450:4001:80c::1") == 200
    assert post("8.8.8.8") == 200
    assert post("::ffff:8.8.8.8") == 429


def test_key_table_evicts_least_recently_used_keys_even_when_fresh() -> None:
    clock = _Clock()
    limiter = RateLimiter(1, window_s=3600, clock=clock, max_keys=2)
    assert limiter.hit("a")[0] is True
    assert limiter.hit("b")[0] is True
    assert limiter.hit("a")[0] is False  # "a" is now the most recently used key
    assert limiter.hit("c")[0] is True  # table full: "b" goes, although its window is open
    assert limiter.hit("a")[0] is False
    assert limiter.hit("b")[0] is True  # forgotten, so it starts again
    assert limiter.remaining("c") == 1  # and "b" pushed out "c"
    assert limiter.remaining("a") == 0


def test_key_table_stays_bounded() -> None:
    limiter = RateLimiter(5, max_keys=100)
    for n in range(10_000):
        limiter.hit(f"10.0.{n // 256}.{n % 256}")
    assert len(limiter._hits) == 100


def test_delete_counts_against_the_post_budget(make_client: Callable[..., TestClient]) -> None:
    client = make_client(rate_limit_per_hour=2)
    created = client.post("/api/samples/square_1km_equator/")
    assert created.status_code == 201
    assert client.delete(f"/api/files/{created.json()['id']}/").status_code == 204
    limited = client.delete(f"/api/files/{created.json()['id']}/")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert client.post("/api/geometry/parse/", json={"text": "12.97, 77.59"}).status_code == 429
