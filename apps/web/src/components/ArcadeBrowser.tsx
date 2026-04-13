import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getArcadeDetail, listArcades, listCities, listCounties, listProvinces } from "../api/client";
import { convertClientLocationToGcj02, geocodeAddressToGcj02, getArcadeGcjPoint } from "../lib/amapCoords";
import { buildAmapMarkerUri, buildAmapNavigationUri } from "../lib/amapUri";
import { loadCachedClientLocation, warmupClientLocationCache } from "../lib/clientLocation";
import type { ArcadeDetail, ArcadeSortBy, ArcadeSummary, GeoPoint, PagedArcades, RegionItem, SortOrder } from "../types";
import { AmapMapCanvas, type AmapRuntime } from "./map/AmapMapCanvas";
import { MapActionBar, type MapAction } from "./map/MapActionBar";
import { AmapRouteOverlay } from "./map/AmapRouteOverlay";
import { AmapShopMarkers } from "./map/AmapShopMarkers";

function usePagedState(): [PagedArcades, (payload: PagedArcades) => void] {
  const [data, setData] = useState<PagedArcades>({
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
    total_pages: 0
  });
  return [data, setData];
}

type MapStatus = {
  state: "idle" | "loading" | "ready" | "disabled" | "error";
  message: string;
};

type SelectedRegionPoint = {
  sourceId: number;
  query: string;
  label: string;
  point: GeoPoint;
};

const KNOWN_REGION_CENTERS: Record<string, [number, number]> = {
  北京市: [116.4074, 39.9042],
  天津市: [117.2000, 39.0842],
  河北省: [114.5149, 38.0428],
  山西省: [112.5492, 37.8570],
  内蒙古自治区: [111.7492, 40.8426],
  辽宁省: [123.4315, 41.8057],
  吉林省: [125.3235, 43.8171],
  黑龙江省: [126.6424, 45.7560],
  上海市: [121.4737, 31.2304],
  江苏省: [118.7969, 32.0603],
  浙江省: [120.1551, 30.2741],
  安徽省: [117.2272, 31.8206],
  福建省: [119.2965, 26.0745],
  江西省: [115.8582, 28.6829],
  山东省: [117.1201, 36.6512],
  河南省: [113.6254, 34.7466],
  湖北省: [114.3054, 30.5931],
  湖南省: [112.9388, 28.2282],
  广东省: [113.2644, 23.1291],
  广西壮族自治区: [108.3669, 22.8170],
  海南省: [110.3492, 20.0174],
  重庆市: [106.5516, 29.5630],
  四川省: [104.0668, 30.5728],
  贵州省: [106.6302, 26.6470],
  云南省: [102.8329, 24.8801],
  西藏自治区: [91.1175, 29.6475],
  陕西省: [108.9398, 34.3416],
  甘肃省: [103.8343, 36.0611],
  青海省: [101.7782, 36.6171],
  宁夏回族自治区: [106.2309, 38.4872],
  新疆维吾尔自治区: [87.6168, 43.8256]
};

function compactRegionParts(parts: Array<string | null | undefined>): string[] {
  return parts.reduce<string[]>((acc, part) => {
    const trimmed = part?.trim();
    if (!trimmed || acc.includes(trimmed)) {
      return acc;
    }
    return [...acc, trimmed];
  }, []);
}

function getArcadeRegionParts(arcade?: ArcadeSummary | null): string[] {
  return compactRegionParts([arcade?.province_name, arcade?.city_name, arcade?.county_name]);
}

function getArcadeFallbackRegionName(arcade?: ArcadeSummary | null): string {
  const city = arcade?.city_name?.trim();
  if (city) {
    return city;
  }
  const province = arcade?.province_name?.trim();
  if (province) {
    return province;
  }
  return arcade?.county_name?.trim() ?? "";
}

function getKnownRegionCenter(arcade?: ArcadeSummary | null): GeoPoint | null {
  const regionNames = compactRegionParts([arcade?.county_name, arcade?.city_name, arcade?.province_name]);
  const center = regionNames
    .map((regionName) => KNOWN_REGION_CENTERS[regionName])
    .find(Boolean);
  if (!center) {
    return null;
  }
  return {
    lng: center[0],
    lat: center[1],
    coord_system: "gcj02",
    source: "geocode",
    precision: "approx"
  };
}

function getArcadeRegionZoom(arcade?: ArcadeSummary | null): number {
  if (arcade?.county_name) {
    return 12;
  }
  if (arcade?.city_name) {
    return 10;
  }
  if (arcade?.province_name) {
    return 7;
  }
  return 5;
}

function isArcadeDetail(arcade?: ArcadeSummary | ArcadeDetail | null): arcade is ArcadeDetail {
  return Boolean(arcade && Array.isArray((arcade as ArcadeDetail).arcades));
}

export function ArcadeBrowser() {
  const [provinces, setProvinces] = useState<RegionItem[]>([]);
  const [cities, setCities] = useState<RegionItem[]>([]);
  const [counties, setCounties] = useState<RegionItem[]>([]);
  const [keyword, setKeyword] = useState("");
  const [provinceCode, setProvinceCode] = useState("");
  const [cityCode, setCityCode] = useState("");
  const [countyCode, setCountyCode] = useState("");
  const [hasArcadesOnly, setHasArcadesOnly] = useState(true);
  const [sortBy, setSortBy] = useState<ArcadeSortBy>("default");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [sortTitleName, setSortTitleName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<ArcadeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [paged, setPaged] = usePagedState();
  const [mapRuntime, setMapRuntime] = useState<AmapRuntime | null>(null);
  const [mapStatus, setMapStatus] = useState<MapStatus>({ state: "idle", message: "" });
  const [clientOriginGcj, setClientOriginGcj] = useState<GeoPoint | null>(null);
  const [clientLocation, setClientLocation] = useState(() => loadCachedClientLocation());
  const [selectedRegionPoint, setSelectedRegionPoint] = useState<SelectedRegionPoint | null>(null);

  const pageSize = 20;
  const detailRequestIdRef = useRef(0);

  useEffect(() => {
    void (async () => {
      const rows = await listProvinces();
      setProvinces(rows);
    })();
  }, []);

  useEffect(() => {
    if (!provinceCode) {
      setCities([]);
      setCityCode("");
      setCounties([]);
      setCountyCode("");
      return;
    }
    void (async () => {
      const rows = await listCities(provinceCode);
      setCities(rows);
      setCityCode("");
      setCounties([]);
      setCountyCode("");
    })();
  }, [provinceCode]);

  useEffect(() => {
    if (!cityCode) {
      setCounties([]);
      setCountyCode("");
      return;
    }
    void (async () => {
      const rows = await listCounties(cityCode);
      setCounties(rows);
      setCountyCode("");
    })();
  }, [cityCode]);

  useEffect(() => {
    let cancelled = false;

    async function refreshClientLocation() {
      const location = await warmupClientLocationCache();
      if (!cancelled && location) {
        setClientLocation(location);
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
      if (!clientLocation) {
        setClientOriginGcj(null);
        return;
      }
      const point = await convertClientLocationToGcj02(mapRuntime?.AMap, clientLocation);
      if (!cancelled) {
        setClientOriginGcj(point);
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

  const handleMapRuntimeChange = useCallback((runtime: AmapRuntime | null) => {
    setMapRuntime(runtime);
  }, []);

  const handleMapStatusChange = useCallback((state: MapStatus["state"], message?: string) => {
    setMapStatus({ state, message: message ?? "" });
  }, []);

  const loadDetailForItem = useCallback(async (item: ArcadeSummary) => {
    const requestId = ++detailRequestIdRef.current;
    setDetailLoading(true);
    setDetailError("");
    try {
      const payload = await getArcadeDetail(item.source_id);
      if (requestId !== detailRequestIdRef.current) {
        return;
      }
      setDetail(payload);
    } catch (err) {
      if (requestId !== detailRequestIdRef.current) {
        return;
      }
      setDetailError(err instanceof Error ? err.message : "加载机厅详情失败");
      setDetail(null);
    } finally {
      if (requestId === detailRequestIdRef.current) {
        setDetailLoading(false);
      }
    }
  }, []);

  const selectShop = useCallback(async (item: ArcadeSummary) => {
    setSelectedSourceId(item.source_id);
    if (detail?.source_id === item.source_id && !detailError) {
      return;
    }
    await loadDetailForItem(item);
  }, [detail?.source_id, detailError, loadDetailForItem]);

  async function runSearch(page = 1) {
    try {
      setLoading(true);
      setError("");
      const payload = await listArcades({
        keyword,
        province_code: provinceCode || undefined,
        city_code: cityCode || undefined,
        county_code: countyCode || undefined,
        has_arcades: hasArcadesOnly ? true : undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
        sort_title_name: sortBy === "title_quantity" ? sortTitleName.trim() || undefined : undefined,
        page,
        page_size: pageSize
      });
      setPaged(payload);

      const existing = payload.items.find((item) => item.source_id === selectedSourceId) ?? null;
      const fallback = payload.items[0] ?? null;
      const nextSelected = existing ?? fallback;
      if (!nextSelected) {
        setSelectedSourceId(null);
        setDetail(null);
        setDetailError("");
        return;
      }
      setSelectedSourceId(nextSelected.source_id);
      await loadDetailForItem(nextSelected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void runSearch(1);
  }, []);

  async function onSubmit(event: FormEvent) {
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
      setSelectedRegionPoint(null);
      if (selectedCatalogPoint || !selectedArcade || !selectedRegionQuery || !mapRuntime?.AMap) {
        return;
      }
      const point = await geocodeAddressToGcj02(mapRuntime.AMap, selectedRegionQuery, selectedArcade.city_name);
      if (!cancelled && point) {
        setSelectedRegionPoint({
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

  return (
    <div className="browser-shell">
      <header className="browser-hero">
        <h2>Arcade Explorer</h2>
        <p>筛选机厅、在地图上看点位，并直接跳转到高德查看或导航。</p>
      </header>

      <main className="browser-layout">
        <section className="browser-card browser-controls">
          <form onSubmit={onSubmit} className="browser-filter-grid">
            <label className="browser-field">
              Keyword
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="maimai / chunithm" />
            </label>
            <label className="browser-field">
              Province
              <select value={provinceCode} onChange={(e) => setProvinceCode(e.target.value)}>
                <option value="">All</option>
                {provinces.map((row) => (
                  <option value={row.code} key={row.code}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="browser-field">
              City
              <select value={cityCode} onChange={(e) => setCityCode(e.target.value)} disabled={!provinceCode}>
                <option value="">All</option>
                {cities.map((row) => (
                  <option value={row.code} key={row.code}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="browser-field">
              County
              <select value={countyCode} onChange={(e) => setCountyCode(e.target.value)} disabled={!cityCode}>
                <option value="">All</option>
                {counties.map((row) => (
                  <option value={row.code} key={row.code}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="browser-field">
              Sort By
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value as ArcadeSortBy)}>
                <option value="default">Default</option>
                <option value="title_quantity">Title Qty (arcades[].quantity)</option>
                <option value="arcade_count">Title Type Count</option>
                <option value="updated_at">Updated At</option>
                <option value="source_id">Source ID</option>
              </select>
            </label>
            <label className="browser-field">
              Sort Order
              <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)}>
                <option value="desc">Desc</option>
                <option value="asc">Asc</option>
              </select>
            </label>
            <label className="browser-field">
              Title Name
              <input
                value={sortTitleName}
                onChange={(e) => setSortTitleName(e.target.value)}
                placeholder="maimai / sdvx"
                disabled={sortBy !== "title_quantity"}
              />
            </label>
            <label className="browser-check">
              <input
                type="checkbox"
                checked={hasArcadesOnly}
                onChange={(e) => setHasArcadesOnly(e.target.checked)}
              />
              Has titles only
            </label>
            <button type="submit" disabled={loading} className="browser-primary-btn">
              {loading ? "Searching..." : "Search"}
            </button>
          </form>

          {error ? <div className="browser-error">{error}</div> : null}

          <div className="browser-list-header">
            <strong>Results</strong>
            <span>
              {pageHint}
              {sortBy === "title_quantity" && sortTitleName.trim()
                ? ` | ${sortTitleName.trim()} ${sortOrder.toUpperCase()}`
                : ""}
            </span>
          </div>

          <ul className="browser-result-list">
            {paged.items.map((item) => {
              const mapped = Boolean(getArcadeGcjPoint(item));
              const active = item.source_id === selectedSourceId;
              return (
                <li key={item.source_id}>
                  <button
                    type="button"
                    onClick={() => void selectShop(item)}
                    className={`browser-item-btn${active ? " is-active" : ""}`}
                    data-testid={`arcade-list-item-${item.source_id}`}
                  >
                    <div className="browser-item-topline">
                      <h3>{item.name}</h3>
                      <span className={`browser-geo-pill${mapped ? " is-ready" : " is-empty"}`}>
                        {mapped ? "地图已定位" : "暂无地图定位"}
                      </span>
                    </div>
                    <p>{item.address || "No address"}</p>
                    <small>
                      {item.province_name || "-"} / {item.city_name || "-"} / {item.county_name || "-"} | titles{" "}
                      {item.arcade_count}
                    </small>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="browser-pager">
            <button
              type="button"
              disabled={paged.page <= 1 || loading}
              onClick={() => void runSearch(Math.max(1, paged.page - 1))}
              className="browser-secondary-btn"
            >
              Prev
            </button>
            <span>
              Page {paged.page} / {Math.max(1, paged.total_pages)}
            </span>
            <button
              type="button"
              disabled={paged.page >= paged.total_pages || loading || paged.total_pages === 0}
              onClick={() => void runSearch(paged.page + 1)}
              className="browser-secondary-btn"
            >
              Next
            </button>
          </div>
        </section>

        <aside className="browser-card browser-detail">
          <div className="browser-detail-head">
            <div>
              <strong>Map & Detail</strong>
            </div>
          </div>

          <div className="browser-map-panel">
            <AmapMapCanvas
              center={mapCenter}
              zoom={mapZoom}
              fallbackRegionName={selectedFallbackRegionName}
              emptyMessage="等待地图就绪"
              onRuntimeChange={handleMapRuntimeChange}
              onStatusChange={handleMapStatusChange}
            />
            <AmapShopMarkers
              runtime={mapRuntime}
              shop={selectedArcade}
              point={selectedPoint}
              onSelectShop={handleMarkerSelect}
            />
            <AmapRouteOverlay runtime={mapRuntime} route={null} />
            {mapStatusText ? <div className="browser-map-state">{mapStatusText}</div> : null}
          </div>

          {detailLoading ? <p className="browser-detail-note">Loading detail...</p> : null}
          {!detailLoading && !selectedArcade ? <p className="browser-detail-note">Select one shop from the left list.</p> : null}
          {detailError ? <p className="browser-error">{detailError}</p> : null}
          {selectedArcade ? (
            <div className="browser-detail-content">
              <h3 data-testid="browser-detail-title">{selectedArcade.name}</h3>
              <p>{selectedArcade.address || "No address"}</p>
              <p>{selectedArcade.transport || "No transport info"}</p>
              <p className="browser-map-hint">
                {selectedPoint
                  ? `地图坐标：${selectedPoint.lng.toFixed(6)}, ${selectedPoint.lat.toFixed(6)}`
                  : mapCenter || selectedRegionLabel
                    ? `该机厅暂时没有精确地图坐标，地图已停在 ${selectedRegionPoint?.label || selectedRegionLabel}`
                    : "该机厅暂时没有可用地图坐标"}
              </p>
              <MapActionBar actions={actions} />
              {selectedDetail ? (
                <p className="browser-comment">{selectedDetail.comment || "No comments"}</p>
              ) : null}
              {selectedDetail ? (
                <>
                  <h4>Titles ({selectedDetail.arcades.length})</h4>
                  <ul className="browser-title-list">
                    {selectedDetail.arcades.map((item, idx) => (
                      <li key={`${item.title_id}-${idx}`}>
                        <b>{item.title_name || "Unknown"}</b>
                        <span>Qty: {item.quantity ?? "-"}</span>
                        <span>Version: {item.version || "-"}</span>
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
      </main>
    </div>
  );
}
