import { FormEvent, useEffect, useRef } from "react";
import { latestMapArtifacts, mapArrivalKey } from "../../lib/mapDock";
import { useAppStore } from "../../stores/appStore";
import { useRunStore } from "../../stores/runStore";
import { useSessionStore } from "../../stores/sessionStore";
import { MapDock } from "./MapDock";
import { RoundList } from "./RoundList";

// Distance from the bottom within which streaming output keeps following.
const FOLLOW_THRESHOLD_PX = 120;
// Visual viewport shrink that means the soft keyboard is up.
const KEYBOARD_THRESHOLD_PX = 150;

type ChatPanelProps = {
  city: string;
  onSubmit: (event: FormEvent) => Promise<void>;
  onQuickAsk: (prompt: string) => void;
  streamReply: string;
  streamReplyActive: boolean;
};

export function ChatPanel({ city, onSubmit, onQuickAsk, streamReply, streamReplyActive }: ChatPanelProps) {
  const quickPrompts = [
    "帮我找找适合下班后去的机厅",
    `${city}最多机台的机厅是哪家？`,
    "给我一条从当前位置到最近机厅的路线建议"
  ];
  const turns = useSessionStore((state) => state.turns);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const loading = useSessionStore((state) => state.turnsLoading);
  const sending = useSessionStore((state) => state.sending);
  const error = useSessionStore((state) => state.chatError);
  const inputValue = useAppStore((state) => state.inputValue);
  const setInputValue = useAppStore((state) => state.setInputValue);
  const awaitingAssistant = useRunStore((state) => state.awaitingAssistant);
  const activeRunId = useRunStore((state) => state.activeRunId);
  const committedRunId = useRunStore((state) => state.committedRunId);
  const liveArtifacts = useRunStore((state) => state.activeMapArtifacts);
  const liveRound = useRunStore((state) => state.liveRound);
  const streamItems = useRunStore((state) => state.streamItems);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);

  // Live while the run's reply has not been handed over to history yet.
  const runLive = awaitingAssistant || (activeRunId !== null && activeRunId !== committedRunId);
  const showEmptyState = turns.length === 0 && !runLive && !loading;
  const composerBusy = sending || awaitingAssistant;
  const mapArtifacts = latestMapArtifacts(turns, liveArtifacts, runLive);
  const arrivalKey = mapArrivalKey(activeSessionId, turns, liveArtifacts, runLive);

  // Follow new output only while the user is near the bottom, so reading
  // history on a phone is not interrupted. Sending always follows.
  useEffect(() => {
    if (sending) {
      followRef.current = true;
    }
  }, [sending]);
  useEffect(() => {
    const element = scrollRef.current;
    if (element && followRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [turns, loading, sending, streamItems, streamReply, liveRound, awaitingAssistant, runLive]);

  // A soft keyboard leaves little room: fold the map away while it is up.
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) {
      return;
    }
    const onResize = () => {
      if (window.innerHeight - viewport.height > KEYBOARD_THRESHOLD_PX) {
        useRunStore.getState().setMapDock({ open: false });
      }
    };
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, []);

  return (
    <div className="chat-view">
      <MapDock artifacts={mapArtifacts} arrivalKey={arrivalKey} />

      <div
        className="chat-scroll"
        ref={scrollRef}
        onScroll={(event) => {
          const element = event.currentTarget;
          followRef.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
      >
        {showEmptyState ? (
          <div className="chat-empty">
            <p className="chat-empty-title">今天想查哪家机厅？</p>
            <p className="chat-empty-subtitle">你可以直接提问，也可以先点一个预设问题。</p>
            <div className="chat-quick-grid">
              {quickPrompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="quick-chip"
                  onClick={() => onQuickAsk(prompt)}
                  disabled={composerBusy}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <RoundList runLive={runLive} streamReply={streamReply} streamReplyActive={streamReplyActive} />
        )}

        {loading ? (
          <div className="chat-loading" role="status" aria-label="加载会话中">
            <svg className="chat-loading-spinner" viewBox="0 0 24 24" width="32" height="32" aria-hidden="true">
              <circle cx="12" cy="12" r="9" fill="none" strokeWidth="2.6" strokeLinecap="round" strokeDasharray="30 30" />
            </svg>
          </div>
        ) : null}
      </div>

      {error ? <div className="chat-error">{error}</div> : null}

      <form className="chat-composer" onSubmit={(event) => void onSubmit(event)}>
        <input
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          placeholder="尽管问机厅相关问题"
          disabled={composerBusy}
        />
        <button type="submit" disabled={composerBusy || inputValue.trim().length === 0}>
          {sending ? "发送中..." : awaitingAssistant ? "处理中..." : "发送"}
        </button>
      </form>
    </div>
  );
}
