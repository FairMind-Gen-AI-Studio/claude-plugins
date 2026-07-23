---
name: design-token-conventions
description: Conventions for naming and using design tokens in a Fairmind project. Use when authoring or auditing CSS, SCSS, or Tailwind code that touches color, spacing, typography, radius, shadow, or border. Defines the primitive vs semantic split, the file layout, and the anti-patterns that token-analyzer flags.
---

# Design Token Conventions

This skill is the reference for how design tokens are named, organized, and consumed across Fairmind projects.

## Two-layer model

Tokens are organized in two layers.

**Primitives** live in `styles/primitives.css`. They are raw values:

```css
:root {
  --color-blue-50: #eff6ff;
  --color-blue-500: #3366ff;
  --color-blue-700: #1d4ed8;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --radius-sm: 4px;
  --radius-md: 8px;
  --font-size-100: 12px;
  --font-size-200: 14px;
  --font-size-300: 16px;
}
```

**Semantic tokens** live in `styles/semantic.css`. They reference primitives and describe intent:

```css
:root {
  --color-action-primary: var(--color-blue-500);
  --color-action-primary-hover: var(--color-blue-700);
  --color-text-default: var(--color-neutral-900);
  --color-text-muted: var(--color-neutral-600);
  --color-text-on-primary: var(--color-neutral-0);
  --space-component-inline: var(--space-3);
  --space-component-block: var(--space-2);
  --radius-control: var(--radius-sm);
  --font-size-body: var(--font-size-200);
  --font-size-heading-md: var(--font-size-400);
}
```

## Rules

1. **Components reference semantic tokens only.** Never reference `--color-blue-500` from a component file. Use `--color-action-primary`.
2. **Primitives are not consumed directly.** They exist to feed semantic tokens.
3. **One value per token.** If you need a different value for a state (hover, disabled), declare a new semantic token.
4. **Naming pattern.** Semantic tokens follow `--<category>-<role>-<modifier>`: `--color-text-muted`, `--color-action-danger-hover`.
5. **Tailwind mapping.** When using Tailwind, map utilities to semantic tokens via `tailwind.config.js`:
   ```js
   theme: {
     colors: {
       'action-primary': 'var(--color-action-primary)',
       'text-default': 'var(--color-text-default)',
     },
     spacing: {
       'component-inline': 'var(--space-component-inline)',
     }
   }
   ```
   Never use Tailwind arbitrary values (`text-[#3366ff]`, `p-[7px]`) for any property that has a documented scale.

## Anti-patterns

These are flagged by `fairmind-token-analyzer` and `fairmind-design-verification`:

- Hex colors in any file outside `styles/primitives.css`
- `rgba(...)`, `hsl(...)` literals in components
- Tailwind arbitrary values for properties on the documented scale
- Inline `style={{ color: '#xxx' }}` in JSX
- A new semantic token created for a one-off value (use an existing one or extend the design system intentionally)
- Two semantic tokens resolving to the same primitive value (probably a duplicate)
- Component-local CSS variables that bypass the token layer

## When to extend the design system

Extending = adding a new semantic token. Three conditions must hold:

1. The visual need recurs (used in 2+ places already).
2. No existing semantic token fits.
3. The change is reviewed by a design owner (open a DS proposal).

If only 1 or 2 holds, reuse the closest existing token and document the trade-off in DESIGN.md.

## Files this skill expects

- `styles/primitives.css` — primitive declarations
- `styles/semantic.css` — semantic declarations
- `tailwind.config.js` — Tailwind utility mapping (if Tailwind is used)
- `DESIGN.md` — project-level design decisions and exceptions
