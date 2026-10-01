// L1 Semua kasus: every incident, with filters.
import { LENS, LENSES, catLabel, countBy, lensOf, nf, shortKab, sortCases } from "../model.js";
import { caseList, contentNote, crumbs, lensChips, select, sortToggle } from "../ui.js";

export function cases({ data, list, query }) {
  const lensKey = LENS[query.get("lensa")] ? query.get("lensa") : "";
  const kat = query.get("kat") || "";
  const wil = query.get("wil") || "";
  const urut = query.get("urut") || "dampak";
  const lensCounts = Object.fromEntries(LENSES.map((l) => [l.key, list.filter((i) => lensOf(i.category).key === l.key).length]));
  const inLens = list.filter((i) => !lensKey || lensOf(i.category).key === lensKey);
  const shown = sortCases(inLens.filter((i) => (!kat || i.category === kat) && (!wil || i.kab_kota === wil)), urut);
  const cats = countBy(inLens, (i) => i.category);
  const kabs = countBy(inLens, (i) => i.kab_kota);

  const html = `
  <header class="band">
    ${crumbs([["Ringkasan", "#/"], ["Semua kasus"]])}
    <h1 tabindex="-1">Semua kasus</h1>
    <p class="lede">${nf.format(shown.length)} dari ${nf.format(list.length)} insiden dalam ${data.window_hours} jam terakhir</p>
  </header>
  <section class="sec" aria-label="Saringan dan daftar kasus">
    <div class="filters">
      ${lensChips(lensCounts, lensKey)}
      ${select("f-kat", "Jenis", [["", "Semua jenis"], ...cats.map(([c]) => [c, catLabel(data, c)])], kat)}
      ${select("f-wil", "Wilayah", [["", "Semua wilayah"], ...kabs.map(([k]) => [k, shortKab(k)])], wil)}
      ${sortToggle(urut)}
    </div>
    ${shown.some((i) => lensOf(i.category).sensitive) ? contentNote() : ""}
    ${caseList(data, shown, { detail: true, empty: "Tidak ada kasus yang cocok dengan saringan." })}
  </section>`;

  return {
    title: "Semua kasus",
    html,
    share: { kind: "list", lens: lensKey ? LENS[lensKey] : null, list: shown, kat, wil },
    filters: { keys: { "f-kat": "kat", "f-wil": "wil" }, lensParam: "lensa" },
  };
}
