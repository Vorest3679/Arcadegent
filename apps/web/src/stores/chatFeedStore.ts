import { create } from "zustand";
import { EMPTY_FEED, type ChatFeed } from "../lib/chatFeed";
import { resolveUpdater, type Updater } from "./updater";

// Everything the chat view lists for the active session — user messages,
// intermediate and final replies, tool calls, maps and errors — in one
// ordered array, plus the status of each tool call. History and the live run
// both write here; see lib/chatFeed for how events fold in.
type ChatFeedStore = ChatFeed & {
  setFeed: (feed: Updater<ChatFeed>) => void;
  resetFeed: () => void;
};

export const useChatFeedStore = create<ChatFeedStore>((set) => ({
  ...EMPTY_FEED,
  setFeed: (feed) => set((state) => {
    const next = resolveUpdater(feed, { items: state.items, toolStatus: state.toolStatus });
    return { items: next.items, toolStatus: next.toolStatus };
  }),
  resetFeed: () => set({ ...EMPTY_FEED })
}));
