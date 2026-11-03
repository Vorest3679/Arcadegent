import { expect, test } from "@playwright/test";
import {
  CHAT_INPUT,
  DEFAULT_REPLY,
  installAmapMock,
  installChatApiMocks,
  installStreamMock,
  mapCards,
  openMapPanel,
  releaseStreamFrame,
  routeRunScript,
  sendChat,
  waitForHandOver
} from "./test-support";

test("the map panel shows the progressive route from SSE route_ready", async ({ page }) => {
  await installAmapMock(page);
  // Hold the route frame: the pending card stays until the test releases it.
  await installStreamMock(page, [routeRunScript({ routeAt: 600, doneAt: 800, holdRoute: "route", holdDone: "done" })]);
  await installChatApiMocks(page);

  await sendChat(page);

  // Opened while the route is still being planned, the panel follows the live map item.
  await openMapPanel(page);
  await expect(page.getByTestId("agent-route-card")).toBeVisible();
  await expect(page.getByTestId("agent-route-pending")).toBeVisible();
  await expect(page.getByText("路线结果生成后会自动更新此卡片。")).toBeVisible();
  await expect(page.getByText("渐进展示")).toBeVisible();
  await releaseStreamFrame(page, "route");
  await expect(page.getByText("1.3 km", { exact: true })).toBeVisible();
  await expect(page.getByTestId("map-action-route-web")).toHaveAttribute("href", /callnative=0/);
  await expect(page.getByTestId("map-action-route-app")).toHaveAttribute("href", /callnative=1/);
  // Let the run finish so the detail response can replace the live card.
  await releaseStreamFrame(page, "done");
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
  await page.keyboard.press("Escape");
  await waitForHandOver(page);
  await expect(page.getByText(DEFAULT_REPLY)).toBeVisible();
  await expect(mapCards(page)).toHaveCount(1);

  await page.reload();
  await openMapPanel(page);
  await expect(page.getByRole("heading", { name: "从当前位置前往 Arcade One" })).toBeVisible();
  await page.keyboard.press("Escape");

  // A new question keeps the previous answer and its map in the feed.
  let releaseDispatch!: () => void;
  const dispatchGate = new Promise<void>((resolve) => { releaseDispatch = resolve; });
  await page.route("**/api/chat/sessions", async (route) => {
    await dispatchGate;
    await route.abort();
  });
  try {
    await page.getByPlaceholder(CHAT_INPUT).fill("还有别的路线吗？");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByTestId("run-indicator")).toBeVisible();
    await expect(mapCards(page)).toHaveCount(1);
    await expect(page.getByText(DEFAULT_REPLY, { exact: true })).toHaveCount(1);
  } finally {
    releaseDispatch();
  }
  // The failed dispatch shows its error in the feed and gives the text back.
  await expect(page.locator('[data-kind="error"]')).toHaveCount(1);
  await expect(page.getByPlaceholder(CHAT_INPUT)).toHaveValue("还有别的路线吗？");
});
