import { Info, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import type { ArcadeDetail, ArcadeSummary, GeoPoint } from "../../types";
import { useArcadeBrowserStore, type SelectedRegionPoint } from "../../stores/arcadeBrowserStore";
import { AmapMapCanvas } from "../map/AmapMapCanvas";
import { AmapRouteOverlay } from "../map/AmapRouteOverlay";
import { AmapShopMarkers } from "../map/AmapShopMarkers";
import { MapActionBar, type MapAction } from "../map/MapActionBar";

export type ArcadeDetailViewModel = {
  mapCenter: GeoPoint | null;
  mapZoom: number;
  fallbackRegionName: string;
  selectedPoint: GeoPoint | null;
  selectedRegionLabel: string;
  selectedRegionPoint: SelectedRegionPoint | null;
  mapStatusText: string;
  actions: MapAction[];
};

type ArcadeDetailPanelProps = {
  selectedArcade: ArcadeSummary | ArcadeDetail | null;
  selectedDetail: ArcadeDetail | null;
  view: ArcadeDetailViewModel;
  onMarkerSelect: (item: ArcadeSummary) => void;
};

export function ArcadeDetailPanel({
  selectedArcade,
  selectedDetail,
  view,
  onMarkerSelect
}: ArcadeDetailPanelProps) {
  const detailLoading = useArcadeBrowserStore((state) => state.detailLoading);
  const detailError = useArcadeBrowserStore((state) => state.detailError);
  const mapRuntime = useArcadeBrowserStore((state) => state.mapRuntime);
  const selectedSourceId = useArcadeBrowserStore((state) => state.selectedSourceId);
  const setMapRuntime = useArcadeBrowserStore((state) => state.setMapRuntime);
  const setMapStatus = useArcadeBrowserStore((state) => state.setMapStatus);
  const shouldLoadMap = Boolean(selectedArcade);
  const positionHint = view.selectedPoint
    ? `地图坐标：${view.selectedPoint.lng.toFixed(6)}, ${view.selectedPoint.lat.toFixed(6)}`
    : view.mapCenter || view.selectedRegionLabel
      ? `该机厅暂时没有精确地图坐标，地图已停在 ${view.selectedRegionPoint?.label || view.selectedRegionLabel}`
      : "该机厅暂时没有可用地图坐标";

  return (
    <Card className="gap-4 p-4 sm:p-5 lg:sticky lg:top-0">
      <h2 className="text-sm font-semibold">地图与详情</h2>

      <div className="relative">
        {shouldLoadMap ? (
          <>
            <AmapMapCanvas
              center={view.mapCenter}
              zoom={view.mapZoom}
              fallbackRegionName={view.fallbackRegionName}
              emptyMessage="等待地图就绪"
              onRuntimeChange={setMapRuntime}
              onStatusChange={setMapStatus}
            />
            <AmapShopMarkers
              runtime={mapRuntime}
              shop={selectedArcade}
              point={view.selectedPoint}
              selectedSourceId={selectedSourceId}
              onSelectShop={onMarkerSelect}
            />
            <AmapRouteOverlay runtime={mapRuntime} route={null} />
          </>
        ) : (
          <div className="amap-canvas-shell" data-testid="arcade-map-placeholder">
            <div className="amap-empty-copy">选择一个机厅后加载地图</div>
          </div>
        )}
        {view.mapStatusText ? (
          <p className="mt-2 rounded-md bg-highlight-soft px-3 py-2 text-sm text-highlight-foreground">{view.mapStatusText}</p>
        ) : null}
      </div>

      {detailLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          正在加载详情...
        </p>
      ) : null}
      {!detailLoading && !selectedArcade ? <p className="text-sm text-muted-foreground">从列表选择一个机厅查看详情。</p> : null}
      {detailError ? <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{detailError}</p> : null}
      {selectedArcade ? (
        <div className="grid gap-2">
          <h3 className="text-lg font-semibold" data-testid="browser-detail-title">{selectedArcade.name}</h3>
          <p className="text-sm">{selectedArcade.address || "暂无地址"}</p>
          <p className="text-sm text-muted-foreground">{selectedArcade.transport || "暂无交通信息"}</p>
          <p className="text-xs text-muted-foreground">{positionHint}</p>
          <MapActionBar actions={view.actions} />
          {selectedDetail ? (
            <p className="flex gap-2 rounded-md bg-muted px-3 py-2 text-sm">
              <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              {selectedDetail.comment || "暂无备注"}
            </p>
          ) : null}
          {selectedDetail ? (
            <>
              <h4 className="mt-2 text-sm font-semibold">机台信息（{selectedDetail.arcades.length}）</h4>
              <ul className="grid gap-2" data-testid="arcade-title-list">
                {selectedDetail.arcades.map((item, idx) => (
                  <li
                    key={`${item.title_id}-${idx}`}
                    className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg border bg-card px-3 py-2 text-sm"
                  >
                    <b className="font-medium">{item.title_name || "未知机种"}</b>
                    <span className="text-muted-foreground">数量：{item.quantity ?? "-"}</span>
                    <span className="text-muted-foreground">版本：{item.version || "-"}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">正在加载详细机台信息...</p>
          )}
        </div>
      ) : null}
    </Card>
  );
}
