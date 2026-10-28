import Foundation

struct InspectorAssignment: Codable, Equatable, Identifiable, Sendable {
    var id: String { session_id }
    let session_id: String
    let inspection_id: String
    let project_id: String
    let room_id: String
    let model_version: String
    let model_is_synthetic: Bool
    let policy_version: String
    let device_id: String
    let capture_origin: String
    let input_mode: String
    var capture_state: String
    let revision: Int
    struct Storage: Codable, Equatable, Sendable {
        let storage_id: String
        let key_prefix: String
        let provider: String?
        let project_url: String?
        let bucket: String?
    }
    var storage: Storage? = nil
    var canCapture: Bool { ["assigned", "capturing"].contains(capture_state) }
    func validate(device: String) throws {
        guard UUID(uuidString: session_id) != nil, device_id == device,
              !inspection_id.isEmpty, !project_id.isEmpty, !room_id.isEmpty,
              !model_version.isEmpty, !policy_version.isEmpty, revision > 0,
              ["device", "synthetic"].contains(capture_origin),
              (capture_origin == "device" && input_mode == "live_capture") ||
                (capture_origin == "synthetic" && input_mode == "synthetic_fixture"),
              ["assigned", "capturing", "submitted", "sealed", "cancelled"].contains(capture_state)
        else { throw CaptureError.invalid("Inspector вернул неподдерживаемое назначение") }
    }
    func sameContext(as other: Self) -> Bool {
        session_id == other.session_id && inspection_id == other.inspection_id && project_id == other.project_id &&
        room_id == other.room_id && model_version == other.model_version && policy_version == other.policy_version &&
        device_id == other.device_id && capture_origin == other.capture_origin && input_mode == other.input_mode &&
        model_is_synthetic == other.model_is_synthetic && storage == other.storage
    }
}

struct InspectorInstruction: Codable, Sendable {
    let instruction_id: String
    let revision: Int
    let session_id: String
    let text: String
    let state: String
}
struct InspectorSnapshot: Decodable {
    let session: InspectorAssignment
    let instructions: [InspectorInstruction]
}
struct AssignmentPage: Decodable { let assignments: [InspectorAssignment]; let next_cursor: String? }

struct CaptureFileReference: Codable, Sendable {
    let name: String
    var object_key: String
    let byte_length: Int
    let sha256: String
}
struct CaptureInventoryItem: Codable, Sendable {
    let observation_id: String
    let segment_id: String
    var files: [CaptureFileReference]
}
struct FinalCaptureManifest: Codable, Sendable {
    struct Segment: Codable, Sendable {
        let segment_id: String
        let created_at: Date
        var units = "m"
        var coordinate_convention = "arkit_world_camera_v1"
        var matrix_layout = "rows_column_vectors"
    }
    var schema_version = "0.1"
    var format = "capture_manifest_supabase_v1"
    let session_id: String
    let inspection_id: String
    let project_id: String
    let room_id: String
    let model_version: String
    let capture_origin: String
    let input_mode: String
    let capture_closed_at: Date
    let observation_count: Int
    let segments: [Segment]
    var observations: [CaptureInventoryItem]
}
struct CaptureCompletion: Codable, Equatable, Sendable {
    struct Reference: Codable, Equatable, Sendable {
        var format = "supabase_capture_v1"
        let storage_id: String
        var manifest_key: String? = nil
        var manifest_byte_length: Int? = nil
        var manifest_sha256: String? = nil
        var archive_key: String? = nil
        var archive_byte_length: Int? = nil
        var archive_sha256: String? = nil
    }
    var schema_version = "0.1"
    let completion_id: String
    let capture_ref: Reference
}
struct CompletionResponse: Decodable {
    struct Operation: Decodable { let completion_id: String; let status: String }
    let session_id: String
    let completion: Operation
}
struct PendingCompletion: Codable, Sendable {
    let completion_id: String
    var manifest: FinalCaptureManifest
    var requestIDs: [String: String] = [:]
    var manifestBytes: Data?
    var request: CaptureCompletion?
    var transportFormat: String?
    var archiveInfo: StoredFile?
}
struct CaptureJournal: Codable {
    var active: InspectorAssignment?
    var startBodies: [String: Data] = [:]
    var ackIDs: [String: String] = [:]
    var acknowledged: [String] = []
    var startedSessionIDs: [String] = []
    var completionRejected = false
    var pending: PendingCompletion?
    var closedSessionIDs: [String] = []
    var eventCursor: String?
}

func freezeCapture(assignment: InspectorAssignment, store: ObservationStore) throws -> PendingCompletion {
    let folder = store.folder(assignment.id)
    let local = try ObservationStore.decoder.decode(SessionManifest.self, from: Data(contentsOf: folder.appendingPathComponent("manifest.json")))
    guard local.session_id == assignment.id, local.room_id == assignment.room_id, local.model_version == assignment.model_version,
          local.model_is_synthetic == assignment.model_is_synthetic, local.capture_origin == assignment.capture_origin else { throw CaptureError.invalid("Набор не соответствует назначению") }
    let frames = try store.observations(assignment.id)
    guard !frames.isEmpty else { throw CaptureError.invalid("Сначала сохраните хотя бы одно наблюдение") }
    let observations = try frames.map { frame -> CaptureInventoryItem in
        guard frame.session_id == assignment.id, UUID(uuidString: frame.id) != nil, local.segment_ids.contains(frame.segment_id) else { throw CaptureError.invalid("Неверная принадлежность кадра") }
        let metadata = try Data(contentsOf: folder.appendingPathComponent("observations/" + frame.id + "/frame.json"))
        let files = [frame.rgb] + (frame.depth.map { [$0] } ?? []) + (frame.confidence.map { [$0] } ?? []) + [StoredFile(name: "frame.json", data: metadata)]
        return CaptureInventoryItem(observation_id: frame.id, segment_id: frame.segment_id, files: files.map {
            CaptureFileReference(name: $0.name, object_key: "", byte_length: $0.byte_length, sha256: $0.sha256)
        })
    }
    let segments = Set(frames.map(\.segment_id)).sorted().map { id in
        FinalCaptureManifest.Segment(segment_id: id, created_at: frames.filter { $0.segment_id == id }.map(\.saved_at).min() ?? local.created_at)
    }
    return PendingCompletion(completion_id: UUID().uuidString.lowercased(), manifest: FinalCaptureManifest(session_id: assignment.id,
        inspection_id: assignment.inspection_id, project_id: assignment.project_id, room_id: assignment.room_id,
        model_version: assignment.model_version, capture_origin: assignment.capture_origin, input_mode: assignment.input_mode,
        capture_closed_at: Date(), observation_count: observations.count, segments: segments, observations: observations), transportFormat: "zip")
}

/// UUID text is case-insensitive; reject malformed IDs even when strings match.
func sameUUID(_ lhs: String?, _ rhs: String?) -> Bool {
    guard let lhs, let rhs, let left = UUID(uuidString: lhs), let right = UUID(uuidString: rhs) else { return false }
    return left == right
}
