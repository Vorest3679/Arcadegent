import { useMemo } from "react";
import { buildRounds } from "../../lib/chatRounds";
import { useRunStore } from "../../stores/runStore";
import { useSessionStore } from "../../stores/sessionStore";
import { RoundItem } from "./RoundItem";

export function RoundList({ runLive, streamReply, streamReplyActive }: {
  runLive: boolean;
  streamReply: string;
  streamReplyActive: boolean;
}) {
  const turns = useSessionStore((state) => state.turns);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const liveRound = useRunStore((state) => state.liveRound);
  const rounds = useMemo(() => buildRounds(turns, liveRound, runLive), [turns, liveRound, runLive]);
  const lastIndex = rounds.length - 1;
  const sessionKey = activeSessionId ?? "new";
  return (
    <ul className="chat-message-list">
      {rounds.map((round, index) => (
        <RoundItem
          key={`${sessionKey}-round-${index}`}
          round={round}
          index={index}
          live={runLive && index === lastIndex && !round.reply}
          streamReply={runLive && index === lastIndex ? streamReply : ""}
          streamReplyActive={runLive && index === lastIndex && streamReplyActive}
        />
      ))}
    </ul>
  );
}
