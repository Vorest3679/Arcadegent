import { type FormEvent, useEffect, useRef, useState } from "react";
import { Loader2, MapPinned, Route, Sparkles } from "lucide-react";
import { useChatFeedStore } from "@/stores/chatFeedStore";
import { useRunStore } from "@/stores/runStore";
import { useSessionStore } from "@/stores/sessionStore";
import { ChatComposer } from "./ChatComposer";
import { ChatFeedList } from "./ChatFeedList";
import { MapPanel } from "./MapPanel";

// Distance from the bottom within which streaming output keeps following.
const FOLLOW_THRESHOLD_PX = 120;

type ChatPanelProps = {
  city: string;
  onSubmit: (event: FormEvent) => Promise<void>;
  onQuickAsk: (prompt: string) => void;
};

export function ChatPanel({ city, onSubmit, onQuickAsk }: ChatPanelProps) {
  const quickPrompts = [
    { icon: Sparkles, text: "帮我找找适合下班后去的机厅" },
    { icon: MapPinned, text: `${city}最多机台的机厅是哪家？` },
    { icon: Route, text: "给我一条从当前位置到最近机厅的路线建议" }
  ];
  const items = useChatFeedStore((state) => state.items);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const loading = useSessionStore((state) => state.turnsLoading);
  const sending = useSessionStore((state) => state.sending);
  const awaitingAssistant = useRunStore((state) => state.awaitingAssistant);
  const [mapKey, setMapKey] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);

  const showEmptyState = items.length === 0 && !awaitingAssistant && !loading;
  const composerBusy = sending || awaitingAssistant;

  // Another session: its maps are not this one's.
  useEffect(() => setMapKey(null), [activeSessionId]);

  // Follow new output only while the user is near the bottom, so reading
  // history is not interrupted. Sending always follows.
  useEffect(() => {
    if (sending) {
      followRef.current = true;
    }
  }, [sending]);
  // Content grows frame by frame while a reply is revealed; follow its size.
  useEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content) {
      return;
    }
    const observer = new ResizeObserver(() => {
      if (followRef.current) {
        scroller.scrollTop = scroller.scrollHeight;
      }
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col" data-testid="chat-view">
      <div
        ref={scrollRef}
        data-testid="chat-scroll"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
        onScroll={(event) => {
          const element = event.currentTarget;
          followRef.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
      >
        <div ref={contentRef} className="mx-auto w-full max-w-3xl px-4 pt-6 pb-8 sm:px-6">
          {showEmptyState ? (
            <div className="flex flex-col items-center gap-6 pt-[12vh] text-center">
              <div className="grid size-14 place-items-center rounded-2xl bg-accent text-primary shadow-sm">
                <Sparkles className="size-7" aria-hidden="true" />
              </div>
              <div className="space-y-2">
                <p className="text-2xl font-semibold tracking-tight">今天想查哪家机厅？</p>
                <p className="text-sm text-muted-foreground">你可以直接提问，也可以先点一个预设问题。</p>
              </div>
              <div className="grid w-full max-w-xl gap-2 sm:grid-cols-3">
                {quickPrompts.map(({ icon: Icon, text }) => (
                  <button
                    key={text}
                    type="button"
                    onClick={() => onQuickAsk(text)}
                    disabled={composerBusy}
                    className="flex flex-col items-start gap-2 rounded-xl border bg-card p-3 text-left text-sm shadow-xs transition hover:border-primary/40 hover:bg-accent/40 disabled:opacity-50"
                  >
                    <Icon className="size-4 text-primary" aria-hidden="true" />
                    <span>{text}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <ChatFeedList runLive={awaitingAssistant} onOpenMap={setMapKey} />
          )}

          {loading ? (
            <div className="flex justify-center py-10" role="status" aria-label="加载会话中">
              <Loader2 className="size-7 animate-spin text-primary" aria-hidden="true" />
            </div>
          ) : null}
        </div>
      </div>

      <div className="mx-auto w-full max-w-3xl px-3 pb-[max(0.75rem,var(--safe-bottom))] sm:px-6">
        <ChatComposer busy={composerBusy} sending={sending} onSubmit={onSubmit} />
      </div>

      <MapPanel itemKey={mapKey} onClose={() => setMapKey(null)} />
    </div>
  );
}
