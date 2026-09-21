import { expect, test } from "@playwright/test";
import { installAmapMock, installApiMocks } from "./test-support";

test("ArcadeBrowser keeps list, map, and actions in sync", async ({ page }) => {
  await installAmapMock(page);
  await installApiMocks(page);

  await page.goto("/arcades");

  await expect(page.getByTestId("arcade-list-item-101")).toBeVisible();
  await expect(page.getByTestId("arcade-map-placeholder")).toBeVisible();
  await expect(page.getByTestId("arcade-map-canvas")).toHaveCount(0);
  await expect(page.getByTestId("browser-detail-title")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).__ARCADEGENT_AMAP_LOADS__)).toBe(0);

  await page.getByTestId("arcade-list-item-101").click();
  await expect(page.getByTestId("arcade-map-canvas")).toBeVisible();
  await expect(page.getByTestId("map-marker-101")).toBeVisible();
  await expect(page.getByTestId("map-marker-103")).toHaveCount(0);
  await expect(page.getByTestId("map-marker-102")).toHaveCount(0);
  await expect(page.getByTestId("browser-detail-title")).toHaveText("Arcade One");
  await expect.poll(() => page.evaluate(() => (window as any).__ARCADEGENT_AMAP_LOADS__)).toBeGreaterThan(0);
  const mapLoadsAfterFirstSelection = await page.evaluate(() => (window as any).__ARCADEGENT_AMAP_LOADS__);

  const viewHref = await page.getByTestId("map-action-view").getAttribute("href");
  const navHref = await page.getByTestId("map-action-navigate").getAttribute("href");
  expect(viewHref).toContain("position=121.475%2C31.228");
  expect(viewHref).toContain("src=arcadegent_e2e");
  expect(navHref).toContain("to=121.475%2C31.228%2CArcade+One");
  expect(navHref).toContain("from=121.4065%2C31.206%2C");
  expect(navHref).toContain("callnative=0");

  await page.getByTestId("arcade-list-item-103").click();
  await expect(page.getByTestId("arcade-list-item-103")).toHaveClass(/is-active/);
  await expect(page.getByTestId("map-marker-101")).toHaveCount(0);
  await expect(page.getByTestId("map-marker-103")).toBeVisible();
  await expect(page.getByTestId("browser-detail-title")).toHaveText("Arcade Three");
  expect(await page.evaluate(() => (window as any).__ARCADEGENT_AMAP_LOADS__)).toBe(mapLoadsAfterFirstSelection);

  await page.getByTestId("arcade-list-item-102").click();
  await expect(page.getByText(/该机厅暂时没有精确地图坐标/)).toBeVisible();
  await expect(page.getByTestId("map-action-view")).toHaveCount(0);
  await expect(page.getByText("暂无地图定位")).toBeVisible();
});

test("ArcadeBrowser submits filters and paginates results", async ({ page }) => {
  await installAmapMock(page);
  await installApiMocks(page, { totalPages: 2 });

  await page.goto("/arcades");
  await expect(page.getByTestId("arcade-list-item-101")).toBeVisible();

  await page.getByLabel("机厅名称").fill("Arcade One");
  await page.getByLabel("省份").selectOption("310000000000");
  await expect(page.getByLabel("城市")).toBeEnabled();
  await page.getByLabel("城市").selectOption("310100000000");
  await expect(page.getByLabel("区县")).toBeEnabled();
  await page.getByLabel("区县").selectOption("310101000000");
  await page.getByLabel("排序字段").selectOption("title_quantity");
  await page.getByTestId("arcade-title-filter").selectOption({ label: "maimai DX" });

  const searchRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/arcades"
      && url.searchParams.get("shop_name") === "Arcade One"
      && url.searchParams.get("province_code") === "310000000000"
      && url.searchParams.get("city_code") === "310100000000"
      && url.searchParams.get("county_code") === "310101000000"
      && url.searchParams.get("sort_by") === "title_quantity"
      && url.searchParams.get("sort_title_name") === "maimai DX";
  });
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await searchRequest;

  await expect(page.getByText("1-20 / 21")).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("21-21 / 21")).toBeVisible();
  await expect(page.getByTestId("arcade-list-item-103")).toBeVisible();
});

test("ArcadeBrowser renders detailed cabinet information", async ({ page }) => {
  await installAmapMock(page);
  await installApiMocks(page);

  await page.goto("/arcades");
  await page.getByTestId("arcade-list-item-101").click();

  await expect(page.getByTestId("browser-detail-title")).toHaveText("Arcade One");
  await expect(page.getByText("First detail")).toBeVisible();
  await expect(page.getByRole("heading", { name: "机台信息（1）" })).toBeVisible();
  await expect(page.locator(".browser-title-list b", { hasText: "maimai DX" })).toBeVisible();
  await expect(page.getByText("数量：2", { exact: true })).toBeVisible();
  await expect(page.getByText("版本：2026", { exact: true })).toBeVisible();
});

test("sidebar switches between chat and arcade views", async ({ page }) => {
  await installAmapMock(page);
  await installApiMocks(page);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Agent 对话" })).toBeVisible();

  await page.getByRole("button", { name: "机厅检索" }).click();
  await expect(page).toHaveURL(/\/arcades$/);
  await expect(page.locator(".topbar").getByRole("heading", { name: "机厅检索" })).toBeVisible();

  await page.getByRole("button", { name: "Agent 对话" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".topbar").getByRole("heading", { name: "Agent 对话" })).toBeVisible();
});

