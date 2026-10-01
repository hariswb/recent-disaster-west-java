// L1 Topik: one psychosocial lens — its numbers, kinds of events, and cases.
import { LENS, catLabel, countBy, esc, lensIcon, lensOf, nf, shortKab, sortCases, totals } from "../model.js";
import { barChart, caseList, contentNote, crumbs, figures, href, sectionHead, select, sortToggle } from "../ui.js";

export function topic({ data, list, params, query }) {
  const lens = LENS[params.lens];
  if (!lens) return null;
  const inLens = list.filter((i) => lensOf(i.category).key === lens.key);
  const kat = query.get("kat") || "";
  const wil = query.get("wil") || "";
  const urut = query.get("urut") || "dampak";
  const shown = sortCases(inLens.filter((i) => (!kat || i.category === kat) && (!wil || i.kab_kota === wil)), urut);
  const self = `/topik/${lens.key}`;
  const keep = { wil, urut: urut === "dampak" ? "" : urut };

  const catCounts = new Map(countBy(inLens, (i) => i.category));
  const catRows = [...new Set([...lens.cats, ...catCounts.keys()])]
    .map((c) => ({ c, n: catCounts.get(c) || 0 }))
    .sort((a, b) => b.n - a.n)
    .map(({ c, n }) => ({ label: catLabel(data, c), value: n, lens: lens.key, pressed: kat === c, scroll: "h-kasus",
      link: href(self, { ...keep, kat: kat === c ? "" : c }) }));
  const kabs = countBy(inLens, (i) => i.kab_kota);

  const html = `
  <header class="band" style="--ld:var(--d-${lens.key})">
    ${crumbs([["Ringkasan", "#/"], [lens.label]])}
    <p class="eyebrow">${lensIcon(lens, 18)} Topik</p>
    <h1 tabindex="-1">${esc(lens.label)}</h1>
    <p class="lede">${esc(lens.desc)}</p>
  </header>
  ${lens.sensitive ? contentNote() : ""}

  <section class="sec stats" aria-labelledby="h-total">
    <h2 id="h-total" class="count-head"><b>${nf.format(inLens.length)}</b> insiden dalam ${data.window_hours} jam terakhir</h2>
    ${figures(totals(inLens))}
    <h2 class="sub">Jenis kejadian</h2>
    ${barChart(catRows, { label: "Jumlah insiden menurut jenis kejadian" })}
  </section>

  <section class="sec" aria-labelledby="h-kasus">
    ${sectionHead("h-kasus", `Kasus <span class="count">${nf.format(shown.length)}</span>`)}
    <div class="filters">
      ${select("f-kat", "Jenis", [["", "Semua jenis"], ...[...catCounts.keys()].map((c) => [c, catLabel(data, c)])], kat)}
      ${select("f-wil", "Wilayah", [["", "Semua wilayah"], ...kabs.map(([k]) => [k, shortKab(k)])], wil)}
      ${sortToggle(urut)}
    </div>
    ${caseList(data, shown, { detail: true,
      empty: inLens.length ? "Tidak ada kasus yang cocok dengan saringan." : "Tidak ada laporan untuk topik ini dalam 24 jam terakhir." })}
  </section>`;

  return {
    title: lens.label,
    html,
    share: { kind: "topic", lens, list: inLens, filtered: shown, kat, wil },
    filters: { keys: { "f-kat": "kat", "f-wil": "wil" } },
  };
}
