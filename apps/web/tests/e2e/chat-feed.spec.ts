import { expect, test } from "@playwright/test";
import {
  appendUser,
  applyOutputText,
  applyRunEvent,
  EMPTY_FEED,
  feedFromTurns,
  finishRun,
  mergeOpenRound,
  sealOutput,
  withoutOpenRound,
  type ChatFeed
} from "../../src/lib/chatFeed";
import { assertChatSessionDetail, isStreamEnvelope } from "../../src/lib/sessionSchema";
import type { ChatHistoryTurn, ChatStreamEnvelope } from "../../src/types";

const user = (at: string, steps: ChatHistoryTurn["steps"] = []): ChatHistoryTurn => ({
  role: "user",
  content: "q",
  created_at: at,
  steps
});
const assistant = (at: string): ChatHistoryTurn => ({ role: "assistant", content: "a", created_at: at });

function envelope(id: number, event: ChatStreamEnvelope["event"], data: Record<string, unknown>, outputId?: string): ChatStreamEnvelope {
  return {
    id,
    session_id: "s",
    run_id: "r",
    kind: "event",
    event,
    output_id: outputId,
    at: `2026-01-01T00:00:0${id}Z`,
    data
  };
}

function toolEvent(id: number, event: ChatStreamEnvelope["event"], callId: string, tool = "db_query_tool"): ChatStreamEnvelope {
  return envelope(id, event, { tool, call_id: callId, active_subagent: "search_worker" });
}

const kinds = (feed: ChatFeed) => feed.items.map((item) => item.kind);

test("history turns become one ordered feed with tool status kept aside", () => {
  const feed = feedFromTurns([
    user("t1", [
      { kind: "text", agent: "main_agent", content: "先查一下", created_at: "t1a" },
      { kind: "tool", call_id: "c1", name: "db_query_tool", agent: "search_worker", status: "failed", created_at: "t1b" }
    ]),
    { ...assistant("t1c"), map_artifacts: { shops: [], route: null, view_payload: { scene: "agent_route" } } },
    user("t2")
  ]);
  expect(kinds(feed)).toEqual(["user", "assistant", "tool", "assistant", "map", "user"]);
  expect(feed.items.map((item) => item.key)).toEqual(["r0:user", "r0:a0", "r0:t:c1", "r0:a1", "r0:map", "r1:user"]);
  expect(feed.items[1]).toMatchObject({ final: false, streaming: false });
  expect(feed.items[3]).toMatchObject({ final: true });
  expect(feed.toolStatus).toEqual({ "r0:t:c1": "failed" });
});

test("live events append in arrival order and keep the keys the history will use", () => {
  let feed = appendUser(EMPTY_FEED, "q", "t0");
  feed = applyOutputText(feed, "out_a", "我先", "t1");
  feed = applyOutputText(feed, "out_a", "我先查一下。", "t1");
  feed = sealOutput(feed, "out_a");
  feed = applyRunEvent(feed, toolEvent(2, "tool.started", "c1"));
  feed = applyOutputText(feed, "out_b", "找到了", "t3");
  feed = applyRunEvent(feed, envelope(4, "assistant.completed", { reply: "找到了 Arcade One。" }, "out_b"));
  expect(kinds(feed)).toEqual(["user", "assistant", "tool", "assistant"]);
  expect(feed.items[1]).toMatchObject({ key: "r0:a0", content: "我先查一下。", streaming: false, final: false });
  expect(feed.items[3]).toMatchObject({ key: "r0:a1", content: "找到了 Arcade One。", streaming: false, final: true });

  const history = feedFromTurns([
    user("t0", [
      { kind: "text", content: "我先查一下。", created_at: "t1" },
      { kind: "tool", call_id: "c1", name: "db_query_tool", status: "completed", created_at: "t2" }
    ]),
    { role: "assistant", content: "找到了 Arcade One。", created_at: "t4" }
  ]);
  expect(history.items.map((item) => item.key)).toEqual(feed.items.map((item) => item.key));
});

test("tool events are idempotent under replay and the run end closes running tools", () => {
  let feed = appendUser(EMPTY_FEED, "q", "t0");
  const events = [
    toolEvent(1, "tool.started", "c1"),
    toolEvent(2, "tool.started", "c2", "geo_resolve_tool"),
    toolEvent(3, "tool.completed", "c1")
  ];
  for (const event of [...events, ...events]) {
    feed = applyRunEvent(feed, event);
  }
  expect(kinds(feed)).toEqual(["user", "tool", "tool"]);
  expect(feed.toolStatus).toEqual({ "r0:t:c1": "completed", "r0:t:c2": "running" });

  feed = finishRun(feed, "cancelled");
  expect(feed.toolStatus).toEqual({ "r0:t:c1": "completed", "r0:t:c2": "cancelled" });
});

test("a failed tool is not overwritten by a late completion", () => {
  let feed = appendUser(EMPTY_FEED, "q", "t0");
  for (const event of [toolEvent(1, "tool.started", "c1"), toolEvent(2, "tool.failed", "c1"), toolEvent(3, "tool.completed", "c1")]) {
    feed = applyRunEvent(feed, event);
  }
  expect(feed.toolStatus["r0:t:c1"]).toBe("failed");
});

test("a failed session keeps its fallback reply and appends the error", () => {
  let feed = appendUser(EMPTY_FEED, "q", "t0");
  feed = applyOutputText(feed, "out_x", "兜底", "t1");
  feed = applyRunEvent(feed, envelope(2, "session.failed", { reply: "兜底回复", error: "provider error" }, "out_x"));
  expect(kinds(feed)).toEqual(["user", "assistant", "error"]);
  expect(feed.items[1]).toMatchObject({ content: "兜底回复", final: true });
  expect(feed.items[2]).toMatchObject({ message: "provider error" });
});

test("route events upsert one map item per round", () => {
  let feed = appendUser(EMPTY_FEED, "q", "t0");
  feed = applyRunEvent(feed, envelope(1, "worker.started", { worker: "navigation_worker" }));
  feed = applyRunEvent(feed, envelope(2, "navigation.route_ready", { provider: "amap", mode: "walking", distance_m: 1200, polyline: [] }));
  const maps = feed.items.filter((item) => item.kind === "map");
  expect(maps).toHaveLength(1);
  expect(maps[0]).toMatchObject({ key: "r0:map", artifacts: { route: { distance_m: 1200 }, route_pending: true } });
});

test("a replayed run starts from the bare user message of the open round", () => {
  const feed = withoutOpenRound(feedFromTurns([
    user("t1"),
    assistant("t1a"),
    user("t2", [{ kind: "tool", call_id: "c9", name: "db_query_tool", status: "completed", created_at: "t2a" }])
  ]));
  expect(kinds(feed)).toEqual(["user", "assistant", "user"]);
  expect(feed.toolStatus).toEqual({});
});

test("a reset detail keeps the output still streaming in the open round", () => {
  let live = appendUser(EMPTY_FEED, "q", "t0");
  live = applyOutputText(live, "out_a", "我先查一下。", "t1");
  live = applyRunEvent(live, toolEvent(2, "tool.started", "c1"));
  live = applyOutputText(live, "out_b", "找到", "t3");
  // The server persisted the first reply and the tool, not the output in progress.
  const detail = feedFromTurns([
    user("t0", [
      { kind: "text", content: "我先查一下。", created_at: "t1" },
      { kind: "tool", call_id: "c1", name: "db_query_tool", status: "completed", created_at: "t2" }
    ])
  ]);
  const merged = mergeOpenRound(detail, live);
  expect(merged.items.map((item) => item.key)).toEqual(["r0:user", "r0:a0", "r0:t:c1", "r0:a1"]);
  expect(merged.items[3]).toMatchObject({ content: "找到", streaming: true, outputId: "out_b" });
  expect(merged.toolStatus["r0:t:c1"]).toBe("completed");
  // Later tokens of that output update the kept item instead of adding one.
  expect(applyOutputText(merged, "out_b", "找到了", "t4").items).toHaveLength(4);
});

test("session schema rejects malformed payloads without echoing them", () => {
  const valid = {
    session_id: "s",
    status: "idle",
    shops: [],
    created_at: "t",
    turns: [user("t1", [{ kind: "tool", name: "x", status: "completed", created_at: "t" }])]
  };
  expect(assertChatSessionDetail(valid)).toBe(valid);
  expect(() => assertChatSessionDetail({ ...valid, turns: [{ role: "robot", content: "secret", created_at: "t" }] }))
    .toThrow(/格式不正确/);
  expect(() => assertChatSessionDetail({ ...valid, turns: [user("t", [{ kind: "tool", name: "x", status: "weird", created_at: "t" } as never])] }))
    .toThrow();
  expect(() => assertChatSessionDetail({ ...valid, current_run: { run_id: "r", status: "bogus" } })).toThrow();
  try {
    assertChatSessionDetail({ session_id: "secret-value" });
  } catch (error) {
    expect(String(error)).not.toContain("secret-value");
  }
  expect(isStreamEnvelope(toolEvent(1, "tool.started", "c"))).toBe(true);
  expect(isStreamEnvelope({ ...toolEvent(1, "tool.failed", "c"), id: "1" })).toBe(false);
});

test("events outside the catalog are not part of the stream contract", () => {
  const base = toolEvent(1, "tool.started", "c");
  expect(isStreamEnvelope({ ...base, event: "tool.progress" })).toBe(false);
  expect(isStreamEnvelope({ ...base, event: "run.state" })).toBe(false);
  expect(isStreamEnvelope({ ...base, kind: "control", event: "tool.started" })).toBe(false);
  expect(isStreamEnvelope({ ...base, kind: "control", event: "run.state", status: "running" })).toBe(true);
});
