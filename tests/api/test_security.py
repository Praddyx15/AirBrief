"""SSRF guard for server-side fetches (the DNS resolver is monkeypatched; no network)."""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import ValidationError

import api.security as security
from airbrief_core.errors import AirBriefError
from api.config import Settings


def _resolver(mapping: dict[str, list[str]]) -> Callable[[str, int], list[str]]:
    def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError(f"unknown host {host}")
        return mapping[host]

    return resolve


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security,
        "resolve_host",
        _resolver(
            {
                "reports.example.org": ["93.184.216.34"],
                "localhost": ["127.0.0.1", "::1"],
                "intranet.example.org": ["10.0.0.5"],
                "metadata.example.org": ["169.254.169.254"],
                "mixed.example.org": ["93.184.216.34", "192.168.1.10"],
                "cgnat.example.org": ["100.64.0.1"],
                "mapped.example.org": ["::ffff:127.0.0.1"],
                "v6.example.org": ["2606:4700::6810:84e5"],
                "flightmd.local": ["192.168.1.20"],
            }
        ),
    )


def _code(url: str, **kwargs: object) -> str:
    with pytest.raises(AirBriefError) as info:
        security.check_url(url, **kwargs)  # type: ignore[arg-type]
    return info.value.code


@pytest.mark.usefixtures("fake_dns")
def test_public_https_is_allowed() -> None:
    checked = security.check_url("https://reports.example.org/report/abc?x=1")
    assert checked.host == "reports.example.org"
    assert checked.port == 443
    assert checked.target == "/report/abc?x=1"
    assert checked.addresses == ["93.184.216.34"]
    assert security.check_url("https://v6.example.org/r").addresses == ["2606:4700::6810:84e5"]


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize(
    "url",
    [
        "http://reports.example.org/report/abc",  # plain http
        "ftp://reports.example.org/report.json",  # other scheme
        "file:///etc/passwd",
        "https://localhost/report/1",  # loopback via DNS
        "https://127.0.0.1/report/1",  # loopback literal
        "https://[::1]/report/1",
        "https://intranet.example.org/report/1",  # private
        "https://metadata.example.org/latest/meta-data/",  # link-local
        "https://169.254.169.254/latest/meta-data/",
        "https://mixed.example.org/report/1",  # one private address is enough to refuse
        "https://cgnat.example.org/report/1",  # shared address space
        "https://mapped.example.org/report/1",  # IPv4-mapped loopback
        "https://0.0.0.0/report/1",
        "https://user:secret@reports.example.org/report/1",  # credentials
        "https:///no-host",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    assert _code(url) == "url_not_allowed"


@pytest.mark.usefixtures("fake_dns")
def test_unresolvable_host_is_fetch_failed() -> None:
    assert _code("https://does-not-exist.example.org/x") == "fetch_failed"


@pytest.mark.usefixtures("fake_dns")
def test_configured_flightmd_host_is_trusted() -> None:
    trusted = frozenset({("flightmd.local", 8000)})
    checked = security.check_url("http://flightmd.local:8000/report/7", trusted_hosts=trusted)
    assert checked.trusted is True
    assert checked.addresses == ["192.168.1.20"]
    # another port on the same host is not trusted
    assert _code("http://flightmd.local:9000/report/7", trusted_hosts=trusted) == "url_not_allowed"


def test_resolve_flightmd_url() -> None:
    api = "https://api.flightmd.example.org"
    share = "https://app.flightmd.example.org/report/abc-123"
    assert security.resolve_flightmd_url(share, api) == "https://api.flightmd.example.org/report/abc-123"
    assert security.resolve_flightmd_url(share + "/", api + "/") == "https://api.flightmd.example.org/report/abc-123"
    # already on the API host, other paths, or no API configured: unchanged
    same = "https://api.flightmd.example.org/report/abc-123"
    assert security.resolve_flightmd_url(same, api) == same
    other = "https://files.example.org/flight.json"
    assert security.resolve_flightmd_url(other, api) == other
    assert security.resolve_flightmd_url(share, None) == share


def test_unwrap_report() -> None:
    inner = {"schema_version": "1.5", "metadata": {}}
    assert security.unwrap_report({"report": inner}) is inner
    assert security.unwrap_report(inner) is inner
    assert security.unwrap_report([1, 2]) == [1, 2]


def test_is_public_address() -> None:
    import ipaddress

    assert security.is_public_address(ipaddress.ip_address("8.8.8.8"))
    for value in ("10.1.2.3", "172.16.0.1", "192.168.0.1", "127.0.0.1", "169.254.1.1", "224.0.0.1",
                  "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "100.64.0.1"):
        assert not security.is_public_address(ipaddress.ip_address(value)), value


def test_fetch_endpoint_refuses_private_urls(api_client: TestClient) -> None:
    for url in ("http://127.0.0.1/report/1", "https://127.0.0.1/report/1", "https://10.0.0.1/x"):
        response = api_client.post("/api/flightmd/fetch/", json={"url": url})
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "url_not_allowed"


def test_fetch_endpoint_returns_unwrapped_report(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    report = {"schema_version": "1.5", "metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.91, 77.51, 950.0]]}}

    def fake_fetch_json(url: str, **kwargs: object) -> object:
        calls.append(url)
        return {"report": report}

    monkeypatch.setattr(security, "fetch_json", fake_fetch_json)
    response = api_client.post("/api/flightmd/fetch/", json={"url": "https://reports.example.org/r/1"})
    assert response.status_code == 200
    assert response.json() == report
    assert calls == ["https://reports.example.org/r/1"]


def test_fetch_endpoint_refuses_json_that_is_not_a_report(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "fetch_json", lambda url, **kwargs: {"report": {"metadata": {"gps_path": []}}})
    response = api_client.post("/api/flightmd/fetch/", json={"url": "https://reports.example.org/r/1"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "report_invalid"


def test_fetch_endpoint_validates_body(api_client: TestClient) -> None:
    response = api_client.post("/api/flightmd/fetch/", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"


# what clients are told (VULN-02)


def _refusal(url: str, **kwargs: object) -> AirBriefError:
    with pytest.raises(AirBriefError) as info:
        security.check_url(url, **kwargs)  # type: ignore[arg-type]
    return info.value


def _fetch_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "airbrief.api.fetch"]


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("", "length"),
        ("https://reports.example.org/a b", "characters"),
        ("https://reports.example.org/a\x00b", "characters"),
        ("https://reports.example.org:99999/x", "syntax"),
        ("ftp://reports.example.org/x", "scheme"),
        ("http://reports.example.org/x", "scheme"),
        ("https://user:pw@reports.example.org/x", "credentials"),
        ("https:///x", "host"),
        ("https://reports.example.org:8443/x", "port"),
        ("https://reports.example.org:22/x", "port"),
        ("https://intranet.example.org/report/1", "private_address"),
        ("https://metadata.example.org/latest/meta-data/", "private_address"),
        ("https://[::ffff:10.0.0.1]/report/1", "private_address"),
    ],
)
def test_refusals_name_only_the_rule(url: str, rule: str) -> None:
    error = _refusal(url)
    assert error.code == "url_not_allowed"
    assert error.details == {"rule": rule}
    for secret in ("10.0.0", "169.254", "intranet", "metadata", "reports.example.org", "pw"):
        assert secret not in error.message


@pytest.mark.usefixtures("fake_dns")
def test_resolver_failures_name_only_the_stage() -> None:
    error = _refusal("https://does-not-exist.example.org/x")
    assert error.code == "fetch_failed"
    assert error.details == {"stage": "resolve"}
    assert "does-not-exist" not in error.message
    assert "unknown host" not in error.message


@pytest.mark.usefixtures("fake_dns")
def test_full_detail_goes_to_the_server_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="airbrief.api.fetch")
    _refusal("https://intranet.example.org/report/1?token=abc", context="POST /api/flightmd/fetch/")
    _refusal("https://does-not-exist.example.org/x", context="flight_audit analysis")
    refused, failed = _fetch_log(caplog)
    assert "POST /api/flightmd/fetch/" in refused
    assert "rule=private_address" in refused
    assert "host=intranet.example.org" in refused and "address=10.0.0.5" in refused
    assert "token=abc" not in refused
    assert "flight_audit analysis" in failed
    assert "stage=resolve" in failed and "unknown host does-not-exist.example.org" in failed


def _answers(monkeypatch: pytest.MonkeyPatch, *responses: security._Response) -> list[str]:
    """``_http_get`` answers from a script (the last answer repeats); returns the URLs asked for."""
    seen: list[str] = []
    queue = list(responses)

    def fake_get(checked: security.CheckedUrl, **kwargs: object) -> security._Response:
        seen.append(checked.url)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(security, "_http_get", fake_get)
    return seen


def _fetch_error(url: str = "https://reports.example.org/report/1") -> AirBriefError:
    with pytest.raises(AirBriefError) as info:
        security.fetch_json(url, timeout_s=5, max_bytes=1024 * 1024)
    return info.value


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize(
    ("response", "stage"),
    [
        (security._Response(500, {}, b'{"detail": "db password wrong"}'), "http_status"),
        (security._Response(404, {}, b""), "http_status"),
        (security._Response(200, {}, b"<html>internal admin</html>"), "not_json"),
        (security._Response(200, {}, b"[" * 100_000), "not_json"),
        (security._Response(302, {}, b""), "redirects"),
        (security._Response(302, {"location": "/again"}, b""), "redirects"),
    ],
)
def test_fetch_failures_name_only_the_stage(
    monkeypatch: pytest.MonkeyPatch, response: security._Response, stage: str
) -> None:
    _answers(monkeypatch, response)
    error = _fetch_error()
    assert error.code == "fetch_failed"
    assert error.details == {"stage": stage}
    for secret in ("500", "404", "302", "password", "admin", "reports.example.org"):
        assert secret not in error.message


@pytest.mark.usefixtures("fake_dns")
def test_redirect_to_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _answers(
        monkeypatch,
        security._Response(302, {"location": "https://intranet.example.org/secret"}, b""),
    )
    error = _fetch_error()
    assert error.details == {"rule": "private_address"}
    assert seen == ["https://reports.example.org/report/1"]


class _FakeResponse:
    def __init__(self, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status = 200
        self._headers = headers
        self._body = body

    def getheaders(self) -> list[tuple[str, str]]:
        return self._headers

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


def _connection(
    fail: Exception | None = None, headers: list[tuple[str, str]] | None = None, body: bytes = b"{}"
) -> type:
    class FakeConnection:
        def __init__(self, host: str, port: int, *, address: str, timeout: float) -> None:
            pass

        def request(self, method: str, target: str, headers: dict[str, str]) -> None:
            if fail is not None:
                raise fail

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse(headers or [], body)

        def close(self) -> None:
            pass

    return FakeConnection


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize(
    ("connection", "stage"),
    [
        (_connection(fail=ConnectionRefusedError("[Errno 111] refused by 93.184.216.34:443")), "connect"),
        (_connection(fail=TimeoutError("timed out after 15 s")), "connect"),
        (_connection(headers=[("Content-Length", str(50 * 1024 * 1024))]), "too_large"),
        (_connection(body=b"x" * (2 * 1024 * 1024)), "too_large"),
    ],
)
def test_download_failures_name_only_the_stage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, connection: type, stage: str
) -> None:
    caplog.set_level(logging.INFO, logger="airbrief.api.fetch")
    monkeypatch.setattr(security, "_PinnedHTTPSConnection", connection)
    error = _fetch_error()
    assert error.code == "fetch_failed"
    assert error.details == {"stage": stage}
    assert "Errno" not in error.message and "93.184" not in error.message
    assert f"stage={stage}" in _fetch_log(caplog)[-1]


def test_fetch_endpoint_errors_carry_only_the_rule(api_client: TestClient) -> None:
    response = api_client.post("/api/flightmd/fetch/", json={"url": "https://10.0.0.1/report/1"})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"rule": "private_address"}
    assert "10.0.0.1" not in response.text


# NAT64 and IPv4-compatible IPv6 (VERIFY-01)


@pytest.mark.parametrize(
    "value",
    [
        "64:ff9b::7f00:1",  # NAT64 well-known prefix -> 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # -> 169.254.169.254
        "64:ff9b::808:808",  # even a public IPv4 behind the gateway
        "64:ff9b:1::a00:1",  # local-use NAT64 prefix
        "64:ff9b:1::808:808",
        "::127.0.0.1",  # IPv4-compatible
        "::a9fe:a9fe",
        "::8.8.8.8",
        "::ffff:169.254.169.254",  # IPv4-mapped
        "2002:a9fe:a9fe::1",  # 6to4
    ],
)
def test_nat64_and_embedded_ipv4_forms_are_not_public(value: str) -> None:
    assert not security.is_public_address(ipaddress.ip_address(value))


def test_refusal_does_not_depend_on_the_stdlib_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    # Today's ipaddress calls all of ::/8 reserved, which happens to cover these; the guard must not need that.
    monkeypatch.setattr(ipaddress.IPv6Address, "is_reserved", property(lambda self: False))
    monkeypatch.setattr(ipaddress.IPv6Address, "is_private", property(lambda self: False))
    monkeypatch.setattr(ipaddress.IPv6Address, "is_global", property(lambda self: True))
    for value in ("64:ff9b::808:808", "64:ff9b:1::808:808", "::8.8.8.8", "::127.0.0.1"):
        assert not security.is_public_address(ipaddress.ip_address(value)), value
    assert security.is_public_address(ipaddress.ip_address("2606:4700::6810:84e5"))


def test_embedded_ipv4_is_extracted() -> None:
    def v6(value: str) -> ipaddress.IPv6Address:
        return ipaddress.IPv6Address(value)

    assert security.embedded_ipv4(v6("64:ff9b::a9fe:a9fe")) == ipaddress.IPv4Address("169.254.169.254")
    assert security.embedded_ipv4(v6("::10.0.0.1")) == ipaddress.IPv4Address("10.0.0.1")
    assert security.embedded_ipv4(v6("::ffff:127.0.0.1")) == ipaddress.IPv4Address("127.0.0.1")
    assert security.embedded_ipv4(v6("2002:a00:1::1")) == ipaddress.IPv4Address("10.0.0.1")
    assert security.embedded_ipv4(v6("2606:4700::6810:84e5")) is None
    assert security.is_public_address(v6("2606:4700::6810:84e5"))


def test_nat64_dns_answer_is_refused(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="airbrief.api.fetch")
    monkeypatch.setattr(security, "resolve_host", _resolver({"nat64.example.org": ["64:ff9b::a9fe:a9fe"]}))
    assert _refusal("https://nat64.example.org/report/1").details == {"rule": "private_address"}
    assert "embeds=169.254.169.254" in _fetch_log(caplog)[-1]


# the configured FlightMD host (VERIFY-02)

_TRUSTED = frozenset({("flightmd.local", 8000)})


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize("path", ["/api/report/abc-123", "/api/report/A_b-9/", "/api/report/" + "a" * 128])
def test_trusted_host_serves_report_paths(path: str) -> None:
    checked = security.check_url(f"http://flightmd.local:8000{path}", trusted_hosts=_TRUSTED, trusted_prefix="/api")
    assert checked.trusted is True
    assert checked.target == path


@pytest.mark.usefixtures("fake_dns")
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/admin",
        "/api/admin",
        "/report/abc",
        "/api/report/abc/raw",
        "/api/report/a%2Fb",
        "/api/report/../admin",
        "/api/report/" + "a" * 129,
        "/api/report/abc?debug=1",
    ],
)
def test_trusted_host_refuses_other_paths(path: str) -> None:
    error = _refusal(f"http://flightmd.local:8000{path}", trusted_hosts=_TRUSTED, trusted_prefix="/api")
    assert error.details == {"rule": "path"}


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@flightmd.example.org/api",
        "https://@flightmd.example.org",
        "https://flightmd.example.org/api?token=x",
        "https://flightmd.example.org/api?",
        "https://flightmd.example.org/api#x",
    ],
)
def test_flightmd_api_url_must_be_a_plain_base_url(url: str) -> None:
    with pytest.raises(ValidationError, match=r"without a user name, password, \?query or #fragment"):
        Settings(_env_file=None, flightmd_api_url=url)  # type: ignore[call-arg]


def test_share_links_go_to_the_report_path_of_the_configured_api(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, flightmd_api_url="http://flightmd.local:8000/api/")  # type: ignore[call-arg]
    calls: list[tuple[str, dict[str, object]]] = []
    report = {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.91, 77.51, 950.0]]}}

    def fake_fetch_json(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return report

    monkeypatch.setattr(security, "fetch_json", fake_fetch_json)
    security.fetch_flightmd_report("https://app.flightmd.example.org/report/abc-1", settings, context="test")
    url, kwargs = calls[0]
    assert url == "http://flightmd.local:8000/api/report/abc-1"
    assert kwargs["trusted_hosts"] == _TRUSTED
    assert kwargs["trusted_prefix"] == "/api"
    assert kwargs["context"] == "test"


def test_report_errors_do_not_name_the_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, flightmd_api_url="http://flightmd.local:8000/internal-api")  # type: ignore[call-arg]
    monkeypatch.setattr(security, "fetch_json", lambda url, **kwargs: [1, 2])
    with pytest.raises(AirBriefError) as info:
        security.fetch_flightmd_report("https://app.example.org/report/abc", settings)
    assert info.value.code == "report_invalid"
    assert info.value.details == {}
    assert "internal-api" not in info.value.message


def test_fetched_report_nested_too_deeply_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    deep: list = []
    for _ in range(200):
        deep = [deep]
    report = {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.91, 77.51, 950.0]]}, "x": deep}
    monkeypatch.setattr(security, "fetch_json", lambda url, **kwargs: report)
    with pytest.raises(AirBriefError) as info:
        security.fetch_flightmd_report("https://reports.example.org/report/1", Settings(_env_file=None))  # type: ignore[call-arg]
    assert info.value.code == "too_complex"


# "Open in FlightMD" links (WEAK-08)


@pytest.fixture
def report(sample_bytes: Callable[[str], bytes]) -> dict:
    return json.loads(sample_bytes("flightmd_report_v15"))


def _audit(client: TestClient, report: dict, **params: object) -> Response:
    body = {"kind": "flight_audit", "params": {"report": report, "use_terrain": False, **params}}
    return client.post("/api/analyses/", json=body)


@pytest.mark.parametrize(
    ("link", "status"),
    [
        ("https://flightmd.example.org/report/1", 202),
        ("http://localhost:5173/report/1", 202),
        ("http://127.0.0.1:5173/report/1", 202),
        ("http://[::1]:5173/report/1", 202),
        ("http://flightmd.example.org/report/1", 422),
        ("http://192.168.1.20/report/1", 422),
        ("javascript:alert(1)", 422),
    ],
)
def test_flightmd_url_in_development(api_client: TestClient, report: dict, link: str, status: int) -> None:
    response = _audit(api_client, report, flightmd_url=link)
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["error"]["code"] == "invalid_params"
        assert response.json()["error"]["details"] == {"field": "flightmd_url"}


def test_flightmd_url_must_be_https_in_production(make_client: Callable[..., TestClient], report: dict) -> None:
    client = make_client(app_env="production")
    refused = _audit(client, report, flightmd_url="http://localhost:5173/report/1")
    assert refused.status_code == 422
    assert refused.json()["error"]["message"] == "flightmd_url must be an https link."
    assert _audit(client, report, flightmd_url="https://flightmd.example.org/report/1").status_code == 202


def test_report_url_becomes_the_link_only_when_it_may_be_one(
    make_client: Callable[..., TestClient], report: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "fetch_flightmd_report", lambda url, settings, **kwargs: report)
    client = make_client(app_env="production", flightmd_api_url="http://flightmd.internal:8000/api")

    def run(report_url: str) -> dict:
        body = {"kind": "flight_audit", "params": {"report_url": report_url, "use_terrain": False}}
        response = client.post("/api/analyses/", json=body)
        assert response.status_code == 202, response.text
        return response.json()["params"]

    assert "flightmd_url" not in run("http://flightmd.internal:8000/api/report/abc")
    share = "https://app.flightmd.example.org/report/abc"
    assert run(share)["flightmd_url"] == share
