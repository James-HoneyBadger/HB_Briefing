// Cross-origin isolation for static hosting.
//
// GitHub Pages cannot send `Cross-Origin-Opener-Policy` or `Cross-Origin-Embedder-Policy`
// headers, and without them a page is not "cross-origin isolated", so the browser withholds
// SharedArrayBuffer and the APIs Pyodide uses when it runs Python with threads. A service
// worker can add headers to its own responses, which is enough: register this file, reload
// once, and the page becomes isolated.
//
// Deliberately small and same-origin only: it never touches a request it did not originate,
// and it does not cache anything. If registration fails the page keeps working unisolated,
// which is what the WebAssembly app does anyway.

const COOP = "same-origin";
const COEP = "require-corp";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (!request.url.startsWith(self.location.origin)) return;
  event.respondWith(
    fetch(request)
      .then((response) => {
        // Opaque and error responses keep their own headers; rewriting them would only hide
        // what went wrong.
        if (response.status === 0 || response.type === "opaque") return response;
        const headers = new Headers(response.headers);
        headers.set("Cross-Origin-Opener-Policy", COOP);
        headers.set("Cross-Origin-Embedder-Policy", COEP);
        return new Response(response.body, {
          status: response.status,
          statusText: response.statusText,
          headers,
        });
      })
      .catch(() => fetch(request)),
  );
});
