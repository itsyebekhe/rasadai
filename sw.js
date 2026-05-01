const CACHE_NAME = 'rasad-cache-v1';
const ASSETS_TO_CACHE = [
    './',
    './index.html',
    'https://cdn.tailwindcss.com',
    'https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@33.003/misc/Farsi-Digits/Vazirmatn-FD-font-face.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'
];

// Install Event: Cache the UI Shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
    self.skipWaiting();
});

// Activate Event: Cleanup old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            );
        })
    );
});

// Fetch Event: Network-First for Data, Cache-First for UI
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Handle navigation requests (loading the website itself)
    // If the network fails (github.io blocked), serve index.html from cache
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(() => caches.match('./index.html'))
        );
        return;
    }

    // Data handling (JSON files)
    if (url.pathname.endsWith('.json')) {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    const cln = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, cln));
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    // UI and Assets handling (Cache-First)
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            if (cachedResponse) return cachedResponse;
            
            return fetch(event.request).then((networkResponse) => {
                // Cache external CDNs dynamically as they are requested
                if (event.request.url.includes('cdn') || event.request.url.includes('cloudflare')) {
                    const cln = networkResponse.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, cln));
                }
                return networkResponse;
            });
        })
    );
});