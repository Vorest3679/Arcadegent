import type {
  ChatRequest,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionDispatch,
  ChatSessionSummary
} from "../types";
import { assertChatSessionDetail } from "../lib/sessionSchema";
import { buildUrl, deleteJson, fetchJson, postJson } from "./http";

export async function sendChat(payload: ChatRequest): Promise<ChatResponse> {
  return postJson<ChatResponse>("/api/chat", payload);
}

export async function dispatchChatSession(payload: ChatRequest): Promise<ChatSessionDispatch> {
  return postJson<ChatSessionDispatch>("/api/chat/sessions", payload);
}

export async function cancelChatSession(
  sessionId: string,
  runId: string,
  clientId?: string
): Promise<ChatSessionDetail> {
  const detail = await postJson<unknown>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`,
    {},
    { client_id: clientId }
  );
  return assertChatSessionDetail(detail);
}

export function buildChatStreamUrl(sessionId: string, runId: string, afterId?: number, clientId?: string): string {
  return buildUrl(`/api/stream/${encodeURIComponent(sessionId)}`, {
    run_id: runId,
    client_id: clientId,
    last_event_id: typeof afterId === "number" ? afterId : undefined
  });
}

export async function listChatSessions(limit = 40, clientId?: string): Promise<ChatSessionSummary[]> {
  return fetchJson<ChatSessionSummary[]>(buildUrl("/api/chat/sessions", { limit, client_id: clientId }));
}

export async function getChatSession(sessionId: string, clientId?: string): Promise<ChatSessionDetail> {
  const detail = await fetchJson<unknown>(
    buildUrl(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { client_id: clientId })
  );
  return assertChatSessionDetail(detail);
}

export async function deleteChatSession(sessionId: string, clientId?: string): Promise<void> {
  return deleteJson(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { client_id: clientId });
}

