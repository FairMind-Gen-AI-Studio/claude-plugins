---
name: ai-ml-systems
description: Use when designing AI/ML system architectures, optimizing LLM performance, building multi-agent systems, or implementing AI evaluation frameworks. Covers LLM optimization, agent orchestration, evaluation patterns, and cost management.
---

# AI/ML Systems Skill

## When to Use

Use this skill when:
- Designing multi-agent system architectures
- Optimizing LLM performance and costs
- Implementing AI evaluation frameworks
- Building orchestration layers for AI agents
- Selecting models and embedding strategies
- Implementing caching and cost optimization

## Core Workflow

### 1. Requirements Analysis

Before implementation:
- Define success metrics and evaluation criteria
- Identify latency and cost constraints
- Determine accuracy requirements
- Plan for observability and debugging

### 2. Architecture Design

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestration Layer                   │
├─────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
│  │ Agent 1 │  │ Agent 2 │  │ Agent 3 │  │ Agent N │   │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘   │
│       │            │            │            │         │
│  ┌────┴────────────┴────────────┴────────────┴────┐   │
│  │              Shared Tool Registry               │   │
│  └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   LLM Pool   │  │ Vector Store │  │    Cache     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 3. Model Selection

Choose a **tier** based on task complexity, and resolve the tier to a concrete
model id in exactly one constant (`MODELS`, see
`references/cost-optimization.md`). Ids and prices are a snapshot of a moving
target — a codebase that inlines them at every call site pays for the drift on
every model generation.

| Task Type | Tier | Rationale |
|-----------|------|-----------|
| Complex reasoning, long-horizon agent runs | Opus (`claude-opus-4-8`) | Highest capability; 1M context |
| Code generation, structured output | Sonnet (`claude-sonnet-5`) | Near-Opus quality at lower cost |
| Simple extraction, classification | Haiku (`claude-haiku-4-5`) | Cheapest; 200K context |
| Embeddings | Whatever model the vector index was built with | Changing it invalidates the index — a reindex, not a redeploy |
| Classification at volume | Fine-tuned small model | Task-specific |

Read per-model limits from the provider rather than hardcoding them
(`client.models.retrieve(id)` returns `max_input_tokens` / `max_tokens`).

### 4. Implementation Pattern

```python
class AISystem:
    def __init__(self, config: SystemConfig):
        self.orchestrator = Orchestrator(config)
        self.evaluator = Evaluator(config.metrics)
        self.cost_tracker = CostTracker()

    async def process(self, request: Request) -> Response:
        # 1. Route to appropriate agent/model
        agent = self.orchestrator.route(request)

        # 2. Execute with monitoring
        with self.cost_tracker.track():
            result = await agent.execute(request)

        # 3. Evaluate response quality
        evaluation = self.evaluator.evaluate(result)

        # 4. Log for observability
        self.log_execution(request, result, evaluation)

        return result
```

### 5. Evaluation Framework

Implement continuous evaluation:
- Accuracy metrics (task-specific)
- Latency percentiles (p50, p95, p99)
- Cost per request
- Error rates and types
- User satisfaction signals

## Reference Files

| File | Purpose |
|------|---------|
| `references/llm-optimization.md` | Model selection, prompt optimization, fine-tuning |
| `references/agent-architecture.md` | Multi-agent patterns, orchestration, tool design |
| `references/evaluation-patterns.md` | Metrics, benchmarks, A/B testing |
| `references/cost-optimization.md` | Caching, batching, model routing |

## Key Principles

1. **Start simple** - Single agent before multi-agent
2. **Measure everything** - Can't optimize what you don't measure
3. **Fail gracefully** - Always have fallback strategies
4. **Cache aggressively** - Same input = same output
5. **Right-size models** - Use smallest model that meets requirements

## Quality Checklist

Before deploying AI systems:

- [ ] Defined success metrics and thresholds
- [ ] Implemented evaluation framework
- [ ] Set up cost monitoring and alerts
- [ ] Created fallback strategies for failures
- [ ] Documented prompt templates and rationale
- [ ] Tested edge cases and adversarial inputs
- [ ] Configured observability (logs, traces, metrics)
- [ ] Reviewed for bias and safety concerns
