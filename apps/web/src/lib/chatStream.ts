import type { ChatHistoryTurn, ChatStreamEnvelope, ChatStreamEventName } from "../types";

export const STREAM_EVENT_NAMES: ChatStreamEventName[] = [
  "session.started",
  "subagent.changed",
  "worker.started",
  "worker.completed",
  "worker.failed",
  "assistant.token",
  "tool.started",
  "tool.progress",
  "tool.completed",
  "tool.failed",
  "navigation.route_ready",
  "assistant.completed",
  "session.failed"
];

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
  event: ChatStreamEventName;
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

function shortText(value: string, limit = 48): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (!compact) {
    return "";
  }
  if (compact.length <= limit) {
    return compact;
  }
  return `${compact.slice(0, Math.max(1, limit - 3))}...`;
}

function readStreamTextField(data: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function getAssistantTokenPreview(data: Record<string, unknown>): string | null {
  return readStreamTextField(data, ["text_preview", "textPreview", "textpreview", "content", "delta"]);
}

export function getAssistantTokenFullText(data: Record<string, unknown>): string | null {
  return readStreamTextField(data, ["content", "text_preview", "textPreview", "textpreview"]);
}

export function getAssistantTokenDelta(data: Record<string, unknown>): string | null {
  return readStreamTextField(data, ["delta"]);
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
    const preview = getAssistantTokenPreview(envelope.data);
    if (preview) {
      return `正在生成回复：${shortText(preview, 56)}`;
    }
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
