import type { ChatSessionSummary } from "../types";

function normalizeText(value: string): string {
  return value.trim().toLocaleLowerCase();
}

// Pinyin columns are delivered with separators ("xing-ji-..."). The search
// query may be typed the same way, or without separators at all.
function compactPinyin(value: string): string {
  return value.toLocaleLowerCase().replace(/[^a-z0-9]/g, "");
}

export function sessionMatchesKeyword(session: ChatSessionSummary, rawKeyword: string): boolean {
  const keyword = normalizeText(rawKeyword);
  if (!keyword) {
    return true;
  }
  if (normalizeText(session.title).includes(keyword)) {
    return true;
  }
  if (session.preview && normalizeText(session.preview).includes(keyword)) {
    return true;
  }

  const pinyinKeyword = compactPinyin(keyword);
  for (const shop of session.artifact_shops ?? []) {
    if (normalizeText(shop.name).includes(keyword)) {
      return true;
    }
    // Separate branch: a Chinese name cannot contain a latin pinyin query, so
    // only pinyin-bearing artifact shops participate in this comparison.
    if (pinyinKeyword && shop.name_pinyin) {
      if (compactPinyin(shop.name_pinyin).includes(pinyinKeyword)) {
        return true;
      }
    }
  }
  return false;
}

export function filterSessionsByKeyword(
  sessions: ChatSessionSummary[],
  keyword: string
): ChatSessionSummary[] {
  return sessions.filter((session) => sessionMatchesKeyword(session, keyword));
}
