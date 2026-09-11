// Playwright E2E config for the Hunting Job System Streamlit dashboard.
// Run with:  npx playwright test   (or  .venv/bin/python -m playwright test)
import { defineConfig, devices } from '@playwright/test';

const BASE_URL = process.env.DASHBOARD_URL || 'http://localhost:8599';
const PASSWORD = process.env.DASHBOARD_PASSWORD || 'hunting2026';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,          // Streamlit reruns + engine runs can be slow
  expect: { timeout: 15_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Shared values exposed to conftest via fixtures.
  webServer: {
    command: '',   // Dashboard is started manually (see README in tests/e2e)
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
  // Expose password to fixtures.
  // (fixtures read it from env directly.)

});
