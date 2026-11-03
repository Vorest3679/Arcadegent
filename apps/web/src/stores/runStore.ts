import { create } from "zustand";

// Transport state of the agent run the chat view follows. What the run
// produced lives in chatFeedStore; session list and status in sessionStore.
type RunStore = {
  streamConnected: boolean;
  activeSubagent: string | null;
  // From send until the run's detail is loaded: the composer stays locked.
  awaitingAssistant: boolean;
  activeRunId: string | null;
  // Local transport notice (e.g. reconnecting); null when there is none.
  streamNotice: string | null;
  // The followed run's stream is open: it has not reached a terminal state
  // and has not been given up. Tool groups of its round stay expanded.
  runOpen: boolean;
  setStreamConnected: (connected: boolean) => void;
  setActiveSubagent: (subagent: string | null) => void;
  setAwaitingAssistant: (awaiting: boolean) => void;
  setActiveRunId: (runId: string | null) => void;
  setStreamNotice: (notice: string | null) => void;
  setRunOpen: (open: boolean) => void;
  resetRunState: () => void;
};

export const useRunStore = create<RunStore>((set) => ({
  streamConnected: false,
  activeSubagent: null,
  awaitingAssistant: false,
  activeRunId: null,
  streamNotice: null,
  runOpen: false,
  setStreamConnected: (streamConnected) => set({ streamConnected }),
  setActiveSubagent: (activeSubagent) => set({ activeSubagent }),
  setAwaitingAssistant: (awaitingAssistant) => set({ awaitingAssistant }),
  setActiveRunId: (activeRunId) => set({ activeRunId }),
  setStreamNotice: (streamNotice) => set({ streamNotice }),
  setRunOpen: (runOpen) => set({ runOpen }),
  resetRunState: () => set({
    activeSubagent: null,
    activeRunId: null,
    awaitingAssistant: false,
    streamNotice: null,
    runOpen: false
  })
}));
