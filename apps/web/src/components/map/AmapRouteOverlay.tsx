/*
AmapRouteOverlay 组件负责在高德地图上渲染路线覆盖物，
根据传入的路线数据生成对应的 Polyline 覆盖物，并添加到地图上。
组件会监听路线数据和地图实例的变化，在数据更新时重新渲染覆盖物，
并在组件卸载时清除覆盖物，确保地图上的显示与当前路线数据保持一致。
*/
import { useEffect, useRef } from "react";
import { normalizePointToGcj02, normalizeRoutePolyline, toLngLatTuple } from "../../lib/amapCoords";
import type { RouteSummary } from "../../types";
import type { AmapRuntime } from "./AmapMapCanvas";

type AmapRouteOverlayProps = {
  runtime: AmapRuntime | null;
  route?: RouteSummary | null;
};

export function AmapRouteOverlay({ runtime, route }: AmapRouteOverlayProps) {
  const overlaysRef = useRef<any[]>([]);

  useEffect(() => {
    if (!runtime?.AMap || !runtime.map) {
      return;
    }

    const path = normalizeRoutePolyline(route);
    if (overlaysRef.current.length && typeof runtime.map.remove === "function") {
      runtime.map.remove(overlaysRef.current);
      overlaysRef.current = [];
    }
    if (!path.length && !route?.origin && !route?.destination) {
      return;
    }

    const nextOverlays: any[] = [];
    if (path.length >= 2 && runtime.AMap.Polyline) {
      nextOverlays.push(
        new runtime.AMap.Polyline({
          path,
          strokeColor: "#1f8f7a",
          strokeWeight: 6,
          strokeOpacity: 0.9,
          lineJoin: "round",
          lineCap: "round"
        })
      );
    }

    const origin = normalizePointToGcj02(route?.origin);
    const destination = normalizePointToGcj02(route?.destination);
    const endpoints = [
      { point: origin, label: "起", className: "is-origin" },
      { point: destination, label: "终", className: "is-destination" }
    ];
    if (runtime.AMap.Marker) {
      endpoints.forEach((entry) => {
        const tuple = toLngLatTuple(entry.point);
        if (!tuple) {
          return;
        }
        nextOverlays.push(
          new runtime.AMap.Marker({
            position: tuple,
            content: `<span class="amap-route-pin ${entry.className}">${entry.label}</span>`,
            anchor: "center",
            zIndex: 140
          })
        );
      });
    }

    overlaysRef.current = nextOverlays;
    if (typeof runtime.map.add === "function") {
      runtime.map.add(overlaysRef.current);
    }
    if (overlaysRef.current.length > 1 && typeof runtime.map.setFitView === "function") {
      runtime.map.setFitView(overlaysRef.current);
    }

    return () => {
      if (overlaysRef.current.length && typeof runtime.map.remove === "function") {
        runtime.map.remove(overlaysRef.current);
      }
      overlaysRef.current = [];
    };
  }, [route, runtime]);

  return null;
}
