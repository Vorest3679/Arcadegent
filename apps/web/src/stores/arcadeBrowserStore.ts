import { create } from "zustand";
import { loadCachedClientLocation } from "../lib/clientLocation";
import type { AmapRuntime } from "../components/map/AmapMapCanvas";
import type {
  ArcadeDetail,
  ArcadeSortBy,
  ArcadeSummary,
  GeoPoint,
  PagedArcades,
  RegionItem,
  SortOrder
} from "../types";

export type ArcadeMapStatus = {
  state: "idle" | "loading" | "ready" | "disabled" | "error";
  message: string;
};

export type SelectedRegionPoint = {
  sourceId: number;
  query: string;
  label: string;
  point: GeoPoint;
};

export const EMPTY_PAGED_ARCADES: PagedArcades = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 0
};

type ArcadeBrowserStore = {
  provinces: RegionItem[];
  cities: RegionItem[];
  counties: RegionItem[];
  keyword: string;
  provinceCode: string;
  cityCode: string;
  countyCode: string;
  hasArcadesOnly: boolean;
  sortBy: ArcadeSortBy;
  sortOrder: SortOrder;
  sortTitleName: string;
  loading: boolean;
  error: string;
  detail: ArcadeDetail | null;
  detailLoading: boolean;
  detailError: string;
  selectedSourceId: number | null;
  paged: PagedArcades;
  mapRuntime: AmapRuntime | null;
  mapStatus: ArcadeMapStatus;
  clientOriginGcj: GeoPoint | null;
  clientLocation: ReturnType<typeof loadCachedClientLocation>;
  selectedRegionPoint: SelectedRegionPoint | null;
  setProvinces: (provinces: RegionItem[]) => void;
  setCities: (cities: RegionItem[]) => void;
  setCounties: (counties: RegionItem[]) => void;
  setKeyword: (keyword: string) => void;
  setProvinceCode: (provinceCode: string) => void;
  setCityCode: (cityCode: string) => void;
  setCountyCode: (countyCode: string) => void;
  setHasArcadesOnly: (hasArcadesOnly: boolean) => void;
  setSortBy: (sortBy: ArcadeSortBy) => void;
  setSortOrder: (sortOrder: SortOrder) => void;
  setSortTitleName: (sortTitleName: string) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string) => void;
  setDetail: (detail: ArcadeDetail | null) => void;
  setDetailLoading: (detailLoading: boolean) => void;
  setDetailError: (detailError: string) => void;
  setSelectedSourceId: (selectedSourceId: number | null) => void;
  setPaged: (paged: PagedArcades) => void;
  setMapRuntime: (mapRuntime: AmapRuntime | null) => void;
  setMapStatus: (state: ArcadeMapStatus["state"], message?: string) => void;
  setClientOriginGcj: (clientOriginGcj: GeoPoint | null) => void;
  setClientLocation: (clientLocation: ReturnType<typeof loadCachedClientLocation>) => void;
  setSelectedRegionPoint: (selectedRegionPoint: SelectedRegionPoint | null) => void;
};

export const useArcadeBrowserStore = create<ArcadeBrowserStore>((set) => ({
  provinces: [],
  cities: [],
  counties: [],
  keyword: "",
  provinceCode: "",
  cityCode: "",
  countyCode: "",
  hasArcadesOnly: true,
  sortBy: "default",
  sortOrder: "desc",
  sortTitleName: "",
  loading: false,
  error: "",
  detail: null,
  detailLoading: false,
  detailError: "",
  selectedSourceId: null,
  paged: EMPTY_PAGED_ARCADES,
  mapRuntime: null,
  mapStatus: { state: "idle", message: "" },
  clientOriginGcj: null,
  clientLocation: loadCachedClientLocation(),
  selectedRegionPoint: null,
  setProvinces: (provinces) => set({ provinces }),
  setCities: (cities) => set({ cities }),
  setCounties: (counties) => set({ counties }),
  setKeyword: (keyword) => set({ keyword }),
  setProvinceCode: (provinceCode) => set({
    provinceCode,
    cityCode: "",
    countyCode: "",
    cities: [],
    counties: []
  }),
  setCityCode: (cityCode) => set({
    cityCode,
    countyCode: "",
    counties: []
  }),
  setCountyCode: (countyCode) => set({ countyCode }),
  setHasArcadesOnly: (hasArcadesOnly) => set({ hasArcadesOnly }),
  setSortBy: (sortBy) => set((state) => ({
    sortBy,
    sortOrder: sortBy === "distance" ? "asc" : state.sortOrder
  })),
  setSortOrder: (sortOrder) => set({ sortOrder }),
  setSortTitleName: (sortTitleName) => set({ sortTitleName }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setDetail: (detail) => set({ detail }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setDetailError: (detailError) => set({ detailError }),
  setSelectedSourceId: (selectedSourceId) => set({ selectedSourceId }),
  setPaged: (paged) => set({ paged }),
  setMapRuntime: (mapRuntime) => set({ mapRuntime }),
  setMapStatus: (state, message = "") => set({ mapStatus: { state, message } }),
  setClientOriginGcj: (clientOriginGcj) => set({ clientOriginGcj }),
  setClientLocation: (clientLocation) => set({ clientLocation }),
  setSelectedRegionPoint: (selectedRegionPoint) => set({ selectedRegionPoint })
}));
