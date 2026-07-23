# LLM Optimization Reference

## Model Selection

### Model Tiers

```
┌─────────────────────────────────────────────────────────┐
│                    Model Selection Guide                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Complexity ▲                                           │
│             │  ┌─────────────────────────────────┐      │
│    High     │  │  Opus tier                      │      │
│             │  │  Complex reasoning, agentic runs │      │
│             │  └─────────────────────────────────┘      │
│             │  ┌─────────────────────────────────┐      │
│    Medium   │  │  Sonnet tier                    │      │
│             │  │  Code, structured output        │      │
│             │  └─────────────────────────────────┘      │
│             │  ┌─────────────────────────────────┐      │
│    Low      │  │  Haiku tier                     │      │
│             │  │  Classification, extraction     │      │
│             │  └─────────────────────────────────┘      │
│             └────────────────────────────────────► Cost │
└─────────────────────────────────────────────────────────┘
```

Reason in **tiers**, not in ids. A tier survives a model generation; an id does
not. Map tier → id exactly once (`MODELS` in `references/cost-optimization.md`)
and route against the tier everywhere else.

### Task-Based Selection

```python
# Tier per task type. The one place ids appear is MODELS — see
# references/cost-optimization.md § Model IDs and pricing live in ONE constant.
TASK_TIER = {
    # High complexity - use best model
    "complex_reasoning": "high",
    "architecture_design": "high",

    # Medium complexity - balanced model
    "code_review": "medium",
    "code_generation": "medium",
    "summarization": "medium",
    "translation": "medium",

    # Low complexity - cost-effective model
    "classification": "low",
    "extraction": "low",
    "formatting": "low",
}

def select_model(task_type: str, context: dict) -> str:
    tier = TASK_TIER.get(task_type, "medium")

    # Upgrade for critical paths
    if context.get("is_critical"):
        tier = upgrade_tier(tier)

    return MODELS[tier]
```

## Prompt Optimization

### Structured Prompts

```python
OPTIMIZED_PROMPT = """
<context>
{system_context}
</context>

<task>
{task_description}
</task>

<constraints>
- Output format: {output_format}
- Max length: {max_length}
- Required fields: {required_fields}
</constraints>

<examples>
{few_shot_examples}
</examples>

<input>
{user_input}
</input>
"""
```

### Few-Shot Learning

```python
def create_few_shot_prompt(
    task: str,
    examples: list[dict],
    input_data: str
) -> str:
    """Create optimized few-shot prompt."""

    example_text = "\n\n".join([
        f"Input: {ex['input']}\nOutput: {ex['output']}"
        for ex in examples[:3]  # Limit to 3 examples
    ])

    return f"""Task: {task}

Examples:
{example_text}

Now process this input:
Input: {input_data}
Output:"""
```

### Chain of Thought

```python
COT_PROMPT = """
Solve this problem step by step.

Problem: {problem}

Think through this carefully:
1. First, identify the key elements
2. Then, analyze the relationships
3. Finally, derive the solution

Let's work through it:
"""

# For complex reasoning tasks
STRUCTURED_COT = """
<problem>
{problem}
</problem>

<analysis>
Step 1 - Understanding:
[Analyze the problem]

Step 2 - Approach:
[Determine solution strategy]

Step 3 - Execution:
[Apply the approach]

Step 4 - Verification:
[Check the solution]
</analysis>

<answer>
[Final answer]
</answer>
"""
```

## Output Optimization

### Structured Output

```python
from pydantic import BaseModel

class AnalysisResult(BaseModel):
    summary: str
    key_points: list[str]
    confidence: float
    recommendations: list[str]

# Force structured output
response = client.messages.create(
    model=MODELS["medium"],
    messages=[{"role": "user", "content": prompt}],
    tools=[{
        "name": "submit_analysis",
        "description": "Submit structured analysis",
        "input_schema": AnalysisResult.model_json_schema()
    }],
    tool_choice={"type": "tool", "name": "submit_analysis"}
)
```

### Output Parsing

```python
import json
from typing import TypeVar, Type

T = TypeVar('T', bound=BaseModel)

def parse_llm_output(
    response: str,
    model_class: Type[T],
    fallback: T | None = None
) -> T:
    """Parse LLM output with fallback."""
    try:
        # Try JSON parsing first
        data = json.loads(response)
        return model_class.model_validate(data)
    except json.JSONDecodeError:
        # Try extracting JSON from markdown
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
            data = json.loads(json_str)
            return model_class.model_validate(data)
    except Exception as e:
        if fallback:
            return fallback
        raise ValueError(f"Failed to parse LLM output: {e}")
```

## Effort and Sampling

### Sampling parameters are not the knob any more

The current Opus and Sonnet tiers **reject** `temperature`, `top_p`, and `top_k`
at non-default values with a 400. A `TEMPERATURE_SETTINGS` table keyed by task
type is only safe if every model it can route to still accepts sampling params
(the Haiku tier and older do) — key it by task while routing across tiers and the
first request that lands on the frontier tier is an error, not a degraded answer.

```python
# Steer with the prompt; constrain shape with structured output (above).
# "temperature=0 for determinism" was never a determinism guarantee even on the
# models that accept it — identical inputs could still yield different outputs.
response = client.messages.create(
    model=MODELS["medium"],
    messages=[{"role": "user", "content": prompt}],
)
```

### Effort is the depth/cost dial

```python
# effort: low | medium | high | xhigh | max   (default: high)
EFFORT_BY_TASK = {
    "classification": "low",
    "extraction": "low",
    "code_generation": "high",
    "complex_reasoning": "xhigh",
}

response = client.messages.create(
    model=MODELS["medium"],
    messages=[{"role": "user", "content": prompt}],
    # Set adaptive thinking explicitly: on the Opus tier, omitting `thinking`
    # runs the request with no thinking at all.
    thinking={"type": "adaptive"},
    output_config={"effort": EFFORT_BY_TASK.get(task_type, "high")},
)
```

Lower effort means fewer, more consolidated tool calls and less preamble. When a
task reasons too shallowly, raise `effort` before you try to prompt around it —
and give `max_tokens` headroom at `xhigh`/`max`, because thinking is billed
against the same output budget as the answer and a tight cap truncates it.

## Context Window Optimization

### Token Estimation

```python
def count_tokens(messages: list[dict], model: str) -> int:
    """Exact token count for these messages on this model.

    Tokenization is model-specific and changes between generations, so a
    chars-per-token constant is wrong by an unknown margin — and wrong in the
    direction that matters, since it is the budget you trim against. Ask the
    provider: `client.messages.count_tokens(model=..., messages=...)`. Never
    reach for another vendor's tokenizer (tiktoken) to size a Claude prompt.
    """
    return client.messages.count_tokens(model=model, messages=messages).input_tokens

def estimate_tokens(text: str) -> int:
    """Offline lower-bound estimate — use only where a network call cannot go
    (a hot path, a synchronous UI budget bar). Anything that gates a real
    request should call count_tokens above."""
    return len(text) // 4

def fits_context(
    messages: list[dict],
    context_window: int,
) -> bool:
    """Check if messages fit in context window.

    No default: a context window is a property of the model you are about to
    call, not of your code. Read it per model — `client.models.retrieve(id)`
    returns `max_input_tokens` — and pass it in. A hardcoded default silently
    becomes wrong the moment routing sends the request to a different tier.
    """
    total = sum(estimate_tokens(m["content"]) for m in messages)
    return total < context_window * 0.8  # Leave 20% buffer
```

### Context Compression

```python
def compress_context(
    messages: list[dict],
    max_tokens: int
) -> list[dict]:
    """Compress context to fit token limit."""

    # Always keep system message and recent messages
    system = [m for m in messages if m["role"] == "system"]
    recent = messages[-5:]  # Last 5 messages

    current_tokens = sum(
        estimate_tokens(m["content"])
        for m in system + recent
    )

    # Add older messages if space allows
    result = system.copy()
    for msg in messages[len(system):-5]:
        msg_tokens = estimate_tokens(msg["content"])
        if current_tokens + msg_tokens < max_tokens:
            result.append(msg)
            current_tokens += msg_tokens

    result.extend(recent)
    return result
```

## Fine-Tuning Considerations

### When to Fine-Tune

```
Fine-tune when:
├── Consistent specialized format needed
├── Domain-specific terminology important
├── Prompt engineering insufficient
└── Cost savings at scale justify training cost

Don't fine-tune when:
├── Few-shot prompting works well
├── Task requirements change frequently
├── Limited training data available
└── General reasoning needed
```

### Fine-Tuning Data Format

```python
# Training data format for Claude
training_examples = [
    {
        "messages": [
            {"role": "user", "content": "Input text here"},
            {"role": "assistant", "content": "Expected output"}
        ]
    },
    # More examples...
]

# Minimum: 100-1000 high-quality examples
# Quality > Quantity
```

## Performance Monitoring

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class LLMMetrics:
    model: str
    task_type: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    success: bool
    timestamp: datetime

class LLMMonitor:
    def __init__(self):
        self.metrics: list[LLMMetrics] = []

    def record(self, metrics: LLMMetrics):
        self.metrics.append(metrics)

        # Alert on high latency
        if metrics.latency_ms > 5000:
            self.alert(f"High latency: {metrics.latency_ms}ms")

        # Alert on failures
        if not metrics.success:
            self.alert(f"LLM call failed: {metrics.model}")

    def get_stats(self, model: str) -> dict:
        model_metrics = [m for m in self.metrics if m.model == model]
        return {
            "avg_latency": sum(m.latency_ms for m in model_metrics) / len(model_metrics),
            "success_rate": sum(1 for m in model_metrics if m.success) / len(model_metrics),
            "total_tokens": sum(m.input_tokens + m.output_tokens for m in model_metrics),
        }
```
