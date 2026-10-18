// Google Analytics 4 for every kondisi.id page. Tracking stays off until GA_ID is set.
// Set it to the web stream's Measurement ID from GA4 (Admin › Data streams), e.g. "G-ABC123XYZ".
export const GA_ID = "";

let ready = false;

export function initAnalytics() {
  if (ready || !/^G-[A-Z0-9]+$/.test(GA_ID)) return;
  ready = true;
  window.dataLayer = window.dataLayer || [];
  window.gtag = function gtag() { window.dataLayer.push(arguments); };
  window.gtag("js", new Date());
  // Hash routes are reported by hand (trackPage), so the automatic page_view is off.
  window.gtag("config", GA_ID, { send_page_view: false });
  const s = document.createElement("script");
  s.async = true;
  s.src = `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`;
  document.head.append(s);
}

// `path` is the route inside the current page ("/topik/bencana"); filters are left out.
export function trackPage(path = "/", title = document.title) {
  if (!ready) return;
  const base = location.pathname.replace(/\/$/, "");
  window.gtag("event", "page_view", {
    page_location: `${location.origin}${base}${path === "/" ? "/" : path}`,
    page_title: title,
  });
}

export function trackEvent(name, params = {}) {
  if (ready) window.gtag("event", name, params);
}
