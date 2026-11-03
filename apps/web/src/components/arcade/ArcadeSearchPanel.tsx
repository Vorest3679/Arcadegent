import { useEffect, useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Loader2, MapPin, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { cn } from "@/lib/utils";
import { getArcadeGcjPoint } from "../../lib/amap/coords";
import { useArcadeBrowserStore } from "../../stores/arcadeBrowserStore";
import type { ArcadeSortBy, ArcadeSummary, SortOrder } from "../../types";
import { ARCADE_TITLE_OPTIONS } from "./titleOptions";

function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      {children(id)}
    </div>
  );
}

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

  const regionSelect = (
    id: string,
    value: string,
    onChange: (value: string) => void,
    rows: { code: string; name: string }[],
    disabled = false
  ) => (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} className="bg-card">
      <NativeSelectOption value="">全部</NativeSelectOption>
      {rows.map((row) => (
        <NativeSelectOption value={row.code} key={row.code}>
          {row.name}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  );

  return (
    <Card className="gap-4 p-4 sm:p-5">
      <form onSubmit={(event) => void onSubmit(event)} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="col-span-2">
          <Field label="机厅名称">
            {(id) => (
              <Input id={id} value={shopName} onChange={(e) => setShopName(e.target.value)} placeholder="星际传奇" className="bg-card" />
            )}
          </Field>
        </div>
        <div className="col-span-2">
          <Field label="机种">
            {(id) => (
              <NativeSelect
                id={id}
                data-testid="arcade-title-filter"
                value={titleName}
                onChange={(e) => setTitleName(e.target.value)}
                className="bg-card"
              >
                <NativeSelectOption value="">全部</NativeSelectOption>
                {ARCADE_TITLE_OPTIONS.map((title) => (
                  <NativeSelectOption value={title} key={title}>
                    {title}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            )}
          </Field>
        </div>
        <Field label="省份">{(id) => regionSelect(id, provinceCode, setProvinceCode, provinces)}</Field>
        <Field label="城市">{(id) => regionSelect(id, cityCode, setCityCode, cities, !provinceCode)}</Field>
        <Field label="区县">{(id) => regionSelect(id, countyCode, setCountyCode, counties, !cityCode)}</Field>
        <Field label="排序字段">
          {(id) => (
            <NativeSelect id={id} value={sortBy} onChange={(e) => setSortBy(e.target.value as ArcadeSortBy)} className="bg-card">
              <NativeSelectOption value="default">默认</NativeSelectOption>
              <NativeSelectOption value="distance">距离</NativeSelectOption>
              <NativeSelectOption value="title_quantity">指定机种机台数</NativeSelectOption>
              <NativeSelectOption value="arcade_count">机种数</NativeSelectOption>
              <NativeSelectOption value="updated_at">更新时间</NativeSelectOption>
              <NativeSelectOption value="source_id">来源 ID</NativeSelectOption>
            </NativeSelect>
          )}
        </Field>
        <Field label="排序方向">
          {(id) => (
            <NativeSelect id={id} value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)} className="bg-card">
              <NativeSelectOption value="desc">降序</NativeSelectOption>
              <NativeSelectOption value="asc">升序</NativeSelectOption>
            </NativeSelect>
          )}
        </Field>
        <div className="col-span-2 flex items-center gap-2 self-end pb-2 sm:col-span-1">
          <Checkbox
            id="arcade-has-arcades"
            checked={hasArcadesOnly}
            onCheckedChange={(checked) => setHasArcadesOnly(checked === true)}
          />
          <Label htmlFor="arcade-has-arcades" className="font-normal">仅看有机台</Label>
        </div>
        <Button type="submit" disabled={loading} className="col-span-2 self-end sm:col-span-2">
          {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Search aria-hidden="true" />}
          {loading ? "检索中..." : "检索"}
        </Button>
      </form>

      {error ? <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p> : null}

      <div className="flex items-baseline justify-between border-t pt-3">
        <h2 className="text-sm font-semibold">检索结果</h2>
        <span className="text-xs text-muted-foreground">
          {pageHint}
          {sortHint}
        </span>
      </div>

      <ul className="grid gap-2">
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
                aria-pressed={active}
                data-testid={`arcade-list-item-${item.source_id}`}
                className={cn(
                  "grid w-full gap-1 rounded-lg border bg-card p-3 text-left transition hover:border-primary/40",
                  active && "border-primary/60 bg-accent/50 ring-2 ring-primary/15"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-medium leading-snug">{item.name}</h3>
                  <Badge
                    variant="outline"
                    className={cn(
                      "shrink-0 font-normal",
                      mapped ? "border-primary/30 bg-accent text-primary" : "text-muted-foreground"
                    )}
                  >
                    {mapped ? <MapPin aria-hidden="true" /> : null}
                    {mapped ? "地图已定位" : "暂无地图定位"}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">{item.address || "暂无地址"}</p>
                <small className="text-xs text-muted-foreground">
                  {item.province_name || "-"} / {item.city_name || "-"} / {item.county_name || "-"} · 机种{" "}
                  {item.arcade_count}
                  {distanceText ? ` · ${distanceText}` : ""}
                </small>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={paged.page <= 1 || loading}
          onClick={() => onSearchPage(Math.max(1, paged.page - 1))}
          className="bg-card"
        >
          <ChevronLeft aria-hidden="true" />
          上一页
        </Button>
        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          第
          <Input
            aria-label="页码"
            inputMode="numeric"
            pattern="[0-9]*"
            value={pageInput}
            disabled={loading || paged.total_pages === 0}
            onChange={(event) => setPageInput(event.target.value.replace(/\D/g, ""))}
            onBlur={submitPageInput}
            onKeyDown={onPageInputKeyDown}
            className="h-8 w-14 bg-card text-center"
          />
          / {totalPages} 页
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={paged.page >= paged.total_pages || loading || paged.total_pages === 0}
          onClick={() => onSearchPage(paged.page + 1)}
          className="bg-card"
        >
          下一页
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </Card>
  );
}
