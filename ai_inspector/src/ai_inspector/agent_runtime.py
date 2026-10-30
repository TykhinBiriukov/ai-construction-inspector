"""Shared durable harness engine, instantiated with each agent's own state and tools."""

import copy
import time
from uuid import uuid4

from .errors import APIError
from .model_client import validated_message
from .service import utc_now
from .storage import canonical_json


class AgentHarness:
    def __init__(self, state, *, model, max_steps, checkpoint, prepare_model, plan_tool, execute_tool):
        self.state = state
        self.model = model
        self.max_steps = max_steps
        self.checkpoint = checkpoint
        self.prepare_model = prepare_model
        self.plan_tool = plan_tool
        self.execute_tool = execute_tool

    def _start_activity(self, kind, name, operation_id):
        events = self.state.setdefault("activity_events", [])
        for old in events:
            if old["status"] == "running":
                old.update(status="interrupted", finished_at=utc_now(), duration_ms=None,
                           summary="Previous attempt stopped reporting; duration unknown.")
        previous = [event for event in events if event["operation_id"] == operation_id]
        event = dict(event_id=str(uuid4()), kind=kind, name=name, operation_id=operation_id,
                     attempt=len(previous) + 1, retry_of=previous[-1]["event_id"] if previous else None,
                     status="running", started_at=utc_now(), finished_at=None, duration_ms=None,
                     origin="live", summary=None)
        events.append(event)
        self.state["activity_events"] = events[-64:]
        return event, time.monotonic()

    @staticmethod
    def _finish_activity(event, started, status, summary):
        event.update(status=status, finished_at=utc_now(),
                     duration_ms=max(0, round((time.monotonic() - started) * 1000)), summary=summary)

    def advance(self):
        state = self.state
        activity = started = None
        try:
            state.update(status="running", error=None, updated_at=utc_now())
            self.checkpoint()
            while True:
                if state.get("pending_tool"):
                    plan = state["pending_tool"]
                    activity, started = self._start_activity("tool", plan["name"], plan["operation_id"])
                    self.checkpoint()  # Fence ownership before executing a persisted command.
                    try:
                        result, session_id = self.execute_tool(plan)
                    except APIError as error:
                        if error.body["retryable"]:
                            raise
                        result, session_id = {"error": error.body}, None
                    self._finish_activity(activity, started, "failed" if result.get("error") else "completed",
                                          result["error"]["message"] if result.get("error") else "Tool completed")
                    state["messages"].append({"role": "tool", "tool_call_id": plan["tool_call_id"], "content": canonical_json(result)})
                    state["tool_results"].append({"name": plan["name"], "operation_id": plan["operation_id"],
                                                   "event_id": activity["event_id"], "result": result})
                    state["pending_tool"] = None
                    if session_id:
                        state["session_id"] = session_id
                    self.checkpoint(link_session=session_id)
                    continue
                if state["steps"] >= self.max_steps:
                    raise APIError(502, "agent_step_limit", "The agent reached its model turn limit")
                if not state.get("pending_model"):
                    messages, tools = self.prepare_model()
                    state["pending_model"] = copy.deepcopy({"messages": messages, "tools": tools})
                request = state["pending_model"]
                activity, started = self._start_activity("model", "model_request", f"model-{state['steps']}")
                self.checkpoint()  # Freeze the exact model request before network IO.
                message = validated_message(self.model.complete(request["messages"], request["tools"]))
                self._finish_activity(activity, started, "completed", "Tool call selected" if message.get("tool_calls") else "Response prepared")
                state["pending_model"] = None
                state["steps"] += 1
                if message.get("tool_calls"):
                    plan = self.plan_tool(message["tool_calls"][0])
                    state["messages"].append(message)
                    state["pending_tool"] = plan
                    self.checkpoint()
                else:
                    state["messages"].append(message)
                    state.update(status="completed", reply=message["content"], updated_at=utc_now())
                    self.checkpoint()
                    return
        except APIError as error:
            if error.body["code"] == "agent_lease_lost":
                raise
            if activity and activity["status"] == "running":
                self._finish_activity(activity, started, "waiting" if error.body["retryable"] else "failed", error.body["message"])
            state.update(status="retryable" if error.body["retryable"] else "failed", error=error.body, updated_at=utc_now())
            self.checkpoint()
            raise
