// Small HTML building blocks shared by the views.
import { LENSES, ago, catLabel, esc, impactText, lensIcon, lensOf, nf, shortKab } from "./model.js";

const CHEVRON = '<svg class="chev" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

export function href(path, query = {}) {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== "" && v != null && v !== false));
  const s = q.toString();
  return `#${path}${s ? `?${s}` : ""}`;
}

export function crumbs(items) {
  return `<nav class="crumbs" aria-label="Lokasi halaman"><ol>${items.map(([label, link], n) =>
    n === items.length - 1
      ? `<li><span aria-current="page">${esc(label)}</span></li>`
      : `<li><a href="${link}">${esc(label)}</a></li>`).join("")}</ol></nav>`;
}

export function sectionHead(id, title, more) {
  return `<div class="sec-head"><h2 id="${id}">${title}</h2>${more ? `<a class="pill" href="${more[0]}">${esc(more[1])}</a>` : ""}</div>`;
}

// Plain figures row: big number + label, no cards.
const FIG_LABEL = { dead: "meninggal", injured: "luka / dirawat", missing: "hilang", displaced: "mengungsi" };
export function figures(t, keys = ["dead", "injured", "missing", "displaced"]) {
  return `<dl class="figures">${keys.map((k) =>
    `<div class="fig${t[k] ? "" : " zero"}${k === "dead" && t[k] ? " dead" : ""}"><dt>${FIG_LABEL[k]}</dt><dd>${nf.format(t[k] || 0)}</dd></div>`).join("")}</dl>`;
}

// Horizontal bar chart; rows = [{label, value, link?, lens?, icon?, pressed?}]. Values are direct-labelled.
export function barChart(rows, { label, unit = "insiden" } = {}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return `<ul class="chart" aria-label="${esc(label || "")}">${rows.map((r) => {
    const w = r.value ? Math.max(1.5, (r.value / max) * 100) : 0;
    const inner = `<span class="chart-label">${r.icon || ""}<span>${esc(r.label)}</span></span>
      <span class="chart-track" aria-hidden="true"><span class="chart-fill" style="width:${w}%;${r.lens ? `--lc:var(--l-${r.lens})` : ""}"></span></span>
      <span class="chart-value">${nf.format(r.value)}<span class="sr-only"> ${unit}</span></span>`;
    return `<li style="${r.lens ? `--lc:var(--l-${r.lens})` : ""}">${r.link
      ? `<a class="chart-row" href="${r.link}"${r.scroll ? ` data-scroll="${r.scroll}"` : ""}${r.pressed ? ` aria-current="true"` : ""}>${inner}</a>`
      : `<div class="chart-row">${inner}</div>`}</li>`;
  }).join("")}</ul>`;
}

// Solid topic card: icon, name, and its two largest victim figures. Nothing else.
const CARD_LABEL = { dead: "meninggal", injured: "luka", missing: "hilang", displaced: "mengungsi" };
export function lensCard(l, count, t) {
  const top = Object.keys(CARD_LABEL).filter((k) => t[k]).sort((a, b) => t[b] - t[a]).slice(0, 2)
    .map((k) => `${nf.format(t[k])} ${CARD_LABEL[k]}`).join(" · ");
  return `<li><a class="topic${count ? "" : " quiet"}" href="#/topik/${l.key}" style="--ld:var(--d-${l.key})">
    <span class="topic-icon">${lensIcon(l, 22)}</span>
    <span class="topic-name">${esc(l.label)}</span>
    <span class="topic-count">${!count ? "Tidak ada laporan" : top || "Belum ada laporan korban"}</span>
    ${CHEVRON}
  </a></li>`;
}

// Case list row. `detail` adds the impact line (topic/list level); the overview keeps rows to title + meta.
export function caseRow(data, i, { detail = false, now = new Date() } = {}) {
  const l = lensOf(i.category);
  const impact = detail ? impactText(i, ["dead", "injured", "missing", "displaced"]) : "";
  return `<li><a class="case-row" href="#/kasus/${esc(i.id)}" style="--lc:var(--l-${l.key})">
    <span class="case-body">
      <span class="case-meta">${lensIcon(l, 16)}<span>${esc(catLabel(data, i.category))} · ${esc(shortKab(i.kab_kota) || "Jawa Barat")}</span>${detail ? `<span class="muted">· ${ago(i.last_reported, now)}</span>` : ""}</span>
      <span class="case-title">${esc(i.title)}</span>
      ${impact ? `<span class="case-impact">${esc(impact)}</span>` : ""}
    </span>${CHEVRON}
  </a></li>`;
}

export function caseList(data, list, { detail = false, empty = "Tidak ada kasus yang cocok." } = {}) {
  const now = new Date();
  return list.length ? `<ul class="cases">${list.map((i) => caseRow(data, i, { detail, now })).join("")}</ul>`
    : `<p class="empty">${esc(empty)}</p>`;
}

// Styled to match the chips; the chevron is our own so it follows the theme colours.
const CARET = '<svg class="caret" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
export function select(id, label, options, value) {
  return `<label class="field" for="${id}"><span>${esc(label)}</span><span class="select"><select id="${id}">${options.map(([v, l]) =>
    `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>${CARET}</span></label>`;
}

export function sortToggle(value) {
  return `<div class="seg" role="group" aria-label="Urutkan">${[["dampak", "Dampak terbesar"], ["terbaru", "Terbaru"]].map(([k, l]) =>
    `<button type="button" id="sort-${k}" data-sort="${k}" aria-pressed="${value === k}">${l}</button>`).join("")}</div>`;
}

export function lensChips(counts, value) {
  return `<div class="chips" role="group" aria-label="Topik">
    <button type="button" class="chip" id="lens-all" data-lens="" aria-pressed="${!value}">Semua</button>
    ${LENSES.filter((l) => counts[l.key] || l.key === value).map((l) =>
      `<button type="button" class="chip" id="lens-${l.key}" data-lens="${l.key}" aria-pressed="${value === l.key}" style="--lc:var(--l-${l.key})">${lensIcon(l, 16)}${esc(l.short)} <span class="n">${counts[l.key] || 0}</span></button>`).join("")}
  </div>`;
}

export function contentNote() {
  return `<p class="note" role="note"><strong>Catatan konten:</strong> memuat kasus kekerasan. Nama penyintas tidak ditampilkan.</p>`;
}
