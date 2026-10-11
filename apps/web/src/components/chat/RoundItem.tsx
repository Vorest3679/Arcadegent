import { memo, useDeferredValue } from "react";
import type { ChatRound } from "../../lib/chatRounds";
import { formatSubagentLabel, formatTimeLabel } from "../../lib/sse/chatStream";
import { useRunStore } from "../../stores/runStore";
import { MarkdownMessage } from "../MarkdownMessage";
import { RoundSteps } from "./RoundSteps";

type RoundItemProps = {
  round: ChatRound;
  index: number;
  // The round whose run is still displayed: shows progress and the streaming bubble.
  live: boolean;
  streamReply: string;
  streamReplyActive: boolean;
};

function StageStatus() {
  const activeSubagent = useRunStore((state) => state.activeSubagent);
  const streamItems = useRunStore((state) => state.streamItems);
  const streamConnected = useRunStore((state) => state.streamConnected);
  const awaitingAssistant = useRunStore((state) => state.awaitingAssistant);
  const latest = streamItems.length ? streamItems[streamItems.length - 1] : null;
  const text = latest?.text ?? (streamConnected ? "等待阶段事件..." : awaitingAssistant ? "连接中..." : "阶段已结束");
  return (
    <li className="chat-message assistant stream-event">
      <div className="chat-bubble chat-event-bubble">
        <p>执行阶段：{formatSubagentLabel(activeSubagent)}</p>
        <small>{text}</small>
        <small>{latest ? formatTimeLabel(latest.at) : "实时同步中..."}</small>
      </div>
    </li>
  );
}

// Children sit at fixed positions, so a live round that is handed over to
// history keeps its DOM nodes (no remount, no replayed entrance animation).
// Memoized: while a reply streams, only the live round re-renders (the list
// passes the stream text to that round alone).
export const RoundItem = memo(function RoundItem({ round, index, live, streamReply, streamReplyActive }: RoundItemProps) {
  // Markdown of the whole text is re-parsed on every change; skip the frames
  // the browser has no time for.
  const shownReply = useDeferredValue(streamReply);
  const delay = (offset: number) => ({ animationDelay: `${Math.min(index * 2 + offset, 8) * 45}ms` });
  return (
    <>
      <li className="chat-message user" style={delay(0)}>
        <div className="chat-bubble">
          <p className="chat-plain-text">{round.user.content}</p>
          <small>{formatTimeLabel(round.user.created_at)}</small>
        </div>
      </li>
      {live ? <StageStatus /> : null}
      {round.steps.length > 0 ? (
        <li className="chat-message assistant round-steps-item" style={delay(1)}>
          <RoundSteps steps={round.steps} running={round.status === "running"} />
        </li>
      ) : null}
      {live ? (
        <li className="chat-message assistant streaming" style={delay(2)}>
          <div className="chat-bubble">
            {shownReply.trim() ? (
              <MarkdownMessage content={shownReply} className={streamReplyActive ? "is-streaming" : undefined} />
            ) : (
              <p className="chat-stream-placeholder">
                正在生成回复...
                {streamReplyActive ? <span className="chat-stream-caret" aria-hidden="true" /> : null}
              </p>
            )}
            <small>{streamReplyActive ? "生成中..." : "已生成"}</small>
          </div>
        </li>
      ) : round.reply ? (
        <li className="chat-message assistant" style={delay(2)}>
          <div className="chat-bubble">
            <MarkdownMessage content={round.reply.content} />
            <small>{formatTimeLabel(round.reply.created_at)}</small>
          </div>
        </li>
      ) : null}
    </>
  );
});
