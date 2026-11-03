import type { ChatMapArtifacts } from "../types";

// Whether the artifacts give the map card something to draw. A destination
// alone is not drawn: it always comes with a route or shops.
export function hasMapContent(artifacts: ChatMapArtifacts | null | undefined): artifacts is ChatMapArtifacts {
  return Boolean(
    artifacts && (artifacts.route || artifacts.shops.length > 0 || artifacts.view_payload || artifacts.route_pending)
  );
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
