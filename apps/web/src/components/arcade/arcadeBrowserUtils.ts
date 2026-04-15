import type { ArcadeDetail, ArcadeSummary, GeoPoint } from "../../types";

const KNOWN_REGION_CENTERS: Record<string, [number, number]> = {
  北京市: [116.4074, 39.9042],
  天津市: [117.2000, 39.0842],
  河北省: [114.5149, 38.0428],
  山西省: [112.5492, 37.8570],
  内蒙古自治区: [111.7492, 40.8426],
  辽宁省: [123.4315, 41.8057],
  吉林省: [125.3235, 43.8171],
  黑龙江省: [126.6424, 45.7560],
  上海市: [121.4737, 31.2304],
  江苏省: [118.7969, 32.0603],
  浙江省: [120.1551, 30.2741],
  安徽省: [117.2272, 31.8206],
  福建省: [119.2965, 26.0745],
  江西省: [115.8582, 28.6829],
  山东省: [117.1201, 36.6512],
  河南省: [113.6254, 34.7466],
  湖北省: [114.3054, 30.5931],
  湖南省: [112.9388, 28.2282],
  广东省: [113.2644, 23.1291],
  广西壮族自治区: [108.3669, 22.8170],
  海南省: [110.3492, 20.0174],
  重庆市: [106.5516, 29.5630],
  四川省: [104.0668, 30.5728],
  贵州省: [106.6302, 26.6470],
  云南省: [102.8329, 24.8801],
  西藏自治区: [91.1175, 29.6475],
  陕西省: [108.9398, 34.3416],
  甘肃省: [103.8343, 36.0611],
  青海省: [101.7782, 36.6171],
  宁夏回族自治区: [106.2309, 38.4872],
  新疆维吾尔自治区: [87.6168, 43.8256]
};

function compactRegionParts(parts: Array<string | null | undefined>): string[] {
  return parts.reduce<string[]>((acc, part) => {
    const trimmed = part?.trim();
    if (!trimmed || acc.includes(trimmed)) {
      return acc;
    }
    return [...acc, trimmed];
  }, []);
}

export function getArcadeRegionParts(arcade?: ArcadeSummary | null): string[] {
  return compactRegionParts([arcade?.province_name, arcade?.city_name, arcade?.county_name]);
}

export function getArcadeFallbackRegionName(arcade?: ArcadeSummary | null): string {
  const city = arcade?.city_name?.trim();
  if (city) {
    return city;
  }
  const province = arcade?.province_name?.trim();
  if (province) {
    return province;
  }
  return arcade?.county_name?.trim() ?? "";
}

export function getKnownRegionCenter(arcade?: ArcadeSummary | null): GeoPoint | null {
  const regionNames = compactRegionParts([arcade?.county_name, arcade?.city_name, arcade?.province_name]);
  const center = regionNames
    .map((regionName) => KNOWN_REGION_CENTERS[regionName])
    .find(Boolean);
  if (!center) {
    return null;
  }
  return {
    lng: center[0],
    lat: center[1],
    coord_system: "gcj02",
    source: "geocode",
    precision: "approx"
  };
}

export function getArcadeRegionZoom(arcade?: ArcadeSummary | null): number {
  if (arcade?.county_name) {
    return 12;
  }
  if (arcade?.city_name) {
    return 10;
  }
  if (arcade?.province_name) {
    return 7;
  }
  return 5;
}

export function isArcadeDetail(arcade?: ArcadeSummary | ArcadeDetail | null): arcade is ArcadeDetail {
  return Boolean(arcade && Array.isArray((arcade as ArcadeDetail).arcades));
}
