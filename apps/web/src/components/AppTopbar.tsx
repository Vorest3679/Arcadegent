import { formatTimeLabel } from "../lib/sse/chatStream";
import { useAppStore } from "../stores/appStore";
import { useSessionStore } from "../stores/sessionStore";

export function AppTopbar() {
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useSessionStore((state) => state.sessions);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const activeSessionUpdatedAt =
    sessions.find((session) => session.session_id === activeSessionId)?.updated_at ?? null;

  return (
    <header className="topbar">
      <button type="button" className="menu-btn" onClick={toggleSidebar}>
        ☰
      </button>
      <div>
        <h2>{viewMode === "chat" ? "Agent 对话" : "机厅检索"}</h2>
        <p>{activeSessionUpdatedAt ? `最近更新 ${formatTimeLabel(activeSessionUpdatedAt)}` : ""}</p>
      </div>
    </header>
  );
}
