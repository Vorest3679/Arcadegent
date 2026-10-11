import { useCallback, useEffect, useRef } from "react";
import { buildChatStreamUrl } from "../api/sessionApi";
import { applyEventToRound, finishRound, sealOutput, startLiveRound } from "../lib/chatRounds";
import { mapArtifactsForEvent, toProgressText } from "../lib/sse/chatStream";
import { isTerminalRunStatus, openRunStream, type RunStream } from "../lib/sse/runStream";
import { useRunStore } from "../stores/runStore";
import { useSessionStore } from "../stores/sessionStore";
import type { ChatStreamEnvelope, ChatTurnStep } from "../types";
import { useStreamReply } from "./useStreamReply";

const sessionState = () => useSessionStore.getState();
const runState = () => useRunStore.getState();

// What the owner of the session does when the followed run ends or is lost.
export type RunStreamCallbacks = {
  // The run reached a terminal state: load its detail (hand-over).
  onTerminal: (sessionId: string, runId: string) => void;
  // stream.reset: the server cannot replay; refresh history.
  onReset: (sessionId: string) => void;
  // Reconnects are exhausted.
  onGiveUp: (sessionId: string, runId: string) => void;
};

// Follows one run over SSE and mirrors it into the run store (progress, live
// steps, map) and the streamed reply text. Session loading, hand-over and
// cancellation stay with the caller, reached through `callbacks`.
export function useRunStream(clientId: string, callbacks: RunStreamCallbacks) {
  const {
    appendStreamReply,
    cancelStreamReplyFlush,
    resetStreamReply,
    streamReplyDisplay,
    streamReplyTarget,
    syncStreamReply
  } = useStreamReply();

  const streamRef = useRef<RunStream | null>(null);
  // Final reply of the live run, kept until its detail hand-over succeeds.
  const finalReplyRef = useRef<string | null>(null);
  // Always the latest callbacks, so a long-lived stream never calls a stale closure.
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const stopStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    runState().setStreamConnected(false);
  }, []);

  useEffect(() => {
    return () => {
      cancelStreamReplyFlush();
      stopStream();
    };
  }, [cancelStreamReplyFlush, stopStream]);

  function pushStreamEnvelope(envelope: ChatStreamEnvelope): void {
    runState().setStreamItems([
      {
        id: envelope.id,
        event: envelope.event,
        text: toProgressText(envelope),
        at: envelope.at
      }
    ]);
  }

  function recordStreamReconnect(attempt: number, maxRetries: number): void {
    runState().setStreamItems([
      {
        // Negative ids are local transport status records. Server event ids are
        // positive and remain the cursor used for replay.
        id: -attempt,
        event: "stream.reconnecting",
        text: `实时连接中断，正在第 ${attempt}/${maxRetries} 次重连（将从上次事件继续）`,
        at: new Date().toISOString()
      }
    ]);
  }

  function handleRunEvent(sessionId: string, envelope: ChatStreamEnvelope): void {
    const store = sessionState();

    if (envelope.event === "session.started" || envelope.event === "subagent.changed") {
      const next = envelope.data.to_subagent ?? envelope.data.active_subagent;
      if (typeof next === "string" && next) {
        runState().setActiveSubagent(next);
      }
    }

    const artifacts = mapArtifactsForEvent(envelope, runState().activeMapArtifacts);
    if (artifacts !== undefined) {
      runState().setActiveMapArtifacts(artifacts);
    }

    if (envelope.event === "assistant.completed") {
      store.setActiveSessionStatus("completed");
      acceptFinalReply(sessionId, envelope, "completed");
    }

    if (envelope.event === "session.failed") {
      store.setActiveSessionStatus("failed");
      const error = envelope.data.error;
      store.setChatError(typeof error === "string" && error.trim() ? error : "会话执行失败");
      // A model failure still ends with a stored fallback reply; interruptions carry none.
      acceptFinalReply(sessionId, envelope, "failed");
    }

    recordToolStep(envelope);
    pushStreamEnvelope(envelope);
  }

  // Folds tool events into the live round; a no-op when no round is followed.
  function recordToolStep(envelope: ChatStreamEnvelope): void {
    runState().setLiveRound((round) => round && applyEventToRound(round, envelope));
  }

  // The terminal reply is authoritative for the final output and is kept for
  // the local hand-over in case the session detail cannot be loaded.
  function acceptFinalReply(sessionId: string, envelope: ChatStreamEnvelope, status: "completed" | "failed"): void {
    const reply = envelope.data.reply;
    if (typeof reply !== "string" || !reply) {
      return;
    }
    finalReplyRef.current = reply;
    syncStreamReply(reply);
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

  // 当 detail 无法加载时，保留本地的最终回复和中间输出作为历史记录。
  function commitRunLocally(runId: string): void {
    const store = sessionState();
    const reply = finalReplyRef.current?.trim();
    // 实时的中间回复将随着移交而消失；保留它们
    // 随着回合用户转动的步骤，就像服务器细节一样。
    const intermediates = (runState().liveRound?.steps ?? []).map((step): ChatTurnStep =>
      step.kind === "text"
        ? { kind: "text", agent: step.agent, content: step.content, created_at: step.at }
        : {
            kind: "tool",
            call_id: step.callId,
            name: step.name,
            agent: step.agent,
            status: step.status === "completed" ? "completed" : "failed",
            created_at: step.at
          }
    );
    if (reply || intermediates.length > 0) {
      const artifacts = runState().activeMapArtifacts;
      store.setTurns((previous) => {
        const next = [...previous];// 找到最后一个来自用户的轮次并附加中间回复
        const lastUser = next.map((turn) => turn.role).lastIndexOf("user");
        if (lastUser >= 0 && intermediates.length > 0) {//如果有最终回复，则添加一个助手轮次。
          next[lastUser] = { ...next[lastUser], steps: intermediates };
        }
        if (reply) {
          next.push({
            role: "assistant",
            content: reply,
            map_artifacts: artifacts ? { ...artifacts, route_pending: false } : null,
            created_at: new Date().toISOString()
          });
        }
        return next;
      });
    }
    finalReplyRef.current = null;
    runState().setActiveRunId(runId);
    runState().setCommittedRunId(runId);
    runState().setActiveMapArtifacts(null);
    runState().setAwaitingAssistant(false);
  }

  function startStream(sessionId: string, runId: string): void {
    stopStream();
    const store = sessionState();
    runState().setActiveRunId(runId);
    runState().setStreamItems([]);
    runState().setLiveRound(null);
    finalReplyRef.current = null;
    store.setActiveSessionStatus("running");
    resetStreamReply();
    const lastUser = [...store.turns].reverse().find((turn) => turn.role === "user");
    runState().setLiveRound(lastUser ? startLiveRound(runId, lastUser) : null);

    const stream: RunStream = openRunStream({
      url: (afterId) => buildChatStreamUrl(sessionId, runId, afterId, clientId),
      sessionId,
      runId,
      handlers: {
        onEvent: (envelope) => handleRunEvent(sessionId, envelope),
        onText: (_outputId, text, delta) => appendStreamReply(text, delta),
        onOutputSealed: (_outputId, text) => {
          runState().setLiveRound((previous) =>
            previous ? sealOutput(previous, text, new Date().toISOString(), "main_agent") : previous
          );
          resetStreamReply();
        },
        onState: (status) => {
          if (!isTerminalRunStatus(status)) {
            sessionState().setActiveSessionStatus("running");
            return;
          }
          const finalStatus = status === "completed" || status === "cancelled" ? status : "failed";
          runState().setLiveRound((previous) =>
            previous ? finishRound(previous, finalStatus) : previous
          );
          if (streamRef.current === stream) {
            streamRef.current = null;
          }
          // The run's reply becomes a history turn once the detail is loaded;
          // the composer stays locked until then (applySessionDetail releases it).
          // A different run already active (e.g. from another tab) is followed.
          callbacksRef.current.onTerminal(sessionId, runId);
        },
        onReset: () => {
          callbacksRef.current.onReset(sessionId);
        },
        onConnection: (connected) => runState().setStreamConnected(connected),
        onRetry: recordStreamReconnect,
        onGiveUp: () => {
          if (streamRef.current === stream) {
            streamRef.current = null;
          }
          callbacksRef.current.onGiveUp(sessionId, runId);
        }
      }
    });
    streamRef.current = stream;
  }


  return {
    startStream,
    stopStream,
    commitRunLocally,
    resetStreamReply,
    streamReply: streamReplyDisplay,
    // Text still being revealed.
    streamReplyPending: streamReplyDisplay.length < streamReplyTarget.length
  };
}
