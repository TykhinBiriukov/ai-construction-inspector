import Foundation

struct UploadObject: Codable, Sendable {
    let observation_id: String?
    let name: String
    let media_type: String
    let byte_length: Int
    let sha256: String
}
struct UploadPlanResponse: Decodable {
    struct Item: Decodable {
        let observation_id: String?
        let name: String
        let status: String
        let object_key: String
        let media_type: String
        let byte_length: Int
        let sha256: String
        let method: String?
        let upload_url: URL?
        let headers: [String: String]
    }
    let format: String
    let request_id: String
    let session_id: String
    let items: [Item]
}
final class SignedStorageTransport: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func put(bytes: Data, plan: UploadPlanResponse.Item) async throws {
        guard let url = plan.upload_url, url.scheme == "https", url.host == "drzxapqypaxhocrksciq.supabase.co",
              url.user == nil, url.password == nil, url.fragment == nil,
              url.path == "/storage/v1/object/upload/sign/inspection-captures/" + plan.object_key,
              plan.method == "PUT", !plan.headers.keys.contains(where: { ["authorization", "apikey", "host"].contains($0.lowercased()) }),
              bytes.count == plan.byte_length, StoredFile(name: "", data: bytes).sha256 == plan.sha256 else { throw CaptureError.invalid("Неверный подписанный план Supabase") }
        var req = URLRequest(url: url); req.httpMethod = "PUT"; req.timeoutInterval = 60
        for (name, value) in plan.headers { req.setValue(value, forHTTPHeaderField: name) }
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (_, response) = try await session.upload(for: req, from: bytes)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw CaptureError.invalid("Supabase не подтвердил загрузку. Повтор запросит свежий план и проверит существующий файл.")
        }
    }
}
