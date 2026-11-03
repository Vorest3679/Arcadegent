import type { ChatHistoryTurn, ChatMapArtifacts, ChatSessionDetail, ChatStreamEnvelope } from "../types";
import { hasMapContent } from "./mapArtifacts";
import { mapArtifactsForEvent } from "./sse/chatStream";
import { turnsFromDetail } from "./sessionDetail";

// The chat view is one flat, ordered list of items. History (session detail)
// and the live run (SSE) are folded into the same list; stream events are
// appended in arrival order. Tool items only carry identity: whether a tool
// is still running is tracked separately in `toolStatus`, because its
// terminal event arrives later and asynchronously.

export type ToolStatus = "running" | "completed" | "failed" | "cancelled";

type ItemBase = {
  // Stable across the live run and the history that replaces it, so a
  // hand-over does not remount what is already on screen.
  key: string;
  // Index of the user message the item answers (-1 before the first one).
  round: number;
  at: string;
};

export type UserItem = ItemBase & { kind: "user"; content: string };
export type AssistantItem = ItemBase & {
  kind: "assistant";
  content: string;
  agent: string;
  outputId: string | null;
  // Text is still arriving for this output.
  streaming: boolean;
  // The terminal reply of the round; the others are intermediate replies.
  final: boolean;
};
export type ToolItem = ItemBase & { kind: "tool"; callId: string; name: string; agent: string };
export type MapItem = ItemBase & { kind: "map"; artifacts: ChatMapArtifacts };
export type ErrorItem = ItemBase & { kind: "error"; message: string };

export type FeedItem = UserItem | AssistantItem | ToolItem | MapItem | ErrorItem;

export type ChatFeed = {
  items: FeedItem[];
  // Keyed by the tool item's key.
  toolStatus: Record<string, ToolStatus>;
};

export const EMPTY_FEED: ChatFeed = { items: [], toolStatus: {} };

function currentRound(items: FeedItem[]): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].kind === "user") {
      return items[index].round;
    }
  }
  return -1;
}

function countInRound(items: FeedItem[], round: number, kind: FeedItem["kind"]): number {
  return items.filter((item) => item.round === round && item.kind === kind).length;
}

function toolKey(round: number, callId: string): string {
  return `r${round}:t:${callId}`;
}

function stringField(data: Record<string, unknown>, field: string): string {
  const value = data[field];
  return typeof value === "string" ? value : "";
}

export function appendUser(feed: ChatFeed, content: string, at: string): ChatFeed {
  const round = currentRound(feed.items) + 1;
  return { ...feed, items: [...feed.items, { kind: "user", key: `r${round}:user`, round, content, at }] };
}

export function appendError(feed: ChatFeed, message: string, at: string): ChatFeed {
  const round = currentRound(feed.items);
  const key = `r${round}:e${countInRound(feed.items, round, "error")}`;
  return { ...feed, items: [...feed.items, { kind: "error", key, round, message, at }] };
}

function appendAssistant(
  feed: ChatFeed,
  fields: Pick<AssistantItem, "content" | "agent" | "outputId" | "streaming" | "final" | "at">
): ChatFeed {
  const round = currentRound(feed.items);
  const key = `r${round}:a${countInRound(feed.items, round, "assistant")}`;
  return { ...feed, items: [...feed.items, { kind: "assistant", key, round, ...fields }] };
}

function upsertMap(feed: ChatFeed, artifacts: ChatMapArtifacts, at: string): ChatFeed {
  const round = currentRound(feed.items);
  const key = `r${round}:map`;
  const index = feed.items.findIndex((item) => item.key === key);
  if (index < 0) {
    return { ...feed, items: [...feed.items, { kind: "map", key, round, artifacts, at }] };
  }
  const items = feed.items.slice();
  items[index] = { ...(items[index] as MapItem), artifacts };
  return { ...feed, items };
}

function updateItem<T extends FeedItem>(feed: ChatFeed, target: T, patch: Partial<T>): ChatFeed {
  return { ...feed, items: feed.items.map((item) => (item === target ? { ...item, ...patch } : item)) };
}

// History of a session detail, in the same shape as the live feed.
export function feedFromTurns(turns: ChatHistoryTurn[]): ChatFeed {
  let feed: ChatFeed = { items: [], toolStatus: {} };
  for (const turn of turns) {
    if (turn.role === "user") {
      feed = appendUser(feed, turn.content, turn.created_at);
      const round = currentRound(feed.items);
      (turn.steps ?? []).forEach((step, index) => {
        if (step.kind === "text") {
          feed = appendAssistant(feed, {
            content: step.content,
            agent: step.agent ?? "",
            outputId: null,
            streaming: false,
            final: false,
            at: step.created_at
          });
          return;
        }
        const callId = step.call_id || `${turn.created_at}-${index}`;
        const key = toolKey(round, callId);
        feed = {
          items: [...feed.items, { kind: "tool", key, round, callId, name: step.name, agent: step.agent ?? "", at: step.created_at }],
          toolStatus: { ...feed.toolStatus, [key]: step.status }
        };
      });
    } else if (turn.role === "assistant") {
      feed = appendAssistant(feed, {
        content: turn.content,
        agent: "",
        outputId: null,
        streaming: false,
        final: true,
        at: turn.created_at
      });
      if (hasMapContent(turn.map_artifacts)) {
        feed = upsertMap(feed, { ...turn.map_artifacts, route_pending: false }, turn.created_at);
      }
    }
  }
  return feed;
}

export function feedFromDetail(detail: ChatSessionDetail): ChatFeed {
  const feed = feedFromTurns(turnsFromDetail(detail));
  if (detail.status === "failed") {
    const message = detail.last_error?.trim() || "会话执行失败";
    return appendError(feed, message, detail.updated_at);
  }
  return feed;
}

// The run log replays a run from its first event; drop what the history
// already shows of the unanswered round so the replay does not repeat it.
export function withoutOpenRound(feed: ChatFeed): ChatFeed {
  const round = currentRound(feed.items);
  const items = feed.items.filter((item) => item.round !== round || item.kind === "user");
  const toolStatus = Object.fromEntries(
    Object.entries(feed.toolStatus).filter(([key]) => !key.startsWith(`r${round}:`))
  );
  return { items, toolStatus };
}

// `text` is the whole output assembled so far (not a delta), so a replayed or
// reloaded output converges to the same content.
export function applyOutputText(feed: ChatFeed, outputId: string, text: string, at: string, agent = ""): ChatFeed {
  const round = currentRound(feed.items);
  const existing = feed.items.find(
    (item): item is AssistantItem => item.kind === "assistant" && item.round === round && item.outputId === outputId
  );
  if (existing) {
    return existing.content === text ? feed : updateItem(feed, existing, { content: text });
  }
  return appendAssistant(feed, { content: text, agent, outputId, streaming: true, final: false, at });
}

// A newer output started: the previous one is complete.
export function sealOutput(feed: ChatFeed, outputId: string): ChatFeed {
  const round = currentRound(feed.items);
  const target = feed.items.find(
    (item): item is AssistantItem =>
      item.kind === "assistant" && item.round === round && item.outputId === outputId && item.streaming
  );
  return target ? updateItem(feed, target, { streaming: false }) : feed;
}

function applyTerminalReply(feed: ChatFeed, envelope: ChatStreamEnvelope): ChatFeed {
  const reply = stringField(envelope.data, "reply");
  if (!reply) {
    return feed;
  }
  const round = currentRound(feed.items);
  const assistants = feed.items.filter(
    (item): item is AssistantItem => item.kind === "assistant" && item.round === round
  );
  const target =
    (envelope.output_id ? assistants.find((item) => item.outputId === envelope.output_id) : undefined)
    ?? [...assistants].reverse().find((item) => item.streaming || item.final);
  const agent = stringField(envelope.data, "active_subagent");
  if (target) {
    return updateItem(feed, target, { content: reply, streaming: false, final: true });
  }
  return appendAssistant(feed, {
    content: reply,
    agent,
    outputId: envelope.output_id ?? null,
    streaming: false,
    final: true,
    at: envelope.at
  });
}

function applyToolEvent(feed: ChatFeed, envelope: ChatStreamEnvelope): ChatFeed {
  const callId = stringField(envelope.data, "call_id");
  const name = stringField(envelope.data, "tool");
  if (!callId || !name) {
    return feed;
  }
  const round = currentRound(feed.items);
  const key = toolKey(round, callId);
  const status: ToolStatus =
    envelope.event === "tool.started" ? "running" : envelope.event === "tool.completed" ? "completed" : "failed";
  // A tool call ends the text the model wrote before it.
  let next: ChatFeed = envelope.event === "tool.started" && feed.items.some((item) => item.kind === "assistant" && item.streaming)
    ? { ...feed, items: feed.items.map((item) => (item.kind === "assistant" && item.streaming ? { ...item, streaming: false } : item)) }
    : feed;
  if (!next.items.some((item) => item.key === key)) {
    const agent = stringField(envelope.data, "active_subagent");
    next = { ...next, items: [...next.items, { kind: "tool", key, round, callId, name, agent, at: envelope.at }] };
  }
  // Only a running (or unseen) tool changes state: a replayed start or a late
  // completion never overrides a terminal status.
  const current = next.toolStatus[key];
  if (current && current !== "running") {
    return next;
  }
  if (current === status) {
    return next;
  }
  return { ...next, toolStatus: { ...next.toolStatus, [key]: status } };
}

function currentMap(feed: ChatFeed): ChatMapArtifacts | null {
  const round = currentRound(feed.items);
  const item = feed.items.find((entry): entry is MapItem => entry.kind === "map" && entry.round === round);
  return item?.artifacts ?? null;
}

// Folds one business event of the live run into the feed.
export function applyRunEvent(feed: ChatFeed, envelope: ChatStreamEnvelope): ChatFeed {
  let next = feed;
  const artifacts = mapArtifactsForEvent(envelope, currentMap(feed));
  if (artifacts) {
    next = upsertMap(next, artifacts, envelope.at);
  }
  switch (envelope.event) {
    case "tool.started":
    case "tool.completed":
    case "tool.failed":
      return applyToolEvent(next, envelope);
    case "assistant.completed":
      return applyTerminalReply(next, envelope);
    case "session.failed": {
      // A model failure still ends with a stored fallback reply.
      next = applyTerminalReply(next, envelope);
      const error = stringField(envelope.data, "error").trim();
      return appendError(next, error || "会话执行失败", envelope.at);
    }
    default:
      return next;
  }
}

// The run ended: nothing streams any more, and tools still running will not
// report back.
export function finishRun(feed: ChatFeed, status: "completed" | "failed" | "cancelled"): ChatFeed {
  const round = currentRound(feed.items);
  const toolStatus = { ...feed.toolStatus };
  let changed = false;
  for (const [key, value] of Object.entries(toolStatus)) {
    if (value === "running" && key.startsWith(`r${round}:`)) {
      toolStatus[key] = status === "cancelled" ? "cancelled" : "failed";
      changed = true;
    }
  }
  const items = feed.items.map((item) => {
    if (item.kind === "assistant" && item.streaming) {
      changed = true;
      return { ...item, streaming: false };
    }
    if (item.kind === "map" && item.round === round && item.artifacts.route_pending && item.artifacts.route) {
      changed = true;
      return { ...item, artifacts: { ...item.artifacts, route_pending: false } };
    }
    return item;
  });
  return changed ? { items, toolStatus } : feed;
}

// stream.reset reloads the detail of a run that keeps streaming without a
// replay. The server has not persisted the output in progress yet, so live
// items of the open round that the detail lacks are kept (with their status).
export function mergeOpenRound(detail: ChatFeed, live: ChatFeed): ChatFeed {
  const round = currentRound(detail.items);
  if (round < 0 || currentRound(live.items) !== round) {
    return detail;
  }
  const known = new Set(detail.items.map((item) => item.key));
  const extra = live.items.filter((item) => item.round === round && item.kind !== "user" && !known.has(item.key));
  if (extra.length === 0) {
    return detail;
  }
  const toolStatus = { ...detail.toolStatus };
  for (const item of extra) {
    if (item.kind === "tool" && live.toolStatus[item.key]) {
      toolStatus[item.key] = live.toolStatus[item.key];
    }
  }
  return { items: [...detail.items, ...extra], toolStatus };
}
