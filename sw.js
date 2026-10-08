const CACHE = "vs-cache-v13";
const CORE = ["./", "index.html", "offline.html", "styles.css",
              "manifest.json", "assets/placeholder.svg", "assets/search-index.json",
              "scores.html", "assets/scores.js", "assets/motogp.json",
              "horoscope.html", "assets/horoscope.json",
              "markets.html", "policy.html", "assets/policy.json", "videos.html", "articles.html", "economics.html", "geography.html", "current-affairs.html", "assets/constitution-articles.json", "assets/current-affairs.json",
              "history-ancient.html", "history-medieval.html", "history-modern.html", "upsc-syllabus.html",
              "study.html", "constitution.html", "articles.html",
              "quiz.html", "assets/quiz.json",
              "factcheck.html", "timeline.html", "topics/index.html",
              "assets/i18n.json", "assets/headlines-hi.json", "assets/factchecks.json",
              "assets/lang-toggle.js", "assets/js/vs-reader.js", "assets/og-default.png",
              "today.html",
              "assets/readaloud.js"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE))
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE)
      .map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  if (e.request.url.indexOf("markets_rates.json") !== -1) return; // network-only: always fresh bullion rates
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => (e.request.mode === "navigate"
        ? caches.match("offline.html") : undefined));
      return hit || net;
    })
  );
});
