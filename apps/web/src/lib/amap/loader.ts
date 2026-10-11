import AMapLoader from "@amap/amap-jsapi-loader";

const AMAP_PLUGINS = ["AMap.Scale", "AMap.ToolBar", "AMap.Driving", "AMap.Walking", "AMap.Geocoder"] as const;
const AMAP_LOAD_TIMEOUT_MS = 8000;

let loaderPromise: Promise<any | null> | null = null;

function getMockAmap(): any | null {
  if (typeof window === "undefined") {
    return null;
  }
  const mock = window.__ARCADEGENT_AMAP_MOCK__;
  if (!mock) {
    return null;
  }
  if (typeof mock.load === "function") {
    return mock.load();
  }
  return mock;
}

export function isAmapConfigured(): boolean {
  const key = import.meta.env.VITE_AMAP_WEB_KEY?.trim();
  return Boolean(key);
}

export async function loadAmapSdk(): Promise<any | null> {
  const mock = getMockAmap();
  if (mock) {
    return Promise.resolve(mock);
  }

  if (typeof window === "undefined" || !isAmapConfigured()) {
    return null;
  }

  if (!loaderPromise) {
    const key = import.meta.env.VITE_AMAP_WEB_KEY?.trim() ?? "";
    const securityJsCode = import.meta.env.VITE_AMAP_SECURITY_JS_CODE?.trim();
    if (securityJsCode) {
      window._AMapSecurityConfig = { securityJsCode };
    }
    const sdkPromise = AMapLoader.load({
      key,
      version: "2.0",
      plugins: [...AMAP_PLUGINS]
    })
      .then((AMap) => {
        window.AMap = AMap;
        return AMap ?? window.AMap ?? null;
      });

    const timeoutPromise = new Promise<never>((_, reject) => {
      window.setTimeout(() => {
        reject(
          new Error("高德地图加载超时，请检查 VITE_AMAP_WEB_KEY / VITE_AMAP_SECURITY_JS_CODE 是否为 Web JS API 配置")
        );
      }, AMAP_LOAD_TIMEOUT_MS);
    });

    loaderPromise = Promise.race([sdkPromise, timeoutPromise]).catch((error) => {
      loaderPromise = null;
      throw error;
    });
  }

  return loaderPromise;
}
