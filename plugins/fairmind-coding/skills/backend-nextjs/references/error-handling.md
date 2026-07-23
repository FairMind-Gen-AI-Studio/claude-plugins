# Error Handling Reference

## Error Types

### Custom Error Classes

```typescript
// lib/errors.ts
export class AppError extends Error {
  constructor(
    public message: string,
    public statusCode: number,
    public code?: string
  ) {
    super(message);
    this.name = 'AppError';
    Error.captureStackTrace(this, this.constructor);
  }
}

export class ValidationError extends AppError {
  constructor(message: string, public details?: Record<string, string[]>) {
    super(message, 400, 'VALIDATION_ERROR');
    this.name = 'ValidationError';
  }
}

export class NotFoundError extends AppError {
  constructor(resource: string) {
    super(`${resource} not found`, 404, 'NOT_FOUND');
    this.name = 'NotFoundError';
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = 'Unauthorized') {
    super(message, 401, 'UNAUTHORIZED');
    this.name = 'UnauthorizedError';
  }
}

export class ForbiddenError extends AppError {
  constructor(message = 'Forbidden') {
    super(message, 403, 'FORBIDDEN');
    this.name = 'ForbiddenError';
  }
}

export class ConflictError extends AppError {
  constructor(message: string) {
    super(message, 409, 'CONFLICT');
    this.name = 'ConflictError';
  }
}

export class RateLimitError extends AppError {
  constructor(retryAfter?: number) {
    super('Too many requests', 429, 'RATE_LIMIT');
    this.name = 'RateLimitError';
  }
}
```

## Error Handler

### Centralized Error Handler

```typescript
// lib/error-handler.ts
import { NextResponse } from 'next/server';
import { ZodError } from 'zod';
import mongoose from 'mongoose';
import { AppError } from './errors';

interface ErrorResponse {
  success: false;
  error: {
    message: string;
    code?: string;
    details?: unknown;
  };
}

export function handleError(error: unknown): NextResponse<ErrorResponse> {
  console.error('Error:', error);

  // Custom app errors
  if (error instanceof AppError) {
    return NextResponse.json(
      {
        success: false,
        error: {
          message: error.message,
          code: error.code,
        },
      },
      { status: error.statusCode }
    );
  }

  // Zod validation errors
  if (error instanceof ZodError) {
    const details = error.errors.reduce((acc, err) => {
      const path = err.path.join('.');
      if (!acc[path]) acc[path] = [];
      acc[path].push(err.message);
      return acc;
    }, {} as Record<string, string[]>);

    return NextResponse.json(
      {
        success: false,
        error: {
          message: 'Validation failed',
          code: 'VALIDATION_ERROR',
          details,
        },
      },
      { status: 400 }
    );
  }

  // MongoDB duplicate key error
  if (
    error instanceof mongoose.mongo.MongoServerError &&
    error.code === 11000
  ) {
    const field = Object.keys(error.keyPattern)[0];
    return NextResponse.json(
      {
        success: false,
        error: {
          message: `${field} already exists`,
          code: 'DUPLICATE_KEY',
        },
      },
      { status: 409 }
    );
  }

  // MongoDB validation error
  if (error instanceof mongoose.Error.ValidationError) {
    const details = Object.entries(error.errors).reduce((acc, [key, err]) => {
      acc[key] = [err.message];
      return acc;
    }, {} as Record<string, string[]>);

    return NextResponse.json(
      {
        success: false,
        error: {
          message: 'Validation failed',
          code: 'VALIDATION_ERROR',
          details,
        },
      },
      { status: 400 }
    );
  }

  // MongoDB cast error (invalid ObjectId)
  if (error instanceof mongoose.Error.CastError) {
    return NextResponse.json(
      {
        success: false,
        error: {
          message: `Invalid ${error.path}`,
          code: 'INVALID_ID',
        },
      },
      { status: 400 }
    );
  }

  // Generic error
  return NextResponse.json(
    {
      success: false,
      error: {
        message: 'Internal server error',
        code: 'INTERNAL_ERROR',
      },
    },
    { status: 500 }
  );
}
```

### Route Wrapper

```typescript
// lib/api-handler.ts
import { NextRequest, NextResponse } from 'next/server';
import { handleError } from './error-handler';

type Handler = (request: NextRequest, context?: any) => Promise<NextResponse>;

export function withErrorHandling(handler: Handler): Handler {
  return async (request: NextRequest, context?: any) => {
    try {
      return await handler(request, context);
    } catch (error) {
      return handleError(error);
    }
  };
}

// Usage
export const GET = withErrorHandling(async (request) => {
  const users = await getUsers();
  return NextResponse.json({ success: true, data: users });
});
```

## Input Validation

### Zod Schemas

```typescript
// lib/validations/user.ts
import { z } from 'zod';

export const createUserSchema = z.object({
  name: z
    .string()
    .min(1, 'Name is required')
    .max(100, 'Name cannot exceed 100 characters')
    .trim(),
  email: z
    .string()
    .email('Invalid email format')
    .toLowerCase()
    .trim(),
  password: z
    .string()
    .min(8, 'Password must be at least 8 characters')
    .regex(
      /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/,
      'Password must contain uppercase, lowercase, and number'
    ),
  role: z.enum(['user', 'admin']).default('user'),
});

export const updateUserSchema = createUserSchema.partial().omit({ password: true });

export const paginationSchema = z.object({
  page: z.coerce.number().int().positive().default(1),
  limit: z.coerce.number().int().min(1).max(100).default(10),
  sort: z.enum(['asc', 'desc']).default('desc'),
  sortBy: z.string().default('createdAt'),
});

export type CreateUserInput = z.infer<typeof createUserSchema>;
export type UpdateUserInput = z.infer<typeof updateUserSchema>;
export type PaginationInput = z.infer<typeof paginationSchema>;
```

### Validation Helper

```typescript
// lib/validate.ts
import { z, ZodSchema } from 'zod';
import { NextRequest } from 'next/server';
import { ValidationError } from './errors';

export async function validateBody<T>(
  request: NextRequest,
  schema: ZodSchema<T>
): Promise<T> {
  const body = await request.json();
  return schema.parse(body);
}

export function validateQuery<T>(
  request: NextRequest,
  schema: ZodSchema<T>
): T {
  const searchParams = Object.fromEntries(
    request.nextUrl.searchParams.entries()
  );
  return schema.parse(searchParams);
}

export function validateParams<T>(
  params: Record<string, string>,
  schema: ZodSchema<T>
): T {
  return schema.parse(params);
}
```

## Response Helpers

```typescript
// lib/response.ts
import { NextResponse } from 'next/server';

interface SuccessResponse<T> {
  success: true;
  data: T;
  meta?: {
    page?: number;
    limit?: number;
    total?: number;
    totalPages?: number;
  };
}

export function successResponse<T>(
  data: T,
  meta?: SuccessResponse<T>['meta'],
  status = 200
): NextResponse<SuccessResponse<T>> {
  return NextResponse.json({ success: true, data, meta }, { status });
}

export function createdResponse<T>(
  data: T
): NextResponse<SuccessResponse<T>> {
  return successResponse(data, undefined, 201);
}

export function noContentResponse(): NextResponse {
  return new NextResponse(null, { status: 204 });
}
```

## Logging

```typescript
// lib/logger.ts
type LogLevel = 'debug' | 'info' | 'warn' | 'error';

interface LogEntry {
  level: LogLevel;
  message: string;
  timestamp: string;
  context?: Record<string, unknown>;
}

class Logger {
  private log(level: LogLevel, message: string, context?: Record<string, unknown>) {
    const entry: LogEntry = {
      level,
      message,
      timestamp: new Date().toISOString(),
      context,
    };

    if (level === 'error') {
      console.error(JSON.stringify(entry));
    } else {
      console.log(JSON.stringify(entry));
    }
  }

  debug(message: string, context?: Record<string, unknown>) {
    if (process.env.NODE_ENV === 'development') {
      this.log('debug', message, context);
    }
  }

  info(message: string, context?: Record<string, unknown>) {
    this.log('info', message, context);
  }

  warn(message: string, context?: Record<string, unknown>) {
    this.log('warn', message, context);
  }

  error(message: string, error?: Error, context?: Record<string, unknown>) {
    this.log('error', message, {
      ...context,
      error: error ? {
        name: error.name,
        message: error.message,
        stack: error.stack,
      } : undefined,
    });
  }
}

export const logger = new Logger();

// Usage in error handler
catch (error) {
  logger.error('Request failed', error as Error, {
    path: request.nextUrl.pathname,
    method: request.method,
  });
  return handleError(error);
}
```

## Complete Route Example

```typescript
// app/api/users/route.ts
import { NextRequest } from 'next/server';
import { connectDB } from '@/lib/mongodb';
import { User } from '@/models/User';
import { withErrorHandling } from '@/lib/api-handler';
import { validateBody, validateQuery } from '@/lib/validate';
import { createUserSchema, paginationSchema } from '@/lib/validations/user';
import { successResponse, createdResponse } from '@/lib/response';
import { ConflictError } from '@/lib/errors';

export const GET = withErrorHandling(async (request: NextRequest) => {
  await connectDB();

  const { page, limit, sort, sortBy } = validateQuery(
    request,
    paginationSchema
  );

  const skip = (page - 1) * limit;

  const [users, total] = await Promise.all([
    User.find()
      .sort({ [sortBy]: sort === 'asc' ? 1 : -1 })
      .skip(skip)
      .limit(limit)
      .lean(),
    User.countDocuments(),
  ]);

  return successResponse(users, {
    page,
    limit,
    total,
    totalPages: Math.ceil(total / limit),
  });
});

export const POST = withErrorHandling(async (request: NextRequest) => {
  await connectDB();

  const data = await validateBody(request, createUserSchema);

  const existing = await User.findOne({ email: data.email });
  if (existing) {
    throw new ConflictError('Email already registered');
  }

  const user = await User.create(data);

  return createdResponse({
    id: user._id,
    name: user.name,
    email: user.email,
    role: user.role,
  });
});
```
