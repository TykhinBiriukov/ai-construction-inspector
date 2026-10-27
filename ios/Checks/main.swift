import Foundation
import simd

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
