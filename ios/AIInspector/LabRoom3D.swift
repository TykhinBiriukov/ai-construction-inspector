import SwiftUI
import SceneKit
import simd

struct LabDisplayMesh: Decodable {
    let element_id: String
    let kind: String
    let vertices: [[Double]]
    let normals: [[Double]]
    let triangles: [Int32]
}
struct LabDisplayConfiguration: Decodable {
    let room: LabState.Room
    let meshes: [LabDisplayMesh]
    let entry_selection_required: Bool?
}

struct CaptureMapButton: View {
    @ObservedObject var localization: SessionLocalization
    @ObservedObject var capture: CaptureService
    let open: () -> Void
    var body: some View {
        if let state = localization.state {
            TimelineView(.periodic(from: .now, by: 0.5)) { time in
                Button(action: open) {
                    VStack(spacing: 4) {
                        RoomMiniMap(state: state, live: capture.running && time.date.timeIntervalSince(localization.receivedAt) < 2)
                            .frame(width: 112, height: 92)
                        Label("BIM", systemImage: "arrow.up.left.and.arrow.down.right").font(.caption2)
                    }.padding(8).background(InspectorStyle.paper.opacity(0.95), in: RoundedRectangle(cornerRadius: 14))
                }.buttonStyle(.plain).accessibilityLabel("Открыть BIM и покрытие")
            }
        }
    }
}

struct RoomMiniMap: View {
    let state: LabState
    let live: Bool
    var body: some View {
        Canvas { context, size in
            let room = state.room
            guard room.length > 0, room.width > 0 else { return }
            let scale = min((size.width - 20) / room.length, (size.height - 20) / room.width)
            let origin = CGPoint(x: (size.width - room.length * scale) / 2, y: (size.height - room.width * scale) / 2)
            func point(_ x: Double, _ z: Double) -> CGPoint { CGPoint(x: origin.x + x * scale, y: origin.y - z * scale) }
            let rect = CGRect(origin: origin, size: CGSize(width: room.length * scale, height: room.width * scale))
            context.fill(Path(rect), with: .color(.white))
            context.stroke(Path(rect), with: .color(InspectorStyle.ink.opacity(0.6)), lineWidth: 2)
            for door in room.doors ?? [] where door.low.count == 3 && door.high.count == 3 {
                var path = Path(); path.move(to: point(door.low[0], door.low[2])); path.addLine(to: point(door.high[0], door.high[2]))
                context.stroke(path, with: .color(InspectorStyle.sage), lineWidth: 5)
            }
            if live, let rows = state.latest_pose?.T_bim_camera, let pose = bimPoseMatrix(rows) {
                let p = point(Double(pose.columns.3.x), Double(pose.columns.3.z))
                let dx = Double(-pose.columns.2.x), dy = Double(pose.columns.2.z)
                let magnitude = hypot(dx, dy)
                if magnitude > 0.05 {
                    let vx = dx / magnitude, vy = dy / magnitude
                    var arrow = Path()
                    arrow.move(to: CGPoint(x: p.x + vx * 9, y: p.y + vy * 9))
                    arrow.addLine(to: CGPoint(x: p.x - vx * 5 - vy * 5, y: p.y - vy * 5 + vx * 5))
                    arrow.addLine(to: CGPoint(x: p.x - vx * 2, y: p.y - vy * 2))
                    arrow.addLine(to: CGPoint(x: p.x - vx * 5 + vy * 5, y: p.y - vy * 5 - vx * 5))
                    arrow.closeSubpath(); context.fill(arrow, with: .color(InspectorStyle.ink))
                } else { context.fill(Path(ellipseIn: CGRect(x: p.x-3, y: p.y-3, width: 6, height: 6)), with: .color(InspectorStyle.ink)) }
            }
        }.clipped().accessibilityLabel("План комнаты; положение телефона показывается только при актуальной привязке")
    }
}

struct BIMViewer: View {
    @ObservedObject var localization: SessionLocalization
    @ObservedObject var capture: CaptureService
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if localization.choosingEntry, let doors = localization.state?.room.doors {
                        Text("С какой двери начнём?").font(.system(size: 28, design: .serif))
                        ForEach(doors) { door in
                            Button(door.label + (door.name.isEmpty ? "" : " · " + door.name)) {
                                Task { await localization.chooseEntry(door.id) }
                            }.buttonStyle(.bordered).disabled(localization.entryBusy)
                        }
                    }
                    TimelineView(.periodic(from: .now, by: 0.5)) { time in
                        if let state = localization.state {
                            LabRoom3DPanel(state: state, meshes: localization.meshes,
                                live: capture.running && time.date.timeIntervalSince(localization.receivedAt) < 2)
                        } else { ContentUnavailableView("Модель ещё не загружена", systemImage: "cube") }
                    }
                    Text(localization.status).font(.footnote).foregroundStyle(.secondary)
                    Button("Вернуться к камере") { dismiss() }.buttonStyle(.borderedProminent).frame(maxWidth: .infinity)
                }.padding(20)
            }.background(InspectorStyle.paper)
                .navigationTitle("BIM и покрытие").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Готово") { dismiss() } }
        }.tint(InspectorStyle.ink).presentationDetents([.large])
    }
}

struct LabRoom3DPanel: View {
    let state: LabState
    let meshes: [LabDisplayMesh]
    let live: Bool
    @State private var reset = 0
    @State private var plan = false
    @State private var showCoverage = true
    @State private var showPhotos = true
    @State private var cutaway = true
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Picker("Вид комнаты", selection: $plan) {
                Text("План").tag(true); Text("3D").tag(false)
            }.pickerStyle(.segmented)
            LabRoom3D(state: state, meshes: meshes, live: live, reset: reset,
                      plan: plan, showCoverage: showCoverage, showPhotos: showPhotos, cutaway: cutaway)
                .frame(height: 380).clipShape(RoundedRectangle(cornerRadius: 16))
                .accessibilityLabel("BIM-модель с подтверждённым покрытием съёмки")
            HStack(spacing: 16) {
                Label { Text("Отснято") } icon: { Circle().fill(InspectorStyle.sage).frame(width: 10, height: 10) }
                Label { Text("Нет данных") } icon: { Circle().fill(.gray.opacity(0.35)).frame(width: 10, height: 10) }
                Spacer()
                Button { reset += 1 } label: { Image(systemName: "arrow.counterclockwise") }.accessibilityLabel("Сбросить ракурс")
            }.font(.caption)
            Text("Покрытие съёмкой, не оценка качества").font(.caption).foregroundStyle(.secondary)
            Divider()
            Toggle("Покрытие", isOn: $showCoverage)
            Toggle("Точки фото", isOn: $showPhotos)
            Toggle("Открытый вид комнаты", isOn: $cutaway)
            Text(live && state.latest_pose != nil ? "Зелёный указатель — телефон и направление камеры." : "Положение телефона не подтверждено или устарело.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct LabRoom3D: UIViewRepresentable {
    let state: LabState
    let meshes: [LabDisplayMesh]
    let live: Bool
    let reset: Int
    var plan = false
    var showCoverage = true
    var showPhotos = true
    var cutaway = true
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = UIColor(InspectorStyle.paper)
        view.scene = SCNScene()
        view.autoenablesDefaultLighting = true
        view.allowsCameraControl = true
        view.defaultCameraController.interactionMode = .orbitTurntable
        view.defaultCameraController.inertiaEnabled = true
        view.antialiasingMode = .multisampling4X
        view.preferredFramesPerSecond = 30
        context.coordinator.configure(view, state: state, meshes: meshes)
        return view
    }
    func updateUIView(_ view: SCNView, context: Context) {
        let coordinator = context.coordinator
        if coordinator.version != state.room.model_version + (state.room.bim_frame_id ?? "") + String(state.room.entry_confirmed ?? true) { coordinator.configure(view, state: state, meshes: meshes) }
        if coordinator.lastReset != reset { coordinator.resetCamera(view, room: state.room); coordinator.lastReset = reset }
        if coordinator.isPlan != plan { coordinator.isPlan = plan; coordinator.resetCamera(view, room: state.room) }
        view.allowsCameraControl = !plan
        coordinator.coverage.isHidden = !showCoverage
        coordinator.photos.isHidden = !showPhotos
        for wall in coordinator.frontWalls { wall.isHidden = cutaway }
        coordinator.update(state: state, live: live)
    }
    final class Coordinator {
        var isPlan = false
        var frontWalls: [SCNNode] = []
        var version = ""
        var lastReset = 0
        var device = SCNNode()
        var photos = SCNNode()
        var coverage = SCNNode()
        var coverageSignature = ""
        var eye = SCNNode()
        var photoPositions: [[Double]] = []
        func material(_ color: UIColor) -> SCNMaterial {
            let m = SCNMaterial(); m.diffuse.contents = color; m.isDoubleSided = true; m.lightingModel = .lambert
            return m
        }
        func configure(_ view: SCNView, state: LabState, meshes: [LabDisplayMesh]) {
            let scene = SCNScene();view.scene = scene;version = state.room.model_version + (state.room.bim_frame_id ?? "") + String(state.room.entry_confirmed ?? true)
            frontWalls = []
            for mesh in meshes {
                guard mesh.vertices.count == mesh.normals.count, !mesh.vertices.isEmpty,
                      mesh.vertices.allSatisfy({ $0.count == 3 && $0.allSatisfy(\.isFinite) }),
                      mesh.normals.allSatisfy({ $0.count == 3 && $0.allSatisfy(\.isFinite) }),
                      mesh.triangles.count % 3 == 0,
                      mesh.triangles.allSatisfy({ $0 >= 0 && Int($0) < mesh.vertices.count }) else { continue }
                let vertices = mesh.vertices.map { SCNVector3(Float($0[0]),Float($0[1]),Float($0[2])) }
                let normals = mesh.normals.map { SCNVector3(Float($0[0]),Float($0[1]),Float($0[2])) }
                let element = SCNGeometryElement(indices: mesh.triangles, primitiveType: .triangles)
                let geometry = SCNGeometry(sources: [SCNGeometrySource(vertices: vertices), SCNGeometrySource(normals: normals)], elements: [element])
                let selected = mesh.element_id == state.room.entry_element_id && state.room.entry_confirmed != false
                let color: UIColor = mesh.kind == "IfcWindow" ? UIColor(red: 0.78, green: 0.85, blue: 0.84, alpha: 1) : (mesh.kind == "IfcDoor" ? UIColor(selected ? InspectorStyle.sage : InspectorStyle.line) : (mesh.kind == "IfcSlab" ? UIColor(white: 0.93, alpha: 1) : UIColor(white: 0.86, alpha: 1)))
                geometry.materials = [material(color)]
                let node = SCNNode(geometry: geometry);node.name = mesh.element_id
                if mesh.kind == "IfcWall" {
                    let xs = mesh.vertices.map { $0[0] }, zs = mesh.vertices.map { $0[2] }
                    let cx = ((xs.min() ?? 0) + (xs.max() ?? 0)) / 2
                    let cz = ((zs.min() ?? 0) + (zs.max() ?? 0)) / 2
                    // Presentation-only cutaway for the initial +X/+Z view. Source meshes stay intact.
                    if abs(cx - state.room.length) < 0.35 || abs(cz) < 0.35 { frontWalls.append(node) }
                }
                if mesh.kind == "IfcDoor" { node.opacity = 0.6 }
                if mesh.kind == "IfcWindow" { node.opacity = 0.45 }
                scene.rootNode.addChildNode(node)
            }
            // Everything below stays in the same right-handed BIM coordinates as IFC meshes.
            coverage = SCNNode();scene.rootNode.addChildNode(coverage);coverageSignature = ""
            device = SCNNode()
            // A small flat chevron in camera-local X/-Z, so the full validated pose is preserved.
            let vertices = [SCNVector3(0,0,-0.25), SCNVector3(-0.13,0,0.12), SCNVector3(0,0,0.04), SCNVector3(0.13,0,0.12)]
            let geometry = SCNGeometry(sources: [SCNGeometrySource(vertices: vertices)],
                elements: [SCNGeometryElement(indices: [Int32(0),1,2,0,2,3], primitiveType: .triangles)])
            geometry.materials = [material(UIColor(InspectorStyle.ink))]
            device.addChildNode(SCNNode(geometry: geometry))
            scene.rootNode.addChildNode(device)
            photos = SCNNode();scene.rootNode.addChildNode(photos);photoPositions = []
            if let doors = state.room.doors {
                for door in doors where door.low.count == 3 && door.high.count == 3 {
                    let selected = door.id == state.room.entry_element_id && state.room.entry_confirmed != false
                    let label = SCNText(string: (selected ? "Вход · " : "") + door.label, extrusionDepth: 0.001)
                    label.font = UIFont.systemFont(ofSize: 14, weight: .semibold)
                    label.materials = [material(UIColor(selected ? InspectorStyle.ink : InspectorStyle.sage))]
                    let text = SCNNode(geometry: label); text.scale = SCNVector3(0.018,0.018,0.018)
                    text.position = SCNVector3(Float((door.low[0]+door.high[0])/2),Float(door.high[1]+0.15),Float((door.low[2]+door.high[2])/2))
                    text.constraints = [SCNBillboardConstraint()]; scene.rootNode.addChildNode(text)
                }
            }
            eye = SCNNode();eye.camera = SCNCamera();eye.camera?.fieldOfView = 48;eye.camera?.zNear = 0.02;eye.camera?.zFar = 100
            scene.rootNode.addChildNode(eye);resetCamera(view, room: state.room)
            update(state: state, live: false)
        }
        func resetCamera(_ view: SCNView, room: LabState.Room) {
            eye.camera?.usesOrthographicProjection = true
            eye.camera?.orthographicScale = max(room.length,room.width) * 0.85
            let target = SCNVector3(Float(room.length/2),Float(room.height*0.3),Float(-room.width/2))
            eye.position = SCNVector3(Float(room.length*1.15),Float(max(room.length,room.width)*1.05),Float(room.width*0.75))
            if isPlan {
                eye.position = SCNVector3(Float(room.length/2),Float(max(room.length,room.width)*2),Float(-room.width/2))
                eye.eulerAngles = SCNVector3(-Float.pi/2,0,0)
            } else { eye.look(at: target) }
            view.pointOfView = eye;view.defaultCameraController.target = target
        }
        func update(state: LabState, live: Bool) {
            let tiles = state.coverage?.tiles ?? []
            let signature = tiles.map { $0.id }.sorted().joined(separator: "|")
            if signature != coverageSignature {
                coverageSignature = signature
                coverage.childNodes.forEach { $0.removeFromParentNode() }
                for tile in tiles {
                    guard tile.center.count == 3, tile.size.count == 3,
                          tile.center.allSatisfy(\.isFinite), tile.size.allSatisfy({ $0.isFinite && $0 > 0 }) else { continue }
                    let box = SCNBox(width: CGFloat(tile.size[0]), height: CGFloat(tile.size[1]), length: CGFloat(tile.size[2]), chamferRadius: 0)
                    box.materials = [material(UIColor(InspectorStyle.sage))]
                    let node = SCNNode(geometry: box)
                    node.position = SCNVector3(Float(tile.center[0]),Float(tile.center[1]),Float(tile.center[2]))
                    coverage.addChildNode(node)
                }
            }
            if live, let rows = state.latest_pose?.T_bim_camera, let matrix = bimPoseMatrix(rows) {
                device.simdTransform = matrix;device.isHidden = false
            } else { device.isHidden = true }
            let positions = state.observation_poses.compactMap { pose -> [Double]? in
                guard let matrix = bimPoseMatrix(pose.T_bim_camera) else { return nil }
                return [Double(matrix.columns.3.x),Double(matrix.columns.3.y),Double(matrix.columns.3.z)]
            }
            guard positions != photoPositions else { return }
            photoPositions = positions
            photos.childNodes.forEach { $0.removeFromParentNode() }
            for p in positions {
                let dot = SCNNode(geometry: SCNSphere(radius: 0.045));dot.geometry?.materials = [material(UIColor(InspectorStyle.ink))]
                dot.position = SCNVector3(Float(p[0]),Float(p[1]),Float(p[2]));photos.addChildNode(dot)
            }
        }
    }
}
