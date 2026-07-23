# Selectors Reference

## Locator Priority (Best to Worst)

1. **Role** - Most accessible and reliable
2. **Text** - User-visible content
3. **Label** - Form elements
4. **Placeholder** - Input hints
5. **Alt text** - Images
6. **Test ID** - Explicit test hooks
7. **CSS** - Last resort

## Role-Based Selectors

```typescript
// Buttons
page.getByRole('button', { name: 'Submit' })
page.getByRole('button', { name: /submit/i }) // Case insensitive regex

// Links
page.getByRole('link', { name: 'Learn more' })

// Headings
page.getByRole('heading', { name: 'Welcome' })
page.getByRole('heading', { level: 1 })

// Form elements
page.getByRole('textbox', { name: 'Email' })
page.getByRole('checkbox', { name: 'Remember me' })
page.getByRole('radio', { name: 'Option A' })
page.getByRole('combobox', { name: 'Country' })

// Navigation
page.getByRole('navigation')
page.getByRole('menu')
page.getByRole('menuitem', { name: 'Settings' })

// Lists
page.getByRole('list')
page.getByRole('listitem')

// Tables
page.getByRole('table')
page.getByRole('row')
page.getByRole('cell', { name: 'Price' })

// Dialogs
page.getByRole('dialog')
page.getByRole('alertdialog')

// Tabs
page.getByRole('tab', { name: 'Settings' })
page.getByRole('tabpanel')
```

## Text-Based Selectors

```typescript
// Exact text
page.getByText('Welcome to our site')

// Partial match
page.getByText('Welcome', { exact: false })

// Regex
page.getByText(/welcome/i)

// With specific element
page.getByText('Submit').locator('button')
```

## Form Selectors

```typescript
// By label
page.getByLabel('Email address')
page.getByLabel('Password', { exact: true })

// By placeholder
page.getByPlaceholder('Enter your email')

// By title
page.getByTitle('Close dialog')

// By alt text (images)
page.getByAltText('Company logo')
```

## Test ID Selectors

```typescript
// HTML: <div data-testid="user-card">
page.getByTestId('user-card')

// Configure custom attribute
// playwright.config.ts
use: {
  testIdAttribute: 'data-test'
}
```

## CSS Selectors

```typescript
// Basic CSS
page.locator('.submit-button')
page.locator('#main-content')
page.locator('button.primary')

// Attribute selectors
page.locator('[data-state="active"]')
page.locator('input[type="email"]')

// Combinators
page.locator('.card > .header')
page.locator('.list .item')
page.locator('.parent ~ .sibling')
```

## Chaining and Filtering

```typescript
// Chain locators
page.getByRole('listitem').filter({ hasText: 'Product A' })

// Filter by child
page.getByRole('listitem').filter({
  has: page.getByRole('button', { name: 'Buy' })
})

// Filter by not having
page.getByRole('listitem').filter({
  hasNot: page.getByText('Out of stock')
})

// Nth element
page.getByRole('listitem').nth(0) // First
page.getByRole('listitem').nth(-1) // Last
page.getByRole('listitem').first()
page.getByRole('listitem').last()

// Within parent
const card = page.locator('.user-card')
card.getByRole('button', { name: 'Edit' })
```

## Relative Locators

```typescript
// Near another element
page.getByText('Password').locator('..').getByRole('textbox')

// Inside specific container
page.locator('.modal').getByRole('button', { name: 'Save' })

// Layout-based
page.locator('button:near(:text("Username"))')
page.locator('button:below(:text("Terms"))')
page.locator('button:right-of(:text("Quantity"))')
```

## Frame Selectors

```typescript
// By name or URL
const frame = page.frameLocator('iframe[name="checkout"]')
frame.getByRole('button', { name: 'Pay' })

// Nested frames
page.frameLocator('#outer').frameLocator('#inner').getByText('Hello')
```

## Shadow DOM

```typescript
// Piercing shadow DOM
page.locator('my-component').locator('button')

// Or with CSS
page.locator('my-component >> button')
```

## Waiting Strategies

```typescript
// Auto-waiting (built-in)
await page.getByRole('button').click() // Waits for visible, stable, enabled

// Explicit wait
await page.getByRole('button').waitFor({ state: 'visible' })
await page.getByRole('button').waitFor({ state: 'attached' })
await page.getByRole('button').waitFor({ state: 'detached' })

// Wait for text
await expect(page.getByText('Success')).toBeVisible()

// Wait for count
await expect(page.getByRole('listitem')).toHaveCount(5)

// Wait for network
await page.waitForResponse('**/api/data')
await page.waitForRequest('**/api/submit')

// Wait for load state
await page.waitForLoadState('networkidle')
await page.waitForLoadState('domcontentloaded')
```

## Anti-Patterns to Avoid

```typescript
// ❌ Avoid: Brittle selectors
page.locator('.sc-bwzfXH.jKHwWl')  // Generated class names
page.locator('div > div > div > button')  // Deep nesting
page.locator('//div[@class="container"]/div[2]/button')  // Complex XPath

// ❌ Avoid: Index-based without context
page.locator('button').nth(3)  // What's button 3?

// ❌ Avoid: Hardcoded waits
await page.waitForTimeout(3000)  // Use conditions instead

// ✅ Do: Semantic selectors
page.getByRole('button', { name: 'Add to cart' })
page.getByLabel('Search products')
page.getByTestId('shopping-cart')
```

## Debugging Selectors

```typescript
// Highlight element
await page.getByRole('button').highlight()

// Count matches
const count = await page.getByRole('listitem').count()

// Get all matches
const items = await page.getByRole('listitem').all()

// Inspect in codegen
npx playwright codegen http://localhost:3000

// Debug mode
PWDEBUG=1 npx playwright test
```

## Best Practices

```typescript
// 1. Create semantic locators in page objects
class ProductPage {
  readonly addToCartButton = this.page.getByRole('button', { name: 'Add to cart' });
  readonly productTitle = this.page.getByRole('heading', { level: 1 });
  readonly priceDisplay = this.page.getByTestId('price');

  getProductCard(name: string) {
    return this.page
      .getByRole('article')
      .filter({ has: this.page.getByText(name) });
  }
}

// 2. Use meaningful test IDs
// <button data-testid="checkout-submit">Pay Now</button>
page.getByTestId('checkout-submit')

// 3. Prefer visible text for assertions
await expect(page.getByRole('alert')).toHaveText('Payment successful')

// 4. Use filter for dynamic lists
page.getByRole('row').filter({ hasText: 'John Doe' })
```
