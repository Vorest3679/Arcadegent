import { FormEvent, useCallback, useEffect, useRef } from "react";
import {
  buildChatStreamUrl,
  cancelChatSession,
  deleteChatSession,
  dispatchChatSession,
  getChatSession,
  listChatSessions
} from "../api/sessionApi";
import { resolveClientLocationForSessionStart, warmupClientLocationCache } from "../lib/clientLocation";
import {
  getChatClientId,
  readStoredActiveSessionId,
  writeStoredActiveSessionId
} from "../lib/chatSessionStorage";
import { applyEventToRound, finishRound, sealOutput, startLiveRound } from "../lib/chatRounds";
import { mapArtifactsForEvent, toProgressText, toVisibleTurns } from "../lib/sse/chatStream";
import { isTerminalRunStatus, openRunStream, type RunStream } from "../lib/sse/runStream";
import { useAppStore } from "../stores/appStore";
import { useRunStore } from "../stores/runStore";
import { useSessionStore } from "../stores/sessionStore";
import type {
  ChatMapArtifacts,
  ChatSessionDetail,
  ChatStreamEnvelope,
  ChatTurnStep
} from "../types";
import { useStreamReply } from "./useStreamReply";

// Current snapshots; callbacks and async code must read fresh state, not a captured render.
const appState = () => useAppStore.getState();
const sessionState = () => useSessionStore.getState();
const runState = () => useRunStore.getState();

function makeSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `s_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  }
  return `s_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function hasSessionMapArtifacts(artifacts: ChatMapArtifacts): boolean {
  return Boolean(artifacts.route || artifacts.destination || artifacts.shops.length || artifacts.view_payload);
}

function mapArtifactsFromSession(detail: ChatSessionDetail): ChatMapArtifacts | null {
  const artifacts: ChatMapArtifacts = {
    shops: detail.shops,
    route: detail.route ?? null,
    client_location: detail.client_location ?? null,
    destination: detail.destination ?? null,
    view_payload: detail.view_payload ?? null,
    route_pending: false
  };
  return hasSessionMapArtifacts(artifacts) ? artifacts : null;
}

function isRunActive(detail: ChatSessionDetail): boolean {
  const run = detail.current_run;
  return Boolean(run && !isTerminalRunStatus(run.status));
}

export function useChatSessionController() {
  const sending = useSessionStore((state) => state.sending);
  const streamConnected = useRunStore((state) => state.streamConnected);
  const awaitingAssistant = useRunStore((state) => state.awaitingAssistant);

  const {
    appendStreamReply,
    cancelStreamReplyFlush,
    resetStreamReply,
    streamReplyDisplay,
    streamReplyTarget,
    syncStreamReply
  } = useStreamReply();

  const streamRef = useRef<RunStream | null>(null);
  const sessionGenerationRef = useRef(0);
  // Only the latest detail request may apply; an older response (e.g. the
  // running snapshot requested on stream.reset) must not overwrite a newer one.
  const detailRequestRef = useRef(0);
  // Final reply of the live run, kept until its detail hand-over succeeds.
  const finalReplyRef = useRef<string | null>(null);
  const clientIdRef = useRef("");
  if (!clientIdRef.current) {
    clientIdRef.current = getChatClientId();
  }

  const stopStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    runState().setStreamConnected(false);
  }, []);

  useEffect(() => {
    void loadSessionList(readStoredActiveSessionId() || undefined);
    void warmupClientLocationCache();
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

  async function giveUpRun(sessionId: string, runId: string): Promise<void> {
    sessionState().setChatError("实时连接中断，已尝试重连 3 次；正在停止本次请求。");
    const isLatest = beginDetailRequest();
    try {
      // Only cancel when the run we lost is still the one running.
      let detail = await getChatSession(sessionId, clientIdRef.current);
      if (detail.current_run?.run_id === runId && isRunActive(detail)) {
        detail = await cancelChatSession(sessionId, runId, clientIdRef.current);
      }
      if (isLatest()) {
        applySessionDetail(sessionId, detail, { preserveStreamState: true, reconnectStream: false });
      }
    } catch (err) {
      if (!isLatest()) {
        return;
      }
      sessionState().setChatError(
        err instanceof Error ? err.message : "停止中断会话失败，请稍后重试。"
      );
      void loadSession(sessionId, {
        preserveStreamState: true,
        reconnectStream: false,
        onFailure: () => commitRunLocally(runId)
      });
    }
  }

  function beginDetailRequest(): () => boolean {
    const generation = sessionGenerationRef.current;
    const request = ++detailRequestRef.current;
    return () => generation === sessionGenerationRef.current && request === detailRequestRef.current;
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
      url: (afterId) => buildChatStreamUrl(sessionId, runId, afterId, clientIdRef.current),
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
          void loadSession(sessionId, {
            preserveStreamState: true,
            reconnectStream: true,
            onFailure: () => commitRunLocally(runId)
          });
          void loadSessionList(sessionId, { preserveStreamState: true });
        },
        onReset: () => {
          void loadSession(sessionId, { preserveStreamState: true, reconnectStream: false });
        },
        onConnection: (connected) => runState().setStreamConnected(connected),
        onRetry: recordStreamReconnect,
        onGiveUp: () => {
          if (streamRef.current === stream) {
            streamRef.current = null;
          }
          void giveUpRun(sessionId, runId);
        }
      }
    });
    streamRef.current = stream;
  }

  function applySessionDetail(
    sessionId: string,
    detail: ChatSessionDetail,
    options?: { preserveStreamState?: boolean; reconnectStream?: boolean }
  ): void {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const reconnectStream = options?.reconnectStream ?? true;
    const store = sessionState();

    store.setActiveSessionId(sessionId);
    writeStoredActiveSessionId(sessionId);
    const detailArtifacts = mapArtifactsFromSession(detail);
    const visibleTurns = toVisibleTurns(detail.turns);
    if (detailArtifacts && !visibleTurns.some((turn) => Boolean(turn.map_artifacts))) {
      let lastAssistantIndex = -1;
      for (let index = visibleTurns.length - 1; index >= 0; index -= 1) {
        if (visibleTurns[index].role === "assistant") {
          lastAssistantIndex = index;
          break;
        }
      }
      if (lastAssistantIndex >= 0) {
        visibleTurns[lastAssistantIndex] = {
          ...visibleTurns[lastAssistantIndex],
          map_artifacts: detailArtifacts
        };
      }
    }
    const run = detail.current_run ?? null;
    const runActive = isRunActive(detail);

    store.setTurns(visibleTurns);
    runState().setActiveSubagent(detail.active_subagent || null);
    store.setActiveSessionStatus(detail.status);

    if (detail.status === "failed") {
      store.setChatError(detail.last_error?.trim() ? detail.last_error : "会话执行失败");
    } else {
      store.setChatError("");
    }

    if (run && runActive) {
      runState().setActiveMapArtifacts(detailArtifacts);
      runState().setAwaitingAssistant(true);
      if (reconnectStream) {
        // Without a cursor the run log replays the whole run, text included.
        startStream(sessionId, run.run_id);
      }
      return;
    }

    // Set together with `turns` so the live bubble hands over to the history
    // turn in the same render.
    runState().setActiveRunId(run?.run_id ?? null);
    runState().setCommittedRunId(run?.run_id ?? null);
    runState().setActiveMapArtifacts(null);
    runState().setAwaitingAssistant(false);
    if (!preserveStreamState) {
      stopStream();
      runState().setStreamItems([]);
      runState().setLiveRound(null);
      resetStreamReply();
    }
  }

  async function loadSessionList(
    preferredSessionId?: string,
    options?: { preserveStreamState?: boolean }
  ): Promise<void> {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const store = sessionState();
    const showLoading = !preserveStreamState;
    if (showLoading) {
      store.setSessionsLoading(true);
    }

    try {
      const rows = await listChatSessions(60, clientIdRef.current);
      const latestStore = sessionState();
      const activeOptimisticSession = preserveStreamState && latestStore.activeSessionStatus === "running"
        ? latestStore.sessions.find((item) => item.session_id === latestStore.activeSessionId)
        : null;
      const nextRows = activeOptimisticSession && !rows.some((item) => item.session_id === activeOptimisticSession.session_id)
        ? [activeOptimisticSession, ...rows]
        : rows;
      latestStore.setSessions(nextRows);

      if (!nextRows.length) {
        writeStoredActiveSessionId(null);
        latestStore.setActiveSessionId(null);
        latestStore.setActiveSessionStatus(null);
        latestStore.setTurns([]);
        runState().setActiveSubagent(null);
        runState().setActiveMapArtifacts(null);
        runState().setActiveRunId(null);
        runState().setCommittedRunId(null);
        if (!preserveStreamState) {
          stopStream();
          runState().setStreamItems([]);
          runState().setLiveRound(null);
          resetStreamReply();
          runState().setAwaitingAssistant(false);
        }
        return;
      }

      const currentActiveSessionId = latestStore.activeSessionId;
      const currentActiveStatus = latestStore.activeSessionStatus;
      const hasPreferred = preferredSessionId ? nextRows.some((item) => item.session_id === preferredSessionId) : false;
      const hasActive = currentActiveSessionId
        ? nextRows.some((item) => item.session_id === currentActiveSessionId)
        : false;
      const targetId = hasPreferred
        ? preferredSessionId
        : hasActive
          ? currentActiveSessionId
          : currentActiveSessionId && currentActiveStatus === "running"
            ? null
            : nextRows[0].session_id;

      if (targetId && targetId !== currentActiveSessionId) {
        await loadSession(targetId, { preserveStreamState, reconnectStream: true });
      }
    } catch (err) {
      sessionState().setChatError(err instanceof Error ? err.message : "加载会话列表失败");
    } finally {
      if (showLoading) {
        sessionState().setSessionsLoading(false);
      }
    }
  }

  async function loadSession(
    sessionId: string,
    options?: { preserveStreamState?: boolean; reconnectStream?: boolean; onFailure?: () => void }
  ): Promise<ChatSessionDetail | null> {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const reconnectStream = options?.reconnectStream ?? true;
    const isLatest = beginDetailRequest();
    const store = sessionState();
    // A background reload (hand-over, reset) keeps the content on screen; only
    // an explicit load shows the loading banner.
    if (!preserveStreamState) {
      store.setTurnsLoading(true);
    }
    store.setChatError("");

    try {
      const detail = await getChatSession(sessionId, clientIdRef.current);
      if (!isLatest()) {
        return null;
      }
      applySessionDetail(sessionId, detail, { preserveStreamState, reconnectStream });
      return detail;
    } catch (err) {
      if (!isLatest()) {
        return null;
      }
      sessionState().setChatError(err instanceof Error ? err.message : "加载会话失败");
      options?.onFailure?.();
      return null;
    } finally {
      if (isLatest()) {
        sessionState().setTurnsLoading(false);
      }
    }
  }

  function openChatView(): void {
    const app = appState();
    app.setViewMode("chat");
    app.setSidebarOpen(false);
  }

  function openArcadesView(): void {
    const app = appState();
    app.setViewMode("arcades");
    app.setSidebarOpen(false);
  }

  function startNewSession(): void {
    sessionGenerationRef.current += 1;
    stopStream();
    const app = appState();
    app.setViewMode("chat");
    app.setInputValue("");
    app.setSidebarOpen(false);
    const store = sessionState();
    store.resetActiveSessionState();
    runState().resetRunState();
    store.setTurnsLoading(false);
    writeStoredActiveSessionId(null);
    store.setChatError("");
    runState().setStreamItems([]);
    resetStreamReply();
  }

  async function submitChat(event: FormEvent): Promise<void> {
    event.preventDefault();
    const currentState = sessionState();
    const message = appState().inputValue.trim();
    if (!message || currentState.sending || runState().awaitingAssistant) {
      return;
    }

    const isNewSession = !currentState.activeSessionId;
    const previousSessionId = currentState.activeSessionId;
    const previousSessionStatus = currentState.activeSessionStatus;
    const previousSessions = currentState.sessions;
    const sessionId = currentState.activeSessionId || makeSessionId();
    const optimisticCreatedAt = new Date().toISOString();

    sessionGenerationRef.current += 1;
    resetStreamReply();
    runState().setStreamItems([]);
    runState().setLiveRound(null);
    runState().setActiveSubagent(null);
    runState().setActiveMapArtifacts(null);
    // No run id until dispatch answers; awaitingAssistant keeps the bubble.
    runState().setActiveRunId(null);
    currentState.setTurns((previous) => [...previous, { role: "user", content: message, created_at: optimisticCreatedAt }]);
    runState().setAwaitingAssistant(true);
    currentState.setTurnsLoading(false);
    currentState.setSending(true);
    currentState.setChatError("");
    currentState.setSessions((previous) => {
      const existing = previous.find((item) => item.session_id === sessionId);
      const optimistic = {
        session_id: sessionId,
        title: existing?.title ?? message.replace(/\s+/g, " ").slice(0, 32),
        preview: message.replace(/\s+/g, " ").slice(0, 72),
        intent: existing?.intent ?? "search" as const,
        status: "running" as const,
        turn_count: (existing?.turn_count ?? currentState.turns.length) + 1,
        created_at: existing?.created_at ?? optimisticCreatedAt,
        updated_at: optimisticCreatedAt
      };
      return [optimistic, ...previous.filter((item) => item.session_id !== sessionId)];
    });

    try {
      const location = isNewSession ? await resolveClientLocationForSessionStart() : undefined;
      const store = sessionState();

      appState().setInputValue("");
      store.setActiveSessionId(sessionId);
      writeStoredActiveSessionId(sessionId);
      store.setActiveSessionStatus("running");

      const dispatched = await dispatchChatSession({
        session_id: sessionId,
        client_id: clientIdRef.current,
        message,
        location: location ?? undefined,
        page_size: 5
      });
      const latestStore = sessionState();
      latestStore.setActiveSessionId(dispatched.session_id);
      writeStoredActiveSessionId(dispatched.session_id);
      latestStore.setActiveSessionStatus(dispatched.status);
      startStream(dispatched.session_id, dispatched.run_id);
      await loadSessionList(dispatched.session_id, { preserveStreamState: true });
    } catch (err) {
      const store = sessionState();
      store.setChatError(err instanceof Error ? err.message : "发送失败");
      appState().setInputValue(message);
      store.setActiveSessionId(previousSessionId);
      writeStoredActiveSessionId(previousSessionId);
      store.setActiveSessionStatus(previousSessionStatus);
      store.setSessions(previousSessions);
      store.setTurns((previous) => {
        const next = [...previous];
        const last = next[next.length - 1];
        if (last && last.role === "user" && last.content === message && last.created_at === optimisticCreatedAt) {
          next.pop();
        }
        return next;
      });
      runState().setStreamItems([]);
      runState().setLiveRound(null);
      runState().setActiveSubagent(null);
      runState().setActiveMapArtifacts(null);
      runState().setActiveRunId(runState().committedRunId);
      resetStreamReply();
      runState().setAwaitingAssistant(false);
      stopStream();
    } finally {
      sessionState().setSending(false);
    }
  }

  function quickAsk(prompt: string): void {
    const app = appState();
    app.setInputValue(prompt);
    app.setViewMode("chat");
    app.setSidebarOpen(false);
  }

  async function removeSession(sessionId: string): Promise<void> {
    const currentState = sessionState();
    if (currentState.deletingSessionId || currentState.sending) {
      return;
    }

    const ok = window.confirm("确认删除这个历史会话吗？");
    if (!ok) {
      return;
    }

    currentState.setDeletingSessionId(sessionId);
    currentState.setChatError("");

    try {
      await deleteChatSession(sessionId, clientIdRef.current);
      const store = sessionState();
      const isActive = store.activeSessionId === sessionId;

      if (isActive) {
        store.resetActiveSessionState();
        runState().resetRunState();
        writeStoredActiveSessionId(null);
        runState().setStreamItems([]);
        resetStreamReply();
      }

      await loadSessionList(isActive ? undefined : store.activeSessionId || undefined);
    } catch (err) {
      sessionState().setChatError(err instanceof Error ? err.message : "删除会话失败");
    } finally {
      sessionState().setDeletingSessionId(null);
    }
  }

  function selectSession(sessionId: string): void {
    sessionGenerationRef.current += 1;
    stopStream();
    const app = appState();
    app.setViewMode("chat");
    void loadSession(sessionId);
    app.setSidebarOpen(false);
  }

  function refreshSessions(): void {
    void loadSessionList(sessionState().activeSessionId || undefined);
  }

  return {
    openChatView,
    openArcadesView,
    startNewSession,
    submitChat,
    quickAsk,
    removeSession,
    selectSession,
    refreshSessions,
    streamReply: streamReplyDisplay,
    streamReplyActive:
      sending ||
      streamConnected ||
      awaitingAssistant ||
      streamReplyDisplay.length < streamReplyTarget.length
  };
}
