import { reverseGeocodeLocation } from "../api/arcadeApi";
import type { ClientLocationContext, ReverseGeocodeResponse } from "../types";

const LOCATION_CACHE_KEY = "arcadegent.client_location.v1";
const LOCATION_EQUALITY_METERS = 30;

type BrowserCoordinates = Pick<ClientLocationContext, "lng" | "lat" | "accuracy_m">;

function isBrowserLocationSupported(): boolean {
  return typeof window !== "undefined" && typeof navigator !== "undefined" && "geolocation" in navigator;
}

function haversineMeters(a: BrowserCoordinates, b: BrowserCoordinates): number {
  // 计算两组经纬度坐标之间的距离（单位：米）
  const toRadians = (value: number) => (value * Math.PI) / 180;
  const earthRadiusMeters = 6371000;
  const lat1 = toRadians(a.lat);
  const lat2 = toRadians(b.lat);
  const deltaLat = lat2 - lat1;
  const deltaLng = toRadians(b.lng - a.lng);
  const sinLat = Math.sin(deltaLat / 2);
  const sinLng = Math.sin(deltaLng / 2);
  const x = sinLat * sinLat + Math.cos(lat1) * Math.cos(lat2) * sinLng * sinLng;
  const c = 2 * Math.atan2(Math.sqrt(x), Math.sqrt(Math.max(1e-12, 1 - x)));
  return earthRadiusMeters * c;
}

function hasResolvedRegion(location: ClientLocationContext | null): boolean {
  if (!location) {
    return false;
  }
  return Boolean(
    location.region_text
    || location.formatted_address
    || location.province
    || location.city
    || location.district
    || location.township
  );
}

function areEquivalentLocations(a: ClientLocationContext | null, b: BrowserCoordinates): boolean {
  if (!a) {
    return false;
  }
  return haversineMeters(a, b) <= LOCATION_EQUALITY_METERS;
}

function normalizeResolvedLocation(
  coords: BrowserCoordinates,
  resolved?: ReverseGeocodeResponse | null
): ClientLocationContext {
  if (!resolved) {
    return {
      lng: coords.lng,
      lat: coords.lat,
      accuracy_m: coords.accuracy_m ?? null
    };
  }
  return {
    lng: coords.lng,
    lat: coords.lat,
    accuracy_m: coords.accuracy_m ?? resolved.accuracy_m ?? null,
    province: resolved.province ?? null,
    city: resolved.city ?? null,
    district: resolved.district ?? null,
    township: resolved.township ?? null,
    adcode: resolved.adcode ?? null,
    formatted_address: resolved.formatted_address ?? null,
    region_text: resolved.region_text ?? null
  };
}

function getCurrentBrowserCoordinates(maximumAge: number): Promise<BrowserCoordinates> {
  // 调用浏览器API获取当前地理位置坐标，包装成Promise以便使用async/await语法
  if (!isBrowserLocationSupported()) {
    return Promise.reject(new Error("browser geolocation unavailable"));
  }

  return new Promise<BrowserCoordinates>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        resolve({
          lng: position.coords.longitude,
          lat: position.coords.latitude,
          accuracy_m: Number.isFinite(position.coords.accuracy) ? position.coords.accuracy : null
        });
      },
      (error) => {
        reject(error);
      },
      {
        enableHighAccuracy: true,
        timeout: 8000,
        maximumAge
      }
    );
  });
}

function readLocationCache(): ClientLocationContext | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(LOCATION_CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<ClientLocationContext>;
    if (typeof parsed.lng !== "number" || typeof parsed.lat !== "number") {
      return null;
    }
    return {
      lng: parsed.lng,
      lat: parsed.lat,
      accuracy_m: typeof parsed.accuracy_m === "number" ? parsed.accuracy_m : null,
      province: typeof parsed.province === "string" ? parsed.province : null,
      city: typeof parsed.city === "string" ? parsed.city : null,
      district: typeof parsed.district === "string" ? parsed.district : null,
      township: typeof parsed.township === "string" ? parsed.township : null,
      adcode: typeof parsed.adcode === "string" ? parsed.adcode : null,
      formatted_address: typeof parsed.formatted_address === "string" ? parsed.formatted_address : null,
      region_text: typeof parsed.region_text === "string" ? parsed.region_text : null
    };
  } catch {
    return null;
  }
}

function writeLocationCache(location: ClientLocationContext): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(LOCATION_CACHE_KEY, JSON.stringify(location));
  } catch {
    // Ignore storage failures and keep chat flow usable.
  }
}

async function resolveLocation({
  maximumAge,
  fallbackToCache
}: {
  maximumAge: number;
  fallbackToCache: boolean;
}): Promise<ClientLocationContext | null> {
  const cached = readLocationCache();
  let coords: BrowserCoordinates;

  try {
    coords = await getCurrentBrowserCoordinates(maximumAge);
  } catch {
    return fallbackToCache ? cached : null;
  }

  if (areEquivalentLocations(cached, coords) && hasResolvedRegion(cached)) {
    const reused = {
      ...cached,
      lng: coords.lng,
      lat: coords.lat,
      accuracy_m: coords.accuracy_m ?? cached?.accuracy_m ?? null
    };
    writeLocationCache(reused);
    return reused;
  }

  let resolved: ReverseGeocodeResponse | null = null;
  try {
    resolved = await reverseGeocodeLocation(coords);
  } catch {
    resolved = null;
  }

  const next = normalizeResolvedLocation(coords, resolved);
  writeLocationCache(next);
  return next;
}

export function loadCachedClientLocation(): ClientLocationContext | null {
  return readLocationCache();
}

export async function warmupClientLocationCache(): Promise<ClientLocationContext | null> {
  return resolveLocation({
    maximumAge: 5 * 60 * 1000,
    fallbackToCache: true
  });
}

export async function resolveClientLocationForSessionStart(): Promise<ClientLocationContext | null> {
  return resolveLocation({
    maximumAge: 0,
    fallbackToCache: true
  });
}
