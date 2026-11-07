import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatTimeLabel } from "@/lib/sse/chatStream";
import { useAppStore } from "@/stores/appStore";
import { useSessionStore } from "@/stores/sessionStore";

export function AppTopbar() {
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useSessionStore((state) => state.sessions);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const activeSession = sessions.find((session) => session.session_id === activeSessionId) ?? null;
  const title = viewMode === "chat" ? activeSession?.title || "Agent 对话" : "机厅查找";

  return (
    <header data-testid="app-topbar" className="flex h-14 shrink-0 items-center gap-3 border-b bg-white/50 px-4 backdrop-blur-md">
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="bg-card lg:hidden"
        aria-label="打开侧边栏"
        onClick={toggleSidebar}
      >
        <Menu aria-hidden="true" />
      </Button>
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold">{title}</h1>
        {viewMode === "chat" && activeSession ? (
          <p className="truncate text-xs text-muted-foreground">最近更新 {formatTimeLabel(activeSession.updated_at)}</p>
        ) : null}
      </div>
    </header>
  );
}
