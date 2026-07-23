# Tailwind CSS & Shadcn UI Reference

## Tailwind Fundamentals

### Spacing Scale

```
0    = 0px
0.5  = 2px
1    = 4px
1.5  = 6px
2    = 8px
2.5  = 10px
3    = 12px
3.5  = 14px
4    = 16px
5    = 20px
6    = 24px
7    = 28px
8    = 32px
9    = 36px
10   = 40px
11   = 44px
12   = 48px
14   = 56px
16   = 64px
20   = 80px
24   = 96px
```

### Common Layout Patterns

```tsx
// Flexbox centering
<div className="flex items-center justify-center">

// Space between items
<div className="flex items-center justify-between">

// Flex column with gap
<div className="flex flex-col gap-4">

// Grid with responsive columns
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">

// Container with max width
<div className="container mx-auto px-4 max-w-7xl">

// Sticky header
<header className="sticky top-0 z-50 bg-white/80 backdrop-blur-sm">

// Full-height layout
<div className="min-h-screen flex flex-col">
  <header />
  <main className="flex-1" />
  <footer />
</div>
```

### Typography

```tsx
// Headings
<h1 className="text-4xl font-bold tracking-tight">
<h2 className="text-3xl font-semibold">
<h3 className="text-2xl font-medium">
<h4 className="text-xl font-medium">

// Body text
<p className="text-base text-gray-700 leading-relaxed">
<p className="text-sm text-gray-600">
<p className="text-xs text-gray-500">

// Truncation
<p className="truncate">            // Single line
<p className="line-clamp-2">        // 2 lines
<p className="line-clamp-3">        // 3 lines
```

### Responsive Design

```tsx
// Mobile-first breakpoints
// sm:  640px
// md:  768px
// lg:  1024px
// xl:  1280px
// 2xl: 1536px

// Example: responsive grid
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">

// Example: hide/show
<div className="hidden md:block">  // Hidden on mobile
<div className="md:hidden">        // Visible only on mobile

// Example: responsive text
<h1 className="text-2xl md:text-4xl lg:text-5xl">
```

### Colors and States

```tsx
// Interactive states
<button className="
  bg-blue-600
  hover:bg-blue-700
  active:bg-blue-800
  focus:ring-2
  focus:ring-blue-500
  focus:ring-offset-2
  disabled:opacity-50
  disabled:cursor-not-allowed
">

// Dark mode
<div className="bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">

// Transition
<button className="transition-colors duration-200">
<div className="transition-all duration-300 ease-in-out">
```

### Shadows and Borders

```tsx
// Shadows
<div className="shadow-sm">      // Subtle
<div className="shadow">         // Default
<div className="shadow-md">      // Medium
<div className="shadow-lg">      // Large
<div className="shadow-xl">      // Extra large

// Borders
<div className="border border-gray-200 rounded-lg">
<div className="border-l-4 border-blue-500">
<div className="divide-y divide-gray-200">  // Dividers between children
```

## Shadcn UI Components

### Installation Pattern

```bash
npx shadcn@latest add button
npx shadcn@latest add card
npx shadcn@latest add input
```

### Button Variants

```tsx
import { Button } from '@/components/ui/button';

// Variants
<Button variant="default">Primary</Button>
<Button variant="secondary">Secondary</Button>
<Button variant="outline">Outline</Button>
<Button variant="ghost">Ghost</Button>
<Button variant="link">Link</Button>
<Button variant="destructive">Destructive</Button>

// Sizes
<Button size="sm">Small</Button>
<Button size="default">Default</Button>
<Button size="lg">Large</Button>
<Button size="icon"><Icon /></Button>

// With icon
<Button>
  <Plus className="mr-2 h-4 w-4" />
  Add Item
</Button>

// Loading state
<Button disabled>
  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
  Loading
</Button>
```

### Card Component

```tsx
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

<Card>
  <CardHeader>
    <CardTitle>Card Title</CardTitle>
    <CardDescription>Card description goes here</CardDescription>
  </CardHeader>
  <CardContent>
    <p>Card content</p>
  </CardContent>
  <CardFooter className="flex justify-between">
    <Button variant="outline">Cancel</Button>
    <Button>Save</Button>
  </CardFooter>
</Card>
```

### Form Components

```tsx
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

// Input with label
<div className="grid gap-2">
  <Label htmlFor="email">Email</Label>
  <Input id="email" type="email" placeholder="Enter email" />
</div>

// Select
<Select>
  <SelectTrigger className="w-[180px]">
    <SelectValue placeholder="Select option" />
  </SelectTrigger>
  <SelectContent>
    <SelectItem value="option1">Option 1</SelectItem>
    <SelectItem value="option2">Option 2</SelectItem>
  </SelectContent>
</Select>

// Textarea
<Textarea placeholder="Enter description" rows={4} />
```

### Dialog/Modal

```tsx
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

<Dialog>
  <DialogTrigger asChild>
    <Button>Open Dialog</Button>
  </DialogTrigger>
  <DialogContent className="sm:max-w-[425px]">
    <DialogHeader>
      <DialogTitle>Edit Profile</DialogTitle>
      <DialogDescription>
        Make changes to your profile here.
      </DialogDescription>
    </DialogHeader>
    <div className="grid gap-4 py-4">
      {/* Form fields */}
    </div>
    <DialogFooter>
      <Button type="submit">Save changes</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

### Table

```tsx
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

<Table>
  <TableHeader>
    <TableRow>
      <TableHead className="w-[100px]">ID</TableHead>
      <TableHead>Name</TableHead>
      <TableHead className="text-right">Amount</TableHead>
    </TableRow>
  </TableHeader>
  <TableBody>
    {items.map((item) => (
      <TableRow key={item.id}>
        <TableCell className="font-medium">{item.id}</TableCell>
        <TableCell>{item.name}</TableCell>
        <TableCell className="text-right">{item.amount}</TableCell>
      </TableRow>
    ))}
  </TableBody>
</Table>
```

### Toast Notifications

```tsx
import { useToast } from '@/hooks/use-toast';
import { Toaster } from '@/components/ui/toaster';

// In layout
<Toaster />

// In component
function MyComponent() {
  const { toast } = useToast();

  const handleClick = () => {
    toast({
      title: 'Success',
      description: 'Your changes have been saved.',
    });

    // Error toast
    toast({
      variant: 'destructive',
      title: 'Error',
      description: 'Something went wrong.',
    });
  };
}
```

## CN Utility

```tsx
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Usage
<div className={cn(
  'base-classes',
  condition && 'conditional-classes',
  variant === 'primary' && 'variant-classes',
  className // Allow overrides from props
)}>
```

## Common Component Patterns

### Loading States

```tsx
// Skeleton loader
<div className="animate-pulse space-y-4">
  <div className="h-4 bg-gray-200 rounded w-3/4" />
  <div className="h-4 bg-gray-200 rounded w-1/2" />
</div>

// Spinner
<Loader2 className="h-6 w-6 animate-spin text-gray-500" />
```

### Empty States

```tsx
<div className="flex flex-col items-center justify-center py-12 text-center">
  <Inbox className="h-12 w-12 text-gray-400 mb-4" />
  <h3 className="text-lg font-medium text-gray-900">No items</h3>
  <p className="text-sm text-gray-500 mt-1">
    Get started by creating your first item.
  </p>
  <Button className="mt-4">
    <Plus className="mr-2 h-4 w-4" />
    Create Item
  </Button>
</div>
```

### Badge Component

```tsx
const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground',
        success: 'bg-green-100 text-green-800',
        warning: 'bg-yellow-100 text-yellow-800',
        error: 'bg-red-100 text-red-800',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

<Badge variant="success">Active</Badge>
```
