import type { ChatHistoryTurn, ChatMapArtifacts, ChatStreamEnvelope, RouteSummary } from "../types";

const SUBAGENT_LABEL: Record<string, string> = {
  intent_router: "意图路由",
  main_agent: "主控阶段",
  search_agent: "检索阶段",
  search_worker: "检索执行",
  navigation_agent: "导航阶段",
  navigation_worker: "导航执行",
  summary_agent: "总结阶段"
};

const TOOL_LABEL: Record<string, string> = {
  invoke_worker: "派发任务",
  db_query_tool: "数据检索",
  geo_resolve_tool: "位置解析",
  route_plan_tool: "路线规划",
  summary_tool: "结果总结"
};

export type StreamProgressItem = {
  id: number;
  // Server event label, or "stream.reconnecting" for local transport status.
  event: string;
  text: string;
  at: string;
};

export function formatTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function toVisibleTurns(turns: ChatHistoryTurn[]): ChatHistoryTurn[] {
  return turns.filter((turn) => turn.role === "user" || turn.role === "assistant");
}

export function formatSubagentLabel(subagent: string | null): string {
  if (!subagent) {
    return "等待阶段信号";
  }
  return SUBAGENT_LABEL[subagent] ?? subagent;
}

function formatToolLabel(toolName: string | undefined): string {
  if (!toolName) {
    return "工具";
  }
  return TOOL_LABEL[toolName] ?? toolName;
}

export function toProgressText(envelope: ChatStreamEnvelope): string {
  const toolNameRaw = envelope.data.tool;
  const toolName = typeof toolNameRaw === "string" ? toolNameRaw : undefined;

  if (envelope.event === "session.started") {
    return "会话开始";
  }
  if (envelope.event === "subagent.changed") {
    const nextRaw = envelope.data.to_subagent ?? envelope.data.active_subagent;
    const next = typeof nextRaw === "string" ? nextRaw : null;
    return `切换到 ${formatSubagentLabel(next)}`;
  }
  if (envelope.event === "worker.started") {
    const worker = typeof envelope.data.worker === "string" ? envelope.data.worker : null;
    return `${formatSubagentLabel(worker)} 已启动`;
  }
  if (envelope.event === "worker.completed") {
    const worker = typeof envelope.data.worker === "string" ? envelope.data.worker : null;
    return `${formatSubagentLabel(worker)} 已完成`;
  }
  if (envelope.event === "worker.failed") {
    const worker = typeof envelope.data.worker === "string" ? envelope.data.worker : null;
    return `${formatSubagentLabel(worker)} 失败`;
  }
  if (envelope.event === "assistant.token") {
    return "正在生成回复";
  }
  if (envelope.event === "tool.started") {
    return `${formatToolLabel(toolName)} 执行中`;
  }
  if (envelope.event === "tool.progress") {
    return `${formatToolLabel(toolName)} 处理中`;
  }
  if (envelope.event === "tool.completed") {
    return `${formatToolLabel(toolName)} 已完成`;
  }
  if (envelope.event === "tool.failed") {
    return `${formatToolLabel(toolName)} 失败`;
  }
  if (envelope.event === "navigation.route_ready") {
    return "路线已生成";
  }
  if (envelope.event === "assistant.completed") {
    return "最终回复已生成";
  }
  if (envelope.event === "session.failed") {
    return "会话执行失败";
  }
  return envelope.event;
}

export function coerceStreamRoute(data: Record<string, unknown>): RouteSummary | null {
  const provider = data.provider;
  const mode = data.mode;
  if (provider !== "amap" && provider !== "google" && provider !== "none") {
    return null;
  }
  if (typeof mode !== "string" || !mode.trim()) {
    return null;
  }
  return {
    provider,
    mode,
    distance_m: typeof data.distance_m === "number" ? data.distance_m : null,
    duration_s: typeof data.duration_s === "number" ? data.duration_s : null,
    origin: typeof data.origin === "object" && data.origin !== null ? data.origin as RouteSummary["origin"] : null,
    destination:
      typeof data.destination === "object" && data.destination !== null
        ? data.destination as RouteSummary["destination"]
        : null,
    polyline: Array.isArray(data.polyline) ? data.polyline as RouteSummary["polyline"] : [],
    hint: typeof data.hint === "string" ? data.hint : null
  };
}

function pendingRouteArtifacts(): ChatMapArtifacts {
  return {
    shops: [],
    route: null,
    client_location: null,
    destination: null,
    view_payload: { version: 1, scene: "agent_route" },
    route_pending: true
  };
}

// Map artifacts shown while a run streams. Returns undefined when the event
// does not change them.
export function mapArtifactsForEvent(
  envelope: ChatStreamEnvelope,
  previous: ChatMapArtifacts | null
): ChatMapArtifacts | null | undefined {
  const { event, data } = envelope;
  if (event === "subagent.changed") {
    const next = data.to_subagent ?? data.active_subagent;
    return next === "navigation_worker" ? pendingRouteArtifacts() : undefined;
  }
  if (event === "worker.started") {
    return data.worker === "navigation_worker" ? pendingRouteArtifacts() : undefined;
  }
  if (event === "tool.started" && data.tool === "route_plan_tool") {
    return {
      shops: previous?.shops ?? [],
      route: null,
      client_location: previous?.client_location ?? null,
      destination: previous?.destination ?? null,
      view_payload: { version: 1, scene: "agent_route" },
      route_pending: true
    };
  }
  if (event === "navigation.route_ready") {
    const route = coerceStreamRoute(data);
    if (!route) {
      return undefined;
    }
    return {
      shops: previous?.shops ?? [],
      route,
      client_location: previous?.client_location ?? null,
      destination: previous?.destination ?? null,
      view_payload: { version: 1, scene: "agent_route" },
      route_pending: true
    };
  }
  return undefined;
}
