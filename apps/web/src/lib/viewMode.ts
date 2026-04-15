import type { ViewMode } from "../types";

export function normalizeViewMode(raw: string | null): ViewMode {
  return raw === "arcades" ? "arcades" : "chat";
}

export function readInitialViewMode(): ViewMode {
  if (typeof window === "undefined") {
    return "chat";
  }
  return normalizeViewMode(new URLSearchParams(window.location.search).get("view"));
}

export function syncViewModeInUrl(viewMode: ViewMode): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  if (viewMode === "chat") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", viewMode);
  }
  const nextHref = `${url.pathname}${url.search}${url.hash}`;
  const currentHref = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextHref !== currentHref) {
    window.history.replaceState({}, "", nextHref);
  }
}
