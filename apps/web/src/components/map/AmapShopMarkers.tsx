/*
AmapShopMarkers 组件负责在高德地图上渲染机厅位置的标记，
可以为候选列表或当前选中的单个机厅生成 Marker 覆盖物，并添加到地图上。
组件会监听机厅列表、选中态、坐标和地图实例的变化，在数据更新时重新渲染标记，
并在组件卸载时清除标记，确保地图上不会混入过期点位。
*/
import { useEffect, useRef } from "react";
import type { ArcadeSummary, GeoPoint } from "../../types";
import { getArcadeGcjPoint, toLngLatTuple } from "../../lib/amapCoords";
import type { AmapRuntime } from "./AmapMapCanvas";

type AmapShopMarkersProps = {
  runtime: AmapRuntime | null;
  shops?: ArcadeSummary[];
  shop?: ArcadeSummary | null;
  point?: GeoPoint | null;
  selectedSourceId?: number | null;
  onSelectShop?: (shop: ArcadeSummary) => void;
};

type MarkerEntry = {
  shop: ArcadeSummary;
  point: GeoPoint;
  selected: boolean;
};

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function markerHtml(shop: ArcadeSummary, selected: boolean): string {
  return `
    <button
      type="button"
      class="amap-shop-marker${selected ? " is-selected" : ""}"
      data-testid="map-marker-${shop.source_id}"
      data-source-id="${shop.source_id}"
      aria-label="${escapeHtml(shop.name)}"
    ></button>
  `;
}

function attachMarker(map: any, marker: any): void {
  try {
    if (typeof marker?.setMap === "function") {
      marker.setMap(map);
      return;
    }
    if (typeof map?.add === "function") {
      map.add(marker);
    }
  } catch {
    // AMap may throw while the underlying map instance is being recreated.
  }
}

function detachMarkers(map: any, markers: any[]): void {
  markers.forEach((marker) => {
    try {
      if (typeof marker?.setMap === "function") {
        marker.setMap(null);
        return;
      }
      if (typeof map?.remove === "function") {
        map.remove(marker);
      }
    } catch {
      // Best-effort cleanup for SDK objects during React remounts.
    }
  });
}

export function AmapShopMarkers({
  runtime,
  shops,
  shop,
  point,
  selectedSourceId,
  onSelectShop
}: AmapShopMarkersProps) {
  const markersRef = useRef<any[]>([]);

  useEffect(() => {
    if (!runtime?.AMap || !runtime.map) {
      return;
    }

    if (markersRef.current.length) {
      detachMarkers(runtime.map, markersRef.current);
    }
    markersRef.current = [];

    const entries: MarkerEntry[] = [];
    const source = shops?.length ? shops : shop ? [shop] : [];
    source.forEach((item) => {
      const itemPoint = shops?.length ? getArcadeGcjPoint(item) : point;
      if (!itemPoint) {
        return;
      }
      entries.push({
        shop: item,
        point: itemPoint,
        selected: item.source_id === selectedSourceId || (!shops?.length && item.source_id === shop?.source_id)
      });
    });

    if (!entries.length) {
      return;
    }

    const markers = entries.map((entry) => {
      const marker = new runtime.AMap.Marker({
        position: toLngLatTuple(entry.point),
        content: markerHtml(entry.shop, entry.selected),
        extData: { shop: entry.shop },
        anchor: "bottom-center",
        zIndex: entry.selected ? 120 : 100
      });
      if (typeof marker.on === "function") {
        marker.on("click", () => onSelectShop?.(entry.shop));
      }
      return marker;
    });

    markersRef.current = markers;
    markers.forEach((marker) => attachMarker(runtime.map, marker));

    const selectedEntry = entries.find((entry) => entry.selected);
    const selectedTuple = toLngLatTuple(selectedEntry?.point);
    if (selectedTuple && typeof runtime.map.setCenter === "function") {
      runtime.map.setCenter(selectedTuple);
    } else if (markers.length > 1 && typeof runtime.map.setFitView === "function") {
      runtime.map.setFitView(markers);
    }

    return () => {
      if (markersRef.current.length) {
        detachMarkers(runtime.map, markersRef.current);
      }
      markersRef.current = [];
    };
  }, [onSelectShop, point, runtime, selectedSourceId, shop, shops]);

  return null;
}
