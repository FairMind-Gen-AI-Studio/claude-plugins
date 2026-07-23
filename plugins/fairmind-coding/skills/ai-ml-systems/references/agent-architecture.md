# Agent Architecture Reference

## Model IDs

Every snippet below routes through the tier map, never a literal id — an agent
system typically calls models from a planner, a specialist, and a fallback ladder,
and three inlined ids is three places to miss on the next generation.

```python
# The single source of truth for ids and prices, shared with the router and the
# cost tracker: references/cost-optimization.md § Model IDs and pricing.
MODELS = {
    "high": "claude-opus-4-8",
    "medium": "claude-sonnet-5",
    "low": "claude-haiku-4-5",
}
```

## Multi-Agent Patterns

### Single Agent (Simple)

```python
class SimpleAgent:
    """Single agent for straightforward tasks."""

    def __init__(self, model: str, tools: list[Tool]):
        self.model = model
        self.tools = tools

    async def execute(self, task: str) -> str:
        messages = [{"role": "user", "content": task}]

        while True:
            response = await self.llm.generate(
                model=self.model,
                messages=messages,
                tools=self.tools
            )

            if response.stop_reason == "end_turn":
                return response.content

            # Execute tool calls
            tool_results = await self.execute_tools(response.tool_calls)
            messages.extend(tool_results)
```

### Supervisor Pattern

```
┌─────────────────────────────────────────────────────────┐
│                      Supervisor                          │
│  - Routes tasks to specialists                          │
│  - Aggregates results                                    │
│  - Makes final decisions                                 │
├─────────────────────────────────────────────────────────┤
│     ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│     │Research │    │ Writer  │    │Reviewer │         │
│     │ Agent   │    │ Agent   │    │ Agent   │         │
│     └─────────┘    └─────────┘    └─────────┘         │
└─────────────────────────────────────────────────────────┘
```

```python
class SupervisorAgent:
    """Orchestrates multiple specialist agents."""

    def __init__(self):
        self.specialists = {
            "research": ResearchAgent(),
            "writing": WriterAgent(),
            "review": ReviewerAgent(),
        }

    async def execute(self, task: str) -> str:
        # 1. Analyze task and create plan
        plan = await self.create_plan(task)

        # 2. Route to specialists
        results = {}
        for step in plan.steps:
            specialist = self.specialists[step.agent]
            results[step.id] = await specialist.execute(step.task)

        # 3. Aggregate and finalize
        return await self.aggregate_results(results)

    async def create_plan(self, task: str) -> Plan:
        response = await self.llm.generate(
            model=MODELS["medium"],
            messages=[{
                "role": "user",
                "content": f"Create execution plan for: {task}"
            }],
            tools=[self.plan_tool]
        )
        return Plan.from_response(response)
```

### Pipeline Pattern

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Input   │───►│ Stage 1  │───►│ Stage 2  │───►│  Output  │
│ Handler  │    │ Process  │    │ Validate │    │ Handler  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

```python
class Pipeline:
    """Sequential processing pipeline."""

    def __init__(self, stages: list[Agent]):
        self.stages = stages

    async def execute(self, input_data: Any) -> Any:
        current = input_data

        for stage in self.stages:
            current = await stage.process(current)

            # Validation between stages
            if not self.validate_output(stage, current):
                raise PipelineError(f"Stage {stage.name} produced invalid output")

        return current

# Usage
pipeline = Pipeline([
    DataExtractionAgent(),
    DataValidationAgent(),
    DataTransformationAgent(),
    OutputFormattingAgent(),
])
```

### Parallel Execution Pattern

```python
import asyncio

class ParallelOrchestrator:
    """Execute independent tasks in parallel."""

    async def execute_parallel(
        self,
        tasks: list[Task],
        agents: dict[str, Agent]
    ) -> list[Result]:

        # Group independent tasks
        independent_groups = self.find_independent_groups(tasks)

        results = []
        for group in independent_groups:
            # Execute group in parallel
            group_results = await asyncio.gather(*[
                agents[task.agent_type].execute(task)
                for task in group
            ])
            results.extend(group_results)

        return results

    def find_independent_groups(self, tasks: list[Task]) -> list[list[Task]]:
        """Group tasks that can run in parallel."""
        # Tasks with no dependencies on each other
        groups = []
        remaining = set(tasks)

        while remaining:
            # Find tasks with no unmet dependencies
            ready = {t for t in remaining if self.deps_met(t, remaining)}
            groups.append(list(ready))
            remaining -= ready

        return groups
```

### Hierarchical Pattern

```
┌─────────────────────────────────────────────────────────┐
│                    Strategic Agent                       │
│              (High-level planning)                       │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────┐         ┌─────────────────┐       │
│  │ Tactical Agent  │         │ Tactical Agent  │       │
│  │   (Planning)    │         │   (Planning)    │       │
│  ├─────────────────┤         ├─────────────────┤       │
│  │┌──────┐┌──────┐│         │┌──────┐┌──────┐│       │
│  ││Worker││Worker││         ││Worker││Worker││       │
│  │└──────┘└──────┘│         │└──────┘└──────┘│       │
│  └─────────────────┘         └─────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

## Tool Design

### Tool Definition Best Practices

```python
from pydantic import BaseModel, Field

class SearchToolInput(BaseModel):
    """Input schema for search tool."""
    query: str = Field(description="Search query string")
    max_results: int = Field(default=10, ge=1, le=100)
    filters: dict | None = Field(default=None, description="Optional filters")

SEARCH_TOOL = {
    "name": "search_documents",
    "description": """Search the document database.

    Use this tool when you need to:
    - Find relevant documents for a topic
    - Look up specific information
    - Gather context for analysis

    Returns a list of matching documents with relevance scores.""",
    "input_schema": SearchToolInput.model_json_schema()
}
```

### Tool Implementation

```python
class ToolRegistry:
    """Registry for agent tools."""

    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, name: str, handler: Callable, schema: dict):
        self.tools[name] = Tool(
            name=name,
            handler=handler,
            schema=schema
        )

    async def execute(self, name: str, input: dict) -> Any:
        tool = self.tools.get(name)
        if not tool:
            raise ToolNotFoundError(f"Tool {name} not found")

        # Validate input
        validated = tool.validate_input(input)

        # Execute with timeout
        try:
            result = await asyncio.wait_for(
                tool.handler(**validated),
                timeout=30.0
            )
            return result
        except asyncio.TimeoutError:
            raise ToolTimeoutError(f"Tool {name} timed out")
```

### Tool Categories

```python
# Information Retrieval Tools
RETRIEVAL_TOOLS = [
    "search_documents",
    "query_database",
    "fetch_url",
    "read_file",
]

# Action Tools
ACTION_TOOLS = [
    "create_document",
    "send_email",
    "execute_code",
    "call_api",
]

# Analysis Tools
ANALYSIS_TOOLS = [
    "analyze_sentiment",
    "extract_entities",
    "summarize_text",
    "compare_documents",
]

# Tool assignment by agent role
AGENT_TOOLS = {
    "researcher": RETRIEVAL_TOOLS + ANALYSIS_TOOLS,
    "writer": RETRIEVAL_TOOLS + ACTION_TOOLS[:2],
    "executor": ACTION_TOOLS,
}
```

## State Management

### Conversation State

```python
from dataclasses import dataclass, field

@dataclass
class ConversationState:
    """Maintains conversation context."""

    messages: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def get_context(self, max_messages: int = 20) -> list[dict]:
        """Get recent context for LLM."""
        return self.messages[-max_messages:]

    def summarize_if_needed(self, threshold: int = 50):
        """Summarize old messages to save context."""
        if len(self.messages) > threshold:
            old_messages = self.messages[:-20]
            summary = self._create_summary(old_messages)
            self.messages = [
                {"role": "system", "content": f"Previous context: {summary}"}
            ] + self.messages[-20:]
```

### Agent Memory

```python
class AgentMemory:
    """Long-term memory for agents."""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.short_term: list[dict] = []

    async def store(self, content: str, metadata: dict):
        """Store in long-term memory."""
        embedding = await self.embed(content)
        await self.vector_store.insert({
            "content": content,
            "embedding": embedding,
            "metadata": metadata,
            "timestamp": datetime.now()
        })

    async def recall(self, query: str, k: int = 5) -> list[dict]:
        """Retrieve relevant memories."""
        embedding = await self.embed(query)
        results = await self.vector_store.search(
            embedding=embedding,
            k=k
        )
        return results

    def add_short_term(self, item: dict):
        """Add to short-term memory (session-based)."""
        self.short_term.append(item)
        # Keep only recent items
        self.short_term = self.short_term[-100:]
```

## Error Handling

### Graceful Degradation

```python
class ResilientAgent:
    """Agent with fallback strategies."""

    async def execute(self, task: str) -> str:
        strategies = [
            self._primary_execution,
            self._simplified_execution,
            self._fallback_execution,
        ]

        for strategy in strategies:
            try:
                return await strategy(task)
            except Exception as e:
                self.log_error(strategy.__name__, e)
                continue

        return self._default_response(task)

    async def _primary_execution(self, task: str) -> str:
        """Full capability execution."""
        return await self.llm.generate(
            model=MODELS["medium"],
            messages=[{"role": "user", "content": task}],
            tools=self.all_tools
        )

    async def _simplified_execution(self, task: str) -> str:
        """Reduced capability execution."""
        return await self.llm.generate(
            model=MODELS["low"],
            messages=[{"role": "user", "content": task}],
            tools=self.essential_tools
        )

    async def _fallback_execution(self, task: str) -> str:
        """No tools, direct response."""
        return await self.llm.generate(
            model=MODELS["low"],
            messages=[{"role": "user", "content": task}]
        )
```

### Retry Logic

```python
import asyncio
from functools import wraps

def with_retry(max_attempts: int = 3, backoff: float = 1.0):
    """Retry decorator with exponential backoff."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except (RateLimitError, TimeoutError) as e:
                    last_error = e
                    wait_time = backoff * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    # Don't retry non-transient errors
                    raise

            raise last_error

        return wrapper
    return decorator

class Agent:
    @with_retry(max_attempts=3, backoff=1.0)
    async def call_llm(self, messages: list[dict]) -> str:
        return await self.llm.generate(messages=messages)
```

## Observability

### Logging and Tracing

```python
import structlog

logger = structlog.get_logger()

class TracedAgent:
    """Agent with comprehensive tracing."""

    async def execute(self, task: str) -> str:
        trace_id = generate_trace_id()

        with logger.bind(trace_id=trace_id, task=task[:100]):
            logger.info("agent_execution_start")

            start_time = time.time()
            try:
                result = await self._execute(task)

                logger.info(
                    "agent_execution_complete",
                    duration_ms=(time.time() - start_time) * 1000,
                    result_length=len(result)
                )
                return result

            except Exception as e:
                logger.error(
                    "agent_execution_failed",
                    error=str(e),
                    duration_ms=(time.time() - start_time) * 1000
                )
                raise
```

### Metrics Collection

```python
from prometheus_client import Counter, Histogram

AGENT_CALLS = Counter(
    'agent_calls_total',
    'Total agent executions',
    ['agent_type', 'status']
)

AGENT_LATENCY = Histogram(
    'agent_latency_seconds',
    'Agent execution latency',
    ['agent_type'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

class MetricsAgent:
    async def execute(self, task: str) -> str:
        with AGENT_LATENCY.labels(agent_type=self.name).time():
            try:
                result = await self._execute(task)
                AGENT_CALLS.labels(
                    agent_type=self.name,
                    status="success"
                ).inc()
                return result
            except Exception:
                AGENT_CALLS.labels(
                    agent_type=self.name,
                    status="failure"
                ).inc()
                raise
```
