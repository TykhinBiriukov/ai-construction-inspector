import SwiftUI

@main
struct AIInspectorApp: App {
    @UIApplicationDelegateAdaptor(InspectorAppDelegate.self) private var appDelegate
    var body: some Scene { WindowGroup { AssignmentWaitingView() } }
}

private struct AssignmentWaitingView: View {
    @ObservedObject private var connection = InspectorConnection.shared
    @State private var showStorage = false
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        Group {
            if let assignment = connection.active {
                InspectorView(assignment: assignment).id(assignment.id)
            } else {
                NavigationStack {
                    VStack(spacing: 16) {
                        Spacer()
                        Image(systemName: "tray").font(.system(size: 52, weight: .light)).foregroundStyle(.secondary)
                        Text("Нет активной сессии").font(.title2.bold())
                        Text("Ожидаем задание от Inspector").foregroundStyle(.secondary)
                        Spacer()
                    }.frame(maxWidth: .infinity).padding(24)
                        .navigationTitle("AI Inspector").navigationBarTitleDisplayMode(.inline)
                        .toolbar { Button { showStorage = true } label: { Image(systemName: "gearshape") } }
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
    init(assignment: InspectorAssignment) {
        _model = StateObject(wrappedValue: InspectorModel(assignment: assignment))
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(model.assignment.room_id).font(.largeTitle.bold())
                    Text("BIM: " + model.assignment.model_version).foregroundStyle(.secondary)
                    if !connection.frozen {
                        CapturePanel(model: model, capture: model.capture, showModel: $showModel)
                    }
                    Text("Снимите помещение согласно заданию инспекции.")
                    Text("Наблюдений на телефоне: \(model.observations.count)")
                    Button("Посмотреть наблюдения") { showEvidence = true }
                    Text(connection.status).font(.footnote)
                    if connection.geometryMode == "mock" {
                        Text("Geometry: MOCK — тест обмена данными, не реконструкция комнаты.").font(.footnote).foregroundStyle(.orange)
                    }
                    if model.capture.supportsMesh && !model.synthetic {
                        Button("Подготовить ZIP для Geometry") { Task { await model.exportGeometry() } }
                            .disabled(model.busy || connection.busy || connection.frozen)
                        if let zip = model.geometryZIP { ShareLink("Экспортировать ZIP", item: zip) }
                        Text("ZIP сохраняется локально. Загрузка архива требует поддержки в Inspector.").font(.caption).foregroundStyle(.secondary)
                    }
                    if connection.frozen {
                        Text("Набор зафиксирован. Ожидаем подтверждение Inspector.").font(.headline)
                    }
                    Button(connection.frozen ? "Повторить отправку" : "Завершить съёмку и отправить") {
                        model.pause(); model.frozen = true
                        Task {
                            await connection.finish(store: model.store, retryRejected: true)
                            model.frozen = connection.frozen
                        }
                    }.buttonStyle(.bordered).disabled(model.busy || connection.busy || model.observations.isEmpty)
                    Text("SESSION " + model.assignment.id).font(.caption.monospaced())
                }.padding(20)
            }
            .navigationTitle("Инспекция").navigationBarTitleDisplayMode(.inline)
            .toolbar { Button { showSettings = true } label: { Image(systemName: "gearshape") } }
            .safeAreaInset(edge: .bottom) {
                if !connection.frozen {
                    Button {
                        model.instructionID = nil
                        Task { await model.takeObservation() }
                    } label: {
                        Label(model.synthetic ? "Сохранить синтетическое наблюдение" : "Снять наблюдение", systemImage: "camera.fill")
                            .frame(maxWidth: .infinity).padding(12)
                    }.buttonStyle(.borderedProminent).padding()
                        .disabled(model.busy || model.manifest == nil)
                }
            }
            .task { await model.load(); model.frozen = connection.frozen }
            .onDisappear { model.pause() }
            .onChange(of: connection.frozen) { _, value in model.frozen = value; if value { model.pause() } }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showSettings) { StorageConnectionView() }
            .alert("Не удалось выполнить действие", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
}

private struct CapturePanel: View {
    @ObservedObject var model: InspectorModel
    @ObservedObject var capture: CaptureService
    @Binding var showModel: Bool

    var body: some View {
        ZStack {
            if model.synthetic {
                RoomPreview()
            } else {
                CameraPreview(session: capture.session)
                if !capture.running { Color.black.opacity(0.7) }
            }
            VStack {
                HStack {
                    Label(model.synthetic ? "СИНТЕТИЧЕСКОЕ ДЕМО" : (capture.supportsDepth ? "КАМЕРА + LiDAR" : "КАМЕРА · БЕЗ LiDAR"), systemImage: model.synthetic ? "cube" : "viewfinder")
                        .font(.system(size: 10, weight: .bold)).padding(10)
                        .background(.regularMaterial, in: Capsule())
                    Spacer()

                }
                Spacer()
                if !model.synthetic && !capture.running {
                    Button { Task { await model.startCamera() } } label: {
                        Label("Включить камеру", systemImage: "camera")
                    }.buttonStyle(.borderedProminent).disabled(model.busy || model.manifest == nil)
                    Spacer()
                }
                VStack(spacing: 5) {
                    Text(model.synthetic ? "Предпросмотр тестовой комнаты" : capture.status).font(.subheadline.weight(.semibold))
                    Text(model.synthetic ? "Кнопка сохранит тестовую плоскость, не скан комнаты" : (capture.supportsDepth ? "Один снимок · RGB, depth, confidence, pose" : "Фото + положение камеры · глубина отсутствует"))
                        .font(.caption)
                    if capture.running {
                        Button("Остановить камеру") { model.pause() }.font(.caption.bold())
                    }
                }.multilineTextAlignment(.center).foregroundStyle(.primary)
                    .frame(maxWidth: .infinity).padding(12).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
            }.padding(12)
        }.frame(height: 350).clipShape(RoundedRectangle(cornerRadius: 24))
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
