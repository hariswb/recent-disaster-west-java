import json
from datetime import timedelta

import pytest

from recentdisaster import pipeline
from recentdisaster.cluster import cluster, to_incident
from recentdisaster.models import Extraction, Report, Victims
from recentdisaster.rules import gazetteer
from recentdisaster.sources.rss import parse_feed

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Longsor timbun rumah di Sukajaya Bogor</title><link>https://ex.id/a?utm_source=x</link>
<pubDate>Wed, 30 Sep 2026 13:29:06 +0700</pubDate><description><![CDATA[<p>Dua orang tewas.</p>]]></description></item>
<item><title>Harga cabai naik</title><link>https://ex.id/b</link><pubDate>Wed, 30 Sep 2026 10:00:00 +0700</pubDate></item>
</channel></rss>"""

GNEWS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Banjir di Garut rendam 100 rumah - Tribun Jabar</title><link>https://news.google.com/rss/articles/X</link>
<pubDate>Wed, 30 Sep 2026 05:00:00 GMT</pubDate><description>Banjir di Garut ... Tribun Jabar</description>
<source url="https://jabar.tribunnews.com">Tribun Jabar</source></item></channel></rss>"""


def test_parse_rss():
    items = parse_feed(RSS, {"id": "s", "name": "S", "type": "rss", "jabar_only": True})
    assert [i.title for i in items] == ["Longsor timbun rumah di Sukajaya Bogor", "Harga cabai naik"]
    assert items[0].summary == "Dua orang tewas."
    assert items[0].published.utcoffset() == timedelta(hours=7)


def test_parse_gnews_strips_publisher_and_summary():
    [item] = parse_feed(GNEWS, {"id": "g", "name": "Google News", "type": "gnews"})
    assert item.title == "Banjir di Garut rendam 100 rumah"
    assert item.publisher == item.source_name == "Tribun Jabar"
    assert item.summary == ""


def _report(make_item, now, title, kab, kec=None, cat="banjir", hours_ago=1, **victims):
    return Report(
        item=make_item(title, published=now - timedelta(hours=hours_ago)),
        first_seen=now,
        extraction=Extraction(is_incident=True, in_jabar=True, category=cat, kab_kota=kab, kecamatan=kec,
                              victims=Victims(**victims), confidence=0.8),
        method="llm",
    )


def test_cluster_merges_same_event_across_outlets(make_item, now):
    reports = [
        _report(make_item, now, "Banjir bandang terjang Parigi Pangandaran, tiga tewas", "Kabupaten Pangandaran", "Parigi", dead=3),
        _report(make_item, now, "Tiga warga meninggal akibat banjir bandang di Pangandaran", "Kabupaten Pangandaran", "Parigi", dead=3, displaced=40),
        _report(make_item, now, "Banjir rendam puluhan rumah di Kota Bekasi", "Kota Bekasi"),
        _report(make_item, now, "Kebakaran gudang di Pangandaran", "Kabupaten Pangandaran", cat="kebakaran"),
        # same kecamatan, same category, different events
        _report(make_item, now, "Kobong Ponpes di Surade Sukabumi Kebakaran Diduga Akibat Korsleting", "Kabupaten Sukabumi", "Surade", cat="kebakaran"),
        _report(make_item, now, "Saat Santri Pergi Mengaji, Kobong Ponpes di Surade Sukabumi Ludes Terbakar", "Kabupaten Sukabumi", "Surade", cat="kebakaran"),
        _report(make_item, now, "Pasar Surade Sukabumi Terbakar, Api Muncul Dari Deretan Kios", "Kabupaten Sukabumi", "Surade", cat="kebakaran"),
        # same kecamatan, shared object word ("rumah") beyond the place name
        _report(make_item, now, "Rumah Ludes Terbakar, Janda 2 Anak di Kalibunder Sukabumi Mengungsi", "Kabupaten Sukabumi", "Kalibunder", cat="kebakaran"),
        _report(make_item, now, "Kebakaran Rumah Warga di Kalibunder, Camat Pastikan Penanganan Berlanjut", "Kabupaten Sukabumi", "Kalibunder", cat="kebakaran"),
    ]
    groups = cluster(reports)
    assert sorted(len(g) for g in groups) == [1, 1, 1, 2, 2, 2]
    merged = to_incident(next(g for g in groups if len(g) == 2), gazetteer())
    assert merged["victims"] == {"dead": 3, "displaced": 40}
    assert merged["report_count"] == 2
    assert merged["location_precision"] == "kecamatan"
    assert merged["lat"] and merged["lon"]


@pytest.fixture
def isolated(tmp_path, monkeypatch, now):
    """Run the pipeline against canned feed items, no network."""
    def run(items, **kw):
        monkeypatch.setattr(pipeline, "fetch_all", lambda sources, known: (items, [{"id": "t", "ok": True, "items": len(items)}]))
        monkeypatch.setattr(pipeline, "_fetch_bodies", lambda its: {})
        return pipeline.run(use_llm=False, now=kw.get("now", now), output_path=tmp_path / "out.json",
                            cache_path=tmp_path / "cache.json")
    return run


def test_pipeline_rules_only_end_to_end(isolated, make_item, now, tmp_path):
    items = [
        make_item("Longsor timbun rumah di Sukajaya Bogor, dua orang tewas", published=now - timedelta(hours=2)),
        make_item("Harga cabai naik di Bandung", published=now - timedelta(hours=1)),
        make_item("Banjir bandang di Garut kemarin lusa", published=now - timedelta(hours=30)),  # outside window
    ]
    out = isolated(items)
    written = json.loads((tmp_path / "out.json").read_text())
    assert [i["title"] for i in written["incidents"]] == ["Longsor timbun rumah di Sukajaya Bogor, dua orang tewas"]
    inc = written["incidents"][0]
    assert inc["kab_kota"] == "Kabupaten Bogor" and inc["victims"] == {"dead": 2} and inc["extraction"] == "rules"
    assert out["stats"]["candidates"] == 1


def test_incidents_persist_from_cache_then_expire(isolated, make_item, now, tmp_path):
    item = make_item("Longsor timbun rumah di Sukajaya Bogor", published=now - timedelta(hours=2))
    isolated([item])
    # Next run: the article has dropped out of the feed but is still < 24h old.
    out = isolated([], now=now + timedelta(hours=6))
    assert len(out["incidents"]) == 1
    # 23h later it is outside the window.
    out = isolated([], now=now + timedelta(hours=23))
    assert out["incidents"] == []
    # And it is pruned from the cache after 48h.
    isolated([], now=now + timedelta(hours=60))
    assert json.loads((tmp_path / "cache.json").read_text())["reports"] == {}


def test_html_listing_links_and_headline_screen():
    from recentdisaster.sources.html import _headline_worth_fetching, listing_links

    html = """<a href="/2026/09/30/banjir-rendam-desa/"><img></a>
    <a href="/2026/09/30/banjir-rendam-desa/">Banjir rendam 3 desa di Indramayu</a>
    <a href="/2026/09/30/harga-cabai/">Harga cabai naik</a>
    <a href>broken</a><a href="/kategori/kuningan/">Kuningan</a>"""
    links = listing_links(html, "https://radarcirebon.id/indramayu/", r"^https://radarcirebon\.id/20\d\d/\d\d/\d\d/[\w-]+/?$")
    assert links == {
        "https://radarcirebon.id/2026/09/30/banjir-rendam-desa/": "Banjir rendam 3 desa di Indramayu",
        "https://radarcirebon.id/2026/09/30/harga-cabai/": "Harga cabai naik",
    }
    assert _headline_worth_fetching("Banjir rendam 3 desa di Indramayu")
    assert not _headline_worth_fetching("Harga cabai naik")
    assert not _headline_worth_fetching("Simulasi gempa di sekolah")
