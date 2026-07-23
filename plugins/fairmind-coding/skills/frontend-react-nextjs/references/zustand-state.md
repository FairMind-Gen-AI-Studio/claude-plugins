# Zustand State Management Reference

## Basic Store

```typescript
import { create } from 'zustand';

interface CounterState {
  count: number;
  increment: () => void;
  decrement: () => void;
  reset: () => void;
}

const useCounterStore = create<CounterState>((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
  decrement: () => set((state) => ({ count: state.count - 1 })),
  reset: () => set({ count: 0 }),
}));

// Usage in component
function Counter() {
  const { count, increment, decrement } = useCounterStore();

  return (
    <div>
      <span>{count}</span>
      <button onClick={increment}>+</button>
      <button onClick={decrement}>-</button>
    </div>
  );
}
```

## Store with Async Actions

```typescript
interface UserState {
  user: User | null;
  isLoading: boolean;
  error: string | null;
  fetchUser: (id: string) => Promise<void>;
  updateUser: (data: Partial<User>) => Promise<void>;
  clearUser: () => void;
}

const useUserStore = create<UserState>((set, get) => ({
  user: null,
  isLoading: false,
  error: null,

  fetchUser: async (id: string) => {
    set({ isLoading: true, error: null });
    try {
      const response = await fetch(`/api/users/${id}`);
      if (!response.ok) throw new Error('Failed to fetch user');
      const user = await response.json();
      set({ user, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Unknown error',
        isLoading: false,
      });
    }
  },

  updateUser: async (data: Partial<User>) => {
    const currentUser = get().user;
    if (!currentUser) return;

    set({ isLoading: true });
    try {
      const response = await fetch(`/api/users/${currentUser.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const updatedUser = await response.json();
      set({ user: updatedUser, isLoading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Unknown error',
        isLoading: false,
      });
    }
  },

  clearUser: () => set({ user: null, error: null }),
}));
```

## Selectors for Performance

```typescript
// Bad: Re-renders on any store change
function UserName() {
  const { user } = useUserStore();
  return <span>{user?.name}</span>;
}

// Good: Only re-renders when user.name changes
function UserName() {
  const name = useUserStore((state) => state.user?.name);
  return <span>{name}</span>;
}

// Multiple selectors
function UserInfo() {
  const name = useUserStore((state) => state.user?.name);
  const email = useUserStore((state) => state.user?.email);

  return (
    <div>
      <span>{name}</span>
      <span>{email}</span>
    </div>
  );
}

// Shallow equality for objects
import { shallow } from 'zustand/shallow';

function UserDetails() {
  const { name, email } = useUserStore(
    (state) => ({ name: state.user?.name, email: state.user?.email }),
    shallow
  );

  return <div>{name} - {email}</div>;
}
```

## Slices Pattern (Large Stores)

```typescript
// types.ts
interface UserSlice {
  user: User | null;
  setUser: (user: User) => void;
  clearUser: () => void;
}

interface CartSlice {
  items: CartItem[];
  addItem: (item: CartItem) => void;
  removeItem: (id: string) => void;
  clearCart: () => void;
}

interface UISlice {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  modalOpen: string | null;
  openModal: (id: string) => void;
  closeModal: () => void;
}

type StoreState = UserSlice & CartSlice & UISlice;

// slices/userSlice.ts
const createUserSlice: StateCreator<StoreState, [], [], UserSlice> = (set) => ({
  user: null,
  setUser: (user) => set({ user }),
  clearUser: () => set({ user: null }),
});

// slices/cartSlice.ts
const createCartSlice: StateCreator<StoreState, [], [], CartSlice> = (set) => ({
  items: [],
  addItem: (item) => set((state) => ({ items: [...state.items, item] })),
  removeItem: (id) => set((state) => ({
    items: state.items.filter((item) => item.id !== id),
  })),
  clearCart: () => set({ items: [] }),
});

// slices/uiSlice.ts
const createUISlice: StateCreator<StoreState, [], [], UISlice> = (set) => ({
  sidebarOpen: false,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  modalOpen: null,
  openModal: (id) => set({ modalOpen: id }),
  closeModal: () => set({ modalOpen: null }),
});

// store.ts
const useStore = create<StoreState>()((...args) => ({
  ...createUserSlice(...args),
  ...createCartSlice(...args),
  ...createUISlice(...args),
}));
```

## Middleware

### Persist Middleware

```typescript
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface SettingsState {
  theme: 'light' | 'dark';
  language: string;
  setTheme: (theme: 'light' | 'dark') => void;
  setLanguage: (lang: string) => void;
}

const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'light',
      language: 'en',
      setTheme: (theme) => set({ theme }),
      setLanguage: (language) => set({ language }),
    }),
    {
      name: 'settings-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        theme: state.theme,
        language: state.language,
      }),
    }
  )
);
```

### DevTools Middleware

```typescript
import { devtools } from 'zustand/middleware';

const useStore = create<StoreState>()(
  devtools(
    (set) => ({
      // state and actions
    }),
    { name: 'MyStore' }
  )
);
```

### Immer Middleware (for complex updates)

```typescript
import { immer } from 'zustand/middleware/immer';

interface TodoState {
  todos: Todo[];
  addTodo: (text: string) => void;
  toggleTodo: (id: string) => void;
  updateTodo: (id: string, text: string) => void;
}

const useTodoStore = create<TodoState>()(
  immer((set) => ({
    todos: [],

    addTodo: (text) =>
      set((state) => {
        state.todos.push({
          id: crypto.randomUUID(),
          text,
          completed: false,
        });
      }),

    toggleTodo: (id) =>
      set((state) => {
        const todo = state.todos.find((t) => t.id === id);
        if (todo) {
          todo.completed = !todo.completed;
        }
      }),

    updateTodo: (id, text) =>
      set((state) => {
        const todo = state.todos.find((t) => t.id === id);
        if (todo) {
          todo.text = text;
        }
      }),
  }))
);
```

## Combining Middleware

```typescript
const useStore = create<StoreState>()(
  devtools(
    persist(
      immer((set) => ({
        // state and actions
      })),
      { name: 'store' }
    ),
    { name: 'MyApp' }
  )
);
```

## Subscriptions

```typescript
// Subscribe to store changes
const unsub = useStore.subscribe(
  (state) => console.log('State changed:', state)
);

// Subscribe with selector
const unsub = useStore.subscribe(
  (state) => state.user,
  (user, previousUser) => {
    console.log('User changed from', previousUser, 'to', user);
  }
);

// Cleanup
useEffect(() => {
  const unsub = useStore.subscribe(/* ... */);
  return () => unsub();
}, []);
```

## Computed Values (Derived State)

```typescript
interface CartState {
  items: CartItem[];
  // Computed getters
  get totalItems(): number;
  get totalPrice(): number;
  get isEmpty(): boolean;
}

const useCartStore = create<CartState>((set, get) => ({
  items: [],

  get totalItems() {
    return get().items.reduce((sum, item) => sum + item.quantity, 0);
  },

  get totalPrice() {
    return get().items.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0
    );
  },

  get isEmpty() {
    return get().items.length === 0;
  },
}));

// Alternative: Use selectors with useMemo
function CartSummary() {
  const items = useCartStore((state) => state.items);

  const { totalItems, totalPrice } = useMemo(() => ({
    totalItems: items.reduce((sum, item) => sum + item.quantity, 0),
    totalPrice: items.reduce((sum, item) => sum + item.price * item.quantity, 0),
  }), [items]);

  return (
    <div>
      <span>Items: {totalItems}</span>
      <span>Total: ${totalPrice}</span>
    </div>
  );
}
```

## Testing

```typescript
import { act, renderHook } from '@testing-library/react';

describe('useCounterStore', () => {
  beforeEach(() => {
    // Reset store before each test
    useCounterStore.setState({ count: 0 });
  });

  it('increments count', () => {
    const { result } = renderHook(() => useCounterStore());

    act(() => {
      result.current.increment();
    });

    expect(result.current.count).toBe(1);
  });

  it('can set initial state for test', () => {
    useCounterStore.setState({ count: 10 });

    const { result } = renderHook(() => useCounterStore());
    expect(result.current.count).toBe(10);
  });
});
```
