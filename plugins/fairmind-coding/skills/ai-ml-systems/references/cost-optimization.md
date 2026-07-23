# Cost Optimization Reference

## Cost Model

### Model IDs and pricing live in ONE constant

Model ids and prices rot: generations ship, prices move, old ids stop being
served. Scattering ids across a router, a tracker, and a fallback ladder means a
new generation is a hunt through the codebase — and the site you miss is the one
that keeps billing at the old tier or starts erroring in production. Declare them
once and import that constant everywhere.

```python
# Snapshot, not a contract. Re-check the provider's pricing page before you bill
# anything against these numbers, and read per-model limits from the Models API
# (`client.models.retrieve(id)` returns max_input_tokens / max_tokens) rather
# than hardcoding a context window.
MODELS = {
    "high": "claude-opus-4-8",     # 1M context — hardest reasoning, agentic runs
    "medium": "claude-sonnet-5",   # 1M context — coding, structured output
    "low": "claude-haiku-4-5",     # 200K context — classification, extraction
}

# USD per 1M tokens.
PRICING = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
```

Output tokens cost 5x input on every current Claude tier, so a token trimmed from
the response is worth five trimmed from the prompt — cap output length before you
start compressing prompts.

### Cost Tracking

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TokenUsage:
    model: str
    input_tokens: int
    output_tokens: int
    timestamp: datetime
    request_id: str
    task_type: str

class CostTracker:
    """Track and analyze LLM costs."""

    def __init__(self, pricing: dict[str, dict[str, float]] = PRICING):
        self.pricing = pricing
        self.usage: list[TokenUsage] = []

    def record(self, usage: TokenUsage):
        self.usage.append(usage)

    def calculate_cost(self, usage: TokenUsage) -> float:
        """Calculate cost for a single request."""
        pricing = self.pricing.get(usage.model)
        if pricing is None:
            # A model missing from PRICING must not silently bill as $0 — that
            # hides an entire model's spend from every dashboard and alert
            # downstream. An unknown id is a config bug; surface it here.
            raise KeyError(f"no pricing entry for model {usage.model!r}")

        input_cost = (usage.input_tokens / 1_000_000) * pricing["input"]
        output_cost = (usage.output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def get_daily_cost(self, date: datetime) -> dict:
        """Get cost breakdown for a day."""
        day_usage = [
            u for u in self.usage
            if u.timestamp.date() == date.date()
        ]

        by_model = {}
        for usage in day_usage:
            if usage.model not in by_model:
                by_model[usage.model] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0
                }

            by_model[usage.model]["requests"] += 1
            by_model[usage.model]["input_tokens"] += usage.input_tokens
            by_model[usage.model]["output_tokens"] += usage.output_tokens
            by_model[usage.model]["cost"] += self.calculate_cost(usage)

        return {
            "date": date.isoformat(),
            "total_cost": sum(m["cost"] for m in by_model.values()),
            "by_model": by_model
        }
```

## Caching Strategies

### Semantic Caching

```python
import hashlib
from typing import Any

class SemanticCache:
    """Cache LLM responses based on semantic similarity."""

    def __init__(
        self,
        vector_store: VectorStore,
        similarity_threshold: float = 0.95
    ):
        self.vector_store = vector_store
        self.similarity_threshold = similarity_threshold
        self.exact_cache: dict[str, Any] = {}

    async def get(self, query: str) -> Any | None:
        # 1. Check exact match first
        cache_key = self._hash(query)
        if cache_key in self.exact_cache:
            return self.exact_cache[cache_key]

        # 2. Check semantic similarity
        embedding = await self._embed(query)
        results = await self.vector_store.search(
            embedding=embedding,
            k=1,
            threshold=self.similarity_threshold
        )

        if results:
            return results[0]["response"]

        return None

    async def set(self, query: str, response: Any):
        # Store exact match
        cache_key = self._hash(query)
        self.exact_cache[cache_key] = response

        # Store for semantic search
        embedding = await self._embed(query)
        await self.vector_store.insert({
            "query": query,
            "response": response,
            "embedding": embedding,
            "timestamp": datetime.now()
        })

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

# Usage
cache = SemanticCache(vector_store, similarity_threshold=0.95)

async def cached_llm_call(prompt: str) -> str:
    # Check cache
    cached = await cache.get(prompt)
    if cached:
        return cached

    # Call LLM
    response = await llm.generate(prompt)

    # Cache response
    await cache.set(prompt, response)

    return response
```

### Response Caching with TTL

```python
from datetime import timedelta
import asyncio

class TTLCache:
    """Cache with time-to-live expiration."""

    def __init__(self, default_ttl: timedelta = timedelta(hours=1)):
        self.cache: dict[str, tuple[Any, datetime]] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        if key not in self.cache:
            return None

        value, expiry = self.cache[key]
        if datetime.now() > expiry:
            del self.cache[key]
            return None

        return value

    def set(self, key: str, value: Any, ttl: timedelta | None = None):
        ttl = ttl or self.default_ttl
        expiry = datetime.now() + ttl
        self.cache[key] = (value, expiry)

    def invalidate(self, pattern: str):
        """Invalidate keys matching pattern."""
        keys_to_delete = [
            k for k in self.cache
            if pattern in k
        ]
        for key in keys_to_delete:
            del self.cache[key]

# Cache configuration by task type
CACHE_CONFIG = {
    "factual_qa": timedelta(days=7),      # Stable facts
    "summarization": timedelta(hours=24),  # Document-dependent
    "code_generation": timedelta(hours=1), # Context-sensitive
    "creative": timedelta(minutes=0),      # Never cache
}
```

## Model Routing

### Intelligent Router

```python
class ModelRouter:
    """Route requests to optimal model based on requirements."""

    def __init__(self, models: dict[str, str] = MODELS):
        # Injected, never re-declared: the router and the cost tracker must agree
        # on which ids exist, or routed traffic lands on a model nobody prices.
        self.models = models

    def route(self, request: dict) -> str:
        """Select model based on request characteristics."""

        # Check explicit model requirement
        if request.get("required_model"):
            return request["required_model"]

        # Route based on task complexity
        complexity = self._assess_complexity(request)

        # Consider budget constraints
        if request.get("budget_constrained"):
            complexity = min(complexity, "medium")

        # Consider latency requirements
        if request.get("low_latency"):
            return self.models["low"]

        return self.models[complexity]

    def _assess_complexity(self, request: dict) -> str:
        """Assess task complexity."""

        task_type = request.get("task_type", "general")
        input_length = len(request.get("input", ""))

        # High complexity indicators
        high_complexity_tasks = [
            "complex_reasoning",
            "code_review",
            "architecture_design",
            "multi_step_analysis"
        ]

        if task_type in high_complexity_tasks:
            return "high"

        # Medium complexity
        medium_complexity_tasks = [
            "code_generation",
            "summarization",
            "translation",
            "analysis"
        ]

        if task_type in medium_complexity_tasks:
            return "medium"

        # Low complexity
        return "low"

# Cascading router - try cheaper model first
class CascadingRouter:
    """Try cheaper models first, escalate if needed."""

    async def execute(self, request: dict) -> str:
        # Cheapest tier first, escalating — order matters, the ids do not live here.
        models = [MODELS["low"], MODELS["medium"], MODELS["high"]]

        for model in models:
            response = await self._try_model(model, request)

            # Check if response meets quality threshold
            if self._is_acceptable(response, request):
                return response

        # Return best effort from most capable model
        return response

    def _is_acceptable(self, response: str, request: dict) -> bool:
        """Check if response meets requirements."""
        # Implement quality checks
        min_length = request.get("min_length", 0)
        required_elements = request.get("required_elements", [])

        if len(response) < min_length:
            return False

        for element in required_elements:
            if element.lower() not in response.lower():
                return False

        return True
```

## Batching

### Request Batching

```python
import asyncio
from collections import defaultdict

class BatchProcessor:
    """Batch similar requests for efficiency."""

    def __init__(
        self,
        batch_size: int = 10,
        batch_timeout: float = 0.5
    ):
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.pending: dict[str, list] = defaultdict(list)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def process(self, request: dict) -> str:
        """Add request to batch and wait for result."""

        batch_key = self._get_batch_key(request)
        future = asyncio.Future()

        async with self.locks[batch_key]:
            self.pending[batch_key].append((request, future))

            if len(self.pending[batch_key]) >= self.batch_size:
                await self._flush_batch(batch_key)

        # Wait for batch timeout if not flushed
        try:
            return await asyncio.wait_for(future, timeout=self.batch_timeout)
        except asyncio.TimeoutError:
            async with self.locks[batch_key]:
                await self._flush_batch(batch_key)
            return await future

    async def _flush_batch(self, batch_key: str):
        """Process all pending requests in batch."""

        if not self.pending[batch_key]:
            return

        batch = self.pending[batch_key]
        self.pending[batch_key] = []

        # Process batch
        requests = [r for r, _ in batch]
        responses = await self._process_batch(requests)

        # Resolve futures
        for (_, future), response in zip(batch, responses):
            future.set_result(response)

    def _get_batch_key(self, request: dict) -> str:
        """Group similar requests."""
        return f"{request.get('model')}_{request.get('task_type')}"
```

### Embedding Batching

```python
class EmbeddingBatcher:
    """Batch embedding requests for cost efficiency."""

    def __init__(self, max_batch_size: int = 100):
        self.max_batch_size = max_batch_size
        self.queue: list[tuple[str, asyncio.Future]] = []
        self.lock = asyncio.Lock()

    async def embed(self, text: str) -> list[float]:
        """Get embedding, batching with other requests."""

        future = asyncio.Future()

        async with self.lock:
            self.queue.append((text, future))

            if len(self.queue) >= self.max_batch_size:
                await self._process_queue()

        return await future

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts."""

        # Split into batches
        batches = [
            texts[i:i + self.max_batch_size]
            for i in range(0, len(texts), self.max_batch_size)
        ]

        results = []
        for batch in batches:
            batch_embeddings = await self._embed_batch(batch)
            results.extend(batch_embeddings)

        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Send batch to embedding API."""
        # EMBEDDING_MODEL is a single constant like MODELS above. It is also the
        # one id you cannot swap freely: changing it invalidates every vector
        # already in the store, so a change here means a reindex, not a redeploy.
        response = await self.embedding_client.create(
            model=EMBEDDING_MODEL,
            input=texts
        )
        return [e.embedding for e in response.data]
```

## Prompt Optimization

### Token Reduction

```python
class PromptOptimizer:
    """Reduce prompt tokens while maintaining quality."""

    def optimize(self, prompt: str) -> str:
        """Apply token reduction techniques."""

        # Remove redundant whitespace
        prompt = " ".join(prompt.split())

        # Use abbreviations for common terms
        prompt = self._apply_abbreviations(prompt)

        # Compress examples if too many
        prompt = self._compress_examples(prompt)

        return prompt

    def _apply_abbreviations(self, text: str) -> str:
        """Replace verbose phrases with shorter versions."""

        replacements = {
            "for example": "e.g.",
            "that is": "i.e.",
            "please provide": "provide",
            "I would like you to": "",
            "Could you please": "",
        }

        for verbose, short in replacements.items():
            text = text.replace(verbose, short)

        return text

    def _compress_examples(
        self,
        prompt: str,
        max_examples: int = 3
    ) -> str:
        """Limit number of examples in prompt."""

        # Find example sections
        if "Examples:" not in prompt:
            return prompt

        parts = prompt.split("Examples:")
        if len(parts) != 2:
            return prompt

        before, examples_section = parts

        # Parse and limit examples
        examples = self._parse_examples(examples_section)
        limited = examples[:max_examples]

        return before + "Examples:\n" + "\n".join(limited)

# Context window management
def fit_to_context(
    messages: list[dict],
    max_tokens: int,
    reserve_output: int = 4000
) -> list[dict]:
    """Fit messages within context window."""

    available = max_tokens - reserve_output

    # Always keep system message
    system = [m for m in messages if m["role"] == "system"]
    system_tokens = estimate_tokens(system)

    available -= system_tokens

    # Add messages from most recent
    result = system.copy()
    for msg in reversed(messages):
        if msg["role"] == "system":
            continue

        msg_tokens = estimate_tokens([msg])
        if msg_tokens <= available:
            result.insert(len(system), msg)
            available -= msg_tokens
        else:
            break

    return result
```

## Budget Management

### Budget Limits

```python
from datetime import datetime, timedelta

class BudgetManager:
    """Manage and enforce budget limits."""

    def __init__(
        self,
        daily_limit: float,
        monthly_limit: float
    ):
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.cost_tracker = CostTracker()

    def can_proceed(self, estimated_cost: float) -> bool:
        """Check if request is within budget."""

        daily_spent = self._get_daily_spend()
        monthly_spent = self._get_monthly_spend()

        if daily_spent + estimated_cost > self.daily_limit:
            return False

        if monthly_spent + estimated_cost > self.monthly_limit:
            return False

        return True

    def get_remaining_budget(self) -> dict:
        """Get remaining budget."""
        return {
            "daily_remaining": self.daily_limit - self._get_daily_spend(),
            "monthly_remaining": self.monthly_limit - self._get_monthly_spend(),
            "daily_limit": self.daily_limit,
            "monthly_limit": self.monthly_limit
        }

    async def execute_with_budget(
        self,
        func,
        estimated_cost: float,
        *args,
        **kwargs
    ):
        """Execute function if within budget."""

        if not self.can_proceed(estimated_cost):
            raise BudgetExceededError(
                f"Request would exceed budget. "
                f"Remaining: {self.get_remaining_budget()}"
            )

        result = await func(*args, **kwargs)
        return result

# Usage
budget_manager = BudgetManager(
    daily_limit=100.0,    # $100/day
    monthly_limit=2000.0  # $2000/month
)

async def make_llm_call(prompt: str) -> str:
    estimated_cost = estimate_cost(prompt)

    return await budget_manager.execute_with_budget(
        llm.generate,
        estimated_cost,
        prompt=prompt
    )
```

### Cost Alerts

```python
class CostAlertManager:
    """Monitor and alert on cost thresholds."""

    def __init__(self, thresholds: dict[str, float]):
        self.thresholds = thresholds
        self.alerts_sent: set[str] = set()

    async def check_and_alert(self, current_cost: float, period: str):
        """Check cost against thresholds and send alerts."""

        threshold = self.thresholds.get(period, float("inf"))
        percentage = (current_cost / threshold) * 100

        alert_levels = [50, 75, 90, 100]

        for level in alert_levels:
            if percentage >= level:
                alert_key = f"{period}_{level}"

                if alert_key not in self.alerts_sent:
                    await self._send_alert(
                        f"Cost alert: {period} spend at {percentage:.1f}% "
                        f"(${current_cost:.2f} of ${threshold:.2f})"
                    )
                    self.alerts_sent.add(alert_key)

    def reset_alerts(self, period: str):
        """Reset alerts for new period."""
        self.alerts_sent = {
            k for k in self.alerts_sent
            if not k.startswith(period)
        }
```
