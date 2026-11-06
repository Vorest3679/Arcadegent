import { expect, test, type Page } from "@playwright/test";
import { installApiMocks } from "./test-support";

const SESSIONS = [
  {
    session_id: "s_a",
    title: "会话 A",
    preview: "最新会话",
    intent: "search",
    status: "completed",
    turn_count: 2,
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:30Z"
  },
  {
    session_id: "s_b",
    title: "会话 B",
    preview: "中间会话",
    intent: "search",
    status: "completed",
    turn_count: 2,
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:20Z"
  },
  {
    session_id: "s_c",
    title: "会话 C",
    preview: "最早会话",
    intent: "search",
    status: "completed",
    turn_count: 2,
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:10Z"
  }
];

function detailFor(sessionId: string) {
  const summary = SESSIONS.find((item) => item.session_id === sessionId) ?? SESSIONS[0];
  return {
    session_id: summary.session_id,
    intent: "search",
    active_subagent: "main_agent",
    status: "completed",
    current_run: null,
    last_error: null,
    reply: null,
    shops: [],
    route: null,
    client_location: null,
    destination: null,
    view_payload: null,
    turn_count: summary.turn_count,
    created_at: summary.created_at,
    updated_at: summary.updated_at,
    turns: []
  };
}

// Backend stand-in: pins live in this map and survive a page reload, so the
// reload assertions below exercise server persistence rather than local storage.
async function installSessionMocks(page: Page) {
  const pins: Record<string, string | null> = {};
  let pinClock = 0;

  await page.route("**/api/chat/sessions/*/pin**", async (route) => {
    const sessionId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    const body = route.request().postDataJSON() as { pinned?: boolean };
    const summary = SESSIONS.find((item) => item.session_id === sessionId);
    if (!summary) {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    pinClock += 1;
    pins[sessionId] = body.pinned ? `2026-11-06T10:00:0${pinClock}.000Z` : null;
    await route.fulfill({ json: { ...summary, pinned_at: pins[sessionId] } });
  });
  await page.route("**/api/chat/sessions/*", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }
    const sessionId = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    await route.fulfill({ json: detailFor(sessionId) });
  });
  await page.route("**/api/chat/sessions?**", async (route) => {
    await route.fulfill({
      json: SESSIONS.map((item) => ({ ...item, pinned_at: pins[item.session_id] ?? null }))
    });
  });
}

function rowMenu(page: Page, title: string) {
  return page.getByRole("menu", { name: `会话操作：${title}`, exact: true });
}

async function openRowMenu(page: Page, title: string) {
  await page.getByRole("button", { name: `会话操作：${title}`, exact: true }).click();
  await expect(rowMenu(page, title)).toBeVisible();
  return rowMenu(page, title);
}

// Chooses an action of the row menu and waits for the pin write it triggers,
// so the next interaction cannot race the optimistic update's server settle.
async function chooseRowAction(page: Page, title: string, action: string) {
  const menu = await openRowMenu(page, title);
  const pinnedWrite = page.waitForResponse(
    (response) => response.url().includes("/pin") && response.request().method() === "PUT"
  );
  await menu.getByRole("menuitem", { name: action, exact: true }).click();
  await pinnedWrite;
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
  await installSessionMocks(page);
  await page.goto("/");
  await expect(page.getByTestId("session-row")).toHaveCount(3);
});

test("置顶由后端保存，刷新后仍排在列表最前", async ({ page }) => {
  const rows = page.getByTestId("session-row");
  await expect(rows.nth(0)).toContainText("会话 A");

  const pinCalls: Array<{ method: string; path: string; body: unknown }> = [];
  page.on("request", (request) => {
    if (!request.url().includes("/pin")) {
      return;
    }
    pinCalls.push({
      method: request.method(),
      path: new URL(request.url()).pathname,
      body: request.postDataJSON()
    });
  });

  await chooseRowAction(page, "会话 B", "置顶");

  await expect(rows.nth(0)).toContainText("会话 B");
  await expect(rows.nth(0)).toHaveAttribute("data-pinned", "true");
  expect(pinCalls).toEqual([
    { method: "PUT", path: "/api/chat/sessions/s_b/pin", body: { pinned: true } }
  ]);

  await page.reload();
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("会话 B");
  await expect(rows.nth(0)).toHaveAttribute("data-pinned", "true");

  await chooseRowAction(page, "会话 B", "取消置顶");
  await expect(rows.nth(0)).toContainText("会话 A");
  await expect(rows.nth(0)).toHaveAttribute("data-pinned", "false");
});

test("多个置顶按最近置顶时间排列", async ({ page }) => {
  const rows = page.getByTestId("session-row");

  await chooseRowAction(page, "会话 C", "置顶");
  await expect(rows.nth(0)).toContainText("会话 C");

  await chooseRowAction(page, "会话 B", "置顶");
  await expect(rows.nth(0)).toContainText("会话 B");
  await expect(rows.nth(1)).toContainText("会话 C");
  await expect(rows.nth(2)).toContainText("会话 A");
});

test("鼠标经过触发器与菜单之间的缝隙时菜单不关闭", async ({ page }) => {
  const trigger = page.getByRole("button", { name: "会话操作：会话 B", exact: true });
  await trigger.click();
  const menu = rowMenu(page, "会话 B");
  await expect(menu).toBeVisible();

  const triggerBox = await trigger.boundingBox();
  const menuBox = await menu.boundingBox();
  expect(triggerBox).not.toBeNull();
  expect(menuBox).not.toBeNull();
  expect(menuBox!.y).toBeGreaterThan(triggerBox!.y + triggerBox!.height);

  const gapY = triggerBox!.y + triggerBox!.height + (menuBox!.y - (triggerBox!.y + triggerBox!.height)) / 2;
  await page.mouse.move(triggerBox!.x + triggerBox!.width - 12, gapY);
  await page.waitForTimeout(150);
  await expect(menu).toBeVisible();

  await page.mouse.move(triggerBox!.x + triggerBox!.width / 2, triggerBox!.y + triggerBox!.height / 2);
  await expect(menu).toBeVisible();

  await page.getByRole("menuitem", { name: "置顶", exact: true }).click();
  await expect(page.getByTestId("session-row").nth(0)).toContainText("会话 B");
});

test("删除已置顶会话会同时移除置顶记录", async ({ page }) => {
  const rows = page.getByTestId("session-row");
  await chooseRowAction(page, "会话 C", "置顶");
  await expect(rows.nth(0)).toContainText("会话 C");

  page.once("dialog", (dialog) => void dialog.accept());
  const menu = await openRowMenu(page, "会话 C");
  await menu.getByRole("menuitem", { name: "删除会话", exact: true }).click();

  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("会话 A");
});
