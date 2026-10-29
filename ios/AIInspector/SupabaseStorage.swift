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
        guard result == errSecSuccess, let data = item as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty else { throw CaptureError.invalid("Введите device token Inspector в настройках") }
        return token
    }
    static func save(_ token: String, scope: String) throws {
        let attrs: [String: Any] = [kSecValueData as String: Data(token.utf8), kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let result = SecItemUpdate(query(scope) as CFDictionary, attrs as CFDictionary)
        if result == errSecItemNotFound {
            guard SecItemAdd(query(scope).merging(attrs) { _, new in new } as CFDictionary, nil) == errSecSuccess else { throw CaptureError.invalid("Не удалось сохранить device token") }
        } else if result != errSecSuccess { throw CaptureError.invalid("Не удалось обновить device token") }
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
                    TextField("HTTPS-адрес", text: $endpoint).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("device_id", text: $deviceID).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Device token", text: $token).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Если токен уже сохранён, оставьте поле пустым.").font(.caption)
                    Button("Сохранить и подключиться") {
                        do { try connection.configure(endpoint: endpoint, device: deviceID, token: token); token = ""; error = nil }
                        catch { self.error = error.localizedDescription }
                    }
                    Text(connection.status).font(.footnote)
                    if let error { Text(error).foregroundStyle(.red) }
                }
                Text("Фото загружаются прямо в Supabase по временным ссылкам Inspector. Вход Supabase не нужен. Device token хранится в Keychain.").font(.footnote)
                Section("Локализация по BIM") {
                    NavigationLink("Настройки Localization Service") { LocalizationSettingsView() }
                        .disabled(connection.active != nil)
                    if connection.active != nil { Text("Настройки доступны после завершения текущей сессии.").font(.caption) }
                }
                Button("Разрешить уведомления") { Task { await PushService.shared.enable() } }
            }.navigationTitle("Подключение").toolbar { Button("Готово") { dismiss() } }
        }
    }
}
