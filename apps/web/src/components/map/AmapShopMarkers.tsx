/*
AmapShopMarkers 组件负责在高德地图上渲染机厅位置的标记，
只为当前选中的机厅生成一个 Marker 覆盖物，并添加到地图上。
组件会监听选中机厅、坐标和地图实例的变化，在数据更新时重新渲染标记，
并在组件卸载时清除标记，确保地图上不会混入当前页其它机厅的点位。
*/
import { useEffect, useRef } from "react";
import type { ArcadeSummary, GeoPoint } from "../../types";
import { toLngLatTuple } from "../../lib/amapCoords";
import type { AmapRuntime } from "./AmapMapCanvas";

type AmapShopMarkersProps = {
  runtime: AmapRuntime | null;
  shop?: ArcadeSummary | null;
  point?: GeoPoint | null;
  onSelectShop?: (shop: ArcadeSummary) => void;
};

function markerHtml(shop: ArcadeSummary): string {
  return `
    <button
      type="button"
      class="amap-shop-marker"
      data-testid="map-marker-${shop.source_id}"
      data-source-id="${shop.source_id}"
      aria-label="${shop.name}"
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
  shop,
  point,
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

    const tuple = toLngLatTuple(point);
    if (!shop || !tuple) {
      return;
    }

    const marker = new runtime.AMap.Marker({
      position: tuple,
      content: markerHtml(shop),
      extData: { shop },
      anchor: "bottom-center"
    });
    if (typeof marker.on === "function") {
      marker.on("click", () => onSelectShop?.(shop));
    }
    markersRef.current = [marker];

    attachMarker(runtime.map, marker);
    if (typeof runtime.map.setCenter === "function") {
      runtime.map.setCenter(tuple);
    }

    return () => {
      if (markersRef.current.length) {
        detachMarkers(runtime.map, markersRef.current);
      }
      markersRef.current = [];
    };
  }, [onSelectShop, point, runtime, shop]);

  return null;
}
