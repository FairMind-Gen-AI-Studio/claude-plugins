# Authentication Patterns Reference

## NextAuth.js Setup

### Configuration

```typescript
// app/api/auth/[...nextauth]/route.ts
import NextAuth from 'next-auth';
import { authOptions } from '@/lib/auth';

const handler = NextAuth(authOptions);
export { handler as GET, handler as POST };
```

```typescript
// lib/auth.ts
import { NextAuthOptions } from 'next-auth';
import CredentialsProvider from 'next-auth/providers/credentials';
import GoogleProvider from 'next-auth/providers/google';
import { MongoDBAdapter } from '@auth/mongodb-adapter';
import clientPromise from '@/lib/mongodb-client';
import { User } from '@/models/User';
import { connectDB } from '@/lib/mongodb';

export const authOptions: NextAuthOptions = {
  adapter: MongoDBAdapter(clientPromise),

  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),

    CredentialsProvider({
      name: 'credentials',
      credentials: {
        email: { label: 'Email', type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) {
          throw new Error('Invalid credentials');
        }

        await connectDB();

        const user = await User.findOne({ email: credentials.email })
          .select('+password');

        if (!user || !user.password) {
          throw new Error('Invalid credentials');
        }

        const isValid = await user.comparePassword(credentials.password);

        if (!isValid) {
          throw new Error('Invalid credentials');
        }

        return {
          id: user._id.toString(),
          email: user.email,
          name: user.name,
          role: user.role,
        };
      },
    }),
  ],

  session: {
    strategy: 'jwt',
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },

  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id;
        token.role = user.role;
      }
      return token;
    },

    async session({ session, token }) {
      if (session.user) {
        session.user.id = token.id as string;
        session.user.role = token.role as string;
      }
      return session;
    },
  },

  pages: {
    signIn: '/login',
    error: '/login',
  },

  events: {
    async signIn({ user, isNewUser }) {
      if (isNewUser) {
        // Handle new user registration
        console.log('New user registered:', user.email);
      }
    },
  },
};
```

### Type Extensions

```typescript
// types/next-auth.d.ts
import 'next-auth';

declare module 'next-auth' {
  interface User {
    role?: string;
  }

  interface Session {
    user: {
      id: string;
      name?: string | null;
      email?: string | null;
      image?: string | null;
      role?: string;
    };
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    id: string;
    role?: string;
  }
}
```

## Session Management

### Server-Side Session Access

```typescript
// In Server Components
import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth';

async function ProfilePage() {
  const session = await getServerSession(authOptions);

  if (!session) {
    redirect('/login');
  }

  return <div>Welcome, {session.user.name}</div>;
}
```

### API Route Protection

```typescript
// app/api/protected/route.ts
import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth';
import { NextResponse } from 'next/server';

export async function GET() {
  const session = await getServerSession(authOptions);

  if (!session) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  return NextResponse.json({ message: 'Protected data', user: session.user });
}
```

### Client-Side Session

```typescript
// components/UserMenu.tsx
'use client';

import { useSession, signIn, signOut } from 'next-auth/react';

export function UserMenu() {
  const { data: session, status } = useSession();

  if (status === 'loading') {
    return <div>Loading...</div>;
  }

  if (!session) {
    return <button onClick={() => signIn()}>Sign In</button>;
  }

  return (
    <div>
      <span>{session.user.name}</span>
      <button onClick={() => signOut()}>Sign Out</button>
    </div>
  );
}
```

## JWT Authentication (Custom)

### Token Generation

```typescript
// lib/jwt.ts
import jwt from 'jsonwebtoken';

const JWT_SECRET = process.env.JWT_SECRET!;

interface TokenPayload {
  userId: string;
  email: string;
  role: string;
}

export function generateToken(payload: TokenPayload): string {
  return jwt.sign(payload, JWT_SECRET, {
    expiresIn: '7d',
  });
}

export function verifyToken(token: string): TokenPayload {
  return jwt.verify(token, JWT_SECRET) as TokenPayload;
}

export function generateRefreshToken(userId: string): string {
  return jwt.sign({ userId }, JWT_SECRET, {
    expiresIn: '30d',
  });
}
```

### Auth Middleware

```typescript
// lib/middleware/auth.ts
import { NextRequest, NextResponse } from 'next/server';
import { verifyToken } from '@/lib/jwt';

export async function authMiddleware(request: NextRequest) {
  const authHeader = request.headers.get('authorization');

  if (!authHeader?.startsWith('Bearer ')) {
    return NextResponse.json(
      { error: 'Missing authorization header' },
      { status: 401 }
    );
  }

  const token = authHeader.substring(7);

  try {
    const payload = verifyToken(token);

    // Add user info to headers for downstream handlers
    const response = NextResponse.next();
    response.headers.set('x-user-id', payload.userId);
    response.headers.set('x-user-role', payload.role);

    return response;
  } catch (error) {
    return NextResponse.json(
      { error: 'Invalid or expired token' },
      { status: 401 }
    );
  }
}
```

### Login Endpoint

```typescript
// app/api/auth/login/route.ts
import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { connectDB } from '@/lib/mongodb';
import { User } from '@/models/User';
import { generateToken, generateRefreshToken } from '@/lib/jwt';

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { email, password } = loginSchema.parse(body);

    await connectDB();

    const user = await User.findOne({ email }).select('+password');

    if (!user || !(await user.comparePassword(password))) {
      return NextResponse.json(
        { error: 'Invalid credentials' },
        { status: 401 }
      );
    }

    const accessToken = generateToken({
      userId: user._id.toString(),
      email: user.email,
      role: user.role,
    });

    const refreshToken = generateRefreshToken(user._id.toString());

    const response = NextResponse.json({
      user: {
        id: user._id,
        name: user.name,
        email: user.email,
        role: user.role,
      },
      accessToken,
    });

    // Set refresh token as HTTP-only cookie
    response.cookies.set('refreshToken', refreshToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 30 * 24 * 60 * 60,
      path: '/',
    });

    return response;
  } catch (error) {
    if (error instanceof z.ZodError) {
      return NextResponse.json({ error: error.errors }, { status: 400 });
    }
    return NextResponse.json({ error: 'Server error' }, { status: 500 });
  }
}
```

## Role-Based Access Control

```typescript
// lib/middleware/rbac.ts
import { NextRequest, NextResponse } from 'next/server';
import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth';

type Role = 'user' | 'admin' | 'moderator';

export function withRole(allowedRoles: Role[]) {
  return async function middleware(request: NextRequest) {
    const session = await getServerSession(authOptions);

    if (!session) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    if (!allowedRoles.includes(session.user.role as Role)) {
      return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
    }

    return NextResponse.next();
  };
}

// Usage in route
// Only admins can access this endpoint
export async function DELETE(request: NextRequest) {
  const roleCheck = await withRole(['admin'])(request);
  if (roleCheck.status !== 200) return roleCheck;

  // Process admin-only request
}
```

## Password Reset Flow

```typescript
// app/api/auth/forgot-password/route.ts
export async function POST(request: NextRequest) {
  const { email } = await request.json();

  const user = await User.findOne({ email });
  if (!user) {
    // Don't reveal if user exists
    return NextResponse.json({ message: 'If email exists, reset link sent' });
  }

  const resetToken = crypto.randomBytes(32).toString('hex');
  const hashedToken = crypto
    .createHash('sha256')
    .update(resetToken)
    .digest('hex');

  await User.findByIdAndUpdate(user._id, {
    passwordResetToken: hashedToken,
    passwordResetExpires: Date.now() + 3600000, // 1 hour
  });

  // Send email with reset link
  await sendEmail({
    to: email,
    subject: 'Password Reset',
    html: `Reset link: ${process.env.APP_URL}/reset-password?token=${resetToken}`,
  });

  return NextResponse.json({ message: 'If email exists, reset link sent' });
}
```
