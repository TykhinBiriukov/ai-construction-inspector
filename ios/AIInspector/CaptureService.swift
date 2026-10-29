import ARKit
import AVFoundation
import CoreImage
import UIKit
import Combine
import Metal

@MainActor
final class CaptureService: NSObject, ObservableObject, ARSessionDelegate {
    let session = ARSession()
    let supportsDepth = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
    let supportsMesh = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
    private var meshAnchors: [UUID: GeometryMesh] = [:]
    private var meshError: String?
    @Published var status = "Камера остановлена"
    @Published var running = false
    var onPose: ((ARFrame) -> Void)?
    var onInterruption: (() -> Void)?
    private var segmentStartedAt: TimeInterval?

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = .main
    }

    func start() async throws {
        guard ARWorldTrackingConfiguration.isSupported else {
            throw CaptureError.invalid("AR-съёмка недоступна на этом устройстве.")
        }
        let allowed = await AVCaptureDevice.requestAccess(for: .video)
        guard allowed else { throw CaptureError.invalid("Разрешите доступ к камере в Настройках → AI Inspector.") }
        guard UIApplication.shared.applicationState == .active else {
            throw CaptureError.invalid("Вернитесь в приложение и включите камеру снова")
        }
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        if supportsDepth { config.frameSemantics = .sceneDepth }
        if supportsMesh { config.sceneReconstruction = .mesh }
        meshAnchors = [:]; meshError = nil
        segmentStartedAt = ProcessInfo.processInfo.systemUptime
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
        running = true
        status = "Наведите камеру на помещение"
    }

    func stop() {
        session.pause()
        segmentStartedAt = nil
        running = false
        status = "Камера остановлена"
    }

    func snapshot() throws -> ARFrame {
        guard running, let frame = session.currentFrame else { throw CaptureError.invalid("Сначала включите камеру") }
        guard let started = segmentStartedAt, frame.timestamp >= started,
              ProcessInfo.processInfo.systemUptime - frame.timestamp < 1 else {
            throw CaptureError.invalid("Ожидаем свежий кадр нового сегмента")
        }
        guard case .normal = frame.camera.trackingState else {
            throw CaptureError.invalid("Положение камеры ещё не определено. Медленно поводите телефоном.")
        }
        guard !supportsDepth || frame.sceneDepth != nil else { throw CaptureError.invalid("Карта глубины ещё не готова") }
        return frame
    }

    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        MainActor.assumeIsolated {
            guard running, let started = segmentStartedAt, frame.timestamp >= started else { return }
            onPose?(frame)
        }
    }

    nonisolated func sessionWasInterrupted(_ session: ARSession) {
        Task { @MainActor in
            self.stop()
            self.status = "Съёмка прервана. Включите камеру для нового сегмента."
            self.onInterruption?()
        }
    }

    nonisolated func session(_ session: ARSession, didFailWithError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in
            self.stop()
            self.status = message
            self.onInterruption?()
        }
    }

    nonisolated func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        MainActor.assumeIsolated { updateMesh(anchors) }
    }
    nonisolated func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        MainActor.assumeIsolated { updateMesh(anchors) }
    }
    nonisolated func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        MainActor.assumeIsolated { for anchor in anchors { meshAnchors.removeValue(forKey: anchor.identifier) } }
    }
    private func updateMesh(_ anchors: [ARAnchor]) {
        guard running else { return }
        for case let anchor as ARMeshAnchor in anchors {
            do {
                let source = anchor.geometry.vertices, faces = anchor.geometry.faces
                guard source.format == .float3, source.stride >= 12, source.offset >= 0,
                      source.count > 0, source.offset + (source.count - 1) * source.stride + 12 <= source.buffer.length,
                      faces.indexCountPerPrimitive == 3, [2, 4].contains(faces.bytesPerIndex),
                      faces.count * 3 * faces.bytesPerIndex <= faces.buffer.length else { throw CaptureError.invalid("Неверный буфер ARMeshAnchor") }
                // Copy synchronously on the delegate queue; never retain mutable Metal buffers.
                var vertices: [SIMD3<Float>] = []
                for index in 0..<source.count {
                    let raw = source.buffer.contents().advanced(by: source.offset + index * source.stride)
                    let point = SIMD4<Float>(raw.loadUnaligned(as: Float.self), raw.loadUnaligned(fromByteOffset: 4, as: Float.self), raw.loadUnaligned(fromByteOffset: 8, as: Float.self), 1)
                    let world = anchor.transform * point
                    vertices.append(SIMD3(world.x, world.y, world.z))
                }
                var triangles: [SIMD3<Int32>] = []
                for face in 0..<faces.count {
                    var indices: [Int32] = []
                    for corner in 0..<3 {
                        let raw = faces.buffer.contents().advanced(by: (face * 3 + corner) * faces.bytesPerIndex)
                        let value = faces.bytesPerIndex == 4 ? raw.loadUnaligned(as: UInt32.self) : UInt32(raw.loadUnaligned(as: UInt16.self))
                        guard value < source.count, value <= Int32.max else { throw CaptureError.invalid("Неверный индекс ARMeshAnchor") }
                        indices.append(Int32(value))
                    }
                    triangles.append(SIMD3(indices[0], indices[1], indices[2]))
                }
                meshAnchors[anchor.identifier] = GeometryMesh(vertices: vertices, triangles: triangles)
            } catch { meshError = error.localizedDescription; meshAnchors.removeValue(forKey: anchor.identifier) }
        }
    }
    func meshSnapshot() throws -> GeometryMesh {
        guard supportsMesh else { throw CaptureError.invalid("Geometry ZIP требует iPhone с LiDAR и поддержкой ARKit mesh") }
        if let meshError { throw CaptureError.invalid(meshError) }
        var vertices: [SIMD3<Float>] = [], triangles: [SIMD3<Int32>] = []
        for key in meshAnchors.keys.sorted(by: { $0.uuidString < $1.uuidString }) {
            guard let mesh = meshAnchors[key], vertices.count + mesh.vertices.count <= Int(Int32.max) else { throw CaptureError.invalid("Слишком большая сетка") }
            let offset = Int32(vertices.count)
            vertices.append(contentsOf: mesh.vertices)
            triangles.append(contentsOf: mesh.triangles.map { $0 &+ SIMD3(repeating: offset) })
        }
        return GeometryMesh(vertices: vertices, triangles: triangles)
    }

    /// Called on a worker queue. Retain only this one ARFrame while encoding;
    /// the UI gates acquisition to a single outstanding save.
    nonisolated static func encode(_ frame: ARFrame) throws -> CapturePayload {
        let scene = frame.sceneDepth
        let image = CIImage(cvPixelBuffer: frame.capturedImage)
        let context = CIContext()
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let rgb = context.jpegRepresentation(of: image, colorSpace: space, options: [:]) else {
            throw CaptureError.invalid("Не удалось сохранить RGB")
        }
        let depth = try scene.map { try packed($0.depthMap, format: kCVPixelFormatType_DepthFloat32, bytesPerPixel: 4) }
        var confidence: Data?
        if let scene, let map = scene.confidenceMap {
            guard CVPixelBufferGetWidth(map) == CVPixelBufferGetWidth(scene.depthMap),
                  CVPixelBufferGetHeight(map) == CVPixelBufferGetHeight(scene.depthMap) else {
                throw CaptureError.invalid("Размер confidence не соответствует depth")
            }
            confidence = try packed(map, format: kCVPixelFormatType_OneComponent8, bytesPerPixel: 1)
        }
        let k = frame.camera.intrinsics
        let t = frame.camera.transform
        return CapturePayload(
            rgb: rgb, depth: depth, confidence: confidence,
            rgbSize: [CVPixelBufferGetWidth(frame.capturedImage), CVPixelBufferGetHeight(frame.capturedImage)],
            depthSize: scene.map { [CVPixelBufferGetWidth($0.depthMap), CVPixelBufferGetHeight($0.depthMap)] },
            intrinsics: matrixRows(k), pose: matrixRows(t),
            timestamp: frame.timestamp, origin: "device", tracking: "normal")
    }

    nonisolated static func packed(_ buffer: CVPixelBuffer, format: OSType, bytesPerPixel: Int) throws -> Data {
        guard CVPixelBufferGetPixelFormatType(buffer) == format,
              !CVPixelBufferIsPlanar(buffer), CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else {
            throw CaptureError.invalid("Неподдерживаемый формат depth/confidence")
        }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { throw CaptureError.invalid("Пустой буфер") }
        let rowBytes = CVPixelBufferGetWidth(buffer) * bytesPerPixel
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        guard stride >= rowBytes else { throw CaptureError.invalid("Неверный шаг строки") }
        var data = Data(capacity: rowBytes * height)
        for row in 0..<height { data.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: rowBytes) }
        return data
    }
}

enum SyntheticCapture {
    /// A repeatable fronto-parallel plane at two metres; not a room scan.
    static func make() throws -> CapturePayload {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 640, height: 480), format: format).image { ctx in
            UIColor(red: 0.86, green: 0.89, blue: 0.93, alpha: 1).setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 640, height: 480))
            UIColor.white.setStroke()
            for x in stride(from: 0, through: 640, by: 40) {
                ctx.cgContext.move(to: CGPoint(x: x, y: 0)); ctx.cgContext.addLine(to: CGPoint(x: x, y: 480))
            }
            for y in stride(from: 0, through: 480, by: 40) {
                ctx.cgContext.move(to: CGPoint(x: 0, y: y)); ctx.cgContext.addLine(to: CGPoint(x: 640, y: y))
            }
            ctx.cgContext.strokePath()
            ("SYNTHETIC · PLANE 2 m" as NSString).draw(at: CGPoint(x: 32, y: 220), withAttributes: [
                .font: UIFont.boldSystemFont(ofSize: 30), .foregroundColor: UIColor.darkGray])
        }
        guard let rgb = image.jpegData(compressionQuality: 0.95) else { throw CaptureError.invalid("Ошибка тестового JPEG") }
        var depth = Data()
        for _ in 0..<(64 * 48) {
            var value = Float(2).bitPattern.littleEndian
            withUnsafeBytes(of: &value) { depth.append(contentsOf: $0) }
        }
        return CapturePayload(rgb: rgb, depth: depth, confidence: Data(repeating: 2, count: 64 * 48),
                              rgbSize: [640, 480], depthSize: [64, 48],
                              intrinsics: [[500,0,320],[0,500,240],[0,0,1]],
                              pose: [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
                              timestamp: ProcessInfo.processInfo.systemUptime, origin: "synthetic", tracking: "synthetic")
    }
}
