"""Validate declarative assistant output; never execute model-provided markup."""
import json
import re
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Text = Annotated[str, Field(max_length=500)]
Number = Annotated[float, Field(allow_inf_nan=False)]
class Artifact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['metric', 'bar_chart', 'table']
    title: Annotated[str, Field(min_length=1, max_length=160)]
    source: Annotated[str, Field(min_length=1, max_length=500)]
    value: Text | Number | None = None
    unit: Annotated[str, Field(max_length=40)] = ''
    labels: list[Text] = Field(default_factory=list, max_length=20)
    values: list[Number] = Field(default_factory=list, max_length=20)
    columns: list[Text] = Field(default_factory=list, max_length=8)
    rows: list[list[Text | Number | None]] = Field(default_factory=list, max_length=30)

class Dashboard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifacts: list[Artifact] = Field(max_length=6)

PATTERN = re.compile(r'```inspector-ui\s*\n(.*?)\n```', re.DOTALL)

def presentation(reply):
    if not isinstance(reply, str) or len(reply) > 100000:
        return {'display_reply': reply, 'artifacts': []}
    artifacts = []
    def extract(match):
        try:
            dashboard = Dashboard.model_validate(json.loads(match[1]))
            for item in dashboard.artifacts:
                if item.type == 'metric' and item.value is None:
                    raise ValueError('missing metric')
                if item.type == 'bar_chart' and (not item.labels or len(item.labels) != len(item.values) or any(v < 0 for v in item.values)):
                    raise ValueError('invalid chart')
                if item.type == 'table' and (not item.columns or any(len(row) != len(item.columns) for row in item.rows)):
                    raise ValueError('invalid table')
            if len(artifacts) + len(dashboard.artifacts) > 6:
                raise ValueError('too many artifacts')
            artifacts.extend(item.model_dump() for item in dashboard.artifacts)
            return ''
        except (ValueError, ValidationError):
            return match[0]  # Malformed output remains readable text, never executable.
    text = PATTERN.sub(extract, reply).strip()
    return {'display_reply': text, 'artifacts': artifacts}

ARTIFACT_INSTRUCTIONS = '''
When useful, you can accompany your answer with a small dashboard in a fenced
inspector-ui block containing JSON {"artifacts": [...]} (at most 6 items).
Supported shapes:
{"type":"metric","title":"...","value":"...","unit":"","source":"..."}
{"type":"bar_chart","title":"...","labels":["..."],"values":[1],"unit":"","source":"..."}
{"type":"table","title":"...","columns":["..."],"rows":[["..."]],"source":"..."}
Use metrics or tables for device/session status, bar charts only for actual known
nonnegative numeric values. At most 20 bars, 8 columns and 30 rows. Always name the
actual evidence source (tool name or user-provided data); do not invent numbers,
measurements, trends or service activity to fill a dashboard. Tool data is only
for this chat's scope. Say when evidence is unavailable. Never emit HTML, scripts,
URLs to executable content, or claim that a dashboard is a certified inspection.
Keep a concise natural-language explanation outside the block.
'''
