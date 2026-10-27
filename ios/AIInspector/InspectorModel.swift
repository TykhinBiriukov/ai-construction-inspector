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
    @Published var synthetic: Bool
    let capture = CaptureService()
    let store: ObservationStore
    private var segment: String?

    init() {
        #if targetEnvironment(simulator)
        synthetic = true
        #else
        synthetic = false
        #endif
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        store = ObservationStore(root: documents.appendingPathComponent("sessions"))
        capture.onInterruption = { [weak self] in self?.segment = nil }
    }

    func load() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        capture.stop()
        segment = nil
        manifest = nil
        observations = []
        instruction = nil
        instructionID = nil
        let store = store, origin = synthetic ? "synthetic" : "device"
        let device = UIDevice.current.model, os = UIDevice.current.systemVersion
        do {
            let result = try await Task.detached {
                let session = try store.open(origin: origin, device: device, os: os)
                return (session, try store.observations(session.session_id))
            }.value
            manifest = result.0
            observations = result.1
        } catch { self.error = error.localizedDescription }
    }

    private func newSegment() throws {
        guard var session = manifest else { throw CaptureError.invalid("Сессия не открыта") }
        let id = UUID().uuidString
        session.segment_ids.append(id)
        try store.save(session)
        manifest = session
        segment = id
    }

    func startCamera() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try newSegment()
            try await capture.start()
        } catch {
            segment = nil
            self.error = error.localizedDescription
        }
    }

    func pause() {
        capture.stop()
        segment = nil
    }

    func takeObservation() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            if synthetic && segment == nil { try newSegment() }
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
            notice = "Наблюдение сохранено на телефоне. Отправка ожидает подключения оркестратора."
        } catch { self.error = error.localizedDescription }
    }

    func demoInstruction() {
        instructionID = "mock-door-01"
        instruction = "Снимите дверной проём с другого ракурса, сохраняя часть предыдущего вида в кадре."
    }

    func cannotAccess() {
        notice = "Демо: ответ «Нет доступа» показан локально. Оркестратору ничего не отправлено."
    }

    func observationURL(_ frame: FrameMetadata) -> URL {
        store.folder(frame.session_id).appendingPathComponent("observations/\(frame.id)")
    }
}
