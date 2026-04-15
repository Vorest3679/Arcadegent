import { FormEvent, useCallback, useEffect, useMemo, useRef } from "react";
import { getArcadeDetail, listArcades, listCities, listCounties, listProvinces } from "../api/client";
import { convertClientLocationToGcj02, geocodeAddressToGcj02, getArcadeGcjPoint } from "../lib/amapCoords";
import { buildAmapMarkerUri, buildAmapNavigationUri } from "../lib/amapUri";
import { warmupClientLocationCache } from "../lib/clientLocation";
import { useArcadeBrowserStore } from "../stores/arcadeBrowserStore";
import type { ArcadeSummary, GeoPoint } from "../types";
import { ArcadeDetailPanel, type ArcadeDetailViewModel } from "./arcade/ArcadeDetailPanel";
import { ArcadeSearchPanel } from "./arcade/ArcadeSearchPanel";
import {
  getArcadeFallbackRegionName,
  getArcadeRegionParts,
  getArcadeRegionZoom,
  getKnownRegionCenter,
  isArcadeDetail
} from "./arcade/arcadeBrowserUtils";
import type { MapAction } from "./map/MapActionBar";

const PAGE_SIZE = 20;

export function ArcadeBrowser() {
  const provinceCode = useArcadeBrowserStore((state) => state.provinceCode);
  const cityCode = useArcadeBrowserStore((state) => state.cityCode);
  const paged = useArcadeBrowserStore((state) => state.paged);
  const detail = useArcadeBrowserStore((state) => state.detail);
  const detailError = useArcadeBrowserStore((state) => state.detailError);
  const selectedSourceId = useArcadeBrowserStore((state) => state.selectedSourceId);
  const mapRuntime = useArcadeBrowserStore((state) => state.mapRuntime);
  const mapStatus = useArcadeBrowserStore((state) => state.mapStatus);
  const clientLocation = useArcadeBrowserStore((state) => state.clientLocation);
  const clientOriginGcj = useArcadeBrowserStore((state) => state.clientOriginGcj);
  const selectedRegionPoint = useArcadeBrowserStore((state) => state.selectedRegionPoint);
  const detailRequestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    async function loadProvinces() {
      const rows = await listProvinces();
      if (!cancelled) {
        useArcadeBrowserStore.getState().setProvinces(rows);
      }
    }

    void loadProvinces();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!provinceCode) {
      const store = useArcadeBrowserStore.getState();
      store.setCities([]);
      store.setCounties([]);
      return;
    }

    let cancelled = false;
    async function loadCities() {
      const rows = await listCities(provinceCode);
      if (!cancelled) {
        useArcadeBrowserStore.getState().setCities(rows);
      }
    }

    void loadCities();
    return () => {
      cancelled = true;
    };
  }, [provinceCode]);

  useEffect(() => {
    if (!cityCode) {
      useArcadeBrowserStore.getState().setCounties([]);
      return;
    }

    let cancelled = false;
    async function loadCounties() {
      const rows = await listCounties(cityCode);
      if (!cancelled) {
        useArcadeBrowserStore.getState().setCounties(rows);
      }
    }

    void loadCounties();
    return () => {
      cancelled = true;
    };
  }, [cityCode]);

  useEffect(() => {
    let cancelled = false;

    async function refreshClientLocation() {
      const location = await warmupClientLocationCache();
      if (!cancelled && location) {
        useArcadeBrowserStore.getState().setClientLocation(location);
      }
    }

    void refreshClientLocation();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function resolveOrigin() {
      const store = useArcadeBrowserStore.getState();
      if (!clientLocation) {
        store.setClientOriginGcj(null);
        return;
      }
      const point = await convertClientLocationToGcj02(mapRuntime?.AMap, clientLocation);
      if (!cancelled) {
        store.setClientOriginGcj(point);
      }
    }

    void resolveOrigin();
    return () => {
      cancelled = true;
    };
  }, [clientLocation, mapRuntime]);

  const selectedSummary = useMemo(
    () => paged.items.find((item) => item.source_id === selectedSourceId) ?? null,
    [paged.items, selectedSourceId]
  );

  const loadDetailForItem = useCallback(async (item: ArcadeSummary) => {
    const requestId = ++detailRequestIdRef.current;
    const store = useArcadeBrowserStore.getState();
    store.setDetailLoading(true);
    store.setDetailError("");
    try {
      const payload = await getArcadeDetail(item.source_id);
      if (requestId !== detailRequestIdRef.current) {
        return;
      }
      useArcadeBrowserStore.getState().setDetail(payload);
    } catch (err) {
      if (requestId !== detailRequestIdRef.current) {
        return;
      }
      const latestStore = useArcadeBrowserStore.getState();
      latestStore.setDetailError(err instanceof Error ? err.message : "加载机厅详情失败");
      latestStore.setDetail(null);
    } finally {
      if (requestId === detailRequestIdRef.current) {
        useArcadeBrowserStore.getState().setDetailLoading(false);
      }
    }
  }, []);

  const selectShop = useCallback(async (item: ArcadeSummary) => {
    useArcadeBrowserStore.getState().setSelectedSourceId(item.source_id);
    if (detail?.source_id === item.source_id && !detailError) {
      return;
    }
    await loadDetailForItem(item);
  }, [detail?.source_id, detailError, loadDetailForItem]);

  async function runSearch(page = 1): Promise<void> {
    const state = useArcadeBrowserStore.getState();
    state.setLoading(true);
    state.setError("");

    try {
      const payload = await listArcades({
        keyword: state.keyword,
        province_code: state.provinceCode || undefined,
        city_code: state.cityCode || undefined,
        county_code: state.countyCode || undefined,
        has_arcades: state.hasArcadesOnly ? true : undefined,
        sort_by: state.sortBy,
        sort_order: state.sortOrder,
        sort_title_name: state.sortBy === "title_quantity" ? state.sortTitleName.trim() || undefined : undefined,
        page,
        page_size: PAGE_SIZE
      });
      const latestStore = useArcadeBrowserStore.getState();
      latestStore.setPaged(payload);

      const existing = payload.items.find((item) => item.source_id === latestStore.selectedSourceId) ?? null;
      const fallback = payload.items[0] ?? null;
      const nextSelected = existing ?? fallback;
      if (!nextSelected) {
        latestStore.setSelectedSourceId(null);
        latestStore.setDetail(null);
        latestStore.setDetailError("");
        return;
      }
      latestStore.setSelectedSourceId(nextSelected.source_id);
      await loadDetailForItem(nextSelected);
    } catch (err) {
      useArcadeBrowserStore.getState().setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      useArcadeBrowserStore.getState().setLoading(false);
    }
  }

  useEffect(() => {
    void runSearch(1);
  }, []);

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    await runSearch(1);
  }

  const selectedArcade = detail?.source_id === selectedSourceId ? detail : selectedSummary;
  const selectedDetail = isArcadeDetail(selectedArcade) ? selectedArcade : null;
  const selectedCatalogPoint = getArcadeGcjPoint(selectedArcade);
  const selectedKnownRegionCenter = useMemo(() => getKnownRegionCenter(selectedArcade), [selectedArcade]);
  const selectedRegionParts = useMemo(() => getArcadeRegionParts(selectedArcade), [selectedArcade]);
  const selectedRegionQuery = selectedRegionParts.join("");
  const selectedRegionLabel = selectedRegionParts.join(" / ");
  const selectedFallbackRegionName = useMemo(() => getArcadeFallbackRegionName(selectedArcade), [selectedArcade]);

  useEffect(() => {
    let cancelled = false;

    async function resolveSelectedRegionPoint() {
      useArcadeBrowserStore.getState().setSelectedRegionPoint(null);
      if (selectedCatalogPoint || !selectedArcade || !selectedRegionQuery || !mapRuntime?.AMap) {
        return;
      }
      const point = await geocodeAddressToGcj02(mapRuntime.AMap, selectedRegionQuery, selectedArcade.city_name);
      if (!cancelled && point) {
        useArcadeBrowserStore.getState().setSelectedRegionPoint({
          sourceId: selectedArcade.source_id,
          query: selectedRegionQuery,
          label: selectedRegionLabel,
          point
        });
      }
    }

    void resolveSelectedRegionPoint();
    return () => {
      cancelled = true;
    };
  }, [
    mapRuntime,
    selectedArcade,
    selectedArcade?.city_name,
    selectedCatalogPoint,
    selectedRegionLabel,
    selectedRegionQuery
  ]);

  let selectedRegionCenterPoint: GeoPoint | null = null;
  if (
    selectedRegionPoint
    && selectedRegionPoint.sourceId === selectedArcade?.source_id
    && selectedRegionPoint.query === selectedRegionQuery
  ) {
    selectedRegionCenterPoint = selectedRegionPoint.point;
  }

  const selectedPoint = selectedCatalogPoint;
  const mapCenter = selectedPoint ?? selectedRegionCenterPoint ?? selectedKnownRegionCenter;
  const mapZoom = selectedPoint ? 15 : getArcadeRegionZoom(selectedArcade);
  const handleMarkerSelect = useCallback((item: ArcadeSummary) => {
    void selectShop(item);
  }, [selectShop]);

  const pageHint = useMemo(() => {
    if (paged.total <= 0) {
      return "No results";
    }
    const start = (paged.page - 1) * paged.page_size + 1;
    const end = Math.min(paged.total, paged.page * paged.page_size);
    return `${start}-${end} / ${paged.total}`;
  }, [paged]);

  const actions = useMemo<MapAction[]>(() => {
    if (!selectedPoint || !selectedArcade) {
      return [];
    }

    const markerHref = buildAmapMarkerUri({
      point: selectedPoint,
      name: selectedArcade.name
    });
    const navHref = buildAmapNavigationUri({
      destination: selectedPoint,
      destinationName: selectedArcade.name,
      origin: clientOriginGcj,
      originName: clientLocation?.region_text || clientLocation?.formatted_address || "我的位置",
      mode: "walk"
    });

    return [
      {
        key: "view",
        label: "在高德查看",
        href: markerHref,
        emphasis: "secondary"
      },
      {
        key: "navigate",
        label: "高德导航",
        href: navHref,
        emphasis: "primary"
      }
    ];
  }, [clientLocation, clientOriginGcj, selectedArcade, selectedPoint]);

  const mapStatusText = useMemo(() => {
    if (mapStatus.state === "disabled") {
      return mapStatus.message || "未配置高德地图 Web JS Key；已保留列表和高德导航。";
    }
    if (mapStatus.state === "error") {
      return mapStatus.message || "地图加载失败；已保留列表和高德导航。";
    }
    if (mapStatus.state === "loading") {
      return "地图加载中...";
    }
    if (!selectedArcade) {
      return "选择一个机厅后查看地图";
    }
    if (!selectedPoint && (mapCenter || selectedRegionLabel)) {
      return `该机厅暂无精确定位，地图已停在 ${selectedRegionPoint?.label || selectedRegionLabel}`;
    }
    if (!selectedPoint) {
      return "该机厅暂时没有可用地图定位";
    }
    return "";
  }, [mapCenter, mapStatus, selectedArcade, selectedPoint, selectedRegionLabel, selectedRegionPoint?.label]);

  const detailView = useMemo<ArcadeDetailViewModel>(() => ({
    mapCenter,
    mapZoom,
    fallbackRegionName: selectedFallbackRegionName,
    selectedPoint,
    selectedRegionLabel,
    selectedRegionPoint,
    mapStatusText,
    actions
  }), [
    actions,
    mapCenter,
    mapStatusText,
    mapZoom,
    selectedFallbackRegionName,
    selectedPoint,
    selectedRegionLabel,
    selectedRegionPoint
  ]);

  return (
    <div className="browser-shell">
      <header className="browser-hero">
        <h2>Arcade Explorer</h2>
        <p>筛选机厅、在地图上看点位，并直接跳转到高德查看或导航。</p>
      </header>

      <main className="browser-layout">
        <ArcadeSearchPanel
          pageHint={pageHint}
          onSubmit={onSubmit}
          onSelectShop={(item) => void selectShop(item)}
          onSearchPage={(page) => void runSearch(page)}
        />
        <ArcadeDetailPanel
          selectedArcade={selectedArcade}
          selectedDetail={selectedDetail}
          view={detailView}
          onMarkerSelect={handleMarkerSelect}
        />
      </main>
    </div>
  );
}
