// Share-as-image: 1080×1440 (3:4) PNGs for each level, rendered client-side with html-to-image.
import {
  SUPPORT_LINE, TZ, byLens, catLabel, countBy, esc, fmtEventDate, groupsAffected, impactText, lensIcon,
  lensOf, locationText, nf, shortKab, sortCases, totals,
} from "./model.js";

const LIB = "https://cdn.jsdelivr.net/npm/html-to-image@1.11.13/dist/html-to-image.js";
const W = 1080, H = 1440;
let libPromise = null;

function loadLib() {
  if (window.htmlToImage) return Promise.resolve(window.htmlToImage);
  libPromise ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = LIB;
    s.onload = () => resolve(window.htmlToImage);
    s.onerror = () => { libPromise = null; reject(new Error("pustaka gambar gagal dimuat")); };
    document.head.append(s);
  });
  return libPromise;
}

// Our own @font-face rules with the woff2 files inlined, so html-to-image never has to
// scan stylesheets (Leaflet's cross-origin CSS would throw SecurityErrors).
const FONTS = [
  ["Atkinson Hyperlegible Next", "../fonts/atkinson-next-latin.woff2"],
  ["Atkinson Hyperlegible Next", "../fonts/atkinson-next-latin-ext.woff2"],
  ["Plus Jakarta Sans", "../fonts/jakarta-latin.woff2"],
  ["Plus Jakarta Sans", "../fonts/jakarta-latin-ext.woff2"],
];
let fontCss = null;
function fontEmbedCSS() {
  fontCss ??= Promise.all(FONTS.map(async ([family, url]) => {
    const blob = await (await fetch(url)).blob();
    const data = await new Promise((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.readAsDataURL(blob);
    });
    return `@font-face{font-family:"${family}";font-weight:200 800;src:url(${data}) format("woff2");}`;
  })).then((rules) => rules.join("\n")).catch((e) => { fontCss = null; throw e; });
  return fontCss;
}

const SITE = "kondisi.id/kebencanaan-jabar";

function stamp(data) {
  const g = new Date(data.generated_at);
  const day = new Intl.DateTimeFormat("id-ID", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" }).format(g);
  const time = new Intl.DateTimeFormat("id-ID", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }).format(g);
  return `${data.window_hours} jam s.d. ${day}, ${time} WIB`;
}

// Every image: a coloured band (brand, date, title) over a light body, then the method line.
function frame(data, { band, body, lens = null, sensitive = false }) {
  const style = lens ? ` style="--lc:var(--l-${lens.key});--ld:var(--d-${lens.key})"` : "";
  return `<div class="story"${style}>
    <div class="st-band">
      <div class="st-head"><span class="st-brand"><span class="st-dot"></span>Kebencanaan Jawa Barat</span><span>${esc(stamp(data))}</span></div>
      ${band}
    </div>
    <div class="st-body">${body}</div>
    <div class="st-foot">
      ${sensitive ? `<p class="st-support">${esc(SUPPORT_LINE)}</p>` : ""}
      <p class="st-url">${SITE}</p>
    </div>
  </div>`;
}

const FIG = { dead: "meninggal", injured: "luka / dirawat", missing: "hilang", displaced: "mengungsi", affected: "terdampak", houses: "rumah / bangunan" };
function figures(t, keys = ["dead", "injured", "missing", "displaced"]) {
  return `<div class="st-figs">${keys.map((k) =>
    `<div class="st-fig${t[k] ? "" : " zero"}${k === "dead" && t[k] ? " dead" : ""}"><b>${nf.format(t[k] || 0)}</b><span>${FIG[k]}</span></div>`).join("")}</div>`;
}

function bars(rows) {
  const m = Math.max(1, ...rows.map((r) => r.value));
  return `<div class="st-bars">${rows.map((r) => `<div class="st-bar"${r.lens ? ` style="--lc:var(--l-${r.lens})"` : ""}>
    <span class="st-bar-label">${r.icon || ""}${esc(r.label)}</span>
    <span class="st-bar-track"><span class="st-bar-fill" style="width:${r.value ? Math.max(2, (r.value / m) * 100) : 0}%"></span></span>
    <b>${nf.format(r.value)}</b></div>`).join("")}</div>`;
}

// Case one-liners never include the headline: category · kab/kota, then impact.
function caseLines(data, list, n = 5) {
  return `<ol class="st-cases">${sortCases(list, "dampak").slice(0, n).map((i) => {
    const l = lensOf(i.category);
    const impact = impactText(i, ["dead", "injured", "missing", "displaced"]);
    return `<li style="--lc:var(--l-${l.key})">${lensIcon(l, 30)}<span><b>${esc(catLabel(data, i.category))} · ${esc(shortKab(i.kab_kota) || "Jawa Barat")}</b>
      ${impact ? `<span class="st-case-impact">${esc(impact)}</span>` : ""}</span></li>`;
  }).join("")}</ol>`;
}

function overviewCard(data, list) {
  const groups = byLens(list).filter((g) => g.count || g.lens.key !== "lainnya");
  const kabs = countBy(list, (i) => i.kab_kota).slice(0, 3);
  return frame(data, {
    band: `<p class="st-eyebrow">Situasi Jawa Barat</p>
      <p class="st-hero"><b>${nf.format(list.length)}</b> insiden</p>`,
    body: `${figures(totals(list))}
      <h2 class="st-h">Jumlah Insiden Berdasarkan Topik</h2>
      ${bars(groups.map((g) => ({ label: g.lens.label, value: g.count, lens: g.lens.key, icon: lensIcon(g.lens, 28) })))}
      ${kabs.length ? `<p class="st-kabs"><span>Wilayah terbanyak</span> ${kabs.map(([k, n]) => `${esc(shortKab(k))} <b>${n}</b>`).join('<span class="st-sep">·</span>')}</p>` : ""}`,
  });
}

function listCard(data, { lens, list, filtered, kat, wil }) {
  const src = filtered ?? list;
  const filt = [kat && catLabel(data, kat), wil && shortKab(wil)].filter(Boolean).join(" · ");
  const sensitive = lens?.sensitive || sortCases(src, "dampak").slice(0, 5).some((i) => lensOf(i.category).sensitive);
  const catRows = countBy(list, (i) => i.category).slice(0, 4)
    .map(([c, n]) => ({ label: catLabel(data, c), value: n, lens: lensOf(c).key }));
  return frame(data, {
    lens, sensitive,
    band: lens
      ? `<p class="st-eyebrow">${lensIcon(lens, 32)} Topik</p><p class="st-title">${esc(lens.label)}</p>
         <p class="st-hero sm"><b>${nf.format(list.length)}</b> insiden</p>`
      : `<p class="st-eyebrow">Daftar kasus</p><p class="st-hero sm"><b>${nf.format(src.length)}</b> insiden</p>`,
    body: `${figures(totals(lens ? list : src))}
      ${lens && catRows.length ? `<h2 class="st-h">Jenis kejadian</h2>${bars(catRows)}` : ""}
      <h2 class="st-h">Dampak terbesar${filt ? ` <span class="st-filter">(${esc(filt)})</span>` : ""}</h2>
      ${src.length ? caseLines(data, src, 5) : `<p class="st-lede">Tidak ada laporan dalam periode ini.</p>`}`,
  });
}

function caseCard(data, i) {
  const l = lensOf(i.category);
  const impact = impactText(i);
  const groups = groupsAffected(i);
  const event = fmtEventDate(i.event_time);
  const rows = [
    ["Lokasi", locationText(i, l.sensitive)],
    event && ["Waktu", event],
    ["Dampak", impact || "Belum disebutkan dalam berita"],
    groups.length && ["Kelompok terdampak", groups.join(", ")],
  ].filter(Boolean);
  return frame(data, {
    lens: l, sensitive: l.sensitive,
    band: `<p class="st-eyebrow">${lensIcon(l, 32)} ${esc(l.label)}</p><p class="st-title">${esc(catLabel(data, i.category))}</p>`,
    body: `<dl class="st-kv">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      ${l.sensitive
        ? `<p class="st-summary muted">Rincian kasus tidak ditampilkan untuk melindungi penyintas.</p>`
        : (i.summary ? `<p class="st-summary">${esc(i.summary)}</p>` : "")}
      <div class="st-sources"><span>Sumber</span>${sourceLines(i)}</div>`,
  });
}

// Site domains only: full article URLs are too long for the card, and for violence cases the slug repeats the headline.
function sourceLines(i) {
  const urls = [...new Set(i.sources.map((s) => { try { return new URL(s.url).host.replace(/^www\./, ""); } catch { return s.name; } }))];
  const shown = urls.slice(0, 2);
  return shown.map((u) => `<p>${esc(u)}</p>`).join("") + (urls.length > 2 ? `<p>+${urls.length - 2} sumber lain</p>` : "");
}

function cardFor(data, spec) {
  if (spec.kind === "overview") return overviewCard(data, spec.list);
  if (spec.kind === "case") return caseCard(data, spec.incident);
  return listCard(data, spec);
}

function fileName(spec, data) {
  const d = data.generated_at.slice(0, 10);
  const part = spec.kind === "case" ? `kasus-${spec.incident.id}` : spec.kind === "topic" ? `topik-${spec.lens.key}` : spec.kind === "list" ? "kasus" : "ringkasan";
  return `kebencanaan-jabar-${part}-${d}.png`;
}

// ---------------------------------------------------------------- captions
function captionFor(data, spec) {
  const w = `dalam ${data.window_hours} jam terakhir`;
  if (spec.kind === "overview") return `${nf.format(spec.list.length)} insiden di Jawa Barat ${w}.`;
  if (spec.kind === "topic") return `${spec.lens.label}: ${nf.format(spec.list.length)} insiden di Jawa Barat ${w}.`;
  if (spec.kind === "list") return `${nf.format(spec.list.length)} insiden di Jawa Barat ${w}.`;
  const i = spec.incident;
  const l = lensOf(i.category);
  const where = shortKab(i.kab_kota) || "Jawa Barat";
  const impact = l.sensitive ? "" : impactText(i, ["dead", "injured", "missing", "displaced"]);
  return `${catLabel(data, i.category)} di ${where}${impact ? ` (${impact})` : ""}.`;
}

// Compose links. None of these accept an image from the web; Instagram has no web compose at all,
// so it goes through the system share sheet with the PNG.
function intents(text, url) {
  const e = encodeURIComponent;
  return {
    x: `https://twitter.com/intent/tweet?text=${e(text)}&url=${e(url)}`,
    threads: `https://www.threads.net/intent/post?text=${e(`${text} ${url}`)}`,
    wa: `https://wa.me/?text=${e(`${text}\n${url}`)}`,
    fb: `https://www.facebook.com/sharer/sharer.php?u=${e(url)}`,
  };
}

// ---------------------------------------------------------------- sheet
let lastUrl = null;

export async function openShare(data, spec, title) {
  const dlg = document.getElementById("share");
  const $a = (k) => dlg.querySelector(`[data-act=${k}]`);
  const img = dlg.querySelector("img");
  const status = dlg.querySelector(".share-status");
  const hint = dlg.querySelector(".share-hint");
  const text = `${captionFor(data, spec)} Kebencanaan Jawa Barat`;
  for (const [k, u] of Object.entries(intents(text, location.href))) $a(k).href = u;
  const ig = $a("ig"), more = $a("more"), save = $a("save");
  ig.disabled = true; more.disabled = true; more.hidden = true;
  save.removeAttribute("href"); save.setAttribute("aria-disabled", "true");
  img.hidden = true;
  status.hidden = false;
  status.textContent = "Menyiapkan gambar…";
  hint.hidden = true;
  dlg.showModal();

  const stage = document.getElementById("story-stage");
  stage.innerHTML = cardFor(data, spec);
  const node = stage.firstElementChild;
  // Drop case lines from the bottom until the card fits in 1080×1440.
  const body = node.querySelector(".st-body");
  await document.fonts.ready;
  let rows = [...node.querySelectorAll(".st-cases li")];
  while (rows.length > 1 && body.scrollHeight > body.clientHeight) rows.pop().remove();
  try {
    const [lib, css] = await Promise.all([loadLib(), fontEmbedCSS()]);
    const opts = { width: W, height: H, pixelRatio: 1, backgroundColor: "#FAFAF8", fontEmbedCSS: css };
    await lib.toBlob(node, opts); // first pass warms font/image embedding (Safari)
    const blob = await lib.toBlob(node, opts);
    if (!blob) throw new Error("gambar kosong");
    if (lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl = URL.createObjectURL(blob);
    const name = fileName(spec, data);
    const file = new File([blob], name, { type: "image/png" });
    img.src = lastUrl;
    img.alt = `Pratinjau gambar: ${title}`;
    img.hidden = false;
    status.hidden = true;
    save.href = lastUrl;
    save.download = name;
    save.removeAttribute("aria-disabled");

    const canFiles = !!navigator.canShare?.({ files: [file] });
    const shareFile = async (withText) => {
      try { await navigator.share(withText ? { files: [file], text } : { files: [file] }); }
      catch (e) { if (e.name !== "AbortError") { status.hidden = false; status.textContent = `Gagal membagikan: ${e.message}`; } }
    };
    ig.disabled = false;
    hint.hidden = !canFiles;
    // Instagram ignores shared text, so send only the image.
    ig.onclick = () => {
      if (canFiles) return shareFile(false);
      save.click();
      status.hidden = false;
      status.textContent = "Gambar diunduh. Unggah ke Instagram lewat aplikasi di ponsel.";
    };
    if (canFiles) {
      more.hidden = false;
      more.disabled = false;
      more.onclick = () => shareFile(true);
    }
  } catch (e) {
    status.hidden = false;
    status.textContent = `Gambar gagal dibuat (${e.message}). Coba lagi atau gunakan tangkapan layar.`;
  } finally {
    stage.innerHTML = "";
  }
}

export { cardFor };
