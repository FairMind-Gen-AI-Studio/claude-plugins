# CI Integration Reference

## GitHub Actions

### Basic Setup

```yaml
# .github/workflows/playwright.yml
name: Playwright Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    timeout-minutes: 60
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright Browsers
        run: npx playwright install --with-deps

      - name: Run Playwright tests
        run: npx playwright test

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: playwright-report
          path: playwright-report/
          retention-days: 30
```

### With Test Sharding

```yaml
jobs:
  test:
    timeout-minutes: 60
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        shardIndex: [1, 2, 3, 4]
        shardTotal: [4]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps

      - name: Run tests
        run: npx playwright test --shard=${{ matrix.shardIndex }}/${{ matrix.shardTotal }}

      - name: Upload blob report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: blob-report-${{ matrix.shardIndex }}
          path: blob-report
          retention-days: 1

  merge-reports:
    if: always()
    needs: [test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Download blob reports
        uses: actions/download-artifact@v4
        with:
          path: all-blob-reports
          pattern: blob-report-*
          merge-multiple: true

      - name: Merge reports
        run: npx playwright merge-reports --reporter html ./all-blob-reports

      - name: Upload HTML report
        uses: actions/upload-artifact@v4
        with:
          name: html-report
          path: playwright-report
          retention-days: 14
```

### With Container

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    container:
      image: mcr.microsoft.com/playwright:v1.40.0-jammy

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Run tests
        run: npx playwright test
        env:
          HOME: /root
```

### With Database Service

```yaml
jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: test_db
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps

      - name: Run migrations
        run: npm run db:migrate
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/test_db

      - name: Run tests
        run: npx playwright test
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/test_db
```

## GitLab CI

```yaml
# .gitlab-ci.yml
image: mcr.microsoft.com/playwright:v1.40.0-jammy

stages:
  - test

playwright:
  stage: test
  script:
    - npm ci
    - npx playwright test
  artifacts:
    when: always
    paths:
      - playwright-report/
    expire_in: 1 week
  cache:
    paths:
      - node_modules/
```

## Configuration for CI

### playwright.config.ts

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',

  // Fail fast in CI
  forbidOnly: !!process.env.CI,

  // Retry on CI
  retries: process.env.CI ? 2 : 0,

  // Parallel workers
  workers: process.env.CI ? 1 : undefined,

  // CI-friendly reporter
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }], ['json', { outputFile: 'results.json' }]]
    : [['html', { open: 'on-failure' }]],

  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',

    // Collect traces on failure
    trace: 'on-first-retry',

    // Screenshots on failure
    screenshot: 'only-on-failure',

    // Video on retry
    video: 'on-first-retry',
  },

  // Start server in CI
  webServer: process.env.CI
    ? {
        command: 'npm run start',
        url: 'http://localhost:3000',
        timeout: 120 * 1000,
      }
    : undefined,

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
```

## Test Reports

### HTML Report in GitHub Pages

```yaml
# Deploy report to GitHub Pages
deploy-report:
  needs: [test]
  runs-on: ubuntu-latest
  if: always() && github.ref == 'refs/heads/main'

  steps:
    - name: Download report
      uses: actions/download-artifact@v4
      with:
        name: playwright-report
        path: playwright-report

    - name: Deploy to GitHub Pages
      uses: peaceiris/actions-gh-pages@v3
      with:
        github_token: ${{ secrets.GITHUB_TOKEN }}
        publish_dir: ./playwright-report
```

### Slack Notification

```yaml
- name: Notify Slack on failure
  if: failure()
  uses: slackapi/slack-github-action@v1
  with:
    payload: |
      {
        "text": "Playwright tests failed on ${{ github.ref }}",
        "blocks": [
          {
            "type": "section",
            "text": {
              "type": "mrkdwn",
              "text": "*Playwright Tests Failed*\n${{ github.event.repository.name }} - ${{ github.ref }}"
            }
          },
          {
            "type": "actions",
            "elements": [
              {
                "type": "button",
                "text": {"type": "plain_text", "text": "View Report"},
                "url": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
              }
            ]
          }
        ]
      }
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
```

## Environment Variables

```yaml
env:
  # Base URL for tests
  BASE_URL: http://localhost:3000

  # Disable colored output
  FORCE_COLOR: 0

  # Playwright settings
  PLAYWRIGHT_BROWSERS_PATH: ${{ github.workspace }}/ms-playwright

  # Test credentials (use secrets)
  TEST_USER_EMAIL: ${{ secrets.TEST_USER_EMAIL }}
  TEST_USER_PASSWORD: ${{ secrets.TEST_USER_PASSWORD }}
```

## Caching

```yaml
- name: Cache Playwright browsers
  uses: actions/cache@v4
  id: playwright-cache
  with:
    path: ~/.cache/ms-playwright
    key: ${{ runner.os }}-playwright-${{ hashFiles('**/package-lock.json') }}

- name: Install Playwright
  if: steps.playwright-cache.outputs.cache-hit != 'true'
  run: npx playwright install --with-deps
```

## Debugging CI Failures

### Enable Debug Mode

```yaml
- name: Run tests with debug
  run: npx playwright test
  env:
    DEBUG: pw:api
```

### Artifact Collection

```yaml
- uses: actions/upload-artifact@v4
  if: failure()
  with:
    name: test-artifacts
    path: |
      playwright-report/
      test-results/
      screenshots/
    retention-days: 7
```

### SSH Debug Session

```yaml
- name: Setup tmate session
  if: failure()
  uses: mxschmitt/action-tmate@v3
  timeout-minutes: 15
```

## Best Practices

1. **Use retries in CI**: Tests can be flaky due to timing
2. **Collect artifacts on failure**: Always upload reports, screenshots, traces
3. **Use sharding for large suites**: Parallelize across multiple jobs
4. **Cache browsers**: Avoid downloading on every run
5. **Run visual tests separately**: They're more prone to flakiness
6. **Set reasonable timeouts**: Don't let failing tests run forever
7. **Use containers**: Consistent environment across runs
8. **Notify on failures**: Slack, email, or PR comments
