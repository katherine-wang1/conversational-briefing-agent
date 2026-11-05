// Copy the Silero VAD model, its AudioWorklet and the onnxruntime-web wasm into public/vad/
// (served as-is in dev and build; not committed). Runs on postinstall / predev / prebuild.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(web, "public", "vad");
const files = [
  "node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js",
  "node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx",
  "node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx",
  "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm",
  "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs",
];

fs.mkdirSync(out, { recursive: true });
for (const f of files) {
  const src = path.join(web, f);
  if (!fs.existsSync(src)) {
    console.warn(`[copy-vad-assets] missing ${f} (run npm install)`);
    continue;
  }
  fs.copyFileSync(src, path.join(out, path.basename(f)));
}
