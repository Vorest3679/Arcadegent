import { formatTimeLabel } from "../lib/chatStream";
import { useAppStore } from "../stores/appStore";
import type { ChatSessionSummary } from "../types";

type SidebarSessionItemProps = {
  item: ChatSessionSummary;
  active: boolean;
  deleting: boolean;
  onClick: () => void;
  onDelete: () => void;
};

function SidebarSessionItem({ item, active, deleting, onClick, onDelete }: SidebarSessionItemProps) {
  return (
    <li>
      <div className={`sidebar-session-wrap ${active ? "is-active" : ""}`}>
        <button type="button" onClick={onClick} className="sidebar-session">
          <strong>{item.title}</strong>
          <small>{formatTimeLabel(item.updated_at)}</small>
        </button>
        <button type="button" className="sidebar-session-delete" onClick={onDelete} disabled={deleting}>
          {deleting ? "..." : "删"}
        </button>
      </div>
    </li>
  );
}

type AppSidebarProps = {
  onStartNewSession: () => void;
  onOpenChatView: () => void;
  onOpenArcadesView: () => void;
  onRefresh: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
};

export function AppSidebar({
  onStartNewSession,
  onOpenChatView,
  onOpenArcadesView,
  onRefresh,
  onSelectSession,
  onDeleteSession
}: AppSidebarProps) {
  const sidebarOpen = useAppStore((state) => state.sidebarOpen);
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useAppStore((state) => state.sessions);
  const activeSessionId = useAppStore((state) => state.activeSessionId);
  const sessionsLoading = useAppStore((state) => state.sessionsLoading);
  const deletingSessionId = useAppStore((state) => state.deletingSessionId);

  return (
    <aside className={`app-sidebar ${sidebarOpen ? "is-open" : ""}`}>
      <div className="sidebar-top">
        <h1>Arcadegent</h1>
        <button type="button" className="sidebar-new" onClick={onStartNewSession}>
          + 新建会话
        </button>
      </div>

      <nav className="sidebar-nav">
        <button
          type="button"
          className={`sidebar-nav-btn ${viewMode === "chat" ? "is-active" : ""}`}
          onClick={onOpenChatView}
        >
          Agent 对话
        </button>
        <button
          type="button"
          className={`sidebar-nav-btn ${viewMode === "arcades" ? "is-active" : ""}`}
          onClick={onOpenArcadesView}
        >
          机厅检索
        </button>
      </nav>

      <div className="sidebar-history-head">
        <strong>历史会话</strong>
        <button type="button" onClick={onRefresh} disabled={sessionsLoading}>
          刷新
        </button>
      </div>

      <ul className="sidebar-history-list">
        {sessionsLoading ? <li className="sidebar-empty">会话加载中...</li> : null}
        {!sessionsLoading && sessions.length === 0 ? <li className="sidebar-empty">暂无历史会话</li> : null}
        {!sessionsLoading
          ? sessions.map((item) => (
              <SidebarSessionItem
                key={item.session_id}
                item={item}
                active={item.session_id === activeSessionId}
                deleting={deletingSessionId === item.session_id}
                onClick={() => onSelectSession(item.session_id)}
                onDelete={() => onDeleteSession(item.session_id)}
              />
            ))
          : null}
      </ul>
    </aside>
  );
}
