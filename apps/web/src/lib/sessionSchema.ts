import {
  CONTROL_EVENT_NAMES,
  RUN_EVENT_NAMES,
  type ChatHistoryTurn,
  type ChatRunStatus,
  type ChatSessionDetail,
  type ChatSessionStatus,
  type ChatStreamEnvelope,
  type ChatTurnStep
} from "../types";

// Hand-written guards for the session payloads the UI depends on. They check
// the structure the UI reads, not every nested field (map data is mapped
// field by field where it is used).

const RUN_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "running",
  "cancelling",
  "completed",
  "failed",
  "cancelled"
]);
const SESSION_STATUSES: ReadonlySet<string> = new Set(["idle", "running", "completed", "failed"]);
const RUN_EVENTS: ReadonlySet<string> = new Set(RUN_EVENT_NAMES);
const CONTROL_EVENTS: ReadonlySet<string> = new Set(CONTROL_EVENT_NAMES);
const TURN_ROLES: ReadonlySet<string> = new Set(["user", "assistant", "tool"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

export function isRunStatus(value: unknown): value is ChatRunStatus {
  return typeof value === "string" && RUN_STATUSES.has(value);
}

export function isChatTurnStep(value: unknown): value is ChatTurnStep {
  if (!isRecord(value) || typeof value.created_at !== "string" || !isOptionalString(value.agent)) {
    return false;
  }
  if (value.kind === "text") {
    return typeof value.content === "string";
  }
  if (value.kind === "tool") {
    return (
      typeof value.name === "string" &&
      (value.status === "completed" || value.status === "failed") &&
      isOptionalString(value.call_id)
    );
  }
  return false;
}

export function isChatHistoryTurn(value: unknown): value is ChatHistoryTurn {
  if (!isRecord(value)) {
    return false;
  }
  if (typeof value.role !== "string" || !TURN_ROLES.has(value.role)) {
    return false;
  }
  if (typeof value.content !== "string" || typeof value.created_at !== "string") {
    return false;
  }
  if (value.map_artifacts !== undefined && value.map_artifacts !== null) {
    if (!isRecord(value.map_artifacts) || !Array.isArray(value.map_artifacts.shops)) {
      return false;
    }
  }
  if (value.steps !== undefined && value.steps !== null) {
    return Array.isArray(value.steps) && value.steps.every(isChatTurnStep);
  }
  return true;
}

export function isChatSessionDetail(value: unknown): value is ChatSessionDetail {
  if (!isRecord(value)) {
    return false;
  }
  if (typeof value.session_id !== "string" || typeof value.created_at !== "string") {
    return false;
  }
  if (typeof value.status !== "string" || !SESSION_STATUSES.has(value.status as ChatSessionStatus)) {
    return false;
  }
  if (!Array.isArray(value.shops) || !Array.isArray(value.turns)) {
    return false;
  }
  if (value.current_run !== undefined && value.current_run !== null) {
    if (!isRecord(value.current_run) || typeof value.current_run.run_id !== "string") {
      return false;
    }
    if (!isRunStatus(value.current_run.status)) {
      return false;
    }
  }
  return value.turns.every(isChatHistoryTurn);
}

// Transport envelope shape and event catalog; whether it belongs to the subscribed
// session/run is the subscriber's check.
export function isStreamEnvelope(value: unknown): value is ChatStreamEnvelope {
  if (!isRecord(value)) {
    return false;
  }
  if (typeof value.id !== "number" || !Number.isFinite(value.id)) {
    return false;
  }
  if (typeof value.session_id !== "string" || typeof value.run_id !== "string") {
    return false;
  }
  if (value.kind !== "event" && value.kind !== "control") {
    return false;
  }
  if (typeof value.event !== "string" || !isRecord(value.data)) {
    return false;
  }
  // The catalog is closed: names outside it are not part of the contract.
  if (!(value.kind === "event" ? RUN_EVENTS : CONTROL_EVENTS).has(value.event)) {
    return false;
  }
  if (value.output_id !== undefined && typeof value.output_id !== "string") {
    return false;
  }
  return value.status === undefined || isRunStatus(value.status);
}

// Response check with a user-facing message; the payload itself is never echoed.
export function assertChatSessionDetail(value: unknown): ChatSessionDetail {
  if (!isChatSessionDetail(value)) {
    throw new Error("会话数据格式不正确，请刷新页面后重试。");
  }
  return value;
}
