from logging import Logger
from typing import Dict
import pytest
from requests import RequestException, Session
from requests.models import Response
from requests.sessions import RequestsCookieJar

from librus_apix.client import Client, Token, new_client
from librus_apix.exceptions import (
    AccessDeniedError,
    AdditionalAuthenticationError,
    AuthorizationError,
    MaintananceError,
    TransportError,
)


class TrackingSession(Session):
    def __init__(self):
        super().__init__()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


class QueueSession(Session):
    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status_code=200, content=b"{}"):
    result = Response()
    result.status_code = status_code
    result._content = content
    return result


def test_client_token(client: Client, log: Logger):
    token = client.token
    assert isinstance(token, Token)
    assert isinstance(token.__repr__(), str)
    if str(token) == "":
        log.warning("test token key is empty")
        pytest.skip("Omitting token parse tests due to empty key")
    assert isinstance(token._parse_api_key(token.API_Key), Dict)
    assert isinstance(token.access_cookies(), RequestsCookieJar)


def test_get_base_url(client: Client):
    assert isinstance(client.cookies, RequestsCookieJar)
    response = client.get(client.BASE_URL)
    assert isinstance(response, Response)
    assert response.status_code == 200


def test_injected_session_is_used_but_not_closed_by_client():
    session = TrackingSession()
    client = Client(Token(), session=session)

    client.close()
    client.close()

    assert client.session is session
    assert session.close_calls == 0


def test_internally_created_session_is_closed_once():
    client = Client(Token())
    session = client.session
    close_calls = 0
    original_close = session.close

    def tracking_close():
        nonlocal close_calls
        close_calls += 1
        original_close()

    session.close = tracking_close

    with client as entered:
        assert entered is client
    client.close()

    assert close_calls == 1


def test_default_clients_do_not_share_mutable_state():
    first = new_client()
    second = new_client()

    assert first.token is not second.token
    assert first.cookies is not second.cookies
    assert first.proxy is not second.proxy
    assert first.session is not second.session


def test_timeout_configuration_is_public_and_per_client():
    client = new_client(connect_timeout=2, read_timeout=7)

    assert client.connect_timeout == 2
    assert client.read_timeout == 7


@pytest.mark.parametrize(
    ("status_code", "maintenance", "error_type"),
    [
        (401, False, AccessDeniedError),
        (403, False, AccessDeniedError),
        (408, False, TransportError),
        (429, False, TransportError),
        (404, False, TransportError),
        (500, False, TransportError),
        (503, False, TransportError),
        (503, True, MaintananceError),
    ],
)
def test_http_errors_are_classified_without_private_response_data(
    status_code, maintenance, error_type
):
    session = QueueSession(response(status_code, b"private body"))
    client = Client(Token(), session=session, connect_timeout=2, read_timeout=7)

    with pytest.raises(error_type) as caught:
        client._request(
            "GET", "https://private.invalid/student-id", maintenance=maintenance
        )

    assert "private" not in str(caught.value)
    assert "student-id" not in str(caught.value)
    if isinstance(caught.value, (AccessDeniedError, TransportError)):
        assert caught.value.status_code == status_code
    assert session.calls[0][2]["timeout"] == (2, 7)


def test_explicit_request_timeout_overrides_client_default():
    session = QueueSession(response())
    client = Client(Token(), session=session, connect_timeout=2, read_timeout=7)

    client._request("GET", "https://example.invalid", timeout=1)

    assert session.calls[0][2]["timeout"] == 1


def test_request_failures_are_sanitized():
    session = QueueSession(RequestException("https://private.invalid/student-id"))
    client = Client(Token(), session=session)

    with pytest.raises(TransportError) as caught:
        client._request("GET", "https://private.invalid/student-id")

    assert caught.value.status_code is None
    assert "private" not in str(caught.value)
    assert len(session.calls) == 1


def test_public_get_and_post_apply_default_timeout_without_closing_session():
    session = TrackingSession()
    queue = QueueSession(response(), response())
    session.request = queue.request
    client = Client(Token("first:second"), session=session)

    client.get("https://example.invalid/get")
    client.post("https://example.invalid/post", {"key": "value"})

    assert [call[2]["timeout"] for call in queue.calls] == [(10, 30), (10, 30)]
    assert session.close_calls == 0


def test_refresh_oauth_uses_central_request_timeout():
    oauth_response = response()
    oauth_response.cookies.set("oauth_token", "oauth-value")
    session = QueueSession(oauth_response)
    client = Client(Token("first:second"), session=session, connect_timeout=2)

    assert client.refresh_oauth() == "oauth-value"
    assert session.calls[0][2]["timeout"] == (2, 30)


def test_login_applies_timeout_to_each_http_operation():
    session = QueueSession(
        response(),
        response(),
        response(content=b'{"status": "ok"}'),
        response(),
    )
    session.cookies.set("DZIENNIKSID", "first")
    session.cookies.set("SDZIENNIKSID", "second")
    client = Client(Token(), session=session, read_timeout=7)

    assert repr(client.get_token("username", "password")) == "first:second"
    assert len(session.calls) == 4
    assert all(call[2]["timeout"] == (10, 7) for call in session.calls)


def test_login_accepts_unauthenticated_maintenance_probe_challenge():
    session = QueueSession(
        response(401),
        response(),
        response(content=b'{"status": "ok"}'),
        response(),
    )
    session.cookies.set("DZIENNIKSID", "first")
    session.cookies.set("SDZIENNIKSID", "second")
    client = Client(Token(), session=session)

    assert repr(client.get_token("username", "password")) == "first:second"
    assert len(session.calls) == 4


def test_login_rejects_unauthorized_credential_submission():
    session = QueueSession(response(401), response(), response(401))
    client = Client(Token(), session=session)

    with pytest.raises(AccessDeniedError) as caught:
        client.get_token("username", "password")

    assert caught.value.status_code == 401
    assert len(session.calls) == 3


def test_login_reports_additional_authentication_without_private_response_data():
    session = QueueSession(
        response(401),
        response(),
        response(
            content=(
                b'{"status":"error","errors":[{"message":'
                b'"Wymagana weryfikacja dwuetapowa: private student"}]}'
            )
        ),
        response(),
    )
    client = Client(Token(), session=session)

    with pytest.raises(AdditionalAuthenticationError) as caught:
        client.get_token("username", "password")

    assert type(caught.value) is AdditionalAuthenticationError
    assert "private" not in str(caught.value)
    assert "student" not in str(caught.value)


def test_login_keeps_invalid_credentials_distinct_from_additional_authentication():
    session = QueueSession(
        response(401),
        response(),
        response(
            content=b'{"status":"error","errors":[{"message":"Bad password"}]}'
        ),
        response(),
    )
    client = Client(Token(), session=session)

    with pytest.raises(AuthorizationError) as caught:
        client.get_token("username", "password")

    assert type(caught.value) is AuthorizationError
    assert str(caught.value) == "Authorization failed"


@pytest.mark.parametrize("failing_request", range(4))
def test_503_on_every_login_request_is_maintenance(failing_request):
    responses = [
        response(401),
        response(),
        response(content=b'{"status":"ok"}'),
        response(),
    ]
    responses[failing_request] = response(503, b"private maintenance body")
    session = QueueSession(*responses)
    client = Client(Token(), session=session)

    with pytest.raises(MaintananceError) as caught:
        client.get_token("username", "password")

    assert "private" not in str(caught.value)
    assert len(session.calls) == failing_request + 1
