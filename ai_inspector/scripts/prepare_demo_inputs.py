"""Build the public demo input library before deploying the backend.

Only the pinned public Geometry archive and the checked-in Progress4D source
directory are accepted. This helper does not read credentials or run analysis.
The destination must not exist; publication is an atomic directory rename.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile


GEOMETRY_URL = (
    "https://github.com/TykhinBiriukov/ai-construction-inspector/releases/"
    "download/evidence-2026-11-03/geometry-real-room-v1.zip"
)
GEOMETRY_PREFIX = "geometry-real-room-v1/"
GEOMETRY_FILES = {
    "reference.ifc": ("room.ifc", 4 * 1024 * 1024),
    "capture/observations/cf50f812-1032-4653-8ec6-098b95c2a165/rgb.jpg":
        ("source-frame.jpg", 16 * 1024 * 1024),
    "capture-manifest.json": ("capture-manifest.json", 4 * 1024 * 1024),
    "result-manifest.json": ("result-manifest.json", 4 * 1024 * 1024),
}
CHUNK_SIZE = 1024 * 1024


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("An input manifest must be a JSON object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def geometry_archive(metadata: dict, target: Path, local_archive: Path | None) -> dict:
    archive = metadata.get("archive", {})
    size, expected_hash = archive.get("bytes"), archive.get("sha256")
    if (metadata.get("version") != "geometry-real-room-v1"
            or archive.get("name") != "geometry-real-room-v1.zip"
            or archive.get("url") != GEOMETRY_URL
            or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= 200 * 1024 * 1024
            or not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
        raise ValueError("The pinned public Geometry archive metadata is invalid")
    if local_archive is not None:
        if not local_archive.is_file() or local_archive.is_symlink():
            raise ValueError("The local Geometry archive must be a regular file")
        source = local_archive.open("rb")
    else:
        request = Request(GEOMETRY_URL, headers={"User-Agent": "AI-Inspector-Demo-Inputs/1.0"})
        source = urlopen(request, timeout=60)
    digest, received = hashlib.sha256(), 0
    with source, target.open("xb") as output:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            received += len(chunk)
            if received > size:
                raise ValueError("The Geometry archive exceeds its pinned byte length")
            digest.update(chunk)
            output.write(chunk)
    if received != size or digest.hexdigest() != expected_hash:
        raise ValueError("The Geometry archive does not match its pinned size and SHA-256")
    return {"bytes": size, "sha256": expected_hash, "url": GEOMETRY_URL}


def prepare_geometry(metadata_path: Path, destination: Path, local_archive: Path | None) -> tuple[dict, dict]:
    metadata = read_json(metadata_path)
    geometry = destination / "geometry"
    geometry.mkdir()
    archive = geometry / "geometry-real-room-v1.zip"
    provenance = {"archive": geometry_archive(metadata, archive, local_archive), "files": {}}
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        sums_info = source.getinfo(GEOMETRY_PREFIX + "SHA256SUMS")
        if sums_info.file_size > 1024 * 1024:
            raise ValueError("The Geometry checksum manifest is too large")
        checksums = {}
        for line in source.read(sums_info).decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if name in checksums or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("The Geometry checksum manifest is invalid")
            checksums[name] = digest
        for member, (name, limit) in GEOMETRY_FILES.items():
            archive_name = GEOMETRY_PREFIX + member
            if names.count(archive_name) != 1:
                raise ValueError("A required Geometry input is missing or duplicated")
            info = source.getinfo(archive_name)
            if (info.is_dir() or not 0 < info.file_size <= limit
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("A required Geometry input is not a bounded regular file")
            target = geometry / name
            with source.open(info) as input_file, target.open("xb") as output:
                shutil.copyfileobj(input_file, output, CHUNK_SIZE)
            digest = file_hash(target)
            if digest != checksums.get(member):
                raise ValueError("An extracted Geometry input differs from its checksum")
            provenance["files"][name] = {"archive_member": member, "sha256": digest,
                                         "bytes": target.stat().st_size}
    if provenance["files"]["room.ifc"]["sha256"] != metadata.get("ifc_sha256"):
        raise ValueError("The Geometry reference IFC differs from its pinned hash")
    entry = {
        "id": "geometry", "title": "Model & site photos",
        "description": "Explore the room BIM reference, original source photo and capture metadata. The full capture and Geometry export are available separately.",
        "prompt": "Inspect the attached BIM reference, original source photo and capture manifest using the existing inspection workflow. These inputs come from a real room device capture. Explain the source coverage and any information or Geometry processing still required. A single photo and manifest do not establish dimensions, alignment or compliance. Do not substitute a retained result or claim a fresh geometric comparison has completed.",
        "notice": "Real room source inputs from the public evidence release. The full ZIP also contains retained Geometry outputs. The archive and export metadata are download-only and are not attached to the analysis request. New measurements require validated capture intake and Geometry computation.",
        "files": [
            {"id": "model", "path": "geometry/room.ifc", "description": "Original BIM reference from the published room capture."},
            {"id": "photo", "path": "geometry/source-frame.jpg", "description": "An original RGB observation from the real room capture."},
            {"id": "capture-manifest", "path": "geometry/capture-manifest.json", "description": "Source manifest for the 99 RGB-D observations."},
            {"id": "export-manifest", "path": "geometry/result-manifest.json", "attach": False, "description": "Retained Geometry export metadata. For inspection and download only; not a fresh result or an analysis input."},
            {"id": "capture-archive", "path": "geometry/geometry-real-room-v1.zip", "attach": False, "description": "Complete published RGB-D capture and retained Geometry outputs, 135 MB. Download only; not automatically processed."},
        ],
    }
    return entry, provenance


def regular_source(root: Path, relative: str) -> Path:
    path = root / relative
    if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root)
            or not 0 < path.stat().st_size <= 32 * 1024 * 1024):
        raise ValueError("A required Progress source is missing or outside the input directory")
    return path


def prepare_progress(source: Path, destination: Path) -> tuple[dict, dict]:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Progress inputs must be a directory")
    files = {"project-materials.ifc": regular_source(source, "project-materials.ifc")}
    with files["project-materials.ifc"].open("rb") as model:
        if not model.read(64).lstrip().startswith(b"ISO-10303-21;"):
            raise ValueError("The Progress BIM reference is not an IFC STEP file")
    weeks, photo_count = [], 0
    for week in range(1, 8):
        directory = f"week-{week:02d}"
        relative = directory + "/capture-manifest.json"
        path = regular_source(source, relative)
        files[relative] = path
        manifest = read_json(path)
        captured_at, photos = manifest.get("captured_at"), manifest.get("photos")
        if manifest.get("synthetic") is not True or not isinstance(captured_at, str):
            raise ValueError("Progress survey provenance and date are required")
        timestamp = datetime.fromisoformat(captured_at)
        if timestamp.utcoffset() is None or not isinstance(photos, list) or not photos:
            raise ValueError("Progress surveys require dated, enumerated photos")
        seen = set()
        for photo in photos:
            if not isinstance(photo, dict):
                raise ValueError("A Progress photo manifest entry is invalid")
            name, expected_hash = photo.get("file"), photo.get("sha256")
            if (not isinstance(name, str) or not re.fullmatch(r"photo-[0-9]{3}\.jpg", name)
                    or name in seen or not isinstance(expected_hash, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
                raise ValueError("A Progress photo identity is invalid")
            seen.add(name)
            relative = directory + "/" + name
            files[relative] = regular_source(source, relative)
            if file_hash(files[relative]) != expected_hash:
                raise ValueError("A Progress photo differs from its capture manifest")
            photo_count += 1
        if "photo-000.jpg" not in seen:
            raise ValueError("Each Progress week requires its original representative photo")
        weeks.append({"week": week, "date": timestamp.date().isoformat(), "captured_at": captured_at,
                      "source": directory + "/photo-000.jpg", "photo_count": len(photos)})
    actual = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    if set(files) != actual or photo_count != 312 or len(files) != 320:
        raise ValueError("The complete Progress input set must contain IFC, seven manifests and 312 source photos only")
    hashes = {name: file_hash(path) for name, path in files.items()}
    progress = destination / "progress"
    progress.mkdir()
    archive = progress / "progress4d-inputs.zip"
    def member_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo("progress4d-inputs/" + name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        return info
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, path in sorted(files.items()):
            digest = hashlib.sha256()
            with path.open("rb") as input_file, output.open(member_info(name), "w") as target:
                for chunk in iter(lambda: input_file.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != hashes[name]:
                raise ValueError("A Progress source changed while building its archive")
        output.writestr(member_info("SHA256SUMS"), "".join(f"{hashes[name]}  {name}\n" for name in sorted(files)), compresslevel=6)
    listing = [{"id": "model", "path": "progress/project-materials.ifc", "description": "Original project BIM reference for the prepared room-002 surveys."}]
    copies = [("project-materials.ifc", "project-materials.ifc")]
    for week in weeks:
        name = f"week-{week['week']:02d}-{week['date']}.jpg"
        copies.append((week["source"], name))
        listing.append({"id": f"week-{week['week']:02d}", "path": "progress/" + name,
                        "description": f"Week {week['week']} · {week['captured_at']}. Original synthetic source photograph, one of {week['photo_count']} views. Not real-site evidence or an analysis result."})
    for original, name in copies:
        target = progress / name
        shutil.copyfile(files[original], target)
        if file_hash(target) != hashes[original]:
            raise ValueError("A copied Progress source differs from its original")
    listing.append({"id": "full-inputs", "path": "progress/progress4d-inputs.zip", "attach": False,
                    "description": "Complete input-only archive: IFC, seven capture manifests and 312 synthetic source photos. Download only; no replay findings or verdicts."})
    dates = "; ".join(f"week {week['week']} = {week['date']}" for week in weeks)
    entry = {
        "id": "progress", "title": "Construction progress",
        "description": "Explore the project BIM and seven weeks of original synthetic survey photographs. One photo per week is attached; the full source set is available separately.",
        "prompt": f"Compare the attached synthetic weekly construction photographs in chronological order, using the project BIM reference when supported by the configured tools. Dates: {dates}. Describe source-supported visible changes and gaps in coverage. These photographs are a representative subset, not a complete survey. Do not invent measured completion percentages, hidden work, dimensions or compliance. Use the existing inspection workflow; explain any missing processing capability. Do not substitute a saved timeline or expected answer for new analysis.",
        "notice": "Prepared synthetic surveys from the published Progress4D input set. Eight attachable inputs: project IFC and one original photo from each of seven weeks. The complete input-only ZIP is download-only. No replay outputs or evaluator answers are supplied.",
        "files": listing,
    }
    provenance = {"source_files": len(files), "verified_photo_hashes": photo_count, "weeks": weeks,
                  "archive": {"sha256": file_hash(archive), "bytes": archive.stat().st_size},
                  "source_sha256": hashes, "replay_included": False}
    return entry, provenance


def prepare(progress_inputs: Path, geometry_manifest: Path, destination: Path,
            local_geometry_archive: Path | None = None) -> dict:
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("The demo input destination already exists; use a new empty destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    with tempfile.TemporaryDirectory(prefix=".inspector-demo-inputs-", dir=parent) as temporary:
        staging = Path(temporary) / "library"
        staging.mkdir()
        geometry, geometry_provenance = prepare_geometry(geometry_manifest, staging, local_geometry_archive)
        progress, progress_provenance = prepare_progress(progress_inputs, staging)
        write_json(staging / "scenario-inputs.json", {"scenarios": [geometry, progress]})
        provenance = {"geometry": geometry_provenance, "progress": progress_provenance}
        write_json(staging / "provenance.json", provenance)
        if destination.exists() or destination.is_symlink() or staging.resolve().parent.parent != parent:
            raise ValueError("The demo input destination changed during preparation")
        staging.rename(destination)
    return {"geometry_files": len(geometry["files"]), "progress_files": len(progress["files"]),
            "progress_verified_photos": progress_provenance["verified_photo_hashes"],
            "progress_archive_sha256": progress_provenance["archive"]["sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress-inputs", type=Path, required=True)
    parser.add_argument("--geometry-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--geometry-archive", type=Path,
                        help="Use the same pinned archive from a local file instead of downloading it")
    args = parser.parse_args()
    summary = prepare(args.progress_inputs, args.geometry_manifest, args.destination, args.geometry_archive)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
