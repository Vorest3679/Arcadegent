import type {
  ArcadeDetail,
  ArcadeSortBy,
  PagedArcades,
  RegionItem,
  ReverseGeocodeRequest,
  ReverseGeocodeResponse,
  SortOrder
} from "../types";
import { buildUrl, fetchJson, postJson } from "./http";

export async function listArcades(params: {
  keyword?: string;
  shop_name?: string;
  title_name?: string;
  province_code?: string;
  city_code?: string;
  county_code?: string;
  has_arcades?: boolean;
  sort_by?: ArcadeSortBy;
  sort_order?: SortOrder;
  sort_title_name?: string;
  origin_lng?: number;
  origin_lat?: number;
  origin_coord_system?: "wgs84" | "gcj02";
  page?: number;
  page_size?: number;
}): Promise<PagedArcades> {
  return fetchJson<PagedArcades>(buildUrl("/api/arcades", params));
}

export async function getArcadeDetail(sourceId: number): Promise<ArcadeDetail> {
  return fetchJson<ArcadeDetail>(buildUrl(`/api/arcades/${sourceId}`));
}

export async function listProvinces(): Promise<RegionItem[]> {
  return fetchJson<RegionItem[]>(buildUrl("/api/regions/provinces"));
}

export async function listCities(provinceCode: string): Promise<RegionItem[]> {
  return fetchJson<RegionItem[]>(buildUrl("/api/regions/cities", { province_code: provinceCode }));
}

export async function listCounties(cityCode: string): Promise<RegionItem[]> {
  return fetchJson<RegionItem[]>(buildUrl("/api/regions/counties", { city_code: cityCode }));
}

export async function reverseGeocodeLocation(
  payload: ReverseGeocodeRequest
): Promise<ReverseGeocodeResponse> {
  return postJson<ReverseGeocodeResponse>("/api/location/reverse-geocode", payload);
}
