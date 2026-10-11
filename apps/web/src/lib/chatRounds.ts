import type { ChatHistoryTurn, ChatRun, ChatStreamEnvelope } from "../types";
import { isTerminalRunStatus } from "./sse/runStream";

// One round = a user message and everything that answered it. History
// (from the session detail) and the live run map to the same structure.

export type RoundStatus = "running" | "completed" | "failed" | "cancelled";
export type ToolStepStatus = "running" | "completed" | "failed" | "cancelled";

export type RoundStep =
  | { kind: "tool"; callId: string; name: string; agent: string; status: ToolStepStatus; at: string }
  | { kind: "text"; agent: string; content: string; at: string };

export type ChatRound = {
  // History: created_at of the user turn. Live: the run id.
  key: string;
  user: ChatHistoryTurn;
  steps: RoundStep[];
  reply: ChatHistoryTurn | null;
  status: RoundStatus;
};

export function roundsFromDetail(turns: ChatHistoryTurn[], run?: ChatRun | null): ChatRound[] {
  const rounds: ChatRound[] = [];
  for (const turn of turns) {
    if (turn.role === "user") {
      rounds.push({
        key: turn.created_at,
        user: turn,
        steps: (turn.steps ?? []).map((step, index): RoundStep =>
          step.kind === "tool"
            ? {
                kind: "tool",
                callId: step.call_id || `${turn.created_at}-${index}`,
                name: step.name,
                agent: step.agent ?? "",
                status: step.status,
                at: step.created_at
              }
            : { kind: "text", agent: step.agent ?? "", content: step.content, at: step.created_at }
        ),
        reply: null,
        status: "failed"
      });
    } else if (turn.role === "assistant" && rounds.length > 0) {
      const round = rounds[rounds.length - 1];
      if (!round.reply) {
        round.reply = turn;
        round.status = "completed";
      }
    }
  }
  const last = rounds[rounds.length - 1];
  if (last && !last.reply && run) {
    last.status = isTerminalRunStatus(run.status)
      ? run.status === "cancelled" ? "cancelled" : "failed"
      : "running";
    if (last.status === "running") {
      last.key = run.run_id;
    }
  }
  return rounds;
}

export function startLiveRound(runId: string, user: ChatHistoryTurn): ChatRound {
  return { key: runId, user, steps: [], reply: null, status: "running" };
}

function stringField(data: Record<string, unknown>, field: string): string {
  const value = data[field];
  return typeof value === "string" ? value : "";
}

// Folds one stream event into the round's tool steps. Replayed events are
// idempotent: a repeated tool.started or terminal event for the same call
// changes nothing.
export function applyEventToRound(round: ChatRound, envelope: ChatStreamEnvelope): ChatRound {
  const { event, data } = envelope;
  if (event !== "tool.started" && event !== "tool.completed" && event !== "tool.failed") {
    return round;
  }
  const callId = stringField(data, "call_id");
  const name = stringField(data, "tool");
  if (!callId || !name) {
    return round;
  }
  const status: ToolStepStatus =
    event === "tool.started" ? "running" : event === "tool.completed" ? "completed" : "failed";
  const index = round.steps.findIndex((step) => step.kind === "tool" && step.callId === callId);
  if (index < 0) {
    const step: RoundStep = {
      kind: "tool",
      callId,
      name,
      agent: stringField(data, "active_subagent"),
      status,
      at: envelope.at
    };
    return { ...round, steps: [...round.steps, step] };
  }
  const current = round.steps[index];
  if (current.kind !== "tool" || current.status === status || current.status !== "running") {
    return round;
  }
  const steps = round.steps.slice();
  steps[index] = { ...current, status };
  return { ...round, steps };
}

// A new output_id sealed the previous one: it becomes an intermediate reply.
export function sealOutput(round: ChatRound, text: string, at: string, agent = ""): ChatRound {
  if (!text.trim()) {
    return round;
  }
  return { ...round, steps: [...round.steps, { kind: "text", agent, content: text, at }] };
}

// The run ended: tools still running will not report back.
export function finishRound(round: ChatRound, status: Exclude<RoundStatus, "running">): ChatRound {
  const open = round.steps.some((step) => step.kind === "tool" && step.status === "running");
  if (!open && round.status === status) {
    return round;
  }
  const toolStatus: ToolStepStatus = status === "cancelled" ? "cancelled" : "failed";
  return {
    ...round,
    status,
    steps: round.steps.map((step) =>
      step.kind === "tool" && step.status === "running" ? { ...step, status: toolStatus } : step
    )
  };
}

export function textSteps(round: ChatRound | null | undefined): string[] {
  return round ? round.steps.flatMap((step) => step.kind === "text" ? [step.content] : []) : [];
}
