import type {
  HistoryItem,
  Me,
  OnboardingRequest,
  OpenQuestion,
  RatingRequest,
  SessionCreated,
  Settings,
  TodayBrief,
} from "../contracts/api";
import type { Interest } from "../contracts/brief";
import { auth } from "./auth";
import { config } from "./config";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = auth.get().token;
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${config.apiBase}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<Me>("GET", "/api/me"),
  patchMe: (patch: Partial<Settings> & { research_consent?: boolean }) =>
    request<Me>("PATCH", "/api/me", patch),
  onboarding: (body: OnboardingRequest) => request<Me>("POST", "/api/onboarding", body),
  today: () => request<TodayBrief>("GET", "/api/briefs/today"),
  createSession: (brief_id: string) => request<SessionCreated>("POST", "/api/sessions", { brief_id }),
  history: (days = 14) => request<HistoryItem[]>("GET", `/api/history?days=${days}`),
  interests: () => request<Interest[]>("GET", "/api/memory/interests"),
  patchInterest: (id: string, patch: Partial<Pick<Interest, "label" | "weight" | "explicit_pin">>) =>
    request<Interest>("PATCH", `/api/memory/interests/${encodeURIComponent(id)}`, patch),
  deleteInterest: (id: string) =>
    request<void>("DELETE", `/api/memory/interests/${encodeURIComponent(id)}`),
  questions: () => request<OpenQuestion[]>("GET", "/api/questions"),
  dismissQuestion: (id: string) =>
    request<OpenQuestion>("PATCH", `/api/questions/${encodeURIComponent(id)}`, { status: "dismissed" }),
  rate: (body: RatingRequest) => request<void>("POST", "/api/ratings", body),
};

/** Resolve the WebSocket URL from POST /api/sessions. Browsers can't set headers on a WebSocket,
 * so the bearer token goes in the `token` query parameter. */
export function resolveWsUrl(wsUrl: string, token: string | null): string {
  let url: URL;
  if (/^wss?:\/\//.test(wsUrl)) url = new URL(wsUrl);
  else {
    const base = config.apiBase || window.location.origin;
    url = new URL(wsUrl, base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  }
  if (token) url.searchParams.set("token", token);
  return url.toString();
}

/** Resolve a manifest segment URL (relative URLs are served by the API host). */
export function resolveAudioUrl(url: string): string {
  if (/^(https?:|blob:|data:)/.test(url)) return url;
  return `${config.apiBase}${url}`;
}
