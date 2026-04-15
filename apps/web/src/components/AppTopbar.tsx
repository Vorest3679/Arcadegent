import { formatTimeLabel } from "../lib/chatStream";
import { useAppStore } from "../stores/appStore";

export function AppTopbar() {
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useAppStore((state) => state.sessions);
  const activeSessionId = useAppStore((state) => state.activeSessionId);
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
