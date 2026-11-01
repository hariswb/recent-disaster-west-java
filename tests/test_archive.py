import json
from datetime import datetime, timedelta

import pytest

from recentdisaster import archive
from recentdisaster.cluster import PAST_DAYS, event_age_days, past_fields
from recentdisaster.config import WIB

NOW = datetime(2026, 10, 20, 14, 0, tzinfo=WIB)


def inc(id_, *sources, title="Banjir di Garut", **kw):
    """sources: (url, hours before NOW)."""
    src = [{"name": "S", "url": u, "title": "t", "published": (NOW - timedelta(hours=h)).isoformat()} for u, h in sources]
    src.sort(key=lambda s: s["published"])
    return {"id": id_, "category": "banjir", "title": title, "victims": kw.pop("victims", {}), "affected_entities": [],
            "event_time": None, "first_reported": src[0]["published"], "last_reported": src[-1]["published"],
            "sources": src, "report_count": len(src), "confidence": 0.9, **kw}


def entries(root):
    return [e for p in sorted(root.glob("????-??.json")) for e in json.loads(p.read_text())["incidents"]]


def run(root, incidents, now=NOW):
    return archive.update(incidents, now, now - timedelta(hours=24), root)


def test_follow_up_runs_extend_one_entry_and_keep_its_id(tmp_path):
    run(tmp_path, [inc("a1", ("https://x.id/1", 20), ("https://x.id/2", 6))])
    later = NOW + timedelta(hours=12)
    # Next run: report 1 is out of the window; 2 and a new 3 cluster under another id.
    ids = archive.update([inc("b2", ("https://x.id/2", 6), ("https://x.id/3", -10), victims={"dead": 2})],
                         later, later - timedelta(hours=24), tmp_path)
    assert ids == {"b2": "a1"}
    later2 = later + timedelta(hours=12)
    archive.update([inc("c3", ("https://x.id/3", -10), ("https://x.id/4", -20))], later2, later2 - timedelta(hours=24), tmp_path)
    [e] = entries(tmp_path)
    assert e["id"] == "a1"
    assert e["report_count"] == 4 and len(e["refs"]) == 4
    assert e["victims"] == {"dead": 2}  # kept from the earlier version
    assert "confidence" not in e and "title" not in e["sources"][0]


def test_reports_dropped_inside_the_window_leave_the_archive(tmp_path):
    run(tmp_path, [inc("a1", ("https://x.id/1", 3)), inc("z9", ("https://x.id/9", 30), ("https://x.id/8", 2))])
    # Re-extraction: report 1 is no incident after all, report 8 left its cluster.
    run(tmp_path, [])
    [e] = entries(tmp_path)
    assert e["id"] == "z9" and e["report_count"] == 1


def test_split_cluster_becomes_two_entries(tmp_path):
    run(tmp_path, [inc("a1", ("https://x.id/1", 30), ("https://x.id/2", 5), ("https://x.id/3", 4))])
    ids = run(tmp_path, [inc("p", ("https://x.id/2", 5)), inc("q", ("https://x.id/3", 4))])
    assert ids["p"] == "a1" and ids["q"] == "q"
    assert sorted(e["report_count"] for e in entries(tmp_path)) == [1, 2]


def test_unrelated_incident_with_the_same_id_is_renamed(tmp_path):
    run(tmp_path, [inc("a1", ("https://x.id/1", 30))])
    ids = run(tmp_path, [inc("a1", ("https://x.id/5", 1))])
    assert ids["a1"] != "a1"
    assert len({e["id"] for e in entries(tmp_path)}) == 2


def test_months_and_index(tmp_path):
    # 31 Oct 23:00 WIB is October; 1 Nov 06:00 WIB (30 Oct 23:00 UTC + 31h) is November.
    now = datetime(2026, 11, 1, 8, 0, tzinfo=WIB)
    a = inc("a", ("https://x.id/a", 0))
    a["sources"][0]["published"] = a["first_reported"] = "2026-10-31T16:00:00+00:00"
    b = inc("b", ("https://x.id/b", 0))
    b["sources"][0]["published"] = b["first_reported"] = "2026-10-31T23:00:00+00:00"
    run(tmp_path, [a, b], now=now)
    assert [e["id"] for e in json.loads((tmp_path / "2026-10.json").read_text())["incidents"]] == ["a"]
    assert [e["id"] for e in json.loads((tmp_path / "2026-11.json").read_text())["incidents"]] == ["b"]
    index = json.loads((tmp_path / "index.json").read_text())
    assert [(m["month"], m["incidents"]) for m in index["months"]] == [("2026-10", 1), ("2026-11", 1)]


def test_google_news_links_are_capped_but_counted(tmp_path):
    srcs = [(f"https://news.google.com/rss/articles/{n}", 10 - n * 0.5) for n in range(12)] + [("https://detik.com/x", 1)]
    run(tmp_path, [inc("a", *srcs)])
    [e] = entries(tmp_path)
    assert e["report_count"] == 13
    assert len(e["sources"]) == archive.MAX_SOURCES
    assert any("detik.com" in s["url"] for s in e["sources"])


@pytest.mark.parametrize("event_time, age", [
    ("2026-10-18", 2),
    ("2026-10-03T22:00:00+07:00", 17),
    ("2025-07", 476),           # month only: counted from the 1st
    ("2024-??-??", None),       # unreadable
    ("2026-11-22T13:00:00+07:00", None),  # future: a misread date
    (None, None),
])
def test_event_age(event_time, age):
    assert event_age_days(event_time, "2026-10-20T09:00:00+07:00") == age


def test_past_flag_threshold():
    reported = datetime(2026, 10, 20, 9, 0, tzinfo=WIB)
    assert past_fields((reported - timedelta(days=PAST_DAYS)).date().isoformat(), reported)["past"]
    assert not past_fields((reported - timedelta(days=PAST_DAYS - 1)).date().isoformat(), reported)["past"]
