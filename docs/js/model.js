// Data model: psychosocial lenses, labels, aggregates and formatting helpers.

export const TZ = "Asia/Jakarta";
export const nf = new Intl.NumberFormat("id-ID");

// Support line shown on violence-related pages and images.
// TODO(asosiasi): confirm the numbers before public release.
export const SUPPORT_LINE = "Butuh dukungan? Layanan SAPA 129 (KemenPPPA) · WhatsApp 08111-129-129";

const ICON = {
  bencana: '<path d="M7 16a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 7a4.5 4.5 0 0 1 .5 9H7Z"/><path d="M8 19.5l-.8 1.5M12 19.5l-.8 1.5M16 19.5l-.8 1.5"/>',
  "sudden-loss": '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6L13 3Z"/>',
  kesehatan: '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M12 8v8M8 12h8"/>',
  kekerasan: '<path d="M12 3l7 3v5.5c0 4.4-3 8.1-7 9.5-4-1.4-7-5.1-7-9.5V6l7-3Z"/><path d="M9 12l2 2 4-4"/>',
  konflik: '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
  lainnya: '<circle cx="12" cy="12" r="8"/><path d="M12 8v5M12 16h.01"/>',
};

// Ordered for adjacent-pair colour-vision separation (validated with the dataviz palette checker).
export const LENSES = [
  { key: "bencana", label: "Bencana & pengungsian", short: "Bencana",
    desc: "Bencana alam yang dapat memicu pengungsian dan trauma kolektif.",
    cats: ["banjir", "longsor", "gempa", "angin", "kekeringan", "gunung"] },
  { key: "sudden-loss", label: "Sudden Loss", short: "Sudden Loss",
    desc: "Kebakaran, kecelakaan, dan kegagalan infrastruktur: peristiwa tiba-tiba yang berisiko memicu duka dan syok.",
    cats: ["kebakaran", "kecelakaan", "infrastruktur"] },
  { key: "kesehatan", label: "Kesehatan publik", short: "Kesehatan",
    desc: "Keracunan massal, insiden MBG, dan wabah yang menimbulkan kecemasan di komunitas.",
    cats: ["mbg", "keracunan", "wabah"] },
  { key: "kekerasan", label: "Kekerasan & perlindungan", short: "Kekerasan",
    desc: "Kekerasan seksual, kekerasan anak dan KDRT, perundungan, serta TPPO/penculikan.",
    cats: ["kekerasan_seksual", "kekerasan_anak", "perundungan", "tppo"], sensitive: true },
  { key: "konflik", label: "Konflik sosial", short: "Konflik",
    desc: "Tawuran, bentrokan, intoleransi, dan penggusuran yang mengusik rasa aman warga.",
    cats: ["tawuran", "intoleransi"] },
  { key: "lainnya", label: "Lainnya", short: "Lainnya",
    desc: "Insiden yang belum masuk kelompok mana pun.", cats: [] },
].map((l) => ({ ...l, icon: ICON[l.key] }));

export const LENS = Object.fromEntries(LENSES.map((l) => [l.key, l]));
const CAT_LENS = Object.fromEntries(LENSES.flatMap((l) => l.cats.map((c) => [c, l.key])));

// The committed incidents.json may predate newer categories; keep labels here as a fallback.
const CAT_LABEL = {
  banjir: "Banjir", longsor: "Tanah longsor", gempa: "Gempa bumi", angin: "Angin kencang / puting beliung",
  kebakaran: "Kebakaran", kekeringan: "Kekeringan / krisis air", gunung: "Aktivitas gunung api",
  mbg: "Keracunan / insiden MBG", keracunan: "Keracunan", wabah: "Wabah / KLB penyakit",
  perundungan: "Perundungan / bullying", kekerasan_seksual: "Kekerasan / pelecehan seksual",
  kekerasan_anak: "Kekerasan anak & KDRT", tppo: "TPPO / penculikan", intoleransi: "Intoleransi / konflik sosial",
  tawuran: "Tawuran / bentrok", infrastruktur: "Kegagalan infrastruktur", kecelakaan: "Kecelakaan", lainnya: "Lainnya",
};

export const VICTIMS = [
  ["dead", "meninggal"], ["injured", "luka/dirawat"], ["missing", "hilang"],
  ["displaced", "mengungsi"], ["affected", "terdampak"], ["houses", "rumah/bangunan"],
];
export const VICTIM_TITLE = {
  dead: "Meninggal", injured: "Luka / dirawat", missing: "Hilang", displaced: "Mengungsi",
  affected: "Terdampak", houses: "Rumah / bangunan",
};

export const ENTITY_LABEL = {
  sekolah: "Sekolah", desa: "Desa/permukiman", permukiman: "Permukiman", rumah: "Rumah", pasar: "Pasar",
  pabrik: "Pabrik", fasilitas_kesehatan: "Faskes", rumah_ibadah: "Rumah ibadah", kantor: "Kantor",
  jalan_jembatan: "Jalan/jembatan", lahan: "Lahan", dapur_mbg: "Dapur MBG/SPPG", lainnya: "Lainnya",
};

// Heuristic: which groups of people are likely affected, from category + affected entities.
const GROUP_BY_ENTITY = {
  sekolah: "Anak & pelajar", dapur_mbg: "Anak & pelajar", permukiman: "Keluarga & warga",
  desa: "Keluarga & warga", rumah: "Keluarga & warga", fasilitas_kesehatan: "Pasien & tenaga kesehatan",
  rumah_ibadah: "Jemaah / komunitas agama", pasar: "Pedagang", pabrik: "Pekerja", lahan: "Petani",
};
const GROUP_BY_CAT = {
  kekerasan_anak: "Anak", perundungan: "Anak & remaja", kekerasan_seksual: "Penyintas kekerasan seksual",
  tppo: "Pekerja migran / korban perdagangan orang", mbg: "Anak & pelajar", tawuran: "Remaja & warga",
  intoleransi: "Komunitas agama / warga terdampak", wabah: "Warga & keluarga",
};
const CHILD_RE = /\b(anak|siswa|siswi|murid|pelajar|santri|balita|bayi|bocah|remaja)\b/i;

export function lensOf(cat) { return LENS[CAT_LENS[cat] || "lainnya"]; }
export function catLabel(data, cat) { return data.categories?.[cat]?.label || CAT_LABEL[cat] || cat; }

export function groupsAffected(i) {
  const s = new Set();
  if (GROUP_BY_CAT[i.category]) s.add(GROUP_BY_CAT[i.category]);
  for (const e of i.affected_entities || []) if (GROUP_BY_ENTITY[e.type]) s.add(GROUP_BY_ENTITY[e.type]);
  if (CHILD_RE.test(`${i.summary || ""}`) && ![...s].some((g) => /anak/i.test(g))) s.add("Anak & remaja");
  return [...s];
}

export function involvesChildren(i) {
  return groupsAffected(i).some((g) => /anak|remaja/i.test(g));
}

const v = (i, k) => i.victims?.[k] || 0;
export function peopleHarmed(i) { return v(i, "dead") + v(i, "injured") + v(i, "missing"); }

// Higher = needs attention sooner. Deaths first, then children, violence, displacement, scale.
export function priority(i) {
  return v(i, "dead") * 100 + v(i, "missing") * 40 + v(i, "injured") * 10
    + (involvesChildren(i) ? 60 : 0) + (lensOf(i.category).key === "kekerasan" ? 50 : 0)
    + Math.min(v(i, "displaced"), 500) / 5 + Math.min(v(i, "affected"), 500) / 20 + v(i, "houses")
    + (i.report_count || 1);
}

export function totals(list) {
  const t = Object.fromEntries(VICTIMS.map(([k]) => [k, 0]));
  for (const i of list) for (const [k] of VICTIMS) t[k] += v(i, k);
  return t;
}

export function countBy(list, keyFn) {
  const m = new Map();
  for (const i of list) {
    const k = keyFn(i);
    if (k) m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
}

export function byLens(list) {
  return LENSES.map((l) => {
    const items = list.filter((i) => lensOf(i.category).key === l.key);
    return { lens: l, items, count: items.length, totals: totals(items) };
  });
}

export function sortCases(list, how) {
  const a = [...list];
  if (how === "terbaru") a.sort((x, y) => y.last_reported.localeCompare(x.last_reported));
  else a.sort((x, y) => priority(y) - priority(x) || y.last_reported.localeCompare(x.last_reported));
  return a;
}

export function shortKab(k) { return String(k || "").replace(/^Kabupaten /, "Kab. "); }

// `coarse` drops desa/kecamatan (used for sensitive cases in images).
export function locationText(i, coarse = false) {
  const parts = coarse ? [i.kab_kota] : [
    i.desa && (/^(desa|kelurahan|kampung)/i.test(i.desa) ? i.desa : `Desa ${i.desa}`),
    i.kecamatan && `Kec. ${i.kecamatan}`, i.kab_kota];
  const p = parts.filter(Boolean);
  return p.length ? p.join(", ") : "Jawa Barat (lokasi umum)";
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const fmtDateTime = (iso) => new Intl.DateTimeFormat("id-ID",
  { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
export const fmtUpdated = (iso) => new Intl.DateTimeFormat("id-ID",
  { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) + " WIB";

export function fmtEventDate(s) {
  if (!s) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    return new Intl.DateTimeFormat("id-ID", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" })
      .format(new Date(`${s}T00:00:00Z`));
  }
  return s;
}

export function ago(iso, now = new Date()) {
  const m = Math.max(0, Math.round((now - new Date(iso)) / 60000));
  if (m < 60) return `${m} menit lalu`;
  if (m < 60 * 36) return `${Math.round(m / 60)} jam lalu`;
  return `${Math.round(m / 1440)} hari lalu`;
}

export function lensIcon(l, size = 20) {
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${l.icon}</svg>`;
}

// "5 meninggal · 3 luka/dirawat" — only non-zero values.
export function impactText(i, keys = VICTIMS.map(([k]) => k)) {
  return VICTIMS.filter(([k]) => keys.includes(k) && v(i, k)).map(([k, l]) => `${nf.format(v(i, k))} ${l}`).join(" · ");
}

export function headlineParts(list) {
  const t = totals(list);
  const withDeaths = list.filter((i) => i.victims?.dead).length;
  const kids = list.filter(involvesChildren).length;
  const bits = [];
  if (withDeaths) bits.push(`${nf.format(withDeaths)} dengan korban jiwa`);
  if (t.displaced) bits.push(`${nf.format(t.displaced)} orang mengungsi`);
  if (kids) bits.push(`${nf.format(kids)} melibatkan anak/remaja`);
  if (!bits.length && list.length) bits.push("tidak ada laporan korban jiwa");
  return bits;
}
