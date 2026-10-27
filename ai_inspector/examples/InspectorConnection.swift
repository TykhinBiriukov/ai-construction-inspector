import Foundation
import CryptoKit

// Transport reference for iOS integration. Capture/state/UI live in the app.
final class InspectorConnection {
    let baseURL: URL
    let deviceID: String
    private let deviceToken: String
    private var socket: URLSessionWebSocketTask?

    init(baseURL: URL, deviceID: String, deviceToken: String) {
        self.baseURL = baseURL
        self.deviceID = deviceID
        self.deviceToken = deviceToken
    }

    func connect(after cursor: String? = nil) {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = "/v1/devices/\(deviceID)/events/ws"
        if let cursor {
            components.queryItems = [URLQueryItem(name: "after", value: cursor)]
        }
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(deviceToken)", forHTTPHeaderField: "Authorization")
        let task = URLSession.shared.webSocketTask(with: request)
        socket = task
        task.resume()
    }

    // Keep receiving while foreground. Persist each packet's next_cursor only
    // after applying all events. On reconnect, pass that cursor to connect().
    func nextEvent() async throws -> Data {
        guard let socket else { throw URLError(.notConnectedToInternet) }
        while true {
            let message = try await socket.receive()
            let data: Data
            switch message {
            case .data(let value): data = value
            case .string(let value): data = Data(value.utf8)
            @unknown default: throw URLError(.cannotParseResponse)
            }
            if let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               object["type"] as? String == "ping" {
                try await socket.send(.string("{\"type\":\"pong\"}"))
                continue
            }
            return data
        }
    }

    func post(path: String, json: Data) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.httpBody = json
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(deviceToken)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            // Decode the backend's code/message/retryable/details before retrying.
            throw URLError(.badServerResponse)
        }
        return data
    }

    func upload(bytes: Data, signedURL: URL, mediaType: String) async throws {
        // Fresh request: never attach Inspector or Supabase admin credentials.
        var request = URLRequest(url: signedURL)
        request.httpMethod = "PUT"
        request.setValue(mediaType, forHTTPHeaderField: "Content-Type")
        let (_, response) = try await URLSession.shared.upload(for: request, from: bytes)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            // Retry upload-plans with the original request ID to resolve timeouts.
            throw URLError(.badServerResponse)
        }
    }

    static func sha256(_ bytes: Data) -> String {
        SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    func disconnect() {
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
    }
}
