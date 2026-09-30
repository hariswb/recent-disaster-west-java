(() => {
  "use strict";

  const GROUPS = {
    alam: { label: "Alam", var: "--g-alam" },
    kebakaran: { label: "Kebakaran", var: "--g-kebakaran" },
    sosial: { label: "Sosial & infrastruktur", var: "--g-sosial" },
  };
  const GLYPH = {
    banjir: "BJ", longsor: "LS", gempa: "GM", angin: "AN", kebakaran: "KB", kekeringan: "KR",
    gunung: "GA", keracunan: "RC", wabah: "WB", infrastruktur: "IF", kecelakaan: "KC", lainnya: "LN",
  };
  const VICTIMS = [
    ["dead", "meninggal"], ["injured", "luka/dirawat"], ["missing", "hilang"],
    ["displaced", "mengungsi"], ["affected", "terdampak"], ["houses", "rumah/bangunan"],
  ];
  const ENTITY_LABEL = {
    sekolah: "Sekolah", desa: "Desa/permukiman", permukiman: "Permukiman", pasar: "Pasar", pabrik: "Pabrik",
    fasilitas_kesehatan: "Faskes", rumah_ibadah: "Rumah ibadah", kantor: "Kantor", jalan_jembatan: "Jalan/jembatan",
    lahan: "Lahan", lainnya: "Lainnya",
  };
  const JABAR = [-6.92, 107.6];
  const TZ = "Asia/Jakarta";

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmtTime = (iso) => new Intl.DateTimeFormat("id-ID", { timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  const nf = new Intl.NumberFormat("id-ID");

  function ago(iso, now) {
    const m = Math.max(0, Math.round((now - new Date(iso)) / 60000));
    if (m < 60) return `${m} menit lalu`;
    return `${Math.round(m / 60)} jam lalu`;
  }

  const state = { data: null, group: "", category: "", kab: "", llmOnly: false, active: null };
  let map, layer;
  const markers = new Map();

  function groupOf(cat) {
    return state.data.categories[cat]?.group || "sosial";
  }
  function labelOf(cat) {
    return state.data.categories[cat]?.label || cat;
  }

  function filtered() {
    return state.data.incidents.filter((i) =>
      (!state.group || groupOf(i.category) === state.group) &&
      (!state.category || i.category === state.category) &&
      (!state.kab || i.kab_kota === state.kab) &&
      (!state.llmOnly || i.extraction === "llm"));
  }

  // ---------------------------------------------------------------- tiles
  function renderTiles(list) {
    const sum = (k) => list.reduce((a, i) => a + (i.victims?.[k] || 0), 0);
    const tiles = [
      ["Insiden", list.length],
      ["Meninggal", sum("dead")],
      ["Luka / dirawat", sum("injured")],
      ["Hilang", sum("missing")],
      ["Mengungsi", sum("displaced")],
    ];
    $("#tiles").innerHTML = tiles.map(([l, v]) =>
      `<div class="tile"><div class="label">${l}</div><div class="value${v ? "" : " zero"}">${nf.format(v)}</div></div>`).join("");
  }

  // ---------------------------------------------------------------- filters
  function renderFilters() {
    const all = state.data.incidents;
    const count = (g) => all.filter((i) => !g || groupOf(i.category) === g).length;
    const chips = [["", "Semua", null], ...Object.entries(GROUPS).map(([k, g]) => [k, g.label, g.var])];
    $("#groups").innerHTML = chips.map(([k, l, v]) =>
      `<button type="button" class="chip" data-group="${k}" aria-pressed="${state.group === k}">` +
      (v ? `<span class="dot" style="background:var(${v})"></span>` : "") +
      `${esc(l)} <span class="n">${count(k)}</span></button>`).join("");

    const cats = [...new Set(all.map((i) => i.category))].sort((a, b) => labelOf(a).localeCompare(labelOf(b)));
    $("#f-category").innerHTML = `<option value="">Semua kategori</option>` +
      cats.map((c) => `<option value="${c}"${c === state.category ? " selected" : ""}>${esc(labelOf(c))} (${all.filter((i) => i.category === c).length})</option>`).join("");
    const kabs = [...new Set(all.map((i) => i.kab_kota).filter(Boolean))].sort();
    $("#f-kab").innerHTML = `<option value="">Semua wilayah</option>` +
      kabs.map((k) => `<option value="${esc(k)}"${k === state.kab ? " selected" : ""}>${esc(k)}</option>`).join("");
  }

  // ---------------------------------------------------------------- list
  function locationText(i) {
    const parts = [i.desa && (/^(desa|kelurahan|kampung)/i.test(i.desa) ? i.desa : `Desa ${i.desa}`),
      i.kecamatan && `Kec. ${i.kecamatan}`, i.kab_kota].filter(Boolean);
    return parts.length ? parts.join(", ") : "Jawa Barat (lokasi umum)";
  }

  function card(i, now) {
    const g = GROUPS[groupOf(i.category)];
    const v = VICTIMS.filter(([k]) => i.victims?.[k]).map(([k, l]) =>
      `<span class="v ${k}"><strong>${nf.format(i.victims[k])}</strong> ${l}</span>`).join("");
    const e = (i.affected_entities || []).map((x) =>
      `<span class="e">${esc(x.name || ENTITY_LABEL[x.type] || x.type)}</span>`).join("");
    const src = i.sources.map((s) =>
      `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a> · ${esc(fmtTime(s.published))}<br><span class="fine">${esc(s.title)}</span></li>`).join("");
    return `<article class="card" id="i-${i.id}" data-id="${i.id}" style="--gc:var(${g.var})">
      <div class="meta">
        <span class="badge"><span class="dot" style="background:var(${g.var})"></span>${esc(labelOf(i.category))}${i.subcategory ? ` · ${esc(i.subcategory)}` : ""}</span>
        <span title="${esc(i.last_reported)}">${ago(i.last_reported, now)}</span>
        ${i.extraction === "rules" ? `<span class="tag" title="Diekstrak dengan aturan kata kunci, belum diverifikasi LLM">aturan</span>` : ""}
      </div>
      <h2>${esc(i.title)}</h2>
      <p class="loc">${esc(locationText(i))}</p>
      ${i.summary && i.summary !== i.title ? `<p class="summary">${esc(i.summary)}</p>` : ""}
      ${v ? `<div class="vchips">${v}</div>` : ""}
      ${e ? `<div class="echips">${e}</div>` : ""}
      <details class="sources"><summary>${i.report_count} sumber berita</summary><ul>${src}</ul></details>
    </article>`;
  }

  function renderList(list, now) {
    $("#list").innerHTML = list.length
      ? list.map((i) => card(i, now)).join("")
      : `<div class="empty">Tidak ada insiden yang cocok dengan filter dalam ${state.data.window_hours} jam terakhir.</div>`;
  }

  // ---------------------------------------------------------------- map
  function initMap() {
    map = L.map("map", { zoomControl: true, scrollWheelZoom: false }).setView(JABAR, 8);
    // Standard OSM tiles (free, attribution required); darkened via CSS in dark mode.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  }

  function renderMap(list) {
    layer.clearLayers();
    markers.clear();
    const placed = new Map();
    const pts = [];
    let unplaced = 0;
    for (const i of list) {
      if (i.lat == null || i.lon == null) { unplaced++; continue; }
      // Spread incidents sharing the same centroid so each stays clickable.
      const key = `${i.lat},${i.lon}`;
      const n = placed.get(key) || 0;
      placed.set(key, n + 1);
      const ang = n * 2.4, r = n ? 0.012 * Math.sqrt(n) : 0;
      const ll = [i.lat + r * Math.sin(ang), i.lon + r * Math.cos(ang)];
      const g = GROUPS[groupOf(i.category)];
      const big = (i.victims?.dead || 0) + (i.victims?.injured || 0) > 0;
      const icon = L.divIcon({
        className: "",
        html: `<div class="marker${big ? " big" : ""}" style="--gc:var(${g.var})">${GLYPH[i.category] || "?"}</div>`,
        iconSize: big ? [32, 32] : [26, 26],
      });
      const m = L.marker(ll, { icon, title: `${labelOf(i.category)}: ${i.title}`, riseOnHover: true })
        .bindPopup(`<div class="meta">${esc(labelOf(i.category))} · ${esc(locationText(i))}</div><h3>${esc(i.title)}</h3>` +
          `<p>${VICTIMS.filter(([k]) => i.victims?.[k]).map(([k, l]) => `${nf.format(i.victims[k])} ${l}`).join(" · ")}</p>`)
        .on("click", () => select(i.id, false));
      m.addTo(layer);
      markers.set(i.id, m);
      pts.push(ll);
    }
    const note = $("#map-note");
    note.hidden = !unplaced;
    note.textContent = unplaced ? `${unplaced} insiden tanpa lokasi kabupaten/kota yang jelas hanya tampil di daftar.` : "";
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 11 });
    else map.setView(JABAR, 8);
  }

  function select(id, fromList) {
    document.querySelectorAll(".card.active").forEach((c) => c.classList.remove("active"));
    const el = document.getElementById(`i-${id}`);
    if (el) {
      el.classList.add("active");
      if (!fromList) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    const m = markers.get(id);
    if (m && fromList) {
      map.setView(m.getLatLng(), Math.max(map.getZoom(), 10));
      m.openPopup();
    }
  }

  // ---------------------------------------------------------------- health
  function renderHealth() {
    const d = state.data;
    const rows = d.source_status.map((s) =>
      `<tr><td>${esc(s.name || s.id)}</td><td>${s.ok ? "OK" : "Gagal"}</td><td>${s.items}</td><td class="${s.ok ? "" : "err"}">${esc(s.error || "")}</td></tr>`).join("");
    const llm = (d.llm_providers || []).map((p) =>
      `<tr><td>${esc(p.name)}</td><td>${esc(p.model || "-")}</td><td>${p.calls}</td><td class="${p.exhausted ? "err" : ""}">${esc(p.exhausted || "")}</td></tr>`).join("");
    const st = d.stats || {};
    $("#health").innerHTML =
      `<p>Artikel diambil: ${st.items_fetched ?? "-"} · dalam 24 jam: ${st.items_in_window ?? "-"} · kandidat: ${st.candidates ?? "-"} · ` +
      `diekstrak run ini: LLM ${st.extracted_this_run?.llm ?? 0}, aturan ${st.extracted_this_run?.rules ?? 0}</p>` +
      `<div class="table-wrap"><table><thead><tr><th>Sumber</th><th>Status</th><th>Artikel</th><th>Galat</th></tr></thead><tbody>${rows}</tbody></table></div>` +
      (llm ? `<div class="table-wrap"><table><thead><tr><th>LLM</th><th>Model</th><th>Panggilan</th><th>Status</th></tr></thead><tbody>${llm}</tbody></table></div>`
        : `<p>Tidak ada penyedia LLM yang aktif pada run ini; semua hasil dari aturan kata kunci.</p>`);
  }

  // ---------------------------------------------------------------- wiring
  function render() {
    const now = new Date();
    const list = filtered();
    renderFilters();
    renderTiles(list);
    renderList(list, now);
    renderMap(list);
  }

  function bind() {
    $("#groups").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-group]");
      if (!b) return;
      state.group = b.dataset.group;
      state.category = "";
      render();
    });
    $("#f-category").addEventListener("change", (e) => { state.category = e.target.value; render(); });
    $("#f-kab").addEventListener("change", (e) => { state.kab = e.target.value; render(); });
    $("#f-llm").addEventListener("change", (e) => { state.llmOnly = e.target.checked; render(); });
    $("#list").addEventListener("click", (e) => {
      if (e.target.closest("a, summary")) return;
      const c = e.target.closest(".card");
      if (c) select(c.dataset.id, true);
    });
  }

  async function load() {
    try {
      const r = await fetch(`data/incidents.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      state.data = await r.json();
    } catch (err) {
      $("#updated").textContent = `Gagal memuat data (${err.message}).`;
      return;
    }
    const gen = new Date(state.data.generated_at);
    $("#updated").textContent =
      `${state.data.window_hours} jam terakhir · diperbarui ${new Intl.DateTimeFormat("id-ID", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }).format(gen)} WIB`;
    initMap();
    bind();
    render();
    renderHealth();
  }

  load();
})();
