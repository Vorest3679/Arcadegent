/*
AmapRouteOverlay 组件负责在高德地图上渲染路线覆盖物，
根据传入的路线数据生成对应的 Polyline 覆盖物，并添加到地图上。
组件会监听路线数据和地图实例的变化，在数据更新时重新渲染覆盖物，
并在组件卸载时清除覆盖物，确保地图上的显示与当前路线数据保持一致。
*/
import { useEffect, useRef } from "react";
import { normalizeRoutePolyline } from "../../lib/amapCoords";
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
    if (!path.length || !runtime.AMap.Polyline) {
      return;
    }

    const polyline = new runtime.AMap.Polyline({
      path,
      strokeColor: "#1f8f7a",
      strokeWeight: 6,
      strokeOpacity: 0.9,
      lineJoin: "round",
      lineCap: "round"
    });

    overlaysRef.current = [polyline];
    if (typeof runtime.map.add === "function") {
      runtime.map.add(overlaysRef.current);
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
