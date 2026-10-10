/* Bump the shell version when shipping changes to HTML, scripts, or styles. */
'use strict';
const SHELL_CACHE = 'lumen-shell-20261010-private-book-v6';
const ART_CACHE = 'lumen-art-v1';
const MAX_ART_ENTRIES = 40;
const SHELL_PATHS = [
  '/', '/index.html', '/styles.css', '/mobile.css', '/app.js', '/progress.js',
  '/book.js', '/legacy-pilot.js', '/install.js', '/manifest.webmanifest',
  '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png',
  '/icons/icon-maskable-512.png', '/icons/apple-touch-icon.png',
  '/icons/favicon-lumen-32-v2.png', '/icons/favicon-lumen-16-v2.png'
];
const SHELL_SET = new Set(SHELL_PATHS);

self.addEventListener('install', event => {
  // Audio and paintings are deliberately excluded: installation is a small download.
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL_PATHS.map(async path => {
      const response = await fetch(new Request(path, {cache: 'reload'}));
      if (response.status !== 200) throw new Error('Missing Lumen shell resource: ' + path);
      await cache.put(path, response);
    }));
  })());
  // No skipWaiting: a release must not replace a worker while someone is listening.
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name =>
      (name.startsWith('lumen-shell-') && name !== SHELL_CACHE) ||
      (name.startsWith('lumen-art-') && name !== ART_CACHE) || name === 'lumen-media-v1')
      .map(name => caches.delete(name)));
    // Retain visited public paintings; remove obsolete shells and prose caches.
  })());
});

function completeResponse(response) {
  return response.status === 200 && !response.headers.has('Content-Range');
}

function offlineResponse() {
  return new Response('Lumen needs a connection for content that has not been downloaded.', {
    status: 503,
    headers: {'Content-Type': 'text/plain; charset=utf-8'}
  });
}

async function saveArt(request, response) {
  if (!completeResponse(response)) return;
  const cache = await caches.open(ART_CACHE);
  await cache.put(request.url, response);
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_ART_ENTRIES))
    .map(key => cache.delete(key)));
}

async function cachedArt(request) {
  const cache = await caches.open(ART_CACHE);
  return await cache.match(request.url) || offlineResponse();
}

function navigation(request, event) {
  const network = fetch(request);
  event.waitUntil(network.then(async response => {
    if (completeResponse(response)) {
      const copy = response.clone();
      const cache = await caches.open(SHELL_CACHE);
      await cache.put('/index.html', copy);
    }
  }).catch(() => {}));
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('Navigation timed out')), 3500);
  });
  return Promise.race([network, timeout]).then(response => {
    clearTimeout(timer);
    if (!response.ok) throw new Error('Navigation unavailable');
    return response;
  }).catch(async () => {
    clearTimeout(timer);
    const cache = await caches.open(SHELL_CACHE);
    return await cache.match('/index.html') || offlineResponse();
  });
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Keep media loading, Range requests, and byte streams entirely in the browser's
  // native network stack. The installed reader currently requires online audio.
  if (url.pathname.startsWith('/api/') || request.headers.has('Range') || request.destination === 'audio' ||
      /\.(?:mp3|m4a|m4b|aac|wav|ogg|oga|opus|flac|aif|aiff|wma)$/i.test(url.pathname)) return;

  if (request.mode === 'navigate') {
    event.respondWith(navigation(request, event));
  } else if (url.pathname.startsWith('/assets/') && /\.(?:png|webp|jpe?g|gif|svg|avif)$/i.test(url.pathname)) {
    // Historical public paintings may be viewed offline. Private /api/ artwork
    // bypasses this worker so expired or revoked access cannot become an offline grant.
    const network = fetch(request);
    event.waitUntil(network.then(response => saveArt(request, response.clone())).catch(() => {}));
    event.respondWith(network.then(response => {
      if (!response.ok) throw new Error('Painting unavailable');
      return response;
    }).catch(() => cachedArt(request)));
  } else if (SHELL_SET.has(url.pathname)) {
    const network = fetch(request);
    event.waitUntil(network.then(async response => {
      if (completeResponse(response)) {
        const copy = response.clone();
        const cache = await caches.open(SHELL_CACHE);
        await cache.put(url.pathname, copy);
      }
    }).catch(() => {}));
    event.respondWith(network.then(response => {
      if (!response.ok) throw new Error('Shell resource unavailable');
      return response;
    }).catch(async () => {
      const cache = await caches.open(SHELL_CACHE);
      return await cache.match(url.pathname) || offlineResponse();
    }));

  }
});
