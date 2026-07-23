---
name: wcag-quick-reference
description: Quick reference for the WCAG 2.1 success criteria cited by the Fairmind accessibility agents. Use when interpreting an audit finding, when planning a fix, or when deciding whether a behavior meets AA. Includes criterion text, common failures, and recommended fixes.
---

# WCAG 2.1 Quick Reference

Concise reference for the criteria cited by `fairmind-wcag-compliance-auditor`, `fairmind-color-contrast-specialist`, `fairmind-keyboard-navigation-tester`, and `fairmind-screen-reader-tester`. Full text: https://www.w3.org/TR/WCAG21/.

## Perceivable

### 1.3.1 Info and Relationships (A)

Info, structure, and relationships conveyed visually must also be available programmatically.

- Common failures: tables without `<th>`, lists rendered with `<div>`, headings styled as paragraphs.
- Fix: use semantic HTML, `<th scope>`, `<ul>`/`<ol>`, `<h1>`–`<h6>`.

### 1.4.3 Contrast (Minimum) (AA)

Text and images of text have a contrast ratio of at least:
- 4.5:1 for normal text
- 3:1 for large text (≥18pt or ≥14pt bold)

- Common failures: placeholder text, disabled controls used as info, gray-on-white body copy.
- Fix: use a token with sufficient ratio; do not lower opacity to "soften" text.

### 1.4.6 Contrast (Enhanced) (AAA)

- 7:1 for normal text, 4.5:1 for large text.

### 1.4.11 Non-text Contrast (AA)

UI components (input borders, focus rings) and informative graphics need at least 3:1 against adjacent colors.

- Common failures: 1px gray borders on white, focus rings tinted the same color as the focused element.
- Fix: thicker indicator, higher-contrast color, or both.

## Operable

### 2.1.1 Keyboard (A)

All functionality must be operable from a keyboard.

- Common failures: drag-and-drop with no keyboard alternative, menus that only open on hover.
- Fix: provide explicit keyboard handlers, keyboard alternatives for pointer-only gestures.

### 2.2.1 Timing Adjustable (A)

If there is a time limit, the user must be able to turn off, adjust, or extend it.

### 2.4.3 Focus Order (A)

The focus order must preserve meaning and operability.

- Common failures: DOM order differs from visual order, modal focus jumps to body.
- Fix: align DOM with visual order; manage focus explicitly when opening dialogs.

### 2.4.6 Headings and Labels (AA)

Headings and labels describe topic or purpose.

- Fix: avoid generic "Form" or "Section"; describe the content.

### 2.4.7 Focus Visible (AA)

Any keyboard-operable interface has a visible focus indicator.

- Common failures: `outline: none` with no replacement, focus ring same color as adjacent surface.
- Fix: use `:focus-visible` with a token-backed indicator that meets 1.4.11.

## Understandable

### 3.1.1 Language of Page (A)

The page's primary language must be programmatically set: `<html lang="en">`.

### 3.1.2 Language of Parts (AA)

Foreign-language fragments need their own `lang` attribute.

### 3.2.5 Change on Request (AAA)

No automatic change of context (page reload, redirect) without explicit user request.

### 3.3.1 Error Identification (A)

When an input error is detected, the error is identified and described in text.

- Fix: associate the error message with the input via `aria-describedby`; set `aria-invalid="true"` on the failing field.

### 3.3.3 Error Suggestion (AA)

When the system knows a correction, suggest it.

## Robust

### 4.1.2 Name, Role, Value (A)

For all UI components, name, role, and state must be available to assistive technology.

- Common failures: `<div onClick>` with no role, custom widgets without ARIA state.
- Fix: use native elements; if not possible, set `role` and the relevant `aria-*` attributes.

### 4.1.3 Status Messages (AA)

Status messages must be programmatically determined through role or properties so assistive tech can announce them without focus change.

- Common failures: toasts injected into DOM with no live region.
- Fix: render messages inside an `aria-live="polite"` region (or `assertive` for urgent), or use `role="status"`/`role="alert"`.

## Decision shortcuts

| Question | Answer |
|---|---|
| Body text contrast | ≥ 4.5:1 (AA), ≥ 7:1 (AAA) |
| Large text contrast | ≥ 3:1 (AA), ≥ 4.5:1 (AAA) |
| Input border contrast | ≥ 3:1 against adjacent (AA) |
| Focus indicator contrast | ≥ 3:1 against adjacent (AA) |
| Toast announcement | needs `aria-live` or `role="status"` (4.1.3) |
| Dialog | `role="dialog"` + `aria-modal="true"` + `aria-labelledby` |
| Icon-only button | `aria-label` describing the action |
