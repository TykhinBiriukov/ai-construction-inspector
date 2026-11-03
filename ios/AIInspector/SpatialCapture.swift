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
            throw CaptureError.invalid("Invalid localization context")
        }
        if let previous {
            guard revision >= previous.revision, guidance.control_revision >= previous.guidance.control_revision else {
                throw CaptureError.invalid("Outdated localization state")
            }
        }
        let activeInstructions = instructions.filter { $0.status == "active" }
        guard activeInstructions.count <= 1 else { throw CaptureError.invalid("Multiple active instructions") }
        if let id = guidance.active_instruction_id {
            let matches = instructions.filter { sameUUID($0.instruction_id, id) && $0.status == "active" }
            guard matches.count == 1, let item = matches.first, item.source == guidance.owner,
                  item.control_revision == guidance.control_revision, sameUUID(item.session_id, session),
                  !item.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw CaptureError.invalid("Conflicting instruction sources")
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

/// Display-only translations for fixed messages from older Localization Service versions.
/// Never rewrite server instructions, persisted context, IFC names or operator-authored content.
enum EnglishInterface {
    static let serviceMessages: [String: String] = [
        "BIM не подготовлена для локализации.": "The BIM model is not ready for localization.",
        "Наведите камеру на входную дверь. Покажите проём, пол и соседний угол.": "Point the camera at the entry door. Include the doorway, floor and adjacent corner.",
        "Алгоритм привязки ещё не подключён. Наблюдения сохранены для теста.": "The alignment algorithm is not connected yet. Observations have been saved for testing.",
        "Не удалось подтвердить привязку.": "Could not confirm alignment.",
        "Положение потеряно. Снова покажите дверь и соседний угол.": "Tracking lost. Show the door and adjacent corner again.",
        "Слишком много неверных кодов. Подождите минуту.": "Too many incorrect codes. Wait a minute before trying again.",
        "Неверный код подключения": "Incorrect connection code",
        "Не удалось обработать данные; оригиналы сохранены, повторите запрос.": "Could not process the data. Originals are saved; please retry.",
        "Дверь целиком по центру кадра, немного стены вокруг и пол.": "Centre the entire door in the frame, including some surrounding wall and floor.",
        "Снимите дверь вместе с ближайшим углом комнаты.": "Capture the door together with the nearest room corner.",
        "Сместитесь примерно на полметра в сторону; снимите ту же дверь и угол.": "Move about half a metre sideways and capture the same door and corner.",
        "Широкий ракурс: пол и обе стены, образующие угол.": "Use a wide view showing the floor and both walls forming the corner.",
        "Недостаточно достоверной глубины. Подойдите ближе и снимите пол и угол.": "Not enough reliable depth data. Move closer and capture the floor and corner.",
        "Покажите больше пола у двери: не удаётся определить уровень пола.": "Show more floor near the door so the floor level can be determined.",
        "Нужны две стены у угла. Снимите угол вместе с полом.": "Both walls at the corner are needed. Capture the corner together with the floor.",
        "Привязка не подтверждена. Проверьте: первая фотография направлена в центр двери; доснимите пол и соседнюю стену.": "Alignment is not confirmed. Check that the first photo faces the centre of the door, then capture more of the floor and adjacent wall.",
        "Есть несколько подходящих положений. Снимите дверь вместе с более широким участком соседней стены.": "Several positions are possible. Capture the door with a wider section of the adjacent wall.",
        "Нужен iPhone с LiDAR: в снимках нет глубины.": "An iPhone with LiDAR is required: these photos have no depth data."
    ]
    static func serviceText(_ text: String) -> String { serviceMessages[text] ?? text }
    static func doorLabel(_ label: String) -> String {
        // Only the service-generated numbered label is translated; IFC names remain unchanged.
        guard label.range(of: "^Дверь [0-9]+$", options: .regularExpression) != nil else { return label }
        return "Door " + label.split(separator: " ").last!
    }
}
