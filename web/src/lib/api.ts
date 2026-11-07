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
import {
  historyFromApi,
  interestsFromApi,
  mePatchToApi,
  questionFromApi,
  questionsFromApi,
  turnsFromApi,
} from "./normalize";

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
    request<Me>("PATCH", "/api/me", mePatchToApi(patch)),
  onboarding: (body: OnboardingRequest) => request<Me>("POST", "/api/onboarding", body),
  /** 200 ready brief, 202 `{status: "pending" | "failed", brief_id: null | id}`, or 409 before onboarding. */
  today: async (): Promise<TodayBrief> => {
    try {
      return await request<TodayBrief>("GET", "/api/briefs/today");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409)
        return { brief_id: null, status: "onboarding_required", script: null, manifest: null };
      throw err;
    }
  },
  createSession: (brief_id: string) => request<SessionCreated>("POST", "/api/sessions", { brief_id }),
  history: async (days = 14): Promise<HistoryItem[]> =>
    historyFromApi(await request<unknown>("GET", `/api/history?days=${days}`)),
  transcript: async (sessionId: string) =>
    turnsFromApi(await request<unknown>("GET", `/api/sessions/${encodeURIComponent(sessionId)}/transcript`)),
  interests: async (): Promise<Interest[]> => interestsFromApi(await request<unknown>("GET", "/api/memory/interests")),
  patchInterest: (id: string, patch: Partial<Pick<Interest, "label" | "weight" | "explicit_pin">>) =>
    request<Interest>("PATCH", `/api/memory/interests/${encodeURIComponent(id)}`, patch),
  deleteInterest: (id: string) =>
    request<void>("DELETE", `/api/memory/interests/${encodeURIComponent(id)}`),
  questions: async (): Promise<OpenQuestion[]> => questionsFromApi(await request<unknown>("GET", "/api/questions")),
  dismissQuestion: async (id: string): Promise<OpenQuestion> =>
    questionFromApi(
      await request<Record<string, unknown>>("PATCH", `/api/questions/${encodeURIComponent(id)}`, {
        status: "dismissed",
      }),
    ),
  rate: (body: RatingRequest) => request<void>("POST", "/api/ratings", body),
};

/** Resolve the WebSocket URL from POST /api/sessions. Browsers can't set headers on a WebSocket,
 * so the bearer token goes in the `token` query parameter.
 *
 * Same-origin mode (no VITE_API_BASE): connect to the ws_url's path on this page's origin. The
 * backend builds an absolute ws_url from its own view of the host, which behind the Vite proxy or an
 * HTTPS tunnel is `ws://localhost:8000` — unreachable from a phone, and mixed content on https. */
export function resolveWsUrl(wsUrl: string, token: string | null, apiBase = config.apiBase): string {
  const absolute = /^wss?:\/\//.test(wsUrl);
  let url: URL;
  if (absolute && apiBase) url = new URL(wsUrl);
  else {
    const u = absolute ? new URL(wsUrl) : null;
    url = new URL(u ? u.pathname + u.search : wsUrl, apiBase || window.location.origin);
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
