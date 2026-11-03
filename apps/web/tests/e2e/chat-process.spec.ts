import { expect, test } from "@playwright/test";
import {
  CHAT_INPUT,
  clickDeleteSession,
  DEFAULT_REPLY,
  eventFrame,
  feedItems,
  installAmapMock,
  installChatApiMocks,
  installStreamMock,
  mapCards,
  releaseStreamFrame,
  openMapPanel,
  routeRunScript,
  runStateFrame,
  sendChat,
  expandToolGroups,
  toolGroups,
  toolRows,
  waitForHandOver
} from "./test-support";

const HISTORY_STEPS = [
  { kind: "text", agent: "main_agent", content: "我先查一下。", created_at: "2026-04-15T00:00:05Z" },
  { kind: "tool", call_id: "c1", name: "db_query_tool", agent: "search_worker", status: "failed", created_at: "2026-04-15T00:00:06Z" },
  { kind: "tool", call_id: "c2", name: "route_plan_tool", agent: "navigation_worker", status: "completed", created_at: "2026-04-15T00:00:07Z" },
  { kind: "tool", call_id: "c3", name: "mcp_weather", agent: "", status: "completed", created_at: "2026-04-15T00:00:08Z" }
];

test("a reloaded session folds tool calls and lists names, agents and status icons when opened", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true, steps: HISTORY_STEPS });

  await page.goto("/");
  // History tool calls start folded; the intermediate reply stays visible.
  const group = toolGroups(page);
  await expect(group).toHaveCount(1);
  await expect(group).toContainText("调用了 3 个工具");
  await expect(group).toContainText("1 个失败");
  await expect(toolRows(page)).toHaveCount(0);
  await expandToolGroups(page);
  const rows = toolRows(page);
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("数据检索");
  await expect(rows.nth(0)).toContainText("检索执行");
  await expect(rows.nth(0).getByRole("img", { name: "失败" })).toBeVisible();
  await expect(rows.nth(1)).toContainText("路线规划");
  await expect(rows.nth(1)).toContainText("导航执行");
  await expect(rows.nth(1).getByRole("img", { name: "已完成" })).toBeVisible();
  // Whitelisted tools carry a tool icon; any other tool shows its own name without one.
  await expect(rows.nth(0).getByTestId("tool-icon")).toBeVisible();
  await expect(rows.nth(2)).toContainText("mcp_weather");
  await expect(rows.nth(2)).toHaveAttribute("title", "mcp_weather");
  await expect(rows.nth(2).getByTestId("tool-icon")).toHaveCount(0);
  await expect(feedItems(page)).toContainText("我先查一下。");
});

test("a running tool turns completed in place", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "tool.started", { tool: "db_query_tool", call_id: "c1", active_subagent: "search_worker" }),
    eventFrame(1200, 3, "tool.completed", { tool: "db_query_tool", call_id: "c1", active_subagent: "search_worker" }),
    // The run stays open long enough to see the completed row before the group folds.
    eventFrame(3500, 4, "assistant.completed", { reply: "好了。" }),
    runStateFrame(3500, 5, "completed")
  ]]);
  // The history the hand-over loads holds the same call, as the server would.
  await installChatApiMocks(page, {
    reply: "好了。",
    detailDelayMs: 1000,
    steps: [{ kind: "tool", call_id: "c1", name: "db_query_tool", agent: "search_worker", status: "completed", created_at: "2026-04-15T00:00:06Z" }]
  });

  await sendChat(page);
  const row = toolRows(page);
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("数据检索");
  await expect(row.getByRole("img", { name: "执行中" })).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "running");
  await expect(row.getByRole("img", { name: "已完成" })).toBeVisible();
  // The stream ended: the group folds into one line.
  await expect(toolGroups(page).locator("> button")).toHaveAttribute("aria-expanded", "false");
  await expect(toolGroups(page)).toContainText("调用了 1 个工具");
  await expandToolGroups(page);
  await expect(row.getByRole("img", { name: "已完成" })).toBeVisible();
});

test("cancelling the run marks the running tool as cancelled", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "tool.started", { tool: "route_plan_tool", call_id: "c1", active_subagent: "navigation_worker" }),
    runStateFrame(400, 3, "cancelled", "cancel")
  ]]);
  // Keep the history hand-over far away: this test asserts the live cancelled state.
  await installChatApiMocks(page, { detailDelayMs: 8000 });

  await sendChat(page);
  await expect(toolRows(page).getByRole("img", { name: "执行中" })).toBeVisible();
  await releaseStreamFrame(page, "cancel");
  await expect(toolGroups(page).locator("> button")).toHaveAttribute("aria-expanded", "false");
  await expandToolGroups(page);
  await expect(toolRows(page).getByRole("img", { name: "已取消" })).toBeVisible();
});

test("a map arrives as a card in the feed and opens in the map panel", async ({ page }) => {
  await installAmapMock(page);
  // Hold the completion frame: the run stays open while we assert the compact card.
  await installStreamMock(page, [routeRunScript({ routeAt: 300, doneAt: 600, holdDone: "done" })]);
  await installChatApiMocks(page, { detailDelayMs: 600 });

  await sendChat(page);
  const card = mapCards(page);
  // The card sits in the feed, right where the navigation run started.
  await expect(card).toHaveCount(1);
  await expect(card).toHaveAccessibleName(/查看地图：路线/);
  await expect(page.getByTestId("agent-route-card")).toHaveCount(0);
  // Compact while the run works; a map thumbnail once it ended.
  await expect(page.getByTestId("map-preview")).toHaveCount(0);

  await releaseStreamFrame(page, "done");
  await waitForHandOver(page);
  await expect(card).toHaveCount(1);
  await expect(page.getByTestId("map-preview")).toBeVisible();
  await expect(page.getByTestId("map-preview").locator("[data-mock-amap-root]")).toHaveCount(1);
  await openMapPanel(page);
  await expect(page.getByTestId("agent-route-card")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();

  await page.getByTestId("map-panel").getByRole("button", { name: "关闭" }).click();
  await expect(page.getByTestId("map-panel")).toHaveCount(0);
  await expect(page.getByTestId("agent-route-card")).toHaveCount(0);
});

test("the map panel is a side panel on desktop and a bottom drawer on phones", async ({ page, isMobile }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");

  const viewport = page.viewportSize()!;
  await openMapPanel(page);
  const panel = page.getByTestId("map-panel");
  await panel.evaluate((node) => Promise.all(node.getAnimations().map((animation) => animation.finished.catch(() => {}))));
  const box = (await panel.boundingBox())!;
  if (isMobile) {
    expect(Math.abs(box.y + box.height - viewport.height)).toBeLessThanOrEqual(1);
    expect(Math.abs(box.width - viewport.width)).toBeLessThanOrEqual(1);
    expect(box.height).toBeLessThanOrEqual(viewport.height * 0.88 + 1);
  } else {
    expect(Math.abs(box.x + box.width - viewport.width)).toBeLessThanOrEqual(1);
    expect(box.width).toBeLessThanOrEqual(576);
  }
  await expect(page.getByTestId("agent-route-card")).toBeVisible();
  // The AMap links stay reachable inside the panel.
  await panel.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(panel.getByRole("link", { name: "网页打开" })).toBeInViewport();
  await expect(panel.getByRole("link", { name: "打开高德 App" })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

  // Escape closes it and gives the chat back.
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(page.getByPlaceholder(CHAT_INPUT)).toBeInViewport();
});

test("the composer stays reachable and the feed scrolls", async ({ page, isMobile }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true, steps: HISTORY_STEPS });

  await page.goto("/");
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();
  const viewport = page.viewportSize()!;
  const input = await page.getByPlaceholder(CHAT_INPUT).boundingBox();
  expect(input).not.toBeNull();
  expect(input!.y + input!.height).toBeLessThanOrEqual(viewport.height);
  if (isMobile) {
    expect(input!.height).toBeGreaterThanOrEqual(44);
  }
  const scroll = await page.getByTestId("chat-scroll").boundingBox();
  expect(scroll!.height).toBeGreaterThan(200);
  await expect(page.locator('[data-kind="user"]').first()).toBeVisible();
  await page.getByTestId("chat-scroll").evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(mapCards(page)).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
});

test("a tool group opened by hand stays open when a new round starts", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "tool.started", { tool: "geo_resolve_tool", call_id: "n1" }),
    eventFrame(40, 3, "tool.completed", { tool: "geo_resolve_tool", call_id: "n1" }),
    eventFrame(3000, 4, "assistant.completed", { reply: "第二轮。" }),
    runStateFrame(3000, 5, "completed")
  ]]);
  await installChatApiMocks(page, { sessionVisible: true, steps: HISTORY_STEPS, detailStatus: "completed" });
  await page.goto("/");
  await expect(toolGroups(page)).toHaveCount(1);
  await expandToolGroups(page);
  await expect(toolRows(page)).toHaveCount(3);

  await page.getByPlaceholder(CHAT_INPUT).fill("再找一家");
  await page.getByRole("button", { name: "发送" }).click();
  // The new round's tools are expanded while its stream is open, the earlier
  // group keeps the state the user chose.
  await expect(toolGroups(page)).toHaveCount(2);
  await expect(toolGroups(page).nth(0).locator("> button")).toHaveAttribute("aria-expanded", "true");
  await expect(toolGroups(page).nth(1).locator("> button")).toHaveAttribute("aria-expanded", "true");
  await expect(toolRows(page)).toHaveCount(4);
});

test("deleting a session updates locally without re-listing sessions", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();

  const calls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/chat/sessions")) {
      calls.push(`${request.method()} ${url.pathname}`);
    }
  });
  page.on("dialog", (dialog) => void dialog.accept());
  // On small screens the history list lives in a drawer.
  const menu = page.getByRole("button", { name: "打开侧边栏" });
  const drawer = await menu.isVisible();
  if (drawer) {
    await menu.click();
  }
  await expect(page.getByTestId("session-row")).toHaveCount(1);
  await page.getByRole("button", { name: /删除会话/ }).click();

  await expect(page.getByTestId("session-row")).toHaveCount(0);
  if (drawer) {
    await page.keyboard.press("Escape");
  }
  await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
  expect(calls).toEqual(["DELETE /api/chat/sessions/s_e2e"]);
});

test("deleting the open session shows the start panel before the server answers", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();

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
  await clickDeleteSession(page);
  try {
    await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
    await expect(page.getByText(DEFAULT_REPLY)).toHaveCount(0);
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
  // On small screens the history list lives in a drawer.
  const menu = page.getByRole("button", { name: "打开侧边栏" });
  if (await menu.isVisible()) {
    await menu.click();
  }
  await expect(page.locator('[data-testid="session-row"][data-active="true"]')).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("status", { name: "加载会话中" })).toHaveCount(0);
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();
});

test("a failed delete alerts and does not reopen the session", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page, { sessionVisible: true });
  await page.goto("/");
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();
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
  await clickDeleteSession(page);
  await expect.poll(() => dialogs.length).toBe(2);
  expect(dialogs[1]).toMatch(/^alert:删除会话失败/);
  await expect(page.getByText("今天想查哪家机厅？")).toBeVisible();
  await expect(page.getByText(DEFAULT_REPLY)).toHaveCount(0);
});
