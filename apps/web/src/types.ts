export type RegionItem = {
  code: string;
  name: string;
};

export type ViewMode = "chat" | "arcades";

export type ArcadeSortBy = "default" | "updated_at" | "source_id" | "arcade_count" | "title_quantity";
export type SortOrder = "asc" | "desc";
export type CoordSystem = "gcj02" | "wgs84";
export type GeoSource = "catalog" | "geocode" | "client" | "route";
export type GeoPrecision = "exact" | "approx";

export type GeoPoint = {
  lng: number;
  lat: number;
  coord_system: CoordSystem;
  source: GeoSource;
  precision: GeoPrecision;
};

export type ArcadeGeo = {
  gcj02?: GeoPoint | null;
  wgs84?: GeoPoint | null;
  source: GeoSource;
  precision: GeoPrecision;
};

export type ArcadeSummary = {
  source: string;
  source_id: number;
  source_url: string;
  name: string;
  name_pinyin?: string | null;
  address?: string | null;
  transport?: string | null;
  province_code?: string | null;
  province_name?: string | null;
  city_code?: string | null;
  city_name?: string | null;
  county_code?: string | null;
  county_name?: string | null;
  status?: string | number | null;
  type?: string | number | null;
  pay_type?: string | number | null;
  locked?: string | number | null;
  ea_status?: string | number | null;
  price?: string | number | null;
  start_time?: string | number | null;
  end_time?: string | number | null;
  fav_count?: number | null;
  updated_at?: string | null;
  arcade_count: number;
  geo?: ArcadeGeo | null;
};

export type ArcadeTitle = {
  id?: number | null;
  title_id?: string | number | null;
  title_name?: string | null;
  quantity?: number | null;
  version?: string | null;
  coin?: string | number | null;
  eacoin?: string | number | null;
  comment?: string | null;
};

export type ArcadeDetail = ArcadeSummary & {
  comment?: string | null;
  url?: string | null;
  image_thumb?: Record<string, unknown> | null;
  events: Array<Record<string, unknown>>;
  arcades: ArcadeTitle[];
  collab?: boolean | null;
};

export type PagedArcades = {
  items: ArcadeSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type IntentType = "search" | "search_nearby" | "navigate";
export type ChatSessionStatus = "idle" | "running" | "completed" | "failed";

export type ClientLocationContext = {
  lng: number;
  lat: number;
  accuracy_m?: number | null;
  province?: string | null;
  city?: string | null;
  district?: string | null;
  township?: string | null;
  adcode?: string | null;
  formatted_address?: string | null;
  region_text?: string | null;
};

export type ReverseGeocodeRequest = {
  lng: number;
  lat: number;
  accuracy_m?: number | null;
};

export type ReverseGeocodeResponse = ClientLocationContext & {
  resolved: boolean;
};

export type ChatRequest = {
  session_id?: string;
  message: string;
  intent?: IntentType;
  shop_id?: number;
  location?: ClientLocationContext;
  keyword?: string;
  province_code?: string;
  city_code?: string;
  county_code?: string;
  page_size?: number;
};

export type RouteSummary = {
  provider: "amap" | "google" | "none";
  mode: string;
  distance_m?: number | null;
  duration_s?: number | null;
  origin?: GeoPoint | null;
  destination?: GeoPoint | null;
  polyline: GeoPoint[];
  hint?: string | null;
};

export type AgentMapScene = "agent_candidates" | "agent_route";

export type ChatMapArtifacts = {
  shops: ArcadeSummary[];
  route?: RouteSummary | null;
  client_location?: ClientLocationContext | null;
  destination?: ArcadeSummary | null;
  view_payload?: Record<string, unknown> | null;
  route_pending?: boolean;
};

export type ChatResponse = {
  session_id: string;
  intent: IntentType;
  reply: string;
  shops: ArcadeSummary[];
  route?: RouteSummary | null;
};

export type ChatSessionDispatch = {
  session_id: string;
  status: ChatSessionStatus;
};

export type ChatHistoryTurn = {
  role: "user" | "assistant" | "tool";
  content: string;
  name?: string | null;
  call_id?: string | null;
  created_at: string;
};

export type ChatSessionSummary = {
  session_id: string;
  title: string;
  preview?: string | null;
  intent: IntentType;
  status: ChatSessionStatus;
  turn_count: number;
  created_at: string;
  updated_at: string;
};

export type ChatSessionDetail = {
  session_id: string;
  intent: IntentType;
  active_subagent: string;
  status: ChatSessionStatus;
  last_error?: string | null;
  reply?: string | null;
  shops: ArcadeSummary[];
  route?: RouteSummary | null;
  client_location?: ClientLocationContext | null;
  destination?: ArcadeSummary | null;
  view_payload?: Record<string, unknown> | null;
  turn_count: number;
  created_at: string;
  updated_at: string;
  turns: ChatHistoryTurn[];
};

export type ChatStreamEventName =
  | "session.started"
  | "subagent.changed"
  | "worker.started"
  | "worker.completed"
  | "worker.failed"
  | "assistant.token"
  | "tool.started"
  | "tool.progress"
  | "tool.completed"
  | "tool.failed"
  | "navigation.route_ready"
  | "assistant.completed"
  | "session.failed";

export type ChatStreamEnvelope = {
  id: number;
  session_id: string;
  event: ChatStreamEventName;
  at: string;
  data: Record<string, unknown>;
};
