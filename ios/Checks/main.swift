import Foundation
import simd
import ImageIO
import CoreGraphics

func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw CaptureError.invalid(message) }
}

let root = FileManager.default.temporaryDirectory.appendingPathComponent("inspector-check-\(UUID().uuidString)")
defer { try? FileManager.default.removeItem(at: root) }
let store = ObservationStore(root: root)
var translation = matrix_identity_float4x4
translation.columns.3 = SIMD4<Float>(4, 5, 6, 1)
let rows = matrixRows(translation)
try require(rows[0][3] == 4 && rows[1][3] == 5 && rows[2][3] == 6 && rows[3] == [0,0,0,1], "SIMD column-major conversion transposed the pose")
let k = simd_float3x3(columns: (SIMD3(500,0,0), SIMD3(0,501,0), SIMD3(320,240,1)))
try require(matrixRows(k) == [[500,0,320],[0,501,240],[0,0,1]], "Intrinsics transposed")
var manifest = try store.open(origin: "synthetic", device: "test", os: "test")
manifest.segment_ids.append("segment-a")
try store.save(manifest)
var depth = Data()
for value: Float in [1, 2, 0, .nan] {
    var bits = value.bitPattern.littleEndian
    withUnsafeBytes(of: &bits) { depth.append(contentsOf: $0) }
}
let payload = CapturePayload(rgb: Data([0xff, 0xd8, 0xff, 0xd9]), depth: depth, confidence: nil,
    rgbSize: [4,4], depthSize: [2,2], intrinsics: [[2,0,2],[0,2,2],[0,0,1]],
    pose: [[1,0,0,4],[0,1,0,5],[0,0,1,6],[0,0,0,1]], timestamp: 2, origin: "synthetic", tracking: "synthetic")
let frame = try store.record(payload, session: manifest, segment: "segment-a", instruction: "instruction-1")
let reopened = try store.open(origin: "synthetic", device: "test", os: "test")
try require(reopened.session_id == manifest.session_id, "Session did not survive reopening")
let inventory = try store.observations(manifest.session_id)
try require(inventory.count == 1 && inventory[0].id == frame.id, "Inventory did not survive reopening")
try require(inventory[0].T_world_camera[0][3] == 4, "Matrix translation changed")
try require(inventory[0].instruction_id == "instruction-1", "Instruction link lost")
let folder = store.folder(manifest.session_id).appendingPathComponent("observations/\(frame.id)")
let savedDepth = try Data(contentsOf: folder.appendingPathComponent("depth.f32"))
try require(savedDepth == depth, "Raw depth changed, including invalid values")
let rawJSON = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("frame.json"))) as! [String: Any]
try require(rawJSON["T_world_camera"] != nil && rawJSON["K_rgb"] != nil, "Contract matrix keys changed")
try require(rawJSON["confidence"] is NSNull, "Missing confidence must be explicit null")
try require(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("confidence.u8").path), "Missing confidence fabricated")
let pending = store.folder(manifest.session_id).appendingPathComponent("observations/.pending-test")
try FileManager.default.createDirectory(at: pending, withIntermediateDirectories: true)
let withPending = try store.observations(manifest.session_id)
try require(withPending.count == 1, "Partial observation leaked into inventory")
do {
    _ = try store.record(payload, session: manifest, segment: "wrong-segment", instruction: nil)
    fatalError("Invalid segment accepted")
} catch is CaptureError {}
let invalid = CapturePayload(rgb: payload.rgb, depth: Data([0]), confidence: nil, rgbSize: [4,4], depthSize: [2,2],
                             intrinsics: payload.intrinsics, pose: payload.pose, timestamp: 3, origin: "synthetic", tracking: "synthetic")
do {
    _ = try store.record(invalid, session: manifest, segment: "segment-a", instruction: nil)
    fatalError("Truncated depth accepted")
} catch is CaptureError {}
try Data([1]).write(to: folder.appendingPathComponent("depth.f32"))
do {
    _ = try store.observations(manifest.session_id)
    fatalError("Corruption not detected")
} catch is CaptureError {}
let deviceSession = try store.open(origin: "device", device: "test", os: "test")
try require(deviceSession.session_id != manifest.session_id, "Synthetic and real data mixed")
print("PASS: session reopen, inventory, instruction link, row matrices, depth preservation, missing confidence, partial writes, invalid segment, truncated depth, corruption, origin isolation")
var rgbSession = deviceSession
rgbSession.segment_ids.append("rgb-segment")
try store.save(rgbSession)
let rgbOnly = CapturePayload(rgb: payload.rgb, depth: nil, confidence: nil,
    rgbSize: payload.rgbSize, depthSize: nil, intrinsics: payload.intrinsics,
    pose: payload.pose, timestamp: 4, origin: "device", tracking: "normal")
let rgbFrame = try store.record(rgbOnly, session: rgbSession, segment: "rgb-segment", instruction: nil)
let rgbFolder = store.folder(rgbSession.session_id).appendingPathComponent("observations/\(rgbFrame.id)")
let rgbJSON = try JSONSerialization.jsonObject(with: Data(contentsOf: rgbFolder.appendingPathComponent("frame.json"))) as! [String: Any]
for key in ["depth", "depth_size", "depth_unit", "depth_to_rgb_mapping", "confidence"] {
    try require(rgbJSON[key] is NSNull, "RGB-only field not null: \(key)")
}
try require(rgbFrame.depth_source == "none", "RGB-only depth source fabricated")
try require(!FileManager.default.fileExists(atPath: rgbFolder.appendingPathComponent("depth.f32").path), "RGB-only depth file fabricated")
let rgbReopened = try store.observations(rgbSession.session_id)
try require(rgbReopened.count == 1 && rgbReopened[0].depth == nil, "RGB-only reopen failed")
let orphanConfidence = CapturePayload(rgb: payload.rgb, depth: nil, confidence: Data([2]),
    rgbSize: payload.rgbSize, depthSize: nil, intrinsics: payload.intrinsics,
    pose: payload.pose, timestamp: 5, origin: "device", tracking: "normal")
do {
    _ = try store.record(orphanConfidence, session: rgbSession, segment: "rgb-segment", instruction: nil)
    fatalError("Confidence without depth accepted")
} catch is CaptureError {}
// Old RGB-D observations must remain decodable after the nullable schema change.
var legacyJSON = rawJSON
legacyJSON["schema_version"] = "0.1"
let legacy = try ObservationStore.decoder.decode(FrameMetadata.self, from: JSONSerialization.data(withJSONObject: legacyJSON))
try require(legacy.depth != nil && legacy.depth_size == [2,2], "Legacy RGB-D decode failed")
print("PASS: RGB-only null metadata, no fabricated files, reopen, orphan confidence rejection, legacy RGB-D decode")

let assignedID = UUID().uuidString
var assigned = try store.openAssigned(id: assignedID, room: "room-test", model: "bim-7", modelIsSynthetic: false, origin: "device", device: "iPhone", os: "17")
try require(assigned.session_id == assignedID, "Server session ID replaced")
let assignment = InspectorAssignment(session_id: assignedID, inspection_id: "inspection-1", project_id: "project-1", room_id: "room-test", model_version: "bim-7", model_is_synthetic: false, policy_version: "p1", device_id: "iphone-1", capture_origin: "device", input_mode: "live_capture", capture_state: "assigned", revision: 1)
try assignment.validate(device: "iphone-1")
do { try assignment.validate(device: "other"); fatalError("Wrong device accepted") } catch is CaptureError {}
do {
    _ = try store.openAssigned(id: assignedID, room: "different", model: "bim-7", modelIsSynthetic: false, origin: "device", device: "iPhone", os: "17")
    fatalError("Assignment context overwritten")
} catch is CaptureError {}
let userID = UUID().uuidString
 do { _ = try freezeCapture(assignment: assignment, store: store); fatalError("Empty capture completed") } catch is CaptureError {}
assigned.segment_ids.append(UUID().uuidString); try store.save(assigned)
_ = try store.record(rgbOnly, session: assigned, segment: assigned.segment_ids[0], instruction: "instruction-1")
let completionPending = try freezeCapture(assignment: assignment, store: store)
let finalInventory = completionPending.manifest
try require(finalInventory.observations.count == 1 && finalInventory.observations[0].files.count == 2, "RGB-only final inventory includes depth")
try require(finalInventory.observations[0].files.allSatisfy { $0.object_key.isEmpty }, "Invented server object keys")
var journal = CaptureJournal(); journal.active = assignment; journal.pending = completionPending
let restored = try ObservationStore.decoder.decode(CaptureJournal.self, from: ObservationStore.encoder.encode(journal))
try require(restored.pending?.completion_id == completionPending.completion_id, "Restart changed completion ID")
let initialBytes = try ObservationStore.encoder.encode(completionPending.manifest)
let restoredBytes = try ObservationStore.encoder.encode(restored.pending!.manifest)
try require(initialBytes == restoredBytes, "Restart changed manifest bytes")
let wire = try JSONSerialization.jsonObject(with: ObservationStore.encoder.encode(finalInventory)) as! [String: Any]
try require(wire["format"] as? String == "capture_manifest_supabase_v1" && wire["observation_count"] as? Int == 1, "Wrong signed manifest format")
let completion = CaptureCompletion(completion_id: completionPending.completion_id, capture_ref: .init(storage_id: "capture-supabase", manifest_key: "returned-key", manifest_byte_length: 20, manifest_sha256: String(repeating: "a", count: 64)))
let completeWire = try JSONSerialization.jsonObject(with: ObservationStore.encoder.encode(completion)) as! [String: Any]
try require(Set(completeWire.keys) == ["schema_version", "completion_id", "capture_ref"], "Extra completion fields rejected by backend")
print("PASS: assigned IDs, device/context validation, empty rejection, RGB-only final inventory, server-owned keys, durable retry, signed manifest/completion wire format")

let triangle = GeometryMesh(vertices: [SIMD3(0,0,0), SIMD3(1,0,0), SIMD3(0,1,0)], triangles: [SIMD3(0,1,2)])
let ply = try triangle.ply()
try require(ply.suffix(13).first == 3, "PLY face is not triangle")
var invalidDepth = Data()
for value: Float in [1.25, 0, -1, .nan, .infinity] { invalidDepth.appendLE(value.bitPattern) }
let clean = try GeometryExport.sanitizedDepth(invalidDepth)
try require(clean.prefix(4) == invalidDepth.prefix(4) && clean.dropFirst(4).allSatisfy { $0 == 0 }, "Geometry depth sanitization wrong")
try require(CaptureZIP.crc32(Data("123456789".utf8)) == 0xcbf43926, "ZIP CRC32 mismatch")
let fileA = root.appendingPathComponent("capture.json"); try Data("{}".utf8).write(to: fileA)
let fileB = root.appendingPathComponent("mesh.ply"); try ply.write(to: fileB)
try CaptureZIP.write(files: [("capture.json", fileA), ("mesh.ply", fileB)], to: URL(fileURLWithPath: "/tmp/inspector-zip-check.zip"))
print("PASS: binary PLY, invalid depth sanitization, ZIP checksum; archive at /tmp/inspector-zip-check.zip")

// A generated JPEG fixture tests archive layout; this is not a real ARKit capture.
let pixels = Data([255,0,0,255, 0,255,0,255, 0,0,255,255, 255,255,255,255])
let provider = CGDataProvider(data: pixels as CFData)!
let cgImage = CGImage(width: 2, height: 2, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: 8,
    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
    provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
let jpegData = NSMutableData()
let destination = CGImageDestinationCreateWithData(jpegData, "public.jpeg" as CFString, 1, nil)!
CGImageDestinationAddImage(destination, cgImage, nil)
try require(CGImageDestinationFinalize(destination), "JPEG fixture failed")
var exportSession = try store.openAssigned(id: UUID().uuidString, room: "fixture-room", model: "fixture-model", modelIsSynthetic: true, origin: "device", device: "fixture", os: "fixture")
let exportSegment = UUID().uuidString; exportSession.segment_ids.append(exportSegment); try store.save(exportSession)
var fourDepths = Data(); for value: Float in [2, .nan, -1, .infinity] { fourDepths.appendLE(value.bitPattern) }
let exportPayload = CapturePayload(rgb: jpegData as Data, depth: fourDepths, confidence: Data([2,1,0,2]), rgbSize: [2,2], depthSize: [2,2], intrinsics: [[2,0,1],[0,2,1],[0,0,1]], pose: rows, timestamp: 123.5, origin: "device", tracking: "normal")
_ = try store.record(exportPayload, session: exportSession, segment: exportSegment, instruction: nil)
let zip = try GeometryExport.export(sessionID: exportSession.session_id, store: store, mesh: ply, meshSegment: exportSegment)
try Data(contentsOf: zip).write(to: URL(fileURLWithPath: "/tmp/inspector-geometry-fixture.zip"), options: .atomic)
exportSession.segment_ids.append(UUID().uuidString); try store.save(exportSession)
_ = try store.record(exportPayload, session: exportSession, segment: exportSession.segment_ids.last!, instruction: nil)
do { _ = try GeometryExport.export(sessionID: exportSession.session_id, store: store, mesh: ply, meshSegment: exportSegment); fatalError("Mixed coordinate segments exported") } catch is CaptureError {}
print("PASS: full Geometry ZIP fixture, real JPEG codec dimensions, mixed-coordinate rejection")

let archivePrefix = "projects/project-1/sessions/" + assignedID + "/"
let archiveResult = try InspectorArchive.build(pending: completionPending, store: store, keyPrefix: archivePrefix)
try require(archiveResult.manifest.observations[0].files.allSatisfy { $0.object_key.hasPrefix(archivePrefix + "observations/") }, "ZIP manifest missing assigned storage prefix")
let archiveURL = InspectorArchive.url(sessionID: assignedID, completionID: completionPending.completion_id, store: store)
let archiveBytes = try Data(contentsOf: archiveURL)
try require(archiveResult.info.sha256 == StoredFile(name: "", data: archiveBytes).sha256 && archiveResult.info.byte_length == archiveBytes.count, "ZIP digest/length mismatch")
try archiveBytes.write(to: URL(fileURLWithPath: "/tmp/inspector-transport-fixture.zip"), options: .atomic)
let zipCompletion = CaptureCompletion(completion_id: completionPending.completion_id, capture_ref: .init(format: "supabase_capture_zip_v1", storage_id: "capture-supabase", archive_key: "returned-key", archive_byte_length: archiveBytes.count, archive_sha256: archiveResult.info.sha256))
let zipWire = try JSONSerialization.jsonObject(with: ObservationStore.encoder.encode(zipCompletion)) as! [String: Any]
let zipRef = zipWire["capture_ref"] as! [String: Any]
try require(Set(zipRef.keys) == ["format", "storage_id", "archive_key", "archive_byte_length", "archive_sha256"], "Unexpected fields in ZIP completion")
var zipPending = completionPending
zipPending.archiveInfo = archiveResult.info; zipPending.manifestBytes = archiveResult.manifestBytes; zipPending.request = zipCompletion
let restoredZIP = try ObservationStore.decoder.decode(PendingCompletion.self, from: ObservationStore.encoder.encode(zipPending))
try require(restoredZIP.request == zipCompletion && restoredZIP.archiveInfo?.sha256 == archiveResult.info.sha256, "ZIP retry changed after restore")
let secondArchive = try InspectorArchive.build(pending: completionPending, store: store, keyPrefix: archivePrefix)
try require(secondArchive.info.sha256 == archiveResult.info.sha256, "Identical inventory produced different ZIP")
print("PASS: Inspector ZIP layout, hashes, RGB-only files, exact completion schema, durable retry, deterministic rebuild")

let sentID = "4D9F9E53-AFD2-4C27-B338-CE130997ABBD"
let receiptID = "4d9f9e53-afd2-4c27-b338-ce130997abbd"
try require(sameUUID(sentID, receiptID), "Server UUID normalization rejected")
try require(!sameUUID(sentID, UUID().uuidString), "Different UUID accepted")
try require(!sameUUID(nil, nil) && !sameUUID("invalid", "invalid"), "Missing/malformed receipt accepted")
print("PASS: real receipt UUID casing regression, mismatched and missing IDs rejected")
