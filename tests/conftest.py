from datetime import datetime

import pytest

from recentdisaster.config import WIB
from recentdisaster.models import RawItem


@pytest.fixture
def now():
    return datetime(2026, 9, 30, 15, 0, tzinfo=WIB)


@pytest.fixture
def make_item():
    def _make(title, summary="", *, url=None, jabar_only=True, published=None, publisher=None):
        return RawItem(
            source_id="test", source_name=publisher or "Test", url=url or f"https://ex.id/{abs(hash(title))}",
            title=title, summary=summary, jabar_only=jabar_only, published=published, publisher=publisher,
        )
    return _make
