export type QueryParams = Record<string, string | number | boolean | undefined | null>;

function resolveApiBase(): string {
  const fallback = typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";
  const configured = import.meta.env.VITE_API_BASE?.trim();
  if (!configured) {
    return fallback;
  }
  if (/^https?:\/\//i.test(configured) || configured.startsWith("/")) {
    return new URL(configured, fallback).toString();
  }
  return `http://${configured}`;
}

const API_BASE = resolveApiBase();

export function buildUrl(path: string, query?: QueryParams): string {
  const url = new URL(path, API_BASE);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && `${value}`.length > 0) {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
}

export async function parseJsonResponse<T>(resp: Response, requestLabel: string): Promise<T> {
  const contentType = resp.headers.get("content-type")?.toLowerCase() ?? "";
  const text = await resp.text();

  if (!resp.ok) {
    const detail = text.trim().slice(0, 180);
    throw new Error(`HTTP ${resp.status}: ${requestLabel}${detail ? ` - ${detail}` : ""}`);
  }

  if (!contentType.includes("application/json")) {
    const preview = text.trim().slice(0, 120);
    throw new Error(
      `Expected JSON from ${requestLabel}, got ${contentType || "unknown content type"}${preview ? ` - ${preview}` : ""}`
    );
  }

  try {
    return JSON.parse(text) as T;
  } catch (error) {
    const preview = text.trim().slice(0, 120);
    throw new Error(
      `Invalid JSON from ${requestLabel}${preview ? ` - ${preview}` : ""}${error instanceof Error ? ` (${error.message})` : ""}`
    );
  }
}

export async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  return parseJsonResponse<T>(resp, url);
}

export async function postJson<T>(
  path: string,
  payload: unknown,
  query?: QueryParams
): Promise<T> {
  const resp = await fetch(buildUrl(path, query), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  return parseJsonResponse<T>(resp, path);
}

export async function deleteJson(
  path: string,
  query?: QueryParams
): Promise<void> {
  const resp = await fetch(buildUrl(path, query), { method: "DELETE" });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
}

