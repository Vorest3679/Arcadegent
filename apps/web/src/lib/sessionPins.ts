import type { ChatSessionSummary } from "../types";

function pinnedTime(session: ChatSessionSummary): number {
  if (!session.pinned_at) {
    return 0;
  }
  const parsed = Date.parse(session.pinned_at);
  return Number.isNaN(parsed) ? 0 : parsed;
}

// Pinned sessions first (newest pin on top), then the rest in the order the
// list API returned. Pins are stored by the backend; this only affects display,
// so the list's "most recently updated" order still drives auto-opening.
export function orderSessionsByPins(sessions: ChatSessionSummary[]): ChatSessionSummary[] {
  if (!sessions.some((item) => item.pinned_at)) {
    return sessions;
  }
  const pinned = sessions.filter((item) => Boolean(item.pinned_at));
  const rest = sessions.filter((item) => !item.pinned_at);
  pinned.sort((a, b) => pinnedTime(b) - pinnedTime(a));
  return [...pinned, ...rest];
}
