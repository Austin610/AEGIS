const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8878',
    channel: process.env.AEGIS_BROWSER_CHANNEL || (process.platform === 'win32' && !process.env.CI ? 'chrome' : undefined),
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: (process.env.CI ? 'python' : process.platform === 'win32' ? '".venv\\Scripts\\python.exe"' : '.venv/bin/python') + ' scripts/browser_fixture.py',
    url: 'http://127.0.0.1:8878/health',
    reuseExistingServer: false,
  },
});
