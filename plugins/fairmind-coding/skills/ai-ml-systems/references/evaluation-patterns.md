# Evaluation Patterns Reference

## Evaluation Framework

### Core Metrics

```python
from dataclasses import dataclass
from enum import Enum

class MetricType(Enum):
    ACCURACY = "accuracy"
    RELEVANCE = "relevance"
    COHERENCE = "coherence"
    FAITHFULNESS = "faithfulness"
    LATENCY = "latency"
    COST = "cost"

@dataclass
class EvaluationResult:
    metric: MetricType
    score: float  # 0.0 to 1.0
    confidence: float
    details: dict

class Evaluator:
    """Base evaluation framework."""

    def __init__(self, metrics: list[MetricType]):
        self.metrics = metrics
        self.evaluators = {
            MetricType.ACCURACY: AccuracyEvaluator(),
            MetricType.RELEVANCE: RelevanceEvaluator(),
            MetricType.COHERENCE: CoherenceEvaluator(),
            MetricType.FAITHFULNESS: FaithfulnessEvaluator(),
        }

    async def evaluate(
        self,
        input: str,
        output: str,
        context: dict | None = None
    ) -> list[EvaluationResult]:
        results = []

        for metric in self.metrics:
            evaluator = self.evaluators.get(metric)
            if evaluator:
                result = await evaluator.evaluate(input, output, context)
                results.append(result)

        return results
```

### Accuracy Evaluation

```python
# The judge model is pinned in one place. It is not the model under test: pin it
# deliberately and change it deliberately, because swapping the judge re-baselines
# every score you have ever recorded and silently breaks comparisons across runs.
# Ids live in MODELS — see references/cost-optimization.md § Model IDs and pricing.
JUDGE_MODEL = MODELS["low"]

# The `temperature=0.0` in the judge calls below is only accepted by the Haiku
# tier and older. Promote JUDGE_MODEL to the Sonnet or Opus tier and every judge
# call 400s on the sampling parameter — drop `temperature` when you promote it.

class AccuracyEvaluator:
    """Evaluate correctness of outputs."""

    async def evaluate(
        self,
        input: str,
        output: str,
        context: dict
    ) -> EvaluationResult:

        if "expected" in context:
            # Exact match or similarity
            score = self._compare_outputs(
                output,
                context["expected"]
            )
        else:
            # LLM-based evaluation
            score = await self._llm_evaluate(input, output, context)

        return EvaluationResult(
            metric=MetricType.ACCURACY,
            score=score,
            confidence=0.8,
            details={"method": "comparison" if "expected" in context else "llm"}
        )

    def _compare_outputs(self, actual: str, expected: str) -> float:
        """Compare outputs using multiple methods."""

        # Exact match
        if actual.strip() == expected.strip():
            return 1.0

        # Normalized comparison
        actual_norm = self._normalize(actual)
        expected_norm = self._normalize(expected)

        if actual_norm == expected_norm:
            return 0.95

        # Semantic similarity
        return self._semantic_similarity(actual, expected)

    async def _llm_evaluate(
        self,
        input: str,
        output: str,
        context: dict
    ) -> float:
        """Use LLM to evaluate correctness."""

        prompt = f"""Evaluate the correctness of this response.

Input: {input}
Output: {output}
Context: {context.get('task_description', 'General task')}

Rate the correctness from 0 to 10, where:
- 10: Perfectly correct and complete
- 7-9: Mostly correct with minor issues
- 4-6: Partially correct
- 1-3: Mostly incorrect
- 0: Completely wrong

Respond with just the number."""

        response = await self.llm.generate(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )

        score = int(response.strip()) / 10
        return score
```

### Relevance Evaluation

```python
class RelevanceEvaluator:
    """Evaluate relevance of response to query."""

    RELEVANCE_PROMPT = """
    Evaluate how relevant the response is to the query.

    Query: {query}
    Response: {response}

    Consider:
    1. Does the response address the query directly?
    2. Is the information provided useful for the query?
    3. Are there irrelevant or off-topic parts?

    Rate relevance from 0 to 10:
    - 10: Perfectly relevant, directly addresses query
    - 7-9: Highly relevant with minor tangents
    - 4-6: Somewhat relevant
    - 1-3: Mostly irrelevant
    - 0: Completely irrelevant

    Respond with: SCORE: [number]
    REASONING: [brief explanation]
    """

    async def evaluate(
        self,
        input: str,
        output: str,
        context: dict
    ) -> EvaluationResult:

        prompt = self.RELEVANCE_PROMPT.format(
            query=input,
            response=output
        )

        response = await self.llm.generate(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )

        score, reasoning = self._parse_response(response)

        return EvaluationResult(
            metric=MetricType.RELEVANCE,
            score=score / 10,
            confidence=0.85,
            details={"reasoning": reasoning}
        )
```

### Faithfulness Evaluation (RAG)

```python
class FaithfulnessEvaluator:
    """Evaluate if response is grounded in provided context."""

    async def evaluate(
        self,
        input: str,
        output: str,
        context: dict
    ) -> EvaluationResult:

        source_docs = context.get("source_documents", [])

        if not source_docs:
            return EvaluationResult(
                metric=MetricType.FAITHFULNESS,
                score=0.5,
                confidence=0.3,
                details={"error": "No source documents provided"}
            )

        # Extract claims from output
        claims = await self._extract_claims(output)

        # Verify each claim against sources
        verified_claims = 0
        claim_details = []

        for claim in claims:
            is_supported, evidence = await self._verify_claim(
                claim,
                source_docs
            )

            if is_supported:
                verified_claims += 1

            claim_details.append({
                "claim": claim,
                "supported": is_supported,
                "evidence": evidence
            })

        score = verified_claims / len(claims) if claims else 0.0

        return EvaluationResult(
            metric=MetricType.FAITHFULNESS,
            score=score,
            confidence=0.9,
            details={"claims": claim_details}
        )

    async def _extract_claims(self, text: str) -> list[str]:
        """Extract factual claims from text."""

        prompt = f"""Extract all factual claims from this text.

Text: {text}

List each claim on a separate line. Only include verifiable factual statements.
"""

        response = await self.llm.generate(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )

        return [c.strip() for c in response.split("\n") if c.strip()]
```

## Benchmark Design

### Task-Specific Benchmarks

```python
@dataclass
class BenchmarkCase:
    id: str
    input: str
    expected_output: str | None
    context: dict
    tags: list[str]

class Benchmark:
    """Benchmark suite for evaluation."""

    def __init__(self, name: str, cases: list[BenchmarkCase]):
        self.name = name
        self.cases = cases

    async def run(
        self,
        agent: Agent,
        evaluator: Evaluator
    ) -> BenchmarkResult:

        results = []

        for case in self.cases:
            # Generate output
            output = await agent.execute(case.input)

            # Evaluate
            eval_results = await evaluator.evaluate(
                input=case.input,
                output=output,
                context={**case.context, "expected": case.expected_output}
            )

            results.append({
                "case_id": case.id,
                "input": case.input,
                "output": output,
                "expected": case.expected_output,
                "evaluations": eval_results
            })

        return BenchmarkResult(
            benchmark_name=self.name,
            results=results,
            summary=self._compute_summary(results)
        )

# Example benchmark
QA_BENCHMARK = Benchmark(
    name="question_answering",
    cases=[
        BenchmarkCase(
            id="qa_001",
            input="What is the capital of France?",
            expected_output="Paris",
            context={"category": "factual"},
            tags=["geography", "simple"]
        ),
        # More cases...
    ]
)
```

### Regression Testing

```python
class RegressionTester:
    """Track performance over time."""

    def __init__(self, baseline_path: str):
        self.baseline = self._load_baseline(baseline_path)

    async def test(
        self,
        agent: Agent,
        benchmark: Benchmark
    ) -> RegressionReport:

        current = await benchmark.run(agent)

        regressions = []
        improvements = []

        for case_id, current_score in current.scores.items():
            baseline_score = self.baseline.scores.get(case_id)

            if baseline_score is None:
                continue

            delta = current_score - baseline_score

            if delta < -0.05:  # 5% regression threshold
                regressions.append({
                    "case_id": case_id,
                    "baseline": baseline_score,
                    "current": current_score,
                    "delta": delta
                })
            elif delta > 0.05:
                improvements.append({
                    "case_id": case_id,
                    "baseline": baseline_score,
                    "current": current_score,
                    "delta": delta
                })

        return RegressionReport(
            regressions=regressions,
            improvements=improvements,
            overall_delta=current.mean_score - self.baseline.mean_score
        )
```

## A/B Testing

### Experiment Framework

```python
import random
from datetime import datetime

@dataclass
class Experiment:
    name: str
    control: Agent
    treatment: Agent
    traffic_split: float  # 0.0 to 1.0 for treatment
    start_date: datetime
    end_date: datetime | None = None

class ExperimentRunner:
    """Run A/B tests on agent variants."""

    def __init__(self):
        self.experiments: dict[str, Experiment] = {}
        self.results: dict[str, list[dict]] = {}

    def register(self, experiment: Experiment):
        self.experiments[experiment.name] = experiment
        self.results[experiment.name] = []

    async def execute(
        self,
        experiment_name: str,
        input: str,
        user_id: str
    ) -> tuple[str, str]:  # (output, variant)

        experiment = self.experiments[experiment_name]

        # Deterministic assignment based on user_id
        variant = self._assign_variant(user_id, experiment.traffic_split)

        agent = experiment.treatment if variant == "treatment" else experiment.control

        output = await agent.execute(input)

        # Record for analysis
        self.results[experiment_name].append({
            "user_id": user_id,
            "variant": variant,
            "input": input,
            "output": output,
            "timestamp": datetime.now()
        })

        return output, variant

    def _assign_variant(self, user_id: str, split: float) -> str:
        """Deterministic variant assignment."""
        hash_value = hash(user_id) % 100
        return "treatment" if hash_value < split * 100 else "control"

    def analyze(self, experiment_name: str) -> dict:
        """Analyze experiment results."""
        results = self.results[experiment_name]

        control = [r for r in results if r["variant"] == "control"]
        treatment = [r for r in results if r["variant"] == "treatment"]

        return {
            "control_count": len(control),
            "treatment_count": len(treatment),
            "control_metrics": self._compute_metrics(control),
            "treatment_metrics": self._compute_metrics(treatment),
            "significance": self._compute_significance(control, treatment)
        }
```

## Human Evaluation

### Rating Collection

```python
@dataclass
class HumanRating:
    evaluator_id: str
    case_id: str
    rating: int  # 1-5
    criteria: str
    feedback: str | None
    timestamp: datetime

class HumanEvaluationManager:
    """Collect and analyze human evaluations."""

    def __init__(self):
        self.ratings: list[HumanRating] = []

    def create_evaluation_task(
        self,
        cases: list[dict],
        criteria: list[str]
    ) -> EvaluationTask:
        """Create task for human evaluators."""

        return EvaluationTask(
            id=generate_id(),
            cases=cases,
            criteria=criteria,
            instructions=self._generate_instructions(criteria)
        )

    def submit_rating(self, rating: HumanRating):
        self.ratings.append(rating)

    def compute_agreement(self, case_id: str) -> float:
        """Compute inter-rater agreement."""
        case_ratings = [r for r in self.ratings if r.case_id == case_id]

        if len(case_ratings) < 2:
            return 1.0

        # Krippendorff's alpha or similar
        return self._compute_krippendorff_alpha(case_ratings)

    def aggregate_ratings(self, case_id: str) -> dict:
        """Aggregate ratings for a case."""
        case_ratings = [r for r in self.ratings if r.case_id == case_id]

        ratings_by_criteria = {}
        for rating in case_ratings:
            if rating.criteria not in ratings_by_criteria:
                ratings_by_criteria[rating.criteria] = []
            ratings_by_criteria[rating.criteria].append(rating.rating)

        return {
            criteria: {
                "mean": sum(ratings) / len(ratings),
                "std": self._std(ratings),
                "count": len(ratings)
            }
            for criteria, ratings in ratings_by_criteria.items()
        }
```

## Continuous Evaluation

### Production Monitoring

```python
class ProductionEvaluator:
    """Continuous evaluation in production."""

    def __init__(self, sample_rate: float = 0.1):
        self.sample_rate = sample_rate
        self.evaluator = Evaluator([
            MetricType.RELEVANCE,
            MetricType.COHERENCE
        ])

    async def maybe_evaluate(
        self,
        input: str,
        output: str,
        context: dict
    ) -> list[EvaluationResult] | None:
        """Probabilistically evaluate production outputs."""

        if random.random() > self.sample_rate:
            return None

        results = await self.evaluator.evaluate(input, output, context)

        # Store for analysis
        await self._store_results(input, output, results)

        # Alert on low scores
        for result in results:
            if result.score < 0.5:
                await self._alert_low_score(result, input, output)

        return results

    async def get_daily_report(self) -> dict:
        """Generate daily evaluation report."""

        today_results = await self._get_results(days=1)

        return {
            "total_evaluated": len(today_results),
            "mean_scores": self._compute_mean_scores(today_results),
            "low_score_count": sum(
                1 for r in today_results
                if any(e.score < 0.5 for e in r["evaluations"])
            ),
            "score_distribution": self._compute_distribution(today_results)
        }
```

### Drift Detection

```python
class DriftDetector:
    """Detect performance drift over time."""

    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self.scores: list[float] = []

    def add_score(self, score: float):
        self.scores.append(score)

        if len(self.scores) > self.window_size * 2:
            self.scores = self.scores[-self.window_size * 2:]

    def check_drift(self) -> dict | None:
        """Check for significant drift."""

        if len(self.scores) < self.window_size * 2:
            return None

        old_window = self.scores[:self.window_size]
        new_window = self.scores[-self.window_size:]

        old_mean = sum(old_window) / len(old_window)
        new_mean = sum(new_window) / len(new_window)

        drift = new_mean - old_mean

        if abs(drift) > 0.1:  # 10% drift threshold
            return {
                "drift_detected": True,
                "old_mean": old_mean,
                "new_mean": new_mean,
                "drift": drift,
                "direction": "improvement" if drift > 0 else "degradation"
            }

        return {"drift_detected": False}
```
