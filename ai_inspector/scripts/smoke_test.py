"""Exercise the HTTP handoff against a running local backend, acting as a device."""

import argparse
from pathlib import Path
from uuid import uuid4

import httpx

from ai_inspector.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bim", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--room-id", default="room-204")
    args = parser.parse_args()
    settings = Settings.from_env()
    operator = {"Authorization": f"Bearer {settings.operator_token}"}
    device = {"Authorization": f"Bearer {settings.device_token}"}
    device_id = settings.device_id

    with httpx.Client(base_url=args.base_url, timeout=30, follow_redirects=False) as client:
        with args.bim.open("rb") as source:
            response = client.post(
                "/v1/inspections", headers=operator,
                data={
                    "schema_version": "0.1", "request_id": str(uuid4()),
                    "project_id": settings.project_id,
                    "room_id": args.room_id, "prompt": args.prompt,
                },
                files={"bim_model": (args.bim.name, source, "application/octet-stream")},
            )
        response.raise_for_status()
        inspection = response.json()
        response = client.post(
            f"/v1/inspections/{inspection['inspection_id']}/capture-sessions", headers=operator,
            json={
                "schema_version": "0.1", "request_id": str(uuid4()), "device_id": device_id,
                "room_id": args.room_id, "capture_origin": "device", "input_mode": "live_capture",
                "parent_session_id": None,
            },
        )
        response.raise_for_status()
        session_id = response.json()["session_id"]
        cursor = None
        while True:
            params = {"after": cursor} if cursor else {}
            response = client.get(f"/v1/devices/{device_id}/capture-assignments", params=params, headers=device)
            response.raise_for_status()
            page = response.json()
            if any(item["session_id"] == session_id for item in page["assignments"]):
                break
            cursor = page["next_cursor"]
            if cursor is None:
                raise RuntimeError("Created session was not delivered in device assignments")
        acknowledgement = {"schema_version": "0.1", "operation_id": str(uuid4())}
        response = client.post(
            f"/v1/sessions/{session_id}/acknowledgements", json=acknowledgement, headers=device,
        )
        response.raise_for_status()
        replay = client.post(
            f"/v1/sessions/{session_id}/acknowledgements", json=acknowledgement, headers=device,
        )
        replay.raise_for_status()
        if replay.status_code != 200 or replay.json() != response.json():
            raise RuntimeError("Acknowledgement retry did not return the original receipt")
        current = client.get(f"/v1/sessions/{session_id}", headers=operator)
        current.raise_for_status()
        if current.json()["capture_state"] != "assigned" or current.json()["device_acknowledged_at"] is None:
            raise RuntimeError("Receipt acknowledgement changed capture state or was not persisted")
        print(f"Inspection: {inspection['inspection_id']}")
        print(f"Session: {session_id}")
        print("Device received the session and acknowledged it. Capture state remains assigned.")


if __name__ == "__main__":
    main()
