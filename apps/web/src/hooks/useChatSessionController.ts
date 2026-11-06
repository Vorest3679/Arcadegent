import { FormEvent, useEffect, useRef, useState } from "react";
import {
  cancelChatSession,
  deleteChatSession,
  dispatchChatSession,
  getChatSession,
  listChatSessions,
  setChatSessionPinned
} from "../api/sessionApi";
import {
  loadCachedClientLocation,
  resolveClientLocationForSessionStart,
  warmupClientLocationCache
} from "../lib/clientLocation";
import {
  getChatClientId,
  readStoredActiveSessionId,
  writeStoredActiveSessionId
} from "../lib/chatSessionStorage";
import { appendError, appendUser, feedFromDetail, mergeOpenRound, withoutOpenRound } from "../lib/chatFeed";
import { isRunActive, makeSessionId } from "../lib/sessionDetail";
import { useAppStore } from "../stores/appStore";
import { useChatFeedStore } from "../stores/chatFeedStore";
import { useRunStore } from "../stores/runStore";
import { useSessionStore } from "../stores/sessionStore";
import type { ChatSessionDetail } from "../types";
import { useRunStream } from "./useRunStream";

// Current snapshots; callbacks and async code must read fresh state, not a captured render.
const appState = () => useAppStore.getState();
const sessionState = () => useSessionStore.getState();
const runState = () => useRunStore.getState();
const feedState = () => useChatFeedStore.getState();

// Errors are part of the chat feed, after whatever the user was looking at.
function pushError(message: string): void {
  feedState().setFeed((feed) => appendError(feed, message, new Date().toISOString()));
}

// Orchestrates the chat view: session list and detail, send, delete, switch and
// refresh-recovery. Following a run lives in useRunStream.
export function useChatSessionController() {
  const [clientLocation, setClientLocation] = useState(loadCachedClientLocation);

  const sessionGenerationRef = useRef(0);
  // Only the latest detail request may apply; an older response (e.g. the
  // running snapshot requested on stream.reset) must not overwrite a newer one.
  const detailRequestRef = useRef(0);
  // One pin request per session at a time: a double click must not race itself.
  const pinRequestsRef = useRef(new Set<string>());
  // Last loaded detail per session: shown at once on switching while the fresh one loads.
  const detailCacheRef = useRef(new Map<string, ChatSessionDetail>());
  const clientIdRef = useRef("");
  if (!clientIdRef.current) {
    clientIdRef.current = getChatClientId();
  }

  const { startStream, commitRunLocally, resetRunView } =
    useRunStream(clientIdRef.current, {
      // The run's reply becomes a history turn once the detail is loaded; the
      // composer stays locked until then (applySessionDetail releases it). A
      // different run already active (e.g. from another tab) is followed.
      onTerminal: (sessionId, runId) => {
        void loadSession(sessionId, {
          preserveStreamState: true,
          reconnectStream: true,
          onFailure: () => commitRunLocally(runId)
        });
        void loadSessionList(sessionId, { preserveStreamState: true });
      },
      onReset: (sessionId) => {
        void loadSession(sessionId, { preserveStreamState: true, reconnectStream: false });
      },
      onGiveUp: (sessionId, runId) => void giveUpRun(sessionId, runId)
    });

  useEffect(() => {
    void loadSessionList(readStoredActiveSessionId() || undefined);
    void warmupClientLocationCache().then(setClientLocation);
  }, []);


  async function giveUpRun(sessionId: string, runId: string): Promise<void> {
    pushError("实时连接中断，已尝试重连 3 次；正在停止本次请求。");
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
      pushError(err instanceof Error ? err.message : "停止中断会话失败，请稍后重试。");
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
    const run = detail.current_run ?? null;
    const runActive = isRunActive(detail);

    if (!preserveStreamState && !runActive) {
      resetRunView();
    }
    runState().setActiveSubagent(detail.active_subagent || null);
    store.setActiveSessionStatus(detail.status);

    const feed = feedFromDetail(detail);
    if (run && runActive) {
      runState().setAwaitingAssistant(true);
      if (reconnectStream) {
        // Without a cursor the run log replays the whole run, text included,
        // so the open round is rebuilt from the replay.
        feedState().setFeed(withoutOpenRound(feed));
        startStream(sessionId, run.run_id);
      } else {
        // stream.reset: the stream goes on from its cursor; keep what it
        // already showed of the output in progress.
        feedState().setFeed((live) => mergeOpenRound(feed, live));
      }
      return;
    }

    // The history replaces the live items in the same render; keys match, so
    // nothing on screen is remounted.
    feedState().setFeed(feed);
    runState().setActiveRunId(run?.run_id ?? null);
    runState().setAwaitingAssistant(false);
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
        feedState().resetFeed();
        // A background reload leaves the stream to whoever started it.
        if (preserveStreamState) {
          runState().resetRunState();
        } else {
          resetRunView();
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
      pushError(err instanceof Error ? err.message : "加载会话列表失败");
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
      store.setActiveSessionId(sessionId);
      store.setTurnsLoading(true);
    }

    try {
      const detail = await getChatSession(sessionId, clientIdRef.current);
      if (!isLatest()) {
        return null;
      }
      detailCacheRef.current.set(sessionId, detail);
      applySessionDetail(sessionId, detail, { preserveStreamState, reconnectStream });
      return detail;
    } catch (err) {
      if (!isLatest()) {
        return null;
      }
      pushError(err instanceof Error ? err.message : "加载会话失败");
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
    resetRunView();
    const app = appState();
    app.setViewMode("chat");
    app.setInputValue("");
    app.setSidebarOpen(false);
    const store = sessionState();
    store.resetActiveSessionState();
    feedState().resetFeed();
    store.setTurnsLoading(false);
    writeStoredActiveSessionId(null);
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
    const previousRunId = runState().activeRunId;
    const previousFeed = { items: feedState().items, toolStatus: feedState().toolStatus };
    const sessionId = currentState.activeSessionId || makeSessionId();
    const optimisticCreatedAt = new Date().toISOString();

    sessionGenerationRef.current += 1;
    // No run id until dispatch answers; awaitingAssistant shows the run indicator.
    resetRunView();
    feedState().setFeed((feed) => appendUser(feed, message, optimisticCreatedAt));
    runState().setAwaitingAssistant(true);
    currentState.setTurnsLoading(false);
    currentState.setSending(true);
    currentState.setSessions((previous) => {
      const existing = previous.find((item) => item.session_id === sessionId);
      const optimistic = {
        session_id: sessionId,
        title: existing?.title ?? message.replace(/\s+/g, " ").slice(0, 32),
        preview: message.replace(/\s+/g, " ").slice(0, 72),
        intent: existing?.intent ?? "search" as const,
        status: "running" as const,
        turn_count: (existing?.turn_count ?? 0) + 1,
        created_at: existing?.created_at ?? optimisticCreatedAt,
        updated_at: optimisticCreatedAt
      };
      return [optimistic, ...previous.filter((item) => item.session_id !== sessionId)];
    });

    try {
      const location = isNewSession ? await resolveClientLocationForSessionStart() : undefined;
      if (isNewSession) {
        setClientLocation(location ?? null);
      }
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
      appState().setInputValue(message);
      store.setActiveSessionId(previousSessionId);
      writeStoredActiveSessionId(previousSessionId);
      store.setActiveSessionStatus(previousSessionStatus);
      store.setSessions(previousSessions);
      resetRunView();
      runState().setActiveRunId(previousRunId);
      feedState().setFeed(previousFeed);
      pushError(err instanceof Error ? err.message : "发送失败");
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
    // Deleting the open session: show the start panel right away, not after the request.
    const wasActive = currentState.activeSessionId === sessionId;
    if (wasActive) {
      startNewSession();
    }

    try {
      await deleteChatSession(sessionId, clientIdRef.current);
    } catch (err) {
      sessionState().setDeletingSessionId(null);
      window.alert(err instanceof Error ? `删除会话失败：${err.message}` : "删除会话失败，请稍后重试。");
      return;
    }

    // Update from local state instead of re-listing: the list request is the
    // slowest one and nothing else changed.
    detailCacheRef.current.delete(sessionId);
    const store = sessionState();
    store.setSessions(store.sessions.filter((item) => item.session_id !== sessionId));
    store.setDeletingSessionId(null);
  }

  function applySessionPin(sessionId: string, pinnedAt: string | null): void {
    sessionState().setSessions((sessions) =>
      sessions.map((item) => (item.session_id === sessionId ? { ...item, pinned_at: pinnedAt } : item))
    );
  }

  // Pin state is server-owned: update the row at once, then settle with the
  // timestamp the server returns (or roll back and report the failure).
  async function toggleSessionPin(sessionId: string): Promise<void> {
    if (pinRequestsRef.current.has(sessionId)) {
      return;
    }
    const session = sessionState().sessions.find((item) => item.session_id === sessionId);
    if (!session) {
      return;
    }
    const pinned = !session.pinned_at;
    const previousPinnedAt = session.pinned_at ?? null;
    pinRequestsRef.current.add(sessionId);
    applySessionPin(sessionId, pinned ? new Date().toISOString() : null);
    try {
      const updated = await setChatSessionPinned(sessionId, pinned, clientIdRef.current);
      applySessionPin(sessionId, updated.pinned_at ?? null);
    } catch (err) {
      applySessionPin(sessionId, previousPinnedAt);
      pushError(err instanceof Error ? err.message : pinned ? "置顶会话失败" : "取消置顶失败");
    } finally {
      pinRequestsRef.current.delete(sessionId);
    }
  }

  function selectSession(sessionId: string): void {
    sessionGenerationRef.current += 1;
    const app = appState();
    app.setViewMode("chat");
    app.setSidebarOpen(false);
    const store = sessionState();
    const cached = detailCacheRef.current.get(sessionId);
    if (cached && !isRunActive(cached)) {
      // Show the last known content now, then revalidate quietly.
      applySessionDetail(sessionId, cached, { reconnectStream: false });
      void loadSession(sessionId, { preserveStreamState: true });
      return;
    }
    // Respond to the click immediately instead of keeping the old chat on screen.
    store.setActiveSessionId(sessionId);
    feedState().resetFeed();
    resetRunView();
    void loadSession(sessionId);
  }

  function refreshSessions(): void {
    void loadSessionList(sessionState().activeSessionId || undefined);
  }

  return {
    currentCity: clientLocation?.city?.trim() || "南京",
    openChatView,
    openArcadesView,
    startNewSession,
    submitChat,
    quickAsk,
    removeSession,
    selectSession,
    refreshSessions,
    toggleSessionPin
  };
}
