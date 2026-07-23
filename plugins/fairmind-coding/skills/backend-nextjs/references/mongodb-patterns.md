# MongoDB Patterns Reference

## Connection Setup

### Singleton Connection

```typescript
// lib/mongodb.ts
import mongoose from 'mongoose';

const MONGODB_URI = process.env.MONGODB_URI!;

if (!MONGODB_URI) {
  throw new Error('Please define MONGODB_URI environment variable');
}

interface MongooseCache {
  conn: typeof mongoose | null;
  promise: Promise<typeof mongoose> | null;
}

declare global {
  var mongoose: MongooseCache | undefined;
}

let cached: MongooseCache = global.mongoose || { conn: null, promise: null };

if (!global.mongoose) {
  global.mongoose = cached;
}

export async function connectDB() {
  if (cached.conn) {
    return cached.conn;
  }

  if (!cached.promise) {
    const opts = {
      bufferCommands: false,
    };

    cached.promise = mongoose.connect(MONGODB_URI, opts);
  }

  try {
    cached.conn = await cached.promise;
  } catch (e) {
    cached.promise = null;
    throw e;
  }

  return cached.conn;
}
```

## Schema Design

### Basic Schema with Timestamps

```typescript
// models/User.ts
import mongoose, { Schema, Document, Model } from 'mongoose';

export interface IUser extends Document {
  name: string;
  email: string;
  password: string;
  role: 'user' | 'admin';
  profile?: {
    avatar?: string;
    bio?: string;
  };
  createdAt: Date;
  updatedAt: Date;
}

const userSchema = new Schema<IUser>(
  {
    name: {
      type: String,
      required: [true, 'Name is required'],
      trim: true,
      maxlength: [100, 'Name cannot exceed 100 characters'],
    },
    email: {
      type: String,
      required: [true, 'Email is required'],
      unique: true,
      lowercase: true,
      trim: true,
      match: [/^\S+@\S+\.\S+$/, 'Invalid email format'],
    },
    password: {
      type: String,
      required: [true, 'Password is required'],
      minlength: [8, 'Password must be at least 8 characters'],
      select: false, // Don't include in queries by default
    },
    role: {
      type: String,
      enum: ['user', 'admin'],
      default: 'user',
    },
    profile: {
      avatar: String,
      bio: {
        type: String,
        maxlength: 500,
      },
    },
  },
  {
    timestamps: true,
    toJSON: {
      transform: (doc, ret) => {
        ret.id = ret._id;
        delete ret._id;
        delete ret.__v;
        delete ret.password;
        return ret;
      },
    },
  }
);

// Indexes
userSchema.index({ email: 1 });
userSchema.index({ role: 1 });
userSchema.index({ createdAt: -1 });

// Instance methods
userSchema.methods.comparePassword = async function (candidatePassword: string) {
  const bcrypt = await import('bcryptjs');
  return bcrypt.compare(candidatePassword, this.password);
};

// Static methods
userSchema.statics.findByEmail = function (email: string) {
  return this.findOne({ email: email.toLowerCase() });
};

// Pre-save middleware
userSchema.pre('save', async function (next) {
  if (!this.isModified('password')) return next();

  const bcrypt = await import('bcryptjs');
  this.password = await bcrypt.hash(this.password, 12);
  next();
});

export const User: Model<IUser> =
  mongoose.models.User || mongoose.model<IUser>('User', userSchema);
```

### Relationships

```typescript
// One-to-Many with References
const postSchema = new Schema({
  title: String,
  content: String,
  author: {
    type: Schema.Types.ObjectId,
    ref: 'User',
    required: true,
  },
  comments: [{
    type: Schema.Types.ObjectId,
    ref: 'Comment',
  }],
});

// Many-to-Many
const courseSchema = new Schema({
  title: String,
  students: [{
    type: Schema.Types.ObjectId,
    ref: 'User',
  }],
  instructors: [{
    type: Schema.Types.ObjectId,
    ref: 'User',
  }],
});

// Embedded Documents
const orderSchema = new Schema({
  customer: {
    type: Schema.Types.ObjectId,
    ref: 'User',
  },
  items: [{
    product: {
      type: Schema.Types.ObjectId,
      ref: 'Product',
    },
    quantity: Number,
    price: Number,
  }],
  shippingAddress: {
    street: String,
    city: String,
    country: String,
    postalCode: String,
  },
});
```

## Query Patterns

### Basic CRUD Operations

```typescript
// Create
const user = await User.create({
  name: 'John Doe',
  email: 'john@example.com',
  password: 'securepassword',
});

// Read - Find one
const user = await User.findById(id);
const user = await User.findOne({ email: 'john@example.com' });

// Read - Find many
const users = await User.find({ role: 'user' });

// Update
const user = await User.findByIdAndUpdate(
  id,
  { name: 'Jane Doe' },
  { new: true, runValidators: true }
);

// Delete
await User.findByIdAndDelete(id);
```

### Advanced Queries

```typescript
// Pagination
async function getPaginatedUsers(page: number, limit: number) {
  const skip = (page - 1) * limit;

  const [users, total] = await Promise.all([
    User.find()
      .sort({ createdAt: -1 })
      .skip(skip)
      .limit(limit)
      .lean(),
    User.countDocuments(),
  ]);

  return {
    data: users,
    meta: {
      page,
      limit,
      total,
      totalPages: Math.ceil(total / limit),
    },
  };
}

// Search with text index
userSchema.index({ name: 'text', email: 'text' });

const results = await User.find(
  { $text: { $search: searchTerm } },
  { score: { $meta: 'textScore' } }
).sort({ score: { $meta: 'textScore' } });

// Filtering with multiple conditions
const users = await User.find({
  role: 'user',
  createdAt: { $gte: startDate, $lte: endDate },
  'profile.bio': { $exists: true },
});

// Population (joins)
const posts = await Post.find()
  .populate('author', 'name email')
  .populate({
    path: 'comments',
    populate: { path: 'author', select: 'name' },
  });
```

### Aggregation Pipeline

```typescript
// User statistics by role
const stats = await User.aggregate([
  {
    $group: {
      _id: '$role',
      count: { $sum: 1 },
      avgCreatedAt: { $avg: { $toLong: '$createdAt' } },
    },
  },
  {
    $project: {
      role: '$_id',
      count: 1,
      _id: 0,
    },
  },
]);

// Monthly registration stats
const monthlyStats = await User.aggregate([
  {
    $match: {
      createdAt: { $gte: new Date('2024-01-01') },
    },
  },
  {
    $group: {
      _id: {
        year: { $year: '$createdAt' },
        month: { $month: '$createdAt' },
      },
      count: { $sum: 1 },
    },
  },
  {
    $sort: { '_id.year': 1, '_id.month': 1 },
  },
]);

// Complex aggregation with lookup
const ordersWithDetails = await Order.aggregate([
  { $match: { status: 'completed' } },
  {
    $lookup: {
      from: 'users',
      localField: 'customer',
      foreignField: '_id',
      as: 'customerDetails',
    },
  },
  { $unwind: '$customerDetails' },
  {
    $project: {
      orderNumber: 1,
      total: 1,
      'customerDetails.name': 1,
      'customerDetails.email': 1,
    },
  },
]);
```

## Transactions

```typescript
async function transferFunds(fromId: string, toId: string, amount: number) {
  const session = await mongoose.startSession();

  try {
    session.startTransaction();

    const fromAccount = await Account.findByIdAndUpdate(
      fromId,
      { $inc: { balance: -amount } },
      { session, new: true }
    );

    if (!fromAccount || fromAccount.balance < 0) {
      throw new Error('Insufficient funds');
    }

    await Account.findByIdAndUpdate(
      toId,
      { $inc: { balance: amount } },
      { session }
    );

    await session.commitTransaction();
    return { success: true };
  } catch (error) {
    await session.abortTransaction();
    throw error;
  } finally {
    session.endSession();
  }
}
```

## Indexes

```typescript
// Single field index
userSchema.index({ email: 1 });

// Compound index
userSchema.index({ role: 1, createdAt: -1 });

// Unique index
userSchema.index({ email: 1 }, { unique: true });

// Text index for search
userSchema.index({ name: 'text', bio: 'text' });

// TTL index (auto-delete after time)
sessionSchema.index({ createdAt: 1 }, { expireAfterSeconds: 3600 });

// Partial index
userSchema.index(
  { email: 1 },
  { partialFilterExpression: { role: 'admin' } }
);

// Check indexes
const indexes = await User.collection.indexes();
```

## Error Handling

```typescript
import mongoose from 'mongoose';

function handleMongoError(error: unknown): { message: string; status: number } {
  // Duplicate key error
  if (error instanceof mongoose.mongo.MongoServerError && error.code === 11000) {
    const field = Object.keys(error.keyPattern)[0];
    return {
      message: `${field} already exists`,
      status: 409,
    };
  }

  // Validation error
  if (error instanceof mongoose.Error.ValidationError) {
    const messages = Object.values(error.errors).map((e) => e.message);
    return {
      message: messages.join(', '),
      status: 400,
    };
  }

  // Cast error (invalid ObjectId)
  if (error instanceof mongoose.Error.CastError) {
    return {
      message: `Invalid ${error.path}`,
      status: 400,
    };
  }

  return {
    message: 'Internal server error',
    status: 500,
  };
}
```
