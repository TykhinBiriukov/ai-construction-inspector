import Foundation
import Combine
import UIKit

@MainActor
final class InspectorConnection: ObservableObject {
    static let shared = InspectorConnection()
    static let defaultEndpoint = "https://inspector.campuscompile.eu"
    static let defaultDevice = "iphone-1"
    @Published private(set) var active: InspectorAssignment?
    @Published private(set) var status = "Inspector не настроен"
    @Published private(set) var uploadProgress: Double?
    @Published private(set) var busy = false
    @Published private(set) var frozen = false
    @Published private(set) var geometryMode = "unknown"
    @Published private(set) var spatial: SpatialSnapshot?
    @Published private(set) var spatialStatus = "Онлайн-локализация не подключена"
    @Published private(set) var localizationEnabled = false
    @Published private(set) var spatialFresh = false
    private var liveCapability: LiveLocalizationCapability?
    private var capabilitiesChecked = Date.distantPast
    private var spatialReceived = Date.distantPast
    private var spatialRefreshing = false
    private var poseSending = false
    private var lastPoseTime = -Double.infinity
    private var lastPoseTracking = ""
    private var desiredSpatialSegment: String?
    private var registeredSpatialSegment: String?
    private var poseSegment: String?
    private var poseSequence = 0
    private var spatialLoop: Task<Void, Never>?
    private var journal = CaptureJournal()
    private var journalURL: URL?
    private var base: URL?
    private var device = ""
    private var scope = ""
    private var loop: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var reader: Task<Void, Never>?
    private let delegate = StorageHTTP()
    private let storage = SignedStorageTransport()
    private lazy var http = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
    var localLabActive = false
    private var foreground = false
    private var reconciling = false
    private var failures = 0
    private var completionFailures = 0
    private var nextCompletionAttempt = Date.distantPast

    func configure(endpoint: String, device: String, token: String) throws {
        guard !busy, active == nil || (endpoint == base?.absoluteString && device == self.device) else { throw CaptureError.invalid("Нельзя менять сервер во время активной сессии") }
        let endpoint = endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: endpoint), url.scheme == "https", url.host != nil, url.path.isEmpty, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              device.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { throw CaptureError.invalid("Нужны HTTPS-адрес сервера и device_id") }
        if !token.isEmpty { try InspectorCredential.save(token, scope: endpoint + "|" + device) }
        UserDefaults.standard.set(endpoint, forKey: "inspector.endpoint"); UserDefaults.standard.set(device, forKey: "inspector.device")
        setForeground(true)
    }
    private func disconnect() {
        spatialFresh = false
        reader?.cancel(); heartbeat?.cancel(); socket?.cancel(with: .goingAway, reason: nil); socket = nil
    }
    func setForeground(_ value: Bool) {
        let value = value && !localLabActive
        foreground = value; loop?.cancel(); spatialLoop?.cancel(); disconnect()
        guard value else { return }
        failures = 0
        spatialLoop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshSpatial()
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                _ = await reconcile()
                let seconds = min(60, 10 * (1 << min(failures, 3)))
                do { try await Task.sleep(for: .seconds(seconds)) } catch { return }
            }
        }
    }
    private func prepare() throws {
        let endpoint = UserDefaults.standard.string(forKey: "inspector.endpoint") ?? Self.defaultEndpoint
        let id = UserDefaults.standard.string(forKey: "inspector.device") ?? Self.defaultDevice
        guard let url = URL(string: endpoint), url.scheme == "https", url.host != nil, url.path.isEmpty, url.user == nil, url.password == nil, url.query == nil,
              id.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { throw CaptureError.invalid("Настройте Inspector") }
        let newScope = endpoint + "|" + id
        _ = try InspectorCredential.read(scope: newScope)
        guard newScope != scope else { return }
        guard active == nil else { throw CaptureError.invalid("Восстановите настройки активной сессии") }
        let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("inspector-signed-v1")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(StoredFile(name: "", data: Data(newScope.utf8)).sha256 + ".json")
        journal = FileManager.default.fileExists(atPath: file.path) ? try ObservationStore.decoder.decode(CaptureJournal.self, from: Data(contentsOf: file)) : CaptureJournal()
        liveCapability = nil; localizationEnabled = false; spatial = nil; geometryMode = "unknown"
        base = url; device = id; scope = newScope; journalURL = file
        active = journal.active; frozen = journal.pending != nil || (active.map { !$0.canCapture } ?? false)
    }
    private func save() throws {
        guard let journalURL else { throw CaptureError.invalid("Нет журнала сессии") }
        try ObservationStore.encoder.encode(journal).write(to: journalURL, options: .atomic)
    }
    private func request(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        guard let base else { throw CaptureError.invalid("Нет сервера") }
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        let pieces = path.split(separator: "?", maxSplits: 1); components.path = "/" + pieces[0]
        if pieces.count == 2 { components.percentEncodedQuery = String(pieces[1]) }
        var req = URLRequest(url: components.url!); req.httpMethod = method; req.httpBody = body; req.timeoutInterval = 30
        req.setValue("Bearer " + (try InspectorCredential.read(scope: scope)), forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await http.data(for: req)
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if !(200..<300).contains(response.statusCode) {
            let error = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let code = error?["code"] as? String ?? "request_failed"
            throw CaptureError.invalid("Inspector HTTP \(response.statusCode): \(code)")
        }
        return data
    }
    /// Independent test upload; never mutates the assigned-session journal or dispatches Geometry.
    func uploadStandalone(sessionID: String, object: UploadObject, bytes: Data, completionID: String?) async throws -> String {
        try prepare()
        let requestID = UUID().uuidString.lowercased()
        struct Body: Encodable {
            let schema_version = "0.1"
            let request_id: String
            let kind: String
            let completion_id: String?
            let objects: [UploadObject]
        }
        let body = try JSONEncoder().encode(Body(request_id: requestID,
            kind: completionID == nil ? "observation_files" : "capture_manifest", completion_id: completionID, objects: [object]))
        func plan() async throws -> UploadPlanResponse.Item {
            let response = try JSONDecoder().decode(UploadPlanResponse.self, from: await request(
                "v1/standalone-captures/\(sessionID)/upload-plans", method: "POST", body: body))
            guard sameUUID(response.session_id, sessionID), sameUUID(response.request_id, requestID),
                  response.format == "supabase_upload_plan_v1", response.items.count == 1,
                  let item = response.items.first, item.name == object.name,
                  item.observation_id == object.observation_id, item.byte_length == bytes.count,
                  item.sha256 == StoredFile(name: object.name, data: bytes).sha256,
                  item.media_type == object.media_type, item.object_key.hasPrefix("standalone/"),
                  item.object_key.contains("/" + sessionID + "/") else {
                throw CaptureError.invalid("Неверный план тестовой загрузки")
            }
            return item
        }
        let item = try await plan()
        if item.status == "upload_required" {
            try await storage.put(bytes: bytes, plan: item)
            let verified = try await plan()
            guard verified.status == "already_uploaded", verified.object_key == item.object_key else {
                throw CaptureError.invalid("Файл отправлен, но проверка Supabase ещё не завершена. Повторите отправку.")
            }
        } else if item.status != "already_uploaded" { throw CaptureError.invalid("Неверный статус загрузки") }
        return item.object_key
    }

    struct LocalizationAccess: Decodable {
        let session_id: String
        let model_version: String
        let endpoint: String
        let session_token: String
    }
    func prepareLocalization(_ assignment: InspectorAssignment) async throws -> LocalizationAccess {
        try prepare()
        let bytes = try await request("v1/sessions/\(assignment.id)/localization", method: "POST", body: Data("{}".utf8))
        let access = try JSONDecoder().decode(LocalizationAccess.self, from: bytes)
        guard sameUUID(access.session_id, assignment.id), access.model_version == assignment.model_version else {
            throw CaptureError.invalid("Inspector выдал локализацию для другого задания")
        }
        return access
    }
    @discardableResult func reconcile() async -> Bool {
        guard foreground, !busy, !reconciling else { return false }
        reconciling = true; defer { reconciling = false }
        do {
            try prepare()
            if geometryMode == "unknown" || Date().timeIntervalSince(capabilitiesChecked) > 30 {
                let data = try await request("v1/capabilities")
                let caps = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                geometryMode = caps?["geometry_mode"] as? String ?? "unknown"
                if let value = caps?["live_localization"], let bytes = try? JSONSerialization.data(withJSONObject: value) {
                    liveCapability = try? JSONDecoder().decode(LiveLocalizationCapability.self, from: bytes)
                }
                localizationEnabled = liveCapability?.supported == true
                capabilitiesChecked = Date()
            }
            if let current = active {
                let latest = try JSONDecoder().decode(InspectorAssignment.self, from: await request("v1/sessions/\(current.id)"))
                guard active?.id == current.id, !busy else { return false }
                try latest.validate(device: device)
                guard latest.sameContext(as: current), latest.revision >= current.revision else { throw CaptureError.invalid("Изменился закреплённый контекст") }
                if latest.capture_state == "cancelled" { try close(current.id); status = "Сессия отменена Inspector" }
                else if journal.pending != nil { await finish(store: Self.store) }
                else if latest.capture_state == "sealed" { try close(current.id); status = "Набор принят; обработка выполняется на сервере" }
                else { journal.active = latest; try save(); active = latest; frozen = !latest.canCapture }
            } else {
                var cursor: String?; var seen = Set<String>(); var chosen: InspectorAssignment?
                repeat {
                    var components = URLComponents(); components.queryItems = [URLQueryItem(name: "limit", value: "100")]
                    if let cursor { components.queryItems?.append(URLQueryItem(name: "after", value: cursor)) }
                    let page = try JSONDecoder().decode(AssignmentPage.self, from: await request("v1/devices/\(device)/capture-assignments?" + (components.percentEncodedQuery ?? "")))
                    for item in page.assignments {
                        try item.validate(device: device)
                        if chosen == nil && item.capture_state == "assigned" && !journal.closedSessionIDs.contains(item.id) { chosen = item }
                    }
                    cursor = page.next_cursor
                    if let cursor, !seen.insert(cursor).inserted { throw CaptureError.invalid("Повтор курсора Inspector") }
                } while cursor != nil
                guard foreground, !localLabActive else { return false }
                if let chosen { journal.active = chosen; try save(); active = chosen; await PushService.shared.notifyAssignment(sessionID: chosen.id) }
            }
            if let active { try await acknowledge(active) }
            connectSocket()
            await refreshSpatial()
            Task { await self.sendEarlyEvidence() }
            if journal.pending == nil { status = active == nil ? "Ожидаем задание от Inspector" : "Сессия подключена" }
            failures = 0; return true
        } catch { failures += 1; status = error.localizedDescription; return false }
    }
    private func acknowledge(_ assignment: InspectorAssignment) async throws {
        if journal.acknowledged.contains(assignment.id) { return }
        let id = journal.ackIDs[assignment.id] ?? UUID().uuidString.lowercased(); journal.ackIDs[assignment.id] = id; try save()
        let body = try JSONSerialization.data(withJSONObject: ["schema_version": "0.1", "operation_id": id], options: .sortedKeys)
        let data = try await request("v1/sessions/\(assignment.id)/acknowledgements", method: "POST", body: body)
        let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard sameUUID(receipt?["session_id"] as? String, assignment.id), sameUUID(receipt?["operation_id"] as? String, id) else { throw CaptureError.invalid("Неверная квитанция получения") }
        journal.acknowledged.append(assignment.id); try save()
    }
    func startCapture() async throws {
        guard let active else { throw CaptureError.invalid("Нет задания") }
        try await acknowledge(active)
        if journal.startedSessionIDs.contains(active.id) { return }
        if journal.startBodies[active.id] == nil {
            journal.startBodies[active.id] = try JSONSerialization.data(withJSONObject: ["schema_version": "0.1", "operation_id": UUID().uuidString.lowercased(),
                "device": ["name": UIDevice.current.model, "os_version": UIDevice.current.systemVersion,
                           "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0"]], options: .sortedKeys)
        }
        try save()
        let body = journal.startBodies[active.id]!
        let expected = try JSONSerialization.jsonObject(with: body) as! [String: Any]
        let data = try await request("v1/sessions/\(active.id)/capture-start", method: "POST", body: body)
        let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard sameUUID(receipt?["session_id"] as? String, active.id), sameUUID(receipt?["operation_id"] as? String, expected["operation_id"] as? String),
              receipt?["capture_state"] as? String == "capturing" else { throw CaptureError.invalid("Неверная квитанция начала") }
        journal.startedSessionIDs.append(active.id); try save()
    }
    private func connectSocket() {
        guard socket == nil, let base, let token = try? InspectorCredential.read(scope: scope) else { return }
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.scheme = "wss"; parts.path = "/v1/devices/\(device)/events/ws"
        if let cursor = journal.eventCursor { parts.queryItems = [URLQueryItem(name: "after", value: cursor)] }
        var req = URLRequest(url: parts.url!); req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        let ws = http.webSocketTask(with: req); socket = ws; ws.resume()
        heartbeat = Task {
            do { while !Task.isCancelled { try await ws.send(.string("{\"type\":\"ping\"}")); try await Task.sleep(for: .seconds(15)) } } catch { }
        }
        reader = Task { [weak self] in
            do {
                while !Task.isCancelled {
                    let message = try await ws.receive()
                    let data: Data
                    switch message { case .data(let bytes): data = bytes; case .string(let text): data = Data(text.utf8); @unknown default: continue }
                    guard let packet = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                    if packet["type"] as? String == "ping" { try await ws.send(.string("{\"type\":\"pong\"}")); continue }
                    if ["pong", "device.connected"].contains(packet["type"] as? String ?? "") { continue }
                    if packet["type"] as? String == "stream.error" || packet["type"] as? String == "error" {
                        self?.journal.eventCursor = nil; try self?.save(); ws.cancel(with: .goingAway, reason: nil); break
                    }
                    // HTTP reconciliation applies full authoritative state; duplicate event packets are harmless.
                    if await self?.reconcile() == true {
                        self?.journal.eventCursor = packet["next_cursor"] as? String ?? packet["resume_cursor"] as? String
                        try self?.save()
                    }
                }
            } catch { }
            if self?.socket === ws { self?.socket = nil; self?.heartbeat?.cancel() }
        }
    }
    static var store: ObservationStore { ObservationStore(root: FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("sessions")) }

    private func upload(bytes: Data, object: UploadObject, logicalID: String, manifest: Bool, archive: Bool = false, geometryPart: Bool = false) async throws -> String {
        guard let current = active, var pending = journal.pending, let assignmentStorage = current.storage,
              assignmentStorage.provider == "supabase", assignmentStorage.bucket == "inspection-captures" else { throw CaptureError.invalid("Inspector не настроил Supabase storage") }
        let requestID = pending.requestIDs[logicalID] ?? UUID().uuidString.lowercased()
        pending.requestIDs[logicalID] = requestID; journal.pending = pending; try save()
        var payload = try JSONSerialization.jsonObject(with: JSONEncoder().encode(object)) as! [String: Any]
        payload["observation_id"] = object.observation_id ?? NSNull() as Any
        let body = try JSONSerialization.data(withJSONObject: ["schema_version": "0.1", "request_id": requestID,
            "kind": geometryPart ? "geometry_archive" : archive ? "capture_archive" : (manifest ? "capture_manifest" : "observation_files"), "completion_id": (manifest || archive || geometryPart) ? pending.completion_id as Any : NSNull(), "objects": [payload]], options: .sortedKeys)
        let response = try JSONDecoder().decode(UploadPlanResponse.self, from: await request("v1/sessions/\(current.id)/upload-plans", method: "POST", body: body))
        guard response.format == "supabase_upload_plan_v1", sameUUID(response.request_id, requestID), sameUUID(response.session_id, current.id),
              response.items.count == 1, let item = response.items.first, item.name == object.name, ((item.observation_id == nil && object.observation_id == nil) || sameUUID(item.observation_id, object.observation_id)),
              item.sha256 == object.sha256, item.byte_length == object.byte_length, item.media_type == object.media_type,
              item.object_key.hasPrefix(assignmentStorage.key_prefix), !item.object_key.split(separator: "/").contains("..") else { throw CaptureError.invalid("План не соответствует файлу") }
        if item.status == "upload_required" { try await storage.put(bytes: bytes, plan: item) }
        else if item.status != "already_uploaded" { throw CaptureError.invalid("Неизвестный статус upload plan") }
        return item.object_key
    }
    func finish(store: ObservationStore, retryRejected: Bool = false) async {
        if journal.completionRejected && !retryRejected { return }
        if !retryRejected && Date() < nextCompletionAttempt { return }
        guard !busy, let current = active else { return }
        busy = true; frozen = true; defer { busy = false }
        do {
            if journal.pending == nil || (journal.completionRejected && retryRejected) {
                journal.completionRejected = false; journal.pending = nil
                journal.pending = try freezeCapture(assignment: current, store: store)
            }
            journal.pending?.useIndividualFilesIfUnsubmitted()
            try save(); try await startCapture()
            guard let frozenSet = journal.pending else { return }
            updateUploadProgress()
            if frozenSet.transportFormat == "geometry_parts" {
                if journal.pending?.geometryParts == nil {
                    let data = try await request("v1/capabilities")
                    let caps = try JSONSerialization.jsonObject(with: data) as? [String:Any]
                    guard (caps?["capture_formats"] as? [String])?.contains("supabase_geometry_parts_v1") == true,
                          let limit = caps?["max_archive_bytes"] as? Int, limit > 65536 else {
                        throw CaptureError.invalid("Inspector ещё не поддерживает несколько Geometry ZIP. Наблюдения сохранены; обновите сервер.")
                    }
                    status = "Подготовка архивов…"
                    let index = try await Task.detached {
                        try GeometryPartsExport.build(assignment: current, pending: frozenSet, store: store, maxBytes: min(limit - 65536, 15 * 1024 * 1024))
                    }.value
                    journal.pending?.geometryParts = index; try save()
                }
                if journal.pending?.manifestBytes == nil {
                    for (i, part) in journal.pending!.geometryParts!.parts.enumerated() {
                        if !part.object_key.isEmpty { continue }
                        status = "Загрузка архивов в хранилище…"
                        let url = store.folder(current.id).appendingPathComponent("geometry-parts/\(frozenSet.completion_id)/\(part.name)")
                        let bytes = try Data(contentsOf: url, options: .mappedIfSafe)
                        guard bytes.count == part.byte_length, StoredFile(name: part.name, data: bytes).sha256 == part.sha256 else { throw CaptureError.invalid("ZIP изменён после подготовки") }
                        let key = try await upload(bytes: bytes, object: UploadObject(observation_id: nil, name: part.name, media_type: "application/zip", byte_length: part.byte_length, sha256: part.sha256), logicalID: part.name, manifest: false, geometryPart: true)
                        journal.pending?.geometryParts?.parts[i].object_key = key; try save(); updateUploadProgress()
                    }
                    let indexBytes = try ObservationStore.encoder.encode(journal.pending!.geometryParts!)
                    journal.pending?.manifestBytes = indexBytes; try save()
                }
                uploadProgress = nil; status = "Сохранение списка архивов…"
                if journal.pending?.request == nil {
                    let bytes = journal.pending!.manifestBytes!, info = StoredFile(name: "capture-manifest.json", data: journal.pending!.manifestBytes!)
                    let key = try await upload(bytes: bytes, object: UploadObject(observation_id: nil, name: info.name, media_type: "application/json", byte_length: info.byte_length, sha256: info.sha256), logicalID: "manifest", manifest: true)
                    journal.pending?.request = CaptureCompletion(completion_id: frozenSet.completion_id,
                        capture_ref: .init(format: "supabase_geometry_parts_v1", storage_id: current.storage!.storage_id, manifest_key: key, manifest_byte_length: info.byte_length, manifest_sha256: info.sha256))
                    try save()
                }
            } else if frozenSet.transportFormat == "zip" {
                if frozenSet.archiveInfo == nil {
                    status = "Подготовка ZIP с наблюдениями…"
                    guard let keyPrefix = current.storage?.key_prefix else { throw CaptureError.invalid("Нет префикса хранилища Inspector") }
                    let result = try await Task.detached {
                        try InspectorArchive.build(pending: frozenSet, store: store, keyPrefix: keyPrefix)
                    }.value
                    journal.pending?.manifest = result.manifest
                    journal.pending?.manifestBytes = result.manifestBytes
                    journal.pending?.archiveInfo = result.info
                    try save()
                }
                guard let info = journal.pending?.archiveInfo else { throw CaptureError.invalid("Не подготовлен ZIP") }
                let file = InspectorArchive.url(sessionID: current.id, completionID: frozenSet.completion_id, store: store)
                let bytes = try Data(contentsOf: file, options: .mappedIfSafe)
                guard bytes.count == info.byte_length, StoredFile(name: "", data: bytes).sha256 == info.sha256 else {
                    throw CaptureError.invalid("ZIP изменён или повреждён. Повторная отправка остановлена.")
                }
                if journal.pending?.request == nil {
                    status = "Загрузка ZIP в Supabase…"
                    let key = try await upload(bytes: bytes, object: UploadObject(observation_id: nil, name: "capture.zip",
                        media_type: "application/zip", byte_length: info.byte_length, sha256: info.sha256), logicalID: "archive", manifest: false, archive: true)
                    journal.pending?.request = CaptureCompletion(completion_id: frozenSet.completion_id,
                        capture_ref: .init(format: "supabase_capture_zip_v1", storage_id: current.storage!.storage_id,
                            archive_key: key, archive_byte_length: info.byte_length, archive_sha256: info.sha256))
                    try save()
                }
            } else {
            if frozenSet.manifestBytes == nil {
                for (oi, observation) in frozenSet.manifest.observations.enumerated() {
                    for (fi, file) in observation.files.enumerated() {
                        // A saved server key means PUT finished; preserve progress across retries.
                        if !file.object_key.isEmpty { continue }
                        let bytes = try Data(contentsOf: store.folder(current.id).appendingPathComponent("observations/" + observation.observation_id + "/" + file.name))
                        guard bytes.count == file.byte_length, StoredFile(name: "", data: bytes).sha256 == file.sha256 else { throw CaptureError.invalid("Локальный файл повреждён") }
                        status = "Загрузка файлов в хранилище…"
                        let object = UploadObject(observation_id: observation.observation_id, name: file.name,
                            media_type: file.name == "rgb.jpg" ? "image/jpeg" : (file.name == "frame.json" ? "application/json" : "application/octet-stream"), byte_length: file.byte_length, sha256: file.sha256)
                        let key = try await upload(bytes: bytes, object: object, logicalID: observation.observation_id + "/" + file.name, manifest: false)
                        journal.pending?.manifest.observations[oi].files[fi].object_key = key; try save()
                        updateUploadProgress()
                    }
                }
                let manifestBytes = try ObservationStore.encoder.encode(journal.pending!.manifest)
                journal.pending?.manifestBytes = manifestBytes; try save()
            }
            uploadProgress = nil
            status = "Отправка манифеста набора…"
            let bytes = journal.pending!.manifestBytes!, info = StoredFile(name: "capture-manifest.json", data: journal.pending!.manifestBytes!)
            if journal.pending?.request == nil {
                let key = try await upload(bytes: bytes, object: UploadObject(observation_id: nil, name: info.name, media_type: "application/json", byte_length: info.byte_length, sha256: info.sha256), logicalID: "manifest", manifest: true)
                let completionID = journal.pending!.completion_id
                journal.pending?.request = CaptureCompletion(completion_id: completionID,
                    capture_ref: .init(storage_id: current.storage!.storage_id, manifest_key: key, manifest_byte_length: info.byte_length, manifest_sha256: info.sha256))
                try save()
            }
            }
            uploadProgress = nil
            status = "Inspector проверяет набор…"
            let pending = journal.pending!
            let data = try await request("v1/sessions/\(current.id)/complete", method: "POST", body: ObservationStore.encoder.encode(pending.request!))
            let receipt = try JSONDecoder().decode(CompletionResponse.self, from: data)
            guard sameUUID(receipt.session_id, current.id), sameUUID(receipt.completion.completion_id, pending.completion_id) else { throw CaptureError.invalid("Неверная квитанция завершения") }
            if receipt.completion.status == "accepted" { try close(current.id); status = "Набор принят Inspector" }
            else if receipt.completion.status == "rejected" { journal.completionRejected = true; try save(); status = "Inspector отклонил набор. Файлы сохранены; можно повторить отправку." }
            else { status = "Файлы отправлены. Inspector проверяет набор." }
        } catch {
            completionFailures += 1
            nextCompletionAttempt = Date().addingTimeInterval(Double(min(60, 2 << min(completionFailures, 5))))
            if journal.pending == nil { frozen = false }; status = error.localizedDescription
        }
    }
    private func updateUploadProgress() {
        guard let pending = journal.pending, pending.transportFormat != "zip", pending.manifestBytes == nil else { uploadProgress = nil; return }
        uploadProgress = pending.fileUploadFraction
    }
    func cancelActive() async -> String? {
        guard !busy, let current = active else { return "Дождитесь завершения текущей операции" }
        busy = true; defer { busy = false }
        do {
            let data = try await request("v1/sessions/\(current.id)/abort", method: "POST")
            let cancelled = try JSONDecoder().decode(InspectorAssignment.self, from: data)
            guard sameUUID(cancelled.id, current.id), cancelled.capture_state == "cancelled" else {
                throw CaptureError.invalid("Inspector ещё не подтвердил отмену")
            }
            try close(current.id)
            status = "Задание отменено. Фото сохранены на телефоне."
            return nil
        } catch { return "Не удалось отменить задание: " + error.localizedDescription }
    }

    private func close(_ id: String) throws {
        uploadProgress = nil
        journal.closedSessionIDs.append(id); journal.active = nil; journal.pending = nil; journal.completionRejected = false
        try save(); endSpatialSegment(); spatial = nil; spatialFresh = false; active = nil; frozen = false; completionFailures = 0; nextCompletionAttempt = .distantPast
    }
}

extension InspectorConnection {
    func registerSpatialSegment(_ segment: String) async throws {
        guard localizationEnabled, let current = active, current.model_version != "unbound" else { return }
        desiredSpatialSegment = segment
        spatialFresh = false
        try await confirmSpatialSegment(segment, current: current)
        await refreshSpatial()
    }
    private func confirmSpatialSegment(_ segment: String, current: InspectorAssignment) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["protocol":"live_bim_v1", "operation_id":segment, "segment_id":segment], options: .sortedKeys)
        let data = try await request("v1/sessions/\(current.id)/spatial-segments", method: "POST", body: body)
        let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard sameUUID(receipt?["session_id"] as? String, current.id), sameUUID(receipt?["segment_id"] as? String, segment) else {
            throw CaptureError.invalid("Неверное подтверждение сегмента локализации")
        }
        guard desiredSpatialSegment == segment, active?.id == current.id else { return }
        registeredSpatialSegment = segment
    }
    func endSpatialSegment() {
        desiredSpatialSegment = nil; registeredSpatialSegment = nil; spatialFresh = false
    }
    func refreshSpatial() async {
        guard foreground, localizationEnabled, let current = active, !frozen, !spatialRefreshing else { return }
        spatialRefreshing = true; defer { spatialRefreshing = false }
        do {
            if let segment = desiredSpatialSegment, registeredSpatialSegment != segment {
                try await confirmSpatialSegment(segment, current: current)
            }
            let snapshot = try JSONDecoder().decode(SpatialSnapshot.self, from: await request("v1/sessions/\(current.id)/spatial-state"))
            guard active?.id == current.id, foreground else { return }
            try snapshot.validate(session: current.id, model: current.model_version, room: current.room_id,
                                  previous: spatial?.session_id == current.id ? spatial : nil)
            spatial = snapshot; spatialReceived = Date(); spatialFresh = true
            spatialStatus = ["awaiting_camera":"Откройте камеру", "localizing":"Определяем положение в BIM",
                             "relocalizing":"Восстанавливаем привязку", "inspecting":"Съёмка под управлением Inspector",
                             "blocked":"Локализация требует внимания", "finished":"Съёмка завершена"][snapshot.guidance.phase] ?? ""
        } catch {
            spatialFresh = false
            spatialStatus = "Локализация недоступна. Фото сохраняются локально."
        }
    }
    func liveInstruction(segment: String?) -> SpatialInstruction? {
        guard spatialFresh, Date().timeIntervalSince(spatialReceived) < 3 else { return nil }
        return spatial?.instruction(segment: segment)
    }
    func sendPose(timestamp: Double, tracking: String, reason: String?, pose: [[Float]], intrinsics: [[Float]], size: [Int], segment: String) {
        guard foreground, localizationEnabled, !frozen, let current = active,
              journal.startedSessionIDs.contains(current.id), registeredSpatialSegment == segment, let socket, !poseSending else { return }
        if poseSegment != segment { poseSegment = segment; poseSequence = 0; lastPoseTime = -.infinity; lastPoseTracking = "" }
        let hz = min(10, liveCapability?.pose_hz ?? 5)
        guard timestamp > lastPoseTime, timestamp - lastPoseTime >= 1 / hz || tracking != lastPoseTracking else { return }
        lastPoseTime = timestamp; lastPoseTracking = tracking; poseSequence += 1
        let message = CameraPose(session_id: current.id, segment_id: segment, sequence: poseSequence,
                                 frame_timestamp_s: timestamp, tracking_state: tracking, tracking_reason: reason,
                                 T_arkit_camera: pose, intrinsics: intrinsics, image_size: size)
        guard let data = try? JSONEncoder().encode(message), let text = String(data: data, encoding: .utf8) else { return }
        poseSending = true
        Task {
            defer { poseSending = false }
            do { try await socket.send(.string(text)) }
            catch { spatialFresh = false }
        }
    }
    /// Persist intent before sending anything; this is independent of the frozen final ZIP journal.
    func enqueueEarly(_ frame: FrameMetadata, store: ObservationStore, instruction: SpatialInstruction?) throws {
        guard localizationEnabled, let current = active, current.id == frame.session_id, current.model_version != "unbound", !frozen else { return }
        guard !(journal.earlyEvidence ?? []).contains(where: { $0.observationID == frame.id }) else { return }
        let folder = store.folder(frame.session_id).appendingPathComponent("observations/" + frame.id)
        let metadata = try Data(contentsOf: folder.appendingPathComponent("frame.json"))
        let files = [StoredFile(name: "frame.json", data: metadata), frame.rgb] + (frame.depth.map { [$0] } ?? []) + (frame.confidence.map { [$0] } ?? [])
        let entry = EarlyEvidence(batchID: UUID().uuidString.lowercased(), sessionID: frame.session_id,
                                  segmentID: frame.segment_id, observationID: frame.id, instructionID: instruction?.instruction_id,
                                  purpose: instruction?.source == "inspector" ? "coverage" : "localization", files: files,
                                  uploadRequestIDs: Dictionary(uniqueKeysWithValues: files.map { ($0.name, UUID().uuidString.lowercased()) }))
        journal.earlyEvidence = (journal.earlyEvidence ?? []) + [entry]
        try save()
        Task { await sendEarlyEvidence() }
    }
    func sendEarlyEvidence() async {
        guard foreground, localizationEnabled, !busy, !frozen, let current = active, let prefix = current.storage?.key_prefix else { return }
        busy = true; defer { busy = false }
        do {
            for index in (journal.earlyEvidence ?? []).indices {
                guard foreground, active?.id == current.id, !frozen else { return }
                guard var entry = journal.earlyEvidence?[index], entry.sessionID == current.id,
                      entry.segmentID == desiredSpatialSegment, entry.segmentID == registeredSpatialSegment, !entry.submitted else { continue }
                if entry.batchBody == nil {
                    var refs: [CaptureFileReference] = []
                    for file in entry.files {
                        let bytes = try Data(contentsOf: Self.store.folder(current.id).appendingPathComponent("observations/\(entry.observationID)/\(file.name)"))
                        guard bytes.count == file.byte_length, StoredFile(name: file.name, data: bytes).sha256 == file.sha256 else { throw CaptureError.invalid("Изменились байты раннего наблюдения") }
                        let mime = file.name == "rgb.jpg" ? "image/jpeg" : (file.name == "frame.json" ? "application/json" : "application/octet-stream")
                        let id = entry.uploadRequestIDs[file.name]!
                        let object = UploadObject(observation_id: entry.observationID, name: file.name, media_type: mime, byte_length: file.byte_length, sha256: file.sha256)
                        let obj = try JSONSerialization.jsonObject(with: JSONEncoder().encode(object))
                        let body = try JSONSerialization.data(withJSONObject: ["schema_version":"0.1", "request_id":id, "kind":"observation_files", "completion_id":NSNull(), "objects":[obj]], options: .sortedKeys)
                        let plan = try JSONDecoder().decode(UploadPlanResponse.self, from: await request("v1/sessions/\(current.id)/upload-plans", method: "POST", body: body))
                        guard plan.format == "supabase_upload_plan_v1", sameUUID(plan.session_id, current.id), sameUUID(plan.request_id, id),
                              plan.items.count == 1, let item = plan.items.first, sameUUID(item.observation_id, entry.observationID),
                              item.name == file.name, item.byte_length == file.byte_length, item.sha256 == file.sha256, item.media_type == mime,
                              item.object_key == prefix + "observations/\(entry.observationID)/\(file.name)" else { throw CaptureError.invalid("Неверный план ранней загрузки") }
                        if item.status == "upload_required" { try await storage.put(bytes: bytes, plan: item) }
                        else if item.status != "already_uploaded" { throw CaptureError.invalid("Неверный статус ранней загрузки") }
                        refs.append(CaptureFileReference(name: file.name, object_key: item.object_key, byte_length: file.byte_length, sha256: file.sha256))
                    }
                    let referenceObjects = try JSONSerialization.jsonObject(with: JSONEncoder().encode(refs))
                    entry.batchBody = try JSONSerialization.data(withJSONObject: ["protocol":"live_bim_v1", "batch_id":entry.batchID,
                        "purpose":entry.purpose, "instruction_id":entry.instructionID as Any? ?? NSNull(), "segment_id":entry.segmentID,
                        "observations":[["observation_id":entry.observationID, "files":referenceObjects]]], options: .sortedKeys)
                    journal.earlyEvidence?[index] = entry; try save()
                }
                let bytes = try await request("v1/sessions/\(current.id)/evidence-batches", method: "POST", body: entry.batchBody)
                let receipt = try JSONSerialization.jsonObject(with: bytes) as? [String: Any]
                guard receipt?["protocol"] as? String == "live_bim_v1", sameUUID(receipt?["session_id"] as? String, current.id), sameUUID(receipt?["batch_id"] as? String, entry.batchID),
                      ["validating", "queued", "processing", "completed"].contains(receipt?["status"] as? String ?? "") else {
                    throw CaptureError.invalid("Неверная квитанция ранних наблюдений")
                }
                entry.submitted = true; journal.earlyEvidence?[index] = entry; try save()
            }
        } catch { spatialStatus = "Ранние данные не отправлены: \(error.localizedDescription). Повтор автоматически; оригиналы сохранены." }
    }
}
