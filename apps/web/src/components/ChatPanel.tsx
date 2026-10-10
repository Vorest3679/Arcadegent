import { FormEvent, ReactNode, useEffect, useRef } from "react";
import { formatSubagentLabel, formatTimeLabel } from "../lib/chatStream";
import { useAppStore } from "../stores/appStore";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ChatHistoryTurn } from "../types";
import { AgentMapCard } from "./map/AgentMapCard";

const QUICK_PROMPTS = [
  "帮我找找适合下班后去的机厅",
  "南京最多机台的机厅是哪家？",
  "给我一条从当前位置到最近机厅的路线建议"
];

type ChatPanelProps = {
  onSubmit: (event: FormEvent) => Promise<void>;
  onQuickAsk: (prompt: string) => void;
  streamReply: string;
  streamReplyActive: boolean;
};

export function ChatPanel({
  onSubmit,
  onQuickAsk,
  streamReply,
  streamReplyActive
}: ChatPanelProps) {
  const turns = useAppStore((state) => state.turns);
  const activeSessionId = useAppStore((state) => state.activeSessionId);
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
  const activeRunId = useAppStore((state) => state.activeRunId);
  const committedRunId = useAppStore((state) => state.committedRunId);
  const sealedOutputs = useAppStore((state) => state.sealedOutputs);
  const endRef = useRef<HTMLDivElement | null>(null);

  // runLive为了判断当前是否有正在进行的执行阶段，
  // 主要用于过渡阶段显示状态、流式气泡和地图卡片等UI元素。
  //（如等待assistant,RunId暂未committed时）
  const runLive = awaitingAssistant || (activeRunId !== null && activeRunId !== committedRunId);
  const showStreamStage = runLive;
  const showStreamingBubble = runLive;
  const showMapCard = Boolean(
    runLive && mapArtifacts && (mapArtifacts.route || mapArtifacts.shops.length > 0 || mapArtifacts.view_payload)
  );
  const showEmptyState = turns.length === 0 && !runLive;
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
  const lastUserIndex = turns.reduce(
    (lastIndex, turn, index) => turn.role === "user" ? index : lastIndex,
    -1
  );
  const stageStatus = showStreamStage ? (
    <li key="streaming-stage-status" className="chat-message assistant stream-event">
      <div className="chat-bubble chat-event-bubble">
        <p>执行阶段：{formatSubagentLabel(activeSubagent)}</p>
        <small>{stageStatusText}</small>
        <small>{stageStatusMeta}</small>
      </div>
    </li>
  ) : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, loading, sending, streamItems, streamReply, sealedOutputs, awaitingAssistant, showStreamStage, showMapCard]);

  const renderMapCard = (key: string, animationIndex: number, artifacts = mapArtifacts) => {
    if (!artifacts) {
      return null;
    }
    return (
      <li
        key={key}
        className="chat-message assistant"
        style={{ animationDelay: `${Math.min(animationIndex, 8) * 45}ms` }}
      >
        <div className="chat-map-card-item">
          <AgentMapCard artifacts={artifacts} />
        </div>
      </li>
    );
  };

  // One flat keyed list. Live items and history items share keys, so a run that
  // is handed over to history keeps its DOM nodes (no remount, no replayed
  // entrance animation, no map card reload).
  const sessionKey = activeSessionId ?? "new";
  const renderIntermediate = (key: string, text: string, animationIndex: number) => (
    <li
      key={key}
      className="chat-message assistant intermediate"
      style={{ animationDelay: `${Math.min(animationIndex, 8) * 45}ms` }}
    >
      <div className="chat-bubble">
        <MarkdownMessage content={text} />
        <small>中间回复</small>
      </div>
    </li>
  );
  // While the round streams, live sealed outputs are the source (a replay
  // rebuilds all of them); history steps only fill in before the replay arrives.
  const roundTexts = (turn: ChatHistoryTurn, index: number): string[] => {
    if (turn.role !== "user") {
      return [];
    }
    if (runLive && index === lastUserIndex && sealedOutputs.length > 0) {
      return sealedOutputs.map((output) => output.text);
    }
    return (turn.steps ?? []).flatMap((step) => step.kind === "text" ? [step.content] : []);
  };

  const messageItems: ReactNode[] = [];
  if (lastUserIndex < 0 && stageStatus) {
    messageItems.push(stageStatus);
  }
  turns.forEach((turn, index) => {
    messageItems.push(
      <li
        key={`${sessionKey}-${turn.role}-${index}`}
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
    );
    if (index === lastUserIndex && stageStatus) {
      messageItems.push(stageStatus);
    }
    roundTexts(turn, index).forEach((text, order) => {
      messageItems.push(renderIntermediate(`${sessionKey}-step-${index}-${order}`, text, index + order + 1));
    });
    if (turn.role === "assistant" && turn.map_artifacts) {
      messageItems.push(renderMapCard(`${sessionKey}-map-${index}`, index + 1, turn.map_artifacts));
    }
  });
  if (showStreamingBubble) {
    // Same key as the history turn this run will become (the next index).
    messageItems.push(
      <li
        key={`${sessionKey}-assistant-${turns.length}`}
        className="chat-message assistant streaming"
        style={{ animationDelay: `${Math.min(turns.length, 8) * 45}ms` }}
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
    );
    if (showMapCard) {
      messageItems.push(renderMapCard(`${sessionKey}-map-${turns.length}`, turns.length + 1, mapArtifacts));
    }
  }

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
          <ul className="chat-message-list">{messageItems}</ul>
        )}

        {loading ? <p className="chat-loading">加载会话中...</p> : null}
        <div ref={endRef} />
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
