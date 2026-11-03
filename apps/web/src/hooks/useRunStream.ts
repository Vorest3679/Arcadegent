import { useCallback, useEffect, useRef } from "react";
import { buildChatStreamUrl } from "../api/sessionApi";
import { preloadAmapSdk } from "../lib/amap/loader";
import { applyOutputText, applyRunEvent, finishRun, sealOutput } from "../lib/chatFeed";
import { isTerminalRunStatus, openRunStream, type RunStream } from "../lib/sse/runStream";
import { useChatFeedStore } from "../stores/chatFeedStore";
import { useRunStore } from "../stores/runStore";
import { useSessionStore } from "../stores/sessionStore";
import type { ChatStreamEnvelope } from "../types";

const sessionState = () => useSessionStore.getState();
const runState = () => useRunStore.getState();
const feedState = () => useChatFeedStore.getState();

// Tools whose results usually end in a map: the SDK starts loading when they start.
const MAP_TOOLS = new Set(["db_query_tool", "result_selection_tool", "geo_resolve_tool", "route_plan_tool"]);

function preloadsMap(envelope: ChatStreamEnvelope): boolean {
  if (envelope.event === "tool.started") {
    return MAP_TOOLS.has(String(envelope.data.tool));
  }
  return envelope.event === "domain.arcade.results_ready" || envelope.event === "navigation.route_ready";
}

// What the owner of the session does when the followed run ends or is lost.
export type RunStreamCallbacks = {
  // The run reached a terminal state: load its detail (hand-over).
  onTerminal: (sessionId: string, runId: string) => void;
  // stream.reset: the server cannot replay; refresh history.
  onReset: (sessionId: string) => void;
  // Reconnects are exhausted.
  onGiveUp: (sessionId: string, runId: string) => void;
};

// Follows one run over SSE and appends what it produces to the chat feed.
// Session loading, hand-over and cancellation stay with the caller, reached
// through `callbacks`.
export function useRunStream(clientId: string, callbacks: RunStreamCallbacks) {
  const streamRef = useRef<RunStream | null>(null);
  // Always the latest callbacks, so a long-lived stream never calls a stale closure.
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const stopStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    runState().setStreamConnected(false);
  }, []);

  useEffect(() => stopStream, [stopStream]);

  function handleRunEvent(sessionId: string, envelope: ChatStreamEnvelope): void {
    runState().setStreamNotice(null);
    if (preloadsMap(envelope)) {
      preloadAmapSdk();
    }
    if (envelope.event === "session.started" || envelope.event === "subagent.changed") {
      const next = envelope.data.to_subagent ?? envelope.data.active_subagent;
      if (typeof next === "string" && next) {
        runState().setActiveSubagent(next);
      }
    }
    if (envelope.event === "assistant.completed") {
      sessionState().setActiveSessionStatus("completed");
      updateSessionPreview(sessionId, envelope, "completed");
    }
    if (envelope.event === "session.failed") {
      sessionState().setActiveSessionStatus("failed");
      updateSessionPreview(sessionId, envelope, "failed");
    }
    feedState().setFeed((feed) => applyRunEvent(feed, envelope));
  }

  function updateSessionPreview(sessionId: string, envelope: ChatStreamEnvelope, status: "completed" | "failed"): void {
    const reply = envelope.data.reply;
    if (typeof reply !== "string" || !reply) {
      return;
    }
    sessionState().setSessions((previous) => previous.map((item) =>
      item.session_id === sessionId
        ? {
            ...item,
            preview: reply.replace(/\s+/g, " ").trim().slice(0, 72),
            status,
            turn_count: item.turn_count + 1,
            updated_at: envelope.at
          }
        : item
    ));
  }

  // The detail could not be loaded: the feed already holds everything the
  // run produced, so it simply becomes the history.
  function commitRunLocally(runId: string): void {
    feedState().setFeed((feed) => finishRun(feed, "completed"));
    runState().setRunOpen(false);
    runState().setActiveRunId(runId);
    runState().setAwaitingAssistant(false);
  }

  function startStream(sessionId: string, runId: string): void {
    stopStream();
    runState().setActiveRunId(runId);
    runState().setStreamNotice(null);
    runState().setRunOpen(true);
    sessionState().setActiveSessionStatus("running");

    const stream: RunStream = openRunStream({
      url: (afterId) => buildChatStreamUrl(sessionId, runId, afterId, clientId),
      sessionId,
      runId,
      handlers: {
        onEvent: (envelope) => handleRunEvent(sessionId, envelope),
        onText: (outputId, text) => {
          const agent = runState().activeSubagent ?? "";
          feedState().setFeed((feed) => applyOutputText(feed, outputId, text, new Date().toISOString(), agent));
        },
        onOutputSealed: (outputId) => feedState().setFeed((feed) => sealOutput(feed, outputId)),
        onState: (status) => {
          if (!isTerminalRunStatus(status)) {
            sessionState().setActiveSessionStatus("running");
            return;
          }
          const finalStatus = status === "completed" || status === "cancelled" ? status : "failed";
          feedState().setFeed((feed) => finishRun(feed, finalStatus));
          runState().setRunOpen(false);
          if (streamRef.current === stream) {
            streamRef.current = null;
          }
          // The composer stays locked until the detail is loaded
          // (applySessionDetail releases it).
          callbacksRef.current.onTerminal(sessionId, runId);
        },
        onReset: () => callbacksRef.current.onReset(sessionId),
        onConnection: (connected) => runState().setStreamConnected(connected),
        onRetry: (attempt, maxRetries) => {
          runState().setStreamNotice(`实时连接中断，正在第 ${attempt}/${maxRetries} 次重连`);
        },
        onGiveUp: () => {
          if (streamRef.current === stream) {
            streamRef.current = null;
          }
          runState().setStreamNotice(null);
          runState().setRunOpen(false);
          callbacksRef.current.onGiveUp(sessionId, runId);
        }
      }
    });
    streamRef.current = stream;
  }

  // Stops following the run and clears its transport state.
  function resetRunView(): void {
    stopStream();
    runState().resetRunState();
  }

  return { startStream, commitRunLocally, resetRunView };
}
