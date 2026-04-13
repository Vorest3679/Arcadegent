import type { GeoPoint } from "../types";

type MarkerUriOptions = {
  point: GeoPoint;
  name: string;
  src?: string;
  callnative?: boolean;
};

type NavigationUriOptions = {
  destination: GeoPoint;
  destinationName: string;
  origin?: GeoPoint | null;
  originName?: string;
  mode?: "walk" | "car";
  src?: string;
  callnative?: boolean;
};

const URI_BASE = "https://uri.amap.com";

function defaultSrc(): string {
  return import.meta.env.VITE_AMAP_URI_SRC?.trim() || "arcadegent_web";
}

function formatCoordinate(value: number): string {
  const fixed = value.toFixed(6);
  return fixed.replace(/\.?0+$/, "");
}

function formatPlace(point: GeoPoint, name: string): string {
  return `${formatCoordinate(point.lng)},${formatCoordinate(point.lat)},${name}`;
}

export function buildAmapMarkerUri({
  point,
  name,
  src = defaultSrc(),
  callnative = false
}: MarkerUriOptions): string {
  const url = new URL("/marker", URI_BASE);
  url.searchParams.set("position", `${formatCoordinate(point.lng)},${formatCoordinate(point.lat)}`);
  url.searchParams.set("name", name);
  url.searchParams.set("src", src);
  url.searchParams.set("coordinate", point.coord_system === "wgs84" ? "wgs84" : "gaode");
  url.searchParams.set("callnative", callnative ? "1" : "0");
  return url.toString();
}

export function buildAmapNavigationUri({
  destination,
  destinationName,
  origin,
  originName = "我的位置",
  mode = "walk",
  src = defaultSrc(),
  callnative = false
}: NavigationUriOptions): string {
  const url = new URL("/navigation", URI_BASE);
  url.searchParams.set("to", formatPlace(destination, destinationName));
  url.searchParams.set("mode", mode);
  url.searchParams.set("src", src);
  url.searchParams.set("callnative", callnative ? "1" : "0");
  if (origin) {
    url.searchParams.set("from", formatPlace(origin, originName));
  }
  return url.toString();
}
