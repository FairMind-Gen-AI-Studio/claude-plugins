# Visual Testing Reference

## Screenshot Comparison

### Basic Visual Test

```typescript
import { test, expect } from '@playwright/test';

test('homepage visual', async ({ page }) => {
  await page.goto('/');

  // Full page screenshot comparison
  await expect(page).toHaveScreenshot('homepage.png');
});

test('component visual', async ({ page }) => {
  await page.goto('/components');

  // Element screenshot
  const card = page.getByTestId('user-card');
  await expect(card).toHaveScreenshot('user-card.png');
});
```

### Screenshot Options

```typescript
test('with options', async ({ page }) => {
  await page.goto('/');

  await expect(page).toHaveScreenshot('homepage.png', {
    // Comparison threshold (0-1)
    maxDiffPixels: 100,
    maxDiffPixelRatio: 0.01,

    // Animation handling
    animations: 'disabled',

    // Mask dynamic content
    mask: [page.getByTestId('timestamp'), page.locator('.ad-banner')],

    // Viewport
    fullPage: true,

    // Ignore anti-aliasing
    threshold: 0.2,
  });
});
```

### Masking Dynamic Content

```typescript
test('masks dynamic elements', async ({ page }) => {
  await page.goto('/dashboard');

  await expect(page).toHaveScreenshot('dashboard.png', {
    mask: [
      page.getByTestId('current-time'),
      page.getByTestId('random-quote'),
      page.locator('.loading-spinner'),
      page.locator('img'), // Mask all images
    ],
  });
});
```

## Baseline Management

### Update Baselines

```bash
# Update all baselines
npx playwright test --update-snapshots

# Update specific test
npx playwright test visual.spec.ts --update-snapshots

# Update in CI (careful!)
UPDATE_SNAPSHOTS=1 npx playwright test
```

### Baseline Organization

```
tests/
├── visual/
│   └── screenshots.spec.ts
└── screenshots.spec.ts-snapshots/
    ├── homepage-chromium-darwin.png
    ├── homepage-chromium-linux.png
    ├── homepage-firefox-darwin.png
    └── user-card-chromium-darwin.png
```

### Cross-Platform Baselines

```typescript
// playwright.config.ts
export default defineConfig({
  snapshotPathTemplate: '{testDir}/__snapshots__/{testFilePath}/{arg}{ext}',

  // Or per-project baselines
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
      },
      snapshotPathTemplate: '{testDir}/__snapshots__/{testFilePath}/{projectName}/{arg}{ext}',
    },
  ],
});
```

## Handling Flaky Visuals

### Stabilize Before Screenshot

```typescript
test('stable screenshot', async ({ page }) => {
  await page.goto('/');

  // Wait for all images to load
  await page.waitForLoadState('networkidle');

  // Wait for animations to complete
  await page.waitForTimeout(500);

  // Or wait for specific animation
  await page.getByTestId('animated-element').waitFor({ state: 'visible' });
  await page.evaluate(() => document.fonts.ready);

  await expect(page).toHaveScreenshot();
});
```

### Disable Animations

```typescript
// Global in config
use: {
  animations: 'disabled',
}

// Or per-test
test('no animations', async ({ page }) => {
  await page.goto('/');

  // Disable CSS animations
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
      }
    `,
  });

  await expect(page).toHaveScreenshot();
});
```

### Handle Fonts

```typescript
test('with stable fonts', async ({ page }) => {
  await page.goto('/');

  // Wait for fonts to load
  await page.evaluate(() => document.fonts.ready);

  // Or use system fonts for consistency
  await page.addStyleTag({
    content: `* { font-family: Arial, sans-serif !important; }`,
  });

  await expect(page).toHaveScreenshot();
});
```

## Responsive Visual Testing

```typescript
const viewports = [
  { width: 375, height: 667, name: 'mobile' },
  { width: 768, height: 1024, name: 'tablet' },
  { width: 1440, height: 900, name: 'desktop' },
];

for (const viewport of viewports) {
  test(`homepage at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });

    await page.goto('/');
    await expect(page).toHaveScreenshot(`homepage-${viewport.name}.png`);
  });
}
```

## Component Visual Testing

```typescript
// Isolated component testing with Storybook
test.describe('Button variants', () => {
  test('primary button', async ({ page }) => {
    await page.goto('/storybook/iframe.html?id=button--primary');
    await expect(page.locator('#storybook-root')).toHaveScreenshot('button-primary.png');
  });

  test('disabled button', async ({ page }) => {
    await page.goto('/storybook/iframe.html?id=button--disabled');
    await expect(page.locator('#storybook-root')).toHaveScreenshot('button-disabled.png');
  });
});
```

## Visual Regression Workflow

### 1. Baseline Creation

```typescript
// First run creates baselines
npx playwright test --update-snapshots
git add tests/**/*-snapshots/**
git commit -m "Add visual baselines"
```

### 2. PR Workflow

```yaml
# .github/workflows/visual-test.yml
name: Visual Tests

on: [pull_request]

jobs:
  visual-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps

      - name: Run visual tests
        run: npx playwright test tests/visual/

      - name: Upload diff artifacts
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: visual-diff
          path: test-results/
```

### 3. Reviewing Differences

```typescript
// playwright.config.ts
reporter: [
  ['html', { open: 'never' }],
],

// View report locally
npx playwright show-report
```

## Best Practices

```typescript
// 1. Test specific components, not full pages when possible
test('header component', async ({ page }) => {
  const header = page.getByRole('banner');
  await expect(header).toHaveScreenshot('header.png');
});

// 2. Use meaningful snapshot names
await expect(page).toHaveScreenshot('login-form-error-state.png');

// 3. Group related visual tests
test.describe('Dark mode', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
  });

  test('homepage dark', async ({ page }) => {
    await expect(page).toHaveScreenshot('homepage-dark.png');
  });
});

// 4. Document baseline updates in PRs
// "Updated baselines for new button design (#123)"
```

## Troubleshooting

### Flaky Screenshots

```typescript
// Increase threshold for anti-aliasing
await expect(page).toHaveScreenshot({
  threshold: 0.3, // Allow more pixel difference
});

// Use pixel count instead of ratio
await expect(page).toHaveScreenshot({
  maxDiffPixels: 50,
});
```

### Platform Differences

```typescript
// Skip on certain platforms
test.skip(process.platform === 'win32', 'Font rendering differs on Windows');

// Or use platform-specific baselines
snapshotPathTemplate: '{testDir}/__snapshots__/{platform}/{arg}{ext}',
```
