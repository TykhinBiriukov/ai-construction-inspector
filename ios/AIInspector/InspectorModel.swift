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
    let store: ObservationStore
    private var segment: String?

    let assignment: InspectorAssignment
    @Published var frozen = false

    init(assignment: InspectorAssignment) {
        self.assignment = assignment
        synthetic = assignment.capture_origin == "synthetic"
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        store = ObservationStore(root: documents.appendingPathComponent("sessions"))
        capture.onInterruption = { [weak self] in self?.segment = nil }
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
            try await InspectorConnection.shared.startCapture()
            try newSegment()
            try await capture.start()
        } catch {
            segment = nil
            self.error = error.localizedDescription
        }
    }

    func pause() {
        persistMeshIfAvailable()
        capture.stop()
        segment = nil
    }

    func takeObservation() async {
        guard !busy, !frozen else { return }
        busy = true
        defer { busy = false }
        do {
            if synthetic && segment == nil { try await InspectorConnection.shared.startCapture(); try newSegment() }
            guard let session = manifest, let segment else { throw CaptureError.invalid("Сначала включите камеру") }
            let store = store, instruction = instructionID
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
        capture.stop(); segment = nil
        do {
            let segments = Set(observations.map(\.segment_id))
            guard segments.count == 1, let meshSegment = segments.first else { throw CaptureError.invalid("Для Geometry ZIP снимите кадры за один запуск камеры, без сбросов ARKit") }
            let store = store
            let mesh = try Data(contentsOf: store.folder(manifest.session_id).appendingPathComponent("mesh/" + meshSegment + ".ply"))
            geometryZIP = try await Task.detached {
                try GeometryExport.export(sessionID: manifest.session_id, store: store, mesh: mesh, meshSegment: meshSegment)
            }.value
            notice = "ZIP готов локально. Он пока не передаётся Inspector."
        } catch { self.error = error.localizedDescription }
    }

    func observationURL(_ frame: FrameMetadata) -> URL {
        store.folder(frame.session_id).appendingPathComponent("observations/\(frame.id)")
    }
}
