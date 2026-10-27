import ARKit
import AVFoundation
import CoreImage
import UIKit
import Combine

@MainActor
final class CaptureService: NSObject, ObservableObject, ARSessionDelegate {
    let session = ARSession()
    @Published var status = "Камера остановлена"
    @Published var running = false
    var onInterruption: (() -> Void)?
    private var segmentStartedAt: TimeInterval?

    override init() {
        super.init()
        session.delegate = self
    }

    func start() async throws {
        guard ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) else {
            throw CaptureError.invalid("Для захвата нужен реальный iPhone с LiDAR. В симуляторе используйте деморежим.")
        }
        let allowed = await AVCaptureDevice.requestAccess(for: .video)
        guard allowed else { throw CaptureError.invalid("Разрешите доступ к камере в Настройках → AI Inspector.") }
        guard UIApplication.shared.applicationState == .active else {
            throw CaptureError.invalid("Вернитесь в приложение и включите камеру снова")
        }
        let config = ARWorldTrackingConfiguration()
        config.frameSemantics = .sceneDepth
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
        guard frame.sceneDepth != nil else { throw CaptureError.invalid("Карта глубины ещё не готова") }
        return frame
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

    /// Called on a worker queue. Retain only this one ARFrame while encoding;
    /// the UI gates acquisition to a single outstanding save.
    nonisolated static func encode(_ frame: ARFrame) throws -> CapturePayload {
        guard let scene = frame.sceneDepth else { throw CaptureError.invalid("Нет sceneDepth") }
        let image = CIImage(cvPixelBuffer: frame.capturedImage)
        let context = CIContext()
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let rgb = context.jpegRepresentation(of: image, colorSpace: space, options: [:]) else {
            throw CaptureError.invalid("Не удалось сохранить RGB")
        }
        let depth = try packed(scene.depthMap, format: kCVPixelFormatType_DepthFloat32, bytesPerPixel: 4)
        var confidence: Data?
        if let map = scene.confidenceMap {
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
            depthSize: [CVPixelBufferGetWidth(scene.depthMap), CVPixelBufferGetHeight(scene.depthMap)],
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
