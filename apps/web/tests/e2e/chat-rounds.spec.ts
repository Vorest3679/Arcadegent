import { expect, test } from "@playwright/test";
import {
  applyEventToRound,
  finishRound,
  roundsFromDetail,
  sealOutput,
  startLiveRound
} from "../../src/lib/chatRounds";
import { assertChatSessionDetail, isStreamEnvelope } from "../../src/lib/sessionSchema";
import type { ChatHistoryTurn, ChatStreamEnvelope } from "../../src/types";

const user = (at: string, steps: ChatHistoryTurn["steps"] = []): ChatHistoryTurn => ({
  role: "user",
  content: "q",
  created_at: at,
  steps
});
const assistant = (at: string): ChatHistoryTurn => ({ role: "assistant", content: "a", created_at: at });

function toolEvent(id: number, event: ChatStreamEnvelope["event"], callId: string, tool = "db_query_tool"): ChatStreamEnvelope {
  return {
    id,
    session_id: "s",
    run_id: "r",
    kind: "event",
    event,
    at: `2026-01-01T00:00:0${id}Z`,
    data: { tool, call_id: callId, active_subagent: "search_worker" }
  };
}

test("roundsFromDetail attaches steps and replies to their user turn", () => {
  const rounds = roundsFromDetail([
    user("t1", [
      { kind: "text", agent: "main_agent", content: "先查一下", created_at: "t1a" },
      { kind: "tool", call_id: "c1", name: "db_query_tool", agent: "search_worker", status: "failed", created_at: "t1b" }
    ]),
    assistant("t1c"),
    user("t2"),
  ]);
  expect(rounds).toHaveLength(2);
  expect(rounds[0].status).toBe("completed");
  expect(rounds[0].reply?.content).toBe("a");
  expect(rounds[0].steps.map((step) => step.kind)).toEqual(["text", "tool"]);
  // A round without a reply and without a live run did not finish.
  expect(rounds[1].status).toBe("failed");
});

test("roundsFromDetail marks the unanswered last round by run status", () => {
  const turns = [user("t1")];
  expect(roundsFromDetail(turns, { run_id: "r1", status: "running" })[0]).toMatchObject({
    status: "running",
    key: "r1"
  });
  expect(roundsFromDetail(turns, { run_id: "r1", status: "cancelled" })[0].status).toBe("cancelled");
});

test("live tool events are idempotent under replay and cancel closes running tools", () => {
  let round = startLiveRound("r", user("t1"));
  const events = [
    toolEvent(1, "tool.started", "c1"),
    toolEvent(2, "tool.started", "c2", "route_plan_tool"),
    toolEvent(3, "tool.completed", "c1")
  ];
  for (const event of [...events, ...events]) {
    round = applyEventToRound(round, event);
  }
  expect(round.steps).toHaveLength(2);
  expect(round.steps.map((step) => step.kind === "tool" && step.status)).toEqual(["completed", "running"]);

  round = sealOutput(round, "  ", "t");
  expect(round.steps).toHaveLength(2);
  round = sealOutput(round, "中间", "t");
  expect(round.steps).toHaveLength(3);

  round = finishRound(round, "cancelled");
  expect(round.status).toBe("cancelled");
  expect(round.steps.map((step) => step.kind === "tool" && step.status)).toEqual([
    "completed",
    "cancelled",
    false
  ]);
});

test("a failed tool is not overwritten by a late completion", () => {
  let round = startLiveRound("r", user("t1"));
  for (const event of [toolEvent(1, "tool.started", "c1"), toolEvent(2, "tool.failed", "c1"), toolEvent(3, "tool.completed", "c1")]) {
    round = applyEventToRound(round, event);
  }
  expect(round.steps[0]).toMatchObject({ status: "failed" });
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
