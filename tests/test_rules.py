import pytest

from recentdisaster.rules import extract_entities, extract_victims, gazetteer, rule_extract, screen

# (title, summary, jabar_only, expected candidate, expected category, expected kab/kota)
HEADLINES = [
    ("Polres Cianjur mengamankan perempuan terkait kebakaran dua rumah",
     "Seorang perempuan diamankan terkait kebakaran dua rumah di Kecamatan Cugenang, Kabupaten Cianjur.",
     True, True, "kebakaran", "Kabupaten Cianjur"),
    ("45 siswa SDN 2 Sukamaju diduga keracunan MBG di Garut", "12 siswa dirawat di puskesmas.",
     True, True, "keracunan", "Kabupaten Garut"),
    ("Banjir bandang terjang Desa Cibenda, tiga orang tewas", "Banjir melanda Kecamatan Parigi, Pangandaran.",
     True, True, "banjir", "Kabupaten Pangandaran"),
    ("Jembatan penghubung dua desa di Bandung Barat ambruk", "Jembatan di Kecamatan Cipongkor KBB ambruk.",
     True, True, "infrastruktur", "Kabupaten Bandung Barat"),
    ("Longsor timbun rumah warga di Sukajaya Bogor", "", True, True, "longsor", "Kabupaten Bogor"),
    ("Kebakaran Rumah di Kawasan Sumur Bandung", "", True, True, "kebakaran", "Kota Bandung"),
    ("Pasar Surade Sukabumi Terbakar, Api Muncul Dari Deretan Kios Belakang", "", False, True, "kebakaran", "Kabupaten Sukabumi"),
    ("Gempa M4,6 Guncang Bayah Banten, Getarannya Dirasakan Warga Sukabumi", "", False, True, "gempa", "Kabupaten Sukabumi"),
    ("Hujan Angin Terjang Bandung, Baliho Roboh Timpa 3 Kios dan Sebabkan 4 Warga Luka-luka", "", True, True, "angin", "Kota Bandung"),
    ("Puluhan pelajar di Kota Tasikmalaya keracunan makanan", "", False, True, "keracunan", "Kota Tasikmalaya"),
    ("Tanggul Citarum jebol, ratusan rumah di Karawang terendam", "", True, True, "banjir", "Kabupaten Karawang"),
    # not incidents / not Jabar
    ("Simulasi penanganan gempa digelar di Bandung", "BPBD Kota Bandung menggelar simulasi.", True, False, "gempa", None),
    ("Harga cabai di Pasar Kosambi naik", "Harga cabai naik di Kota Bandung.", True, False, None, None),
    ("Banjir rendam Jakarta Utara", "Banjir setinggi 50 cm di Jakarta Utara.", False, False, "banjir", None),
    ("Banjir Ucapan Selamat! Bupati Cianjur Raih Gelar Doktor", "", True, False, "banjir", None),
    ("Kebakaran kembali melanda kawasan hutan Malaumkarta Sorong", "", False, False, "kebakaran", None),
    ("DLH Cianjur menormalisasi saluran air cegah banjir", "", True, False, "banjir", None),
]


@pytest.mark.parametrize("title,summary,jabar_only,cand,cat,kab", HEADLINES)
def test_screen(make_item, title, summary, jabar_only, cand, cat, kab):
    s = screen(make_item(title, summary, jabar_only=jabar_only))
    assert s.candidate is cand, s.reason
    assert s.category == cat
    if cand:
        assert s.location.kab_kota == kab


@pytest.mark.parametrize("text,expected", [
    ("Tiga orang tewas dan puluhan warga mengungsi.", {"dead": 3, "displaced": 20}),
    ("Sebanyak 45 siswa mengalami keracunan, 12 siswa dirawat di puskesmas.", {"affected": 45, "injured": 12}),
    ("Seorang warga hilang terseret arus.", {"missing": 1}),
    ("Sebanyak 1.250 rumah terendam banjir.", {"houses": 1250}),
    ("Baliho roboh timpa 3 kios dan sebabkan 4 warga luka-luka.", {"injured": 4}),
    ("Sebanyak 5 rumah rusak berat akibat puting beliung.", {"houses": 5}),
    ("Pada tahun 2024 hilang sudah kenangan itu.", {}),
    ("Lebih dari 1 juta jiwa terdampak kekeringan.", {"affected": 1_000_000}),
    ("Sebanyak 2,5 ribu warga mengungsi.", {"displaced": 2500}),
    ("TNI gelontorkan 55 ribu liter air.", {}),
])
def test_victims(text, expected):
    assert extract_victims(text).model_dump(exclude_none=True) == expected


def test_entities():
    ents = {(e.type, e.name) for e in extract_entities("Siswa SDN 2 Sukamaju di Desa Cibenda keracunan, dirawat di RSUD Garut.")}
    assert ("sekolah", "SDN 2 Sukamaju") in ents
    assert ("desa", "Desa Cibenda") in ents
    assert ("fasilitas_kesehatan", "RSUD Garut") in ents


@pytest.mark.parametrize("text,kab", [
    ("Kabupaten Bandung", "Kabupaten Bandung"),
    ("Kab. Bandung Barat", "Kabupaten Bandung Barat"),
    ("KBB", "Kabupaten Bandung Barat"),
    ("Kota Bogor", "Kota Bogor"),
    ("garut", "Kabupaten Garut"),
    ("Bandung", "Kota Bandung"),
    ("Kota Tasikmalaya", "Kota Tasikmalaya"),
    ("Jakarta Selatan", None),
])
def test_normalize_kab(text, kab):
    assert gazetteer().normalize_kab(text) == kab


def test_kecamatan_resolves_regency():
    loc = gazetteer().locate("Kebakaran terjadi di Kecamatan Cugenang tadi malam.")
    assert (loc.kab_kota, loc.kecamatan) == ("Kabupaten Cianjur", "Cugenang")
    assert loc.lat and loc.lon


def test_rule_extract_marks_title_match_as_incident(make_item):
    ex = rule_extract(make_item("Banjir bandang terjang Desa Cibenda, tiga orang tewas", "Kecamatan Parigi, Pangandaran."))
    assert ex.is_incident and ex.in_jabar
    assert ex.desa == "Cibenda"
    assert ex.victims.dead == 3
