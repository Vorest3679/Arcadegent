const CLIENT_ID_KEY = "arcadegent.chat.clientId.v1";
const ACTIVE_SESSION_ID_KEY = "arcadegent.chat.activeSessionId.v1";

let fallbackClientId: string | null = null;
let fallbackActiveSessionId: string | null = null;

function makeLocalId(prefix: string): string {
  const cryptoApi = typeof globalThis.crypto !== "undefined" ? globalThis.crypto : null;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return `${prefix}_${cryptoApi.randomUUID().replace(/-/g, "").slice(0, 16)}`;
  }
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function readLocalStorage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function normalizeStoredId(value: string | null): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

export function getChatClientId(): string {
  const storage = readLocalStorage();
  if (!storage) {
    if (!fallbackClientId) {
      fallbackClientId = makeLocalId("c");
    }
    return fallbackClientId;
  }

  const existing = normalizeStoredId(storage.getItem(CLIENT_ID_KEY));
  if (existing) {
    return existing;
  }

  const created = makeLocalId("c");
  try {
    storage.setItem(CLIENT_ID_KEY, created);
  } catch {
    fallbackClientId = created;
  }
  return created;
}

export function readStoredActiveSessionId(): string | null {
  const storage = readLocalStorage();
  if (!storage) {
    return fallbackActiveSessionId;
  }
  return normalizeStoredId(storage.getItem(ACTIVE_SESSION_ID_KEY));
}

export function writeStoredActiveSessionId(sessionId: string | null): void {
  fallbackActiveSessionId = normalizeStoredId(sessionId);
  const storage = readLocalStorage();
  if (!storage) {
    return;
  }
  try {
    if (fallbackActiveSessionId) {
      storage.setItem(ACTIVE_SESSION_ID_KEY, fallbackActiveSessionId);
    } else {
      storage.removeItem(ACTIVE_SESSION_ID_KEY);
    }
  } catch {
    // Keep the in-memory fallback so the active tab still behaves consistently.
  }
}
