import { expect, test, type Page } from "@playwright/test";

const ARCADES = [
  {
    source: "bemanicn",
    source_id: 101,
    source_url: "https://map.bemanicn.com/s/101",
    name: "Arcade One",
    address: "Nanjing East Road",
    province_code: "310000000000",
    province_name: "上海市",
    city_code: "310100000000",
    city_name: "上海市",
    county_code: "310101000000",
    county_name: "黄浦区",
    updated_at: "2026-04-13T00:00:00Z",
    arcade_count: 2,
    geo: {
      gcj02: {
        lng: 121.475,
        lat: 31.228,
        coord_system: "gcj02",
        source: "geocode",
        precision: "approx"
      },
      source: "geocode",
      precision: "approx"
    }
  },
  {
    source: "bemanicn",
    source_id: 102,
    source_url: "https://map.bemanicn.com/s/102",
    name: "Arcade No Geo",
    address: "Unknown Mall",
    province_code: "310000000000",
    province_name: "上海市",
    city_code: "310100000000",
    city_name: "上海市",
    county_code: "310104000000",
    county_name: "徐汇区",
    updated_at: "2026-04-13T00:00:00Z",
    arcade_count: 1,
    geo: null
  },
  {
    source: "bemanicn",
    source_id: 103,
    source_url: "https://map.bemanicn.com/s/103",
    name: "Arcade Three",
    address: "People Square",
    province_code: "310000000000",
    province_name: "上海市",
    city_code: "310100000000",
    city_name: "上海市",
    county_code: "310101000000",
    county_name: "黄浦区",
    updated_at: "2026-04-13T00:00:00Z",
    arcade_count: 4,
    geo: {
      gcj02: {
        lng: 121.482,
        lat: 31.236,
        coord_system: "gcj02",
        source: "geocode",
        precision: "approx"
      },
      source: "geocode",
      precision: "approx"
    }
  }
];

const DETAILS: Record<number, object> = {
  101: {
    ...ARCADES[0],
    transport: "Line 2",
    comment: "First detail",
    arcades: [{ title_id: 1, title_name: "maimai DX", quantity: 2, version: "2026" }]
  },
  102: {
    ...ARCADES[1],
    transport: "Bus",
    comment: "No geo detail",
    arcades: [{ title_id: 2, title_name: "CHUNITHM", quantity: 1, version: "2026" }]
  },
  103: {
    ...ARCADES[2],
    transport: "Line 1",
    comment: "Third detail",
    arcades: [{ title_id: 3, title_name: "SDVX", quantity: 4, version: "2026" }]
  }
};

const CHAT_ROUTE = {
  provider: "amap",
  mode: "walking",
  distance_m: 1280,
  duration_s: 960,
  origin: {
    lng: 121.4,
    lat: 31.2,
    coord_system: "wgs84",
    source: "client",
    precision: "approx"
  },
  destination: {
    lng: 121.475,
    lat: 31.228,
    coord_system: "gcj02",
    source: "route",
    precision: "approx"
  },
  polyline: [
    {
      lng: 121.4,
      lat: 31.2,
      coord_system: "wgs84",
      source: "client",
      precision: "approx"
    },
    {
      lng: 121.475,
      lat: 31.228,
      coord_system: "gcj02",
      source: "route",
      precision: "approx"
    }
  ],
  hint: null
};

async function installAmapMock(page: Page) {
  await page.addInitScript(() => {
    (window as any).__ARCADEGENT_AMAP_LOADS__ = 0;

    class MockMap {
      container: HTMLElement;
      overlays: any[] = [];
      controls: any[] = [];
      center: [number, number] | null;

      constructor(container: HTMLElement, options: { center?: [number, number] }) {
        this.container = container;
        this.center = options.center ?? null;
        this.container.setAttribute("data-mock-amap-root", "true");
      }

      add(items: any[] | any) {
        const list = Array.isArray(items) ? items : [items];
        list.forEach((item) => {
          this.overlays.push(item);
          item.setMap?.(this);
        });
      }

      remove(items: any[] | any) {
        const list = Array.isArray(items) ? items : [items];
        list.forEach((item) => {
          this.overlays = this.overlays.filter((current) => current !== item);
          item.setMap?.(null);
        });
      }

      addControl(control: any) {
        this.controls.push(control);
      }

      setCenter(center: [number, number]) {
        this.center = center;
      }

      setFitView() {}

      destroy() {
        this.overlays.forEach((item) => item.setMap?.(null));
        this.overlays = [];
        this.container.innerHTML = "";
      }
    }

    class MockMarker {
      private map: MockMap | null = null;
      private handlers = new Map<string, () => void>();
      private element: HTMLElement | null = null;
      private options: any;

      constructor(options: any) {
        this.options = options;
      }

      on(eventName: string, handler: () => void) {
        this.handlers.set(eventName, handler);
      }

      setMap(map: MockMap | null) {
        if (this.element && this.element.parentElement) {
          this.element.parentElement.removeChild(this.element);
        }
        this.map = map;
        if (!map) {
          this.element = null;
          return;
        }
        const wrapper = document.createElement("div");
        wrapper.className = "mock-amap-marker";
        wrapper.innerHTML = this.options.content;
        const button = wrapper.firstElementChild as HTMLElement | null;
        if (button) {
          button.addEventListener("click", () => {
            this.handlers.get("click")?.();
          });
        }
        map.container.appendChild(wrapper);
        this.element = wrapper;
      }
    }

    class MockPolyline {
      private map: MockMap | null = null;

      constructor(_options: any) {}

      setMap(map: MockMap | null) {
        this.map = map;
      }
    }

    window.__ARCADEGENT_AMAP_MOCK__ = {
      async load() {
        (window as any).__ARCADEGENT_AMAP_LOADS__ += 1;
        return {
          Map: MockMap,
          Marker: MockMarker,
          Polyline: MockPolyline,
          Scale: class {},
          ToolBar: class {},
          convertFrom([lng, lat]: [number, number], _source: string, callback: (status: string, result: any) => void) {
            callback("complete", {
              locations: [{ lng: lng + 0.0065, lat: lat + 0.006 }]
            });
          }
        };
      }
    };

    window.localStorage.setItem(
      "arcadegent.client_location.v1",
      JSON.stringify({
        lng: 121.4,
        lat: 31.2,
        accuracy_m: 25,
        city: "上海市"
      })
    );
  });
}

async function installStreamMock(page: Page) {
  await page.addInitScript((routePayload) => {
    class MockEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;

      url: string;
      readyState = MockEventSource.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          if (this.readyState === MockEventSource.CLOSED) {
            return;
          }
          this.readyState = MockEventSource.OPEN;
          this.onopen?.(new Event("open"));
          this.emit("navigation.route_ready", {
            id: 1,
            session_id: "s_e2e",
            event: "navigation.route_ready",
            at: new Date().toISOString(),
            data: routePayload
          });
        }, 30);
        window.setTimeout(() => {
          if (this.readyState === MockEventSource.CLOSED) {
            return;
          }
          this.emit("assistant.completed", {
            id: 2,
            session_id: "s_e2e",
            event: "assistant.completed",
            at: new Date().toISOString(),
            data: {
              reply: "路线已经准备好了，建议步行前往 Arcade One。",
              active_subagent: "main_agent"
            }
          });
          this.close();
        }, 500);
      }

      addEventListener(eventName: string, handler: (event: MessageEvent<string>) => void) {
        const bucket = this.listeners.get(eventName) ?? new Set();
        bucket.add(handler);
        this.listeners.set(eventName, bucket);
      }

      removeEventListener(eventName: string, handler: (event: MessageEvent<string>) => void) {
        this.listeners.get(eventName)?.delete(handler);
      }

      close() {
        this.readyState = MockEventSource.CLOSED;
      }

      private emit(eventName: string, payload: object) {
        const event = new MessageEvent(eventName, {
          data: JSON.stringify(payload)
        });
        this.listeners.get(eventName)?.forEach((handler) => handler(event));
      }
    }

    window.EventSource = MockEventSource as typeof EventSource;
  }, CHAT_ROUTE);
}

async function installApiMocks(page: Page) {
  await page.route("**/api/v1/chat/sessions**", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/v1/regions/provinces", async (route) => {
    await route.fulfill({ json: [{ code: "310000000000", name: "上海市" }] });
  });
  await page.route("**/api/v1/regions/cities**", async (route) => {
    await route.fulfill({ json: [{ code: "310100000000", name: "上海市" }] });
  });
  await page.route("**/api/v1/regions/counties**", async (route) => {
    await route.fulfill({
      json: [
        { code: "310101000000", name: "黄浦区" },
        { code: "310104000000", name: "徐汇区" }
      ]
    });
  });
  await page.route("**/api/v1/location/reverse-geocode", async (route) => {
    await route.fulfill({
      json: {
        lng: 121.4,
        lat: 31.2,
        resolved: true,
        city: "上海市"
      }
    });
  });
  await page.route("**/api/v1/arcades?**", async (route) => {
    await route.fulfill({
      json: {
        items: ARCADES,
        page: 1,
        page_size: 20,
        total: ARCADES.length,
        total_pages: 1
      }
    });
  });
  await page.route("**/api/v1/arcades/*", async (route) => {
    const id = Number(route.request().url().split("/").pop());
    await route.fulfill({ json: DETAILS[id] });
  });
}

async function installChatApiMocks(page: Page) {
  let sessionVisible = false;
  await page.route("**/api/v1/chat/sessions/s_e2e", async (route) => {
    await route.fulfill({
      json: {
        session_id: "s_e2e",
        intent: "navigate",
        active_subagent: "main_agent",
        status: "completed",
        last_error: null,
        reply: "路线已经准备好了，建议步行前往 Arcade One。",
        shops: [ARCADES[0], ARCADES[2]],
        route: CHAT_ROUTE,
        client_location: {
          lng: 121.4,
          lat: 31.2,
          accuracy_m: 25,
          city: "上海市",
          region_text: "上海市"
        },
        destination: ARCADES[0],
        view_payload: {
          version: 1,
          scene: "agent_route",
          title: "从当前位置前往 Arcade One"
        },
        turn_count: 2,
        created_at: "2026-04-15T00:00:00Z",
        updated_at: "2026-04-15T00:00:10Z",
        turns: [
          {
            role: "user",
            content: "给我一条到 Arcade One 的路线",
            created_at: "2026-04-15T00:00:00Z"
          },
          {
            role: "assistant",
            content: "路线已经准备好了，建议步行前往 Arcade One。",
            created_at: "2026-04-15T00:00:10Z"
          }
        ]
      }
    });
  });
  await page.route("**/api/v1/chat/sessions?**", async (route) => {
    await route.fulfill({
      json: sessionVisible
        ? [
          {
            session_id: "s_e2e",
            title: "给我一条到 Arcade One 的路线",
            preview: "路线已经准备好了",
            intent: "navigate",
            status: "completed",
            turn_count: 2,
            created_at: "2026-04-15T00:00:00Z",
            updated_at: "2026-04-15T00:00:10Z"
          }
        ]
        : []
    });
  });
  await page.route("**/api/chat/sessions", async (route) => {
    sessionVisible = true;
    await route.fulfill({
      status: 202,
      json: {
        session_id: "s_e2e",
        status: "running"
      }
    });
  });
  await page.route("**/api/v1/location/reverse-geocode", async (route) => {
    await route.fulfill({
      json: {
        lng: 121.4,
        lat: 31.2,
        resolved: true,
        city: "上海市",
        region_text: "上海市"
      }
    });
  });
}

test("ArcadeBrowser keeps list, map, and actions in sync", async ({ page }) => {
  await installAmapMock(page);
  await installApiMocks(page);

  await page.goto("/?view=arcades");

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

test("ChatPanel shows progressive route card from SSE route_ready", async ({ page }) => {
  await installAmapMock(page);
  await installStreamMock(page);
  await installChatApiMocks(page);

  await page.goto("/");
  await page.getByPlaceholder("尽管问机厅相关问题").fill("给我一条到 Arcade One 的路线");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByTestId("agent-route-card")).toBeVisible();
  await expect(page.getByText("渐进展示")).toBeVisible();
  await expect(page.getByText("1.3 km", { exact: true })).toBeVisible();
  await expect(page.getByTestId("map-action-route-web")).toHaveAttribute("href", /callnative=0/);
  await expect(page.getByTestId("map-action-route-app")).toHaveAttribute("href", /callnative=1/);
  await expect(page.getByText("路线已经准备好了，建议步行前往 Arcade One。")).toBeVisible();
});
