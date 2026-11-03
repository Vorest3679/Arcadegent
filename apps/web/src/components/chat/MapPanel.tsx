import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import type { MapItem } from "@/lib/chatFeed";
import { mapSummary } from "@/lib/mapArtifacts";
import { useChatFeedStore } from "@/stores/chatFeedStore";
import { AgentMapCard } from "../map/AgentMapCard";

// Large view of one map item of the feed: a side panel on wide screens, a
// bottom drawer on phones. It reads the item from the feed by key, so a live
// map (e.g. a route arriving) updates while the panel is open.
export function MapPanel({ itemKey, onClose }: { itemKey: string | null; onClose: () => void }) {
  const wide = useMediaQuery("(min-width: 1024px)");
  const item = useChatFeedStore((state) =>
    itemKey ? state.items.find((entry): entry is MapItem => entry.kind === "map" && entry.key === itemKey) : undefined
  );
  const open = Boolean(item);
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side={wide ? "right" : "bottom"}
        data-testid="map-panel"
        className={
          wide
            ? "w-full gap-0 overflow-y-auto sm:max-w-xl"
            : "max-h-[88dvh] gap-0 overflow-y-auto rounded-t-2xl pb-[var(--safe-bottom)]"
        }
      >
        <SheetHeader className="pr-12">
          <SheetTitle>地图</SheetTitle>
          <SheetDescription>{item ? mapSummary(item.artifacts) : ""}</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-4">{item ? <AgentMapCard artifacts={item.artifacts} /> : null}</div>
      </SheetContent>
    </Sheet>
  );
}
