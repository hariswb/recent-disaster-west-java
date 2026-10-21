"""Clustering regression on a real 24h window (2026-10-21), where the same events
were split into many incidents: Kota/Kabupaten mix-ups, headlines sharing a single
distinctive word, and a publisher domain mistaken for the location."""

import json
from collections import Counter
from pathlib import Path

import pytest

from recentdisaster.cluster import cluster, to_incident, unique_ids
from recentdisaster.models import Report
from recentdisaster.rules import gazetteer

FIXTURE = Path(__file__).parent / "fixtures" / "reports_2026-10-21.json"

# Each tuple: headline fragments that all report one event.
SAME_EVENT = {
    "mbg kalijaga cirebon": (
        "Sejumlah Siswa SD di Kalijaga Cirebon Sakit Perut",
        "Kasus MBG di Cirebon, 106 Orang Dirawat",
        "Walkot Cirebon Ungkap Kondisi",
        "Sekitar 100 Anak di Cirebon Diduga Keracunan MBG",
        "Sekitar 100 Anak di Kalijaga Cirebon Diduga Keracunan",
        "7 Fakta Keracunan MBG di Kota Cirebon",
        "Lagi-lagi di Jabar, Sejumlah Siswa SD di Sakit Perut",
        "50 Siswa di Kalijaga Cirebon Dirawat",
    ),
    "dokter cabuli anak bandung": (
        "Dokter di Bandung Diduga Cabuli Anak Kandung Usia 3 Tahun",
        "Tega, Seorang Ayah di Bandung Diduga Cabuli Anak Kandungnya",
        "Atalia Desak Polda Jabar Tangkap Oknum Dokter",
        "Anak 3 Tahun Diduga Jadi Korban Pencabulan Ayah Kandung di Bandung",
        "4 Fakta Kasus Viral Pencabulan Anak",
    ),
    "ponpes bekasi": (
        "Ponpes Dar Et Tauhid di Bekasi Dibekukan",
        "Kemenag Cabut Izin Ponpes di Bekasi",
    ),
    "kebakaran bpn karawang": (
        "Kebakaran Kantah Karawang",
        "Kantor Pertanahan Karawang Terbakar",
        "Kebakaran BPN Karawang: Polisi Tunggu Hasil Puslabfor",
        "Kebakaran BPN Karawang Disorot, Askun Minta",
        "Penyebab Kebakaran Kantor BPN Karawang Masih Misterius",
        "Misteri Kebakaran BPN Karawang",
        "Penyebab Kebakaran BPN Karawang Masih Jadi Tanda Tanya",
    ),
    "lapak rongsok tajurhalang": (
        "Lapak Rongsokan di Tajur Halang Bogor",
        "Lapak Rongsok di Bogor Kebakaran, Api Padam Setelah 8 Jam",
        "Kebakaran Hanguskan Lapak Rongsokan di Bogor dan Rugikan Rp300 Juta",
        "Water Tank TNI Bantu Padamkan Kebakaran di Tajurhalang",
        "Kebakaran Hebat Hantam Bangunan di Tajur Halang",
    ),
    "tpa pasirbajing garut": (
        "Asap Kebakaran TPA Pasirbajing",
        "Kebakaran TPA Pasirbajing Garut Padam",
    ),
    "geng motor pecel lele tasikmalaya": (
        "Geng Motor Serang Warga dan Pedaga",
        "Teror Berandalan Motor di Tasikmalaya, Pedagang Pecel Lele Diserang",
        "5 Tukang Pecel Lele di Kota Tasikmalaya",
    ),
    "kekeringan cianjur": (
        "Krisis Air Bersih Meluas di 12 Kecamatan Cianjur",
        "Kekeringan Cianjur Meluas, 12 Kecamatan",
    ),
}

# Same regency and category, but different events.
DIFFERENT_EVENTS = [
    ("Kantor Pertanahan Karawang Terbakar", "Hotel Grand Mutiara Cikampek", "Gudang Limbah Majalaya"),
    ("Kebakaran TPA Pasirbajing Garut Padam", "Pasar Andir Bayongbong"),
    ("Rumah di Cijurey Gegerbitung", "Lahan Ditumbuhi Pinus di Sukabumi"),
    ("Dua Pemuda di Bandung Jadi Korban Pembacokan", "Upaya Polisi Redam Aksi Geng Motor di Bandung"),
]


@pytest.fixture(scope="module")
def groups():
    reports = [Report.model_validate(r) for r in json.loads(FIXTURE.read_text())]
    return cluster(reports)


def _group_of(groups, fragment: str) -> set[int]:
    found = {i for i, g in enumerate(groups) for r in g if fragment.lower() in r.item.title.lower()}
    assert found, f"no report titled like {fragment!r}"
    return found


@pytest.mark.parametrize("event", SAME_EVENT)
def test_reports_of_one_event_form_one_incident(groups, event):
    owners = set().union(*(_group_of(groups, f) for f in SAME_EVENT[event]))
    assert len(owners) == 1, [[r.item.title for r in groups[i]][:3] for i in owners]


@pytest.mark.parametrize("fragments", DIFFERENT_EVENTS)
def test_distinct_events_stay_apart(groups, fragments):
    owners = [_group_of(groups, f) for f in fragments]
    for i, a in enumerate(owners):
        for b in owners[i + 1 :]:
            assert not a & b, fragments


def test_incident_ids_are_unique(groups):
    ids = Counter(i["id"] for i in unique_ids([to_incident(g, gazetteer()) for g in groups]))
    assert [k for k, n in ids.items() if n > 1] == []


def test_kecamatan_decides_kota_or_kabupaten(groups):
    [mbg] = [g for g in groups if any("Kalijaga" in r.item.title for r in g)]
    inc = to_incident(mbg, gazetteer())
    assert (inc["kab_kota"], inc["kecamatan"]) == ("Kota Cirebon", "Harjamukti")
