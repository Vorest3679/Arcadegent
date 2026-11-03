import { useEffect } from "react";
import { AppSidebar } from "./components/AppSidebar";
import { AppTopbar } from "./components/AppTopbar";
import { ArcadeBrowser } from "./components/ArcadeBrowser";
import { ChatPanel } from "./components/chat/ChatPanel";
import { useChatSessionController } from "./hooks/useChatSessionController";
import { readInitialViewMode } from "./lib/viewMode";
import { useAppStore } from "./stores/appStore";

export function App() {
  const viewMode = useAppStore((state) => state.viewMode);
  const chat = useChatSessionController();

  useEffect(() => {
    function handlePopState() {
      useAppStore.getState().setViewMode(readInitialViewMode(), { syncUrl: false });
    }

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  return (
    <div className="flex h-dvh overflow-hidden pt-[var(--safe-top)] pr-[var(--safe-right)] pl-[var(--safe-left)]">
      <AppSidebar
        onStartNewSession={chat.startNewSession}
        onOpenChatView={chat.openChatView}
        onOpenArcadesView={chat.openArcadesView}
        onRefresh={chat.refreshSessions}
        onSelectSession={chat.selectSession}
        onDeleteSession={(sessionId) => void chat.removeSession(sessionId)}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <AppTopbar />

        {viewMode === "chat" ? (
          <ChatPanel city={chat.currentCity} onSubmit={chat.submitChat} onQuickAsk={chat.quickAsk} />
        ) : (
          <ArcadeBrowser />
        )}
      </main>
    </div>
  );
}
