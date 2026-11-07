import type { Settings } from "../contracts/api";
import { api } from "./api";
import { settings, type ClientSettings } from "./settings";

/** Settings stored on the server (PATCH /api/me nests them under `settings`). */
export const SERVER_SETTING_KEYS: readonly (keyof Settings)[] = [
  "brief_time",
  "voice",
  "stt_mode",
  "auto_resume",
  "headphones_mode",
  "playback_rate",
  "show_debug",
];

/** Update a setting locally (applies at once) and sync the server-side ones (best effort). */
export function updateSetting<K extends keyof ClientSettings>(k: K, v: ClientSettings[K]): void {
  settings.patch({ [k]: v } as Partial<ClientSettings>);
  if ((SERVER_SETTING_KEYS as readonly string[]).includes(k))
    void api.patchMe({ [k]: v } as Partial<Settings>).catch(() => {});
}
