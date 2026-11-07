import { expect, test, type Page } from "@playwright/test";
import { installApiMocks } from "./test-support";

const SESSIONS = [
  {
    session_id: "s_title",
    title: "星极周末探店",
    preview: "浦东的机厅",
    intent: "search",
    status: "completed",
    turn_count: 2,
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:40Z"
  },
  {
    session_id: "s_artifact",
    title: "周末出行",
    preview: "帮我看看这家",
    intent: "search",
    status: "completed",
    turn_count: 2,
    artifact_shops: [{ name: "银河电玩城", name_pinyin: "yin-he-dian-wan-cheng" }],
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:30Z"
  },
  {
    session_id: "s_pinyin",
    title: "路线规划",
    preview: "已经规划",
    intent: "search",
    status: "completed",
    turn_count: 2,
    artifact_shops: [{ name: "东方明珠", name_pinyin: "dong-fang-ming-zhu" }],
    created_at: "2026-04-15T00:00:00Z",
    updated_at: "2026-04-15T00:00:20Z"
  },
  {
    session_id: "s_miss",
    title: "闲聊",
    preview: "没有结果",
    intent: "search",
    status: "completed",
    turn_count: 1,
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

async function installSessionSearchMocks(page: Page) {
  await page.route("**/api/chat/sessions?**", async (route) => {
    await route.fulfill({ json: SESSIONS });
  });
  await page.route("**/api/chat/sessions/*", async (route) => {
    const sessionId = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    await route.fulfill({ json: detailFor(sessionId) });
  });
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
  await installSessionSearchMocks(page);
  await page.goto("/");
  await expect(page.getByTestId("session-row")).toHaveCount(SESSIONS.length);
});

test("搜索入口展开为输入框，取消后恢复历史列表", async ({ page }) => {
  await page.getByRole("button", { name: "搜索会话" }).click();
  const input = page.getByRole("textbox", { name: "搜索会话或机厅" });
  await expect(input).toBeVisible();
  await expect(input).toBeFocused();
  await expect(page.getByRole("button", { name: "取消搜索" })).toBeVisible();
  await expect(page.getByRole("button", { name: "搜索会话" })).toHaveCount(0);

  await input.fill("银河");
  await expect(page.getByTestId("session-row")).toHaveCount(1);
  await expect(page.getByTestId("session-row")).toContainText("周末出行");

  await page.getByRole("button", { name: "取消搜索" }).click();
  await expect(page.getByRole("textbox", { name: "搜索会话或机厅" })).toHaveCount(0);
  await expect(page.getByTestId("session-row")).toHaveCount(SESSIONS.length);
});

test("关键词动态命中标题、artifacts 机厅与拼音", async ({ page }) => {
  await page.getByRole("button", { name: "搜索会话" }).click();
  const input = page.getByRole("textbox", { name: "搜索会话或机厅" });
  const rows = page.getByTestId("session-row");

  await input.fill("探店");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("星极周末探店");

  await input.fill("银河");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("周末出行");

  await input.fill("yin-he");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("周末出行");

  await input.fill("dongfang");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("路线规划");

  await input.fill("不存在的关键词");
  await expect(rows).toHaveCount(0);
  await expect(page.getByText("没有匹配的会话")).toBeVisible();
});

test("搜索结果反色显示，选中后回到普通会话样式", async ({ page }) => {
  await page.getByRole("button", { name: "搜索会话" }).click();
  await page.getByRole("textbox", { name: "搜索会话或机厅" }).fill("银河");

  const row = page.getByTestId("session-row");
  const surface = row.locator("div").first();
  await expect(row).toHaveAttribute("data-search-result", "true");
  await expect(surface).toHaveClass(/bg-primary/);
  const green = await surface.evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(green).not.toBe("rgb(255, 255, 255)");

  await surface.hover();
  await expect(surface).toHaveCSS("background-color", "rgb(255, 255, 255)");

  await row.locator("button").first().click();
  await expect(row).toHaveAttribute("data-active", "true");
  await expect(surface).toHaveClass(/bg-accent/);
  await expect(surface).not.toHaveClass(/bg-primary/);
});
