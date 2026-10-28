# One ZIP for the complete capture

The app can upload one ZIP instead of separate photos/metadata/depth and a separate
manifest object. The existing individual-file workflow remains supported. The
ZIP is stored in the same private Supabase session folder; Geometry Service keeps
using the existing verified manifest/file source API.

## ZIP layout and object keys

Archive the **contents** of the capture directory, without an enclosing folder:

```text
capture-manifest.json
observations/
  22222222-2222-4222-8222-222222222222/
    frame.json
    rgb.jpg
    depth.f32        # only when present
    confidence.u8    # only when present
```

The root JSON uses `schema_version=0.1` and
`format=capture_manifest_supabase_v1`, the same document shape as individual-file
Supabase captures. Its context comes from the server assignment. Its observations
and segments describe all included frames, with exact member byte lengths and
lowercase SHA-256 hashes. ZIP itself is not an observation and is not listed among
the observation files. S3 version IDs are omitted.

**`object_key` in the manifest is the full logical key from the bucket root.** It
does not include the bucket name, a URL, or the archive's `archives/...zip` path.
For example, this is one `observations[].files` entry:

```json
{
  "name": "rgb.jpg",
  "object_key": "projects/demo-building/sessions/11111111-1111-4111-8111-111111111111/observations/22222222-2222-4222-8222-222222222222/rgb.jpg",
  "byte_length": 123456,
  "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

IDs, size and hash above are illustrative; use the real assigned session, local
observation UUID and actual file metadata. This file's **ZIP member path** is:

```text
observations/22222222-2222-4222-8222-222222222222/rgb.jpg
```

The exact rule is:

```text
manifest file.object_key = session.storage.key_prefix + ZIP member path
```

The `key_prefix` already ends with `/`. A relative `object_key` beginning only
with `observations/` is rejected, and an archive that wraps everything in another
top-level directory is rejected. There is no need to upload the individual files
to their logical raw object keys first; the backend reads them from the ZIP and
pins verified copies.

## 1. Request an archive upload plan

After acknowledging and starting capture, generate a persistent `completion_id`.
Build the final manifest and ZIP, then calculate SHA-256 and byte length of the
**exact ZIP bytes**. POST `/v1/sessions/{session_id}/upload-plans` with the device
bearer token:

```json
{
  "schema_version": "0.1",
  "request_id": "33333333-3333-4333-8333-333333333333",
  "kind": "capture_archive",
  "completion_id": "44444444-4444-4444-8444-444444444444",
  "objects": [
    {
      "observation_id": null,
      "name": "capture.zip",
      "media_type": "application/zip",
      "byte_length": 123456,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

Replace illustrative IDs/hash/size. `name=capture.zip` is the protocol filename;
the local file may have any name. The backend returns the actual storage key:

```text
projects/{project_id}/sessions/{session_id}/archives/{completion_id}.zip
```

## 2. Upload the ZIP bytes

For `status=upload_required`, PUT the raw ZIP bytes to the returned `upload_url`
with its exact headers (`Content-Type: application/zip`). Do not add Inspector
Authorization or a Supabase secret key. Do not send JSON/base64 or multipart to
that URL. For `already_uploaded`, continue without sending the bytes again.

After timeout or duplicate-object response, repeat the original upload-plan
request ID/body. It verifies existing bytes and safely returns their status or
fresh permission. A changed ZIP needs a new completion ID and object key. Never
reuse an observation ID for different frame bytes, including when mixing the
individual-file and ZIP transports.

## 3. Complete the capture

POST `/v1/sessions/{session_id}/complete` using the **same completion ID**:

```json
{
  "schema_version": "0.1",
  "completion_id": "44444444-4444-4444-8444-444444444444",
  "capture_ref": {
    "format": "supabase_capture_zip_v1",
    "storage_id": "capture-supabase",
    "archive_key": "OBJECT_KEY_RETURNED_BY_UPLOAD_PLAN",
    "archive_byte_length": 123456,
    "archive_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

The archive hash and size must match the uploaded bytes. No separate manifest
upload or manifest hash is required in this completion message; the manifest is
inside the ZIP. 202 means the request was saved. Poll `GET session` or use device
events to observe validation and the Geometry result.

## Validation and preserved source

The backend checks the outer ZIP hash/size, manifest context and every evidence
member's hash, byte length and ZIP CRC. It reads entries as streams without using
archive paths to write filesystem files. Traversal/absolute paths, duplicates,
symlinks, encrypted archives, unsupported compression and unlisted evidence files
are rejected. Finder `.DS_Store` / `__MACOSX` metadata is ignored after path/type/
size checks. Supported compression is stored or deflated ZIP.

Verified members, original embedded manifest bytes and original ZIP bytes are
pinned in server-owned accepted storage before source acceptance and job creation.
G receives the unchanged manifest/file download API for those member copies. The
source's `manifest_sha256` is the embedded manifest's hash; the ZIP hash is kept
separately in `source.capture_ref.archive`. Thus G does not need a new ZIP parser.

Default limits: 50 MiB ZIP, 2 GiB total unpacked bytes, 40,000 ZIP entries, and the
existing 8 MiB manifest limit. See `INSPECTOR_MAX_ARCHIVE_BYTES`,
`INSPECTOR_MAX_UNPACKED_ARCHIVE_BYTES`, and `INSPECTOR_MAX_ARCHIVE_ENTRIES` in
`.env.example`; Supabase's account/bucket limits also apply.

Run the synthetic complete network test with:

```powershell
.\.venv\Scripts\python.exe -B ../../local-checks/inspector/scripts/simulate_iphone.py --zip
```

Geometry remains explicitly `mock` in the current remote test environment.

Codex verification tests, diagnostic scripts and their temporary data live
outside this checkout in the workspace's local-checks/inspector/ directory.
These optional local files are not part of the distributed backend or its
runtime. Run the local suite with ../../local-checks/inspector/run-tests.ps1.
