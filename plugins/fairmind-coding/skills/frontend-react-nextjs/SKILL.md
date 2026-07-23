---
name: frontend-react-nextjs
description: Use when implementing frontend features with React, NextJS, TypeScript, Tailwind CSS, or Shadcn UI. This skill provides patterns, conventions, and best practices for modern React development including component architecture, state management with Zustand, and responsive design.
---

# Frontend Development with React & NextJS

## Overview

This skill provides guidance for frontend development using the React/NextJS/TypeScript stack with Tailwind CSS and Shadcn UI components.

**Announce at start:** "I'm using the frontend-react-nextjs skill for this frontend implementation."

## When to Use

Use this skill when:
- Building React components with TypeScript
- Implementing NextJS pages or app router components
- Creating responsive layouts with Tailwind CSS
- Integrating or customizing Shadcn UI components
- Managing state with Zustand or React Context
- Implementing forms, data fetching, or authentication UI

## Core Workflow

### Step 1: Understand Requirements

Before writing any code:
1. Review the user story acceptance criteria
2. Identify UI/UX requirements from mockups or descriptions
3. Check for existing components that can be reused
4. Understand the data flow and API contracts

### Step 2: Design Component Architecture

1. Break down the UI into logical components
2. Define component hierarchy (parent/child relationships)
3. Identify shared vs. page-specific components
4. Plan props interfaces and state management

### Step 3: Implement Components

Follow this order:
1. **Types first** - Define TypeScript interfaces
2. **Component skeleton** - Basic structure with props
3. **State and logic** - Add hooks and handlers
4. **Styling** - Apply Tailwind classes
5. **Accessibility** - Add ARIA attributes and keyboard support

### Step 4: Integrate and Test

1. Connect to API endpoints or mock data
2. Test all user interactions
3. Verify responsive behavior
4. Check accessibility compliance

## Reference Files

For detailed patterns and examples, load the appropriate reference file:

| File | Content | When to Use |
|------|---------|-------------|
| `references/react-patterns.md` | Component patterns, hooks, composition | Building any React component |
| `references/nextjs-conventions.md` | App router, server components, routing | NextJS-specific features |
| `references/typescript-guidelines.md` | Type patterns, interfaces, generics | Type definitions, complex types |
| `references/tailwind-shadcn.md` | UI components, styling patterns | Styling and UI components |
| `references/zustand-state.md` | State management patterns | Global or complex state |

## Key Principles

### Component Design
- Prefer composition over inheritance
- Keep components focused on single responsibility
- Extract reusable logic into custom hooks
- Use TypeScript to enforce prop contracts

### State Management
- Local state for component-specific data
- Zustand for global/shared state
- Server state with React Query or SWR
- Avoid prop drilling with context or state managers

### Styling
- Use Tailwind utility classes primarily
- Create custom components for repeated patterns
- Follow mobile-first responsive design
- Maintain consistent spacing and typography

### Performance
- Use `React.memo` for expensive renders
- Implement lazy loading for routes and large components
- Optimize images with next/image
- Avoid unnecessary re-renders with proper dependency arrays

## Integration with Fairmind

When working on Fairmind tasks:
1. Use `fairmind-context` skill to gather requirements first
2. Check work package for specific UI requirements
3. Reference architectural blueprints for component patterns
4. Update journal with UI decisions and component structure

## Error Handling

**If design requirements are unclear:**
- Ask for mockups or detailed descriptions
- Document assumptions in the journal
- Propose component structure for approval

**If API contracts are missing:**
- Define provisional TypeScript interfaces
- Note dependencies on backend work
- Use mock data during development

## Example Usage

```typescript
// Example: Creating a user profile component

// 1. Define types
interface UserProfileProps {
  userId: string;
  onUpdate?: (user: User) => void;
}

// 2. Create component with proper patterns
export function UserProfile({ userId, onUpdate }: UserProfileProps) {
  // 3. Use appropriate hooks
  const { user, isLoading } = useUser(userId);

  // 4. Handle states
  if (isLoading) return <ProfileSkeleton />;
  if (!user) return <NotFound />;

  // 5. Render with Tailwind + Shadcn
  return (
    <Card className="p-6">
      <Avatar src={user.avatar} fallback={user.initials} />
      <h2 className="text-lg font-semibold">{user.name}</h2>
      {/* ... */}
    </Card>
  );
}
```

## Next Steps

After completing frontend implementation:
- Use `fairmind-tdd` skill for component testing
- Request code review via Code Reviewer agent
- Update journal with final component inventory
