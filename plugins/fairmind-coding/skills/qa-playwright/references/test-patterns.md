# Test Patterns Reference

## Project Structure

```
tests/
├── fixtures/
│   ├── auth.fixture.ts      # Authentication fixtures
│   ├── data.fixture.ts      # Test data fixtures
│   └── index.ts             # Export all fixtures
├── pages/
│   ├── base.page.ts         # Base page object
│   ├── login.page.ts        # Login page object
│   └── dashboard.page.ts    # Dashboard page object
├── e2e/
│   ├── auth/
│   │   ├── login.spec.ts
│   │   └── logout.spec.ts
│   ├── dashboard/
│   │   └── dashboard.spec.ts
│   └── user/
│       └── profile.spec.ts
├── visual/
│   └── screenshots.spec.ts
└── playwright.config.ts
```

## Configuration

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ['html'],
    ['json', { outputFile: 'test-results.json' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'mobile',
      use: { ...devices['iPhone 13'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

## Page Object Pattern

```typescript
// pages/base.page.ts
import { Page, Locator } from '@playwright/test';

export abstract class BasePage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  abstract get url(): string;

  async goto() {
    await this.page.goto(this.url);
    await this.waitForLoad();
  }

  async waitForLoad() {
    await this.page.waitForLoadState('networkidle');
  }

  async waitForElement(locator: Locator, timeout = 5000) {
    await locator.waitFor({ state: 'visible', timeout });
  }
}

// pages/login.page.ts
import { Page, Locator } from '@playwright/test';
import { BasePage } from './base.page';

export class LoginPage extends BasePage {
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    super(page);
    this.emailInput = page.getByLabel('Email');
    this.passwordInput = page.getByLabel('Password');
    this.submitButton = page.getByRole('button', { name: 'Sign in' });
    this.errorMessage = page.getByRole('alert');
  }

  get url() {
    return '/login';
  }

  async login(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async expectError(message: string) {
    await expect(this.errorMessage).toHaveText(message);
  }
}
```

## Fixtures

```typescript
// fixtures/auth.fixture.ts
import { test as base, expect } from '@playwright/test';
import { LoginPage } from '../pages/login.page';

type AuthFixtures = {
  loginPage: LoginPage;
  authenticatedPage: void;
};

export const test = base.extend<AuthFixtures>({
  loginPage: async ({ page }, use) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await use(loginPage);
  },

  authenticatedPage: async ({ page }, use) => {
    // Setup: login
    await page.goto('/login');
    await page.getByLabel('Email').fill('test@example.com');
    await page.getByLabel('Password').fill('password123');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/dashboard');

    await use();

    // Cleanup: logout
    await page.goto('/logout');
  },
});

// fixtures/data.fixture.ts
import { test as base } from '@playwright/test';

type DataFixtures = {
  testUser: { email: string; password: string };
  uniqueEmail: string;
};

export const test = base.extend<DataFixtures>({
  testUser: async ({}, use) => {
    await use({
      email: 'test@example.com',
      password: 'password123',
    });
  },

  uniqueEmail: async ({}, use) => {
    const email = `test-${Date.now()}@example.com`;
    await use(email);
  },
});

// fixtures/index.ts
import { mergeTests } from '@playwright/test';
import { test as authTest } from './auth.fixture';
import { test as dataTest } from './data.fixture';

export const test = mergeTests(authTest, dataTest);
export { expect } from '@playwright/test';
```

## Test Organization

```typescript
// e2e/auth/login.spec.ts
import { test, expect } from '../../fixtures';

test.describe('Login', () => {
  test.describe('successful login', () => {
    test('with valid credentials', async ({ loginPage, page }) => {
      await loginPage.login('user@example.com', 'password');
      await expect(page).toHaveURL('/dashboard');
    });

    test('remembers user', async ({ loginPage, page }) => {
      await loginPage.login('user@example.com', 'password');
      await page.reload();
      await expect(page).toHaveURL('/dashboard');
    });
  });

  test.describe('failed login', () => {
    test('with invalid email', async ({ loginPage }) => {
      await loginPage.login('invalid', 'password');
      await loginPage.expectError('Invalid email format');
    });

    test('with wrong password', async ({ loginPage }) => {
      await loginPage.login('user@example.com', 'wrong');
      await loginPage.expectError('Invalid credentials');
    });
  });
});
```

## Hooks

```typescript
test.describe('Feature', () => {
  test.beforeAll(async () => {
    // Run once before all tests in this describe block
    await seedDatabase();
  });

  test.afterAll(async () => {
    // Run once after all tests
    await cleanupDatabase();
  });

  test.beforeEach(async ({ page }) => {
    // Run before each test
    await page.goto('/');
  });

  test.afterEach(async ({ page }) => {
    // Run after each test
    await page.evaluate(() => localStorage.clear());
  });
});
```

## Parameterized Tests

```typescript
const testCases = [
  { role: 'admin', canDelete: true },
  { role: 'user', canDelete: false },
  { role: 'guest', canDelete: false },
];

for (const { role, canDelete } of testCases) {
  test(`${role} ${canDelete ? 'can' : 'cannot'} delete items`, async ({ page }) => {
    await loginAs(page, role);
    await page.goto('/items');

    const deleteButton = page.getByRole('button', { name: 'Delete' });

    if (canDelete) {
      await expect(deleteButton).toBeVisible();
    } else {
      await expect(deleteButton).toBeHidden();
    }
  });
}
```

## API Mocking

```typescript
test('handles API error gracefully', async ({ page }) => {
  // Mock failed API response
  await page.route('**/api/users', (route) => {
    route.fulfill({
      status: 500,
      body: JSON.stringify({ error: 'Server error' }),
    });
  });

  await page.goto('/users');

  await expect(page.getByText('Failed to load users')).toBeVisible();
});

test('displays loading state', async ({ page }) => {
  // Delay API response
  await page.route('**/api/data', async (route) => {
    await new Promise((r) => setTimeout(r, 2000));
    route.continue();
  });

  await page.goto('/data');

  await expect(page.getByRole('progressbar')).toBeVisible();
});
```

## Network Interception

```typescript
test('tracks analytics events', async ({ page }) => {
  const analyticsRequests: string[] = [];

  await page.route('**/analytics/**', (route) => {
    analyticsRequests.push(route.request().url());
    route.fulfill({ status: 200 });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Subscribe' }).click();

  expect(analyticsRequests).toContainEqual(
    expect.stringContaining('event=subscribe')
  );
});
```

## Storage State

```typescript
// Save authentication state
test('login and save state', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill('user@example.com');
  await page.getByLabel('Password').fill('password');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await page.context().storageState({ path: 'auth.json' });
});

// Use saved state
test.use({ storageState: 'auth.json' });

test('authenticated test', async ({ page }) => {
  await page.goto('/dashboard');
  // Already logged in
});
```
