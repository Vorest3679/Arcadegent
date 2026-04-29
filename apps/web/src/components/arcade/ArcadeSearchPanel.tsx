import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { getArcadeGcjPoint } from "../../lib/amapCoords";
import { useArcadeBrowserStore } from "../../stores/arcadeBrowserStore";
import type { ArcadeSortBy, ArcadeSummary, SortOrder } from "../../types";
import { ARCADE_TITLE_OPTIONS } from "./titleOptions";

type ArcadeSearchPanelProps = {
  pageHint: string;
  onSubmit: (event: FormEvent) => Promise<void>;
  onSelectShop: (item: ArcadeSummary) => void;
  onSearchPage: (page: number) => void;
};

export function ArcadeSearchPanel({
  pageHint,
  onSubmit,
  onSelectShop,
  onSearchPage
}: ArcadeSearchPanelProps) {
  const provinces = useArcadeBrowserStore((state) => state.provinces);
  const cities = useArcadeBrowserStore((state) => state.cities);
  const counties = useArcadeBrowserStore((state) => state.counties);
  const shopName = useArcadeBrowserStore((state) => state.shopName);
  const titleName = useArcadeBrowserStore((state) => state.titleName);
  const provinceCode = useArcadeBrowserStore((state) => state.provinceCode);
  const cityCode = useArcadeBrowserStore((state) => state.cityCode);
  const countyCode = useArcadeBrowserStore((state) => state.countyCode);
  const hasArcadesOnly = useArcadeBrowserStore((state) => state.hasArcadesOnly);
  const sortBy = useArcadeBrowserStore((state) => state.sortBy);
  const sortOrder = useArcadeBrowserStore((state) => state.sortOrder);
  const loading = useArcadeBrowserStore((state) => state.loading);
  const error = useArcadeBrowserStore((state) => state.error);
  const paged = useArcadeBrowserStore((state) => state.paged);
  const selectedSourceId = useArcadeBrowserStore((state) => state.selectedSourceId);
  const setShopName = useArcadeBrowserStore((state) => state.setShopName);
  const setTitleName = useArcadeBrowserStore((state) => state.setTitleName);
  const setProvinceCode = useArcadeBrowserStore((state) => state.setProvinceCode);
  const setCityCode = useArcadeBrowserStore((state) => state.setCityCode);
  const setCountyCode = useArcadeBrowserStore((state) => state.setCountyCode);
  const setHasArcadesOnly = useArcadeBrowserStore((state) => state.setHasArcadesOnly);
  const setSortBy = useArcadeBrowserStore((state) => state.setSortBy);
  const setSortOrder = useArcadeBrowserStore((state) => state.setSortOrder);
  const totalPages = Math.max(1, paged.total_pages);
  const [pageInput, setPageInput] = useState(String(paged.page));
  const sortHint =
    sortBy === "title_quantity" && titleName
      ? ` | ${titleName} ${sortOrder.toUpperCase()}`
      : "";

  useEffect(() => {
    setPageInput(String(paged.page));
  }, [paged.page]);

  function submitPageInput(): void {
    const parsedPage = Number.parseInt(pageInput, 10);
    const nextPage = Number.isFinite(parsedPage)
      ? Math.min(Math.max(parsedPage, 1), totalPages)
      : paged.page;

    setPageInput(String(nextPage));
    if (nextPage !== paged.page && !loading) {
      onSearchPage(nextPage);
    }
  }

  function onPageInputKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter") {
      event.preventDefault();
      submitPageInput();
      event.currentTarget.blur();
    }
    if (event.key === "Escape") {
      setPageInput(String(paged.page));
      event.currentTarget.blur();
    }
  }

  return (
    <section className="browser-card browser-controls">
      <form onSubmit={(event) => void onSubmit(event)} className="browser-filter-grid">
        <label className="browser-field">
          机厅名称
          <input value={shopName} onChange={(e) => setShopName(e.target.value)} placeholder="星际传奇" />
        </label>
        <label className="browser-field">
          省份
          <select value={provinceCode} onChange={(e) => setProvinceCode(e.target.value)}>
            <option value="">全部</option>
            {provinces.map((row) => (
              <option value={row.code} key={row.code}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <label className="browser-field">
          城市
          <select value={cityCode} onChange={(e) => setCityCode(e.target.value)} disabled={!provinceCode}>
            <option value="">全部</option>
            {cities.map((row) => (
              <option value={row.code} key={row.code}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <label className="browser-field">
          区县
          <select value={countyCode} onChange={(e) => setCountyCode(e.target.value)} disabled={!cityCode}>
            <option value="">全部</option>
            {counties.map((row) => (
              <option value={row.code} key={row.code}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <label className="browser-field">
          排序字段
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as ArcadeSortBy)}>
            <option value="default">默认</option>
            <option value="distance">距离</option>
            <option value="title_quantity">指定机种机台数</option>
            <option value="arcade_count">机种数</option>
            <option value="updated_at">更新时间</option>
            <option value="source_id">来源 ID</option>
          </select>
        </label>
        <label className="browser-field">
          排序方向
          <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)}>
            <option value="desc">降序</option>
            <option value="asc">升序</option>
          </select>
        </label>
        <label className="browser-field">
          机种
          <select
            value={titleName}
            onChange={(e) => setTitleName(e.target.value)}
          >
            <option value="">全部</option>
            {ARCADE_TITLE_OPTIONS.map((title) => (
              <option value={title} key={title}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label className="browser-check">
          <input
            type="checkbox"
            checked={hasArcadesOnly}
            onChange={(e) => setHasArcadesOnly(e.target.checked)}
          />
          仅看有机台
        </label>
        <button type="submit" disabled={loading} className="browser-primary-btn">
          {loading ? "检索中..." : "检索"}
        </button>
      </form>

      {error ? <div className="browser-error">{error}</div> : null}

      <div className="browser-list-header">
        <strong>检索结果</strong>
        <span>
          {pageHint}
          {sortHint}
        </span>
      </div>

      <ul className="browser-result-list">
        {paged.items.map((item) => {
          const mapped = Boolean(getArcadeGcjPoint(item));
          const active = item.source_id === selectedSourceId;
          const distanceText =
            typeof item.distance_m === "number"
              ? item.distance_m >= 1000
                ? `${(item.distance_m / 1000).toFixed(1)} km`
                : `${Math.round(item.distance_m)} m`
              : null;
          return (
            <li key={item.source_id}>
              <button
                type="button"
                onClick={() => onSelectShop(item)}
                className={`browser-item-btn${active ? " is-active" : ""}`}
                data-testid={`arcade-list-item-${item.source_id}`}
              >
                <div className="browser-item-topline">
                  <h3>{item.name}</h3>
                  <span className={`browser-geo-pill${mapped ? " is-ready" : " is-empty"}`}>
                    {mapped ? "地图已定位" : "暂无地图定位"}
                  </span>
                </div>
                <p>{item.address || "暂无地址"}</p>
                <small>
                  {item.province_name || "-"} / {item.city_name || "-"} / {item.county_name || "-"} | 机种{" "}
                  {item.arcade_count}
                  {distanceText ? ` | ${distanceText}` : ""}
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
          onClick={() => onSearchPage(Math.max(1, paged.page - 1))}
          className="browser-secondary-btn"
        >
          上一页
        </button>
        <span className="browser-page-control">
          第
          <input
            aria-label="页码"
            className="browser-page-input"
            inputMode="numeric"
            pattern="[0-9]*"
            value={pageInput}
            disabled={loading || paged.total_pages === 0}
            onChange={(event) => setPageInput(event.target.value.replace(/\D/g, ""))}
            onBlur={submitPageInput}
            onKeyDown={onPageInputKeyDown}
          />
          / {totalPages} 页
        </span>
        <button
          type="button"
          disabled={paged.page >= paged.total_pages || loading || paged.total_pages === 0}
          onClick={() => onSearchPage(paged.page + 1)}
          className="browser-secondary-btn"
        >
          下一页
        </button>
      </div>
    </section>
  );
}
