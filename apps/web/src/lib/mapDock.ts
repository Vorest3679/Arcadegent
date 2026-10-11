import type { ChatHistoryTurn, ChatMapArtifacts } from "../types";

export function hasMapContent(artifacts: ChatMapArtifacts | null | undefined): artifacts is ChatMapArtifacts {
  return Boolean(artifacts && (artifacts.route || artifacts.shops.length > 0 || artifacts.view_payload));
}

// The one map the dock shows: the live run's map, else the last reply's.
export function latestMapArtifacts(
  turns: ChatHistoryTurn[],
  liveArtifacts: ChatMapArtifacts | null,
  runLive: boolean
): ChatMapArtifacts | null {
  if (runLive && hasMapContent(liveArtifacts)) {
    return liveArtifacts;
  }
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const artifacts = turns[index].map_artifacts;
    if (turns[index].role === "assistant" && hasMapContent(artifacts)) {
      return artifacts;
    }
  }
  return null;
}

// Identifies "a new map arrived". Counting maps (not run ids) keeps the key
// unchanged when a live map is handed over to the history reply carrying it.
export function mapArrivalKey(
  sessionId: string | null,
  turns: ChatHistoryTurn[],
  liveArtifacts: ChatMapArtifacts | null,
  runLive: boolean
): string | null {
  const historical = turns.filter((turn) => turn.role === "assistant" && hasMapContent(turn.map_artifacts)).length;
  const live = runLive && hasMapContent(liveArtifacts) ? 1 : 0;
  const count = historical + live;
  return count > 0 ? `${sessionId ?? "new"}:${count}` : null;
}

function formatKm(distance: number): string {
  return distance >= 1000 ? `${(distance / 1000).toFixed(distance >= 10_000 ? 0 : 1)} 公里` : `${Math.round(distance)} 米`;
}

export function mapSummary(artifacts: ChatMapArtifacts): string {
  const distance = artifacts.route?.distance_m;
  if (typeof distance === "number" && Number.isFinite(distance)) {
    return `路线 ${formatKm(distance)}`;
  }
  if (artifacts.route_pending && !artifacts.route) {
    return "路线规划中";
  }
  if (artifacts.shops.length > 0) {
    return `地图 · ${artifacts.shops.length} 家机厅`;
  }
  return "地图";
}
