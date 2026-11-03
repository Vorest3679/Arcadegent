import { create } from "zustand";
import type { ChatSessionStatus, ChatSessionSummary } from "../types";
import { resolveUpdater, type Updater } from "./updater";

// Session list and the active session. Its messages live in chatFeedStore,
// the run being followed in runStore.
type SessionStore = {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  activeSessionStatus: ChatSessionStatus | null;
  sessionsLoading: boolean;
  turnsLoading: boolean;
  sending: boolean;
  deletingSessionId: string | null;
  setSessions: (sessions: Updater<ChatSessionSummary[]>) => void;
  setActiveSessionId: (sessionId: string | null) => void;
  setActiveSessionStatus: (status: ChatSessionStatus | null) => void;
  setSessionsLoading: (loading: boolean) => void;
  setTurnsLoading: (loading: boolean) => void;
  setSending: (sending: boolean) => void;
  setDeletingSessionId: (sessionId: string | null) => void;
  resetActiveSessionState: () => void;
};

export const useSessionStore = create<SessionStore>((set) => ({
  sessions: [],
  activeSessionId: null,
  activeSessionStatus: null,
  sessionsLoading: false,
  turnsLoading: false,
  sending: false,
  deletingSessionId: null,
  setSessions: (sessions) => set((state) => ({ sessions: resolveUpdater(sessions, state.sessions) })),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
  setActiveSessionStatus: (activeSessionStatus) => set({ activeSessionStatus }),
  setSessionsLoading: (sessionsLoading) => set({ sessionsLoading }),
  setTurnsLoading: (turnsLoading) => set({ turnsLoading }),
  setSending: (sending) => set({ sending }),
  setDeletingSessionId: (deletingSessionId) => set({ deletingSessionId }),
  resetActiveSessionState: () => set({ activeSessionId: null, activeSessionStatus: null })
}));
