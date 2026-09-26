import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag

from librus_apix import urls
from librus_apix.client import Client, Token

SAFE_LABELS = {
    "Brak wiadomości",
    "Brak uwag",
    "Pozytywna",
    "Negatywna",
    "Neutralna",
    "Punkty",
    "Uwagi",
    "Wiadomości",
}
DATE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{4}(?:,?\s+\d{1,2}:\d{2})?$")
ACCOUNT_ID = re.compile(r"^[a-z][a-z0-9_]*$")
REMARKS_URL = urls.BASE_URL + "/przegladaj_uwagi/uczen"
SAFE_CLASSES = {
    "big",
    "bolded",
    "center",
    "container-background",
    "decorated",
    "left",
    "line0",
    "line1",
    "message-recipients",
    "micro",
    "pagination",
    "right",
    "screen-only",
    "small",
    "stretch",
    "text",
}


def sanitize_html(html: str, section: str) -> str:
    if section not in {"messages", "remarks"}:
        raise ValueError("Unsupported discovery section")

    soup = BeautifulSoup(html, "lxml")
    for unsafe in soup.find_all(["script", "style"]):
        unsafe.decompose()
    for unsafe_text in soup.find_all(string=lambda value: isinstance(value, Comment)):
        unsafe_text.extract()
    for doctype in soup.find_all(string=lambda value: isinstance(value, Doctype)):
        doctype.extract()

    href_index = 0
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        sanitized_attributes: dict[str, str | list[str]] = {}
        classes = tag.get("class", [])
        safe_classes = [value for value in classes if value in SAFE_CLASSES]
        if safe_classes:
            sanitized_attributes["class"] = safe_classes

        style = tag.get("style", "")
        if "font-weight: bold" in style:
            sanitized_attributes["style"] = "font-weight: bold"

        for name in ("colspan", "rowspan"):
            value = tag.get(name)
            if isinstance(value, str) and value.isdigit() and 1 <= int(value) <= 99:
                sanitized_attributes[name] = value

        if tag.has_attr("href"):
            href_index += 1
            item = "message" if section == "messages" else "remark"
            sanitized_attributes["href"] = f"/fixture/{item}-{href_index}"
        tag.attrs = sanitized_attributes

    text_index = 0
    prefix = "Message" if section == "messages" else "Remark"
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString) or not node.strip():
            continue
        text = node.strip()
        if text in SAFE_LABELS:
            replacement = text
        elif DATE.fullmatch(text):
            replacement = "2026-09-01"
        else:
            text_index += 1
            replacement = f"{prefix} text {text_index}"
        node.replace_with(replacement)
    return str(soup)


def check_tracked_paths(paths: Sequence[str]) -> list[str]:
    violations = []
    for value in paths:
        path = Path(value)
        if ".librus-discovery" in path.parts:
            violations.append(f"discovery-artifact:{value}")
        elif value.endswith(".html.raw"):
            violations.append(f"raw-html:{value}")
        elif any(part == ".env" or part.startswith(".env.") for part in path.parts):
            violations.append(f"environment-file:{value}")
    return violations


def is_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(path)],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def create_client() -> Client:
    return Client(Token())


def load_values(path: Path) -> Mapping[str, str | None]:
    from dotenv import dotenv_values

    return dotenv_values(path)


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        value.decode("utf-8")
        for value in result.stdout.split(b"\0")
        if value
    ]


def _finish(code: int) -> int:
    violations = check_tracked_paths(_tracked_paths())
    if violations:
        rules = sorted({value.split(":", 1)[0] for value in violations})
        print("Tracked-path safety failure: " + ", ".join(rules), file=sys.stderr)
        return 3
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover sanitized Librus markup")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--account", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    if check_tracked_paths(_tracked_paths()):
        return _finish(3)
    args = _parser().parse_args(argv)
    if not args.confirm_live or not args.env_file.is_file():
        return _finish(2)
    if not ACCOUNT_ID.fullmatch(args.account):
        return _finish(2)

    values = {
        key: value
        for key, value in load_values(args.env_file).items()
        if value is not None
    }
    overrides = os.environ if environ is None else environ
    values.update(overrides)
    prefix = f"LIBRUS_ACCOUNT_{args.account.upper()}"
    username = values.get(f"{prefix}_USERNAME", "").strip()
    password = values.get(f"{prefix}_PASSWORD", "").strip()
    if not username or not password:
        return _finish(2)

    if args.output_dir != Path(".librus-discovery"):
        return _finish(2)
    messages_path = args.output_dir / "messages_current.html"
    remarks_path = args.output_dir / "remarks_current.html"
    if not is_ignored(messages_path) or not is_ignored(remarks_path):
        return _finish(2)

    client = create_client()
    try:
        client.get_token(username, password)
        messages_html = client.get(client.MESSAGE_URL).text
        remarks_html = client.get(REMARKS_URL).text
        sanitized_messages = sanitize_html(messages_html, "messages")
        sanitized_remarks = sanitize_html(remarks_html, "remarks")
        args.output_dir.mkdir(parents=False, exist_ok=True)
        messages_path.write_text(sanitized_messages, encoding="utf-8")
        remarks_path.write_text(sanitized_remarks, encoding="utf-8")
    except Exception:
        return _finish(1)
    finally:
        client.close()
    return _finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
