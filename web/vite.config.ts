import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// The dev server proxies /api, /ws, /audio (mock) and /media (real backend, AUDIO_STORAGE=local) to BACKEND_URL so the app is same-origin in dev
// (one host to expose to the phone). Default: the mock server in web/mock (port 8787).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.BACKEND_URL || "http://localhost:8787";
  const wsBackend = backend.replace(/^http/, "ws");

  return {
    plugins: [
      react(),
      // The Silero VAD model, worklet and onnxruntime wasm live in public/vad/ (copied there by
      // scripts/copy-vad-assets.mjs). onnxruntime imports its .mjs loader at runtime; in dev Vite
      // appends "?import" to that dynamic import, which would turn the public file into a URL
      // stub, so strip the query for /vad/ requests.
      {
        name: "serve-vad-assets-raw",
        configureServer(server) {
          server.middlewares.use((req, _res, next) => {
            if (req.url?.startsWith("/vad/")) req.url = req.url.replace(/\?.*$/, "");
            next();
          });
        },
      },
      VitePWA({
        registerType: "autoUpdate",
        injectRegister: "auto",
        includeAssets: ["icon.svg", "apple-touch-icon.png"],
        manifest: {
          name: "Briefing",
          short_name: "Briefing",
          description: "Your daily spoken news brief you can talk to.",
          display: "standalone",
          orientation: "portrait",
          start_url: "/",
          background_color: "#0f1115",
          theme_color: "#0f1115",
          icons: [
            { src: "icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icon-512.png", sizes: "512x512", type: "image/png" },
            { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
          ],
        },
        workbox: {
          // App shell only (TDD §13.4): no API, audio or VAD-model caching.
          globPatterns: ["**/*.{js,css,html,svg,png}"],
          globIgnores: ["vad/**"],
          navigateFallbackDenylist: [/^\/api\//, /^\/ws\//, /^\/audio\//, /^\/media\//, /^\/vad\//],
          runtimeCaching: [],
        },
      }),
    ],
    server: {
      host: true,
      port: 5173,
      // iPhone testing needs HTTPS for the mic: expose the dev server through a tunnel
      // (see docs/iphone-checklist.md). Extra hosts: ALLOWED_HOSTS=a.example.com,b.example.com
      allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok.app", ...(env.ALLOWED_HOSTS?.split(",") ?? [])],
      proxy: {
        "/api": { target: backend, changeOrigin: true },
        "/audio": { target: backend, changeOrigin: true },
        "/media": { target: backend, changeOrigin: true },
        "/healthz": { target: backend, changeOrigin: true },
        "/ws": {
          target: wsBackend,
          ws: true,
          changeOrigin: true,
          // A closing tab resets the proxied socket; that's not an error worth logging.
          configure: (proxy) => proxy.on("error", () => {}),
        },
      },
    },
    preview: {
      port: 4173,
      proxy: {
        "/api": { target: backend, changeOrigin: true },
        "/audio": { target: backend, changeOrigin: true },
        "/media": { target: backend, changeOrigin: true },
        "/ws": {
          target: wsBackend,
          ws: true,
          changeOrigin: true,
          configure: (proxy) => proxy.on("error", () => {}),
        },
      },
    },
  };
});
