import { create } from "zustand";
import type { StreamProgressItem } from "../lib/chatStream";
import { readInitialViewMode, syncViewModeInUrl } from "../lib/viewMode";
import type {
  ChatHistoryTurn,
  ChatMapArtifacts,
  ChatSessionStatus,
  ChatSessionSummary,
  ViewMode
} from "../types";

type Updater<T> = T | ((previous: T) => T);

function resolveUpdater<T>(next: Updater<T>, previous: T): T {
  return typeof next === "function" ? (next as (value: T) => T)(previous) : next;
}

type AppStore = {
  viewMode: ViewMode;
  sidebarOpen: boolean;
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  activeSessionStatus: ChatSessionStatus | null;
  turns: ChatHistoryTurn[];
  sessionsLoading: boolean;
  turnsLoading: boolean;
  sending: boolean;
  deletingSessionId: string | null;
  inputValue: string;
  chatError: string;
  streamConnected: boolean;
  activeSubagent: string | null;
  streamItems: StreamProgressItem[];
  awaitingAssistant: boolean;
  activeMapArtifacts: ChatMapArtifacts | null;
  setViewMode: (viewMode: ViewMode, options?: { replace?: boolean; syncUrl?: boolean }) => void;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setSessions: (sessions: ChatSessionSummary[]) => void;
  setActiveSessionId: (sessionId: string | null) => void;
  setActiveSessionStatus: (status: ChatSessionStatus | null) => void;
  setTurns: (turns: Updater<ChatHistoryTurn[]>) => void;
  setSessionsLoading: (loading: boolean) => void;
  setTurnsLoading: (loading: boolean) => void;
  setSending: (sending: boolean) => void;
  setDeletingSessionId: (sessionId: string | null) => void;
  setInputValue: (value: string) => void;
  setChatError: (error: string) => void;
  setStreamConnected: (connected: boolean) => void;
  setActiveSubagent: (subagent: string | null) => void;
  setStreamItems: (items: Updater<StreamProgressItem[]>) => void;
  setAwaitingAssistant: (awaiting: boolean) => void;
  setActiveMapArtifacts: (artifacts: Updater<ChatMapArtifacts | null>) => void;
  resetActiveSessionState: () => void;
};

export const useAppStore = create<AppStore>((set) => ({
  viewMode: readInitialViewMode(),
  sidebarOpen: false,
  sessions: [],
  activeSessionId: null,
  activeSessionStatus: null,
  turns: [],
  sessionsLoading: false,
  turnsLoading: false,
  sending: false,
  deletingSessionId: null,
  inputValue: "",
  chatError: "",
  streamConnected: false,
  activeSubagent: null,
  streamItems: [],
  awaitingAssistant: false,
  activeMapArtifacts: null,
  setViewMode: (viewMode, options = {}) => {
    if (options.syncUrl !== false) {
      syncViewModeInUrl(viewMode, { replace: options.replace });
    }
    set({ viewMode });
  },
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSessions: (sessions) => set({ sessions }),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
  setActiveSessionStatus: (activeSessionStatus) => set({ activeSessionStatus }),
  setTurns: (turns) => set((state) => ({ turns: resolveUpdater(turns, state.turns) })),
  setSessionsLoading: (sessionsLoading) => set({ sessionsLoading }),
  setTurnsLoading: (turnsLoading) => set({ turnsLoading }),
  setSending: (sending) => set({ sending }),
  setDeletingSessionId: (deletingSessionId) => set({ deletingSessionId }),
  setInputValue: (inputValue) => set({ inputValue }),
  setChatError: (chatError) => set({ chatError }),
  setStreamConnected: (streamConnected) => set({ streamConnected }),
  setActiveSubagent: (activeSubagent) => set({ activeSubagent }),
  setStreamItems: (streamItems) => set((state) => ({
    streamItems: resolveUpdater(streamItems, state.streamItems)
  })),
  setAwaitingAssistant: (awaitingAssistant) => set({ awaitingAssistant }),
  setActiveMapArtifacts: (activeMapArtifacts) => set((state) => ({
    activeMapArtifacts: resolveUpdater(activeMapArtifacts, state.activeMapArtifacts)
  })),
  resetActiveSessionState: () => set({
    activeSessionId: null,
    activeSessionStatus: null,
    turns: [],
    activeSubagent: null,
    activeMapArtifacts: null,
    awaitingAssistant: false
  })
}));
