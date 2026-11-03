import { useEffect, useRef, useState } from "react";

// Reveals streamed text gradually instead of in network-sized jumps. While
// `active`, each frame catches up a fraction of what is still hidden; once
// inactive (or when the text is replaced rather than extended) it shows the
// target at once.
export function useSmoothText(target: string, active: boolean): string {
  const [shown, setShown] = useState(target);
  const shownRef = useRef(target);

  useEffect(() => {
    const show = (value: string) => {
      shownRef.current = value;
      setShown(value);
    };
    if (!active || !target.startsWith(shownRef.current)) {
      show(target);
      return;
    }
    let frame = 0;
    const step = () => {
      const current = shownRef.current;
      if (current.length >= target.length) {
        return;
      }
      const remaining = target.length - current.length;
      show(target.slice(0, current.length + Math.max(1, Math.ceil(remaining / 6))));
      frame = window.requestAnimationFrame(step);
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
  }, [target, active]);

  return shown;
}
