import SwiftUI
import UserNotifications
import Combine

@MainActor
final class PushService: ObservableObject {
    static let shared = PushService()
    @Published private(set) var status = "Локальные уведомления · Inspector ещё не подключён"
    @Published private(set) var pendingSessionID: String?
    @Published private(set) var pendingIsDemo = false
    private var scheduling = Set<String>()
    private(set) var deviceToken: String?
    // Supplied only after authenticated Inspector setup, never read from a push payload.
    var registerToken: ((PushRegistration) async throws -> Void)?
    private var uploading = false
    private var lastEventID: String?

    private init() {
        let stored = UserDefaults.standard.string(forKey: "push.pendingSession")
        pendingSessionID = stored.flatMap { UUID(uuidString: $0) != nil ? $0 : nil }
        pendingIsDemo = UserDefaults.standard.bool(forKey: "push.pendingIsDemo")
    }

    var enabledInBuild: Bool {
        Bundle.main.object(forInfoDictionaryKey: "AIInspectorPushEnabled") as? String == "YES"
    }

    func enable() async {
        do {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
            guard allowed else { status = "Уведомления отключены в настройках iPhone"; return }
            if enabledInBuild {
                UIApplication.shared.registerForRemoteNotifications()
                status = "Регистрируем устройство в APNs…"
            } else {
                status = "Локальные уведомления разрешены. Новые задания получаем только при открытом приложении."
            }
        } catch { status = "Не удалось запросить уведомления. Повторите позже." }
    }

    func refresh() async {
        guard enabledInBuild else { return }
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard [.authorized, .provisional, .ephemeral].contains(settings.authorizationStatus) else {
            status = "Уведомления не разрешены"; return
        }
        UIApplication.shared.registerForRemoteNotifications()
    }

    /// Invoke only after an authenticated assignment has been persisted by the Inspector client.
    /// No background polling or remote delivery is implied by this local notification.
    func notifyAssignment(sessionID: String, isDemo: Bool = false) async {
        guard UUID(uuidString: sessionID) != nil,
              UIApplication.shared.applicationState == .active else { return }
        let key = (isDemo ? "demo-" : "assignment-") + sessionID
        var delivered = UserDefaults.standard.stringArray(forKey: "push.notifiedAssignments") ?? []
        guard !delivered.contains(key), scheduling.insert(key).inserted else { return }
        defer { scheduling.remove(key) }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional, .ephemeral].contains(settings.authorizationStatus) else {
            status = "Сначала разрешите уведомления"; return
        }
        let content = UNMutableNotificationContent()
        content.title = isDemo ? "AI Inspector · ТЕСТ" : "AI Inspector"
        content.body = isDemo ? "Тестовое задание. Нажмите, чтобы открыть приложение." : "Вам назначена новая съёмка. Откройте задание."
        content.sound = .default
        content.userInfo = ["schema_version": "0.1", "type": "capture.assigned",
                            "event_id": UUID().uuidString.lowercased(), "session_id": sessionID, "local_demo": isDemo]
        do {
            let trigger = isDemo ? UNTimeIntervalNotificationTrigger(timeInterval: 3, repeats: false) : nil
            try await center.add(UNNotificationRequest(identifier: key, content: content, trigger: trigger))
            // Persist after successful scheduling. Stable request IDs also replace crash-time retries.
            if !isDemo { delivered.append(key); UserDefaults.standard.set(delivered, forKey: "push.notifiedAssignments") }
            status = isDemo ? "Тестовое уведомление появится через 3 секунды; это не ответ Inspector." : "Уведомление о назначении создано на телефоне"
        } catch { status = "Не удалось показать уведомление. Назначение остаётся доступно в приложении." }
    }

    func testLocalNotification() async {
        await notifyAssignment(sessionID: UUID().uuidString.lowercased(), isDemo: true)
    }

    func registered(_ token: Data) {
        // APNs tokens are variable-length and kept only in memory; refreshed every launch.
        deviceToken = token.map { String(format: "%02x", $0) }.joined()
        Task { await syncToken() }
    }

    func syncToken() async {
        guard !uploading, let token = deviceToken else { return }
        guard let registerToken else { status = "APNs готов; подключение к Inspector ещё не настроено"; return }
        guard let environment = Bundle.main.object(forInfoDictionaryKey: "AIInspectorAPNSEnvironment") as? String,
              ["development", "production"].contains(environment), let topic = Bundle.main.bundleIdentifier else {
            status = "Не настроено окружение APNs"; return
        }
        uploading = true
        defer { uploading = false }
        do {
            try await registerToken(PushRegistration(token: token, environment: environment, topic: topic))
            status = "Устройство зарегистрировано для уведомлений"
        } catch { status = "Не удалось зарегистрировать push в Inspector. Повторим при открытии приложения." }
        // If APNs rotated the token during the request, send the newer token too.
        if deviceToken != token { Task { await self.syncToken() } }
    }

    func failed() { status = "APNs недоступен: проверьте подпись Push Notifications и сеть." }

    func receive(_ push: CapturePush) {
        guard push.eventID != lastEventID else { return }
        lastEventID = push.eventID
        pendingSessionID = push.sessionID
        pendingIsDemo = push.isDemo
        UserDefaults.standard.set(push.isDemo, forKey: "push.pendingIsDemo")
        UserDefaults.standard.set(push.sessionID, forKey: "push.pendingSession")
        status = push.isDemo ? "Открыто тестовое уведомление. Живого задания нет." : "Получено назначение. Требуется проверка сессии через Inspector."
        // Never replace local capture IDs or start the camera from an unverified push.
    }

    func clearPending() {
        pendingSessionID = nil
        pendingIsDemo = false
        UserDefaults.standard.removeObject(forKey: "push.pendingIsDemo")
        UserDefaults.standard.removeObject(forKey: "push.pendingSession")
    }
}

final class InspectorAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        Task { @MainActor in await PushService.shared.refresh() }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
        Task { @MainActor in PushService.shared.registered(token) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushService.shared.failed() }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        guard CapturePush(notification.request.content.userInfo) != nil else { completionHandler([]); return }
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
           let push = CapturePush(response.notification.request.content.userInfo) {
            Task { @MainActor in
                PushService.shared.receive(push)
                completionHandler()
            }
        } else { completionHandler() }
    }
}
