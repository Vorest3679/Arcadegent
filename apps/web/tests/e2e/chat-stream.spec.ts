import { expect, test, type Page } from "@playwright/test";
import {
  E2E_RUN_ID,
  eventFrame,
  installAmapMock,
  installChatApiMocks,
  installStreamMock,
  readStreamUrls,
  runStateFrame,
  type StreamFrame
} from "./test-support";

const INPUT = "尽管问机厅相关问题";

function token(at: number, id: number, delta: string, outputId = "out_a"): StreamFrame {
  return eventFrame(at, id, "assistant.token", { delta }, { output_id: outputId });
}

async function send(page: Page, message = "给我一条到 Arcade One 的路线") {
  await page.goto("/");
  await page.getByPlaceholder(INPUT).fill(message);
  await page.getByRole("button", { name: "发送" }).click();
}

test("reply text appears progressively before assistant.completed", async ({ page }) => {
  const reply = "第一段，第二段。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    token(100, 2, "第一段，"),
    token(1200, 3, "第二段。"),
    eventFrame(1300, 4, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(1300, 5, "completed")
  ]]);
  await installChatApiMocks(page, { reply });

  await send(page);
  const bubble = page.locator(".chat-message.streaming");
  await expect(bubble).toContainText("第一段，");
  await expect(bubble).not.toContainText("第二段。");

  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
});

test("reconnect replays from the cursor without duplicating text", async ({ page }) => {
  const reply = "你好，世界！";
  await installAmapMock(page);
  await installStreamMock(page, [
    [
      runStateFrame(10, 1, "running"),
      token(20, 2, "你好，"),
      token(30, 3, "世界"),
      { at: 60, error: true }
    ],
    [
      // The server replays from last_event_id; ids 2 and 3 overlap.
      token(10, 2, "你好，"),
      token(10, 3, "世界"),
      token(20, 4, "！"),
      eventFrame(1500, 5, "assistant.completed", { reply }, { output_id: "out_a" }),
      runStateFrame(1500, 6, "completed")
    ]
  ]);
  await installChatApiMocks(page, { reply });

  await send(page);
  const streamed = page.locator(".chat-message.streaming .chat-markdown");
  await expect(streamed).toContainText("！");
  // Snapshot before assistant.completed overwrites the assembled text.
  expect(await streamed.innerText()).toBe(reply);

  const urls = await readStreamUrls(page);
  expect(urls).toHaveLength(2);
  const resumed = new URL(urls[1]);
  expect(resumed.searchParams.get("run_id")).toBe(E2E_RUN_ID);
  expect(resumed.searchParams.get("last_event_id")).toBe("3");

  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
});

test("frames of another run are ignored", async ({ page }) => {
  const reply = "这是新的回复。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "assistant.token", { delta: "旧回复" }, { run_id: "r_old", output_id: "out_old" }),
    token(40, 3, reply),
    eventFrame(60, 4, "assistant.completed", { reply: "旧回复" }, { run_id: "r_old" }),
    eventFrame(80, 5, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(80, 6, "completed")
  ]]);
  await installChatApiMocks(page, { reply, detailDelayMs: 800 });

  await send(page);
  await expect(page.locator(".chat-message.streaming")).toContainText(reply);
  // One-shot check while the run is still live, before the detail reload.
  expect(await page.locator(".chat-message-list").innerText()).not.toContain("旧回复");
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  expect(await page.locator(".chat-message-list").innerText()).not.toContain("旧回复");
});

test("a second output_id seals the first as an intermediate reply", async ({ page }) => {
  const reply = "找到了 Arcade One。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    token(20, 2, "我先查一下。", "out_a"),
    eventFrame(40, 3, "tool.started", { tool: "db_query_tool", call_id: "c1" }),
    eventFrame(60, 4, "tool.completed", { tool: "db_query_tool", call_id: "c1" }),
    token(80, 5, reply, "out_b"),
    eventFrame(100, 6, "assistant.completed", { reply }, { output_id: "out_b" }),
    runStateFrame(100, 7, "completed")
  ]]);
  await installChatApiMocks(page, { reply, detailDelayMs: 1500 });

  await send(page);
  const intermediate = page.locator(".chat-message.intermediate");
  const bubble = page.locator(".chat-message.streaming");
  await expect(intermediate).toHaveCount(1);
  await expect(intermediate).toContainText("我先查一下。");
  await expect(intermediate).toContainText("中间回复");
  await expect(bubble).toContainText(reply);
  await expect(bubble).not.toContainText("我先查一下");
});

test("stream.reset reloads the session detail while the run continues", async ({ page }) => {
  const reply = "重置后完成。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    { at: 100, envelope: { id: 9, kind: "control", event: "stream.reset", data: { head_id: 12 } } },
    token(200, 10, reply),
    eventFrame(1200, 11, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(1200, 12, "completed")
  ]]);
  const api = await installChatApiMocks(page, { reply, detailStatus: "follow-stream" });

  await send(page);
  await expect(page.locator(".chat-message.streaming")).toContainText(reply);
  expect(api.detailCalls()).toBe(1);
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  expect(api.detailCalls()).toBe(2);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
});

test("giving up on reconnects cancels the current run by run_id", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page, [[{ at: 20, error: true }]]);
  await installChatApiMocks(page, { detailStatus: "running" });
  await page.route("**/api/chat/sessions/s_e2e/runs/*/cancel?**", async (route) => {
    await route.fulfill({
      json: {
        session_id: "s_e2e",
        intent: "navigate",
        active_subagent: "main_agent",
        status: "failed",
        current_run: { run_id: E2E_RUN_ID, status: "cancelled" },
        last_error: "已停止",
        reply: null,
        shops: [],
        turn_count: 1,
        created_at: "2026-04-15T00:00:00Z",
        updated_at: "2026-04-15T00:00:10Z",
        turns: [{ role: "user", content: "给我一条到 Arcade One 的路线", created_at: "2026-04-15T00:00:00Z" }]
      }
    });
  });

  const cancelRequest = page.waitForRequest("**/api/chat/sessions/s_e2e/runs/*/cancel?**", { timeout: 10_000 });
  await send(page);
  const request = await cancelRequest;
  const url = new URL(request.url());
  expect(url.pathname).toBe(`/api/chat/sessions/s_e2e/runs/${E2E_RUN_ID}/cancel`);
  expect(url.searchParams.get("client_id")).toBeTruthy();
  expect(await readStreamUrls(page)).toHaveLength(4);
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
});

test("resuming a running session after reload leaves no stage card behind", async ({ page }) => {
  const reply = "刷新后完成的回复。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    eventFrame(20, 2, "session.started", { active_subagent: "main_agent" }),
    token(200, 3, reply),
    eventFrame(2000, 4, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(2000, 5, "completed")
  ]]);
  await installChatApiMocks(page, {
    reply,
    sessionVisible: true,
    detailStatus: "follow-stream"
  });

  await page.goto("/");
  await expect(page.getByText("执行阶段：主控阶段")).toBeVisible();
  const urls = await readStreamUrls(page);
  expect(new URL(urls[0]).searchParams.get("run_id")).toBe(E2E_RUN_ID);
  expect(new URL(urls[0]).searchParams.has("last_event_id")).toBe(false);

  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
  await page.waitForTimeout(300);
  expect(await page.locator(".chat-message.stream-event").count()).toBe(0);
});

test("the composer stays locked until the finished run is handed over", async ({ page }) => {
  const reply = "交接前不能再发送。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    token(20, 2, reply),
    eventFrame(60, 3, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(60, 4, "completed")
  ]]);
  await installChatApiMocks(page, { reply, detailDelayMs: 1500 });

  await send(page);
  await expect(page.locator(".chat-message.streaming")).toContainText(reply);
  await page.waitForTimeout(300);
  // The run is over but its reply is not a history turn yet.
  expect(await page.getByPlaceholder(INPUT).isDisabled()).toBe(true);

  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByPlaceholder(INPUT)).toBeEnabled();
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
});

test("a failed hand-over keeps the final reply and releases the composer", async ({ page }) => {
  const reply = "详情加载失败也要保留。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    token(20, 2, reply),
    eventFrame(60, 3, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(60, 4, "completed")
  ]]);
  await installChatApiMocks(page, { reply });
  await page.route("**/api/chat/sessions/s_e2e?**", async (route) => {
    await route.fulfill({ status: 500, body: "boom" });
  });

  await send(page);
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
  await expect(page.getByPlaceholder(INPUT)).toBeEnabled();
});

test("a stale stream.reset snapshot cannot overwrite the finished run", async ({ page }) => {
  const reply = "终态详情不被覆盖。";
  await installAmapMock(page);
  await installStreamMock(page, [[
    runStateFrame(10, 1, "running"),
    { at: 50, envelope: { id: 5, kind: "control", event: "stream.reset", data: { head_id: 8 } } },
    token(100, 6, reply),
    eventFrame(300, 7, "assistant.completed", { reply }, { output_id: "out_a" }),
    runStateFrame(300, 8, "completed")
  ]]);
  // The reset request (running snapshot) answers after the terminal one.
  await installChatApiMocks(page, { reply, detailStatus: "follow-stream", detailDelayMs: [1500, 0] });

  await send(page);
  await expect(page.locator(".chat-message.streaming")).toHaveCount(0);
  await expect(page.getByText(reply, { exact: true })).toHaveCount(1);
  await page.waitForTimeout(1800);
  expect(await page.locator(".chat-message.streaming").count()).toBe(0);
  expect(await page.getByPlaceholder(INPUT).isDisabled()).toBe(false);
  expect(await page.getByText(reply, { exact: true }).count()).toBe(1);
});
