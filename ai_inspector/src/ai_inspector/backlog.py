"""Shared operator backlog. Revisions prevent overwriting a teammate's edits."""
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .errors import APIError

Status = Literal["planned", "in_progress", "done", "not_needed"]

class BacklogWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    contract: str = Field(default="", max_length=8000)
    web_status: Status = "planned"
    backend_status: Status = "planned"
    web_notes: str = Field(default="", max_length=8000)
    backend_notes: str = Field(default="", max_length=8000)
    updated_by: str = Field(min_length=1, max_length=100)
    archived: bool = False


def list_items(database, project_id):
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT data FROM backlog_items WHERE project_id=? ORDER BY updated_at DESC, item_id",
            (project_id,),
        ).fetchall()
    return {"items": [json.loads(row["data"]) for row in rows]}


def save_item(database, project_id, item_id: UUID, request: BacklogWrite):
    item_id = str(item_id)
    fields = request.model_dump(exclude={"revision"})
    with database.transaction() as connection:
        row = connection.execute("SELECT data FROM backlog_items WHERE project_id=? AND item_id=?",
                                 (project_id, item_id)).fetchone()
        previous = json.loads(row["data"]) if row else None
        # The same save can be retried after a lost response without duplicate writes.
        if previous and previous["revision"] == request.revision + 1 and all(previous[k] == v for k, v in fields.items()):
            return previous
        if (previous["revision"] if previous else 0) != request.revision:
            raise APIError(409, "backlog_conflict", "This item changed. Reload it before saving; your draft has been kept.")
        now = datetime.now(timezone.utc).isoformat()
        item = dict(fields, item_id=item_id, revision=request.revision + 1,
                    created_at=previous["created_at"] if previous else now, updated_at=now)
        data = json.dumps(item, ensure_ascii=False)
        if previous:
            connection.execute("UPDATE backlog_items SET data=?, updated_at=? WHERE project_id=? AND item_id=?",
                               (data, now, project_id, item_id))
        else:
            connection.execute("INSERT INTO backlog_items (item_id, project_id, updated_at, data) VALUES (?, ?, ?, ?)",
                               (item_id, project_id, now, data))
    return item
