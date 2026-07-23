---
name: qa-playwright
description: Use when implementing automated testing with Playwright, including E2E tests, visual testing, or using the Playwright MCP tools for browser automation. This skill covers test patterns, selector strategies, and CI integration.
---

# QA Testing with Playwright

## Overview

This skill provides guidance for automated testing using Playwright, including E2E testing, visual regression, and browser automation via MCP tools.

**Announce at start:** "I'm using the qa-playwright skill for this testing implementation."

## When to Use

Use this skill when:
- Writing E2E tests with Playwright
- Implementing visual regression tests
- Using Playwright MCP tools for browser automation
- Setting up test infrastructure
- Debugging flaky tests
- Integrating tests with CI/CD

## Core Workflow

### Step 1: Understand Test Requirements

Before writing tests:
1. Review acceptance criteria from user story
2. Identify critical user flows to test
3. Plan test data and fixtures
4. Determine visual testing needs

### Step 2: Design Test Structure

1. Organize tests by feature/page
2. Create reusable page objects
3. Plan fixture hierarchy
4. Define test data strategy

### Step 3: Implement Tests

Follow this order:
1. **Page objects** - Create abstraction layer
2. **Fixtures** - Set up reusable test data
3. **Tests** - Write test cases
4. **Assertions** - Verify expected behavior
5. **Visual tests** - Add screenshot comparisons

### Step 4: Integrate and Maintain

1. Configure CI pipeline
2. Handle test failures
3. Maintain baseline screenshots
4. Review and update tests

## Reference Files

| File | Content | When to Use |
|------|---------|-------------|
| `references/test-patterns.md` | Test organization, fixtures | Test structure |
| `references/selectors.md` | Locator strategies | Element selection |
| `references/visual-testing.md` | Screenshot comparison | Visual regression |
| `references/mcp-tools.md` | Playwright MCP tools | Browser automation |
| `references/ci-integration.md` | CI/CD patterns | Pipeline setup |

## Key Principles

### Test Design
- Test user behavior, not implementation
- Keep tests independent and isolated
- Use meaningful test names
- Handle async operations properly

### Selectors
- Prefer user-facing attributes (role, text, label)
- Avoid brittle selectors (CSS classes, XPath)
- Use data-testid for complex cases
- Keep selectors maintainable

### Reliability
- Wait for conditions, not time
- Handle network requests properly
- Isolate test data
- Clean up after tests

## Integration with Fairmind

When working on Fairmind tasks:
1. Use `fairmind-context` skill for requirements
2. Check test expectations from user story
3. Map acceptance criteria to test cases
4. Update journal with test coverage

## MCP Tools Usage

The Playwright MCP server provides browser automation:

```typescript
// Navigate
mcp__playwright__browser_navigate({ url: "http://localhost:3000" })

// Take snapshot (accessibility tree)
mcp__playwright__browser_snapshot()

// Click element
mcp__playwright__browser_click({ element: "Login button", ref: "button[name='login']" })

// Fill form
mcp__playwright__browser_type({ element: "Email input", ref: "input[name='email']", text: "test@example.com" })

// Screenshot
mcp__playwright__browser_take_screenshot({ filename: "login-page.png" })
```

## Example Usage

```typescript
// Example: Login flow test
import { test, expect } from '@playwright/test';
import { LoginPage } from './pages/login.page';

test.describe('Authentication', () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.goto();
  });

  test('successful login redirects to dashboard', async ({ page }) => {
    await loginPage.login('user@example.com', 'password123');

    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  });

  test('invalid credentials shows error', async () => {
    await loginPage.login('wrong@example.com', 'wrongpass');

    await expect(loginPage.errorMessage).toBeVisible();
    await expect(loginPage.errorMessage).toHaveText('Invalid credentials');
  });
});
```

## Next Steps

After completing tests:
- Run tests in CI pipeline
- Review coverage reports
- Update test documentation
- Report results to tech lead
