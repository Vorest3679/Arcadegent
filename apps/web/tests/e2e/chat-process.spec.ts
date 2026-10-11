import { expect, test, type Page } from "@playwright/test";
import {
  eventFrame,
  installAmapMock,
  installChatApiMocks,
  installStreamMock,
  openMapDock,
  routeRunScript,
  runStateFrame
} from "./test-support";

const INPUT = "尽管问机厅相关问题";

async function send(page: Page) {
  await page.goto("/");
  await page.getByPlaceholder(INPUT).fill("给我一条到 Arcade One 的路线");
  await page.getByRole("button", { name: "发送" }).click();
}

// A finished round folds its process area into one line.
async function expandSteps(page: Page) {
  const toggle = page.locator(".round-steps-toggle").first();
  await expect(toggle).toBeVisible();
  if ((await toggle.getAttribute("aria-expanded")) === "false") {
    await toggle.click();
  }
}

// Waits for the round to end (the process area folds itself), then opens it.
async function openAfterRoundEnds(page: Page) {
  const toggle = page.locator(".round-steps-toggle").first();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
}

const HISTORY_STEPS = [
  { kind: "text", agent: "main_agent", content: "我先查一下。", created_at: "2026-04-15T00:00:05Z" },
  { kind: "tool", call_id: "c1", name: "db_query_tool", agent: "search_worker", status: "failed", created_at: "2026-04-15T00:00:06Z" },
  { kind: "tool", call_id: "c2", name: "route_plan_tool", agent: "navigation_worker", status: "completed", created_at: "2026-04-15T00:00:07Z" },
  { kind: "tool", call_id: "c3", name: "mcp_weather", agent: "", status: "completed", created_at: "2026-04-15T00:00:08Z" }
];

test("a reloaded session shows tool names, agents and status icons in the process area", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true, steps: HISTORY_STEPS });

  await page.goto("/");
  const toggle = page.locator(".round-steps-toggle");
  // History rounds start folded.
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toContainText("调用了 3 个工具 · 1 段中间回复");
  await expect(page.locator(".step-tool")).toHaveCount(0);

  await toggle.click();
  const rows = page.locator(".step-tool");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("数据检索");
  await expect(rows.nth(0)).toContainText("检索执行");
  await expect(rows.nth(0).getByRole("img", { name: "失败" })).toBeVisible();
  await expect(rows.nth(1)).toContainText("路线规划");
  await expect(rows.nth(1)).toContainText("导航执行");
  await expect(rows.nth(1).getByRole("img", { name: "已完成" })).toBeVisible();
  // Tools without an alias show their own name.
  await expect(rows.nth(2)).toContainText("mcp_weather");
  await expect(rows.nth(2)).toHaveAttribute("title", "mcp_weather");
  await expect(page.locator(".step-text")).toContainText("我先查一下。");
});

test("a running tool turns completed in the live process area", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "tool.started", { tool: "db_query_tool", call_id: "c1", active_subagent: "search_worker" }),
    eventFrame(1200, 3, "tool.completed", { tool: "db_query_tool", call_id: "c1", active_subagent: "search_worker" }),
    eventFrame(1400, 4, "assistant.completed", { reply: "好了。" }),
    runStateFrame(1400, 5, "completed")
  ]]);
  await installChatApiMocks(page, { reply: "好了。", detailDelayMs: 2500 });

  await send(page);
  const row = page.locator(".step-tool");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("数据检索");
  await expect(row.getByRole("img", { name: "执行中" })).toBeVisible();
  await openAfterRoundEnds(page);
  await expect(row.getByRole("img", { name: "已完成" })).toBeVisible();
});

test("cancelling the run marks the running tool as cancelled", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "tool.started", { tool: "route_plan_tool", call_id: "c1", active_subagent: "navigation_worker" }),
    runStateFrame(400, 3, "cancelled")
  ]]);
  await installChatApiMocks(page, { detailDelayMs: 2500 });

  await send(page);
  await expect(page.locator(".step-tool").getByRole("img", { name: "执行中" })).toBeVisible();
  await openAfterRoundEnds(page);
  await expect(page.locator(".step-tool").getByRole("img", { name: "已取消" })).toBeVisible();
});

test("the map dock starts folded, highlights a new map and opens on demand", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [routeRunScript({ routeAt: 300, doneAt: 800 })]);
  await installChatApiMocks(page, { detailDelayMs: 600 });

  await send(page);
  const dock = page.getByTestId("map-dock");
  const bar = dock.locator(".map-dock-bar");
  await expect(bar).toBeVisible();
  // A new map only highlights the entry, on desktop and mobile alike.
  await expect(dock).toHaveClass(/is-highlight/);
  await expect(bar).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("agent-route-card")).toHaveCount(0);
  await bar.click();
  await expect(bar).toHaveAttribute("aria-expanded", "true");
  await expect(dock).not.toHaveClass(/is-highlight/);
  await expect(page.getByTestId("agent-route-card")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  // The map is not part of the message list.
  await expect(page.locator(".chat-message-list").getByTestId("agent-route-card")).toHaveCount(0);

  await bar.click();
  await expect(bar).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("agent-route-card")).toHaveCount(0);
  await expect(bar).toContainText("路线");
});

test("the folded map handle sits at the desktop right edge and keeps the mobile summary", async ({ page, isMobile }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");

  const dock = page.getByTestId("map-dock");
  const bar = dock.locator(".map-dock-bar");
  await expect(bar).toHaveAttribute("aria-expanded", "false");
  await expect(bar).toHaveAccessibleName(/展开地图：路线/);

  const widths = isMobile ? [page.viewportSize()!.width] : [1440, 961, 960];
  for (const width of widths) {
    if (!isMobile) {
      await page.setViewportSize({ width, height: 900 });
    }
    await page.evaluate(() => Promise.all(document.getAnimations()
      .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
      .map((animation) => animation.finished.catch(() => {}))));
    if (width > 960) {
      await expect(dock.locator(".map-dock-map-icon")).toBeVisible();
      await expect(dock.locator(".map-dock-chevron")).toBeVisible();
      await expect(dock.locator(".map-dock-summary")).toBeHidden();
      const view = (await page.locator(".chat-view").boundingBox())!;
      const handle = (await bar.boundingBox())!;
      expect(Math.abs(handle.x + handle.width + 8 - (view.x + view.width))).toBeLessThanOrEqual(2);
      expect(Math.abs(handle.y + handle.height / 2 - (view.y + view.height / 2))).toBeLessThanOrEqual(2);
      expect(handle.width).toBeGreaterThanOrEqual(44);
      expect(handle.height).toBe(100);
      await expect(bar).toHaveCSS("border-top-style", "solid");
      await expect(bar).toHaveCSS("border-top-width", "1px");
      await expect(dock.locator(".map-dock-chevron")).toHaveCSS("transform", "none");
      const message = (await page.locator(".chat-message.user").first().boundingBox())!;
      expect(Math.abs(message.x + message.width - (view.x + view.width - 23))).toBeLessThanOrEqual(1);
      expect(await page.locator(".chat-scroll").evaluate((node) => getComputedStyle(node).paddingRight)).toBe("22px");
      expect(await page.locator(".chat-scroll").evaluate((node) => getComputedStyle(node).paddingTop)).toBe("24px");
    } else {
      await expect(dock.locator(".map-dock-map-icon")).toBeHidden();
      await expect(dock.locator(".map-dock-chevron")).toBeHidden();
      await expect(dock.locator(".map-dock-summary")).toBeVisible();
      const handle = (await bar.boundingBox())!;
      const scroll = (await page.locator(".chat-scroll").boundingBox())!;
      expect(handle.y + handle.height).toBeLessThanOrEqual(scroll.y);
    }

    await bar.click();
    await expect(bar).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("agent-route-card")).toBeVisible();
    await dock.evaluate((node) => Promise.all(node.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => {}))));
    if (width > 960) {
      await expect(dock.locator(".map-dock-map-icon")).toBeVisible();
      await expect(dock.locator(".map-dock-summary")).toBeHidden();
      await expect(dock.locator(".map-dock-toggle-text")).toBeHidden();
      await expect(dock.locator(".map-dock-chevron")).toHaveCSS("transform", "matrix(-1, 0, 0, -1, 0, 0)");
      const scroll = (await page.locator(".chat-scroll").boundingBox())!;
      const map = (await dock.boundingBox())!;
      expect(scroll.x + scroll.width).toBeLessThanOrEqual(map.x);
      expect(map.width).toBeLessThanOrEqual(440);
      const view = (await page.locator(".chat-view").boundingBox())!;
      const handle = (await bar.boundingBox())!;
      expect(handle.height).toBe(100);
      const mapIcon = (await dock.locator(".map-dock-map-icon").boundingBox())!;
      const arrow = (await dock.locator(".map-dock-chevron").boundingBox())!;
      expect(mapIcon.x).toBeGreaterThanOrEqual(handle.x);
      expect(mapIcon.x + mapIcon.width).toBeLessThanOrEqual(arrow.x);
      expect(arrow.x + arrow.width).toBeLessThanOrEqual(handle.x + handle.width);
      expect(Math.abs(handle.y + handle.height / 2 - (view.y + view.height / 2))).toBeLessThanOrEqual(2);
      expect(Math.abs(handle.x - (map.x - 22))).toBeLessThanOrEqual(2);
      expect(handle.x).toBeLessThan(map.x);
      expect(handle.x + handle.width).toBeGreaterThan(map.x);
      await expect(bar).toHaveCSS("border-top-width", "1px");
      expect(await dock.locator(".map-dock-body").evaluate((node) => getComputedStyle(node).paddingLeft)).toBe("12px");
      const card = (await page.getByTestId("agent-route-card").boundingBox())!;
      expect(Math.abs(card.x - (map.x + 13))).toBeLessThanOrEqual(1);
    } else {
      await expect(dock.locator(".map-dock-summary")).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page.getByPlaceholder(INPUT)).toBeInViewport();
    await bar.click();
    await expect(dock).not.toHaveClass(/is-open/);
    await expect(page.getByTestId("agent-route-card")).toHaveCount(0);
  }

  // The same control works from the keyboard and announces its next action.
  await bar.focus();
  await page.keyboard.press("Enter");
  await expect(bar).toHaveAttribute("aria-expanded", "true");
  await expect(bar).toHaveAccessibleName(/收起地图：路线/);
});

test("a closed map dock stays closed for the rest of the run", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [routeRunScript({ routeAt: 1500, doneAt: 2200 })]);
  await installChatApiMocks(page, { detailDelayMs: 600 });

  await send(page);
  const bar = page.getByTestId("map-dock").locator(".map-dock-bar");
  await expect(bar).toBeVisible();
  // Open it, then close it before the route arrives.
  await openMapDock(page);
  await bar.click();
  await expect(bar).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(bar).toHaveAttribute("aria-expanded", "false");
});

test("the composer stays reachable and the list scrolls with the map open", async ({ page, isMobile }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true, steps: HISTORY_STEPS });

  await page.goto("/");
  const bar = page.getByTestId("map-dock").locator(".map-dock-bar");
  await openMapDock(page);
  await expect(page.getByTestId("agent-route-card")).toBeVisible();

  const viewport = page.viewportSize()!;
  const input = await page.getByPlaceholder(INPUT).boundingBox();
  expect(input).not.toBeNull();
  expect(input!.y + input!.height).toBeLessThanOrEqual(viewport.height);
  if (isMobile) {
    expect(input!.height).toBeGreaterThanOrEqual(44);
  }
  // The message list keeps usable room beside the map.
  const scroll = await page.locator(".chat-scroll").boundingBox();
  expect(scroll!.height).toBeGreaterThan(60);
  await expect(page.locator(".chat-message.user").first()).toBeVisible();
  // The dock bar stays reachable while the list scrolls.
  await page.locator(".chat-scroll").evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(bar).toBeInViewport();
});

test("deleting a session updates locally without re-listing sessions", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.locator(".sidebar-session-row")).toHaveCount(1);

  const calls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/chat/sessions")) {
      calls.push(`${request.method()} ${url.pathname}`);
    }
  });
  page.on("dialog", (dialog) => void dialog.accept());
  // On small screens the history list lives in a drawer.
  const menu = page.locator(".menu-btn");
  if (await menu.isVisible()) {
    await menu.click();
  }
  await page.getByRole("button", { name: /删除会话/ }).click();

  await expect(page.locator(".sidebar-session-row")).toHaveCount(0);
  if (await menu.isVisible()) {
    await page.getByRole("button", { name: "关闭侧边栏" }).click({ force: true });
  }
  await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
  expect(calls).toEqual(["DELETE /api/chat/sessions/s_e2e"]);
});

test("deleting the open session shows the start panel before the server answers", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toBeVisible();

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/chat/sessions/s_e2e?**", async (route) => {
    if (route.request().method() === "DELETE") {
      await gate;
      await route.fulfill({ status: 204 });
    } else {
      await route.fallback();
    }
  });
  page.on("dialog", (dialog) => void dialog.accept());
  const menu = page.locator(".menu-btn");
  if (await menu.isVisible()) {
    await menu.click();
  }
  await page.getByRole("button", { name: /删除会话/ }).click();
  try {
    await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
    await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toHaveCount(0);
  } finally {
    release();
  }
});

test("opening a session highlights it at once and shows a spinner while loading", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true, detailDelayMs: 1500 });
  await page.goto("/");

  await expect(page.getByRole("status", { name: "加载会话中" })).toBeVisible();
  await expect(page.getByText("今天想查哪家机厅？")).toHaveCount(0);
  await expect(page.locator(".sidebar-session-wrap.is-active")).toHaveCount(1);
  await expect(page.getByRole("status", { name: "加载会话中" })).toHaveCount(0);
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toBeVisible();
});

test("a failed delete alerts and does not reopen the session", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toBeVisible();
  await page.route("**/api/chat/sessions/s_e2e?**", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 500, body: "boom" });
    } else {
      await route.fallback();
    }
  });
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(`${dialog.type()}:${dialog.message()}`);
    void dialog.accept();
  });
  const menu = page.locator(".menu-btn");
  if (await menu.isVisible()) {
    await menu.click();
  }
  await page.getByRole("button", { name: /删除会话/ }).click();
  await expect.poll(() => dialogs.length).toBe(2);
  expect(dialogs[1]).toMatch(/^alert:删除会话失败/);
  await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toHaveCount(0);
});
