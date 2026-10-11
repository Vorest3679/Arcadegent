import type { ChatHistoryTurn, ChatMapArtifacts, ChatSessionDetail } from "../types";
import { toVisibleTurns } from "./sse/chatStream";
import { isTerminalRunStatus } from "./sse/runStream";

export function makeSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `s_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  }
  return `s_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function hasSessionMapArtifacts(artifacts: ChatMapArtifacts): boolean {
  return Boolean(artifacts.route || artifacts.destination || artifacts.shops.length || artifacts.view_payload);
}

export function mapArtifactsFromSession(detail: ChatSessionDetail): ChatMapArtifacts | null {
  const artifacts: ChatMapArtifacts = {
    shops: detail.shops,
    route: detail.route ?? null,
    client_location: detail.client_location ?? null,
    destination: detail.destination ?? null,
    view_payload: detail.view_payload ?? null,
    route_pending: false
  };
  return hasSessionMapArtifacts(artifacts) ? artifacts : null;
}

export function isRunActive(detail: ChatSessionDetail): boolean {
  const run = detail.current_run;
  return Boolean(run && !isTerminalRunStatus(run.status));
}

// Visible history of a detail. The session-level map goes onto the last reply
// when no reply carries one of its own.
export function turnsFromDetail(detail: ChatSessionDetail): ChatHistoryTurn[] {
  const visibleTurns = toVisibleTurns(detail.turns);
  const detailArtifacts = mapArtifactsFromSession(detail);
  if (detailArtifacts && !visibleTurns.some((turn) => Boolean(turn.map_artifacts))) {
    for (let index = visibleTurns.length - 1; index >= 0; index -= 1) {
      if (visibleTurns[index].role === "assistant") {
        visibleTurns[index] = { ...visibleTurns[index], map_artifacts: detailArtifacts };
        break;
      }
    }
  }
  return visibleTurns;
}
