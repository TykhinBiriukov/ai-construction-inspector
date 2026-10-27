import SwiftUI

@main
struct AIInspectorApp: App {
    var body: some Scene { WindowGroup { InspectorView() } }
}

struct InspectorView: View {
    @StateObject private var model = InspectorModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var showEvidence = false
    @State private var showModel = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Room 204").font(.largeTitle.bold())
                            Label("Demo building · Level 2", systemImage: "building.2")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Menu {
                            Button(model.synthetic ? "Перейти к камере iPhone" : "Тестовые данные") {
                                model.synthetic.toggle()
                                Task { await model.load() }
                            }
                            Button("Демоинструкция", systemImage: "text.bubble") { model.demoInstruction() }
                        } label: {
                            Image(systemName: "slider.horizontal.3").font(.title3)
                                .padding(14).background(.white, in: Circle())
                        }.disabled(model.busy)
                    }

                    CapturePanel(model: model, capture: model.capture, showModel: $showModel)

                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Label("Задание инспектора", systemImage: "text.bubble.fill").font(.headline)
                            Spacer()
                            Text(model.instruction == nil ? "OFFLINE" : "DEMO")
                                .font(.caption2.bold()).foregroundStyle(.orange)
                        }
                        Text(model.instruction ?? "Подключение к оркестратору появится после согласования API. Пока можно проверить съёмку и сохранение.")
                            .font(.subheadline).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if model.instruction == nil {
                            Button("Показать пример задания", action: model.demoInstruction).font(.subheadline.weight(.semibold))
                        } else {
                            Button("Нет доступа к объекту", action: model.cannotAccess).font(.subheadline)
                        }
                    }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 22))

                    HStack(spacing: 12) {
                        metric("Сохранено", value: "\(model.observations.count)", icon: "checkmark.circle.fill", color: .blue)
                        metric("Отправлено", value: "0", icon: "arrow.up.circle", color: .secondary)
                        metric("Ожидают", value: "\(model.observations.count)", icon: "clock", color: .orange)
                    }
                    Button { showEvidence = true } label: {
                        HStack {
                            Label("Наблюдения", systemImage: "photo.stack")
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption.bold())
                        }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 18))
                    }
                    Text(model.notice).font(.footnote).foregroundStyle(.secondary)
                    if let manifest = model.manifest {
                        Text("SESSION \(manifest.session_id.prefix(8)) · RAW RGB-D")
                            .font(.system(size: 10, weight: .medium, design: .monospaced)).foregroundStyle(.tertiary)
                    }
                }.padding(20)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("AI Inspector")
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                Button { Task { await model.takeObservation() } } label: {
                    HStack(spacing: 10) {
                        if model.busy { ProgressView().tint(.white) } else { Image(systemName: "camera.fill") }
                        Text(model.synthetic ? "Сохранить тестовое наблюдение" : "Снять наблюдение").fontWeight(.semibold)
                    }.frame(maxWidth: .infinity).padding(.vertical, 17)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.roundedRectangle(radius: 18))
                .disabled(model.busy || model.manifest == nil)
                .padding(.horizontal, 20).padding(.vertical, 10).background(.ultraThinMaterial)
            }
            .task { await model.load() }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .alert("Не удалось выполнить действие", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showModel) {
                NavigationStack {
                    VStack {
                        RoomPreview()
                        Text("Синтетическая комната · demo-room-v1").font(.headline)
                        Text("Привязка к съёмке отсутствует. Покрытие и положение телефона пока не показаны.")
                            .font(.subheadline).foregroundStyle(.secondary).padding()
                    }.navigationTitle("Модель помещения")
                        .toolbar { Button("Готово") { showModel = false } }
                }
            }
        }.tint(.blue)
    }

    private func metric(_ title: String, value: String, icon: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: icon).foregroundStyle(color)
            Text(value).font(.title2.bold())
            Text(title).font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(.white, in: RoundedRectangle(cornerRadius: 16))
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
                    Label(model.synthetic ? "СИНТЕТИЧЕСКОЕ ДЕМО" : "КАМЕРА + LiDAR", systemImage: model.synthetic ? "cube" : "viewfinder")
                        .font(.system(size: 10, weight: .bold)).padding(10)
                        .background(.regularMaterial, in: Capsule())
                    Spacer()
                    Button { showModel = true } label: {
                        Image(systemName: "cube.transparent").padding(12).background(.regularMaterial, in: Circle())
                    }.accessibilityLabel("Открыть модель помещения")
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
                    Text(model.synthetic ? "Кнопка сохранит тестовую плоскость, не скан комнаты" : "Один снимок · RGB, depth, confidence, pose")
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
                    Text("Сохранено локально. Отправка на сервер ещё не подключена. Файлы доступны также в Файлы → На iPhone → AI Inspector → sessions.")
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
                            Label(frame.capture_origin == "synthetic" ? "Тестовое наблюдение" : "Снимок RGB-D", systemImage: "photo")
                            Text(frame.saved_at.formatted(date: .abbreviated, time: .standard)).font(.caption).foregroundStyle(.secondary)
                            Text("\(frame.rgb_size[0]) × \(frame.rgb_size[1]) · depth \(frame.depth_size[0]) × \(frame.depth_size[1])")
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
            LabeledContent("Depth", value: frame.depth_source)
            LabeledContent("Единицы", value: frame.depth_unit)
            LabeledContent("Сегмент", value: String(frame.segment_id.prefix(8)))
            LabeledContent("Confidence", value: frame.confidence == nil ? "Отсутствует" : "Сохранена")
            if let instruction = frame.instruction_id { LabeledContent("Задание", value: instruction) }
            Section("Экспорт исходных файлов") {
                ForEach(["frame.json", "rgb.jpg", "depth.f32"] + (frame.confidence == nil ? [] : ["confidence.u8"]), id: \.self) { name in
                    ShareLink(item: folder.appendingPathComponent(name)) { Label(name, systemImage: "square.and.arrow.up") }
                }
            }
            Text(frame.id).font(.caption.monospaced()).textSelection(.enabled)
        }.navigationTitle("Наблюдение").navigationBarTitleDisplayMode(.inline)
    }
}
