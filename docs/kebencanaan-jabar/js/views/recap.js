// Rekap: one week (Monday–Sunday) or one calendar month from the archive, read as a report: headline
// numbers against the previous period, a daily trend, a topic table, regions, highlights, then the
// cases grouped by topic and folded. A second tab puts the same period on the map.
// Past incidents (follow-ups of an event a week or more before the first report) are listed apart, not counted.
import {
  LENS, addDays, byLens, countBy, dayDate, esc, fmtUpdated, fmtYmd, headlineParts, impactText,
  lensIcon, lensOf, nf, shortKab, sortCases, totals, wibDay,
} from "../model.js";
import { caseHref, caseList, columnChart, contentNote, href, select } from "../ui.js";
import { mapSection } from "./map.js";

export const KIND = {
  minggu: { label: "Rekap mingguan", prev: "Pekan sebelumnya", next: "Pekan berikutnya", ago: "pekan lalu", when: "pada pekan ini" },
  bulan: { label: "Rekap bulanan", prev: "Bulan sebelumnya", next: "Bulan berikutnya", ago: "bulan lalu", when: "pada bulan ini" },
};

const YMD_RE = /^\d{4}-\d{2}-\d{2}$/;
const monday = (ymd) => addDays(ymd, -((dayDate(ymd).getUTCDay() + 6) % 7));
const shiftMonth = (ym, n) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7);
};
const rangeLabel = (start, end) => {
  const sameYear = start.slice(0, 4) === end.slice(0, 4);
  return `${fmtYmd(start, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) })} – ${fmtYmd(end, { day: "numeric", month: "short", year: "numeric" })}`;
};

// "#/rekap/minggu/<any day of the week>": without a date, the last finished week.
// "#/rekap/bulan/<YYYY-MM>": without a month, the current one.
export function recapPeriod(kind, key, today = wibDay(new Date())) {
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

// Months to load: the period and the one before it (for the comparison).
export function recapMonths(period) {
  return [...new Set([...period.months, ...recapPeriod(period.kind, period.prev).months])];
}

export const recapPath = (kind, key, tab = "") => `/rekap/${kind}${key ? `/${key}` : ""}${tab ? `/${tab}` : ""}`;

function incidentsIn(archive, period) {
  const all = period.months.flatMap((m) => archive.months.get(m) || [])
    .filter((i) => { const d = wibDay(i.first_reported); return period.start <= d && d <= period.end; });
  return { list: all.filter((i) => !i.past), past: all.filter((i) => i.past) };
}

const archiveStart = (archive) => (archive.index?.months?.[0] ? wibDay(archive.index.months[0].first_reported) : null);

// "+12" / "−5" / "sama", against the previous period.
function delta(cur, prev) {
  const d = cur - prev;
  return d === 0 ? "sama" : `${d > 0 ? "+" : "−"}${nf.format(Math.abs(d))}`;
}

const titleOf = (p) => `${KIND[p.kind].label} ${p.label}`;

// Band with the period stepper, then the Laporan / Peta tabs: the same on both tabs.
function head(data, archive, period, tab) {
  const k = KIND[period.kind];
  const since = archiveStart(archive);
  const prevOk = !since || period.start > since;
  const step = (key, label, dir) => key
    ? `<a class="step" href="${href(recapPath(period.kind, key, tab))}" aria-label="${label}" title="${label}">${dir}</a>`
    : `<span class="step off" aria-hidden="true">${dir}</span>`;
  const notes = [
    period.current && `Periode berjalan; data hingga ${esc(fmtUpdated(data.generated_at))}.`,
    since && period.start < since && `Arsip dimulai ${esc(fmtYmd(since, { day: "numeric", month: "long", year: "numeric" }))}.`,
    archive.failed && "Sebagian arsip gagal dimuat. Muat ulang halaman untuk mencoba lagi.",
  ].filter(Boolean);
  const tabs = [["", "Laporan"], ["peta", "Peta"]].map(([t, l]) =>
    `<a href="${href(recapPath(period.kind, period.key, t))}"${t === tab ? ' aria-current="page"' : ""}>${l}</a>`).join("");
  return `
  <header class="band recap-band">
    <p class="eyebrow">${k.label}</p>
    <div class="stepper">
      ${step(prevOk && period.prev, k.prev, "‹")}
      <h1 tabindex="-1">${esc(period.label)}</h1>
      ${step(period.next, k.next, "›")}
    </div>
    ${notes.length ? `<p class="meta">${notes.join(" ")}</p>` : ""}
  </header>
  <nav class="subtabs" aria-label="Tampilan rekap">${tabs}</nav>`;
}

// Weekly: seven labelled columns. Monthly: one column per day, Mondays ticked, the peak labelled.
function trend(period, list) {
  const today = wibDay(new Date());
  const day = new Map(countBy(list, (i) => wibDay(i.first_reported)));
  const days = [];
  for (let d = period.start; d <= period.end; d = addDays(d, 1)) days.push(d);
  const weekly = period.kind === "minggu";
  const peak = Math.max(0, ...days.map((d) => day.get(d) || 0));
  return columnChart(days.map((d) => {
    const n = day.get(d) || 0;
    return {
      value: n, future: d > today,
      full: `${fmtYmd(d, { weekday: "long", day: "numeric", month: "long" })}: ${nf.format(n)} insiden`,
      tick: weekly ? fmtYmd(d, { weekday: "short" }) : (dayDate(d).getUTCDay() === 1 || d === period.start ? fmtYmd(d, { day: "numeric" }) : ""),
      showValue: weekly || (n === peak && n > 0),
    };
  }), { label: weekly ? "Jumlah insiden per hari" : "Jumlah insiden per hari dalam bulan ini", dense: !weekly });
}

export function recap({ data, archive, period, query }) {
  const k = KIND[period.kind];
  const self = recapPath(period.kind, period.key);
  const { list, past } = incidentsIn(archive, period);
  const since = archiveStart(archive);
  // Compare finished periods only, and only when the archive covers the whole previous one.
  const prevPeriod = recapPeriod(period.kind, period.prev);
  const prev = !period.current && since && prevPeriod.start >= since ? incidentsIn(archive, prevPeriod).list : null;

  const wil = query.get("wil") || "";
  const open = LENS[query.get("lensa")] ? query.get("lensa") : "";
  const t = totals(list);
  const pt = prev && totals(prev);
  const groups = byLens(list).filter((g) => g.count);
  const prevBy = prev && Object.fromEntries(byLens(prev).map((g) => [g.lens.key, g.count]));
  const kabs = countBy(list, (i) => i.kab_kota).slice(0, 6);
  const top = sortCases(list, "dampak").slice(0, 5);
  const inWil = list.filter((i) => !wil || i.kab_kota === wil);

  const FIG = [["dead", "meninggal"], ["injured", "luka / dirawat"], ["missing", "hilang"], ["displaced", "mengungsi"]];
  const figs = `<dl class="figures">${FIG.map(([key, label]) =>
    `<div class="fig${t[key] ? "" : " zero"}${key === "dead" && t[key] ? " dead" : ""}"><dt>${label}</dt><dd>${nf.format(t[key])}</dd>${
      pt ? `<dd class="fig-prev">${esc(k.ago)}: ${nf.format(pt[key])}</dd>` : ""}</div>`).join("")}</dl>`;

  const topicRows = groups.map((g) => `<tr style="--lc:var(--l-${g.lens.key})">
      <th scope="row"><a href="${href(self, { wil, lensa: g.lens.key })}" data-scroll="grp-${g.lens.key}">${lensIcon(g.lens, 18)}<span>${esc(g.lens.short)}</span></a></th>
      <td><b>${nf.format(g.count)}</b>${prevBy ? ` <span class="d">${delta(g.count, prevBy[g.lens.key] || 0)}</span>` : ""}</td>
      <td>${g.totals.dead ? nf.format(g.totals.dead) : "–"}</td>
      <td>${g.totals.displaced ? nf.format(g.totals.displaced) : "–"}</td>
    </tr>`).join("");

  const kabRows = kabs.map(([w, n]) => {
    const l = LENS[countBy(list.filter((i) => i.kab_kota === w), (i) => lensOf(i.category).key)[0][0]];
    return `<tr>
      <th scope="row"><a href="${href(self, { wil: w })}" data-scroll="h-kasus">${esc(shortKab(w))}</a></th>
      <td><b>${nf.format(n)}</b></td>
      <td><span class="tl" style="--lc:var(--l-${l.key})">${lensIcon(l, 16)}${esc(l.short)}</span></td>
    </tr>`;
  }).join("");

  const SHOWN = 8;
  const groupBlocks = byLens(inWil).filter((g) => g.count).map((g) => {
    const cases = sortCases(g.items, "dampak");
    const impact = impactText({ victims: g.totals }, ["dead", "injured", "missing", "displaced"]);
    return `<details class="grp" id="grp-${g.lens.key}" style="--lc:var(--l-${g.lens.key})"${open === g.lens.key ? " open" : ""}>
      <summary>${lensIcon(g.lens, 20)}<span class="grp-name">${esc(g.lens.label)}</span><span class="grp-n">${nf.format(g.count)}</span>
        ${impact ? `<span class="grp-impact">${esc(impact)}</span>` : ""}</summary>
      ${g.lens.sensitive ? contentNote() : ""}
      ${caseList(data, cases.slice(0, SHOWN), { detail: true, archived: true })}
      ${cases.length > SHOWN ? `<details class="grp-more"><summary>Tampilkan ${nf.format(cases.length - SHOWN)} kasus lainnya</summary>
        ${caseList(data, cases.slice(SHOWN), { detail: true, archived: true })}</details>` : ""}
    </details>`;
  }).join("");

  const html = `
  ${head(data, archive, period, "")}

  <section class="sec stats" aria-labelledby="h-total">
    <div class="kpi">
      <h2 id="h-total" class="count-head"><b>${nf.format(list.length)}</b> insiden</h2>
      ${prev ? `<p class="kpi-delta">${esc(delta(list.length, prev.length))} dibanding ${esc(k.ago)} (${nf.format(prev.length)})</p>` : ""}
    </div>
    ${list.length ? `<p class="lede">${esc(headlineParts(list).join(" · "))}</p>` : ""}
    ${figs}
    <h2 class="sub">Insiden per hari</h2>
    ${trend(period, list)}
  </section>

  ${list.length ? `<section class="sec" aria-label="Topik dan wilayah">
    <div class="split">
      <div class="tbl">
        <h2 id="h-topik">Per topik</h2>
        <table class="rtable" aria-labelledby="h-topik">
          <thead><tr><th scope="col">Topik</th><th scope="col">Insiden${prev ? ` <span class="d">vs ${esc(k.ago)}</span>` : ""}</th><th scope="col">Meninggal</th><th scope="col">Mengungsi</th></tr></thead>
          <tbody>${topicRows}</tbody>
        </table>
      </div>
      <div class="tbl">
        <h2 id="h-wil">Wilayah terbanyak</h2>
        <table class="rtable" aria-labelledby="h-wil">
          <thead><tr><th scope="col">Kab/kota</th><th scope="col">Insiden</th><th scope="col">Topik terbanyak</th></tr></thead>
          <tbody>${kabRows}</tbody>
        </table>
      </div>
    </div>
  </section>

  <section class="sec" aria-labelledby="h-prio">
    <h2 id="h-prio">Sorotan</h2>
    <p class="muted">Lima kasus dengan dampak terbesar.</p>
    ${caseList(data, top, { archived: true })}
  </section>` : ""}

  <section class="sec" aria-labelledby="h-kasus">
    <h2 id="h-kasus">Kasus per topik</h2>
    ${list.length ? `<div class="filters">
      ${select("f-wil", "Wilayah", [["", "Semua wilayah"], ...countBy(list, (i) => i.kab_kota).map(([w, n]) => [w, `${shortKab(w)} (${n})`])], wil)}
    </div>` : ""}
    ${groupBlocks || `<p class="empty">${list.length ? "Tidak ada kasus di wilayah ini." : `Belum ada insiden tercatat ${k.when}.`}</p>`}
    ${past.length ? `<details class="grp past-list">
      <summary><span class="grp-name">Kejadian lampau, tidak dihitung</span><span class="grp-n">${nf.format(past.length)}</span></summary>
      <p class="muted">Kejadiannya seminggu atau lebih sebelum pertama kali diberitakan: tindak lanjut, proses hukum, atau kisah ulang.</p>
      ${caseList(data, sortCases(past, "terbaru"), { archived: true })}
    </details>` : ""}
    <p class="method-note">Insiden dihitung pada tanggal pertama kali diberitakan (WIB). Kejadian yang diberitakan lagi setelah jeda lebih dari sehari dapat tercatat dua kali.</p>
  </section>`;

  return {
    title: titleOf(period),
    html,
    share: { kind: "recap", period, label: k.label, list },
    filters: { keys: { "f-wil": "wil" } },
  };
}

export function recapMap({ data, archive, period, query }) {
  const report = recapPath(period.kind, period.key);
  const { list } = incidentsIn(archive, period);
  const map = mapSection({
    data, list, query,
    caseLink: (i) => caseHref(i, true),
    listLink: (kab, lensa) => href(report, { wil: kab, lensa }),
    scope: period.current ? `${KIND[period.kind].when} (sejauh ini)` : KIND[period.kind].when,
  });
  return {
    title: `${titleOf(period)} · Peta`,
    html: `${head(data, archive, period, "peta")}${map.html}`,
    share: { kind: "recap", period, label: KIND[period.kind].label, list },
    filters: { keys: {}, lensParam: "lensa" },
    themed: true,
    mount: map.mount,
    unmount: map.unmount,
  };
}
