import Foundation
import simd
import ImageIO

struct GeometryMesh: Sendable {
    let vertices: [SIMD3<Float>]
    let triangles: [SIMD3<Int32>]
    func ply() throws -> Data {
        guard !vertices.isEmpty, !triangles.isEmpty, vertices.count <= Int(Int32.max),
              vertices.allSatisfy({ $0.x.isFinite && $0.y.isFinite && $0.z.isFinite }),
              triangles.allSatisfy({ face in [face.x, face.y, face.z].allSatisfy { $0 >= 0 && Int($0) < vertices.count } }) else {
            throw CaptureError.invalid("Сетка ARKit отсутствует или повреждена")
        }
        var bytes = Data("ply\nformat binary_little_endian 1.0\nelement vertex \(vertices.count)\nproperty float x\nproperty float y\nproperty float z\nelement face \(triangles.count)\nproperty list uchar int vertex_indices\nend_header\n".utf8)
        for vertex in vertices { for value in [vertex.x, vertex.y, vertex.z] { bytes.appendLE(value.bitPattern) } }
        for triangle in triangles {
            bytes.append(3)
            for index in [triangle.x, triangle.y, triangle.z] { bytes.appendLE(UInt32(bitPattern: index)) }
        }
        return bytes
    }
}

extension Data {
    mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        var little = value.littleEndian
        Swift.withUnsafeBytes(of: &little) { append(contentsOf: $0) }
    }
}

/// ZIP STORE (method 0): JPEG and binary data are streamed one file at a time.
/// Deliberately rejects ZIP64-sized input rather than truncating offsets/sizes.
enum CaptureZIP {
    private static let crcTable: [UInt32] = (0..<256).map { n in
        var value = UInt32(n)
        for _ in 0..<8 { value = (value & 1) == 1 ? (value >> 1) ^ 0xedb88320 : value >> 1 }
        return value
    }
    static func crc32(_ bytes: Data) -> UInt32 {
        var crc: UInt32 = 0xffffffff
        for byte in bytes { crc = (crc >> 8) ^ crcTable[Int((crc ^ UInt32(byte)) & 255)] }
        return crc ^ 0xffffffff
    }
    static func write(files: [(String, URL)], to output: URL) throws {
        guard files.count < 65535, Set(files.map { $0.0 }).count == files.count else { throw CaptureError.invalid("Слишком много файлов ZIP или повтор пути") }
        let temp = output.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString.lowercased()).zip")
        FileManager.default.createFile(atPath: temp.path, contents: nil)
        defer { try? FileManager.default.removeItem(at: temp) }
        let handle = try FileHandle(forWritingTo: temp); defer { try? handle.close() }
        var directory = Data()
        for (name, file) in files {
            guard !name.hasPrefix("/"), !name.split(separator: "/").contains(".."), name.utf8.count < 65535 else { throw CaptureError.invalid("Недопустимый путь ZIP") }
            let bytes = try Data(contentsOf: file, options: .mappedIfSafe)
            let offset = try handle.offset()
            guard bytes.count < Int(UInt32.max), offset < UInt64(UInt32.max) else { throw CaptureError.invalid("ZIP больше поддерживаемого размера") }
            let path = Data(name.utf8), crc = crc32(bytes), size = UInt32(bytes.count)
            var local = Data(); local.appendLE(UInt32(0x04034b50)); local.appendLE(UInt16(20))
            for value: UInt16 in [0x0800, 0, 0, 0x0021] { local.appendLE(value) }
            local.appendLE(crc); local.appendLE(size); local.appendLE(size); local.appendLE(UInt16(path.count)); local.appendLE(UInt16(0)); local.append(path)
            try handle.write(contentsOf: local); try handle.write(contentsOf: bytes)
            directory.appendLE(UInt32(0x02014b50)); directory.appendLE(UInt16(20)); directory.appendLE(UInt16(20))
            for value: UInt16 in [0x0800, 0, 0, 0x0021] { directory.appendLE(value) }
            directory.appendLE(crc); directory.appendLE(size); directory.appendLE(size); directory.appendLE(UInt16(path.count))
            for _ in 0..<4 { directory.appendLE(UInt16(0)) }
            directory.appendLE(UInt32(0)); directory.appendLE(UInt32(offset)); directory.append(path)
        }
        let offset = try handle.offset()
        guard offset + UInt64(directory.count) + 22 < UInt64(UInt32.max) else { throw CaptureError.invalid("ZIP больше поддерживаемого размера") }
        try handle.write(contentsOf: directory)
        var end = Data(); end.appendLE(UInt32(0x06054b50)); end.appendLE(UInt16(0)); end.appendLE(UInt16(0))
        end.appendLE(UInt16(files.count)); end.appendLE(UInt16(files.count)); end.appendLE(UInt32(directory.count)); end.appendLE(UInt32(offset)); end.appendLE(UInt16(0))
        try handle.write(contentsOf: end); try handle.synchronize(); try handle.close()
        if FileManager.default.fileExists(atPath: output.path) { _ = try FileManager.default.replaceItemAt(output, withItemAt: temp) }
        else { try FileManager.default.moveItem(at: temp, to: output) }
    }
}

enum GeometryExport {
    static func sanitizedDepth(_ bytes: Data) throws -> Data {
        guard bytes.count % 4 == 0 else { throw CaptureError.invalid("Неполный Float32 в depth") }
        var result = Data(capacity: bytes.count)
        bytes.withUnsafeBytes { raw in
            for offset in stride(from: 0, to: bytes.count, by: 4) {
                let value = Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: offset, as: UInt32.self)))
                result.appendLE(value.isFinite && value > 0 ? value.bitPattern : UInt32(0))
            }
        }
        return result
    }
    static func export(sessionID: String, store: ObservationStore, mesh: Data?, meshSegment: String, projectID: String? = nil, selectedFrameIDs: Set<String>? = nil, outputName: String? = nil) throws -> URL {
        guard sessionID.range(of: "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", options: .regularExpression) != nil else { throw CaptureError.invalid("Неверный ID Geometry capture") }
        let frames = try store.observations(sessionID).filter { selectedFrameIDs == nil || selectedFrameIDs!.contains($0.id) }.sorted { $0.timestamp_s < $1.timestamp_s }
        guard !frames.isEmpty, Set(frames.map(\.segment_id)) == [meshSegment], frames.allSatisfy({ $0.capture_origin == "device" }) else {
            throw CaptureError.invalid("Geometry ZIP требует реальные кадры и mesh из одной непрерывной AR-сессии")
        }
        if let mesh {
            guard mesh.starts(with: Data("ply\nformat binary_little_endian 1.0\n".utf8)) else { throw CaptureError.invalid("Нет mesh ARKit") }
        } else if frames.contains(where: { $0.depth != nil }) {
            throw CaptureError.invalid("Для LiDAR-кадров отсутствует mesh.ply сегмента " + meshSegment)
        }
        let staging = store.folder(sessionID).appendingPathComponent(".geometry-\(UUID().uuidString.lowercased())")
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: staging) }
        var files: [(String, URL)] = []
        func add(_ name: String, _ data: Data) throws {
            let url = staging.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic); files.append((name, url))
        }
        func json(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted]) }
        if let mesh { try add("mesh.ply", mesh) }
        for frame in frames {
            guard frame.tracking_state == "normal", frame.K_rgb.flatMap({ $0 }).allSatisfy(\.isFinite), frame.T_world_camera.flatMap({ $0 }).allSatisfy(\.isFinite), frame.timestamp_s.isFinite else { throw CaptureError.invalid("Неверные метаданные Geometry кадра") }
            let id = frame.frame_id, prefix = "frames/" + frame.frame_id + "/"
            let source = store.folder(sessionID).appendingPathComponent("observations/" + frame.id)
            let rgb = try Data(contentsOf: source.appendingPathComponent(frame.rgb.name))
            guard let image = CGImageSourceCreateWithData(rgb as CFData, nil), let properties = CGImageSourceCopyPropertiesAtIndex(image, 0, nil) as? [CFString: Any],
                  properties[kCGImagePropertyPixelWidth] as? Int == frame.rgb_size[0], properties[kCGImagePropertyPixelHeight] as? Int == frame.rgb_size[1],
                  (properties[kCGImagePropertyOrientation] as? Int ?? 1) == 1 else { throw CaptureError.invalid("Размер/ориентация JPEG не соответствует intrinsics") }
            try add(prefix + "rgb.jpg", rgb)
            var depth: Any = NSNull(), confidence: Any = NSNull()
            if let file = frame.depth, let size = frame.depth_size {
                guard frame.depth_source == "sceneDepth", file.byte_length == size[0] * size[1] * 4 else { throw CaptureError.invalid("Неверный depth для Geometry") }
                try add(prefix + "depth.bin", Data(contentsOf: source.appendingPathComponent(file.name)))
                depth = ["path": "depth.bin", "width": size[0], "height": size[1], "dtype": "float32", "byte_order": "little", "unit": "meter", "source": "sceneDepth"] as [String: Any]
                if let file = frame.confidence {
                    let bytes = try Data(contentsOf: source.appendingPathComponent(file.name))
                    guard bytes.count == size[0] * size[1], bytes.allSatisfy({ $0 <= 2 }) else { throw CaptureError.invalid("Неверный confidence") }
                    try add(prefix + "confidence.bin", bytes)
                    confidence = ["path": "confidence.bin", "width": size[0], "height": size[1], "dtype": "uint8"] as [String: Any]
                }
            }
            try add(prefix + "metadata.json", json(["schema_version": "1.0", "frame_id": id, "timestamp_s": frame.timestamp_s, "tracking_state": frame.tracking_state,
                "rgb": ["path": "rgb.jpg", "width": frame.rgb_size[0], "height": frame.rgb_size[1]], "intrinsics": ["K": frame.K_rgb, "width": frame.rgb_size[0], "height": frame.rgb_size[1]],
                "pose": ["T_world_from_camera": frame.T_world_camera], "depth": depth, "confidence": confidence]))
        }
        try add("capture.json", json(["schema_version": "1.0", "session_id": sessionID, "project_id": projectID as Any? ?? NSNull(), "length_unit": "meter", "coordinate_system": "arkit", "world_alignment": "gravity", "matrix_storage": "rows", "mesh": mesh == nil ? NSNull() : "mesh.ply" as Any, "frames_directory": "frames", "frame_count": frames.count]))
        let output = store.folder(sessionID).appendingPathComponent(outputName ?? (sessionID + ".zip"))
        try CaptureZIP.write(files: files, to: output)
        return output
    }
}

/// Inspector's transport archive is distinct from the Geometry Engine mesh archive.
enum InspectorArchive {
    struct Result: Sendable {
        let manifest: FinalCaptureManifest
        let manifestBytes: Data
        let info: StoredFile
    }
    static func url(sessionID: String, completionID: String, store: ObservationStore) -> URL {
        store.folder(sessionID).appendingPathComponent("archives/" + completionID + ".zip")
    }
    static func build(pending: PendingCompletion, store: ObservationStore, keyPrefix: String) throws -> Result {
        guard !keyPrefix.isEmpty, keyPrefix.hasSuffix("/"), !keyPrefix.hasPrefix("/"), !keyPrefix.contains("://"),
              !keyPrefix.split(separator: "/").contains("..") else { throw CaptureError.invalid("Неверный префикс хранилища Inspector") }
        var manifest = pending.manifest
        guard UUID(uuidString: manifest.session_id) != nil, UUID(uuidString: pending.completion_id) != nil,
              !manifest.observations.isEmpty, manifest.observation_count == manifest.observations.count,
              Set(manifest.observations.map(\.observation_id)).count == manifest.observations.count else {
            throw CaptureError.invalid("Неверный inventory ZIP")
        }
        var entries: [(String, URL)] = []
        for (oi, observation) in manifest.observations.enumerated() {
            guard UUID(uuidString: observation.observation_id) != nil,
                  Set(observation.files.map(\.name)).isSuperset(of: ["frame.json", "rgb.jpg"]) else { throw CaptureError.invalid("Неполное наблюдение ZIP") }
            for (fi, file) in observation.files.enumerated() {
                guard ["frame.json", "rgb.jpg", "depth.f32", "confidence.u8"].contains(file.name) else { throw CaptureError.invalid("Неизвестный файл ZIP") }
                let relative = "observations/" + observation.observation_id + "/" + file.name
                let source = store.folder(manifest.session_id).appendingPathComponent(relative)
                let bytes = try Data(contentsOf: source, options: .mappedIfSafe)
                guard bytes.count == file.byte_length, StoredFile(name: "", data: bytes).sha256 == file.sha256 else { throw CaptureError.invalid("Повреждён файл перед архивацией") }
                manifest.observations[oi].files[fi].object_key = keyPrefix + relative
                entries.append((relative, source))
            }
        }
        let bytes = try ObservationStore.encoder.encode(manifest)
        let output = url(sessionID: manifest.session_id, completionID: pending.completion_id, store: store)
        try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
        let tempManifest = output.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString.lowercased()).json")
        try bytes.write(to: tempManifest, options: .atomic)
        defer { try? FileManager.default.removeItem(at: tempManifest) }
        try CaptureZIP.write(files: [("capture-manifest.json", tempManifest)] + entries, to: output)
        let archive = try Data(contentsOf: output, options: .mappedIfSafe)
        return Result(manifest: manifest, manifestBytes: bytes, info: StoredFile(name: "capture.zip", data: archive))
    }
}

/// Independent ZIPs: whole frames, one ARKit segment, matching mesh repeated in every part.
/// Publication is described by a separate index; no part is a binary slice of a ZIP.
enum GeometryPartsExport {
    struct Part: Codable, Sendable {
        let name: String
        let segment_id: String
        let frame_ids: [String]
        let depth_frame_ids: [String]
        let byte_length: Int
        let sha256: String
        var object_key: String = ""
    }
    struct Index: Codable, Sendable {
        var schema_version = "1.0"
        var format = "geometry_capture_parts_v1"
        let session_id: String
        let project_id: String
        let completion_id: String
        let frame_count: Int
        var parts: [Part]
    }
    static func build(assignment: InspectorAssignment, pending: PendingCompletion, store: ObservationStore, maxBytes: Int) throws -> Index {
        guard maxBytes > 0, pending.manifest.session_id == assignment.id else { throw CaptureError.invalid("Неверный лимит или сессия архивов") }
        let allowed = Set(pending.manifest.observations.map(\.observation_id))
        let frames = try store.observations(assignment.id).filter { allowed.contains($0.id) }
        guard frames.count == allowed.count, !frames.isEmpty else { throw CaptureError.invalid("Неполный набор кадров для ZIP") }
        let folder = store.folder(assignment.id).appendingPathComponent("geometry-parts/" + pending.completion_id)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var parts: [Part] = []
        for segment in Set(frames.map(\.segment_id)).sorted() {
            let group = frames.filter { $0.segment_id == segment }.sorted { $0.timestamp_s < $1.timestamp_s }
            let meshURL = store.folder(assignment.id).appendingPathComponent("mesh/" + segment + ".ply")
            let mesh = FileManager.default.fileExists(atPath: meshURL.path) ? try Data(contentsOf: meshURL) : nil
            if group.contains(where: { $0.depth != nil }) && mesh == nil { throw CaptureError.invalid("Не найден mesh.ply для сегмента " + segment) }
            func pack(_ selected: [FrameMetadata]) throws {
                let temporary = try GeometryExport.export(sessionID: assignment.id, store: store, mesh: mesh, meshSegment: segment,
                    projectID: assignment.project_id, selectedFrameIDs: Set(selected.map(\.id)), outputName: ".geometry-candidate.zip")
                defer { try? FileManager.default.removeItem(at: temporary) }
                let size = try temporary.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                if size > maxBytes {
                    guard selected.count > 1 else { throw CaptureError.invalid("Mesh и один кадр превышают лимит ZIP. Данные сохранены; требуется отдельная передача mesh или другой лимит.") }
                    let midpoint = selected.count / 2
                    try pack(Array(selected[..<midpoint])); try pack(Array(selected[midpoint...])); return
                }
                let name = String(format: "capture-%04d.zip", parts.count + 1)
                let destination = folder.appendingPathComponent(name)
                let bytes = try Data(contentsOf: temporary, options: .mappedIfSafe)
                try bytes.write(to: destination, options: .atomic)
                let info = StoredFile(name: name, data: bytes)
                parts.append(Part(name: name, segment_id: segment, frame_ids: selected.map(\.frame_id),
                    depth_frame_ids: selected.filter { $0.depth != nil && $0.confidence != nil }.map(\.frame_id), byte_length: info.byte_length, sha256: info.sha256))
            }
            // Keep test-only RGB/missing-confidence frames in separate archives so Geometry can skip downloads.
            for usableDepth in [true, false] {
                let selected = group.filter { ($0.depth != nil && $0.confidence != nil) == usableDepth }
                if !selected.isEmpty { try pack(selected) }
            }
        }
        return Index(session_id: assignment.id, project_id: assignment.project_id, completion_id: pending.completion_id, frame_count: frames.count, parts: parts)
    }
}
