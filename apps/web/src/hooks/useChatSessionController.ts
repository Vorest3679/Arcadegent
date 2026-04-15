import { FormEvent, useCallback, useEffect, useRef } from "react";
import {
  buildChatStreamUrl,
  deleteChatSession,
  dispatchChatSession,
  getChatSession,
  listChatSessions
} from "../api/client";
import { resolveClientLocationForSessionStart, warmupClientLocationCache } from "../lib/clientLocation";
import { STREAM_EVENT_NAMES, toProgressText, toVisibleTurns } from "../lib/chatStream";
import { syncViewModeInUrl } from "../lib/viewMode";
import { useAppStore } from "../stores/appStore";
import type {
  ChatMapArtifacts,
  ChatSessionDetail,
  ChatStreamEnvelope,
  RouteSummary
} from "../types";
import { useStreamReply } from "./useStreamReply";

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

function coerceStreamRoute(data: Record<string, unknown>): RouteSummary | null {
  const provider = data.provider;
  const mode = data.mode;
  if (provider !== "amap" && provider !== "google" && provider !== "none") {
    return null;
  }
  if (typeof mode !== "string" || !mode.trim()) {
    return null;
  }
  return {
    provider,
    mode,
    distance_m: typeof data.distance_m === "number" ? data.distance_m : null,
    duration_s: typeof data.duration_s === "number" ? data.duration_s : null,
    origin: typeof data.origin === "object" && data.origin !== null ? data.origin as RouteSummary["origin"] : null,
    destination:
      typeof data.destination === "object" && data.destination !== null
        ? data.destination as RouteSummary["destination"]
        : null,
    polyline: Array.isArray(data.polyline) ? data.polyline as RouteSummary["polyline"] : [],
    hint: typeof data.hint === "string" ? data.hint : null
  };
}

export function useChatSessionController() {
  const viewMode = useAppStore((state) => state.viewMode);
  const turns = useAppStore((state) => state.turns);
  const sending = useAppStore((state) => state.sending);
  const streamConnected = useAppStore((state) => state.streamConnected);
  const awaitingAssistant = useAppStore((state) => state.awaitingAssistant);

  const {
    applyStreamToken,
    cancelStreamReplyFlush,
    getStreamReplyTarget,
    resetStreamReply,
    streamReplyDisplay,
    streamReplyTarget,
    syncStreamReply,
    writeStreamReplyTarget
  } = useStreamReply();

  const streamRef = useRef<EventSource | null>(null);

  const stopStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    useAppStore.getState().setStreamConnected(false);
  }, []);

  useEffect(() => {
    if (!awaitingAssistant) {
      return;
    }

    const hasStreamReply = streamReplyDisplay.trim().length > 0;
    const last = turns[turns.length - 1];
    const hasAssistantTurn = last?.role === "assistant" && last.content.trim().length > 0;
    const streamReplySettled = streamReplyDisplay === streamReplyTarget;

    if (!sending && !streamConnected && streamReplySettled && (hasStreamReply || hasAssistantTurn)) {
      useAppStore.getState().setAwaitingAssistant(false);
    }
  }, [awaitingAssistant, sending, streamConnected, streamReplyDisplay, streamReplyTarget, turns]);

  useEffect(() => {
    if (!awaitingAssistant || streamReplyTarget.trim()) {
      return;
    }

    const last = turns[turns.length - 1];
    if (last?.role === "assistant" && last.content.trim() && last.content.length > getStreamReplyTarget().length) {
      writeStreamReplyTarget(last.content);
    }
  }, [awaitingAssistant, getStreamReplyTarget, streamReplyTarget, turns, writeStreamReplyTarget]);

  useEffect(() => {
    void loadSessionList();
    void warmupClientLocationCache();
  }, []);

  useEffect(() => {
    syncViewModeInUrl(viewMode);
  }, [viewMode]);

  useEffect(() => {
    return () => {
      cancelStreamReplyFlush();
      stopStream();
    };
  }, [cancelStreamReplyFlush, stopStream]);

  function pushStreamEnvelope(envelope: ChatStreamEnvelope): void {
    useAppStore.getState().setStreamItems([
      {
        id: envelope.id,
        event: envelope.event,
        text: toProgressText(envelope),
        at: envelope.at
      }
    ]);
  }

  function commitStreamReply(reply: string): void {
    const normalized = reply.trim();
    if (!normalized) {
      return;
    }

    useAppStore.getState().setTurns((previous) => {
      const next = [...previous];
      const last = next[next.length - 1];

      if (last?.role === "assistant") {
        if (last.content === normalized) {
          return previous;
        }
        if (normalized.startsWith(last.content) || last.content.startsWith(normalized)) {
          next[next.length - 1] = {
            ...last,
            content: normalized
          };
          return next;
        }
      }

      next.push({
        role: "assistant",
        content: normalized,
        created_at: new Date().toISOString()
      });
      return next;
    });
  }

  function startStream(sessionId: string): void {
    stopStream();
    const store = useAppStore.getState();
    store.setStreamItems([]);
    store.setActiveSubagent(null);
    store.setActiveSessionStatus("running");
    resetStreamReply();

    const source = new EventSource(buildChatStreamUrl(sessionId));
    streamRef.current = source;

    const handleEvent = (raw: Event) => {
      if (streamRef.current !== source) {
        return;
      }

      const message = raw as MessageEvent<string>;
      if (!message.data) {
        return;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(message.data);
      } catch {
        return;
      }

      if (!parsed || typeof parsed !== "object") {
        return;
      }

      const envelope = parsed as ChatStreamEnvelope;
      if (typeof envelope.id !== "number" || typeof envelope.event !== "string") {
        return;
      }
      if (typeof envelope.data !== "object" || envelope.data === null) {
        return;
      }

      const currentStore = useAppStore.getState();

      if (envelope.event === "session.started") {
        currentStore.setActiveSessionStatus("running");
        const current = envelope.data.active_subagent;
        if (typeof current === "string" && current) {
          currentStore.setActiveSubagent(current);
        }
      }

      if (envelope.event === "subagent.changed") {
        const next = envelope.data.to_subagent ?? envelope.data.active_subagent;
        if (typeof next === "string" && next) {
          currentStore.setActiveSubagent(next);
        }
      }

      if (envelope.event === "assistant.token") {
        applyStreamToken(envelope.data);
      }

      if (envelope.event === "navigation.route_ready") {
        const route = coerceStreamRoute(envelope.data);
        if (route) {
          currentStore.setActiveMapArtifacts((previous) => ({
            shops: previous?.shops ?? [],
            route,
            client_location: previous?.client_location ?? null,
            destination: previous?.destination ?? null,
            view_payload: previous?.view_payload ?? { version: 1, scene: "agent_route" },
            route_pending: true
          }));
        }
      }

      if (envelope.event === "assistant.completed") {
        currentStore.setActiveSessionStatus("completed");
        const reply = envelope.data.reply;
        if (typeof reply === "string" && reply) {
          if (reply.length >= getStreamReplyTarget().length) {
            writeStreamReplyTarget(reply);
          }
          commitStreamReply(reply);
        }
      }

      if (envelope.event === "session.failed") {
        currentStore.setActiveSessionStatus("failed");
        const error = envelope.data.error;
        currentStore.setChatError(typeof error === "string" && error.trim() ? error : "会话执行失败");
      }

      pushStreamEnvelope(envelope);

      if (envelope.event === "assistant.completed" || envelope.event === "session.failed") {
        currentStore.setAwaitingAssistant(false);
        stopStream();
        void loadSession(sessionId, {
          preserveStreamState: true,
          reconnectStream: false
        });
        void loadSessionList(sessionId, { preserveStreamState: true });
      }
    };

    source.onopen = () => {
      if (streamRef.current !== source) {
        return;
      }
      const currentStore = useAppStore.getState();
      currentStore.setStreamConnected(true);
      currentStore.setActiveSessionStatus("running");
    };

    source.onerror = () => {
      if (streamRef.current !== source) {
        return;
      }
      useAppStore.getState().setStreamConnected(false);
      if (source.readyState === EventSource.CLOSED) {
        stopStream();
        void loadSession(sessionId, {
          preserveStreamState: true,
          reconnectStream: false
        });
      }
    };

    STREAM_EVENT_NAMES.forEach((eventName) => {
      source.addEventListener(eventName, handleEvent as EventListener);
    });
  }

  function applySessionDetail(
    sessionId: string,
    detail: ChatSessionDetail,
    options?: { preserveStreamState?: boolean; reconnectStream?: boolean }
  ): void {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const reconnectStream = options?.reconnectStream ?? true;
    const store = useAppStore.getState();

    store.setActiveSessionId(sessionId);
    store.setTurns(toVisibleTurns(detail.turns));
    store.setActiveSubagent(detail.active_subagent || null);
    store.setActiveSessionStatus(detail.status);
    store.setActiveMapArtifacts(mapArtifactsFromSession(detail));

    if (!preserveStreamState) {
      store.setStreamItems([]);
      resetStreamReply();
    }

    if (detail.reply && detail.reply.trim() && detail.reply.length > getStreamReplyTarget().length) {
      if (detail.status === "running") {
        writeStreamReplyTarget(detail.reply);
      } else {
        syncStreamReply(detail.reply);
      }
    }

    if (detail.status === "failed") {
      store.setChatError(detail.last_error?.trim() ? detail.last_error : "会话执行失败");
    } else {
      store.setChatError("");
    }

    if (detail.status === "running") {
      store.setAwaitingAssistant(true);
      if (reconnectStream) {
        startStream(sessionId);
      }
      return;
    }

    store.setAwaitingAssistant(false);
    if (!preserveStreamState) {
      stopStream();
    }
  }

  async function loadSessionList(
    preferredSessionId?: string,
    options?: { preserveStreamState?: boolean }
  ): Promise<void> {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const store = useAppStore.getState();
    store.setSessionsLoading(true);

    try {
      const rows = await listChatSessions(60);
      const latestStore = useAppStore.getState();
      latestStore.setSessions(rows);

      if (!rows.length) {
        latestStore.setActiveSessionId(null);
        latestStore.setActiveSessionStatus(null);
        latestStore.setTurns([]);
        latestStore.setActiveSubagent(null);
        latestStore.setActiveMapArtifacts(null);
        if (!preserveStreamState) {
          stopStream();
          latestStore.setStreamItems([]);
          resetStreamReply();
          latestStore.setAwaitingAssistant(false);
        }
        return;
      }

      const currentActiveSessionId = latestStore.activeSessionId;
      const currentActiveStatus = latestStore.activeSessionStatus;
      const hasPreferred = preferredSessionId ? rows.some((item) => item.session_id === preferredSessionId) : false;
      const hasActive = currentActiveSessionId
        ? rows.some((item) => item.session_id === currentActiveSessionId)
        : false;
      const targetId = hasPreferred
        ? preferredSessionId
        : hasActive
          ? currentActiveSessionId
          : currentActiveSessionId && currentActiveStatus === "running"
            ? null
            : rows[0].session_id;

      if (targetId && targetId !== currentActiveSessionId) {
        await loadSession(targetId, { preserveStreamState, reconnectStream: true });
      }
    } catch (err) {
      useAppStore.getState().setChatError(err instanceof Error ? err.message : "加载会话列表失败");
    } finally {
      useAppStore.getState().setSessionsLoading(false);
    }
  }

  async function loadSession(
    sessionId: string,
    options?: { preserveStreamState?: boolean; reconnectStream?: boolean }
  ): Promise<ChatSessionDetail | null> {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const reconnectStream = options?.reconnectStream ?? true;
    const store = useAppStore.getState();
    store.setTurnsLoading(true);
    store.setChatError("");

    try {
      const detail = await getChatSession(sessionId);
      applySessionDetail(sessionId, detail, { preserveStreamState, reconnectStream });
      return detail;
    } catch (err) {
      useAppStore.getState().setChatError(err instanceof Error ? err.message : "加载会话失败");
      return null;
    } finally {
      useAppStore.getState().setTurnsLoading(false);
    }
  }

  function openChatView(): void {
    const store = useAppStore.getState();
    store.setViewMode("chat");
    store.setSidebarOpen(false);
  }

  function openArcadesView(): void {
    const store = useAppStore.getState();
    store.setViewMode("arcades");
    store.setSidebarOpen(false);
  }

  function startNewSession(): void {
    stopStream();
    const store = useAppStore.getState();
    store.setViewMode("chat");
    store.resetActiveSessionState();
    store.setInputValue("");
    store.setChatError("");
    store.setSidebarOpen(false);
    store.setStreamItems([]);
    resetStreamReply();
  }

  async function submitChat(event: FormEvent): Promise<void> {
    event.preventDefault();
    const currentState = useAppStore.getState();
    const message = currentState.inputValue.trim();
    if (!message || currentState.sending || currentState.awaitingAssistant) {
      return;
    }

    const isNewSession = !currentState.activeSessionId;
    const previousSessionId = currentState.activeSessionId;
    const previousSessionStatus = currentState.activeSessionStatus;
    const sessionId = currentState.activeSessionId || makeSessionId();
    const optimisticCreatedAt = new Date().toISOString();

    currentState.setSending(true);
    currentState.setChatError("");

    try {
      const location = isNewSession ? await resolveClientLocationForSessionStart() : undefined;
      const store = useAppStore.getState();

      store.setInputValue("");
      store.setActiveSessionId(sessionId);
      store.setActiveSessionStatus("running");
      store.setActiveMapArtifacts(null);
      store.setAwaitingAssistant(true);
      store.setTurns((previous) => [...previous, { role: "user", content: message, created_at: optimisticCreatedAt }]);

      const dispatched = await dispatchChatSession({
        session_id: sessionId,
        message,
        location: location ?? undefined,
        page_size: 5
      });
      const latestStore = useAppStore.getState();
      latestStore.setActiveSessionId(dispatched.session_id);
      latestStore.setActiveSessionStatus(dispatched.status);
      startStream(dispatched.session_id);
      await loadSessionList(dispatched.session_id, { preserveStreamState: true });
    } catch (err) {
      const store = useAppStore.getState();
      store.setChatError(err instanceof Error ? err.message : "发送失败");
      store.setInputValue(message);
      store.setActiveSessionId(previousSessionId);
      store.setActiveSessionStatus(previousSessionStatus);
      store.setTurns((previous) => {
        const next = [...previous];
        const last = next[next.length - 1];
        if (last && last.role === "user" && last.content === message && last.created_at === optimisticCreatedAt) {
          next.pop();
        }
        return next;
      });
      store.setStreamItems([]);
      store.setActiveSubagent(null);
      store.setActiveMapArtifacts(null);
      resetStreamReply();
      store.setAwaitingAssistant(false);
      stopStream();
    } finally {
      useAppStore.getState().setSending(false);
    }
  }

  function quickAsk(prompt: string): void {
    const store = useAppStore.getState();
    store.setInputValue(prompt);
    store.setViewMode("chat");
    store.setSidebarOpen(false);
  }

  async function removeSession(sessionId: string): Promise<void> {
    const currentState = useAppStore.getState();
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
      await deleteChatSession(sessionId);
      const store = useAppStore.getState();
      const isActive = store.activeSessionId === sessionId;

      if (isActive) {
        store.resetActiveSessionState();
        store.setStreamItems([]);
        resetStreamReply();
      }

      await loadSessionList(isActive ? undefined : store.activeSessionId || undefined);
    } catch (err) {
      useAppStore.getState().setChatError(err instanceof Error ? err.message : "删除会话失败");
    } finally {
      useAppStore.getState().setDeletingSessionId(null);
    }
  }

  function selectSession(sessionId: string): void {
    stopStream();
    const store = useAppStore.getState();
    store.setViewMode("chat");
    void loadSession(sessionId);
    store.setSidebarOpen(false);
  }

  function refreshSessions(): void {
    void loadSessionList(useAppStore.getState().activeSessionId || undefined);
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
    streamReplyTarget,
    streamReply: streamReplyDisplay,
    streamReplyActive:
      sending ||
      streamConnected ||
      awaitingAssistant ||
      streamReplyDisplay.length < streamReplyTarget.length
  };
}
