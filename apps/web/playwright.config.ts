import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:4174",
    headless: true,
    reducedMotion: "reduce",
    trace: "retain-on-failure"
  },
  // Mobile projects run the process/map-dock spec only. Both use Chromium with
  // the device's viewport, touch and user agent (WebKit is not installed here).
  projects: [
    { name: "desktop" },
    {
      name: "mobile-iphone",
      testMatch: /chat-process\.spec\.ts/,
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" }
    },
    {
      name: "mobile-pixel",
      testMatch: /chat-process\.spec\.ts/,
      use: { ...devices["Pixel 7"] }
    }
  ],
  webServer: {
    command:
      "VITE_API_BASE=http://127.0.0.1:4174 VITE_AMAP_WEB_KEY=e2e-key VITE_AMAP_URI_SRC=arcadegent_e2e pnpm run dev --host 127.0.0.1 --port 4174",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: true,
    timeout: 120_000
  }
});
