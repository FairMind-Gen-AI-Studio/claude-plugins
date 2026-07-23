# Playwright MCP Tools Reference

## Overview

The Playwright MCP server provides browser automation tools accessible via the MCP protocol. These tools enable browser control, element interaction, and screenshot capture.

## Navigation

### browser_navigate

Navigate to a URL:

```typescript
mcp__playwright__browser_navigate({
  url: "http://localhost:3000/login"
})
```

### browser_navigate_back

Go back to the previous page:

```typescript
mcp__playwright__browser_navigate_back()
```

## Page Inspection

### browser_snapshot

Capture accessibility snapshot (preferred for finding elements):

```typescript
mcp__playwright__browser_snapshot()
```

Returns structured accessibility tree with element refs.

### browser_take_screenshot

Capture visual screenshot:

```typescript
// Full viewport
mcp__playwright__browser_take_screenshot({
  filename: "current-page.png"
})

// Full page
mcp__playwright__browser_take_screenshot({
  filename: "full-page.png",
  fullPage: true
})

// Specific element
mcp__playwright__browser_take_screenshot({
  element: "Login form",
  ref: "form[name='login']",
  filename: "login-form.png"
})
```

### browser_console_messages

Get console messages:

```typescript
// All messages
mcp__playwright__browser_console_messages()

// Only errors
mcp__playwright__browser_console_messages({
  onlyErrors: true
})
```

### browser_network_requests

Get network requests:

```typescript
mcp__playwright__browser_network_requests()
```

## Element Interaction

### browser_click

Click an element:

```typescript
mcp__playwright__browser_click({
  element: "Submit button",
  ref: "button[type='submit']"
})

// With modifiers
mcp__playwright__browser_click({
  element: "Link",
  ref: "a.external",
  button: "middle",
  modifiers: ["Control"]
})

// Double click
mcp__playwright__browser_click({
  element: "Cell",
  ref: "td.editable",
  doubleClick: true
})
```

### browser_type

Type text into element:

```typescript
mcp__playwright__browser_type({
  element: "Email input",
  ref: "input[name='email']",
  text: "user@example.com"
})

// Type slowly (for autocomplete)
mcp__playwright__browser_type({
  element: "Search box",
  ref: "input[type='search']",
  text: "playwright",
  slowly: true
})

// Type and submit
mcp__playwright__browser_type({
  element: "Search box",
  ref: "input[type='search']",
  text: "query",
  submit: true
})
```

### browser_fill_form

Fill multiple form fields:

```typescript
mcp__playwright__browser_fill_form({
  fields: [
    {
      name: "Email",
      type: "textbox",
      ref: "input[name='email']",
      value: "user@example.com"
    },
    {
      name: "Password",
      type: "textbox",
      ref: "input[name='password']",
      value: "secure123"
    },
    {
      name: "Remember me",
      type: "checkbox",
      ref: "input[name='remember']",
      value: "true"
    },
    {
      name: "Country",
      type: "combobox",
      ref: "select[name='country']",
      value: "United States"
    }
  ]
})
```

### browser_select_option

Select dropdown option:

```typescript
mcp__playwright__browser_select_option({
  element: "Country selector",
  ref: "select[name='country']",
  values: ["US"]
})

// Multiple selection
mcp__playwright__browser_select_option({
  element: "Tags",
  ref: "select[multiple]",
  values: ["tag1", "tag2", "tag3"]
})
```

### browser_press_key

Press keyboard key:

```typescript
// Single key
mcp__playwright__browser_press_key({
  key: "Enter"
})

// Special keys
mcp__playwright__browser_press_key({
  key: "Escape"
})

mcp__playwright__browser_press_key({
  key: "ArrowDown"
})

// Key combination
mcp__playwright__browser_press_key({
  key: "Control+a"
})
```

### browser_hover

Hover over element:

```typescript
mcp__playwright__browser_hover({
  element: "Menu item",
  ref: "nav li.has-submenu"
})
```

### browser_drag

Drag and drop:

```typescript
mcp__playwright__browser_drag({
  startElement: "Draggable item",
  startRef: ".draggable",
  endElement: "Drop zone",
  endRef: ".dropzone"
})
```

## File Operations

### browser_file_upload

Upload files:

```typescript
mcp__playwright__browser_file_upload({
  paths: ["/path/to/document.pdf"]
})

// Multiple files
mcp__playwright__browser_file_upload({
  paths: [
    "/path/to/image1.png",
    "/path/to/image2.png"
  ]
})
```

## Dialog Handling

### browser_handle_dialog

Handle browser dialogs:

```typescript
// Accept alert/confirm
mcp__playwright__browser_handle_dialog({
  accept: true
})

// Dismiss
mcp__playwright__browser_handle_dialog({
  accept: false
})

// With prompt text
mcp__playwright__browser_handle_dialog({
  accept: true,
  promptText: "User input"
})
```

## Browser State

### browser_resize

Resize browser window:

```typescript
mcp__playwright__browser_resize({
  width: 1920,
  height: 1080
})

// Mobile viewport
mcp__playwright__browser_resize({
  width: 375,
  height: 667
})
```

### browser_tabs

Manage browser tabs:

```typescript
// List tabs
mcp__playwright__browser_tabs({
  action: "list"
})

// Open new tab
mcp__playwright__browser_tabs({
  action: "new"
})

// Select tab by index
mcp__playwright__browser_tabs({
  action: "select",
  index: 0
})

// Close current tab
mcp__playwright__browser_tabs({
  action: "close"
})
```

### browser_close

Close browser:

```typescript
mcp__playwright__browser_close()
```

## Waiting

### browser_wait_for

Wait for conditions:

```typescript
// Wait for text
mcp__playwright__browser_wait_for({
  text: "Success!"
})

// Wait for text to disappear
mcp__playwright__browser_wait_for({
  textGone: "Loading..."
})

// Wait for time (seconds)
mcp__playwright__browser_wait_for({
  time: 2
})
```

## JavaScript Evaluation

### browser_evaluate

Execute JavaScript:

```typescript
// Get page info
mcp__playwright__browser_evaluate({
  function: "() => document.title"
})

// Scroll to bottom
mcp__playwright__browser_evaluate({
  function: "() => window.scrollTo(0, document.body.scrollHeight)"
})

// Get element property
mcp__playwright__browser_evaluate({
  element: "Input field",
  ref: "input[name='email']",
  function: "(element) => element.value"
})

// Modify element
mcp__playwright__browser_evaluate({
  element: "Element",
  ref: "#my-element",
  function: "(el) => { el.style.border = '2px solid red'; }"
})
```

## Common Workflows

### Login Flow

```typescript
// 1. Navigate to login
mcp__playwright__browser_navigate({ url: "http://localhost:3000/login" })

// 2. Take snapshot to find elements
mcp__playwright__browser_snapshot()

// 3. Fill form
mcp__playwright__browser_fill_form({
  fields: [
    { name: "Email", type: "textbox", ref: "input[name='email']", value: "user@example.com" },
    { name: "Password", type: "textbox", ref: "input[name='password']", value: "password123" }
  ]
})

// 4. Click submit
mcp__playwright__browser_click({
  element: "Login button",
  ref: "button[type='submit']"
})

// 5. Wait for navigation
mcp__playwright__browser_wait_for({ text: "Dashboard" })

// 6. Screenshot result
mcp__playwright__browser_take_screenshot({ filename: "logged-in.png" })
```

### Form Validation Testing

```typescript
// 1. Navigate to form
mcp__playwright__browser_navigate({ url: "http://localhost:3000/register" })

// 2. Submit empty form
mcp__playwright__browser_click({
  element: "Submit",
  ref: "button[type='submit']"
})

// 3. Check for validation errors
mcp__playwright__browser_snapshot()

// 4. Screenshot error state
mcp__playwright__browser_take_screenshot({ filename: "validation-errors.png" })
```

### Visual Inspection

```typescript
// 1. Navigate
mcp__playwright__browser_navigate({ url: "http://localhost:3000" })

// 2. Resize for different viewports
mcp__playwright__browser_resize({ width: 375, height: 667 })
mcp__playwright__browser_take_screenshot({ filename: "mobile.png" })

mcp__playwright__browser_resize({ width: 1440, height: 900 })
mcp__playwright__browser_take_screenshot({ filename: "desktop.png" })
```
