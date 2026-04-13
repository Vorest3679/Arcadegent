/*
amapCoords.ts 提供了一系列与高德地图坐标相关的工具函数，
包括坐标转换、地址解析和路线数据处理等功能。
这些函数主要用于将不同坐标系统之间进行转换，解析高德地图返回的坐标数据，
以及根据地址信息获取对应的地理坐标，方便在地图上进行定位和导航等操作。
*/
import type { ArcadeGeo, ClientLocationContext, GeoPoint, RouteSummary } from "../types";

const EARTH_A = 6378245.0;
const EARTH_EE = 0.006693421622965943;

export function getArcadeGcjPoint(value?: { geo?: ArcadeGeo | null } | null): GeoPoint | null {
  const point = value?.geo?.gcj02 ?? null;
  if (!point || point.coord_system !== "gcj02") {
    return null;
  }
  return point;
}

export function toLngLatTuple(point?: Pick<GeoPoint, "lng" | "lat"> | null): [number, number] | null {
  if (!point) {
    return null;
  }
  return [point.lng, point.lat];
}

export function normalizeRoutePolyline(route?: RouteSummary | null): Array<[number, number]> {
  if (!route?.polyline?.length) {
    return [];
  }
  return route.polyline
    .filter((point) => point.coord_system === "gcj02")
    .map((point) => [point.lng, point.lat] as [number, number]);
}

function parseAmapLngLat(raw: any): [number, number] | null {
  if (!raw) {
    return null;
  }
  if (typeof raw === "string") {
    const [rawLng, rawLat] = raw.split(",");
    const lng = Number(rawLng);
    const lat = Number(rawLat);
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      return [lng, lat];
    }
  }
  if (Array.isArray(raw) && raw.length >= 2) {
    const lng = Number(raw[0]);
    const lat = Number(raw[1]);
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      return [lng, lat];
    }
  }
  if (typeof raw.lng === "number" && typeof raw.lat === "number") {
    return [raw.lng, raw.lat];
  }
  if (typeof raw.getLng === "function" && typeof raw.getLat === "function") {
    const lng = Number(raw.getLng());
    const lat = Number(raw.getLat());
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      return [lng, lat];
    }
  }
  return null;
}

function isInMainlandChina(lng: number, lat: number): boolean {
  return lng >= 72.004 && lng <= 137.8347 && lat >= 0.8293 && lat <= 55.8271;
}

function transformLat(lng: number, lat: number): number {
  let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng));
  ret += ((20.0 * Math.sin(6.0 * lng * Math.PI) + 20.0 * Math.sin(2.0 * lng * Math.PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(lat * Math.PI) + 40.0 * Math.sin((lat / 3.0) * Math.PI)) * 2.0) / 3.0;
  ret += ((160.0 * Math.sin((lat / 12.0) * Math.PI) + 320 * Math.sin((lat * Math.PI) / 30.0)) * 2.0) / 3.0;
  return ret;
}

function transformLng(lng: number, lat: number): number {
  let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng));
  ret += ((20.0 * Math.sin(6.0 * lng * Math.PI) + 20.0 * Math.sin(2.0 * lng * Math.PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(lng * Math.PI) + 40.0 * Math.sin((lng / 3.0) * Math.PI)) * 2.0) / 3.0;
  ret += ((150.0 * Math.sin((lng / 12.0) * Math.PI) + 300.0 * Math.sin((lng / 30.0) * Math.PI)) * 2.0) / 3.0;
  return ret;
}

export function approximateWgs84ToGcj02(location: Pick<ClientLocationContext, "lng" | "lat">): GeoPoint {
  const lng = location.lng;
  const lat = location.lat;
  if (!isInMainlandChina(lng, lat)) {
    return {
      lng,
      lat,
      coord_system: "gcj02",
      source: "client",
      precision: "approx"
    };
  }

  let dLat = transformLat(lng - 105.0, lat - 35.0);
  let dLng = transformLng(lng - 105.0, lat - 35.0);
  const radLat = (lat / 180.0) * Math.PI;
  let magic = Math.sin(radLat);
  magic = 1 - EARTH_EE * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180.0) / (((EARTH_A * (1 - EARTH_EE)) / (magic * sqrtMagic)) * Math.PI);
  dLng = (dLng * 180.0) / ((EARTH_A / sqrtMagic) * Math.cos(radLat) * Math.PI);
  return {
    lng: lng + dLng,
    lat: lat + dLat,
    coord_system: "gcj02",
    source: "client",
    precision: "approx"
  };
}

export async function convertClientLocationToGcj02(
  AMap: any,
  location: ClientLocationContext
): Promise<GeoPoint | null> {
  if (!AMap?.convertFrom) {
    return approximateWgs84ToGcj02(location);
  }

  return new Promise<GeoPoint | null>((resolve) => {
    try {
      AMap.convertFrom([location.lng, location.lat], "gps", (status: string, result: any) => {
        if (status !== "complete") {
          resolve(approximateWgs84ToGcj02(location));
          return;
        }
        const first = Array.isArray(result?.locations) ? result.locations[0] : null;
        const parsed = parseAmapLngLat(first);
        if (!parsed) {
          resolve(approximateWgs84ToGcj02(location));
          return;
        }
        resolve({
          lng: parsed[0],
          lat: parsed[1],
          coord_system: "gcj02",
          source: "client",
          precision: "approx"
        });
      });
    } catch {
      resolve(approximateWgs84ToGcj02(location));
    }
  });
}

export async function geocodeAddressToGcj02(
  AMap: any,
  address?: string | null,
  city?: string | null
): Promise<GeoPoint | null> {
  const trimmedAddress = address?.trim();
  if (!AMap?.Geocoder || !trimmedAddress) {
    return null;
  }

  return new Promise<GeoPoint | null>((resolve) => {
    try {
      const geocoder = new AMap.Geocoder({
        city: city?.trim() || undefined
      });
      geocoder.getLocation(trimmedAddress, (status: string, result: any) => {
        if (status !== "complete") {
          resolve(null);
          return;
        }
        const first = Array.isArray(result?.geocodes) ? result.geocodes[0] : null;
        const parsed = parseAmapLngLat(first?.location);
        if (!parsed) {
          resolve(null);
          return;
        }
        resolve({
          lng: parsed[0],
          lat: parsed[1],
          coord_system: "gcj02",
          source: "geocode",
          precision: "approx"
        });
      });
    } catch {
      resolve(null);
    }
  });
}
