// L1 Peta: where incidents happened, on a street basemap or a terrain (elevation contour) basemap.
import { LENS, LENSES, catLabel, countBy, esc, lensOf, nf, shortKab } from "../model.js";
import { barChart, crumbs, href, lensChips } from "../ui.js";

const LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/";

// Free tile servers (attribution required). Kontur = OpenTopoMap: elevation contour lines + hillshade from SRTM.
const BASEMAPS = {
  jalan: {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  },
  kontur: {
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", subdomains: "abc", maxZoom: 17,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, SRTM · gaya peta &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
  },
};

// Kab/kota centroids (data/gazetteer/jabar.json). Sensitive cases are drawn here, never finer.
const KAB_CENTROID = {
  "Kabupaten Bogor": [-6.5598, 106.767], "Kabupaten Sukabumi": [-7.0763, 106.7076], "Kabupaten Cianjur": [-7.1309, 107.1592],
  "Kabupaten Bandung": [-7.0976, 107.6087], "Kabupaten Garut": [-7.3594, 107.7879], "Kabupaten Tasikmalaya": [-7.4967, 108.1416],
  "Kabupaten Ciamis": [-7.2903, 108.4287], "Kabupaten Kuningan": [-7.0034, 108.5598], "Kabupaten Cirebon": [-6.7462, 108.5522],
  "Kabupaten Majalengka": [-6.8151, 108.2572], "Kabupaten Sumedang": [-6.8247, 107.9813], "Kabupaten Indramayu": [-6.4475, 108.169],
  "Kabupaten Subang": [-6.4846, 107.7318], "Kabupaten Purwakarta": [-6.5953, 107.4308], "Kabupaten Karawang": [-6.2523, 107.3543],
  "Kabupaten Bekasi": [-6.2198, 107.1217], "Kabupaten Bandung Barat": [-6.8904, 107.415], "Kabupaten Pangandaran": [-7.6356, 108.5186],
  "Kota Bogor": [-6.5935, 106.7994], "Kota Sukabumi": [-6.9399, 106.9244], "Kota Bandung": [-6.9192, 107.6366],
  "Kota Cirebon": [-6.7417, 108.5533], "Kota Bekasi": [-6.2808, 106.9754], "Kota Depok": [-6.3962, 106.8168],
  "Kota Cimahi": [-6.8863, 107.5429], "Kota Tasikmalaya": [-7.3605, 108.2191], "Kota Banjar": [-7.377, 108.5672],
};
const JABAR_BOUNDS = [[-7.85, 106.35], [-5.9, 108.9]];

let leaflet = null;
let saved = null; // { center, zoom }, kept across filter changes
let map = null;

function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  leaflet ??= new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${LEAFLET}leaflet.css`;
    document.head.append(css);
    const s = document.createElement("script");
    s.src = `${LEAFLET}leaflet.js`;
    s.onload = () => resolve(window.L);
    s.onerror = () => { leaflet = null; reject(new Error("pustaka peta gagal dimuat")); };
    document.head.append(s);
  });
  return leaflet;
}

// One map point per incident; violence-lens cases snap to their kab/kota.
function point(i) {
  const l = lensOf(i.category);
  const ll = l.sensitive ? KAB_CENTROID[i.kab_kota] : (i.lat != null && i.lon != null ? [i.lat, i.lon] : null);
  return ll && { i, l, lat: ll[0], lon: ll[1] };
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isDark = () => document.documentElement.dataset.theme === "dark"
  || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);

function popup(data, pts) {
  const place = pts[0].l.sensitive || pts[0].i.location_precision !== "kecamatan" ? shortKab(pts[0].i.kab_kota)
    : `Kec. ${pts[0].i.kecamatan}, ${shortKab(pts[0].i.kab_kota)}`;
  const rows = pts.slice(0, 6).map(({ i }) =>
    `<li><a href="#/kasus/${esc(i.id)}"><span>${esc(catLabel(data, i.category))}</span>${esc(i.title)}</a></li>`).join("");
  const more = pts.length > 6 ? `<a class="pop-more" href="${href("/kasus", { wil: pts[0].i.kab_kota })}">Lihat ${nf.format(pts.length)} kasus</a>` : "";
  return `<div class="pop"><b>${esc(place)}</b><ul>${rows}</ul>${more}</div>`;
}

function drawPoints(L, data, pts, layer) {
  const groups = new Map();
  for (const p of pts) {
    const k = `${p.lat.toFixed(4)},${p.lon.toFixed(4)}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  }
  for (const g of groups.values()) {
    const lenses = new Set(g.map((p) => p.l.key));
    const color = lenses.size === 1 ? css(`--l-${g[0].l.key}`) : css("--ink-2");
    L.circleMarker([g[0].lat, g[0].lon], {
      radius: 7 + 3 * Math.sqrt(g.length - 1), color: css("--surface"), weight: 2, fillColor: color, fillOpacity: 0.9,
    }).bindPopup(popup(data, g), { maxWidth: 300 }).bindTooltip(`${nf.format(g.length)} insiden`).addTo(layer);
  }
}

async function mount(data, pts, mode) {
  const el = document.getElementById("map");
  let L;
  try {
    L = await loadLeaflet();
  } catch (e) {
    if (el) el.innerHTML = `<p class="empty">Peta gagal dimuat: ${esc(e.message)}. Coba muat ulang halaman.</p>`;
    return;
  }
  if (!el?.isConnected) return; // navigated away while loading
  map = L.map(el, { scrollWheelZoom: false, minZoom: 7, maxBounds: [[-9.5, 104.5], [-4.5, 110.5]] });
  if (saved) map.setView(saved.center, saved.zoom); else map.fitBounds(JABAR_BOUNDS);
  map.on("moveend", () => { saved = { center: map.getCenter(), zoom: map.getZoom() }; });
  const { url, ...opts } = BASEMAPS[mode];
  L.tileLayer(url, opts).addTo(map);
  // Dark mode: the street map is inverted; the terrain map is only dimmed so its colours keep their meaning.
  el.classList.toggle("dark-tiles", isDark());
  el.classList.toggle("topo", mode === "kontur");
  drawPoints(L, data, pts, L.layerGroup().addTo(map));
}

function unmount() {
  map?.remove();
  map = null;
}

export function mapView({ data, list, query }) {
  const lensKey = LENS[query.get("lensa")] ? query.get("lensa") : "";
  const mode = query.get("mode") === "kontur" ? "kontur" : "jalan";
  const lensCounts = Object.fromEntries(LENSES.map((l) => [l.key, list.filter((i) => lensOf(i.category).key === l.key).length]));
  const shown = list.filter((i) => !lensKey || lensOf(i.category).key === lensKey);
  const pts = shown.map(point).filter(Boolean);
  const unplaced = shown.length - pts.length;
  const kabs = countBy(shown, (i) => i.kab_kota);
  const seg = [["", "Jalan"], ["kontur", "Kontur"]].map(([v, l]) =>
    `<button type="button" id="mode-${v || "jalan"}" data-q="mode" data-v="${v}" aria-pressed="${(v || "jalan") === mode}">${l}</button>`).join("");

  const html = `
  <header class="band">
    ${crumbs([["Ringkasan", "#/"], ["Peta"]])}
    <h1 tabindex="-1">Peta insiden</h1>
    <p class="lede">${nf.format(pts.length)} insiden berlokasi dalam ${data.window_hours} jam terakhir${unplaced ? ` · ${nf.format(unplaced)} tanpa lokasi` : ""}</p>
  </header>
  <section class="sec" aria-label="Peta dan saringan">
    <div class="filters">
      ${lensChips(lensCounts, lensKey)}
      <div class="seg" role="group" aria-label="Peta dasar">${seg}</div>
    </div>
    <div id="map" class="map" role="region" aria-label="Peta Jawa Barat${mode === "kontur" ? " dengan garis kontur ketinggian" : ""}: titik lokasi insiden"></div>
    <p class="map-note">${mode === "kontur"
      ? "Peta kontur menampilkan garis ketinggian dan relief tanah, membantu membaca lereng, lembah, dan dataran di sekitar lokasi insiden."
      : ""} Ukuran titik mengikuti jumlah insiden di lokasi yang sama; ketuk titik untuk melihat kasusnya.
      Lokasi adalah titik pusat kecamatan atau kab/kota, bukan alamat kejadian. Kasus kekerasan hanya ditampilkan di tingkat kab/kota.</p>
  </section>
  <section class="sec" aria-labelledby="h-wil">
    <h2 id="h-wil">Per kabupaten/kota</h2>
    ${kabs.length ? barChart(kabs.map(([k, n]) => ({ label: shortKab(k), value: n, link: href("/kasus", { wil: k, lensa: lensKey }) })),
      { label: "Jumlah insiden per kabupaten/kota" }) : `<p class="empty">Belum ada lokasi.</p>`}
  </section>`;

  return {
    title: "Peta",
    html,
    filters: { keys: {}, lensParam: "lensa" },
    themed: true,
    mount: () => mount(data, pts, mode),
    unmount,
  };
}
