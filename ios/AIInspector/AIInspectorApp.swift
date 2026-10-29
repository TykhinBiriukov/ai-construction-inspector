import SwiftUI

// Shared palette matches the Inspector workspace; capture retains its native camera preview.
enum InspectorStyle {
    static let paper = Color(red: 0.980, green: 0.976, blue: 0.965)
    static let ink = Color(red: 0.149, green: 0.243, blue: 0.188)
    static let sage = Color(red: 0.631, green: 0.725, blue: 0.467)
    static let line = Color(red: 0.906, green: 0.906, blue: 0.875)
}

@main
struct AIInspectorApp: App {
    @UIApplicationDelegateAdaptor(InspectorAppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup {
            AssignmentWaitingView().tint(InspectorStyle.ink).preferredColorScheme(.light)
        }
    }
}

private struct AssignmentWaitingView: View {
    @ObservedObject private var connection = InspectorConnection.shared
    @State private var showStorage = false
    @State private var standalone: InspectorAssignment?
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        Group {
            if let standalone {
                StandaloneCaptureView(assignment: standalone, restart: {
                    self.standalone = StandaloneCapture.assignment()
                }) {
                    self.standalone = nil
                    connection.localLabActive = false
                    connection.setForeground(true)
                }.id(standalone.id)
            } else if let assignment = connection.active {
                InspectorView(assignment: assignment).id(assignment.id)
            } else {
                NavigationStack {
                    VStack(alignment: .leading, spacing: 28) {
                        HStack(spacing: 10) {
                            Image("InspectorMark").resizable().scaledToFit().frame(width: 48, height: 48)
                            Text("inspector").font(.system(size: 32, weight: .semibold, design: .serif))
                        }.foregroundStyle(InspectorStyle.ink)
                        Spacer()
                        Text("Готовы к съёмке").font(.system(size: 34, design: .serif))
                        VStack(alignment: .leading, spacing: 10) {
                            Label("Ожидаем задание", systemImage: "antenna.radiowaves.left.and.right")
                                .font(.headline)
                            Text("Когда Inspector назначит комнату, задание появится здесь.")
                                .foregroundStyle(.secondary)
                        }
                        Divider()
                        Button {
                            connection.localLabActive = true
                            connection.setForeground(false)
                            standalone = StandaloneCapture.assignment()
                        } label: {
                            HStack { Label("Свободная съёмка", systemImage: "camera"); Spacer(); Image(systemName: "arrow.up.right") }
                                .padding(12).frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent)
                        Text("Фото и метаданные без задания и привязки к BIM.")
                            .font(.footnote).foregroundStyle(.secondary)
                        Spacer()
                        Text(connection.status).font(.caption).foregroundStyle(.secondary)
                    }.padding(24).background(InspectorStyle.paper)
                        .toolbar { Button { showStorage = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("Настройки") }
                        .toolbarBackground(InspectorStyle.paper, for: .navigationBar)
                }
            }
        }
        .sheet(isPresented: $showStorage) { StorageConnectionView() }
        .task { connection.setForeground(true) }
        .onChange(of: scenePhase) { _, phase in connection.setForeground(phase == .active) }
    }
}

struct InspectorView: View {
    @StateObject private var model: InspectorModel
    @ObservedObject private var connection = InspectorConnection.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var showEvidence = false
    @State private var showModel = false
    @State private var showSettings = false
    @State private var showFinish = false
    @State private var showExit = false
    init(assignment: InspectorAssignment) {
        _model = StateObject(wrappedValue: InspectorModel(assignment: assignment))
    }
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !connection.frozen {
                    CapturePanel(model: model, capture: model.capture, showModel: $showModel)
                    CaptureControls(model: model, showEvidence: $showEvidence) { showFinish = true }
                        .disabled(connection.busy)
                } else {
                    VStack(spacing: 24) {
                        Spacer()
                        Image(systemName: "arrow.up.circle").font(.system(size: 48, weight: .ultraLight))
                        Text("Сохраняем съёмку").font(.system(size: 30, design: .serif))
                        Text(connection.status).font(.callout).multilineTextAlignment(.center)
                        if let progress = connection.uploadProgress { ProgressView(value: progress) }
                        else if connection.busy { ProgressView() }
                        Button("Повторить отправку") { finish() }.buttonStyle(.borderedProminent).disabled(connection.busy)
                        Button("Посмотреть фото") { showEvidence = true }
                        Text("Оригиналы остаются на телефоне").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                    }.padding(24).frame(maxWidth: .infinity)
                }
            }
            .background(InspectorStyle.paper)
            .navigationTitle(model.assignment.room_id).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(InspectorStyle.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button { showExit = true } label: { Image(systemName: "xmark") }.accessibilityLabel("Выйти из задания").disabled(model.busy || connection.busy) }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Фото", systemImage: "photo.on.rectangle") { showEvidence = true }
                        Button("Настройки", systemImage: "gearshape") { showSettings = true }
                        Button("Скопировать ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = model.assignment.id }
                        Button("Выйти из задания", systemImage: "xmark.circle", role: .destructive) { showExit = true }
                    } label: { Image(systemName: "ellipsis") }.disabled(model.busy || connection.busy)
                }
            }
            .confirmationDialog("Завершить съёмку?", isPresented: $showFinish, titleVisibility: .visible) {
                Button("Завершить и отправить") { finish() }
            } message: { Text("Будут отправлены все сохранённые фото и метаданные. После этого набор нельзя дополнить.") }
            .confirmationDialog("Выйти из задания?", isPresented: $showExit, titleVisibility: .visible) {
                Button("Выйти и отменить задание", role: .destructive) {
                    model.pause()
                    Task { if let error = await connection.cancelActive() { model.error = error } }
                }
            } message: { Text("Задание будет отменено в Inspector. Локальные фото останутся на телефоне. Для отмены нужна связь с сервером.") }
            .task { await model.load(); model.frozen = connection.frozen }
            .onDisappear { model.pause() }
            .onChange(of: connection.frozen) { _, value in model.frozen = value; if value { model.pause() } }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .sheet(isPresented: $showModel) { BIMViewer(localization: model.localization, capture: model.capture) }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showSettings) { StorageConnectionView() }
            .alert("Не удалось выполнить действие", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
    private func finish() {
        model.pause(); model.frozen = true
        Task { await connection.finish(store: model.store, retryRejected: true); model.frozen = connection.frozen }
    }
}

private struct CapturePanel: View {
    @ObservedObject var model: InspectorModel
    @ObservedObject var capture: CaptureService
    @Binding var showModel: Bool
    var body: some View {
        ZStack {
            if model.synthetic { RoomPreview() }
            else {
                CameraPreview(session: capture.session)
                if !capture.running { Color.black.opacity(0.72) }
            }
            Image(systemName: "viewfinder").font(.system(size: 44, weight: .ultraLight))
                .foregroundStyle(.white.opacity(0.65)).allowsHitTesting(false)
            VStack(spacing: 12) {
                HStack(alignment: .top) {
                    Label(model.synthetic ? "Синтетическое демо" : (capture.supportsDepth ? "Фото + LiDAR" : "Фото · без глубины"),
                          systemImage: model.synthetic ? "cube" : "camera")
                        .font(.caption.weight(.medium)).padding(10).background(.regularMaterial, in: Capsule())
                    Spacer()
                    if !model.standalone {
                        CaptureMapButton(localization: model.localization, capture: capture) { showModel = true }
                    }
                }
                Spacer()
                if !model.synthetic && !capture.running {
                    Button { Task { await model.startCamera() } } label: {
                        Label(model.busy ? "Подготовка…" : "Включить камеру", systemImage: "camera")
                            .padding(8)
                    }.buttonStyle(.borderedProminent).disabled(model.busy || model.manifest == nil)
                    Spacer()
                }
                if !model.standalone {
                    CaptureGuidance(localization: model.localization, showModel: $showModel)
                }
                if capture.running {
                    HStack {
                        Text(capture.status).font(.caption).lineLimit(2)
                        Spacer()
                        Button("Пауза") { model.pause() }.font(.caption.bold()).disabled(model.busy)
                    }.padding(12).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
            }.padding(16)
        }.frame(maxWidth: .infinity, maxHeight: .infinity).clipped()
    }
}

private struct CaptureGuidance: View {
    @ObservedObject var localization: SessionLocalization
    @Binding var showModel: Bool
    var body: some View {
        if localization.enabled {
            TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                VStack(alignment: .leading, spacing: 8) {
                    if localization.choosingEntry {
                        Button { showModel = true } label: { Label("Выберите входную дверь", systemImage: "door.left.hand.open") }
                    } else {
                        Text(localization.instruction?.text ?? localization.status).font(.subheadline)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(14)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
    }
}

private struct CaptureControls: View {
    @ObservedObject var model: InspectorModel
    @Binding var showEvidence: Bool
    let finish: () -> Void
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Button { showEvidence = true } label: {
                    Image(systemName: "photo.on.rectangle").font(.title2).frame(width: 56, height: 56)
                }.accessibilityLabel("Посмотреть фото")
                Spacer()
                Button {
                    model.instructionID = nil
                    Task { await model.takeObservation() }
                } label: {
                    ZStack {
                        Circle().stroke(InspectorStyle.ink.opacity(0.3), lineWidth: 2).frame(width: 76, height: 76)
                        Circle().fill(InspectorStyle.ink).frame(width: 64, height: 64)
                        if model.busy { ProgressView().tint(.white) }
                        else { Image(systemName: "camera.fill").foregroundStyle(.white) }
                    }
                }.accessibilityLabel(model.synthetic ? "Сохранить синтетическое наблюдение" : "Сделать фото")
                    .disabled(model.busy || model.frozen || model.manifest == nil || (!model.synthetic && model.segment == nil))
                Spacer()
                Text("\(model.observations.count) фото").font(.subheadline.monospacedDigit()).frame(minWidth: 56)
            }
            Divider()
            Button("Завершить", action: finish).frame(maxWidth: .infinity, minHeight: 36)
                .disabled(model.busy || model.observations.isEmpty)
        }.padding(.horizontal, 24).padding(.vertical, 12).background(InspectorStyle.paper)
    }
}

private struct EvidenceView: View {
    @ObservedObject var model: InspectorModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Локальные оригиналы сохраняются после загрузки. Статус отправки и приёма Inspector показан на экране сессии.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if model.observations.isEmpty {
                    ContentUnavailableView("Пока нет наблюдений", systemImage: "camera", description: Text("Сохраните первый снимок на основном экране."))
                }
                ForEach(model.observations.reversed()) { frame in
                    NavigationLink {
                        ObservationDetail(frame: frame, folder: model.observationURL(frame))
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(frame.capture_origin == "synthetic" ? "Тестовое наблюдение" : (frame.depth == nil ? "Фото без глубины" : "Снимок RGB-D"), systemImage: "photo")
                            Text(frame.saved_at.formatted(date: .abbreviated, time: .standard)).font(.caption).foregroundStyle(.secondary)
                            Text("\(frame.rgb_size[0]) × \(frame.rgb_size[1]) · " + (frame.depth_size.map { "depth \($0[0]) × \($0[1])" } ?? "без глубины"))
                                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }.padding(.vertical, 4)
                    }
                }
            }.navigationTitle("Наблюдения").toolbar { Button("Готово") { dismiss() } }
        }
    }
}

private struct ObservationDetail: View {
    let frame: FrameMetadata
    let folder: URL
    var body: some View {
        List {
            if let image = UIImage(contentsOfFile: folder.appendingPathComponent("rgb.jpg").path) {
                Image(uiImage: image).resizable().scaledToFit()
            }
            LabeledContent("Источник", value: frame.capture_origin)
            LabeledContent("Depth", value: frame.depth == nil ? "Отсутствует" : frame.depth_source)
            LabeledContent("Единицы", value: frame.depth_unit ?? "—")
            LabeledContent("Сегмент", value: String(frame.segment_id.prefix(8)))
            LabeledContent("Confidence", value: frame.confidence == nil ? "Отсутствует" : "Сохранена")
            if let instruction = frame.instruction_id { LabeledContent("Задание", value: instruction) }
            Section("Экспорт исходных файлов") {
                ForEach(["frame.json", "rgb.jpg"] + (frame.depth == nil ? [] : ["depth.f32"]) + (frame.confidence == nil ? [] : ["confidence.u8"]), id: \.self) { name in
                    ShareLink(item: folder.appendingPathComponent(name)) { Label(name, systemImage: "square.and.arrow.up") }
                }
            }
            Text(frame.id).font(.caption.monospaced()).textSelection(.enabled)
        }.navigationTitle("Наблюдение").navigationBarTitleDisplayMode(.inline)
    }
}


private struct StandaloneCaptureView: View {
    @StateObject private var model: InspectorModel
    @StateObject private var upload = StandaloneCapture()
    @State private var showModel = false
    @State private var showEvidence = false
    @State private var showSettings = false
    @State private var copiedSessionID = false
    @State private var showFinish = false
    @State private var showReset = false
    @Environment(\.scenePhase) private var scenePhase
    let restart: () -> Void
    let close: () -> Void
    init(assignment: InspectorAssignment, restart: @escaping () -> Void, close: @escaping () -> Void) {
        self.restart = restart
        _model = StateObject(wrappedValue: InspectorModel(assignment: assignment, standalone: true))
        self.close = close
    }
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !model.frozen && !upload.busy {
                    CapturePanel(model: model, capture: model.capture, showModel: $showModel)
                    CaptureControls(model: model, showEvidence: $showEvidence) { showFinish = true }
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 24) {
                            Image(systemName: upload.manifestKey == nil ? "arrow.up.circle" : "checkmark.circle")
                                .font(.system(size: 50, weight: .ultraLight)).foregroundStyle(InspectorStyle.ink)
                            Text(upload.manifestKey == nil ? "Сохраняем съёмку" : "Съёмка сохранена")
                                .font(.system(size: 34, design: .serif))
                            Text("\(model.observations.count) фото и метаданные").foregroundStyle(.secondary)
                            Button { showEvidence = true } label: {
                                HStack(spacing: 8) {
                                    ForEach(Array(model.observations.suffix(3))) { frame in
                                        if let image = UIImage(contentsOfFile: model.observationURL(frame).appendingPathComponent("rgb.jpg").path) {
                                            Image(uiImage: image).resizable().scaledToFill().frame(width: 90, height: 90).clipped().clipShape(RoundedRectangle(cornerRadius: 10))
                                        }
                                    }
                                }
                            }.accessibilityLabel("Посмотреть фото")
                            if let key = upload.manifestKey {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text("ID сессии").font(.subheadline).foregroundStyle(.secondary)
                                    Text(model.assignment.id).font(.body.monospaced()).textSelection(.enabled)
                                    Button {
                                        UIPasteboard.general.string = model.assignment.id; copiedSessionID = true
                                    } label: {
                                        Label(copiedSessionID ? "ID скопирован" : "Скопировать ID", systemImage: copiedSessionID ? "checkmark" : "doc.on.doc")
                                            .frame(maxWidth: .infinity).padding(8)
                                    }.buttonStyle(.borderedProminent)
                                }
                                DisclosureGroup("Путь в Supabase") {
                                    Text("inspection-captures / " + key).font(.caption.monospaced()).textSelection(.enabled)
                                    ShareLink(item: key) { Label("Поделиться путём", systemImage: "square.and.arrow.up") }
                                }
                                Text("Оригиналы остались на телефоне").font(.footnote).foregroundStyle(.secondary)
                                Button("Новая съёмка") {
                                    UserDefaults.standard.removeObject(forKey: StandaloneCapture.sessionKey); close()
                                }.buttonStyle(.borderedProminent)
                                Button("На главную") { close() }
                            } else {
                                Text(upload.status).font(.callout)
                                if upload.busy { ProgressView(value: upload.progress) }
                                Button("Повторить загрузку") { Task { await upload.upload(model) } }
                                    .buttonStyle(.borderedProminent).disabled(upload.busy)
                            }
                        }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }.background(InspectorStyle.paper)
            .navigationTitle("Свободная съёмка").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(InspectorStyle.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button { model.pause(); close() } label: { Image(systemName: "xmark") }.accessibilityLabel("Закрыть, сохранив фото").disabled(upload.busy || model.busy) }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Настройки", systemImage: "gearshape") { showSettings = true }
                        Button("Сбросить и начать заново", systemImage: "arrow.counterclockwise", role: .destructive) { showReset = true }
                    } label: { Image(systemName: "ellipsis") }
                        .accessibilityLabel("Действия со съёмкой").disabled(upload.busy || model.busy)
                }
            }
            .confirmationDialog("Завершить съёмку?", isPresented: $showFinish, titleVisibility: .visible) {
                Button("Сохранить в Supabase") { Task { await upload.upload(model) } }
                Button("Посмотреть фото") { showEvidence = true }
                Button("Сбросить и начать заново", role: .destructive) { showReset = true }
            } message: { Text("После начала загрузки набор фиксируется. Оригиналы сохранятся на телефоне.") }
            .alert("Удалить текущую съёмку?", isPresented: $showReset) {
                Button("Удалить и начать заново", role: .destructive) {
                    do {
                        try model.discardStandaloneCapture()
                        UserDefaults.standard.removeObject(forKey: StandaloneCapture.sessionKey)
                        restart()
                    } catch { model.error = error.localizedDescription }
                }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("Все фото и метаданные этой сессии на телефоне будут удалены без отправки. Откроется пустая сессия с новым ID. Уже загруженные в Supabase файлы не удаляются.")
            }
            .task { await model.load(); upload.restore(model) }
            .onDisappear { model.pause() }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showSettings) { StorageConnectionView() }
            .alert("Не удалось выполнить действие", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
}
