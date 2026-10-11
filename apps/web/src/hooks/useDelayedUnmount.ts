import { useEffect, useState } from "react";

// Keeps content mounted for `ms` after `open` turns false so an exit animation
// can play. `closing` is true during that window.
export function useDelayedUnmount(open: boolean, ms = 200): { mounted: boolean; closing: boolean } {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    const timer = window.setTimeout(() => setMounted(false), ms);
    return () => window.clearTimeout(timer);
  }, [open, ms]);
  return { mounted: open || mounted, closing: !open && mounted };
}
