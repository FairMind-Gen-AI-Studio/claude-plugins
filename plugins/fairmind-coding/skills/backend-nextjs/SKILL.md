---
name: backend-nextjs
description: Use when implementing backend features with NextJS API routes, MongoDB integration, or server-side logic. This skill covers route handlers, database patterns, authentication flows, and API design for NextJS applications.
---

# Backend Development with NextJS

## Overview

This skill provides guidance for backend development using NextJS API routes with MongoDB as the database layer.

**Announce at start:** "I'm using the backend-nextjs skill for this backend implementation."

## When to Use

Use this skill when:
- Creating NextJS API route handlers
- Implementing MongoDB database operations
- Building authentication with NextAuth.js
- Designing REST APIs with NextJS
- Implementing server actions
- Adding middleware for request processing

## Core Workflow

### Step 1: Understand API Requirements

Before implementing:
1. Review the API contract from user story or work package
2. Identify data models and relationships
3. Understand authentication/authorization needs
4. Plan error handling strategy

### Step 2: Design Data Model

1. Define MongoDB schema structure
2. Plan indexes for query optimization
3. Consider data validation with Zod or Mongoose
4. Document relationships between collections

### Step 3: Implement API Routes

Follow this order:
1. **Types first** - Define request/response types
2. **Validation** - Add input validation with Zod
3. **Business logic** - Implement core functionality
4. **Error handling** - Handle all error cases
5. **Response formatting** - Consistent API responses

### Step 4: Test and Document

1. Test with various inputs including edge cases
2. Verify error handling
3. Check authentication flows
4. Document API endpoints

## Reference Files

| File | Content | When to Use |
|------|---------|-------------|
| `references/api-routes.md` | Route handlers, middleware | Creating API endpoints |
| `references/mongodb-patterns.md` | Schema design, queries | Database operations |
| `references/authentication.md` | NextAuth.js, JWT patterns | Auth implementation |
| `references/error-handling.md` | Error patterns, validation | Error handling |

## Key Principles

### API Design
- Use appropriate HTTP methods (GET, POST, PUT, DELETE)
- Return consistent response formats
- Include proper status codes
- Validate all inputs

### Database
- Use indexes for frequently queried fields
- Implement pagination for large datasets
- Use transactions for related operations
- Handle connection errors gracefully

### Security
- Validate and sanitize all inputs
- Implement rate limiting
- Use environment variables for secrets
- Apply principle of least privilege

### Performance
- Use database indexes effectively
- Implement caching where appropriate
- Use connection pooling
- Optimize query patterns

## Integration with Fairmind

When working on Fairmind tasks:
1. Use `fairmind-context` skill to gather requirements
2. Check work package for API specifications
3. Reference architectural blueprints for patterns
4. Update journal with API decisions

## Example Usage

```typescript
// Example: User API endpoint
// app/api/users/route.ts

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { connectDB } from '@/lib/mongodb';
import { User } from '@/models/User';

const createUserSchema = z.object({
  name: z.string().min(1),
  email: z.string().email(),
  role: z.enum(['user', 'admin']).default('user'),
});

export async function POST(request: NextRequest) {
  try {
    await connectDB();

    const body = await request.json();
    const validatedData = createUserSchema.parse(body);

    const user = await User.create(validatedData);

    return NextResponse.json(
      { success: true, data: user },
      { status: 201 }
    );
  } catch (error) {
    if (error instanceof z.ZodError) {
      return NextResponse.json(
        { success: false, error: error.errors },
        { status: 400 }
      );
    }
    return NextResponse.json(
      { success: false, error: 'Internal server error' },
      { status: 500 }
    );
  }
}
```

## Next Steps

After completing backend implementation:
- Use `fairmind-tdd` skill for API testing
- Request code review via Code Reviewer agent
- Document API endpoints for frontend team
