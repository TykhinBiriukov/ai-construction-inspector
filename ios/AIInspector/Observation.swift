import Foundation
import CryptoKit
import simd

func matrixRows(_ matrix: simd_float3x3) -> [[Float]] {
    (0..<3).map { row in (0..<3).map { column in matrix[column][row] } }
}

func matrixRows(_ matrix: simd_float4x4) -> [[Float]] {
    (0..<4).map { row in (0..<4).map { column in matrix[column][row] } }
}

struct StoredFile: Codable, Sendable {
    let name: String
    let byte_length: Int
    let sha256: String

    init(name: String, data: Data) {
        self.name = name
        byte_length = data.count
        sha256 = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

struct FrameMetadata: Codable, Sendable, Identifiable {
    var id: String { observation_id }
    let schema_version: String
    let observation_id: String
    let frame_id: String
    let session_id: String
    let segment_id: String
    let instruction_id: String?
    let capture_origin: String
    let timestamp_s: Double
    let saved_at: Date
    let rgb_size: [Int]
    let depth_size: [Int]?
    let K_rgb: [[Float]]
    let T_world_camera: [[Float]]
    let tracking_state: String
    let depth_source: String
    let depth_unit: String?
    let image_orientation: String
    let source_to_stored_image: [[Float]]
    let depth_to_rgb_mapping: String?
    let rgb: StoredFile
    let depth: StoredFile?
    let confidence: StoredFile?

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schema_version, forKey: .schema_version)
        try c.encode(observation_id, forKey: .observation_id)
        try c.encode(frame_id, forKey: .frame_id)
        try c.encode(session_id, forKey: .session_id)
        try c.encode(segment_id, forKey: .segment_id)
        try c.encode(instruction_id, forKey: .instruction_id)
        try c.encode(capture_origin, forKey: .capture_origin)
        try c.encode(timestamp_s, forKey: .timestamp_s)
        try c.encode(saved_at, forKey: .saved_at)
        try c.encode(rgb_size, forKey: .rgb_size)
        try c.encode(depth_size, forKey: .depth_size)
        try c.encode(K_rgb, forKey: .K_rgb)
        try c.encode(T_world_camera, forKey: .T_world_camera)
        try c.encode(tracking_state, forKey: .tracking_state)
        try c.encode(depth_source, forKey: .depth_source)
        try c.encode(depth_unit, forKey: .depth_unit)
        try c.encode(image_orientation, forKey: .image_orientation)
        try c.encode(source_to_stored_image, forKey: .source_to_stored_image)
        try c.encode(depth_to_rgb_mapping, forKey: .depth_to_rgb_mapping)
        try c.encode(rgb, forKey: .rgb)
        try c.encode(depth, forKey: .depth)
        try c.encode(confidence, forKey: .confidence)
    }
}

struct CapturePayload: Sendable {
    let rgb: Data
    let depth: Data?
    let confidence: Data?
    let rgbSize: [Int]
    let depthSize: [Int]?
    let intrinsics: [[Float]]
    let pose: [[Float]]
    let timestamp: Double
    let origin: String
    let tracking: String
}

struct SessionManifest: Codable, Sendable {
    let schema_version: String
    let session_id: String
    let room_id: String
    let model_version: String
    let model_is_synthetic: Bool
    let capture_origin: String
    let created_at: Date
    var segment_ids: [String]
    let units: String
    let coordinates: String
    let matrix_convention: String
    let depth_encoding: String
    let confidence_encoding: String
    let device: String
    let os_version: String
}

enum CaptureError: LocalizedError {
    case invalid(String)
    var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

/// One writer per session. Observation directories are committed by an atomic rename;
/// the inventory is discovered from committed directories rather than a second index.
struct ObservationStore: Sendable {
    let root: URL

    static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    func open(origin: String, device: String, os: String) throws -> SessionManifest {
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let folders = try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        let existing = try folders.filter { !$0.lastPathComponent.hasPrefix(".") }.map {
            try Self.decoder.decode(SessionManifest.self, from: Data(contentsOf: $0.appendingPathComponent("manifest.json")))
        }.filter { $0.capture_origin == origin }.sorted { $0.created_at > $1.created_at }
        if let session = existing.first { return session }
        let session = SessionManifest(
            schema_version: "0.1", session_id: UUID().uuidString.lowercased(), room_id: "room-204",
            model_version: "demo-room-v1", model_is_synthetic: true, capture_origin: origin,
            created_at: Date(), segment_ids: [], units: "m",
            coordinates: "ARKit right-handed; world Y up; camera X right, Y up, looks along -Z. Each segment has independent origin.",
            matrix_convention: "Nested rows; column vectors; T_world_camera maps camera to segment world.",
            depth_encoding: "float32 little-endian, row-major, no padding; metres; exclude nonfinite/nonpositive values",
            confidence_encoding: "uint8 row-major; 0 low, 1 medium, 2 high; null when missing",
            device: device, os_version: os)
        try save(session)
        return session
    }

    func openAssigned(id: String, room: String, model: String, modelIsSynthetic: Bool,
                      origin: String, device: String, os: String) throws -> SessionManifest {
        guard UUID(uuidString: id) != nil else { throw CaptureError.invalid("Неверный ID сессии") }
        let url = folder(id).appendingPathComponent("manifest.json")
        if FileManager.default.fileExists(atPath: url.path) {
            let existing = try Self.decoder.decode(SessionManifest.self, from: Data(contentsOf: url))
            guard existing.session_id == id, existing.room_id == room, existing.model_version == model,
                  existing.model_is_synthetic == modelIsSynthetic, existing.capture_origin == origin else {
                throw CaptureError.invalid("Контекст существующей сессии изменился")
            }
            return existing
        }
        let session = SessionManifest(schema_version: "0.1", session_id: id, room_id: room, model_version: model,
            model_is_synthetic: modelIsSynthetic, capture_origin: origin, created_at: Date(), segment_ids: [], units: "m",
            coordinates: "ARKit right-handed; world Y up; camera X right, Y up, looks along -Z. Each segment has independent origin.",
            matrix_convention: "Nested rows; column vectors; T_world_camera maps camera to segment world.",
            depth_encoding: "float32 little-endian, row-major, no padding; metres; absent for RGB-only",
            confidence_encoding: "uint8 row-major; 0 low, 1 medium, 2 high; null when missing", device: device, os_version: os)
        try save(session)
        return session
    }

    func folder(_ sessionID: String) -> URL { root.appendingPathComponent(sessionID) }

    func save(_ manifest: SessionManifest) throws {
        let folder = folder(manifest.session_id)
        try FileManager.default.createDirectory(at: folder.appendingPathComponent("observations"), withIntermediateDirectories: true)
        try Self.encoder.encode(manifest).write(to: folder.appendingPathComponent("manifest.json"), options: .atomic)
    }

    func observations(_ sessionID: String) throws -> [FrameMetadata] {
        let folders = try FileManager.default.contentsOfDirectory(
            at: folder(sessionID).appendingPathComponent("observations"), includingPropertiesForKeys: nil)
        return try folders.filter { !$0.lastPathComponent.hasPrefix(".") }.map {
            let frame = try Self.decoder.decode(FrameMetadata.self, from: Data(contentsOf: $0.appendingPathComponent("frame.json")))
            guard frame.session_id == sessionID, frame.id == $0.lastPathComponent,
                  UUID(uuidString: frame.id) != nil else { throw CaptureError.invalid("Неверный ID сохранённого наблюдения") }
            for file in [frame.rgb] + (frame.depth.map { [$0] } ?? []) + (frame.confidence.map { [$0] } ?? []) {
                guard ["rgb.jpg", "depth.f32", "confidence.u8"].contains(file.name) else { throw CaptureError.invalid("Неверное имя файла") }
                let bytes = try Data(contentsOf: $0.appendingPathComponent(file.name))
                guard StoredFile(name: file.name, data: bytes).sha256 == file.sha256,
                      bytes.count == file.byte_length else { throw CaptureError.invalid("Повреждено наблюдение \(frame.id)") }
            }
            return frame
        }.sorted { $0.saved_at < $1.saved_at }
    }

    func record(_ payload: CapturePayload, session: SessionManifest, segment: String, instruction: String?) throws -> FrameMetadata {
        guard session.segment_ids.contains(segment), session.capture_origin == payload.origin else {
            throw CaptureError.invalid("Неверный сегмент или источник наблюдения")
        }
        guard payload.rgbSize.count == 2,
              payload.rgbSize.allSatisfy({ $0 > 0 && $0 < 16384 }),
              !payload.rgb.isEmpty,
              payload.intrinsics.count == 3, payload.intrinsics.allSatisfy({ $0.count == 3 && $0.allSatisfy(\.isFinite) }),
              payload.pose.count == 4, payload.pose.allSatisfy({ $0.count == 4 && $0.allSatisfy(\.isFinite) }),
              payload.timestamp.isFinite else { throw CaptureError.invalid("Неполные данные кадра") }
        if let depth = payload.depth {
            guard let size = payload.depthSize, size.count == 2,
                  size.allSatisfy({ $0 > 0 && $0 < 16384 }),
                  depth.count == size[0] * size[1] * 4,
                  payload.confidence == nil || payload.confidence?.count == size[0] * size[1]
            else { throw CaptureError.invalid("Неверные данные глубины") }
        } else {
            guard payload.depthSize == nil, payload.confidence == nil else {
                throw CaptureError.invalid("Метаданные глубины без карты глубины")
            }
        }
        let id = UUID().uuidString.lowercased()
        let frame = FrameMetadata(
            schema_version: "0.2", observation_id: id, frame_id: id,
            session_id: session.session_id, segment_id: segment, instruction_id: instruction,
            capture_origin: payload.origin, timestamp_s: payload.timestamp, saved_at: Date(),
            rgb_size: payload.rgbSize, depth_size: payload.depthSize,
            K_rgb: payload.intrinsics, T_world_camera: payload.pose, tracking_state: payload.tracking,
            depth_source: payload.depth == nil ? "none" : (payload.origin == "device" ? "sceneDepth" : "synthetic_plane"),
            depth_unit: payload.depth == nil ? nil : "m", image_orientation: "native_buffer_unrotated_no_exif_rotation",
            source_to_stored_image: [[1,0,0],[0,1,0],[0,0,1]],
            depth_to_rgb_mapping: payload.depth == nil ? nil : "Unrotated aligned buffers; pixel origin top-left, u right/v down. Scale K_rgb rows 0/1 by depthWidth/rgbWidth and depthHeight/rgbHeight. Device reprojection validation pending.",
            rgb: StoredFile(name: "rgb.jpg", data: payload.rgb),
            depth: payload.depth.map { StoredFile(name: "depth.f32", data: $0) },
            confidence: payload.confidence.map { StoredFile(name: "confidence.u8", data: $0) })
        let parent = folder(session.session_id).appendingPathComponent("observations")
        let staging = parent.appendingPathComponent(".pending-\(id)")
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: staging) }
        try payload.rgb.write(to: staging.appendingPathComponent("rgb.jpg"))
        try payload.depth?.write(to: staging.appendingPathComponent("depth.f32"))
        try payload.confidence?.write(to: staging.appendingPathComponent("confidence.u8"))
        try Self.encoder.encode(frame).write(to: staging.appendingPathComponent("frame.json"))
        try FileManager.default.moveItem(at: staging, to: parent.appendingPathComponent(id))
        return frame
    }
}
