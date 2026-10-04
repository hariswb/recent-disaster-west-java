// Entry: load data, route between Ringkasan › Topik › Kasus, wire filters, share and theme.
import { esc } from "./model.js";
import { openShare } from "./share.js";
import { overview } from "./views/overview.js";
import { topic } from "./views/topic.js";
import { cases } from "./views/cases.js";
import { caseView } from "./views/case.js";

const $ = (s) => document.querySelector(s);
const state = { data: null, view: null, navigated: false };

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs = ""] = raw.split("?");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(qs) };
}

function resolve({ parts, query }) {
  const ctx = { data: state.data, list: state.data.incidents, query, params: {} };
  if (parts[0] === "topik" && parts[1]) return topic({ ...ctx, params: { lens: parts[1] } });
  if (parts[0] === "kasus" && parts[1]) return caseView({ ...ctx, params: { id: parts[1] } });
  if (parts[0] === "kasus") return cases(ctx);
  if (!parts.length) return overview(ctx);
  return null;
}

function notFound() {
  return {
    title: "Tidak ditemukan",
    html: `<section class="hero slim"><h1 tabindex="-1">Halaman tidak ditemukan</h1>
      <p class="lede">Kasus ini mungkin sudah lewat dari jendela ${state.data.window_hours} jam, atau tautannya keliru.</p>
      <p><a class="more" href="#/">← Kembali ke ringkasan</a></p></section>`,
  };
}

function render({ routeChange = true } = {}) {
  const route = parseHash();
  const view = resolve(route) || notFound();
  state.view = view;
  const active = document.activeElement;
  const focusKey = routeChange ? null : active?.id;
  const main = $("#view");
  main.innerHTML = view.html;
  document.title = `${view.title} · Kebencanaan Jawa Barat`;
  $("#live").textContent = `Halaman: ${view.title}`;
  $("#btn-back").hidden = !route.parts.length;
  $("#btn-share").hidden = !view.share;
  if (routeChange) {
    window.scrollTo({ top: 0 });
    main.querySelector("h1")?.focus({ preventScroll: true });
  } else if (focusKey) {
    document.getElementById(focusKey)?.focus({ preventScroll: true });
  }
}

// Filters rewrite the query without adding history entries.
function setQuery(patch) {
  const { parts, query } = parseHash();
  for (const [k, v] of Object.entries(patch)) {
    if (v === "" || v == null || (k === "urut" && v === "dampak")) query.delete(k); else query.set(k, v);
  }
  const qs = query.toString();
  history.replaceState(null, "", `#/${parts.map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`);
  render({ routeChange: false });
}

function bind() {
  window.addEventListener("hashchange", () => { state.navigated = true; render(); });
  const main = $("#view");
  main.addEventListener("change", (e) => {
    const keys = state.view?.filters?.keys || {};
    if (keys[e.target.id]) setQuery({ [keys[e.target.id]]: e.target.value });
  });
  main.addEventListener("click", (e) => {
    // Chart rows that filter the list below: stay on the page and jump to the results.
    const jump = e.target.closest("a[data-scroll]");
    if (jump) {
      e.preventDefault();
      history.replaceState(null, "", jump.getAttribute("href"));
      render({ routeChange: false });
      const target = document.getElementById(jump.dataset.scroll);
      if (target) {
        target.setAttribute("tabindex", "-1");
        target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
        target.focus({ preventScroll: true });
      }
      return;
    }
    const sort = e.target.closest("button[data-sort]");
    if (sort) { setQuery({ urut: sort.dataset.sort }); return; }
    const chip = e.target.closest("button[data-lens]");
    if (chip && state.view?.filters?.lensParam) {
      setQuery({ [state.view.filters.lensParam]: chip.dataset.lens, kat: "" });
    }
  });
  $("#btn-back").addEventListener("click", () => {
    // Return to where the reader came from inside the app; on a deep link, go one level up the hierarchy.
    if (state.navigated) { history.back(); return; }
    const { parts } = parseHash();
    location.hash = parts[0] === "kasus" && parts[1] ? "#/kasus" : "#/";
  });
  $("#btn-share").addEventListener("click", () => {
    if (state.view?.share) openShare(state.data, state.view.share, state.view.title);
  });
  $("#share").addEventListener("click", (e) => {
    if (e.target.closest("[data-act=close]") || e.target === e.currentTarget) e.currentTarget.close();
  });
  $("#theme").addEventListener("click", cycleTheme);
}

// ---------------------------------------------------------------- theme
// Sun/moon toggle. Without a saved choice the page follows the OS setting.
const SUN = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z"/></svg>';
const isDark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
function paintThemeButton() {
  const dark = isDark();
  const btn = $("#theme");
  btn.innerHTML = dark ? SUN : MOON;
  btn.setAttribute("aria-label", dark ? "Ganti ke tema terang" : "Ganti ke tema gelap");
  btn.title = btn.getAttribute("aria-label");
}
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  paintThemeButton();
}
function cycleTheme() {
  const next = isDark() ? "light" : "dark";
  try { localStorage.setItem("theme", next); } catch { /* storage unavailable */ }
  applyTheme(next);
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintThemeButton);

// ---------------------------------------------------------------- load
async function load() {
  let saved = "";
  try { saved = localStorage.getItem("theme") || ""; } catch { /* storage unavailable */ }
  applyTheme(saved);
  const sample = new URLSearchParams(location.search).get("data") === "sample";
  const url = sample ? "data/sample-incidents.json" : "data/incidents.json";
  try {
    const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.data = await r.json();
  } catch (err) {
    $("#view").innerHTML = `<section class="hero slim"><h1>Data gagal dimuat</h1><p class="lede">${esc(err.message)}. Coba muat ulang halaman.</p></section>`;
    return;
  }
  $("#sample-banner").hidden = !sample;
  bind();
  render();
}

load();
