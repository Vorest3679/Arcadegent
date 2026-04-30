import type { ViewMode } from "../types";

const VIEW_MODE_PATHS: Record<ViewMode, string> = {
  chat: "/",
  arcades: "/arcades"
};

export function readViewModeFromPath(pathname: string): ViewMode {
  return pathname === VIEW_MODE_PATHS.arcades || pathname.startsWith(`${VIEW_MODE_PATHS.arcades}/`)
    ? "arcades"
    : "chat";
}

export function readInitialViewMode(): ViewMode {
  if (typeof window === "undefined") {
    return "chat";
  }
  return readViewModeFromPath(window.location.pathname);
}

export function syncViewModeInUrl(viewMode: ViewMode, options: { replace?: boolean } = {}): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  url.pathname = VIEW_MODE_PATHS[viewMode];
  url.search = "";
  const nextHref = `${url.pathname}${url.search}${url.hash}`;
  const currentHref = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextHref !== currentHref) {
    const action = options.replace ? "replaceState" : "pushState";
    window.history[action]({}, "", nextHref);
  }
}
