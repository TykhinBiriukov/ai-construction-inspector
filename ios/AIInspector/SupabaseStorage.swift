import Foundation
import Security
import SwiftUI

enum InspectorCredential {
    static func query(_ scope: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.aiinspector.device-token", kSecAttrAccount as String: scope]
    }
    static func read(scope: String) throws -> String {
        var q = query(scope); q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?; let result = SecItemCopyMatching(q as CFDictionary, &item)
        guard result == errSecSuccess, let data = item as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty else { throw CaptureError.invalid("Enter the Inspector device token in Settings") }
        return token
    }
    static func save(_ token: String, scope: String) throws {
        let attrs: [String: Any] = [kSecValueData as String: Data(token.utf8), kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let result = SecItemUpdate(query(scope) as CFDictionary, attrs as CFDictionary)
        if result == errSecItemNotFound {
            guard SecItemAdd(query(scope).merging(attrs) { _, new in new } as CFDictionary, nil) == errSecSuccess else { throw CaptureError.invalid("Could not save the device token") }
        } else if result != errSecSuccess { throw CaptureError.invalid("Could not update the device token") }
    }
}
final class StorageHTTP: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
struct StorageConnectionView: View {
    @ObservedObject private var connection = InspectorConnection.shared
    @Environment(\.dismiss) private var dismiss
    @State private var endpoint = UserDefaults.standard.string(forKey: "inspector.endpoint") ?? InspectorConnection.defaultEndpoint
    @State private var deviceID = UserDefaults.standard.string(forKey: "inspector.device") ?? InspectorConnection.defaultDevice
    @State private var token = ""
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Section("Inspector") {
                    TextField("HTTPS URL", text: $endpoint).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("device_id", text: $deviceID).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Device token", text: $token).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Leave this field blank if a token is already saved.").font(.caption)
                    Button("Save and connect") {
                        do { try connection.configure(endpoint: endpoint, device: deviceID, token: token); token = ""; error = nil }
                        catch { self.error = error.localizedDescription }
                    }
                    Text(connection.status).font(.footnote)
                    if let error { Text(error).foregroundStyle(.red) }
                }
                Text("Photos upload directly to Supabase using temporary Inspector links. No Supabase sign-in is needed. The device token is stored in Keychain.").font(.footnote)
                Section("BIM localization") {
                    NavigationLink("Localization Service settings") { LocalizationSettingsView() }
                        .disabled(connection.active != nil)
                    if connection.active != nil { Text("Settings are available after the current session ends.").font(.caption) }
                }
                Button("Enable notifications") { Task { await PushService.shared.enable() } }
            }.navigationTitle("Connection").toolbar { Button("Done") { dismiss() } }
        }
    }
}
