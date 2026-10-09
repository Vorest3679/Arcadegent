import { useCallback, useRef, useState } from "react";

export function useStreamReply() {
  const [streamReplyTarget, setStreamReplyTarget] = useState("");
  const [streamReplyDisplay, setStreamReplyDisplay] = useState("");

  const streamReplyTargetRef = useRef("");
  const streamReplyDisplayRef = useRef("");
  const streamReplyQueueRef = useRef<string[]>([]);
  const streamReplyFlushTimerRef = useRef<number | null>(null);

  const cancelStreamReplyFlush = useCallback(() => {
    if (streamReplyFlushTimerRef.current !== null) {
      window.clearTimeout(streamReplyFlushTimerRef.current);
      streamReplyFlushTimerRef.current = null;
    }
  }, []);

  const writeStreamReplyTarget = useCallback((value: string) => {
    streamReplyTargetRef.current = value;
    setStreamReplyTarget(value);
  }, []);

  const writeStreamReplyDisplay = useCallback((value: string) => {
    streamReplyDisplayRef.current = value;
    setStreamReplyDisplay(value);
  }, []);

  const flushStreamReplyQueue = useCallback(() => {
    cancelStreamReplyFlush();
    const nextDelta = streamReplyQueueRef.current.shift();

    if (nextDelta) {
      writeStreamReplyDisplay(streamReplyDisplayRef.current + nextDelta);
    } else if (streamReplyDisplayRef.current !== streamReplyTargetRef.current) {
      writeStreamReplyDisplay(streamReplyTargetRef.current);
    }

    if (streamReplyQueueRef.current.length > 0) {
      streamReplyFlushTimerRef.current = window.setTimeout(flushStreamReplyQueue, 28);
    }
  }, [cancelStreamReplyFlush, writeStreamReplyDisplay]);

  const enqueueStreamReplyDelta = useCallback((delta: string) => {
    if (!delta) {
      return;
    }
    streamReplyQueueRef.current.push(delta);
    if (streamReplyFlushTimerRef.current === null) {
      streamReplyFlushTimerRef.current = window.setTimeout(flushStreamReplyQueue, 28);
    }
  }, [flushStreamReplyQueue]);

  const resetStreamReply = useCallback(() => {
    cancelStreamReplyFlush();
    streamReplyQueueRef.current = [];
    writeStreamReplyTarget("");
    writeStreamReplyDisplay("");
  }, [cancelStreamReplyFlush, writeStreamReplyDisplay, writeStreamReplyTarget]);

  // `text` is the assembled output so far; only `delta` is queued for display.
  const appendStreamReply = useCallback((text: string, delta: string) => {
    writeStreamReplyTarget(text);
    enqueueStreamReplyDelta(delta);
  }, [enqueueStreamReplyDelta, writeStreamReplyTarget]);

  const syncStreamReply = useCallback((value: string) => {
    cancelStreamReplyFlush();
    streamReplyQueueRef.current = [];
    writeStreamReplyTarget(value);
    writeStreamReplyDisplay(value);
  }, [cancelStreamReplyFlush, writeStreamReplyDisplay, writeStreamReplyTarget]);

  return {
    appendStreamReply,
    cancelStreamReplyFlush,
    resetStreamReply,
    streamReplyDisplay,
    streamReplyTarget,
    syncStreamReply
  };
}
