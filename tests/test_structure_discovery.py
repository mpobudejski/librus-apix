from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Comment

from librus_apix.exceptions import (
    AccessDeniedError,
    AuthorizationError,
    MaintananceError,
    TransportError,
)
from tools import librus_structure_discovery as discovery
from tools.librus_structure_discovery import check_tracked_paths, sanitize_html


def test_sanitizer_removes_private_values_and_unsafe_markup():
    raw = """
    <html><head><style>.secret { color: red; }</style></head><body>
    <!-- Jan Kowalski -->
    <table class="decorated stretch student-987 Jan-Kowalski" data-owner="Jan Kowalski">
      <tr class="line0"><td title="Jan Kowalski">Jan Kowalski</td>
      <td><a href="/wiadomosci/1/5/987">Sekretny temat</a></td>
      <td>01.02.2025</td><td>uczen@example.test</td></tr>
    </table><script>token='secret'</script></body></html>
    """

    result = sanitize_html(raw, "messages")

    for secret in (
        "Jan",
        "Kowalski",
        "Sekretny",
        "987",
        "token",
        "secret",
        "example.test",
        "data-owner",
        "title=",
    ):
        assert secret not in result
    assert "/fixture/message-1" in result
    assert "2026-09-01" in result
    soup = BeautifulSoup(result, "lxml")
    assert soup.find("script") is None
    assert soup.find("style") is None
    assert soup.find(string=lambda value: isinstance(value, Comment)) is None
    assert soup.table["class"] == ["decorated", "stretch"]


def test_sanitizer_preserves_only_explicit_safe_labels_and_attributes():
    raw = """
    <table><tr><td colspan="2" rowspan="1" style="font-weight: bold; color: red"
      aria-label="private">Pozytywna</td><td>Brak uwag</td><td>Dowolny tekst</td></tr></table>
    """

    result = sanitize_html(raw, "remarks")
    soup = BeautifulSoup(result, "lxml")
    cells = soup.select("td")

    assert cells[0].get_text(strip=True) == "Pozytywna"
    assert cells[1].get_text(strip=True) == "Brak uwag"
    assert cells[2].get_text(strip=True) == "Remark text 1"
    assert cells[0].attrs == {
        "colspan": "2",
        "rowspan": "1",
        "style": "font-weight: bold",
    }


def test_sanitizer_output_is_deterministic():
    raw = "<p>Teacher Name</p><a href='/private/123'>Subject</a>"

    assert sanitize_html(raw, "messages") == sanitize_html(raw, "messages")


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        (".librus-discovery/result.html", "discovery-artifact"),
        ("capture.html.raw", "raw-html"),
        (".env", "environment-file"),
        ("config/.env.local", "environment-file"),
    ],
)
def test_tracked_path_safety_rules_reject_private_artifacts(path, rule):
    assert check_tracked_paths([path]) == [f"{rule}:{path}"]


def test_tracked_path_safety_rules_accept_synthetic_fixture():
    assert check_tracked_paths(["tests/fixtures/messages_current.html"]) == []


def test_missing_cli_arguments_never_construct_client(monkeypatch):
    monkeypatch.setattr(
        discovery, "create_client", lambda: pytest.fail("client was constructed")
    )

    with pytest.raises(SystemExit) as caught:
        discovery.main([], {})

    assert caught.value.code == 2


def test_missing_credentials_return_before_constructing_client(tmp_path, monkeypatch):
    env_file = tmp_path / "credentials.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        discovery, "create_client", lambda: pytest.fail("client was constructed")
    )
    monkeypatch.setattr(discovery, "load_values", lambda _path: {})

    result = discovery.main(
        [
            "--confirm-live",
            "--env-file",
            str(env_file),
            "--account",
            "primary",
            "--output-dir",
            ".librus-discovery",
        ],
        {},
    )

    assert result == 2


def test_discovery_directory_is_git_ignored():
    assert discovery.is_ignored(Path(".librus-discovery/messages_current.html"))


@pytest.mark.parametrize(
    ("failing_stage", "safe_category"),
    [
        ("authentication", "authentication:unexpected"),
        ("messages", "messages-list:unexpected"),
        ("remarks", "remarks:unexpected"),
    ],
)
def test_runtime_failure_reports_only_the_sanitized_stage(
    failing_stage, safe_category, tmp_path, monkeypatch, capsys
):
    class FakeResponse:
        text = "<table><tr><td>Safe shape</td></tr></table>"

    class StageFailureClient:
        INDEX_URL = "https://private.invalid/index"
        MESSAGE_URL = "https://private.invalid/messages"

        def __init__(self):
            self.close_calls = 0

        def get_token(self, _username, _password):
            if failing_stage == "authentication":
                raise RuntimeError("private student data")

        def get(self, url):
            if url == self.INDEX_URL:
                return type(
                    "IndexResponse",
                    (),
                    {"text": '<a href="/current-remarks-route">Uwagi</a>'},
                )()
            if failing_stage == "messages" and url == self.MESSAGE_URL:
                raise RuntimeError("private student data")
            if failing_stage == "remarks" and url.endswith("current-remarks-route"):
                raise RuntimeError("private student data")
            return FakeResponse()

        def close(self):
            self.close_calls += 1

    env_file = tmp_path / "credentials.env"
    env_file.write_text("", encoding="utf-8")
    client = StageFailureClient()
    monkeypatch.setattr(discovery, "_tracked_paths", lambda: [])
    monkeypatch.setattr(discovery, "is_ignored", lambda _path: True)
    monkeypatch.setattr(
        discovery,
        "load_values",
        lambda _path: {
            "LIBRUS_ACCOUNT_PRIMARY_USERNAME": "private username",
            "LIBRUS_ACCOUNT_PRIMARY_PASSWORD": "private password",
        },
    )
    monkeypatch.setattr(discovery, "create_client", lambda: client)

    result = discovery.main(
        [
            "--confirm-live",
            "--env-file",
            str(env_file),
            "--account",
            "primary",
            "--output-dir",
            ".librus-discovery",
        ],
        {},
    )

    stderr = capsys.readouterr().err
    assert result == 1
    assert stderr == f"Discovery failed: {safe_category}\n"
    assert "private" not in stderr
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (MaintananceError("private body"), "authentication:maintenance"),
        (AccessDeniedError(403), "authentication:access-denied:403"),
        (TransportError(503), "authentication:transport:503"),
        (TransportError(), "authentication:transport:no-status"),
        (AuthorizationError("private body"), "authentication:authorization"),
        (KeyError("private response field"), "authentication:response-format"),
        (ValueError("private response body"), "authentication:response-format"),
        (RuntimeError("private details"), "authentication:unexpected"),
    ],
)
def test_authentication_failure_category_never_contains_exception_data(
    error, category
):
    result = discovery.classify_failure("authentication", error)

    assert result == category
    assert "private" not in result


def test_remarks_url_is_discovered_from_same_origin_navigation():
    html = """
    <nav><a href="/przegladaj_plan_lekcji">Plan</a>
    <a href="/current-remarks-route">Uwagi</a></nav>
    """

    assert discovery.find_remarks_url(html) == (
        "https://synergia.librus.pl/current-remarks-route"
    )


@pytest.mark.parametrize(
    "html",
    [
        "<nav><a href='https://attacker.invalid/remarks'>Uwagi</a></nav>",
        "<nav><a href='/one'>Uwagi</a><a href='/two'>Uwagi</a></nav>",
        "<nav><a href='/grades'>Oceny</a></nav>",
    ],
)
def test_remarks_url_rejects_external_ambiguous_or_missing_navigation(html):
    with pytest.raises(ValueError, match="Remarks navigation is unavailable"):
        discovery.find_remarks_url(html)


def test_discovery_fetches_remarks_from_validated_navigation(
    tmp_path, monkeypatch
):
    class FakeResponse:
        def __init__(self, text):
            self.text = text

    class RecordingClient:
        INDEX_URL = "https://synergia.librus.pl/uczen/index"
        MESSAGE_URL = "https://synergia.librus.pl/messages"

        def __init__(self):
            self.calls = []
            self.close_calls = 0

        def get_token(self, _username, _password):
            return None

        def get(self, url):
            self.calls.append(url)
            if url == self.INDEX_URL:
                return FakeResponse(
                    '<nav><a href="/current-remarks-route">Uwagi</a></nav>'
                )
            return FakeResponse("<table><tr><td>Private data</td></tr></table>")

        def close(self):
            self.close_calls += 1

    env_file = tmp_path / "credentials.env"
    env_file.write_text("", encoding="utf-8")
    client = RecordingClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(discovery, "_tracked_paths", lambda: [])
    monkeypatch.setattr(discovery, "is_ignored", lambda _path: True)
    monkeypatch.setattr(
        discovery,
        "load_values",
        lambda _path: {
            "LIBRUS_ACCOUNT_PRIMARY_USERNAME": "private username",
            "LIBRUS_ACCOUNT_PRIMARY_PASSWORD": "private password",
        },
    )
    monkeypatch.setattr(discovery, "create_client", lambda: client)

    result = discovery.main(
        [
            "--confirm-live",
            "--env-file",
            str(env_file),
            "--account",
            "primary",
            "--output-dir",
            ".librus-discovery",
        ],
        {},
    )

    assert result == 0
    assert client.calls == [
        client.INDEX_URL,
        client.MESSAGE_URL,
        "https://synergia.librus.pl/current-remarks-route",
    ]
    assert client.close_calls == 1
    for fixture in ("messages_current.html", "remarks_current.html"):
        content = (tmp_path / ".librus-discovery" / fixture).read_text()
        assert "Private data" not in content
