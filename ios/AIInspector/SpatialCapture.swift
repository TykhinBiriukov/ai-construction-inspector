import Foundation
import simd

/// Additive, capability-gated protocol. No inference or BIM fitting runs on iPhone.
struct LiveLocalizationCapability: Decodable {
    let `protocol`: String
    let enabled: Bool
    let pose_hz: Double
    let stale_after_ms: Int
    var supported: Bool { enabled && `protocol` == "live_bim_v1" && pose_hz.isFinite && pose_hz > 0 && stale_after_ms > 0 }
}
struct SpatialInstruction: Codable {
    let instruction_id: String
    let source: String
    let control_revision: Int
    let session_id: String
    let segment_id: String?
    let text: String
    let status: String
}
struct SpatialSnapshot: Decodable {
    struct Context: Decodable { let model_version: String; let room_id: String }
    struct Guidance: Decodable {
        let phase: String
        let owner: String
        let control_revision: Int
        let active_instruction_id: String?
        let paused_inspector_instruction_id: String?
    }
    struct Alignment: Decodable {
        let segment_id: String
        let status: String
    }
    let alignments: [Alignment]
    let `protocol`: String
    let session_id: String
    let revision: Int
    let context: Context?
    let guidance: Guidance
    let instructions: [SpatialInstruction]

    func validate(session: String, model: String, room: String, previous: Self?) throws {
        let owners = ["awaiting_camera":"inspector", "localizing":"localization", "relocalizing":"localization",
                      "inspecting":"inspector", "blocked":"localization", "finished":"none"]
        guard `protocol` == "live_bim_v1", sameUUID(session_id, session), revision >= 0,
              owners[guidance.phase] == guidance.owner, guidance.control_revision >= 0,
              context == nil || (context?.model_version == model && context?.room_id == room) else {
            throw CaptureError.invalid("Неверный контекст локализации")
        }
        if let previous {
            guard revision >= previous.revision, guidance.control_revision >= previous.guidance.control_revision else {
                throw CaptureError.invalid("Устаревшее состояние локализации")
            }
        }
        let activeInstructions = instructions.filter { $0.status == "active" }
        guard activeInstructions.count <= 1 else { throw CaptureError.invalid("Несколько активных инструкций") }
        if let id = guidance.active_instruction_id {
            let matches = instructions.filter { sameUUID($0.instruction_id, id) && $0.status == "active" }
            guard matches.count == 1, let item = matches.first, item.source == guidance.owner,
                  item.control_revision == guidance.control_revision, sameUUID(item.session_id, session),
                  !item.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw CaptureError.invalid("Конфликт источников инструкций")
            }
        }
    }
    func instruction(segment: String?) -> SpatialInstruction? {
        guard let id = guidance.active_instruction_id else { return nil }
        if context != nil && guidance.owner == "inspector" && guidance.phase == "inspecting" {
            guard let segment, alignments.contains(where: { sameUUID($0.segment_id, segment) && $0.status == "localized" }) else { return nil }
        }
        return instructions.first { sameUUID($0.instruction_id, id) && $0.status == "active" &&
            ($0.segment_id == nil || sameUUID($0.segment_id, segment)) }
    }
}
struct CameraPose: Encodable, Sendable {
    var type = "camera.pose"
    var `protocol` = "live_bim_v1"
    let session_id: String
    let segment_id: String
    let sequence: Int
    let frame_timestamp_s: Double
    let tracking_state: String
    let tracking_reason: String?
    let T_arkit_camera: [[Float]]
    let intrinsics: [[Float]]
    let image_size: [Int]
}
struct EarlyEvidence: Codable {
    let batchID: String
    let sessionID: String
    let segmentID: String
    let observationID: String
    let instructionID: String?
    let purpose: String
    let files: [StoredFile]
    let uploadRequestIDs: [String: String]
    var batchBody: Data?
    var submitted = false
}

/// JSON uses rows and column vectors. SceneKit takes columns; do not flip any axis here.
func bimPoseMatrix(_ rows: [[Double]]) -> simd_float4x4? {
    guard rows.count == 4, rows.allSatisfy({ $0.count == 4 && $0.allSatisfy({ $0.isFinite }) }),
          Swift.zip(rows[3], [0.0,0,0,1]).allSatisfy({ abs($0-$1) < 0.0001 }) else { return nil }
    let columns = (0..<4).map { c in SIMD4<Float>(Float(rows[0][c]), Float(rows[1][c]), Float(rows[2][c]), Float(rows[3][c])) }
    let result = simd_float4x4(columns: (columns[0],columns[1],columns[2],columns[3]))
    let rotation = simd_float3x3(columns: (SIMD3(columns[0].x,columns[0].y,columns[0].z), SIMD3(columns[1].x,columns[1].y,columns[1].z), SIMD3(columns[2].x,columns[2].y,columns[2].z)))
    guard abs(simd_determinant(rotation)-1) < 0.001 else { return nil }
    return result
}
