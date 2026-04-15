import { Fragment, FormEvent, useEffect, useMemo, useRef } from "react";
import { formatSubagentLabel, formatTimeLabel } from "../lib/chatStream";
import { useAppStore } from "../stores/appStore";
import { MarkdownMessage } from "./MarkdownMessage";
import { AgentMapCard } from "./map/AgentMapCard";

const QUICK_PROMPTS = [
  "帮我找北京适合下班后去的机厅",
  "我在广州，推荐几家有 maimai 的店",
  "给我一条从当前位置到最近机厅的路线建议"
];

type ChatPanelProps = {
  onSubmit: (event: FormEvent) => Promise<void>;
  onQuickAsk: (prompt: string) => void;
  streamReplyTarget: string;
  streamReply: string;
  streamReplyActive: boolean;
};

export function ChatPanel({
  onSubmit,
  onQuickAsk,
  streamReplyTarget,
  streamReply,
  streamReplyActive
}: ChatPanelProps) {
  const turns = useAppStore((state) => state.turns);
  const loading = useAppStore((state) => state.turnsLoading);
  const sending = useAppStore((state) => state.sending);
  const inputValue = useAppStore((state) => state.inputValue);
  const setInputValue = useAppStore((state) => state.setInputValue);
  const error = useAppStore((state) => state.chatError);
  const streamConnected = useAppStore((state) => state.streamConnected);
  const activeSubagent = useAppStore((state) => state.activeSubagent);
  const streamItems = useAppStore((state) => state.streamItems);
  const awaitingAssistant = useAppStore((state) => state.awaitingAssistant);
  const mapArtifacts = useAppStore((state) => state.activeMapArtifacts);
  const endRef = useRef<HTMLDivElement | null>(null);

  const turnsForRender = useMemo(() => {
    if (!turns.length) {
      return turns;
    }

    const last = turns[turns.length - 1];
    if (last.role !== "assistant") {
      return turns;
    }

    const hasStreamingContext =
      awaitingAssistant || sending || streamConnected || streamReplyActive || streamReplyTarget.trim().length > 0;
    if (!hasStreamingContext) {
      return turns;
    }

    if (awaitingAssistant) {
      return turns.slice(0, -1);
    }

    const streamText = streamReplyTarget.trim();
    if (!streamText) {
      return turns.slice(0, -1);
    }

    const lastText = last.content.trim();
    const overlaps =
      lastText === streamText || lastText.startsWith(streamText) || streamText.startsWith(lastText);

    if (overlaps) {
      return turns.slice(0, -1);
    }

    return turns;
  }, [awaitingAssistant, sending, streamConnected, streamReplyActive, streamReplyTarget, turns]);

  const lastAssistantReply = useMemo(() => {
    for (let idx = turnsForRender.length - 1; idx >= 0; idx -= 1) {
      const turn = turnsForRender[idx];
      if (turn.role === "assistant") {
        return turn.content;
      }
    }
    return "";
  }, [turnsForRender]);

  const showStreamReply =
    streamReply.trim().length > 0 &&
    (streamReplyActive || !lastAssistantReply || !lastAssistantReply.startsWith(streamReply));
  const showStreamStage = streamItems.length > 0 || sending || streamConnected || streamReplyActive || awaitingAssistant;
  const showStreamingBubble = showStreamReply || awaitingAssistant;
  const showMapCard = Boolean(
    mapArtifacts && (mapArtifacts.route || mapArtifacts.shops.length > 0 || mapArtifacts.view_payload)
  );
  const showEmptyState = turns.length === 0 && !showStreamingBubble && !showStreamStage && !showMapCard;
  const latestStreamItem = streamItems.length ? streamItems[streamItems.length - 1] : null;
  const composerBusy = sending || awaitingAssistant;
  const stageStatusText =
    latestStreamItem?.text
    ?? (streamConnected
      ? "等待阶段事件..."
      : sending
        ? "连接中..."
        : awaitingAssistant
          ? "等待会话继续..."
          : "阶段已结束");
  const stageStatusMeta = latestStreamItem ? formatTimeLabel(latestStreamItem.at) : "实时同步中...";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turnsForRender, loading, sending, streamItems, streamReply, awaitingAssistant, showStreamStage, showMapCard]);

  const lastAssistantIndex = useMemo(() => {
    for (let idx = turnsForRender.length - 1; idx >= 0; idx -= 1) {
      if (turnsForRender[idx].role === "assistant") {
        return idx;
      }
    }
    return -1;
  }, [turnsForRender]);

  const renderMapCard = (key: string, animationIndex: number) => {
    if (!showMapCard || !mapArtifacts) {
      return null;
    }
    return (
      <li
        key={key}
        className="chat-message assistant"
        style={{ animationDelay: `${Math.min(animationIndex, 8) * 45}ms` }}
      >
        <div className="chat-map-card-item">
          <AgentMapCard artifacts={mapArtifacts} />
        </div>
      </li>
    );
  };

  return (
    <div className="chat-view">
      <div className="chat-scroll">
        {showEmptyState ? (
          <div className="chat-empty">
            <p className="chat-empty-title">今天想查哪家机厅？</p>
            <p className="chat-empty-subtitle">你可以直接提问，也可以先点一个预设问题。</p>
            <div className="chat-quick-grid">
              {QUICK_PROMPTS.map((prompt) => (
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
          <ul className="chat-message-list">
            {turnsForRender.map((turn, index) => (
              <Fragment key={`${turn.created_at}-${index}`}>
                <li
                  className={`chat-message ${turn.role}`}
                  style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}
                >
                  <div className="chat-bubble">
                    {turn.role === "assistant" ? (
                      <MarkdownMessage content={turn.content} />
                    ) : (
                      <p className="chat-plain-text">{turn.content}</p>
                    )}
                    <small>{formatTimeLabel(turn.created_at)}</small>
                  </div>
                </li>
                {!showStreamingBubble && index === lastAssistantIndex
                  ? renderMapCard("agent-map-card-history", index + 1)
                  : null}
              </Fragment>
            ))}

            {showStreamStage ? (
              <li
                key="streaming-stage-status"
                className="chat-message assistant stream-event"
                style={{ animationDelay: `${Math.min(turnsForRender.length, 8) * 45}ms` }}
              >
                <div className="chat-bubble chat-event-bubble">
                  <p>执行阶段：{formatSubagentLabel(activeSubagent)}</p>
                  <small>{stageStatusText}</small>
                  <small>{stageStatusMeta}</small>
                </div>
              </li>
            ) : null}

            {showStreamingBubble ? (
              <li
                key="streaming-assistant"
                className="chat-message assistant streaming"
                style={{ animationDelay: `${Math.min(turnsForRender.length, 8) * 45}ms` }}
              >
                <div className="chat-bubble">
                  {streamReply.trim() ? (
                    <MarkdownMessage content={streamReply} className={streamReplyActive ? "is-streaming" : undefined} />
                  ) : (
                    <p className="chat-stream-placeholder">
                      正在生成回复...
                      {streamReplyActive ? <span className="chat-stream-caret" aria-hidden="true" /> : null}
                    </p>
                  )}
                  <small>{streamReplyActive ? "生成中..." : "已生成"}</small>
                </div>
              </li>
            ) : null}

            {showStreamingBubble || lastAssistantIndex < 0
              ? renderMapCard("agent-map-card-streaming", turnsForRender.length + 1)
              : null}
          </ul>
        )}

        {loading ? <p className="chat-loading">加载会话中...</p> : null}
        <div ref={endRef} />
      </div>

      {error ? <div className="chat-error">{error}</div> : null}

      <form className="chat-composer" onSubmit={(event) => void onSubmit(event)}>
        <input
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          placeholder="尽管问，带图也行"
          disabled={composerBusy}
        />
        <button type="submit" disabled={composerBusy || inputValue.trim().length === 0}>
          {sending ? "发送中..." : awaitingAssistant ? "处理中..." : "发送"}
        </button>
      </form>
    </div>
  );
}
