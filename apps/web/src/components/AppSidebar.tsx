import { Fragment, useMemo } from "react";
import { Loader2, MessageSquareText, MoreHorizontal, Pin, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { formatTimeLabel } from "@/lib/sse/chatStream";
import { orderSessionsByPins } from "@/lib/sessionPins";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/appStore";
import { useSessionStore } from "@/stores/sessionStore";
import type { ChatSessionSummary } from "@/types";

type SidebarSessionItemProps = {
  item: ChatSessionSummary;
  active: boolean;
  pinned: boolean;
  deleting: boolean;
  onClick: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
};

function SidebarSessionItem({
  item,
  active,
  pinned,
  deleting,
  onClick,
  onTogglePin,
  onDelete
}: SidebarSessionItemProps) {
  return (
    <li data-testid="session-row" data-active={active ? "true" : "false"} data-pinned={pinned ? "true" : "false"}>
      <div
        className={cn(
          "group flex items-center gap-1 rounded-lg border border-transparent transition-colors hover:bg-accent/60",
          active && "border-primary/30 bg-accent"
        )}
      >
        <button type="button" onClick={onClick} className="min-w-0 flex-1 px-3 py-2 text-left">
          <span className="line-clamp-2 text-sm leading-snug font-medium">{item.title}</span>
          <span className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
            {pinned ? <Pin className="size-3 shrink-0 fill-current text-primary" aria-hidden="true" /> : null}
            <span className="truncate">{formatTimeLabel(item.updated_at)}</span>
          </span>
        </button>
        {/* Click-triggered, not hover-triggered: the pointer crossing the
            trigger/panel gap (or any other move) cannot close the menu; only an
            outside click, Escape or picking an item does. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger
            aria-label={`会话操作：${item.title}`}
            title="更多操作"
            disabled={deleting}
            className="mr-1 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:bg-accent data-[state=open]:text-foreground disabled:pointer-events-none disabled:opacity-50"
          >
            {deleting ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <MoreHorizontal className="size-4" aria-hidden="true" />
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem onSelect={onTogglePin}>
              <Pin className={cn(pinned && "fill-current")} aria-hidden="true" />
              {pinned ? "取消置顶" : "置顶"}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 aria-hidden="true" />
              删除会话
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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
  onToggleSessionPin: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
};

function SidebarContent({
  onStartNewSession,
  onOpenChatView,
  onOpenArcadesView,
  onRefresh,
  onSelectSession,
  onToggleSessionPin,
  onDeleteSession
}: AppSidebarProps) {
  const viewMode = useAppStore((state) => state.viewMode);
  const sessions = useSessionStore((state) => state.sessions);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const sessionsLoading = useSessionStore((state) => state.sessionsLoading);
  const deletingSessionId = useSessionStore((state) => state.deletingSessionId);
  const orderedSessions = useMemo(() => orderSessionsByPins(sessions), [sessions]);
  const pinnedCount = orderedSessions.filter((item) => item.pinned_at).length;
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
        {orderedSessions.map((item, index) => (
          <Fragment key={item.session_id}>
            {pinnedCount > 0 && index === pinnedCount ? (
              <li aria-hidden="true" className="mx-1 my-1 border-t border-border/70" />
            ) : null}
            <SidebarSessionItem
              item={item}
              active={item.session_id === activeSessionId}
              pinned={Boolean(item.pinned_at)}
              deleting={deletingSessionId === item.session_id}
              onClick={() => onSelectSession(item.session_id)}
              onTogglePin={() => onToggleSessionPin(item.session_id)}
              onDelete={() => onDeleteSession(item.session_id)}
            />
          </Fragment>
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
