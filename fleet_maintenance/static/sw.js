// A very basic Service Worker to satisfy PWA installation requirements
self.addEventListener('install', (e) => {
    console.log('[Service Worker] Install');
});
self.addEventListener('fetch', (e) => {
    // Network-first strategy
    e.respondWith(fetch(e.request).catch(() => console.log('Offline')));
});