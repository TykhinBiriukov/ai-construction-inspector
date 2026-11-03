import Foundation

/// A hint to refresh an authenticated assignment; never an authoritative session.
struct CapturePush: Equatable {
    let eventID: String
    let sessionID: String
    let isDemo: Bool

    init?(_ userInfo: [AnyHashable: Any]) {
        guard userInfo["schema_version"] as? String == "0.1",
              userInfo["type"] as? String == "capture.assigned",
              let event = userInfo["event_id"] as? String, UUID(uuidString: event) != nil,
              let session = userInfo["session_id"] as? String, UUID(uuidString: session) != nil
        else { return nil }
        eventID = event
        sessionID = session
        isDemo = userInfo["local_demo"] as? Bool == true
    }
}

struct PushRegistration: Encodable {
    let schema_version = "0.1"
    let provider = "apns"
    let token: String
    let environment: String
    let topic: String
}

/// Called by the future authenticated Inspector client; no credentials in app config.
/// Use a dedicated session that refuses redirects so bearer tokens cannot leave I.
final class PushRegistrationClient: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func register(baseURL: URL, deviceID: String, bearer: String, registration: PushRegistration) async throws {
        guard baseURL.scheme == "https", baseURL.host != nil,
              baseURL.user == nil, baseURL.password == nil,
              baseURL.query == nil, baseURL.fragment == nil,
              !deviceID.isEmpty, deviceID.utf8.allSatisfy({
                  (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
              }), !bearer.isEmpty,
              ["development", "production"].contains(registration.environment)
        else { throw URLError(.badURL) }
        let url = baseURL.appendingPathComponent("v1/devices").appendingPathComponent(deviceID).appendingPathComponent("push-registration")
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.timeoutInterval = 20
        request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(registration)
        let config = URLSessionConfiguration.ephemeral
        let session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, [200, 201, 204].contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
}
