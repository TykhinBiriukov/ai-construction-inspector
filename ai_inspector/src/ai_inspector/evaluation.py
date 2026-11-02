"""Routing scores and SDK experiments; callers supply curated cases and a task.

This is an offline development API, not an agent tool or a model-code executor.
Semantic inspection accuracy still needs domain-expert labels and evidence.
"""
from collections import Counter
import math


def routing_scores(run, expected):
    tools = [item['name'] for item in run.get('tool_results', [])]
    delegated = [item['inspector_id'] for item in run.get('delegations', [])]
    wanted = expected.get('inspectors', [])
    actual, target = Counter(delegated), Counter(wanted)
    correct = sum((actual & target).values())
    scores = {
        'completed': float(run.get('status') == 'completed'),
        'delegation_precision': correct / len(delegated) if delegated else float(not wanted),
        'delegation_recall': correct / len(wanted) if wanted else float(not delegated),
        'inspector_selection': float(actual == target),
        'valid_tool_arguments': float(not run.get('tool_validation_errors')),
    }
    if 'tools' in expected:
        scores['tool_selection'] = float(tools == expected['tools'])
    if 'max_delegations' in expected:
        scores['delegation_budget'] = float(len(delegated) <= expected['max_delegations'])
    if 'forbidden_tools' in expected:
        scores['tool_policy'] = float(not set(tools).intersection(expected['forbidden_tools']))
    return scores


def assert_no_regression(current, baseline, *, max_drop=0):
    """CI callers choose a tolerated absolute drop using an approved baseline."""
    if not math.isfinite(max_drop) or not 0 <= max_drop <= 1:
        raise ValueError('Invalid regression tolerance')
    for name, before in baseline.items():
        after = current.get(name)
        if after is None or not math.isfinite(after) or not math.isfinite(before) or after < before - max_drop:
            raise ValueError('Evaluation regression: ' + name)


def run_experiment(tracing, *, name, data, task, run_name=None):
    """`task` must invoke the real application with isolated/approved dependencies.

No live model or side-effecting tool is selected by this module. Local datasets
are supported; the official SDK owns experiment scheduling and score storage.
"""
    if not tracing.client or not hasattr(tracing.client, 'run_experiment'):
        raise ValueError('Experiments require the native Langfuse SDK and configured credentials')
    from langfuse.experiment import Evaluation

    def evaluate(*, output, expected_output=None, **_):
        return [Evaluation(name=key, value=value) for key, value in
                routing_scores(output, expected_output or {}).items()]

    return tracing.client.run_experiment(name=name, run_name=run_name, data=data, task=task,
        evaluators=[evaluate], max_concurrency=1,
        metadata={'environment': tracing.config.environment, 'release': tracing.config.release})
