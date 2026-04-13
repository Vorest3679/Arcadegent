/* 
AmapMapCanvas 组件负责在 Web 端渲染高德地图，
并提供地图实例的回调接口，供父组件进行地图操作和交互。
组件会根据传入的中心点和缩放级别初始化地图，并在地图加载状态发生变化时通过回调通知父组件。
同时，组件会处理地图资源的加载和销毁，确保在组件卸载时正确释放资源，避免内存泄漏。
*/
import { useEffect, useRef } from "react";
import type { GeoPoint } from "../../types";
import { toLngLatTuple } from "../../lib/amapCoords";
import { isAmapConfigured, loadAmapSdk } from "../../lib/amapLoader";

export type AmapRuntime = {
  AMap: any;
  map: any;
};

type AmapMapCanvasProps = {
  center?: GeoPoint | null;
  zoom?: number;
  fallbackRegionName?: string | null;
  emptyMessage?: string;
  onRuntimeChange: (runtime: AmapRuntime | null) => void;
  onStatusChange?: (status: "idle" | "loading" | "ready" | "disabled" | "error", message?: string) => void;
};

export function AmapMapCanvas({
  center,
  zoom = 12,
  fallbackRegionName,
  emptyMessage = "暂无地图数据",
  onRuntimeChange,
  onStatusChange
}: AmapMapCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<AmapRuntime | null>(null);
  const centerRef = useRef(center);
  const fallbackRegionNameRef = useRef(fallbackRegionName);
  const zoomRef = useRef(zoom);

  function moveToFallbackRegion(regionName?: string | null): void {
    const trimmedRegionName = regionName?.trim();
    const map = runtimeRef.current?.map;
    if (!trimmedRegionName || !map) {
      return;
    }
    if (typeof map.setZoom === "function") {
      map.setZoom(zoomRef.current);
    }
    if (typeof map.setCity === "function") {
      map.setCity(trimmedRegionName, () => undefined);
    }
  }

  useEffect(() => {
    zoomRef.current = zoom;
    if (typeof runtimeRef.current?.map?.setZoom === "function") {
      runtimeRef.current.map.setZoom(zoom);
    }
  }, [zoom]);

  useEffect(() => {
    let disposed = false;

    async function boot() {
      const hasMock = typeof window !== "undefined" && Boolean(window.__ARCADEGENT_AMAP_MOCK__);
      if (!hasMock && !isAmapConfigured()) {
        onStatusChange?.("disabled", "未配置高德地图 Web JS Key；已保留列表和高德导航。");
        onRuntimeChange(null);
        return;
      }

      onStatusChange?.("loading");
      try {
        const AMap = await loadAmapSdk();
        if (disposed || !containerRef.current || !AMap?.Map) {
          return;
        }
        const map = new AMap.Map(containerRef.current, {
          zoom: zoomRef.current,
          center: toLngLatTuple(centerRef.current) ?? undefined,
          resizeEnable: true
        });
        if (AMap.Scale) {
          map.addControl(new AMap.Scale());
        }
        if (AMap.ToolBar) {
          map.addControl(new AMap.ToolBar());
        }
        runtimeRef.current = { AMap, map };
        if (!centerRef.current) {
          moveToFallbackRegion(fallbackRegionNameRef.current);
        }
        onRuntimeChange(runtimeRef.current);
        onStatusChange?.("ready");
      } catch (error) {
        if (disposed) {
          return;
        }
        onRuntimeChange(null);
        onStatusChange?.("error", error instanceof Error ? error.message : "地图加载失败");
      }
    }

    void boot();

    return () => {
      disposed = true;
      if (runtimeRef.current?.map?.destroy) {
        runtimeRef.current.map.destroy();
      }
      runtimeRef.current = null;
      onRuntimeChange(null);
    };
  }, [onRuntimeChange, onStatusChange]);

  useEffect(() => {
    centerRef.current = center;
    const tuple = toLngLatTuple(center);
    if (!tuple) {
      moveToFallbackRegion(fallbackRegionNameRef.current);
      return;
    }
    if (!runtimeRef.current?.map?.setCenter) {
      return;
    }
    runtimeRef.current.map.setCenter(tuple);
  }, [center]);

  useEffect(() => {
    fallbackRegionNameRef.current = fallbackRegionName;
    if (!centerRef.current) {
      moveToFallbackRegion(fallbackRegionName);
    }
  }, [fallbackRegionName]);

  return (
    <div className="amap-canvas-shell">
      <div ref={containerRef} className="amap-canvas" data-testid="arcade-map-canvas" />
      <div className="amap-empty-copy">
        {emptyMessage}
      </div>
    </div>
  );
}
