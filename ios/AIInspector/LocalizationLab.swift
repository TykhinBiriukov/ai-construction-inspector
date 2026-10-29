import SwiftUI
import ARKit
import Combine

struct LabState: Decodable {
    struct Room: Decodable {
        struct Door: Decodable, Identifiable {
            let element_id: String
            let label: String
            let name: String
            let low: [Double]
            let high: [Double]
            var id: String { element_id }
        }
        let doors: [Door]?
        let entry_element_id: String?
        let entry_confirmed: Bool?
        let bim_frame_id: String?
        let model_version: String
        let room_id: String
        let length: Double
        let width: Double
        let height: Double
        let door_height: Double
        let door_offset: Double
        let door_width: Double
    }
    struct Result: Decodable {
        struct Quality: Decodable { let residual_rmse_m: Double?; let overlap_fraction: Double? }
        let status: String
        let quality: Quality?
    }
    struct Pose: Decodable { let T_bim_camera: [[Double]] }
    let session_id: String
    let revision: Int
    let guidance: SpatialSnapshot.Guidance
    let instructions: [SpatialInstruction]
    let room: Room
    let observation_count: Int
    let localization: Result?
    let latest_pose: Pose?
    let observation_poses: [Pose]
    struct Coverage: Decodable {
        struct Tile: Decodable { let id: String; let center: [Double]; let size: [Double]; let observation_ids: [String] }
        let policy: String
        let tiles: [Tile]
    }
    let coverage: Coverage?
    struct Binding: Decodable { let session_id: String; let project_id: String; let room_id: String; let model_version: String }
    let assignment_binding: Binding?
}

@MainActor
final class SessionLocalization: ObservableObject {
    @Published var state: LabState?
    @Published var meshes: [LabDisplayMesh] = []
    @Published var status = ""
    @Published var receivedAt = Date.distantPast
    @Published var enabled = false
    @Published var choosingEntry = false
    @Published var entryBusy = false
    private var generation = UUID()
    private var pendingSegment: String?
    private var pendingStore: ObservationStore?

    private var assignment: InspectorAssignment?
    private var segment: String?
    private var base: URL?
    private var code = ""
    private var sequence = 0
    private var lastPose = -Double.infinity
    private var sending = false
    private var draining = false
    private var accepted = Set<String>()
    private var poll: Task<Void,Never>?
    private let redirects = StorageHTTP()
    private lazy var http = URLSession(configuration: .ephemeral, delegate: redirects, delegateQueue: nil)
    static var endpoint: String {
        UserDefaults.standard.string(forKey: "localization.lab.endpoint") ?? (Bundle.main.object(forInfoDictionaryKey: "LocalizationLabDefaultEndpoint") as? String ?? "http://mac.local:8788")
    }
    static func configure(endpoint: String, pairing: String) throws {
        let text = endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        _ = try validatedURL(text)
        if !pairing.isEmpty { try InspectorCredential.save(pairing.trimmingCharacters(in: .whitespacesAndNewlines), scope: "localization-lab|" + text) }
        UserDefaults.standard.set(text, forKey: "localization.lab.endpoint")
    }
    private static func validatedURL(_ text: String) throws -> URL {
        guard let url = URL(string: text), let host = url.host,
              url.scheme == "https" || (url.scheme == "http" && (host.hasSuffix(".local") || host == Bundle.main.object(forInfoDictionaryKey: "LocalizationLabAllowedIPv4") as? String)),
              url.user == nil, url.password == nil, url.path.isEmpty, url.query == nil, url.fragment == nil else {
            throw CaptureError.invalid("Укажите HTTPS-адрес Localization Service или разрешённый локальный адрес Mac")
        }
        return url
    }
    func start(assignment: InspectorAssignment, segment: String, store: ObservationStore) async throws {
        stop(); self.assignment = assignment
        let generation = self.generation
        enabled = assignment.model_version != "unbound" && assignment.capture_origin == "device"
        guard enabled else { state = nil; meshes = []; return }
        status = "Подключение локализации…"
        let access = try await InspectorConnection.shared.prepareLocalization(assignment)
        guard URL(string: access.endpoint)?.scheme == "https" else { throw CaptureError.invalid("Облачная локализация требует HTTPS") }
        base = try Self.validatedURL(access.endpoint)
        code = access.session_token
        guard self.generation == generation else { throw CancellationError() }
        let configuration = try JSONDecoder().decode(LabDisplayConfiguration.self, from: await request("sessions/\(assignment.id)/config"))
        guard assignment.model_version == configuration.room.model_version || assignment.model_version == "upload-sha256:" + configuration.room.model_version,
              !configuration.meshes.isEmpty else { throw CaptureError.invalid("Localization Service вернул другую BIM-модель") }
        guard self.generation == generation else { throw CancellationError() }
        meshes = configuration.meshes; state = nil
        try accept(await request("sessions/\(assignment.id)"))
        guard self.generation == generation else { throw CancellationError() }
        if configuration.entry_selection_required == true {
            pendingSegment = segment; pendingStore = store; choosingEntry = true
            status = "Выберите дверь, с которой начнёте съёмку. Номера показаны на 3D-модели."
            return
        }
        try await activate(assignment: assignment, segment: segment, store: store)
    }
    func chooseEntry(_ id: String) async {
        guard !entryBusy, let assignment, let pendingSegment, let pendingStore else { return }
        let generation = self.generation
        entryBusy = true; defer { if self.generation == generation { entryBusy = false } }
        do {
            let bytes = try await request("sessions/\(assignment.id)/entry", body: ["entry_element_id":id])
            let config = try JSONDecoder().decode(LabDisplayConfiguration.self, from: bytes)
            guard config.room.entry_element_id == id, config.entry_selection_required == false else {
                throw CaptureError.invalid("Сервис не подтвердил выбранную дверь")
            }
            guard self.pendingSegment == pendingSegment, self.assignment?.id == assignment.id else { return }
            meshes = config.meshes
            try accept(await request("sessions/\(assignment.id)"))
            guard self.generation == generation else { return }
            try await activate(assignment: assignment, segment: pendingSegment, store: pendingStore)
            choosingEntry = false; self.pendingSegment = nil; self.pendingStore = nil
        } catch { guard self.generation == generation else { return }; status = "Не удалось выбрать вход: \(error.localizedDescription)" }
    }
    private func activate(assignment: InspectorAssignment, segment: String, store: ObservationStore) async throws {
        let generation = self.generation
        let bytes = try await request("sessions/\(assignment.id)/start", body: ["segment_id":segment])
        guard self.generation == generation else { throw CancellationError() }
        try accept(bytes)
        self.segment = segment; accepted = []; sequence = 0; lastPose = -.infinity
        status = "Наведите камеру на выбранную дверь и сохраните первый снимок."
        poll = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
                guard let self, self.segment == segment else { return }
                await self.flush(store: store)
                do {
                    let bytes = try await self.request("sessions/\(assignment.id)")
                    guard self.segment == segment else { return }
                    try self.accept(bytes)
                } catch { self.receivedAt = .distantPast; self.status = "Нет связи с локализацией. Фото сохраняются; подключение повторяется." }
            }
        }
    }
    func stop() {
        generation = UUID(); entryBusy = false
        poll?.cancel(); poll = nil; segment = nil; receivedAt = .distantPast
        choosingEntry = false; pendingSegment = nil; pendingStore = nil
    }
    var instruction: SpatialInstruction? {
        guard Date().timeIntervalSince(receivedAt) < 2 else { return nil }
        return state?.instructions.first { $0.status == "active" && $0.source == "localization" }
    }
    private func request(_ path: String, body: [String:Any]? = nil) async throws -> Data {
        guard let base else { throw CaptureError.invalid("Настройте Localization Service") }
        var req = URLRequest(url: base.appendingPathComponent("v1/localization/" + path)); req.timeoutInterval = 30
        req.setValue(code, forHTTPHeaderField: "X-Localization-Code")
        if let body { req.httpMethod = "POST"; req.setValue("application/json", forHTTPHeaderField: "Content-Type"); req.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (bytes,response) = try await http.data(for: req)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            let message = ((try? JSONSerialization.jsonObject(with: bytes)) as? [String:Any])?["message"] as? String
            throw CaptureError.invalid(message ?? "Localization Service не подтвердил запрос")
        }
        return bytes
    }
    private func accept(_ bytes: Data) throws {
        let next = try JSONDecoder().decode(LabState.self, from: bytes)
        guard let assignment, let binding = next.assignment_binding,
              sameUUID(next.session_id, assignment.id), sameUUID(binding.session_id,assignment.id),
              binding.project_id == assignment.project_id, binding.room_id == assignment.room_id,
              binding.model_version == assignment.model_version else { throw CaptureError.invalid("Локализация относится к другому заданию") }
        if let state, next.revision < state.revision { return }
        state = next; receivedAt = Date()
        status = next.guidance.phase == "inspecting" ? "Привязка подтверждена. Снимайте непокрытые участки." : "Следуйте инструкции локализации."
    }
    func flush(store: ObservationStore) async {
        guard !draining, let assignment, let segment else { return }
        draining = true; defer { draining = false }
        do {
            let frames = try store.observations(assignment.id).filter { $0.segment_id == segment && !accepted.contains($0.id) }
            for frame in frames {
                guard self.segment == segment, !Task.isCancelled else { return }
                var files: [String:String] = [:]
                let folder = store.folder(frame.session_id).appendingPathComponent("observations/" + frame.id)
                for name in ["frame.json","rgb.jpg","depth.f32"] + (frame.confidence == nil ? [] : ["confidence.u8"]) {
                    files[name] = try Data(contentsOf: folder.appendingPathComponent(name)).base64EncodedString()
                }
                let result = try await request("sessions/\(assignment.id)/observations", body: ["segment_id":segment,"files":files])
                guard self.segment == segment else { return }
                try accept(result); accepted.insert(frame.id)
            }
        } catch { receivedAt = .distantPast; status = "Фото сохранено. Передача в локализацию повторится: \(error.localizedDescription)" }
    }
    func pose(_ frame: ARFrame) {
        guard let assignment, let segment, !sending, !draining, frame.timestamp-lastPose >= 0.2 else { return }
        lastPose = frame.timestamp; sequence += 1; sending = true
        let tracking: String
        switch frame.camera.trackingState { case .normal: tracking = "normal"; case .limited: tracking = "limited"; case .notAvailable: tracking = "not_available" }
        let body: [String:Any] = ["session_id":assignment.id,"segment_id":segment,"sequence":sequence,"frame_timestamp_s":frame.timestamp,"tracking_state":tracking,"T_arkit_camera":matrixRows(frame.camera.transform)]
        Task {
            defer { sending = false }
            do {
                let bytes = try await request("sessions/\(assignment.id)/pose", body: body)
                guard self.segment == segment else { return }
                try accept(bytes)
            } catch { receivedAt = .distantPast }
        }
    }
}

struct SessionLocalizationPanel: View {
    @ObservedObject var localization: SessionLocalization
    @ObservedObject var capture: CaptureService
    var body: some View {
        if localization.enabled {
            VStack(alignment: .leading, spacing: 10) {
                Text(localization.status).font(.headline)
                if localization.choosingEntry, let doors = localization.state?.room.doors {
                    ForEach(doors) { door in
                        Button(door.label + (door.name.isEmpty ? "" : " · " + door.name)) {
                            Task { await localization.chooseEntry(door.id) }
                        }.buttonStyle(.bordered).disabled(localization.entryBusy)
                    }
                }
                TimelineView(.periodic(from: .now, by: 0.5)) { time in
                    if time.date.timeIntervalSince(localization.receivedAt) < 2, let instruction = localization.instruction {
                        Text(instruction.text).padding().background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                    }
                    if let state = localization.state {
                        LabRoom3DPanel(state: state, meshes: localization.meshes, live: capture.running && time.date.timeIntervalSince(localization.receivedAt) < 2)
                    }
                }
            }
        }
    }
}

struct LocalizationSettingsView: View {
    var body: some View {
        Form {
            Text("Localization Service подключается автоматически через Inspector при открытии камеры задания с BIM.")
            Text("Модель берётся из задания. Пока поддерживается одна прямоугольная комната: четыре стены, пол, окна и несколько дверей; размеры IFC в метрах. Начальную дверь выбираете на модели.")
        }.navigationTitle("Локализация")
    }
}
