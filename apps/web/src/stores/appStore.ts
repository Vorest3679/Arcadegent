import { create } from "zustand";
import { readInitialViewMode, syncViewModeInUrl } from "../lib/viewMode";
import type { ViewMode } from "../types";

// View-level state only; chat session state lives in sessionStore.
type AppStore = {
  viewMode: ViewMode;
  sidebarOpen: boolean;
  inputValue: string;
  setViewMode: (viewMode: ViewMode, options?: { replace?: boolean; syncUrl?: boolean }) => void;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setInputValue: (value: string) => void;
};

export const useAppStore = create<AppStore>((set) => ({
  viewMode: readInitialViewMode(),
  sidebarOpen: false,
  inputValue: "",
  setViewMode: (viewMode, options = {}) => {
    if (options.syncUrl !== false) {
      syncViewModeInUrl(viewMode, { replace: options.replace });
    }
    set({ viewMode });
  },
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setInputValue: (inputValue) => set({ inputValue })
}));
