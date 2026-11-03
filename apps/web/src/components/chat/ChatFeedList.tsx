import { useEffect, useMemo, useState } from "react";
import type { FeedItem, MapItem, ToolItem } from "@/lib/chatFeed";
import { useChatFeedStore } from "@/stores/chatFeedStore";
import { useRunStore } from "@/stores/runStore";
import { useSessionStore } from "@/stores/sessionStore";
import { AssistantMessage, ErrorMessage, MapArtifactCard, RunIndicator, ToolGroup, UserMessage } from "./FeedItems";

type Segment =
  | { kind: "item"; key: string; item: Exclude<FeedItem, ToolItem | MapItem> }
  | { kind: "tools"; key: string; round: number; items: ToolItem[] }
  | { kind: "map"; key: string; item: MapItem };

// Display order of the feed: consecutive tool calls form one group, and a
// round's map goes after everything else of that round (a map that appears
// mid-run would otherwise split a tool group).
function toSegments(items: FeedItem[]): Segment[] {
  const segments: Segment[] = [];
  let pendingMap: MapItem | null = null;
  const flushMap = () => {
    if (pendingMap) {
      segments.push({ kind: "map", key: pendingMap.key, item: pendingMap });
      pendingMap = null;
    }
  };
  for (const item of items) {
    if (pendingMap && item.round !== pendingMap.round) {
      flushMap();
    }
    if (item.kind === "map") {
      pendingMap = item;
      continue;
    }
    if (item.kind === "tool") {
      const last = segments[segments.length - 1];
      if (last?.kind === "tools" && last.round === item.round) {
        last.items.push(item);
      } else {
        // Keyed by its first call: stable while the group grows and after the hand-over.
        segments.push({ kind: "tools", key: `g:${item.key}`, round: item.round, items: [item] });
      }
      continue;
    }
    segments.push({ kind: "item", key: item.key, item });
  }
  flushMap();
  return segments;
}

// Renders the feed in order. Keys come from the feed and survive the
// hand-over from the live run to the loaded history.
export function ChatFeedList({ runLive, onOpenMap }: { runLive: boolean; onOpenMap: (key: string) => void }) {
  const items = useChatFeedStore((state) => state.items);
  const runOpen = useRunStore((state) => state.runOpen);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  // Groups the user opened or closed by hand; everything else follows the default.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const segments = useMemo(() => toSegments(items), [items]);
  const last = items[items.length - 1];
  const lastRound = last?.round ?? -1;

  useEffect(() => setToggled({}), [activeSessionId]);

  // Nothing to indicate while text streams or once the round has its answer
  // (the run is then only waiting for the hand-over).
  const answering = items.some(
    (item) => item.kind === "assistant" && item.round === lastRound && (item.streaming || item.final)
  );
  return (
    <ol className="flex flex-col gap-3" data-testid="chat-feed">
      {segments.map((segment) => {
        const round = segment.kind === "tools" ? segment.round : segment.item.round;
        // The round whose stream is still open: its tools stay expanded.
        const liveRound = runOpen && round === lastRound;
        if (segment.kind === "tools") {
          const open = toggled[segment.key] ?? liveRound;
          return (
            <li key={segment.key}>
              <ToolGroup
                items={segment.items}
                open={open}
                onToggle={() => setToggled((previous) => ({ ...previous, [segment.key]: !open }))}
              />
            </li>
          );
        }
        if (segment.kind === "map") {
          return (
            <li key={segment.key}>
              <MapArtifactCard item={segment.item} onOpen={onOpenMap} />
            </li>
          );
        }
        const { item } = segment;
        return (
          <li key={segment.key} className={item.kind === "user" && item.round > 0 ? "mt-4" : undefined}>
            {item.kind === "user" ? <UserMessage item={item} /> : null}
            {item.kind === "assistant" ? <AssistantMessage item={item} /> : null}
            {item.kind === "error" ? <ErrorMessage item={item} /> : null}
          </li>
        );
      })}
      {runLive && !answering ? (
        <li>
          <RunIndicator />
        </li>
      ) : null}
    </ol>
  );
}
