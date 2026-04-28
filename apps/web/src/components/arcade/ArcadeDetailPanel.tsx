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
  const paged = useArcadeBrowserStore((state) => state.paged);
  const selectedSourceId = useArcadeBrowserStore((state) => state.selectedSourceId);
  const setMapRuntime = useArcadeBrowserStore((state) => state.setMapRuntime);
  const setMapStatus = useArcadeBrowserStore((state) => state.setMapStatus);
  const positionHint = view.selectedPoint
    ? `地图坐标：${view.selectedPoint.lng.toFixed(6)}, ${view.selectedPoint.lat.toFixed(6)}`
    : view.mapCenter || view.selectedRegionLabel
      ? `该机厅暂时没有精确地图坐标，地图已停在 ${view.selectedRegionPoint?.label || view.selectedRegionLabel}`
      : "该机厅暂时没有可用地图坐标";

  return (
    <aside className="browser-card browser-detail">
      <div className="browser-detail-head">
        <div>
          <strong>地图与详情</strong>
        </div>
      </div>

      <div className="browser-map-panel">
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
          shops={paged.items}
          shop={selectedArcade}
          point={view.selectedPoint}
          selectedSourceId={selectedSourceId}
          onSelectShop={onMarkerSelect}
        />
        <AmapRouteOverlay runtime={mapRuntime} route={null} />
        {view.mapStatusText ? <div className="browser-map-state">{view.mapStatusText}</div> : null}
      </div>

      {detailLoading ? <p className="browser-detail-note">正在加载详情...</p> : null}
      {!detailLoading && !selectedArcade ? <p className="browser-detail-note">从左侧列表选择一个机厅查看详情。</p> : null}
      {detailError ? <p className="browser-error">{detailError}</p> : null}
      {selectedArcade ? (
        <div className="browser-detail-content">
          <h3 data-testid="browser-detail-title">{selectedArcade.name}</h3>
          <p>{selectedArcade.address || "暂无地址"}</p>
          <p>{selectedArcade.transport || "暂无交通信息"}</p>
          <p className="browser-map-hint">{positionHint}</p>
          <MapActionBar actions={view.actions} />
          {selectedDetail ? (
            <p className="browser-comment">{selectedDetail.comment || "暂无备注"}</p>
          ) : null}
          {selectedDetail ? (
            <>
              <h4>机台信息（{selectedDetail.arcades.length}）</h4>
              <ul className="browser-title-list">
                {selectedDetail.arcades.map((item, idx) => (
                  <li key={`${item.title_id}-${idx}`}>
                    <b>{item.title_name || "未知机种"}</b>
                    <span>数量：{item.quantity ?? "-"}</span>
                    <span>版本：{item.version || "-"}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="browser-detail-note">正在加载详细机台信息...</p>
          )}
        </div>
      ) : null}
    </aside>
  );
}
