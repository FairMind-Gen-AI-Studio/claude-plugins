# Async Patterns Reference

## Basic Async/Await

```python
import asyncio
from typing import List

# Basic async function
async def fetch_user(user_id: str) -> dict:
    # Simulate async I/O
    await asyncio.sleep(0.1)
    return {"id": user_id, "name": f"User {user_id}"}

# Calling async functions
async def main():
    user = await fetch_user("123")
    print(user)

# Running async code
asyncio.run(main())
```

## Concurrent Execution

### gather - Run Multiple Tasks

```python
async def fetch_all_users(user_ids: List[str]) -> List[dict]:
    """Fetch multiple users concurrently."""
    tasks = [fetch_user(uid) for uid in user_ids]
    users = await asyncio.gather(*tasks)
    return users

# With error handling
async def fetch_all_users_safe(user_ids: List[str]) -> List[dict | Exception]:
    """Fetch users, returning exceptions instead of raising."""
    tasks = [fetch_user(uid) for uid in user_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

### TaskGroup - Structured Concurrency (Python 3.11+)

```python
async def process_items(items: List[str]) -> List[dict]:
    """Process items with proper cancellation handling."""
    results = []

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(process_item(item)) for item in items]

    # All tasks completed successfully
    results = [task.result() for task in tasks]
    return results
```

### as_completed - Process Results as They Arrive

```python
async def fetch_with_progress(user_ids: List[str]) -> List[dict]:
    """Fetch users and process results as they complete."""
    tasks = [asyncio.create_task(fetch_user(uid)) for uid in user_ids]
    results = []

    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        print(f"Completed: {result['id']}")

    return results
```

## Timeouts

```python
async def fetch_with_timeout(user_id: str, timeout: float = 5.0) -> dict:
    """Fetch user with timeout."""
    try:
        async with asyncio.timeout(timeout):
            return await fetch_user(user_id)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out")

# Legacy approach (pre-3.11)
async def fetch_with_timeout_legacy(user_id: str, timeout: float = 5.0) -> dict:
    try:
        return await asyncio.wait_for(fetch_user(user_id), timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out")
```

## Semaphores - Rate Limiting

```python
class RateLimitedClient:
    def __init__(self, max_concurrent: int = 10):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch(self, url: str) -> dict:
        async with self.semaphore:
            # Only max_concurrent requests at a time
            return await self._do_fetch(url)

    async def _do_fetch(self, url: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.json()

# Usage
client = RateLimitedClient(max_concurrent=5)
results = await asyncio.gather(*[
    client.fetch(url) for url in urls
])
```

## Locks

```python
class Counter:
    def __init__(self):
        self.value = 0
        self.lock = asyncio.Lock()

    async def increment(self):
        async with self.lock:
            current = self.value
            await asyncio.sleep(0.001)  # Simulate work
            self.value = current + 1
            return self.value

# RWLock pattern
class RWLock:
    def __init__(self):
        self._read_count = 0
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()

    async def acquire_read(self):
        async with self._read_lock:
            self._read_count += 1
            if self._read_count == 1:
                await self._write_lock.acquire()

    async def release_read(self):
        async with self._read_lock:
            self._read_count -= 1
            if self._read_count == 0:
                self._write_lock.release()
```

## Queues

```python
async def producer(queue: asyncio.Queue, items: List[str]):
    """Produce items to queue."""
    for item in items:
        await queue.put(item)
        print(f"Produced: {item}")

    # Signal completion
    await queue.put(None)

async def consumer(queue: asyncio.Queue, consumer_id: int):
    """Consume items from queue."""
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break

        await process_item(item)
        print(f"Consumer {consumer_id} processed: {item}")
        queue.task_done()

async def main():
    queue = asyncio.Queue(maxsize=10)
    items = [f"item_{i}" for i in range(20)]

    # Start producer and consumers
    producer_task = asyncio.create_task(producer(queue, items))
    consumer_tasks = [
        asyncio.create_task(consumer(queue, i))
        for i in range(3)
    ]

    # Wait for producer to finish
    await producer_task

    # Signal consumers to stop
    for _ in consumer_tasks:
        await queue.put(None)

    # Wait for consumers
    await asyncio.gather(*consumer_tasks)
```

## Context Variables

```python
from contextvars import ContextVar
from uuid import uuid4

# Request context
request_id: ContextVar[str] = ContextVar("request_id", default="")
current_user: ContextVar[dict | None] = ContextVar("current_user", default=None)

async def set_request_context():
    """Middleware to set request context."""
    request_id.set(str(uuid4()))

async def get_request_id() -> str:
    return request_id.get()

# Usage in logging
async def log_action(action: str):
    rid = request_id.get()
    user = current_user.get()
    print(f"[{rid}] User {user}: {action}")
```

## Async Generators

```python
from typing import AsyncGenerator

async def paginate_users(
    per_page: int = 10
) -> AsyncGenerator[List[dict], None]:
    """Yield pages of users."""
    page = 1
    while True:
        users = await fetch_users_page(page, per_page)
        if not users:
            break
        yield users
        page += 1

async def process_all_users():
    async for users_page in paginate_users(per_page=100):
        for user in users_page:
            await process_user(user)

# Async iterator class
class AsyncUserIterator:
    def __init__(self, user_ids: List[str]):
        self.user_ids = user_ids
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.user_ids):
            raise StopAsyncIteration

        user = await fetch_user(self.user_ids[self.index])
        self.index += 1
        return user
```

## HTTP Client (aiohttp)

```python
import aiohttp
from typing import Any

class AsyncHTTPClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get(self, url: str) -> dict:
        session = await self._get_session()
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()

    async def post(self, url: str, data: dict) -> dict:
        session = await self._get_session()
        async with session.post(url, json=data) as response:
            response.raise_for_status()
            return await response.json()

    async def close(self):
        if self._session:
            await self._session.close()

# Context manager usage
async with aiohttp.ClientSession() as session:
    async with session.get("https://api.example.com/data") as response:
        data = await response.json()
```

## Database Async

```python
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)

# Engine setup
engine = create_async_engine(
    "postgresql+asyncpg://user:pass@localhost/db",
    echo=True,
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Usage
async def get_user(user_id: str) -> User | None:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

async def create_user(data: UserCreate) -> User:
    async with async_session() as session:
        user = User(**data.model_dump())
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user
```

## Error Handling

```python
async def resilient_fetch(url: str, retries: int = 3) -> dict:
    """Fetch with exponential backoff retry."""
    last_error = None

    for attempt in range(retries):
        try:
            return await fetch(url)
        except aiohttp.ClientError as e:
            last_error = e
            if attempt < retries - 1:
                delay = 2 ** attempt  # Exponential backoff
                await asyncio.sleep(delay)

    raise last_error

# Circuit breaker pattern
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60):
        self.failures = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.state = "closed"

    async def call(self, coro):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker is open")

        try:
            result = await coro
            if self.state == "half-open":
                self.state = "closed"
                self.failures = 0
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold:
                self.state = "open"
            raise
```
