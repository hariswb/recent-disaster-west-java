// L1 Rekap: the last 7 days, one week or one month from the archive — totals, trend, topics, kinds, regions and every case.
// Past incidents (follow-ups of an event a week or more before the first report) are listed apart, not counted.
import {
  LENS, LENSES, addDays, byLens, catLabel, countBy, dayDate, esc, fmtDateTime, fmtUpdated, fmtYmd, headlineParts, lensIcon, lensOf,
  nf, shortKab, sortCases, totals, wibDay,
} from "../model.js";
import { barChart, caseList, contentNote, crumbs, figures, href, lensChips, sectionHead, select, sortToggle } from "../ui.js";

export const KIND = {
  "7-hari": { label: "Rekap 7 hari terakhir", tab: "7 hari", when: "dalam 7 hari terakhir" },
  minggu: { label: "Rekap mingguan", tab: "Mingguan", prev: "Pekan sebelumnya", next: "Pekan berikutnya", when: "pada pekan ini" },
  bulan: { label: "Rekap bulanan", tab: "Bulanan", prev: "Bulan sebelumnya", next: "Bulan berikutnya", when: "pada bulan ini" },
};

const YMD_RE = /^\d{4}-\d{2}-\d{2}$/;
const monday = (ymd) => addDays(ymd, -((dayDate(ymd).getUTCDay() + 6) % 7));
const shiftMonth = (ym, n) => {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
};
const short = (ymd) => fmtYmd(ymd, { day: "numeric", month: "short" });

const rangeLabel = (start, end) => {
  const sameYear = start.slice(0, 4) === end.slice(0, 4);
  return `${fmtYmd(start, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) })} – ${fmtYmd(end, { day: "numeric", month: "short", year: "numeric" })}`;
};

// "#/rekap/7-hari": a rolling 7×24 hours up to the latest update (`asOf`).
// "#/rekap/minggu/<any day of the week>": Monday–Sunday; without a key, the last finished week.
// "#/rekap/bulan/<YYYY-MM>": without a key, the current month.
export function recapPeriod(kind, key, { today = wibDay(new Date()), asOf = new Date().toISOString() } = {}) {
  if (kind === "7-hari") {
    if (key) return null;
    const to = new Date(asOf);
    const from = new Date(to.getTime() - 7 * 864e5);
    const start = wibDay(from), end = wibDay(to);
    return { kind, key: "", start, end, from, to, label: rangeLabel(start, end), prev: null, next: null, current: true,
      months: [...new Set([start.slice(0, 7), end.slice(0, 7)])] };
  }
  if (kind === "minggu") {
    if (key && (!YMD_RE.test(key) || isNaN(dayDate(key)))) return null;
    const start = monday(key || addDays(today, -7));
    const end = addDays(start, 6);
    return {
      kind, key: start, start, end, label: rangeLabel(start, end),
      prev: addDays(start, -7), next: end < today ? addDays(start, 7) : null,
      current: start <= today && today <= end, months: [...new Set([start.slice(0, 7), end.slice(0, 7)])],
    };
  }
  if (kind === "bulan") {
    const ym = key || today.slice(0, 7);
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(ym)) return null;
    const start = `${ym}-01`;
    const end = addDays(`${shiftMonth(ym, 1)}-01`, -1);
    return {
      kind, key: ym, start, end, label: fmtYmd(start, { month: "long", year: "numeric" }),
      prev: shiftMonth(ym, -1), next: end < today ? shiftMonth(ym, 1) : null,
      current: start <= today && today <= end, months: [ym],
    };
  }
  return null;
}

export const recapPath = (kind, key) => `/rekap/${kind}${key ? `/${key}` : ""}`;

// Weekly and 7 days: one bar per day. Monthly: one bar per week (clipped to the month), linking to that week's recap.
// The rolling 7 days start and end mid-day, so its first and last days are partial.
function trend(period, list, today) {
  const day = new Map(countBy(list, (i) => wibDay(i.first_reported)));
  if (period.kind !== "bulan") {
    const days = [];
    for (let d = period.start; d <= period.end; d = addDays(d, 1)) days.push(d);
    return days.map((d) => ({
      label: fmtYmd(d, { weekday: "short", day: "numeric", month: "short" })
        + (d > today ? " (belum)" : period.from && (d === period.start || d === period.end) ? " (sebagian)" : ""),
      value: day.get(d) || 0,
    }));
  }
  const rows = [];
  for (let s = period.start; s <= period.end; s = addDays(monday(s), 7)) {
    const e = [addDays(monday(s), 6), period.end].sort()[0];
    let n = 0;
    for (let d = s; d <= e; d = addDays(d, 1)) n += day.get(d) || 0;
    rows.push({ label: (s === e ? short(s) : `${fmtYmd(s, { day: "numeric" })}–${short(e)}`) + (s > today ? " (belum)" : ""), value: n,
      link: href(recapPath("minggu", monday(s))) });
  }
  return rows;
}

export function recap({ data, archive, period, query }) {
  const today = wibDay(new Date());
  const k = KIND[period.kind];
  const self = recapPath(period.kind, period.key);
  const inPeriod = period.months.flatMap((m) => archive.months.get(m) || [])
    .filter(period.from
      ? (i) => { const t = new Date(i.first_reported); return period.from < t && t <= period.to; }
      : (i) => { const d = wibDay(i.first_reported); return period.start <= d && d <= period.end; });
  const list = inPeriod.filter((i) => !i.past);
  const past = sortCases(inPeriod.filter((i) => i.past), "terbaru");

  const lensKey = LENS[query.get("lensa")] ? query.get("lensa") : "";
  const kat = query.get("kat") || "";
  const wil = query.get("wil") || "";
  const urut = query.get("urut") || "dampak";
  const keep = { lensa: lensKey, kat, wil, urut: urut === "dampak" ? "" : urut };
  const lensCounts = Object.fromEntries(LENSES.map((l) => [l.key, list.filter((i) => lensOf(i.category).key === l.key).length]));
  const inLens = list.filter((i) => !lensKey || lensOf(i.category).key === lensKey);
  const shown = sortCases(inLens.filter((i) => (!kat || i.category === kat) && (!wil || i.kab_kota === wil)), urut);

  const groups = byLens(list).filter((g) => g.count || g.lens.key !== "lainnya");
  const cats = countBy(list, (i) => i.category);
  const kabs = countBy(list, (i) => i.kab_kota);
  const top = sortCases(list, "dampak").slice(0, 5);

  const since = archive.index?.months?.[0] ? wibDay(archive.index.months[0].first_reported) : null;
  const prevOk = period.prev && (!since || period.start > since);
  // Switching tabs keeps the reader near the same dates; from a current period, go to each tab's default.
  const tabKey = {
    "7-hari": "",
    minggu: period.kind === "bulan" && !period.current ? monday(period.end) : "",
    bulan: period.kind === "minggu" ? period.start.slice(0, 7) : "",
  };
  const tabs = Object.entries(KIND).map(([key, v]) => key === period.kind
    ? `<a aria-current="page" href="${href(self)}">${v.tab}</a>`
    : `<a href="${href(recapPath(key, tabKey[key]))}">${v.tab}</a>`).join("");

  const notes = [
    period.from ? `${esc(fmtDateTime(period.from.toISOString()))} – ${esc(fmtUpdated(period.to.toISOString()))}.`
      : period.current && `Periode berjalan; data hingga ${esc(fmtUpdated(data.generated_at))}.`,
    since && period.start < since && `Arsip dimulai ${esc(fmtYmd(since, { day: "numeric", month: "long", year: "numeric" }))}; kejadian sebelumnya tidak tercatat.`,
    archive.failed && "Sebagian arsip gagal dimuat. Muat ulang halaman untuk mencoba lagi.",
  ].filter(Boolean);

  const html = `
  <header class="band">
    ${crumbs([["Ringkasan", "#/"], [k.label]])}
    <p class="eyebrow">${k.label}</p>
    <h1 tabindex="-1">${esc(period.label)}</h1>
    ${notes.length ? `<p class="meta">${notes.join(" ")}</p>` : ""}
  </header>
  <nav class="period" aria-label="Pilih periode rekap">
    <div class="seg" role="group" aria-label="Jenis rekap">${tabs}</div>
    <div class="period-step"${period.prev || period.next ? "" : " hidden"}>
      ${prevOk ? `<a class="pill" href="${href(recapPath(period.kind, period.prev))}">← ${k.prev}</a>` : ""}
      ${period.next ? `<a class="pill" href="${href(recapPath(period.kind, period.next))}">${k.next} →</a>` : ""}
    </div>
  </nav>

  <section class="sec stats" aria-labelledby="h-total">
    <h2 id="h-total" class="count-head"><b>${nf.format(list.length)}</b> insiden</h2>
    ${list.length ? `<p class="lede">${esc(headlineParts(list).join(" · "))}</p>` : ""}
    ${figures(totals(list))}
    <div class="split">
      <div>
        <h2 class="sub">${period.kind === "bulan" ? "Per pekan" : "Per hari"}</h2>
        ${barChart(trend(period, list, today), { label: period.kind === "bulan" ? "Jumlah insiden per pekan" : "Jumlah insiden per hari" })}
      </div>
      <div>
        <h2 class="sub">Berdasarkan topik</h2>
        ${barChart(groups.map((g) => ({ label: g.lens.label, value: g.count, lens: g.lens.key, icon: lensIcon(g.lens, 18),
          pressed: lensKey === g.lens.key, scroll: "h-kasus", link: href(self, { ...keep, lensa: lensKey === g.lens.key ? "" : g.lens.key, kat: "" }) })),
          { label: "Jumlah insiden menurut topik" })}
      </div>
    </div>
  </section>

  <section class="sec" aria-labelledby="h-prio">
    ${sectionHead("h-prio", "Perlu perhatian")}
    ${caseList(data, top, { archived: true, empty: `Belum ada insiden tercatat ${k.when}.` })}
  </section>

  <section class="sec" aria-labelledby="h-jenis">
    <div class="split">
      <div>
        <h2 class="sub" id="h-jenis">Jenis kejadian terbanyak</h2>
        ${cats.length ? barChart(cats.slice(0, 8).map(([c, n]) => ({ label: catLabel(data, c), value: n, lens: lensOf(c).key,
          pressed: kat === c, scroll: "h-kasus", link: href(self, { ...keep, lensa: "", kat: kat === c ? "" : c }) })),
          { label: "Jenis kejadian dengan insiden terbanyak" }) : `<p class="empty">Belum ada data.</p>`}
      </div>
      <div>
        <h2 class="sub" id="h-wil">Wilayah terbanyak</h2>
        ${kabs.length ? barChart(kabs.slice(0, 8).map(([w, n]) => ({ label: shortKab(w), value: n, pressed: wil === w, scroll: "h-kasus",
          link: href(self, { ...keep, wil: wil === w ? "" : w }) })), { label: "Kabupaten/kota dengan insiden terbanyak" })
          : `<p class="empty">Belum ada lokasi.</p>`}
      </div>
    </div>
  </section>

  <section class="sec" aria-labelledby="h-kasus">
    ${sectionHead("h-kasus", `Semua kasus <span class="count">${nf.format(shown.length)}</span>`)}
    <div class="filters">
      ${lensChips(lensCounts, lensKey)}
      ${select("f-kat", "Jenis", [["", "Semua jenis"], ...countBy(inLens, (i) => i.category).map(([c]) => [c, catLabel(data, c)])], kat)}
      ${select("f-wil", "Wilayah", [["", "Semua wilayah"], ...countBy(inLens, (i) => i.kab_kota).map(([w]) => [w, shortKab(w)])], wil)}
      ${sortToggle(urut)}
    </div>
    ${shown.some((i) => lensOf(i.category).sensitive) ? contentNote() : ""}
    ${caseList(data, shown, { detail: true, archived: true, empty: list.length ? "Tidak ada kasus yang cocok dengan saringan." : `Belum ada insiden tercatat ${k.when}.` })}
    ${past.length ? `<details class="past-list">
      <summary>${nf.format(past.length)} berita tentang kejadian lampau, tidak dihitung</summary>
      <p class="muted">Kejadiannya seminggu atau lebih sebelum pertama kali diberitakan: tindak lanjut, proses hukum, atau kisah ulang.</p>
      ${caseList(data, past, { archived: true })}
    </details>` : ""}
    <p class="method-note">Insiden dihitung pada tanggal pertama kali diberitakan (WIB). Kejadian yang diberitakan lagi setelah jeda lebih dari sehari dapat tercatat dua kali.</p>
  </section>`;

  return {
    title: `${k.label} ${period.label}`,
    html,
    share: { kind: "recap", period, label: k.label, list },
    filters: { keys: { "f-kat": "kat", "f-wil": "wil" }, lensParam: "lensa" },
  };
}
