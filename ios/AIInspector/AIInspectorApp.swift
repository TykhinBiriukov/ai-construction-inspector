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
                .environment(\.locale, Locale(identifier: "en"))
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
                        Text("Ready to capture").font(.system(size: 34, design: .serif))
                        VStack(alignment: .leading, spacing: 10) {
                            Label("Waiting for an assignment", systemImage: "antenna.radiowaves.left.and.right")
                                .font(.headline)
                            Text("Your assignment will appear here when Inspector assigns a room.")
                                .foregroundStyle(.secondary)
                        }
                        Divider()
                        Button {
                            connection.localLabActive = true
                            connection.setForeground(false)
                            standalone = StandaloneCapture.assignment()
                        } label: {
                            HStack { Label("Free capture", systemImage: "camera"); Spacer(); Image(systemName: "arrow.up.right") }
                                .padding(12).frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent)
                        Text("Photos and metadata without an assignment or BIM alignment.")
                            .font(.footnote).foregroundStyle(.secondary)
                        Spacer()
                        Text(connection.status).font(.caption).foregroundStyle(.secondary)
                    }.padding(24).background(InspectorStyle.paper)
                        .toolbar { Button { showStorage = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("Settings") }
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
                        Text("Saving capture").font(.system(size: 30, design: .serif))
                        Text(connection.status).font(.callout).multilineTextAlignment(.center)
                        if let progress = connection.uploadProgress { ProgressView(value: progress) }
                        else if connection.busy { ProgressView() }
                        Button("Retry upload") { finish() }.buttonStyle(.borderedProminent).disabled(connection.busy)
                        Button("View photos") { showEvidence = true }
                        Text("Originals remain on this phone").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                    }.padding(24).frame(maxWidth: .infinity)
                }
            }
            .background(InspectorStyle.paper)
            .navigationTitle(model.assignment.room_id).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(InspectorStyle.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button { showExit = true } label: { Image(systemName: "xmark") }.accessibilityLabel("Exit assignment").disabled(model.busy || connection.busy) }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Photos", systemImage: "photo.on.rectangle") { showEvidence = true }
                        Button("Settings", systemImage: "gearshape") { showSettings = true }
                        Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = model.assignment.id }
                        Button("Exit assignment", systemImage: "xmark.circle", role: .destructive) { showExit = true }
                    } label: { Image(systemName: "ellipsis") }.disabled(model.busy || connection.busy)
                }
            }
            .confirmationDialog("Finish capture?", isPresented: $showFinish, titleVisibility: .visible) {
                Button("Finish and upload") { finish() }
            } message: { Text("All saved photos and metadata will be uploaded. You cannot add to this capture afterwards.") }
            .confirmationDialog("Exit assignment?", isPresented: $showExit, titleVisibility: .visible) {
                Button("Exit and cancel assignment", role: .destructive) {
                    model.pause()
                    Task { if let error = await connection.cancelActive() { model.error = error } }
                }
            } message: { Text("The assignment will be cancelled in Inspector. Local photos will remain on this phone. Cancellation requires a server connection.") }
            .task { await model.load(); model.frozen = connection.frozen }
            .onDisappear { model.pause() }
            .onChange(of: connection.frozen) { _, value in model.frozen = value; if value { model.pause() } }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .sheet(isPresented: $showModel) { BIMViewer(localization: model.localization, capture: model.capture) }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showSettings) { StorageConnectionView() }
            .alert("Could not complete the action", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
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
                    Label(model.synthetic ? "Synthetic demo" : (capture.supportsDepth ? "Photo + LiDAR" : "Photo · no depth"),
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
                        Label(model.busy ? "Preparing…" : "Turn on camera", systemImage: "camera")
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
                        Button("Pause") { model.pause() }.font(.caption.bold()).disabled(model.busy)
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
                        Button { showModel = true } label: { Label("Select the entry door", systemImage: "door.left.hand.open") }
                    } else {
                        Text(EnglishInterface.serviceText(localization.instruction?.text ?? localization.status)).font(.subheadline)
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
                }.accessibilityLabel("View photos")
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
                }.accessibilityLabel(model.synthetic ? "Save synthetic observation" : "Take photo")
                    .disabled(model.busy || model.frozen || model.manifest == nil || (!model.synthetic && model.segment == nil))
                Spacer()
                Text("\(model.observations.count) photos").font(.subheadline.monospacedDigit()).frame(minWidth: 56)
            }
            Divider()
            Button("Finish", action: finish).frame(maxWidth: .infinity, minHeight: 36)
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
                    Text("Local originals are kept after upload. Upload and Inspector acceptance status are shown on the session screen.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if model.observations.isEmpty {
                    ContentUnavailableView("No observations yet", systemImage: "camera", description: Text("Take your first photo on the capture screen."))
                }
                ForEach(model.observations.reversed()) { frame in
                    NavigationLink {
                        ObservationDetail(frame: frame, folder: model.observationURL(frame))
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(frame.capture_origin == "synthetic" ? "Test observation" : (frame.depth == nil ? "Photo without depth" : "RGB-D capture"), systemImage: "photo")
                            Text(frame.saved_at.formatted(.dateTime.year().month(.abbreviated).day().hour().minute().second().locale(Locale(identifier: "en")))).font(.caption).foregroundStyle(.secondary)
                            Text("\(frame.rgb_size[0]) × \(frame.rgb_size[1]) · " + (frame.depth_size.map { "depth \($0[0]) × \($0[1])" } ?? "no depth"))
                                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }.padding(.vertical, 4)
                    }
                }
            }.navigationTitle("Observations").toolbar { Button("Done") { dismiss() } }
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
            LabeledContent("Source", value: frame.capture_origin)
            LabeledContent("Depth", value: frame.depth == nil ? "Not available" : frame.depth_source)
            LabeledContent("Units", value: frame.depth_unit ?? "—")
            LabeledContent("Segment", value: String(frame.segment_id.prefix(8)))
            LabeledContent("Confidence", value: frame.confidence == nil ? "Not available" : "Saved")
            if let instruction = frame.instruction_id { LabeledContent("Assignment", value: instruction) }
            Section("Export original files") {
                ForEach(["frame.json", "rgb.jpg"] + (frame.depth == nil ? [] : ["depth.f32"]) + (frame.confidence == nil ? [] : ["confidence.u8"]), id: \.self) { name in
                    ShareLink(item: folder.appendingPathComponent(name)) { Label(name, systemImage: "square.and.arrow.up") }
                }
            }
            Text(frame.id).font(.caption.monospaced()).textSelection(.enabled)
        }.navigationTitle("Observation").navigationBarTitleDisplayMode(.inline)
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
                            Text(upload.manifestKey == nil ? "Saving capture" : "Capture saved")
                                .font(.system(size: 34, design: .serif))
                            Text("\(model.observations.count) photos and metadata").foregroundStyle(.secondary)
                            Button { showEvidence = true } label: {
                                HStack(spacing: 8) {
                                    ForEach(Array(model.observations.suffix(3))) { frame in
                                        if let image = UIImage(contentsOfFile: model.observationURL(frame).appendingPathComponent("rgb.jpg").path) {
                                            Image(uiImage: image).resizable().scaledToFill().frame(width: 90, height: 90).clipped().clipShape(RoundedRectangle(cornerRadius: 10))
                                        }
                                    }
                                }
                            }.accessibilityLabel("View photos")
                            if let key = upload.manifestKey {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text("Session ID").font(.subheadline).foregroundStyle(.secondary)
                                    Text(model.assignment.id).font(.body.monospaced()).textSelection(.enabled)
                                    Button {
                                        UIPasteboard.general.string = model.assignment.id; copiedSessionID = true
                                    } label: {
                                        Label(copiedSessionID ? "ID copied" : "Copy ID", systemImage: copiedSessionID ? "checkmark" : "doc.on.doc")
                                            .frame(maxWidth: .infinity).padding(8)
                                    }.buttonStyle(.borderedProminent)
                                }
                                DisclosureGroup("Supabase path") {
                                    Text("inspection-captures / " + key).font(.caption.monospaced()).textSelection(.enabled)
                                    ShareLink(item: key) { Label("Share path", systemImage: "square.and.arrow.up") }
                                }
                                Text("Originals remain on this phone").font(.footnote).foregroundStyle(.secondary)
                                Button("New capture") {
                                    UserDefaults.standard.removeObject(forKey: StandaloneCapture.sessionKey); close()
                                }.buttonStyle(.borderedProminent)
                                Button("Back to home") { close() }
                            } else {
                                Text(upload.status).font(.callout)
                                if upload.busy { ProgressView(value: upload.progress) }
                                Button("Retry upload") { Task { await upload.upload(model) } }
                                    .buttonStyle(.borderedProminent).disabled(upload.busy)
                            }
                        }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }.background(InspectorStyle.paper)
            .navigationTitle("Free capture").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(InspectorStyle.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button { model.pause(); close() } label: { Image(systemName: "xmark") }.accessibilityLabel("Close and keep photos").disabled(upload.busy || model.busy) }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Settings", systemImage: "gearshape") { showSettings = true }
                        Button("Reset and start over", systemImage: "arrow.counterclockwise", role: .destructive) { showReset = true }
                    } label: { Image(systemName: "ellipsis") }
                        .accessibilityLabel("Capture actions").disabled(upload.busy || model.busy)
                }
            }
            .confirmationDialog("Finish capture?", isPresented: $showFinish, titleVisibility: .visible) {
                Button("Save to Supabase") { Task { await upload.upload(model) } }
                Button("View photos") { showEvidence = true }
                Button("Reset and start over", role: .destructive) { showReset = true }
            } message: { Text("The capture is locked when uploading starts. Originals remain on this phone.") }
            .alert("Delete this capture?", isPresented: $showReset) {
                Button("Delete and start over", role: .destructive) {
                    do {
                        try model.discardStandaloneCapture()
                        UserDefaults.standard.removeObject(forKey: StandaloneCapture.sessionKey)
                        restart()
                    } catch { model.error = error.localizedDescription }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("All photos and metadata for this session on this phone will be deleted without uploading. A new empty session will open with a new ID. Files already uploaded to Supabase will not be deleted.")
            }
            .task { await model.load(); upload.restore(model) }
            .onDisappear { model.pause() }
            .onChange(of: scenePhase) { _, phase in if phase != .active { model.pause() } }
            .sheet(isPresented: $showEvidence) { EvidenceView(model: model) }
            .sheet(isPresented: $showSettings) { StorageConnectionView() }
            .alert("Could not complete the action", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
}
