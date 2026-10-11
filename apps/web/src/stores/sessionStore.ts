import { create } from "zustand";
import type { ChatHistoryTurn, ChatSessionStatus, ChatSessionSummary } from "../types";
import { resolveUpdater, type Updater } from "./updater";

// Session list, the active session and its history. The run being followed
// lives in runStore.
type SessionStore = {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  activeSessionStatus: ChatSessionStatus | null;
  turns: ChatHistoryTurn[];
  sessionsLoading: boolean;
  turnsLoading: boolean;
  sending: boolean;
  deletingSessionId: string | null;
  chatError: string;
  setSessions: (sessions: Updater<ChatSessionSummary[]>) => void;
  setActiveSessionId: (sessionId: string | null) => void;
  setActiveSessionStatus: (status: ChatSessionStatus | null) => void;
  setTurns: (turns: Updater<ChatHistoryTurn[]>) => void;
  setSessionsLoading: (loading: boolean) => void;
  setTurnsLoading: (loading: boolean) => void;
  setSending: (sending: boolean) => void;
  setDeletingSessionId: (sessionId: string | null) => void;
  setChatError: (error: string) => void;
  resetActiveSessionState: () => void;
};

export const useSessionStore = create<SessionStore>((set) => ({
  sessions: [],
  activeSessionId: null,
  activeSessionStatus: null,
  turns: [],
  sessionsLoading: false,
  turnsLoading: false,
  sending: false,
  deletingSessionId: null,
  chatError: "",
  setSessions: (sessions) => set((state) => ({ sessions: resolveUpdater(sessions, state.sessions) })),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
  setActiveSessionStatus: (activeSessionStatus) => set({ activeSessionStatus }),
  setTurns: (turns) => set((state) => ({ turns: resolveUpdater(turns, state.turns) })),
  setSessionsLoading: (sessionsLoading) => set({ sessionsLoading }),
  setTurnsLoading: (turnsLoading) => set({ turnsLoading }),
  setSending: (sending) => set({ sending }),
  setDeletingSessionId: (deletingSessionId) => set({ deletingSessionId }),
  setChatError: (chatError) => set({ chatError }),
  resetActiveSessionState: () => set({ activeSessionId: null, activeSessionStatus: null, turns: [] })
}));
