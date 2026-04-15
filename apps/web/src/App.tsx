import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildChatStreamUrl,
  dispatchChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions
} from "./api/client";
import { AppSidebar } from "./components/AppSidebar";
import { ArcadeBrowser } from "./components/ArcadeBrowser";
import { ChatPanel } from "./components/ChatPanel";
import { AppTopbar } from "./components/AppTopbar";
import { useStreamReply } from "./hooks/useStreamReply";
import { resolveClientLocationForSessionStart, warmupClientLocationCache } from "./lib/clientLocation";
import { STREAM_EVENT_NAMES, toProgressText, toVisibleTurns, type StreamProgressItem } from "./lib/chatStream";
import type {
  ChatHistoryTurn,
  ChatMapArtifacts,
  ChatSessionDetail,
  ChatSessionStatus,
  ChatSessionSummary,
  ChatStreamEnvelope,
  RouteSummary
} from "./types";

type ViewMode = "chat" | "arcades";

function normalizeViewMode(raw: string | null): ViewMode {
  return raw === "arcades" ? "arcades" : "chat";
}

function readInitialViewMode(): ViewMode {
  if (typeof window === "undefined") {
    return "chat";
  }
  return normalizeViewMode(new URLSearchParams(window.location.search).get("view"));
}

// 同步 viewMode 到 URL，保持在刷新页面时能够恢复到之前的视图模式，同时也方便用户在不同视图模式之间切换时能够通过浏览器的前进后退按钮进行导航。
function syncViewModeInUrl(viewMode: ViewMode) {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  if (viewMode === "chat") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", viewMode);
  }
  const nextHref = `${url.pathname}${url.search}${url.hash}`;
  const currentHref = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextHref !== currentHref) {
    window.history.replaceState({}, "", nextHref);
  }
}

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

export function App() {
  const [viewMode, setViewMode] = useState<ViewMode>(() => readInitialViewMode());
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeSessionStatus, setActiveSessionStatus] = useState<ChatSessionStatus | null>(null);
  const [turns, setTurns] = useState<ChatHistoryTurn[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [turnsLoading, setTurnsLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [chatError, setChatError] = useState("");
  const [streamConnected, setStreamConnected] = useState(false);
  const [activeSubagent, setActiveSubagent] = useState<string | null>(null);
  const [streamItems, setStreamItems] = useState<StreamProgressItem[]>([]);
  const [awaitingAssistant, setAwaitingAssistant] = useState(false);
  const [activeMapArtifacts, setActiveMapArtifacts] = useState<ChatMapArtifacts | null>(null);

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

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeSessionId) ?? null,
    [sessions, activeSessionId]
  );

  const stopStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    setStreamConnected(false);
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
      setAwaitingAssistant(false);
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

  const pushStreamEnvelope = useCallback((envelope: ChatStreamEnvelope) => {
    setStreamItems(() => [
      {
        id: envelope.id,
        event: envelope.event,
        text: toProgressText(envelope),
        at: envelope.at
      }
    ]);
  }, []);

  const commitStreamReply = useCallback((reply: string) => {
    const normalized = reply.trim();
    if (!normalized) {
      return;
    }

    setTurns((previous) => {
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
  }, []);

  const startStream = useCallback(
    (sessionId: string) => {
      stopStream();
      setStreamItems([]);
      setActiveSubagent(null);
      setActiveSessionStatus("running");
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

        if (envelope.event === "session.started") {
          setActiveSessionStatus("running");
          const current = envelope.data.active_subagent;
          if (typeof current === "string" && current) {
            setActiveSubagent(current);
          }
        }

        if (envelope.event === "subagent.changed") {
          const next = envelope.data.to_subagent ?? envelope.data.active_subagent;
          if (typeof next === "string" && next) {
            setActiveSubagent(next);
          }
        }

        if (envelope.event === "assistant.token") {
          applyStreamToken(envelope.data);
        }

        if (envelope.event === "navigation.route_ready") {
          const route = coerceStreamRoute(envelope.data);
          if (route) {
            setActiveMapArtifacts((previous) => ({
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
          setActiveSessionStatus("completed");
          const reply = envelope.data.reply;
          if (typeof reply === "string" && reply) {
            if (reply.length >= getStreamReplyTarget().length) {
              writeStreamReplyTarget(reply);
            }
            commitStreamReply(reply);
          }
        }

        if (envelope.event === "session.failed") {
          setActiveSessionStatus("failed");
          const error = envelope.data.error;
          setChatError(typeof error === "string" && error.trim() ? error : "会话执行失败");
        }

        pushStreamEnvelope(envelope);

        if (envelope.event === "assistant.completed" || envelope.event === "session.failed") {
          setAwaitingAssistant(false);
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
        setStreamConnected(true);
        setActiveSessionStatus("running");
      };

      source.onerror = () => {
        if (streamRef.current !== source) {
          return;
        }
        setStreamConnected(false);
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
    },
    [
      applyStreamToken,
      commitStreamReply,
      getStreamReplyTarget,
      pushStreamEnvelope,
      resetStreamReply,
      stopStream,
      writeStreamReplyTarget
    ]
  );

  const applySessionDetail = useCallback(
    (
      sessionId: string,
      detail: ChatSessionDetail,
      options?: { preserveStreamState?: boolean; reconnectStream?: boolean }
    ) => {
      const preserveStreamState = options?.preserveStreamState ?? false;
      const reconnectStream = options?.reconnectStream ?? true;

      setActiveSessionId(sessionId);
      setTurns(toVisibleTurns(detail.turns));
      setActiveSubagent(detail.active_subagent || null);
      setActiveSessionStatus(detail.status);
      setActiveMapArtifacts(mapArtifactsFromSession(detail));

      if (!preserveStreamState) {
        setStreamItems([]);
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
        setChatError(detail.last_error?.trim() ? detail.last_error : "会话执行失败");
      } else {
        setChatError("");
      }

      if (detail.status === "running") {
        setAwaitingAssistant(true);
        if (reconnectStream) {
          startStream(sessionId);
        }
        return;
      }

      setAwaitingAssistant(false);
      if (!preserveStreamState) {
        stopStream();
      }
    },
    [getStreamReplyTarget, resetStreamReply, startStream, stopStream, syncStreamReply, writeStreamReplyTarget]
  );

  async function loadSessionList(
    preferredSessionId?: string,
    options?: { preserveStreamState?: boolean }
  ) {
    const preserveStreamState = options?.preserveStreamState ?? false;
    setSessionsLoading(true);

    try {
      const rows = await listChatSessions(60);
      setSessions(rows);

      if (!rows.length) {
        setActiveSessionId(null);
        setActiveSessionStatus(null);
        setTurns([]);
        setActiveSubagent(null);
        setActiveMapArtifacts(null);
        if (!preserveStreamState) {
          stopStream();
          setStreamItems([]);
          resetStreamReply();
          setAwaitingAssistant(false);
        }
        return;
      }

      const hasPreferred = preferredSessionId ? rows.some((item) => item.session_id === preferredSessionId) : false;
      const hasActive = activeSessionId ? rows.some((item) => item.session_id === activeSessionId) : false;
      const targetId = hasPreferred
        ? preferredSessionId
        : hasActive
          ? activeSessionId
          : activeSessionId && activeSessionStatus === "running"
            ? null
            : rows[0].session_id;

      if (targetId && targetId !== activeSessionId) {
        await loadSession(targetId, { preserveStreamState, reconnectStream: true });
      }
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "加载会话列表失败");
    } finally {
      setSessionsLoading(false);
    }
  }

  async function loadSession(
    sessionId: string,
    options?: { preserveStreamState?: boolean; reconnectStream?: boolean }
  ): Promise<ChatSessionDetail | null> {
    const preserveStreamState = options?.preserveStreamState ?? false;
    const reconnectStream = options?.reconnectStream ?? true;
    setTurnsLoading(true);
    setChatError("");

    try {
      const detail = await getChatSession(sessionId);
      applySessionDetail(sessionId, detail, { preserveStreamState, reconnectStream });
      return detail;
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "加载会话失败");
      return null;
    } finally {
      setTurnsLoading(false);
    }
  }

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

  function openChatView() {
    setViewMode("chat");
    setSidebarOpen(false);
  }

  function openArcadesView() {
    setViewMode("arcades");
    setSidebarOpen(false);
  }

  function startNewSession() {
    stopStream();
    setViewMode("chat");
    setActiveSessionId(null);
    setActiveSessionStatus(null);
    setTurns([]);
    setInputValue("");
    setChatError("");
    setSidebarOpen(false);
    setActiveSubagent(null);
    setStreamItems([]);
    setActiveMapArtifacts(null);
    resetStreamReply();
    setAwaitingAssistant(false);
  }

  async function submitChat(event: FormEvent) {
    event.preventDefault();
    const message = inputValue.trim();
    if (!message || sending || awaitingAssistant) {
      return;
    }

    const isNewSession = !activeSessionId;
    const previousSessionId = activeSessionId;
    const previousSessionStatus = activeSessionStatus;
    const sessionId = activeSessionId || makeSessionId();
    const optimisticCreatedAt = new Date().toISOString();

    setSending(true);
    setChatError("");

    try {
      const location = isNewSession ? await resolveClientLocationForSessionStart() : undefined;

      setInputValue("");
      setActiveSessionId(sessionId);
      setActiveSessionStatus("running");
      setActiveMapArtifacts(null);
      setAwaitingAssistant(true);
      setTurns((previous) => [...previous, { role: "user", content: message, created_at: optimisticCreatedAt }]);

      const dispatched = await dispatchChatSession({
        session_id: sessionId,
        message,
        location: location ?? undefined,
        page_size: 5
      });
      setActiveSessionId(dispatched.session_id);
      setActiveSessionStatus(dispatched.status);
      startStream(dispatched.session_id);
      await loadSessionList(dispatched.session_id, { preserveStreamState: true });
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "发送失败");
      setInputValue(message);
      setActiveSessionId(previousSessionId);
      setActiveSessionStatus(previousSessionStatus);
      setTurns((previous) => {
        const next = [...previous];
        const last = next[next.length - 1];
        if (last && last.role === "user" && last.content === message && last.created_at === optimisticCreatedAt) {
          next.pop();
        }
        return next;
      });
      setStreamItems([]);
      setActiveSubagent(null);
      setActiveMapArtifacts(null);
      resetStreamReply();
      setAwaitingAssistant(false);
      stopStream();
    } finally {
      setSending(false);
    }
  }

  function quickAsk(prompt: string) {
    setInputValue(prompt);
    setViewMode("chat");
    setSidebarOpen(false);
  }

  async function removeSession(sessionId: string) {
    if (deletingSessionId || sending) {
      return;
    }

    const ok = window.confirm("确认删除这个历史会话吗？");
    if (!ok) {
      return;
    }

    setDeletingSessionId(sessionId);
    setChatError("");

    try {
      await deleteChatSession(sessionId);
      const isActive = activeSessionId === sessionId;

      if (isActive) {
        setActiveSessionId(null);
        setActiveSessionStatus(null);
        setTurns([]);
        setActiveSubagent(null);
        setStreamItems([]);
        setActiveMapArtifacts(null);
        resetStreamReply();
        setAwaitingAssistant(false);
      }

      await loadSessionList(isActive ? undefined : activeSessionId || undefined);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "删除会话失败");
    } finally {
      setDeletingSessionId(null);
    }
  }

  return (
    <div className="app-shell">
      <AppSidebar
        sidebarOpen={sidebarOpen}
        viewMode={viewMode}
        sessions={sessions}
        activeSessionId={activeSessionId}
        sessionsLoading={sessionsLoading}
        deletingSessionId={deletingSessionId}
        onStartNewSession={startNewSession}
        onOpenChatView={openChatView}
        onOpenArcadesView={openArcadesView}
        onRefresh={() => void loadSessionList(activeSessionId || undefined)}
        onSelectSession={(sessionId) => {
          stopStream();
          setViewMode("chat");
          void loadSession(sessionId);
          setSidebarOpen(false);
        }}
        onDeleteSession={(sessionId) => void removeSession(sessionId)}
      />

      <button
        type="button"
        className={`sidebar-backdrop ${sidebarOpen ? "is-open" : ""}`}
        aria-label="关闭侧边栏"
        onClick={() => setSidebarOpen(false)}
      />

      <main className="app-main">
        <AppTopbar
          viewMode={viewMode}
          activeSessionUpdatedAt={activeSession?.updated_at ?? null}
          onToggleSidebar={() => setSidebarOpen((value) => !value)}
        />

        {viewMode === "chat" ? (
          <ChatPanel
            turns={turns}
            loading={turnsLoading}
            sending={sending}
            inputValue={inputValue}
            onInputChange={setInputValue}
            onSubmit={submitChat}
            onQuickAsk={quickAsk}
            error={chatError}
            streamConnected={streamConnected}
            activeSubagent={activeSubagent}
            streamItems={streamItems}
            streamReplyTarget={streamReplyTarget}
            streamReply={streamReplyDisplay}
            streamReplyActive={
              sending ||
              streamConnected ||
              awaitingAssistant ||
              streamReplyDisplay.length < streamReplyTarget.length
            }
            awaitingAssistant={awaitingAssistant}
            mapArtifacts={activeMapArtifacts}
          />
        ) : (
          <ArcadeBrowser />
        )}
      </main>
    </div>
  );
}
