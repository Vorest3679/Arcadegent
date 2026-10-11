import { create } from "zustand";
import type { ChatRound } from "../lib/chatRounds";
import type { StreamProgressItem } from "../lib/sse/chatStream";
import type { ChatMapArtifacts } from "../types";
import { resolveUpdater, type Updater } from "./updater";

// State of the agent run the chat view follows: transport, progress, live
// steps and map. Session list and history live in sessionStore.
type RunStore = {
  streamConnected: boolean;
  activeSubagent: string | null;
  streamItems: StreamProgressItem[];
  awaitingAssistant: boolean;
  activeMapArtifacts: ChatMapArtifacts | null;
  // Run the chat view follows; it is live until its detail is loaded and it
  // becomes committedRunId, whose reply is then part of `turns`.
  activeRunId: string | null;
  committedRunId: string | null;
  // Tool and intermediate-reply steps of the run being followed.
  liveRound: ChatRound | null;
  setStreamConnected: (connected: boolean) => void;
  setActiveSubagent: (subagent: string | null) => void;
  setStreamItems: (items: Updater<StreamProgressItem[]>) => void;
  setAwaitingAssistant: (awaiting: boolean) => void;
  setActiveMapArtifacts: (artifacts: Updater<ChatMapArtifacts | null>) => void;
  setActiveRunId: (runId: string | null) => void;
  setCommittedRunId: (runId: string | null) => void;
  setLiveRound: (round: Updater<ChatRound | null>) => void;
  resetRunState: () => void;
};

export const useRunStore = create<RunStore>((set) => ({
  streamConnected: false,
  activeSubagent: null,
  streamItems: [],
  awaitingAssistant: false,
  activeMapArtifacts: null,
  activeRunId: null,
  committedRunId: null,
  liveRound: null,
  setStreamConnected: (streamConnected) => set({ streamConnected }),
  setActiveSubagent: (activeSubagent) => set({ activeSubagent }),
  setStreamItems: (streamItems) => set((state) => ({
    streamItems: resolveUpdater(streamItems, state.streamItems)
  })),
  setAwaitingAssistant: (awaitingAssistant) => set({ awaitingAssistant }),
  setActiveMapArtifacts: (activeMapArtifacts) => set((state) => ({
    activeMapArtifacts: resolveUpdater(activeMapArtifacts, state.activeMapArtifacts)
  })),
  setActiveRunId: (activeRunId) => set({ activeRunId }),
  setCommittedRunId: (committedRunId) => set({ committedRunId }),
  setLiveRound: (liveRound) => set((state) => ({
    liveRound: resolveUpdater(liveRound, state.liveRound)
  })),
  resetRunState: () => set({
    activeSubagent: null,
    activeMapArtifacts: null,
    activeRunId: null,
    committedRunId: null,
    liveRound: null,
    awaitingAssistant: false
  })
}));
