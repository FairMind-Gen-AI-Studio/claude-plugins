# NextJS Conventions Reference

## App Router Structure

### Directory Organization

```
app/
├── layout.tsx           # Root layout (wraps all pages)
├── page.tsx             # Home page (/)
├── loading.tsx          # Loading UI for this route
├── error.tsx            # Error UI for this route
├── not-found.tsx        # 404 page
├── globals.css          # Global styles
├── dashboard/
│   ├── layout.tsx       # Dashboard layout (nested)
│   ├── page.tsx         # /dashboard
│   ├── loading.tsx      # Dashboard loading state
│   └── settings/
│       └── page.tsx     # /dashboard/settings
├── users/
│   ├── page.tsx         # /users
│   └── [id]/
│       ├── page.tsx     # /users/[id]
│       └── edit/
│           └── page.tsx # /users/[id]/edit
├── api/
│   └── users/
│       ├── route.ts     # /api/users (GET, POST)
│       └── [id]/
│           └── route.ts # /api/users/[id] (GET, PUT, DELETE)
└── (marketing)/         # Route group (no URL segment)
    ├── about/
    │   └── page.tsx     # /about
    └── contact/
        └── page.tsx     # /contact
```

## Server vs Client Components

### Server Components (Default)

```tsx
// app/users/page.tsx - Server Component
// Can fetch data directly, no useState/useEffect
async function UsersPage() {
  const users = await db.user.findMany();

  return (
    <div>
      <h1>Users</h1>
      <UserList users={users} />
    </div>
  );
}

export default UsersPage;
```

### Client Components

```tsx
// components/counter.tsx - Client Component
'use client';

import { useState } from 'react';

export function Counter() {
  const [count, setCount] = useState(0);

  return (
    <button onClick={() => setCount(c => c + 1)}>
      Count: {count}
    </button>
  );
}
```

### When to Use Each

| Server Components | Client Components |
|-------------------|-------------------|
| Fetch data | useState, useEffect |
| Access backend resources | Event listeners (onClick, onChange) |
| Keep sensitive info on server | Browser APIs |
| Large dependencies | Interactivity |
| No JS sent to client | Third-party client libraries |

## Data Fetching

### Server Component Fetching

```tsx
// Automatic deduplication and caching
async function getUser(id: string) {
  const res = await fetch(`https://api.example.com/users/${id}`, {
    next: { revalidate: 3600 }, // Revalidate every hour
  });
  return res.json();
}

async function UserProfile({ userId }: { userId: string }) {
  const user = await getUser(userId);
  return <div>{user.name}</div>;
}
```

### Parallel Data Fetching

```tsx
async function Dashboard() {
  // Fetch in parallel
  const [user, posts, analytics] = await Promise.all([
    getUser(),
    getPosts(),
    getAnalytics(),
  ]);

  return (
    <div>
      <UserInfo user={user} />
      <PostList posts={posts} />
      <AnalyticsChart data={analytics} />
    </div>
  );
}
```

### Server Actions

```tsx
// app/actions.ts
'use server';

import { revalidatePath } from 'next/cache';

export async function createUser(formData: FormData) {
  const name = formData.get('name');
  const email = formData.get('email');

  await db.user.create({
    data: { name, email },
  });

  revalidatePath('/users');
}

// Usage in component
import { createUser } from '@/app/actions';

function CreateUserForm() {
  return (
    <form action={createUser}>
      <input name="name" required />
      <input name="email" type="email" required />
      <button type="submit">Create User</button>
    </form>
  );
}
```

## Route Handlers (API Routes)

### Basic Route Handler

```tsx
// app/api/users/route.ts
import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const page = searchParams.get('page') || '1';

  const users = await db.user.findMany({
    skip: (parseInt(page) - 1) * 10,
    take: 10,
  });

  return NextResponse.json(users);
}

export async function POST(request: NextRequest) {
  const body = await request.json();

  const user = await db.user.create({
    data: body,
  });

  return NextResponse.json(user, { status: 201 });
}
```

### Dynamic Route Handler

```tsx
// app/api/users/[id]/route.ts
import { NextRequest, NextResponse } from 'next/server';

export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  const user = await db.user.findUnique({
    where: { id: params.id },
  });

  if (!user) {
    return NextResponse.json(
      { error: 'User not found' },
      { status: 404 }
    );
  }

  return NextResponse.json(user);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  await db.user.delete({
    where: { id: params.id },
  });

  return new NextResponse(null, { status: 204 });
}
```

## Layouts and Templates

### Root Layout

```tsx
// app/layout.tsx
import { Inter } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'My App',
  description: 'Description here',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
```

### Nested Layout

```tsx
// app/dashboard/layout.tsx
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex">
      <Sidebar />
      <div className="flex-1 p-6">{children}</div>
    </div>
  );
}
```

## Loading and Error States

### Loading UI

```tsx
// app/dashboard/loading.tsx
export default function Loading() {
  return (
    <div className="animate-pulse">
      <div className="h-8 bg-gray-200 rounded w-1/4 mb-4" />
      <div className="h-4 bg-gray-200 rounded w-3/4 mb-2" />
      <div className="h-4 bg-gray-200 rounded w-1/2" />
    </div>
  );
}
```

### Error Handling

```tsx
// app/dashboard/error.tsx
'use client';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="p-6 bg-red-50 rounded-lg">
      <h2 className="text-lg font-semibold text-red-800">
        Something went wrong
      </h2>
      <p className="text-red-600 mt-2">{error.message}</p>
      <button
        onClick={reset}
        className="mt-4 px-4 py-2 bg-red-600 text-white rounded"
      >
        Try again
      </button>
    </div>
  );
}
```

## Middleware

```tsx
// middleware.ts (root level)
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  // Check authentication
  const token = request.cookies.get('token');

  if (!token && request.nextUrl.pathname.startsWith('/dashboard')) {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  // Add headers
  const response = NextResponse.next();
  response.headers.set('x-custom-header', 'value');

  return response;
}

export const config = {
  matcher: ['/dashboard/:path*', '/api/:path*'],
};
```

## Image Optimization

```tsx
import Image from 'next/image';

function Avatar({ user }: { user: User }) {
  return (
    <Image
      src={user.avatarUrl}
      alt={user.name}
      width={48}
      height={48}
      className="rounded-full"
      priority={false}
      placeholder="blur"
      blurDataURL="data:image/jpeg;base64,..."
    />
  );
}
```

## Metadata and SEO

```tsx
// Static metadata
export const metadata = {
  title: 'Page Title',
  description: 'Page description',
  openGraph: {
    title: 'Page Title',
    description: 'Page description',
    images: ['/og-image.jpg'],
  },
};

// Dynamic metadata
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const product = await getProduct(params.id);

  return {
    title: product.name,
    description: product.description,
    openGraph: {
      images: [product.imageUrl],
    },
  };
}
```
