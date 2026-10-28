import SwiftUI
import RealityKit
import ARKit

#if targetEnvironment(simulator)
// Simulator RealityKit initialization can block the first SwiftUI layout.
// Keep the fixture workflow independent of AR/Metal availability.
struct CameraPreview: View {
    let session: ARSession
    var body: some View { Color.black }
}

struct RoomPreview: View {
    var body: some View {
        Canvas { context, size in
            let scale = min(size.width / 400, size.height / 330)
            let origin = CGPoint(x: size.width / 2, y: size.height / 2 + 20 * scale)
            func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
                CGPoint(x: origin.x + x * scale, y: origin.y + y * scale)
            }
            func polygon(_ vertices: [CGPoint], color: Color) {
                var path = Path()
                path.addLines(vertices)
                path.closeSubpath()
                context.fill(path, with: .color(color))
                context.stroke(path, with: .color(.white.opacity(0.8)), lineWidth: 1)
            }
            polygon([point(-155,0), point(0,-75), point(155,0), point(0,85)], color: Color(red: 0.80, green: 0.84, blue: 0.89))
            polygon([point(-155,0), point(-155,-120), point(0,-195), point(0,-75)], color: Color(red: 0.70, green: 0.75, blue: 0.82))
            polygon([point(0,-75), point(0,-195), point(155,-120), point(155,0)], color: Color(red: 0.85, green: 0.88, blue: 0.92))
            var doorway = Path()
            doorway.addLines([point(52,-50), point(52,-137), point(100,-114), point(100,-27)])
            context.stroke(doorway, with: .color(.blue), style: StrokeStyle(lineWidth: 4, lineCap: .round))
            context.draw(Text("Схема · simulator").font(.caption).foregroundStyle(.secondary), at: point(0,110))
        }
        .background(Color(red: 0.91, green: 0.93, blue: 0.96))
        .accessibilityLabel("Схематический вид тестовой комнаты. 3D-превью доступно на устройстве.")
    }
}
#else
struct CameraPreview: UIViewRepresentable {
    let session: ARSession
    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        view.session = session
        return view
    }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

/// Generated display model only. It has no relationship to captured real geometry.
struct RoomPreview: UIViewRepresentable {
    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .nonAR, automaticallyConfigureSession: false)
        view.environment.background = .color(UIColor(red: 0.91, green: 0.93, blue: 0.96, alpha: 1))
        let anchor = AnchorEntity(world: .zero)
        func box(_ id: String, _ size: SIMD3<Float>, _ position: SIMD3<Float>, _ color: UIColor) {
            let entity = ModelEntity(mesh: .generateBox(size: size), materials: [SimpleMaterial(color: color, roughness: 0.85, isMetallic: false)])
            entity.name = id
            entity.position = position
            anchor.addChild(entity)
        }
        let concrete = UIColor(red: 0.78, green: 0.80, blue: 0.82, alpha: 1)
        box("floor-01", [4,0.12,4], [0,-0.06,0], .white)
        box("wall-left", [0.12,2.8,4], [-2,1.4,0], concrete)
        box("wall-back-left", [1.5,2.8,0.12], [-1.25,1.4,-2], concrete)
        box("wall-back-right", [1.5,2.8,0.12], [1.25,1.4,-2], concrete)
        box("wall-back-top", [1,0.6,0.12], [0,2.5,-2], concrete)
        box("door-01-left", [0.04,2.2,0.16], [-0.5,1.1,-1.92], .systemBlue)
        box("door-01-right", [0.04,2.2,0.16], [0.5,1.1,-1.92], .systemBlue)
        box("door-01-top", [1.04,0.04,0.16], [0,2.2,-1.92], .systemBlue)
        for step in -2...2 {
            let offset = Float(step)
            box("grid-x-\(step)", [0.012,0.004,4], [offset,0.003,0], .systemGray4)
            box("grid-z-\(step)", [4,0.004,0.012], [0,0.003,offset], .systemGray4)
        }
        let light = DirectionalLight()
        light.light.intensity = 2500
        light.look(at: .zero, from: [3,6,4], relativeTo: nil)
        anchor.addChild(light)
        let camera = PerspectiveCamera()
        camera.camera.fieldOfViewInDegrees = 48
        camera.look(at: [0,0.8,0], from: [5,5.5,7], relativeTo: nil)
        anchor.addChild(camera)
        view.scene.addAnchor(anchor)
        return view
    }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

#endif
