import { Loader2, MessageSquareText, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { formatTimeLabel } from "@/lib/sse/chatStream";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/appStore";
import { useSessionStore } from "@/stores/sessionStore";
import type { ChatSessionSummary } from "@/types";

type SidebarSessionItemProps = {
  item: ChatSessionSummary;
  active: boolean;
  deleting: boolean;
  onClick: () => void;
  onDelete: () => void;
};

function SidebarSessionItem({ item, active, deleting, onClick, onDelete }: SidebarSessionItemProps) {
  return (
    <li data-testid="session-row" data-active={active ? "true" : "false"}>
      <div
        className={cn(
          "group flex items-center gap-1 rounded-lg border border-transparent transition-colors hover:bg-accent/60",
          active && "border-primary/30 bg-accent"
        )}
      >
        <button type="button" onClick={onClick} className="min-w-0 flex-1 px-3 py-2 text-left">
          <span className="line-clamp-2 text-sm leading-snug font-medium">{item.title}</span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">{formatTimeLabel(item.updated_at)}</span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onDelete}
          disabled={deleting}
          aria-label={`删除会话：${item.title}`}
          title="删除会话"
          className="mr-1 size-8 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
        >
          {deleting ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
        </Button>
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

function SidebarContent({
  onStartNewSession,
  onOpenChatView,
  onOpenArcadesView,
  onRefresh,
  onSelectSession,
  onDeleteSession
}: AppSidebarProps) {
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useSessionStore((state) => state.sessions);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const sessionsLoading = useSessionStore((state) => state.sessionsLoading);
  const deletingSessionId = useSessionStore((state) => state.deletingSessionId);
  const navItems = [
    { key: "chat", label: "Agent 对话", icon: MessageSquareText, onClick: onOpenChatView },
    { key: "arcades", label: "机厅检索", icon: Search, onClick: onOpenArcadesView }
  ] as const;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4">
      <div className="flex items-center gap-2 px-1">
        <img src="/icons/icon-192.png" alt="" className="size-8 rounded-lg" />
        <span className="text-lg font-semibold tracking-tight">Arcadegent</span>
      </div>

      <Button type="button" variant="outline" onClick={onStartNewSession} className="justify-start bg-card">
        <Plus aria-hidden="true" />
        新建会话
      </Button>

      <nav className="grid gap-1">
        {navItems.map(({ key, label, icon: Icon, onClick }) => (
          <button
            key={key}
            type="button"
            onClick={onClick}
            aria-current={viewMode === key ? "page" : undefined}
            className={cn(
              "flex h-9 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground",
              viewMode === key && "bg-accent font-medium text-foreground"
            )}
          >
            <Icon className="size-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      <div className="flex items-center justify-between px-1">
        <span className="text-xs font-medium tracking-wide text-muted-foreground">历史会话</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          disabled={sessionsLoading}
          className="h-7 px-2 text-xs text-muted-foreground"
        >
          <RefreshCw className={cn("size-3.5", sessionsLoading && "animate-spin")} aria-hidden="true" />
          {sessionsLoading ? "同步中" : "刷新"}
        </Button>
      </div>

      <ul className="-mx-1 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-1">
        {sessionsLoading && sessions.length === 0 ? (
          <li className="px-3 py-2 text-sm text-muted-foreground">会话加载中...</li>
        ) : null}
        {!sessionsLoading && sessions.length === 0 ? (
          <li className="px-3 py-2 text-sm text-muted-foreground">暂无历史会话</li>
        ) : null}
        {sessions.map((item) => (
          <SidebarSessionItem
            key={item.session_id}
            item={item}
            active={item.session_id === activeSessionId}
            deleting={deletingSessionId === item.session_id}
            onClick={() => onSelectSession(item.session_id)}
            onDelete={() => onDeleteSession(item.session_id)}
          />
        ))}
      </ul>
    </div>
  );
}

// A fixed column on wide screens, a drawer on small ones.
export function AppSidebar(props: AppSidebarProps) {
  const wide = useMediaQuery("(min-width: 1024px)");
  const sidebarOpen = useAppStore((state) => state.sidebarOpen);
  const setSidebarOpen = useAppStore((state) => state.setSidebarOpen);

  if (wide) {
    return (
      <aside className="flex w-72 shrink-0 flex-col border-r bg-white/70 backdrop-blur-md" data-testid="app-sidebar">
        <SidebarContent {...props} />
      </aside>
    );
  }
  return (
    <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
      <SheetContent side="left" className="w-[86vw] max-w-80 gap-0 p-0 pt-[var(--safe-top)]" data-testid="app-sidebar">
        <SheetTitle className="sr-only">导航与历史会话</SheetTitle>
        <SheetDescription className="sr-only">切换视图或打开历史会话</SheetDescription>
        <SidebarContent {...props} />
      </SheetContent>
    </Sheet>
  );
}
