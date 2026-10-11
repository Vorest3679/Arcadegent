import { useEffect } from "react";
import { useDelayedUnmount } from "../../hooks/useDelayedUnmount";
import { mapSummary } from "../../lib/mapDock";
import { useRunStore } from "../../stores/runStore";
import type { ChatMapArtifacts } from "../../types";
import { AgentMapCard } from "../map/AgentMapCard";

// Map area (right of the chat on desktop, below it on small screens); shows
// only the latest map. It starts folded everywhere: a new map only highlights
// the entry button, and the user opens it.
export function MapDock({ artifacts, arrivalKey }: { artifacts: ChatMapArtifacts | null; arrivalKey: string | null }) {
  const open = useRunStore((state) => state.mapDockOpen);
  const highlight = useRunStore((state) => state.mapDockHighlight);
  const setDock = useRunStore((state) => state.setMapDock);
  // Stays laid out as "open" while the exit animation plays.
  const { mounted, closing } = useDelayedUnmount(open);

  useEffect(() => {
    if (!arrivalKey) {
      setDock({ open: false, highlight: false });
      return;
    }
    if (useRunStore.getState().mapDockClosedKey === arrivalKey) {
      return;
    }
    setDock({ highlight: true });
  }, [arrivalKey, setDock]);

  if (!artifacts) {
    return null;
  }
  return (
    <section
      className={`map-dock${mounted ? " is-open" : ""}${closing ? " is-closing" : ""}${highlight && !open ? " is-highlight" : ""}`}
      data-testid="map-dock"
    >
      <button
        type="button"
        className="map-dock-bar"
        aria-expanded={open}
        aria-label={`${open ? "收起" : "展开"}地图：${mapSummary(artifacts)}`}
        title={mapSummary(artifacts)}
        onClick={() => setDock({ open: !open, highlight: false, closedKey: open ? arrivalKey : null })}
      >
        <svg className="map-dock-map-icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m9 4-6 3v14l6-3 6 3 6-3V4l-6 3-6-3Z" />
          <path d="M9 4v14m6-11v14" />
        </svg>
        <svg className="map-dock-chevron" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m11 5-7 7 7 7m9-14-7 7 7 7" />
        </svg>
      </button>
      {mounted ? (
        <div className="map-dock-body">
          <AgentMapCard artifacts={artifacts} />
        </div>
      ) : null}
    </section>
  );
}
