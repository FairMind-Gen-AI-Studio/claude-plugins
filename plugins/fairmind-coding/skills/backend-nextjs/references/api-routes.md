# NextJS API Routes Reference

## Route Handler Basics

### File-based Routing

```
app/api/
├── users/
│   ├── route.ts           # GET /api/users, POST /api/users
│   └── [id]/
│       └── route.ts       # GET/PUT/DELETE /api/users/:id
├── auth/
│   ├── login/
│   │   └── route.ts       # POST /api/auth/login
│   └── [...nextauth]/
│       └── route.ts       # NextAuth.js handler
└── health/
    └── route.ts           # GET /api/health
```

### HTTP Methods

```typescript
// app/api/users/route.ts
import { NextRequest, NextResponse } from 'next/server';

// GET /api/users
export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const page = parseInt(searchParams.get('page') || '1');
  const limit = parseInt(searchParams.get('limit') || '10');

  // Fetch users with pagination
  const users = await getUsersPaginated(page, limit);

  return NextResponse.json(users);
}

// POST /api/users
export async function POST(request: NextRequest) {
  const body = await request.json();

  const user = await createUser(body);

  return NextResponse.json(user, { status: 201 });
}
```

### Dynamic Routes

```typescript
// app/api/users/[id]/route.ts
import { NextRequest, NextResponse } from 'next/server';

interface RouteParams {
  params: { id: string };
}

// GET /api/users/:id
export async function GET(
  request: NextRequest,
  { params }: RouteParams
) {
  const user = await getUserById(params.id);

  if (!user) {
    return NextResponse.json(
      { error: 'User not found' },
      { status: 404 }
    );
  }

  return NextResponse.json(user);
}

// PUT /api/users/:id
export async function PUT(
  request: NextRequest,
  { params }: RouteParams
) {
  const body = await request.json();
  const user = await updateUser(params.id, body);

  return NextResponse.json(user);
}

// DELETE /api/users/:id
export async function DELETE(
  request: NextRequest,
  { params }: RouteParams
) {
  await deleteUser(params.id);

  return new NextResponse(null, { status: 204 });
}
```

## Request Handling

### Headers

```typescript
export async function GET(request: NextRequest) {
  // Read headers
  const authorization = request.headers.get('authorization');
  const contentType = request.headers.get('content-type');

  // Create response with headers
  return NextResponse.json(data, {
    headers: {
      'Cache-Control': 'max-age=3600',
      'X-Custom-Header': 'value',
    },
  });
}
```

### Cookies

```typescript
import { cookies } from 'next/headers';

export async function GET() {
  const cookieStore = cookies();
  const token = cookieStore.get('token');

  return NextResponse.json({ token: token?.value });
}

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ success: true });

  // Set cookie
  response.cookies.set('session', 'value', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 7, // 1 week
  });

  return response;
}
```

### Query Parameters

```typescript
export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;

  // Get single value
  const query = searchParams.get('q');

  // Get all values for a key
  const tags = searchParams.getAll('tag');

  // Check if parameter exists
  const hasFilter = searchParams.has('filter');

  // Parse pagination
  const page = parseInt(searchParams.get('page') || '1');
  const limit = Math.min(parseInt(searchParams.get('limit') || '10'), 100);

  return NextResponse.json({
    query,
    tags,
    pagination: { page, limit },
  });
}
```

## Response Patterns

### Standard JSON Response

```typescript
interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  meta?: {
    page: number;
    limit: number;
    total: number;
  };
}

function successResponse<T>(data: T, meta?: ApiResponse<T>['meta']) {
  return NextResponse.json<ApiResponse<T>>({
    success: true,
    data,
    meta,
  });
}

function errorResponse(message: string, status: number) {
  return NextResponse.json<ApiResponse<never>>(
    { success: false, error: message },
    { status }
  );
}

// Usage
export async function GET() {
  try {
    const users = await getUsers();
    return successResponse(users, { page: 1, limit: 10, total: 100 });
  } catch (error) {
    return errorResponse('Failed to fetch users', 500);
  }
}
```

### Streaming Response

```typescript
export async function GET() {
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      for (let i = 0; i < 10; i++) {
        const data = JSON.stringify({ count: i }) + '\n';
        controller.enqueue(encoder.encode(data));
        await new Promise((r) => setTimeout(r, 100));
      }
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    },
  });
}
```

## Middleware

### Global Middleware

```typescript
// middleware.ts (root level)
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  // Check auth for API routes
  if (request.nextUrl.pathname.startsWith('/api/')) {
    const token = request.headers.get('authorization');

    if (!token && !request.nextUrl.pathname.startsWith('/api/auth')) {
      return NextResponse.json(
        { error: 'Unauthorized' },
        { status: 401 }
      );
    }
  }

  // Add request ID
  const response = NextResponse.next();
  response.headers.set('X-Request-Id', crypto.randomUUID());

  return response;
}

export const config = {
  matcher: '/api/:path*',
};
```

### Route-level Middleware Pattern

```typescript
// lib/middleware/auth.ts
import { NextRequest, NextResponse } from 'next/server';
import { verifyToken } from '@/lib/auth';

type Handler = (
  request: NextRequest,
  context: { params: Record<string, string>; user: User }
) => Promise<NextResponse>;

export function withAuth(handler: Handler) {
  return async (request: NextRequest, context: { params: Record<string, string> }) => {
    const token = request.headers.get('authorization')?.split(' ')[1];

    if (!token) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    try {
      const user = await verifyToken(token);
      return handler(request, { ...context, user });
    } catch {
      return NextResponse.json({ error: 'Invalid token' }, { status: 401 });
    }
  };
}

// Usage in route
export const GET = withAuth(async (request, { user }) => {
  const data = await getDataForUser(user.id);
  return NextResponse.json(data);
});
```

## Rate Limiting

```typescript
// lib/rate-limit.ts
import { LRUCache } from 'lru-cache';

interface RateLimitOptions {
  interval: number;
  uniqueTokenPerInterval: number;
}

export function rateLimit(options: RateLimitOptions) {
  const tokenCache = new LRUCache<string, number[]>({
    max: options.uniqueTokenPerInterval,
    ttl: options.interval,
  });

  return {
    check: (limit: number, token: string) => {
      const tokenCount = tokenCache.get(token) || [0];
      const currentCount = tokenCount[0];

      if (currentCount >= limit) {
        return { success: false, remaining: 0 };
      }

      tokenCache.set(token, [currentCount + 1]);
      return { success: true, remaining: limit - currentCount - 1 };
    },
  };
}

// Usage
const limiter = rateLimit({
  interval: 60 * 1000, // 1 minute
  uniqueTokenPerInterval: 500,
});

export async function POST(request: NextRequest) {
  const ip = request.ip || 'anonymous';
  const { success, remaining } = limiter.check(10, ip);

  if (!success) {
    return NextResponse.json(
      { error: 'Rate limit exceeded' },
      {
        status: 429,
        headers: { 'X-RateLimit-Remaining': '0' },
      }
    );
  }

  // Process request...
}
```

## File Uploads

```typescript
export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get('file') as File;

  if (!file) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 });
  }

  // Validate file type
  const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
  if (!allowedTypes.includes(file.type)) {
    return NextResponse.json({ error: 'Invalid file type' }, { status: 400 });
  }

  // Validate file size (5MB)
  if (file.size > 5 * 1024 * 1024) {
    return NextResponse.json({ error: 'File too large' }, { status: 400 });
  }

  const bytes = await file.arrayBuffer();
  const buffer = Buffer.from(bytes);

  // Save to storage (local, S3, etc.)
  const url = await uploadToStorage(buffer, file.name);

  return NextResponse.json({ url });
}
```

## CORS Configuration

```typescript
// app/api/public/route.ts
export async function GET() {
  return NextResponse.json(
    { data: 'public data' },
    {
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      },
    }
  );
}

export async function OPTIONS() {
  return new NextResponse(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}
```
