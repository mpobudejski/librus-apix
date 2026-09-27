from logging import Logger
from pathlib import Path
from typing import List

import pytest
from bs4 import BeautifulSoup

from librus_apix.client import Client
from librus_apix.exceptions import ParseError
from librus_apix.messages import (
    Message,
    MessageData,
    get_received,
    get_sent,
    get_max_page_number,
    message_content,
    parse,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "lxml")


def _test_message_data(msg: Message, log: Logger):
    strings = list(msg.__dict__.items())[:4]
    for key, val in strings:
        assert isinstance(val, str)
        if val == "":
            log.warning(f"{key} is an empty string")
    assert isinstance(msg.unread, bool)
    assert isinstance(msg.has_attachment, bool)


def test_get_max_page(client: Client):
    max_page = get_max_page_number(client)
    assert isinstance(max_page, int)
    assert max_page >= 0


def test_get_sent_messages(client: Client, log: Logger):
    sent = get_sent(client, 1)
    assert isinstance(sent, list)
    for msg in sent:
        assert isinstance(msg, Message)
        _test_message_data(msg, log)


@pytest.fixture
def get_received_messages(client: Client) -> List[Message]:
    received = get_received(client, 1)
    return received


def test_get_received_messages(get_received_messages: List[Message], log: Logger):
    assert isinstance(get_received_messages, List)
    for msg in get_received_messages:
        assert isinstance(msg, Message)
        _test_message_data(msg, log)


def test_message_content(
    get_received_messages: List[Message], client: Client, log: Logger
):
    if len(get_received_messages) == 0:
        pytest.skip("No messages to check")
    sample: Message = get_received_messages[0]
    data = message_content(client, sample.href)
    assert isinstance(data, MessageData)
    for key, value in data.__dict__.items():
        if value == "":
            log.warning(f"{key} value is empty")


def test_legacy_message_schema_is_parsed():
    messages = parse(load_soup("messages_legacy.html"))

    assert messages == [
        Message(
            author="Nauczyciel A",
            title="Temat testowy",
            date="2026-09-03 12:30",
            href="message-1",
            unread=True,
            has_attachment=False,
        )
    ]


def test_current_message_schema_is_parsed_without_fixed_href_depth():
    messages = parse(load_soup("messages_current.html"))

    assert len(messages) == 12
    assert (messages[0].author, messages[0].title, messages[0].href) == (
        "Message text 66",
        "Message text 67",
        "message-58",
    )
    assert (messages[-1].author, messages[-1].title, messages[-1].href) == (
        "Message text 99",
        "Message text 100",
        "message-91",
    )
    assert {message.date for message in messages} == {"2026-09-01"}


def test_explicit_empty_inbox_is_empty():
    soup = BeautifulSoup(
        "<table class='decorated stretch'><tbody>"
        "<tr class='line0'><td>Brak wiadomości</td></tr>"
        "</tbody></table>",
        "lxml",
    )

    assert parse(soup) == []


def test_missing_rows_are_not_an_empty_inbox():
    soup = BeautifulSoup(
        "<table class='decorated stretch'><tbody/></table>", "lxml"
    )

    with pytest.raises(ParseError, match="Error in parsing messages"):
        parse(soup)


def test_attachment_unread_and_trailing_slash_href_are_parsed():
    soup = BeautifulSoup(
        """
        <table class="decorated stretch"><tbody><tr class="line0">
          <td></td><td><img></td>
          <td><a href="/wiadomosci/odebrane/pokaz/message-7/">Nauczyciel</a></td>
          <td style="font-weight: bold">Temat</td>
          <td>2026-09-03 12:30</td><td></td>
        </tr></tbody></table>
        """,
        "lxml",
    )

    assert parse(soup) == [
        Message(
            author="Nauczyciel",
            title="Temat",
            date="2026-09-03 12:30",
            href="message-7",
            unread=True,
            has_attachment=True,
        )
    ]


def test_message_row_with_a_missing_semantic_cell_is_rejected():
    soup = BeautifulSoup(
        """
        <table class="decorated stretch"><tbody><tr class="line0">
          <td></td><td></td><td><a href="/message-7">Nauczyciel</a></td>
          <td>Temat</td><td></td>
        </tr></tbody></table>
        """,
        "lxml",
    )

    with pytest.raises(ParseError, match="Error in parsing messages"):
        parse(soup)


def test_pagination_with_spaces_returns_zero_based_maximum_page():
    class Response:
        text = '<div class="pagination"><span>1 z 4</span></div>'

    class PaginationClient:
        MESSAGE_URL = "https://example.invalid/messages"

        def get(self, _url):
            return Response()

    assert get_max_page_number(PaginationClient()) == 3
