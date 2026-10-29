import SwiftUI
import ARKit
import Combine

@MainActor
final class InspectorModel: ObservableObject {
    @Published var manifest: SessionManifest?
    @Published var observations: [FrameMetadata] = []
    @Published var busy = false
    @Published var error: String?
    @Published var notice = "Оркестратор не подключён. Данные сохраняются на телефоне."
    @Published var instruction: String?
    @Published var instructionID: String?
    @Published var geometryZIP: URL?
    @Published var synthetic: Bool
    let capture = CaptureService()
    let localization = SessionLocalization()
    let store: ObservationStore
    @Published private(set) var segment: String?

    let standalone: Bool
    let assignment: InspectorAssignment
    @Published var frozen = false

    init(assignment: InspectorAssignment, standalone: Bool = false) {
        self.standalone = standalone
        self.assignment = assignment
        synthetic = assignment.capture_origin == "synthetic"
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        store = ObservationStore(root: documents.appendingPathComponent("sessions"))
        capture.onPose = { [weak self] frame in
            guard let self, !self.standalone, let segment = self.segment else { return }
            let tracking: String, reason: String?
            switch frame.camera.trackingState {
            case .normal: tracking = "normal"; reason = nil
            case .notAvailable: tracking = "not_available"; reason = nil
            case .limited(let why): tracking = "limited"; reason = String(describing: why)
            }
            self.localization.pose(frame)
            if !self.localization.enabled { InspectorConnection.shared.sendPose(timestamp: frame.timestamp, tracking: tracking, reason: reason,
                pose: matrixRows(frame.camera.transform), intrinsics: matrixRows(frame.camera.intrinsics),
                size: [Int(frame.camera.imageResolution.width), Int(frame.camera.imageResolution.height)], segment: segment) }
        }
        capture.onInterruption = { [weak self] in self?.segment = nil; self?.localization.stop(); if self?.standalone == false { InspectorConnection.shared.endSpatialSegment() } }
    }

    func load() async {
        guard !busy, !frozen else { return }
        busy = true
        defer { busy = false }
        capture.stop()
        segment = nil
        manifest = nil
        observations = []
        instruction = nil
        instructionID = nil
        let store = store, origin = synthetic ? "synthetic" : "device", assignment = assignment
        let device = UIDevice.current.model, os = UIDevice.current.systemVersion
        do {
            let result = try await Task.detached {
                let session = try store.openAssigned(id: assignment.id, room: assignment.room_id, model: assignment.model_version, modelIsSynthetic: assignment.model_is_synthetic, origin: origin, device: device, os: os)
                return (session, try store.observations(session.session_id))
            }.value
            manifest = result.0
            observations = result.1
        } catch { self.error = error.localizedDescription }
    }

    private func newSegment() throws {
        guard var session = manifest else { throw CaptureError.invalid("Сессия не открыта") }
        let id = UUID().uuidString.lowercased()
        session.segment_ids.append(id)
        try store.save(session)
        manifest = session
        segment = id
    }

    func startCamera() async {
        guard !busy, !frozen else { return }
        busy = true
        defer { busy = false }
        do {
            if !standalone { try await InspectorConnection.shared.startCapture() }
            try newSegment()
            if let segment, !standalone {
                if assignment.model_version != "unbound", !capture.supportsDepth {
                    throw CaptureError.invalid("Для локализации по BIM нужен LiDAR. Обычная съёмка доступна в задании без BIM.")
                }
                try await localization.start(assignment: assignment, segment: segment, store: store)
            }
            try await capture.start()
            if let segment, !localization.enabled, !standalone {
                do { try await InspectorConnection.shared.registerSpatialSegment(segment) }
                catch { notice = "Локализация не подключилась: \(error.localizedDescription). Можно сохранять фото." }
            }
        } catch {
            segment = nil; localization.stop(); capture.stop()
            localization.status = error.localizedDescription
            self.error = error.localizedDescription
        }
    }

    func pause() {
        localization.stop()
        if !standalone { InspectorConnection.shared.endSpatialSegment() }
        persistMeshIfAvailable()
        capture.stop()
        segment = nil
    }

    func takeObservation() async {
        guard !busy, !frozen else { return }
        busy = true
        defer { busy = false }
        do {
            if localization.choosingEntry { throw CaptureError.invalid("Сначала выберите входную дверь на плане") }
            if synthetic && segment == nil { if !standalone { try await InspectorConnection.shared.startCapture() }; try newSegment() }
            guard let session = manifest, let segment else { throw CaptureError.invalid("Сначала включите камеру") }
            let spatialInstruction = standalone ? nil : localization.enabled ? localization.instruction : InspectorConnection.shared.liveInstruction(segment: segment)
            let store = store, instruction = spatialInstruction?.instruction_id ?? instructionID
            let frame: FrameMetadata
            if synthetic {
                let payload = try SyntheticCapture.make()
                frame = try await Task.detached {
                    try store.record(payload, session: session, segment: segment, instruction: instruction)
                }.value
            } else {
                let snapshot = try capture.snapshot()
                frame = try await Task.detached {
                    try store.record(CaptureService.encode(snapshot), session: session, segment: segment, instruction: instruction)
                }.value
            }
            observations.append(frame)
            if localization.enabled {
                Task { await localization.flush(store: store) }
            } else if !standalone {
                try InspectorConnection.shared.enqueueEarly(frame, store: store, instruction: spatialInstruction)
            }
            persistMeshIfAvailable()

            notice = "Наблюдение сохранено на телефоне. Завершите съёмку для отправки."
        } catch { self.error = error.localizedDescription }
    }

    private func persistMeshIfAvailable() {
        guard !synthetic, let segment, let manifest, capture.supportsMesh else { return }
        do {
            let bytes = try capture.meshSnapshot().ply()
            let folder = store.folder(manifest.session_id).appendingPathComponent("mesh")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try bytes.write(to: folder.appendingPathComponent(segment + ".ply"), options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: store.folder(manifest.session_id).appendingPathComponent("mesh/" + segment + ".ply"))
            notice = "Mesh ещё не готов: ZIP Geometry недоступен. Фото можно сохранить."
        }
    }
    func exportGeometry() async {
        guard !busy, !frozen, let manifest else { return }
        busy = true; defer { busy = false }
        persistMeshIfAvailable()
        if !standalone { InspectorConnection.shared.endSpatialSegment() }
        capture.stop(); segment = nil
        do {
            let segments = Set(observations.map(\.segment_id))
            guard segments.count == 1, let meshSegment = segments.first else { throw CaptureError.invalid("Для Geometry ZIP снимите кадры за один запуск камеры, без сбросов ARKit") }
            let store = store, projectID = assignment.project_id
            let mesh = try Data(contentsOf: store.folder(manifest.session_id).appendingPathComponent("mesh/" + meshSegment + ".ply"))
            geometryZIP = try await Task.detached {
                try GeometryExport.export(sessionID: manifest.session_id, store: store, mesh: mesh, meshSegment: meshSegment, projectID: projectID)
            }.value
            notice = "ZIP готов локально. Он пока не передаётся Inspector."
        } catch { self.error = error.localizedDescription }
    }

    func observationURL(_ frame: FrameMetadata) -> URL {
        store.folder(frame.session_id).appendingPathComponent("observations/\(frame.id)")
    }
}

@MainActor
final class StandaloneCapture: ObservableObject {
    @Published var busy = false
    @Published var progress = 0.0
    @Published var status = "Фото и метаданные сохраняются без задания и локализации."
    @Published var manifestKey: String?
    static let sessionKey = "standalone.capture.session"

    static func assignment() -> InspectorAssignment {
        let id = UserDefaults.standard.string(forKey: sessionKey) ?? UUID().uuidString.lowercased()
        UserDefaults.standard.set(id, forKey: sessionKey)
        return InspectorAssignment(session_id: id, inspection_id: "standalone", project_id: "standalone",
            room_id: "Тестовая съёмка", model_version: "unbound", model_is_synthetic: false,
            policy_version: "standalone_v1", device_id: InspectorConnection.defaultDevice,
            capture_origin: "device", input_mode: "live_capture", capture_state: "capturing", revision: 1)
    }
    func restore(_ model: InspectorModel) {
        let folder = model.store.folder(model.assignment.id)
        model.frozen = FileManager.default.fileExists(atPath: folder.appendingPathComponent("standalone-pending.json").path)
        manifestKey = try? String(contentsOf: folder.appendingPathComponent("standalone-uploaded.txt"), encoding: .utf8)
        if manifestKey != nil { status = "Фото и метаданные проверены и сохранены в Supabase." }
        else if model.frozen { status = "Набор зафиксирован. Можно повторить отправку." }
    }
    func upload(_ model: InspectorModel) async {
        guard !busy, !model.busy else { return }
        busy = true; progress = 0; model.pause()
        defer { busy = false }
        do {
            let folder = model.store.folder(model.assignment.id)
            let pendingURL = folder.appendingPathComponent("standalone-pending.json")
            var pending: PendingCompletion
            if FileManager.default.fileExists(atPath: pendingURL.path) {
                pending = try ObservationStore.decoder.decode(PendingCompletion.self, from: Data(contentsOf: pendingURL))
            } else {
                pending = try freezeCapture(assignment: model.assignment, store: model.store)
                pending.manifest.format = "standalone_capture_v1"
                pending.transportFormat = "files"
                try ObservationStore.encoder.encode(pending).write(to: pendingURL, options: .atomic)
            }
            model.frozen = true
            let total = pending.manifest.observations.reduce(0) { $0 + $1.files.count } + 1
            var done = 0
            for oi in pending.manifest.observations.indices {
                let observation = pending.manifest.observations[oi]
                for fi in observation.files.indices {
                    let file = observation.files[fi]
                    status = "Загрузка файлов: \(done + 1) / \(total)"
                    let bytes = try Data(contentsOf: folder.appendingPathComponent("observations/" + observation.observation_id + "/" + file.name))
                    guard bytes.count == file.byte_length, StoredFile(name: file.name, data: bytes).sha256 == file.sha256 else {
                        throw CaptureError.invalid("Локальный файл изменился после фиксации набора")
                    }
                    let media = file.name.hasSuffix("json") ? "application/json" : file.name.hasSuffix("jpg") ? "image/jpeg" : "application/octet-stream"
                    let key = try await InspectorConnection.shared.uploadStandalone(sessionID: model.assignment.id,
                        object: UploadObject(observation_id: observation.observation_id, name: file.name, media_type: media,
                                             byte_length: file.byte_length, sha256: file.sha256), bytes: bytes, completionID: nil)
                    pending.manifest.observations[oi].files[fi].object_key = key
                    done += 1; progress = Double(done) / Double(total)
                }
            }
            // Publish the manifest last. Consumers should discover only manifests, not incomplete folders.
            let bytes = try ObservationStore.encoder.encode(pending.manifest)
            let info = StoredFile(name: "capture-manifest.json", data: bytes)
            let key = try await InspectorConnection.shared.uploadStandalone(sessionID: model.assignment.id,
                object: UploadObject(observation_id: nil, name: info.name, media_type: "application/json",
                                     byte_length: info.byte_length, sha256: info.sha256), bytes: bytes, completionID: pending.completion_id)
            try key.write(to: folder.appendingPathComponent("standalone-uploaded.txt"), atomically: true, encoding: .utf8)
            manifestKey = key; progress = 1
            status = "Фото и метаданные проверены и сохранены в Supabase."
        } catch { model.error = error.localizedDescription; status = "Отправка не завершена. Оригиналы сохранены; повторите загрузку." }
    }
}
