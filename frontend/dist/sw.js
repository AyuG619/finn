// FINN service worker: receives push alerts and opens the right item when tapped.
// Deliberately no fetch caching: the brief must never show a stale build or stale data silently.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "FINN", body: event.data ? event.data.text() : "" }; }
  const title = data.title || "FINN";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "Something in your portfolio needs a look.",
      icon: "/icon-192.png",
      badge: "/badge-96.png",
      tag: data.tag || "finn",
      renotify: true,
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ("focus" in w) { w.navigate(url); return w.focus(); }
      }
      return self.clients.openWindow(url);
    })
  );
});
