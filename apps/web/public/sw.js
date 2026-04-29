const APP_CACHE = "arcadegent-app-v1";
const RUNTIME_CACHE = "arcadegent-runtime-v1";
const CORE_ASSETS = [
  "/",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-512.png",
  "/icons/arcadegent-icon.svg",
  "/icons/arcadegent-maskable.svg"
];
const BYPASS_PATH_PREFIXES = ["/api/", "/health"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(APP_CACHE).then((cache) => cache.addAll(CORE_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== APP_CACHE && key !== RUNTIME_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (shouldBypassCache(url)) {
    event.respondWith(fetch(request));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
    return;
  }

  if (isStaticAsset(request, url)) {
    event.respondWith(cacheFirst(request));
  }
});

function shouldBypassCache(url) {
  return (
    url.pathname === "/sw.js" ||
    BYPASS_PATH_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
  );
}

function isStaticAsset(request, url) {
  return (
    url.pathname.startsWith("/assets/") ||
    ["font", "image", "manifest", "script", "style", "worker"].includes(
      request.destination
    )
  );
}

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(APP_CACHE);

  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cachedResponse = await cache.match(request);
    return cachedResponse || cache.match(fallbackUrl);
  }
}

async function cacheFirst(request) {
  const cachedResponse = await caches.match(request);
  if (cachedResponse) {
    return cachedResponse;
  }

  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(RUNTIME_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}
