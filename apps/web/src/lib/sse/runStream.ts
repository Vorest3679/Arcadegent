import type { ChatRunStatus, ChatStreamEnvelope } from "../../types";

// Subscribes to the event stream of one run. Owns the transport concerns:
// envelope checks, cursor de-duplication, bounded reconnects and assembling
// `assistant.token` deltas per output_id. Business events pass through as-is.

const TERMINAL_STATUSES: ReadonlySet<ChatRunStatus> = new Set(["completed", "failed", "cancelled"]);
const RUN_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "running",
  "cancelling",
  "completed",
  "failed",
  "cancelled"
]);

export type RunStreamHandlers = {
  onEvent(envelope: ChatStreamEnvelope): void;
  onText(outputId: string, text: string, delta: string): void;
  // A new output_id arrived; the previous output is final from here on.
  onOutputSealed(outputId: string, text: string): void;
  onState(status: ChatRunStatus): void;
  onReset(): void;
  onConnection(connected: boolean): void;
  onRetry(attempt: number, maxRetries: number): void;
  onGiveUp(): void;
};

export type RunStream = {
  close(): void;
};

export function isTerminalRunStatus(status: ChatRunStatus | null | undefined): boolean {
  return status ? TERMINAL_STATUSES.has(status) : false;
}

function parseEnvelope(raw: string, sessionId: string, runId: string): ChatStreamEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") {
    return null;
  }
  const envelope = parsed as Partial<ChatStreamEnvelope>;
  if (typeof envelope.id !== "number" || !Number.isFinite(envelope.id)) {
    return null;
  }
  if (envelope.session_id !== sessionId || envelope.run_id !== runId) {
    return null;
  }
  if (envelope.kind !== "event" && envelope.kind !== "control") {
    return null;
  }
  if (typeof envelope.event !== "string" || typeof envelope.data !== "object" || envelope.data === null) {
    return null;
  }
  if (envelope.status !== undefined && !RUN_STATUSES.has(envelope.status)) {
    return null;
  }
  return envelope as ChatStreamEnvelope;
}

export function openRunStream(options: {
  url: (afterId?: number) => string;
  sessionId: string;
  runId: string;
  handlers: RunStreamHandlers;
  maxRetries?: number;
}): RunStream {
  const { url, sessionId, runId, handlers } = options;
  const maxRetries = options.maxRetries ?? 3;

  let source: EventSource | null = null;
  let retryTimer: number | null = null;
  let lastId: number | undefined;
  let attempts = 0;
  let closed = false;
  let outputId: string | null = null;
  let outputText = "";

  function close(): void {
    closed = true;
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
    if (source) {
      source.close();
      source = null;
    }
  }

  function applyToken(envelope: ChatStreamEnvelope): void {
    const delta = envelope.data.delta;
    if (typeof delta !== "string" || !delta) {
      return;
    }
    const nextOutputId = envelope.output_id ?? "";
    if (outputId !== null && outputId !== nextOutputId) {
      handlers.onOutputSealed(outputId, outputText);
      outputText = "";
    }
    outputId = nextOutputId;
    outputText += delta;
    handlers.onText(nextOutputId, outputText, delta);
  }

  function handleMessage(message: MessageEvent<string>, current: EventSource): void {
    if (closed || source !== current || !message.data) {
      return;
    }
    const envelope = parseEnvelope(message.data, sessionId, runId);
    if (!envelope) {
      return;
    }
    // Replayed events after a reconnect overlap what was already delivered.
    if (lastId !== undefined && envelope.id <= lastId) {
      return;
    }
    lastId = envelope.id;
    // Count failures between delivered events, not TCP opens, so a connection
    // that keeps closing right after opening still runs out of retries.
    attempts = 0;

    if (envelope.kind === "control") {
      if (envelope.event === "stream.reset") {
        handlers.onReset();
        return;
      }
      if (envelope.event === "run.state" && envelope.status) {
        if (isTerminalRunStatus(envelope.status)) {
          // The terminal state is the last frame of the run.
          close();
          handlers.onConnection(false);
        }
        handlers.onState(envelope.status);
      }
      return;
    }

    if (envelope.event === "assistant.token") {
      applyToken(envelope);
    }
    handlers.onEvent(envelope);
  }

  function connect(): void {
    if (closed) {
      return;
    }
    const current = new EventSource(url(lastId));
    source = current;

    current.onopen = () => {
      if (!closed && source === current) {
        handlers.onConnection(true);
      }
    };
    current.addEventListener("message", (event) => handleMessage(event as MessageEvent<string>, current));
    current.onerror = () => {
      if (closed || source !== current) {
        return;
      }
      current.close();
      source = null;
      handlers.onConnection(false);
      if (attempts >= maxRetries) {
        close();
        handlers.onGiveUp();
        return;
      }
      attempts += 1;
      handlers.onRetry(attempts, maxRetries);
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        connect();
      }, 500 * 2 ** (attempts - 1));
    };
  }

  connect();
  return { close };
}
