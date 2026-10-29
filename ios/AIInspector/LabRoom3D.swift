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
}

struct LabRoom3DPanel: View {
    let state: LabState
    let meshes: [LabDisplayMesh]
    let live: Bool
    @State private var reset = 0
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("3D BIM · помещение").font(.headline)
                Spacer()
                Button("Сбросить ракурс") { reset += 1 }.font(.caption)
            }
            LabRoom3D(state: state, meshes: meshes, live: live, reset: reset)
                .frame(height: 340).clipShape(RoundedRectangle(cornerRadius: 16))
            Text(live && state.latest_pose != nil ? "Синяя камера — телефон, стрелка — направление съёмки." : "Положение телефона пока не подтверждено или устарело.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Зелёное — поверхность, подтверждённая LiDAR в сохранённых фото. Серое — пока не подтверждено. Это не оценка качества инспекции.").font(.caption2).foregroundStyle(.secondary)
            Text("Поворачивайте одним пальцем, приближайте двумя. Оранжевые точки — места снимков, не покрытие стен.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct LabRoom3D: UIViewRepresentable {
    let state: LabState
    let meshes: [LabDisplayMesh]
    let live: Bool
    let reset: Int
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = UIColor(white: 0.94, alpha: 1)
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
        if coordinator.version != state.room.model_version { coordinator.configure(view, state: state, meshes: meshes) }
        if coordinator.lastReset != reset { coordinator.resetCamera(view, room: state.room); coordinator.lastReset = reset }
        coordinator.update(state: state, live: live)
    }
    final class Coordinator {
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
            let scene = SCNScene();view.scene = scene;version = state.room.model_version
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
                let color: UIColor = mesh.kind == "IfcDoor" ? .systemBlue : (mesh.kind == "IfcSlab" ? .white : .systemGray3)
                geometry.materials = [material(color)]
                let node = SCNNode(geometry: geometry);node.name = mesh.element_id
                if mesh.kind == "IfcWall" { node.opacity = 0.24; node.geometry?.firstMaterial?.writesToDepthBuffer = false }
                if mesh.kind == "IfcDoor" { node.opacity = 0.5 }
                scene.rootNode.addChildNode(node)
            }
            // Everything below stays in the same right-handed BIM coordinates as IFC meshes.
            coverage = SCNNode();scene.rootNode.addChildNode(coverage);coverageSignature = ""
            device = SCNNode()
            let body = SCNNode(geometry: SCNBox(width: 0.17, height: 0.12, length: 0.12, chamferRadius: 0.025))
            body.geometry?.materials = [material(.systemBlue)];device.addChildNode(body)
            let shaft = SCNNode(geometry: SCNCylinder(radius: 0.022, height: 0.5))
            shaft.eulerAngles.x = -.pi/2;shaft.position.z = -0.32;shaft.geometry?.materials = [material(.systemBlue)];device.addChildNode(shaft)
            let tip = SCNNode(geometry: SCNCone(topRadius: 0, bottomRadius: 0.075, height: 0.18))
            tip.eulerAngles.x = -.pi/2;tip.position.z = -0.65;tip.geometry?.materials = [material(.systemBlue)];device.addChildNode(tip)
            scene.rootNode.addChildNode(device)
            photos = SCNNode();scene.rootNode.addChildNode(photos);photoPositions = []
            let label = SCNText(string: "Вход", extrusionDepth: 0.001);label.font = UIFont.systemFont(ofSize: 14, weight: .semibold)
            label.materials = [material(.systemBlue)]
            let text = SCNNode(geometry: label);text.scale = SCNVector3(0.018,0.018,0.018)
            text.position = SCNVector3(0.05,Float(state.room.door_height+0.15),Float(-state.room.door_offset-state.room.door_width/2))
            text.constraints = [SCNBillboardConstraint()];scene.rootNode.addChildNode(text)
            eye = SCNNode();eye.camera = SCNCamera();eye.camera?.fieldOfView = 48;eye.camera?.zNear = 0.02;eye.camera?.zFar = 100
            scene.rootNode.addChildNode(eye);resetCamera(view, room: state.room)
            update(state: state, live: false)
        }
        func resetCamera(_ view: SCNView, room: LabState.Room) {
            let target = SCNVector3(Float(room.length/2),Float(room.height*0.3),Float(-room.width/2))
            eye.position = SCNVector3(Float(room.length*1.15),Float(max(room.length,room.width)*1.05),Float(room.width*0.75))
            eye.look(at: target)
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
                    box.materials = [material(.systemGreen)]
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
                let dot = SCNNode(geometry: SCNSphere(radius: 0.065));dot.geometry?.materials = [material(.systemOrange)]
                dot.position = SCNVector3(Float(p[0]),Float(p[1]),Float(p[2]));photos.addChildNode(dot)
            }
        }
    }
}
