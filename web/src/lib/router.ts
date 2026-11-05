import { useSyncExternalStore } from "react";

/** Tiny hash router (#/today, #/memory, ...): works in a home-screen PWA without server rewrites. */
export type Route = "today" | "history" | "memory" | "questions" | "settings" | "onboarding";

const ROUTES: Route[] = ["today", "history", "memory", "questions", "settings", "onboarding"];

function current(): Route {
  const h = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return (ROUTES as string[]).includes(h) ? (h as Route) : "today";
}

function subscribe(cb: () => void) {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, current, current);
}

export function navigate(r: Route): void {
  window.location.hash = `/${r}`;
}
