# TypeScript Guidelines Reference

## Type Definitions

### Basic Types and Interfaces

```typescript
// Prefer interfaces for object shapes
interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  createdAt: Date;
  metadata?: UserMetadata; // Optional property
}

// Use types for unions, primitives, and utilities
type UserRole = 'admin' | 'user' | 'guest';
type UserId = string;

// Extend interfaces
interface AdminUser extends User {
  permissions: Permission[];
  department: string;
}
```

### Component Props

```typescript
// Basic props interface
interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  loading?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}

// Props with HTML attributes
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
}

// Props with ref forwarding
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'elevated' | 'outlined';
}

const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ variant = 'elevated', className, children, ...props }, ref) => (
    <div ref={ref} className={cn(variants[variant], className)} {...props}>
      {children}
    </div>
  )
);
```

### Generic Components

```typescript
// Generic list component
interface ListProps<T> {
  items: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  keyExtractor: (item: T) => string;
  emptyMessage?: string;
}

function List<T>({ items, renderItem, keyExtractor, emptyMessage }: ListProps<T>) {
  if (items.length === 0) {
    return <p className="text-gray-500">{emptyMessage || 'No items'}</p>;
  }

  return (
    <ul>
      {items.map((item, index) => (
        <li key={keyExtractor(item)}>{renderItem(item, index)}</li>
      ))}
    </ul>
  );
}

// Usage
<List<User>
  items={users}
  keyExtractor={(user) => user.id}
  renderItem={(user) => <UserCard user={user} />}
/>
```

### Function Types

```typescript
// Function type alias
type EventHandler<T = void> = (event: T) => void;
type AsyncEventHandler<T, R = void> = (event: T) => Promise<R>;

// Callback props
interface FormProps {
  onSubmit: (data: FormData) => Promise<void>;
  onCancel: () => void;
  onValidate?: (field: string, value: unknown) => string | null;
}

// Generic function type
type Comparator<T> = (a: T, b: T) => number;

const sortUsers: Comparator<User> = (a, b) => a.name.localeCompare(b.name);
```

## Utility Types

### Built-in Utilities

```typescript
// Partial - all properties optional
type UserUpdate = Partial<User>;

// Required - all properties required
type RequiredUser = Required<User>;

// Pick - select specific properties
type UserPreview = Pick<User, 'id' | 'name' | 'avatar'>;

// Omit - exclude specific properties
type UserInput = Omit<User, 'id' | 'createdAt'>;

// Record - create object type with specific keys
type UsersByRole = Record<UserRole, User[]>;

// Extract - extract from union
type AdminOrUser = Extract<UserRole, 'admin' | 'user'>;

// Exclude - exclude from union
type RegularUser = Exclude<UserRole, 'admin'>;

// NonNullable - remove null and undefined
type DefiniteUser = NonNullable<User | null | undefined>;

// ReturnType - get function return type
type ApiResponse = ReturnType<typeof fetchUser>;

// Parameters - get function parameters
type FetchParams = Parameters<typeof fetchUser>;
```

### Custom Utility Types

```typescript
// Make specific properties optional
type PartialBy<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

type CreateUserInput = PartialBy<User, 'id' | 'createdAt'>;

// Make specific properties required
type RequiredBy<T, K extends keyof T> = T & Required<Pick<T, K>>;

// Deep partial
type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};

// Nullable type
type Nullable<T> = T | null;

// Array element type
type ArrayElement<T> = T extends (infer U)[] ? U : never;

// Promise unwrap
type Awaited<T> = T extends Promise<infer U> ? U : T;
```

## API Response Types

```typescript
// Generic API response
interface ApiResponse<T> {
  data: T;
  status: number;
  message?: string;
}

// Paginated response
interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
}

// Error response
interface ApiError {
  code: string;
  message: string;
  details?: Record<string, string[]>;
}

// Result type (Either pattern)
type Result<T, E = Error> =
  | { success: true; data: T }
  | { success: false; error: E };

// Usage
async function fetchUser(id: string): Promise<Result<User, ApiError>> {
  try {
    const response = await fetch(`/api/users/${id}`);
    if (!response.ok) {
      const error = await response.json();
      return { success: false, error };
    }
    const data = await response.json();
    return { success: true, data };
  } catch (e) {
    return {
      success: false,
      error: { code: 'NETWORK_ERROR', message: 'Failed to fetch' }
    };
  }
}
```

## Event Handling Types

```typescript
// Form events
const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
  setValue(e.target.value);
};

const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
  e.preventDefault();
  // ...
};

// Mouse events
const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
  console.log(e.clientX, e.clientY);
};

// Keyboard events
const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
  if (e.key === 'Enter') {
    handleSubmit();
  }
};

// Custom event handlers
type ItemSelectHandler = (item: Item, index: number) => void;

interface SelectableListProps {
  items: Item[];
  onSelect: ItemSelectHandler;
  onMultiSelect?: (items: Item[]) => void;
}
```

## Type Guards

```typescript
// Type predicate
function isUser(obj: unknown): obj is User {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'id' in obj &&
    'name' in obj &&
    'email' in obj
  );
}

// Discriminated unions
interface SuccessState {
  status: 'success';
  data: User;
}

interface ErrorState {
  status: 'error';
  error: string;
}

interface LoadingState {
  status: 'loading';
}

type FetchState = SuccessState | ErrorState | LoadingState;

function renderState(state: FetchState) {
  switch (state.status) {
    case 'loading':
      return <Spinner />;
    case 'error':
      return <Error message={state.error} />;
    case 'success':
      return <UserCard user={state.data} />;
  }
}

// Assertion functions
function assertDefined<T>(value: T | null | undefined, message?: string): asserts value is T {
  if (value === null || value === undefined) {
    throw new Error(message || 'Value is not defined');
  }
}
```

## Module Patterns

```typescript
// Constants with as const
export const ROLES = ['admin', 'user', 'guest'] as const;
export type Role = typeof ROLES[number]; // 'admin' | 'user' | 'guest'

// Enum alternative with object
export const Status = {
  PENDING: 'pending',
  ACTIVE: 'active',
  INACTIVE: 'inactive',
} as const;

export type Status = typeof Status[keyof typeof Status];

// Namespace for related types
export namespace Api {
  export interface User {
    id: string;
    name: string;
  }

  export interface CreateUserRequest {
    name: string;
    email: string;
  }

  export interface CreateUserResponse {
    user: User;
    token: string;
  }
}
```

## Strict Null Checks Patterns

```typescript
// Optional chaining with nullish coalescing
const userName = user?.profile?.name ?? 'Anonymous';

// Non-null assertion (use sparingly)
const element = document.getElementById('root')!;

// Type narrowing
function processUser(user: User | null) {
  if (!user) {
    return <EmptyState />;
  }
  // user is now User (not null)
  return <UserProfile user={user} />;
}

// Default parameters
function greet(name: string = 'Guest') {
  return `Hello, ${name}`;
}
```
