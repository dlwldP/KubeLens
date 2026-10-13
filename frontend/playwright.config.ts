import { defineConfig } from '@playwright/test'

// e2e는 이미 떠 있는 KubeLens 서버(UI 포함)를 대상으로 한다. 실행 방법은 README 참고.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  reporter: [['list']],
  outputDir: './e2e-results',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000',
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.PW_CHROMIUM_PATH
      ? { executablePath: process.env.PW_CHROMIUM_PATH }
      : undefined,
    trace: 'retain-on-failure',
  },
})
