import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as dateparser

from recentdisaster.config import WIB

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|ref$|source$|amp$|page$|single$)")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)]
    path = re.sub(r"/(amp)/?$", "/", parts.path)
    path = path.rstrip("/") or "/"
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    return urlunsplit(("https", host, path, urlencode(query), ""))


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()


def parse_date(value) -> datetime | None:
    """Parse to an aware datetime. Naive values are assumed to be WIB."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, tuple):  # feedparser time.struct_time (UTC)
        dt = datetime(*value[:6], tzinfo=UTC)
    else:
        try:
            dt = dateparser.parse(str(value))
        except (ValueError, OverflowError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.astimezone(WIB)


def clean_title(title: str, publisher: str | None = None) -> str:
    title = strip_html(title)
    while publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(publisher) - 3]
    return title.strip()
