import type { Page } from "@playwright/test";

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

export async function installAmapMock(page: Page) {
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

// One scripted SSE connection: frames are emitted `at` ms after the
// connection opens. Envelope fields left out (session_id, run_id, kind, at)
// are filled from the connection URL, so a frame may override run_id to
// simulate a late frame of another run. `error` drops the connection.
export type StreamFrame =
  | { at: number; envelope: Record<string, unknown> }
  | { at: number; error: true };

export const E2E_RUN_ID = "r_e2e1";
export const DEFAULT_REPLY = "路线已经准备好了，建议步行前往 Arcade One。";

export function eventFrame(at: number, id: number, event: string, data: object = {}, extra: object = {}): StreamFrame {
  return { at, envelope: { id, kind: "event", event, data, ...extra } };
}

export function runStateFrame(at: number, id: number, status: string): StreamFrame {
  return { at, envelope: { id, kind: "control", event: "run.state", status, data: {} } };
}

const DEFAULT_SCRIPT: StreamFrame[][] = [[
  runStateFrame(10, 1, "running"),
  eventFrame(20, 2, "worker.started", { worker: "navigation_worker" }),
  eventFrame(180, 3, "navigation.route_ready", CHAT_ROUTE),
  eventFrame(500, 4, "assistant.completed", { reply: DEFAULT_REPLY, active_subagent: "main_agent" }),
  runStateFrame(500, 5, "completed")
]];

// Each new EventSource plays the next script; the last one repeats.
export async function installStreamMock(page: Page, scripts: StreamFrame[][] = DEFAULT_SCRIPT) {
  await page.addInitScript((connectionScripts) => {
    const urls: string[] = [];
    (window as any).__ARCADEGENT_SSE_URLS__ = urls;

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
        urls.push(url);
        const parsed = new URL(url);
        const sessionId = decodeURIComponent(parsed.pathname.split("/").pop() ?? "");
        const runId = parsed.searchParams.get("run_id");
        const script = connectionScripts[Math.min(urls.length - 1, connectionScripts.length - 1)];

        window.setTimeout(() => {
          if (this.readyState === MockEventSource.CLOSED) {
            return;
          }
          this.readyState = MockEventSource.OPEN;
          this.onopen?.(new Event("open"));
        }, 5);
        script.forEach((frame: any) => {
          window.setTimeout(() => {
            if (this.readyState === MockEventSource.CLOSED) {
              return;
            }
            if (frame.error) {
              this.onerror?.(new Event("error"));
              return;
            }
            const envelope = {
              session_id: sessionId,
              run_id: runId,
              kind: "event",
              at: new Date().toISOString(),
              ...frame.envelope
            };
            if (envelope.event === "run.state" && ["completed", "failed", "cancelled"].includes(envelope.status)) {
              (window as any).__ARCADEGENT_SSE_TERMINAL__ = true;
            }
            const event = new MessageEvent("message", { data: JSON.stringify(envelope) });
            this.listeners.get("message")?.forEach((handler) => handler(event));
          }, 5 + frame.at);
        });
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
    }

    window.EventSource = MockEventSource as typeof EventSource;
  }, scripts);
}

export async function readStreamUrls(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as any).__ARCADEGENT_SSE_URLS__ as string[]);
}

export async function installApiMocks(page: Page, options: { totalPages?: number } = {}) {
  const totalPages = options.totalPages ?? 1;

  await page.route("**/api/chat/sessions**", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/regions/provinces", async (route) => {
    await route.fulfill({ json: [{ code: "310000000000", name: "上海市" }] });
  });
  await page.route("**/api/regions/cities**", async (route) => {
    await route.fulfill({ json: [{ code: "310100000000", name: "上海市" }] });
  });
  await page.route("**/api/regions/counties**", async (route) => {
    await route.fulfill({
      json: [
        { code: "310101000000", name: "黄浦区" },
        { code: "310104000000", name: "徐汇区" }
      ]
    });
  });
  await page.route("**/api/location/reverse-geocode", async (route) => {
    await route.fulfill({
      json: {
        lng: 121.4,
        lat: 31.2,
        resolved: true,
        city: "上海市"
      }
    });
  });
  await page.route("**/api/arcades?**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const pageNumber = Number(requestUrl.searchParams.get("page") ?? "1");
    const items = totalPages > 1 && pageNumber > 1 ? [ARCADES[2]] : ARCADES;
    await route.fulfill({
      json: {
        items,
        page: pageNumber,
        page_size: 20,
        total: totalPages > 1 ? 21 : ARCADES.length,
        total_pages: totalPages
      }
    });
  });
  await page.route("**/api/arcades/*", async (route) => {
    const id = Number(route.request().url().split("/").pop());
    await route.fulfill({ json: DETAILS[id] });
  });
}

export type ChatApiMockOptions = {
  reply?: string;
  // Steps the server attaches to the user turn of the round (see ChatTurnStep).
  steps?: object[];
  // Steps already persisted while the run is still running (a mid-run reload).
  runningSteps?: object[];
  // "follow-stream": running until the mocked stream sent its terminal state.
  detailStatus?: "running" | "completed" | "follow-stream";
  // A number delays every detail response; an array delays by request index.
  detailDelayMs?: number | number[];
  sessionVisible?: boolean;
};

export async function installChatApiMocks(page: Page, options: ChatApiMockOptions = {}) {
  const reply = options.reply ?? DEFAULT_REPLY;
  const detailStatus = options.detailStatus ?? "completed";
  let sessionVisible = options.sessionVisible ?? false;
  let detailCalls = 0;
  const userTurn = {
    role: "user",
    content: "给我一条到 Arcade One 的路线",
    created_at: "2026-04-15T00:00:00Z"
  };

  await page.route("**/api/chat/sessions/s_e2e?**", async (route) => {
    detailCalls += 1;
    const status = detailStatus !== "follow-stream"
      ? detailStatus
      : await page.evaluate(() => (window as any).__ARCADEGENT_SSE_TERMINAL__ === true)
        ? "completed"
        : "running";
    const delays = options.detailDelayMs;
    const delayMs = Array.isArray(delays) ? delays[Math.min(detailCalls - 1, delays.length - 1)] : delays;
    if (delayMs) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    const running = status === "running";
    await route.fulfill({
      json: {
        session_id: "s_e2e",
        intent: "navigate",
        active_subagent: "main_agent",
        status: running ? "running" : "completed",
        current_run: { run_id: E2E_RUN_ID, status },
        last_error: null,
        reply: running ? null : reply,
        shops: running ? [] : [ARCADES[0], ARCADES[2]],
        route: running ? null : CHAT_ROUTE,
        client_location: {
          lng: 121.4,
          lat: 31.2,
          accuracy_m: 25,
          city: "上海市",
          region_text: "上海市"
        },
        destination: running ? null : ARCADES[0],
        view_payload: running
          ? null
          : {
            version: 1,
            scene: "agent_route",
            title: "从当前位置前往 Arcade One"
          },
        turn_count: running ? 1 : 2,
        created_at: "2026-04-15T00:00:00Z",
        updated_at: "2026-04-15T00:00:10Z",
        turns: running
          ? [{ ...userTurn, steps: options.runningSteps ?? [] }]
          : [
            { ...userTurn, steps: options.steps ?? [] },
            {
              role: "assistant",
              content: reply,
              created_at: "2026-04-15T00:00:10Z"
            }
          ]
      }
    });
  });
  await page.route("**/api/chat/sessions?**", async (route) => {
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
        run_id: E2E_RUN_ID,
        status: "running"
      }
    });
  });
  await page.route("**/api/location/reverse-geocode", async (route) => {
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

  return {
    detailCalls: () => detailCalls
  };
}
