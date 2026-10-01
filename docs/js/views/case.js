// L2 Kasus: one incident — metadata on top, one key-value table, then the description.
import {
  ENTITY_LABEL, SUPPORT_LINE, catLabel, esc, fmtDateTime, fmtEventDate, groupsAffected, impactText, lensIcon, lensOf,
  locationText,
} from "../model.js";
import { contentNote, crumbs, href } from "../ui.js";

export function caseView({ data, params }) {
  const i = data.incidents.find((x) => x.id === params.id);
  if (!i) return null;
  const l = lensOf(i.category);
  const impact = impactText(i);
  const groups = groupsAffected(i);
  const places = (i.affected_entities || []).filter((e) => e.type !== "lainnya" || e.name)
    .map((e) => e.name || ENTITY_LABEL[e.type] || e.type);
  const event = fmtEventDate(i.event_time);
  const EXT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';
  const sources = i.sources.map((s) => `<a class="btn src" href="${esc(s.url)}" target="_blank" rel="noopener">
    Baca berita di ${esc(s.name)}${EXT}<span class="sr-only"> (tab baru)</span></a>`).join("");

  const rows = [
    ["Topik", `<a href="${href(`/topik/${l.key}`)}">${esc(l.label)}</a>`],
    ["Jenis", esc(catLabel(data, i.category)) + (i.subcategory ? ` (${esc(i.subcategory.replace(/_/g, " "))})` : "")],
    ["Lokasi", esc(locationText(i))],
    event && ["Waktu kejadian", esc(event)],
    ["Dampak", impact ? esc(impact) : `<span class="muted">Belum disebutkan dalam berita</span>`],
    groups.length && ["Kelompok terdampak", esc(groups.join(", "))],
    places.length && ["Tempat/objek", esc([...new Set(places)].join(", "))],
  ].filter(Boolean);

  const html = `
  <header class="band" style="--ld:var(--d-${l.key})">
    ${crumbs([["Ringkasan", "#/"], [l.label, href(`/topik/${l.key}`)], ["Kasus"]])}
    <p class="eyebrow">${lensIcon(l, 18)} ${esc(catLabel(data, i.category))}</p>
    <h1 tabindex="-1">${esc(i.title)}</h1>
    <p class="meta">Dilaporkan ${esc(fmtDateTime(i.first_reported))} WIB · ${i.sources.length} sumber berita</p>
    <div class="src-btns">${sources}</div>
  </header>
  ${l.sensitive ? contentNote() : ""}

  <section class="sec" aria-labelledby="h-rincian">
    <h2 id="h-rincian">Rincian</h2>
    <dl class="kv">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
  </section>

  ${i.summary && i.summary !== i.title ? `<section class="sec" aria-labelledby="h-desk">
    <h2 id="h-desk">Deskripsi</h2>
    <p class="summary">${esc(i.summary)}</p>
  </section>` : ""}
  ${l.sensitive ? `<p class="support">${esc(SUPPORT_LINE)}</p>` : ""}`;

  return { title: i.title, html, share: { kind: "case", incident: i } };
}
