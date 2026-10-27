"""Exercise the remote first case with explicitly synthetic RGB-D test data."""

import argparse
import hashlib
import io
import json
import queue
import struct
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from websockets.sync.client import connect


def checked(response):
    if response.status_code >= 400:
        try:
            code = response.json().get("code", "request_failed")
        except (ValueError, AttributeError):
            code = "request_failed"
        raise RuntimeError(f"HTTP {response.status_code}: {code}")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-config", type=Path, default=Path(".local/ios-connection.json"))
    parser.add_argument("--operator-config", type=Path, default=Path(".local/web-connection.json"))
    parser.add_argument("--zip", action="store_true", dest="zip_capture", help="Upload one ZIP containing the entire capture")
    args = parser.parse_args()
    device = json.loads(args.device_config.read_text(encoding="utf-8"))
    operator = json.loads(args.operator_config.read_text(encoding="utf-8"))
    device_headers = {"Authorization": "Bearer " + device["device_token"]}
    operator_headers = {"Authorization": "Bearer " + operator["operator_token"]}
    messages = queue.Queue()
    with connect(device["device_websocket_url"], additional_headers=device_headers, open_timeout=20) as socket:
        def read_messages():
            try:
                for raw in socket:
                    message = json.loads(raw)
                    if message.get("type") == "ping":
                        socket.send(json.dumps({"type": "pong"}))
                    else:
                        messages.put(message)
            except Exception:
                messages.put({"type": "connection.closed"})

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()
        with httpx.Client(base_url=device["inspector_base_url"], timeout=30, follow_redirects=False) as api:
            session = checked(api.post("/v1/capture-sessions", headers=operator_headers, json={
                "schema_version": "0.1", "request_id": str(uuid4()), "device_id": device["device_id"],
                "capture_origin": "synthetic", "input_mode": "synthetic_fixture", "prompt": "Synthetic first-case transport test",
            }))
            session_id = session["session_id"]
            deadline = time.monotonic() + 20
            delivered = False
            while time.monotonic() < deadline:
                message = messages.get(timeout=max(0.1, deadline - time.monotonic()))
                if any(event.get("type") == "session.assigned" and event.get("session_id") == session_id for event in message.get("events", [])):
                    delivered = True
                    break
            if not delivered:
                raise RuntimeError("Session was not delivered over WebSocket")
            print("WebSocket assignment received:", session_id)
            base = "/v1/sessions/" + session_id
            checked(api.post(base + "/acknowledgements", headers=device_headers, json={"schema_version": "0.1", "operation_id": str(uuid4())}))
            checked(api.post(base + "/capture-start", headers=device_headers, json={
                "schema_version": "0.1", "operation_id": str(uuid4()),
                "device": {"name": "Synthetic HTTP client", "os_version": "test", "app_version": "0.1"},
            }))
            observation_id, segment_id, completion_id = str(uuid4()), str(uuid4()), str(uuid4())
            # These are transport fixtures, not real device measurements.
            payloads = {"rgb.jpg": b"explicit synthetic RGB transport fixture", "depth.f32": struct.pack("<f", 1.0), "confidence.u8": b"\x02"}

            def descriptor(name):
                data = payloads[name]
                return {"name": name, "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest()}

            timestamp = "2026-10-27T14:00:00Z"
            frame = {"schema_version": "0.2", "session_id": session_id, "observation_id": observation_id, "frame_id": observation_id,
                     "segment_id": segment_id, "instruction_id": None, "capture_origin": "synthetic", "timestamp_s": 1.0,
                     "saved_at": timestamp, "tracking_state": "synthetic", "depth_source": "synthetic_plane", "depth_unit": "m",
                     "rgb_size": [1, 1], "depth_size": [1, 1], "K_rgb": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     "T_world_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                     "rgb": descriptor("rgb.jpg"), "depth": descriptor("depth.f32"), "confidence": descriptor("confidence.u8")}
            payloads["frame.json"] = json.dumps(frame, separators=(",", ":")).encode()
            types = {"rgb.jpg": "image/jpeg", "frame.json": "application/json", "depth.f32": "application/octet-stream", "confidence.u8": "application/octet-stream"}
            refs = [descriptor(name) | {"object_key": session["storage"]["key_prefix"] + f"observations/{observation_id}/{name}"} for name in payloads]
            with httpx.Client(timeout=30, follow_redirects=False) as storage:
                if not args.zip_capture:
                    plan = checked(api.post(base + "/upload-plans", headers=device_headers, json={
                        "schema_version": "0.1", "request_id": str(uuid4()), "kind": "observation_files",
                        "objects": [descriptor(name) | {"observation_id": observation_id, "media_type": types[name]} for name in payloads],
                    }))
                    refs = []
                    for item in plan["items"]:
                        if item["status"] == "upload_required":
                            checked(storage.put(item["upload_url"], headers=item["headers"], content=payloads[item["name"]]))
                        refs.append({key: item[key] for key in ("name", "object_key", "byte_length", "sha256")})
                manifest = {"schema_version": "0.1", "format": "capture_manifest_supabase_v1",
                    **{key: session[key] for key in ("session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode")},
                    "capture_closed_at": timestamp, "observation_count": 1,
                    "segments": [{"segment_id": segment_id, "created_at": timestamp, "units": "m", "coordinate_convention": "arkit_world_camera_v1", "matrix_layout": "rows_column_vectors"}],
                    "observations": [{"observation_id": observation_id, "segment_id": segment_id, "files": refs}]}
                manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
                digest = hashlib.sha256(manifest_bytes).hexdigest()
                upload_bytes, kind, name, media_type = manifest_bytes, "capture_manifest", "capture-manifest.json", "application/json"
                if args.zip_capture:
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("capture-manifest.json", manifest_bytes)
                        for filename, content in payloads.items():
                            archive.writestr(f"observations/{observation_id}/{filename}", content)
                    upload_bytes, kind, name, media_type = buffer.getvalue(), "capture_archive", "capture.zip", "application/zip"
                upload_hash = hashlib.sha256(upload_bytes).hexdigest()
                plan = checked(api.post(base + "/upload-plans", headers=device_headers, json={
                    "schema_version": "0.1", "request_id": str(uuid4()), "kind": kind, "completion_id": completion_id,
                    "objects": [{"observation_id": None, "name": name, "media_type": media_type, "byte_length": len(upload_bytes), "sha256": upload_hash}],
                }))
                item = plan["items"][0]
                if item["status"] == "upload_required":
                    checked(storage.put(item["upload_url"], headers=item["headers"], content=upload_bytes))
            capture_ref = {"format": "supabase_capture_v1", "storage_id": session["storage"]["storage_id"],
                           "manifest_key": item["object_key"], "manifest_byte_length": len(manifest_bytes), "manifest_sha256": digest}
            if args.zip_capture:
                capture_ref = {"format": "supabase_capture_zip_v1", "storage_id": session["storage"]["storage_id"],
                               "archive_key": item["object_key"], "archive_byte_length": len(upload_bytes), "archive_sha256": upload_hash}
            checked(api.post(base + "/complete", headers=device_headers, json={
                "schema_version": "0.1", "completion_id": completion_id, "capture_ref": capture_ref}))
            print("Capture ZIP uploaded and completion accepted for validation." if args.zip_capture else "Capture uploaded and completion accepted for validation.")
            deadline, previous = time.monotonic() + 90, None
            while time.monotonic() < deadline:
                state = checked(api.get(base, headers=device_headers))
                summary = (state["capture_state"], state["processing_state"])
                if summary != previous:
                    print("State:", *summary)
                    previous = summary
                if state["processing_state"] == "completed":
                    print("Geometry result saved; origin:", state["geometry_result"]["analysis_origin"])
                    return
                if state["processing_state"] in ("failed", "cancelled") or state["completion"]["status"] == "rejected":
                    raise RuntimeError("Capture or geometry processing failed; inspect authenticated session status")
                time.sleep(1)
            raise RuntimeError("Timed out waiting for Geometry Service result")


if __name__ == "__main__":
    main()
