import type { FormEvent } from "react";
import { getArcadeGcjPoint } from "../../lib/amapCoords";
import { useArcadeBrowserStore } from "../../stores/arcadeBrowserStore";
import type { ArcadeSortBy, ArcadeSummary, SortOrder } from "../../types";

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
  const keyword = useArcadeBrowserStore((state) => state.keyword);
  const provinceCode = useArcadeBrowserStore((state) => state.provinceCode);
  const cityCode = useArcadeBrowserStore((state) => state.cityCode);
  const countyCode = useArcadeBrowserStore((state) => state.countyCode);
  const hasArcadesOnly = useArcadeBrowserStore((state) => state.hasArcadesOnly);
  const sortBy = useArcadeBrowserStore((state) => state.sortBy);
  const sortOrder = useArcadeBrowserStore((state) => state.sortOrder);
  const sortTitleName = useArcadeBrowserStore((state) => state.sortTitleName);
  const loading = useArcadeBrowserStore((state) => state.loading);
  const error = useArcadeBrowserStore((state) => state.error);
  const paged = useArcadeBrowserStore((state) => state.paged);
  const selectedSourceId = useArcadeBrowserStore((state) => state.selectedSourceId);
  const setKeyword = useArcadeBrowserStore((state) => state.setKeyword);
  const setProvinceCode = useArcadeBrowserStore((state) => state.setProvinceCode);
  const setCityCode = useArcadeBrowserStore((state) => state.setCityCode);
  const setCountyCode = useArcadeBrowserStore((state) => state.setCountyCode);
  const setHasArcadesOnly = useArcadeBrowserStore((state) => state.setHasArcadesOnly);
  const setSortBy = useArcadeBrowserStore((state) => state.setSortBy);
  const setSortOrder = useArcadeBrowserStore((state) => state.setSortOrder);
  const setSortTitleName = useArcadeBrowserStore((state) => state.setSortTitleName);
  const sortHint =
    sortBy === "title_quantity" && sortTitleName.trim()
      ? ` | ${sortTitleName.trim()} ${sortOrder.toUpperCase()}`
      : "";

  return (
    <section className="browser-card browser-controls">
      <form onSubmit={(event) => void onSubmit(event)} className="browser-filter-grid">
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
            <option value="distance">Distance</option>
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
                <p>{item.address || "No address"}</p>
                <small>
                  {item.province_name || "-"} / {item.city_name || "-"} / {item.county_name || "-"} | titles{" "}
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
          Prev
        </button>
        <span>
          Page {paged.page} / {Math.max(1, paged.total_pages)}
        </span>
        <button
          type="button"
          disabled={paged.page >= paged.total_pages || loading || paged.total_pages === 0}
          onClick={() => onSearchPage(paged.page + 1)}
          className="browser-secondary-btn"
        >
          Next
        </button>
      </div>
    </section>
  );
}
