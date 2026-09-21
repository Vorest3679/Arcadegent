import { expect, test } from "@playwright/test";
import { installAmapMock, installChatApiMocks, installStreamMock } from "./test-support";

test("ChatPanel shows progressive route card from SSE route_ready", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page);

  await page.goto("/");
  await page.getByPlaceholder("尽管问机厅相关问题").fill("给我一条到 Arcade One 的路线");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByTestId("agent-route-card")).toBeVisible();
  await expect(page.getByTestId("agent-route-pending")).toBeVisible();
  await expect(page.getByText("路线结果生成后会自动更新此卡片。")).toBeVisible();
  await expect(page.getByText("渐进展示")).toBeVisible();
  await expect(page.getByText("1.3 km", { exact: true })).toBeVisible();
  await expect(page.getByTestId("map-action-route-web")).toHaveAttribute("href", /callnative=0/);
  await expect(page.getByTestId("map-action-route-app")).toHaveAttribute("href", /callnative=1/);
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toBeVisible();
  // Wait for the completed detail response, not just the earlier SSE card.
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByTestId("agent-route-card")).toHaveCount(1);

  await page.reload();
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);

  let releaseDispatch!: () => void;
  const dispatchGate = new Promise<void>((resolve) => { releaseDispatch = resolve; });
  await page.route("**/api/chat/sessions", async (route) => {
    await dispatchGate;
    await route.abort();
  });
  try {
    await page.getByPlaceholder("尽管问机厅相关问题").fill("还有别的路线吗？");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("正在生成回复...", { exact: true })).toBeVisible();
    await expect(page.getByTestId("agent-route-card")).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
    await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。", { exact: true })).toHaveCount(1);
  } finally {
    releaseDispatch();
  }
});
