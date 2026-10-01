// L0 Ringkasan: the whole province at a glance. Details come later, on the topic and case pages.
import { byLens, countBy, esc, fmtUpdated, headlineParts, lensIcon, nf, shortKab, sortCases, totals } from "../model.js";
import { barChart, caseList, figures, href, lensCard, sectionHead } from "../ui.js";

export function overview({ data, list }) {
  const groups = byLens(list).filter((g) => g.count || g.lens.key !== "lainnya");
  const top = sortCases(list, "dampak").slice(0, 5);
  const kabs = countBy(list, (i) => i.kab_kota).slice(0, 5);

  const html = `
  <section class="sec stats" aria-labelledby="h-total">
    <p class="stamp">Diperbarui ${esc(fmtUpdated(data.generated_at))}</p>
    <h1 id="h-total" tabindex="-1">${nf.format(list.length)} insiden tercatat</h1>
    <p class="lede">${esc(headlineParts(list).join(" · "))}</p>
    ${figures(totals(list))}
    <h2 class="sub">Menurut topik</h2>
    ${barChart(groups.map((g) => ({ label: g.lens.label, value: g.count, lens: g.lens.key, icon: lensIcon(g.lens, 18) })), { label: "Jumlah insiden menurut topik" })}
  </section>

  <section class="sec" aria-labelledby="h-topik">
    ${sectionHead("h-topik", "Topik")}
    <ul class="topics">${groups.map((g) => lensCard(g.lens, g.count)).join("")}</ul>
  </section>

  <section class="sec" aria-labelledby="h-prio">
    ${sectionHead("h-prio", "Perlu perhatian", [href("/kasus"), "Lihat semua"])}
    ${caseList(data, top, { empty: "Belum ada insiden dalam periode ini." })}
  </section>

  <section class="sec" aria-labelledby="h-wil">
    ${sectionHead("h-wil", "Wilayah terbanyak")}
    ${kabs.length ? barChart(kabs.map(([k, n]) => ({ label: shortKab(k), value: n, link: href("/kasus", { wil: k }) })), { label: "Lima kabupaten/kota dengan insiden terbanyak" })
      : `<p class="empty">Belum ada lokasi.</p>`}
  </section>`;

  return { title: "Ringkasan", html, share: { kind: "overview", list } };
}
