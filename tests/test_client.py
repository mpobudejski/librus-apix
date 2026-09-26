from logging import Logger
from typing import Dict
import pytest
from requests import Session
from requests.models import Response
from requests.sessions import RequestsCookieJar

from librus_apix.client import Client, Token, new_client


class TrackingSession(Session):
    def __init__(self):
        super().__init__()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


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
