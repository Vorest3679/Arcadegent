import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approximateWgs84ToGcj02,
  getArcadeGcjPoint,
  normalizePointToGcj02,
  normalizeRouteToGcj02
} from "../../lib/amapCoords";
import { buildAmapMarkerUri, buildAmapNavigationUri } from "../../lib/amapUri";
import type { AgentMapScene, ArcadeSummary, ChatMapArtifacts, GeoPoint, RouteSummary } from "../../types";
import { AmapMapCanvas, type AmapRuntime } from "./AmapMapCanvas";
import { AmapRouteOverlay } from "./AmapRouteOverlay";
import { AmapShopMarkers } from "./AmapShopMarkers";
import { MapActionBar, type MapAction } from "./MapActionBar";

type MapStatus = {
  state: "idle" | "loading" | "ready" | "disabled" | "error";
  message: string;
};

type AgentMapCardProps = {
  artifacts: ChatMapArtifacts;
};

function readPayloadString(payload: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = payload?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readPayloadScene(payload: Record<string, unknown> | null | undefined): AgentMapScene | null {
  const scene = readPayloadString(payload, "scene");
  if (scene === "agent_candidates" || scene === "agent_route") {
    return scene;
  }
  return null;
}

function routeModeToUriMode(mode?: string | null): "walk" | "car" {
  const normalized = mode?.toLowerCase();
  return normalized === "driving" || normalized === "drive" || normalized === "car" ? "car" : "walk";
}

function routeModeLabel(mode?: string | null): string {
  const normalized = mode?.toLowerCase();
  if (normalized === "driving" || normalized === "drive" || normalized === "car") {
    return "驾车";
  }
  if (normalized === "walking" || normalized === "walk") {
    return "步行";
  }
  return mode || "路线";
}

function formatDistance(distance?: number | null): string {
  if (typeof distance !== "number" || !Number.isFinite(distance)) {
    return "距离待确认";
  }
  if (distance >= 1000) {
    return `${(distance / 1000).toFixed(distance >= 10_000 ? 0 : 1)} km`;
  }
  return `${Math.round(distance)} m`;
}

function formatDuration(duration?: number | null): string {
  if (typeof duration !== "number" || !Number.isFinite(duration)) {
    return "时间待确认";
  }
  const minutes = Math.max(1, Math.round(duration / 60));
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
  }
  return `${minutes} 分钟`;
}

function firstShopWithGeo(shops: ArcadeSummary[]): ArcadeSummary | null {
  return shops.find((shop) => Boolean(getArcadeGcjPoint(shop))) ?? null;
}

function findShop(shops: ArcadeSummary[], sourceId?: number | null): ArcadeSummary | null {
  if (sourceId == null) {
    return null;
  }
  return shops.find((shop) => shop.source_id === sourceId) ?? null;
}

function getFallbackRegionName(shop?: ArcadeSummary | null): string {
  return shop?.city_name?.trim() || shop?.province_name?.trim() || shop?.county_name?.trim() || "";
}

function routeDestinationPoint(route: RouteSummary | null, destination?: ArcadeSummary | null): GeoPoint | null {
  return getArcadeGcjPoint(destination) ?? normalizePointToGcj02(route?.destination);
}

function hasMapContent(artifacts: ChatMapArtifacts): boolean {
  return Boolean(artifacts.route || artifacts.shops.length || artifacts.view_payload);
}

export function AgentMapCard({ artifacts }: AgentMapCardProps) {
  const [mapRuntime, setMapRuntime] = useState<AmapRuntime | null>(null);
  const [mapStatus, setMapStatus] = useState<MapStatus>({ state: "idle", message: "" });
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(() => {
    return artifacts.destination?.source_id ?? firstShopWithGeo(artifacts.shops)?.source_id ?? artifacts.shops[0]?.source_id ?? null;
  });

  const normalizedRoute = useMemo(() => normalizeRouteToGcj02(artifacts.route), [artifacts.route]);
  const scene = readPayloadScene(artifacts.view_payload) ?? (normalizedRoute ? "agent_route" : "agent_candidates");
  const hasRoute = scene === "agent_route" && Boolean(normalizedRoute);
  const destination = artifacts.destination ?? findShop(artifacts.shops, selectedSourceId) ?? artifacts.shops[0] ?? null;
  const selectedShop = findShop(artifacts.shops, selectedSourceId) ?? destination ?? firstShopWithGeo(artifacts.shops);
  const selectedPoint = getArcadeGcjPoint(selectedShop);
  const destinationPoint = routeDestinationPoint(normalizedRoute, destination);
  const clientOrigin = artifacts.client_location ? approximateWgs84ToGcj02(artifacts.client_location) : null;
  const routeOrigin = normalizedRoute?.origin ?? clientOrigin;
  const fallbackShop = destination ?? selectedShop;
  const title =
    readPayloadString(artifacts.view_payload, "title")
    ?? (hasRoute
      ? `前往 ${destination?.name ?? "目标机厅"}`
      : artifacts.shops.length
        ? "候选机厅地图"
        : "地图卡片");
  const subtitle = hasRoute
    ? `${routeModeLabel(normalizedRoute?.mode)} · ${formatDistance(normalizedRoute?.distance_m)} · ${formatDuration(normalizedRoute?.duration_s)}`
    : `${artifacts.shops.length} 个候选机厅，${artifacts.shops.filter((shop) => getArcadeGcjPoint(shop)).length} 个可定位`;
  const mapCenter = hasRoute
    ? destinationPoint ?? routeOrigin ?? selectedPoint
    : selectedPoint ?? getArcadeGcjPoint(firstShopWithGeo(artifacts.shops));
  const mapZoom = hasRoute ? 13 : selectedPoint ? 14 : 11;

  useEffect(() => {
    const nextSelected =
      artifacts.destination?.source_id ?? firstShopWithGeo(artifacts.shops)?.source_id ?? artifacts.shops[0]?.source_id ?? null;
    setSelectedSourceId((current) => {
      if (current != null && artifacts.shops.some((shop) => shop.source_id === current)) {
        return current;
      }
      return nextSelected;
    });
  }, [artifacts.destination?.source_id, artifacts.shops]);

  const handleMapRuntimeChange = useCallback((runtime: AmapRuntime | null) => {
    setMapRuntime(runtime);
  }, []);

  const handleMapStatusChange = useCallback((state: MapStatus["state"], message?: string) => {
    setMapStatus({ state, message: message ?? "" });
  }, []);

  const actions = useMemo<MapAction[]>(() => {//定义地图操作按钮，优先展示路线相关操作，其次是候选机厅相关操作
    if (hasRoute) {
      const target = destinationPoint;
      if (!target) {
        return [];
      }
      const destinationName = destination?.name ?? "目标机厅";
      return [
        {
          key: "route-web",
          label: "网页打开",
          href: buildAmapNavigationUri({
            destination: target,
            destinationName,
            origin: routeOrigin,
            originName: artifacts.client_location?.region_text || artifacts.client_location?.formatted_address || "我的位置",
            mode: routeModeToUriMode(normalizedRoute?.mode),
            callnative: false
          }),
          emphasis: "secondary"
        },
        {
          key: "route-app",
          label: "打开高德 App",
          href: buildAmapNavigationUri({
            destination: target,
            destinationName,
            origin: routeOrigin,
            originName: artifacts.client_location?.region_text || artifacts.client_location?.formatted_address || "我的位置",
            mode: routeModeToUriMode(normalizedRoute?.mode),
            callnative: true
          }),
          emphasis: "primary"
        }
      ];
    }

    if (!selectedShop || !selectedPoint) {
      return [];
    }
    return [
      {
        key: "candidate-web",
        label: "网页打开",
        href: buildAmapMarkerUri({ point: selectedPoint, name: selectedShop.name, callnative: false }),
        emphasis: "secondary"
      },
      {
        key: "candidate-app",
        label: "打开高德 App",
        href: buildAmapMarkerUri({ point: selectedPoint, name: selectedShop.name, callnative: true }),
        emphasis: "primary"
      }
    ];
  }, [
    artifacts.client_location?.formatted_address,
    artifacts.client_location?.region_text,
    destination?.name,
    destinationPoint,
    hasRoute,
    normalizedRoute?.mode,
    routeOrigin,
    selectedPoint,
    selectedShop
  ]);

  const mapStatusText = useMemo(() => {
    if (mapStatus.state === "disabled") {
      return mapStatus.message || "未配置高德地图 Web JS Key；已保留候选列表和高德跳转。";
    }
    if (mapStatus.state === "error") {
      return mapStatus.message || "地图加载失败；已保留候选列表和高德跳转。";
    }
    if (mapStatus.state === "loading") {
      return "地图加载中...";
    }
    if (hasRoute && artifacts.route_pending) {
      return "路线事件已到达，正在等待最终回复补全文本。";
    }
    if (hasRoute && !destinationPoint) {
      return "路线已生成，但终点坐标暂不可用。";
    }
    if (!hasRoute && !artifacts.shops.some((shop) => getArcadeGcjPoint(shop))) {
      return "候选机厅暂时没有可地图定位的坐标。";
    }
    return "";
  }, [artifacts.route_pending, artifacts.shops, destinationPoint, hasRoute, mapStatus]);

  if (!hasMapContent(artifacts)) {
    return null;
  }

  return (
    <section
      className={`agent-map-card ${hasRoute ? "is-route" : "is-candidates"}`}
      data-testid={hasRoute ? "agent-route-card" : "agent-candidates-card"}
    >
      <div className="agent-map-head">
        <div>
          <p className="agent-map-kicker">{hasRoute ? "路线卡片" : "候选地图"}</p>
          <h3>{title}</h3>
          <span>{subtitle}</span>
        </div>
        {artifacts.route_pending ? <small>渐进展示</small> : null}
      </div>

      <div className="agent-map-grid">
        <div className="agent-map-canvas-wrap">
          <AmapMapCanvas
            center={mapCenter}
            zoom={mapZoom}
            fallbackRegionName={getFallbackRegionName(fallbackShop)}
            emptyMessage={hasRoute ? "路线地图" : "候选机厅地图"}
            onRuntimeChange={handleMapRuntimeChange}
            onStatusChange={handleMapStatusChange}
          />
          {hasRoute ? (
            <AmapRouteOverlay runtime={mapRuntime} route={normalizedRoute} />
          ) : (
            <AmapShopMarkers
              runtime={mapRuntime}
              shops={artifacts.shops}
              selectedSourceId={selectedSourceId}
              onSelectShop={(shop) => setSelectedSourceId(shop.source_id)}
            />
          )}
          {mapStatusText ? <div className="agent-map-state">{mapStatusText}</div> : null}
        </div>

        <div className="agent-map-side">
          {hasRoute ? (
            <div className="agent-route-summary">
              <strong>{destination?.name ?? "目标机厅"}</strong>
              <p>{destination?.address ?? "终点地址待补充"}</p>
              <div className="agent-route-metrics">
                <span>{formatDistance(normalizedRoute?.distance_m)}</span>
                <span>{formatDuration(normalizedRoute?.duration_s)}</span>
                <span>{normalizedRoute?.provider ?? "provider 待确认"}</span>
              </div>
              {normalizedRoute?.hint ? <small>{normalizedRoute.hint}</small> : null}
            </div>
          ) : (
            <ul className="agent-candidate-list">
              {artifacts.shops.slice(0, 6).map((shop) => {
                const mapped = Boolean(getArcadeGcjPoint(shop));
                const active = shop.source_id === selectedSourceId;
                return (
                  <li key={shop.source_id}>
                    <button
                      type="button"
                      className={`agent-candidate-btn${active ? " is-active" : ""}`}
                      onClick={() => setSelectedSourceId(shop.source_id)}
                      data-testid={`agent-candidate-${shop.source_id}`}
                    >
                      <b>{shop.name}</b>
                      <span>{shop.address || "暂无地址"}</span>
                      <small>{mapped ? "地图已定位" : "暂无地图定位"}</small>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <MapActionBar actions={actions} />
        </div>
      </div>
    </section>
  );
}
