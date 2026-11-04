import { memo, useRef, type ComponentType } from "react";
import {
  BookOpen,
  BookOpenText,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleMinus,
  CircleX,
  Database,
  FileText,
  ListFilter,
  Loader2,
  Map as MapIcon,
  MapPin,
  Maximize2,
  Pickaxe,
  Route,
  Workflow,
  type LucideProps
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { useInView } from "@/hooks/useInView";
import { useSmoothText } from "@/hooks/useSmoothText";
import type { AssistantItem, ErrorItem, MapItem, ToolItem, ToolStatus, UserItem } from "@/lib/chatFeed";
import { mapSummary } from "@/lib/mapArtifacts";
import { formatAgentName, formatTimeLabel, formatToolName } from "@/lib/sse/chatStream";
import { cn } from "@/lib/utils";
import { useChatFeedStore } from "@/stores/chatFeedStore";
import { useRunStore } from "@/stores/runStore";
import { AgentMapCard } from "../map/AgentMapCard";
import { MarkdownMessage } from "../MarkdownMessage";

export function UserMessage({ item }: { item: UserItem }) {
  return (
    <div className="flex flex-col items-end gap-1" data-kind="user">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-[0.95rem] leading-relaxed whitespace-pre-wrap break-words text-primary-foreground shadow-sm">
        {item.content}
      </div>
      <time className="px-1 text-xs text-muted-foreground">{formatTimeLabel(item.at)}</time>
    </div>
  );
}

// Replies are plain text with no bubble or side rule, so the live stream and
// the stored history render the same. Only the final reply carries its time.
export const AssistantMessage = memo(function AssistantMessage({ item }: { item: AssistantItem }) {
  const content = useSmoothText(item.content, item.streaming);
  return (
    <div
      data-kind="assistant"
      data-final={item.final ? "true" : "false"}
      data-streaming={item.streaming ? "true" : "false"}
      className="max-w-full"
    >
      {content.trim() ? (
        <MarkdownMessage content={content} className={item.streaming ? "is-streaming" : undefined} />
      ) : null}
      {item.final ? (
        <time className="mt-1.5 block text-xs text-muted-foreground">{formatTimeLabel(item.at)}</time>
      ) : null}
    </div>
  );
});

// Icons of the whitelisted tools; any other tool (e.g. an MCP tool) shares
// the pickaxe so every call still reads as a tool.
const TOOL_ICON: Record<string, ComponentType<LucideProps>> = {
  invoke_worker: Workflow,
  db_query_tool: Database,
  geo_resolve_tool: MapPin,
  route_plan_tool: Route,
  summary_tool: FileText,
  result_selection_tool: ListFilter,
  list_skills: BookOpen,
  read_skill: BookOpenText
};

const EXTERNAL_TOOL_ICON = Pickaxe;

const STATUS_LABEL: Record<ToolStatus, string> = {
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消"
};

function ToolStatusIcon({ status }: { status: ToolStatus }) {
  const props = { role: "img", "aria-label": STATUS_LABEL[status], className: "size-4 shrink-0" } as const;
  switch (status) {
    case "running":
      return <Loader2 {...props} className={cn(props.className, "animate-spin text-primary")} />;
    case "completed":
      return <CircleCheck {...props} className={cn(props.className, "text-primary")} />;
    case "failed":
      return <CircleX {...props} className={cn(props.className, "text-destructive")} />;
    default:
      return <CircleMinus {...props} className={cn(props.className, "text-muted-foreground")} />;
  }
}

// The row reads its status from the feed's toolStatus map: the terminal event
// of a call arrives later, and only that entry changes.
export function ToolCallRow({ item }: { item: ToolItem }) {
  const status = useChatFeedStore((state) => state.toolStatus[item.key]) ?? "running";
  const Icon = TOOL_ICON[item.name] ?? EXTERNAL_TOOL_ICON;
  const running = status === "running";
  return (
    <div data-kind="tool" data-status={status} title={item.name} className="flex">
      <div
        className={cn(
          "inline-flex max-w-full items-center gap-2 rounded-full border bg-white/70 px-3 py-1.5 text-sm shadow-xs",
          status === "failed" && "border-destructive/30 bg-destructive/5"
        )}
      >
        {Icon ? (
          <span
            data-testid="tool-icon"
            className={cn(
              "relative grid size-5 shrink-0 place-items-center rounded-full",
              running ? "bg-highlight-soft text-highlight-foreground" : "bg-accent text-primary"
            )}
          >
            {running ? <span className="absolute inset-0 animate-ping rounded-full bg-highlight/30" aria-hidden="true" /> : null}
            <Icon className={cn("relative size-3.5", running && "animate-pulse")} aria-hidden="true" />
          </span>
        ) : null}
        <span className="truncate font-medium">{formatToolName(item.name)}</span>
        {item.agent ? (
          <Badge variant="secondary" className="shrink-0 font-normal text-muted-foreground">
            {formatAgentName(item.agent)}
          </Badge>
        ) : null}
        <ToolStatusIcon status={status} />
      </div>
    </div>
  );
}

// A static map thumbnail sits on the card as soon as there is something to
// draw (candidates or a planned route), even while the run still works; a
// route being planned shows the compact line only. The thumbnail mounts near
// the viewport, so a long history does not create a map per answer.
export function MapArtifactCard({ item, onOpen }: { item: MapItem; onOpen: (key: string) => void }) {
  const { artifacts } = item;
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref);
  const title = typeof artifacts.view_payload?.title === "string" && artifacts.view_payload.title.trim()
    ? artifacts.view_payload.title.trim()
    : artifacts.route || artifacts.route_pending ? "路线地图" : "候选机厅地图";
  const pending = artifacts.route_pending && !artifacts.route;
  const showPreview = !pending;
  return (
    <div
      ref={ref}
      data-kind="map"
      className={cn(
        "group w-full overflow-hidden rounded-xl border bg-card shadow-sm transition hover:border-primary/40 hover:shadow-md",
        showPreview ? "max-w-xl" : "max-w-md"
      )}
    >
      {showPreview ? (
        <div className="relative border-b">
          {/* Decorative and not focusable (its markers are buttons); the
              button below is the accessible control. React 18 has no `inert` prop. */}
          <div ref={(node) => node?.setAttribute("inert", "")} aria-hidden="true">
            {inView ? <AgentMapCard artifacts={artifacts} variant="preview" /> : <div className="h-40 bg-muted sm:h-48" />}
          </div>
          <div aria-hidden="true" onClick={() => onOpen(item.key)} className="absolute inset-0 cursor-pointer" />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-2 right-2 inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-xs text-primary shadow-sm"
          >
            <Maximize2 className="size-3" />
            查看大图
          </span>
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => onOpen(item.key)}
        aria-label={`查看地图：${mapSummary(artifacts)}`}
        className="flex w-full items-center gap-3 p-3 text-left focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-primary">
          {pending ? <Loader2 className="size-5 animate-spin" aria-hidden="true" /> : <MapIcon className="size-5" aria-hidden="true" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{title}</span>
          <span className="block truncate text-sm text-muted-foreground">{mapSummary(artifacts)}</span>
        </span>
        <Maximize2 className="size-4 shrink-0 text-muted-foreground transition group-hover:text-primary" aria-hidden="true" />
      </button>
    </div>
  );
}

// Consecutive tool calls between two replies. Expanded while the run's stream
// is open, folded into one line once it ended; an explicit toggle wins.
export function ToolGroup({ items, open, onToggle }: {
  items: ToolItem[];
  open: boolean;
  onToggle: () => void;
}) {
  const toolStatus = useChatFeedStore((state) => state.toolStatus);
  const statuses = items.map((item) => toolStatus[item.key] ?? "running");
  const running = statuses.some((status) => status === "running");
  const failed = statuses.filter((status) => status === "failed").length;
  const icons = [...new Set(items.map((item) => item.name))]
    .map((name) => ({ name, Icon: TOOL_ICON[name] ?? EXTERNAL_TOOL_ICON }))
    // One badge per icon: external tools share the pickaxe.
    .filter(({ Icon }, index, all) => all.findIndex((entry) => entry.Icon === Icon) === index)
    .slice(0, 4);
  return (
    <div data-kind="tool-group" data-testid="tool-group" className="grid gap-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="inline-flex w-fit items-center gap-2 rounded-full px-1 py-0.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        {running ? <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" /> : null}
        {icons.length > 0 ? (
          <span className="flex -space-x-1" aria-hidden="true">
            {icons.map(({ name, Icon }) => (
              <span key={name} className="grid size-5 place-items-center rounded-full border border-card bg-accent text-primary">
                <Icon className="size-3" />
              </span>
            ))}
          </span>
        ) : null}
        <span>{`调用了 ${items.length} 个工具`}</span>
        {failed > 0 ? <span className="text-destructive">{`${failed} 个失败`}</span> : null}
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      {open ? (
        <div className="grid gap-2 border-l-2 border-border pl-3">
          {items.map((item) => (
            <ToolCallRow key={item.key} item={item} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ErrorMessage({ item }: { item: ErrorItem }) {
  return (
    <Alert variant="destructive" data-kind="error" className="bg-destructive/5">
      <CircleAlert aria-hidden="true" />
      <AlertDescription className="text-destructive">{item.message}</AlertDescription>
    </Alert>
  );
}

// Shown at the end of the feed while the run works and no text is streaming.
export function RunIndicator() {
  const activeSubagent = useRunStore((state) => state.activeSubagent);
  const notice = useRunStore((state) => state.streamNotice);
  const connected = useRunStore((state) => state.streamConnected);
  const text = notice ?? (connected ? `${formatAgentName(activeSubagent, "智能体")} · 处理中` : "连接中");
  return (
    <div
      role="status"
      data-testid="run-indicator"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
      <span>{text}…</span>
    </div>
  );
}
