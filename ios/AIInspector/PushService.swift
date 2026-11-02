import SwiftUI
import UserNotifications
import Combine

@MainActor
final class PushService: ObservableObject {
    static let shared = PushService()
    @Published private(set) var status = "Local notifications · Inspector is not connected yet"
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
            guard allowed else { status = "Notifications are disabled in iPhone Settings"; return }
            if enabledInBuild {
                UIApplication.shared.registerForRemoteNotifications()
                status = "Registering this device with APNs…"
            } else {
                status = "Local notifications enabled. New assignments are received only while the app is open."
            }
        } catch { status = "Could not request notifications. Please try again later." }
    }

    func refresh() async {
        guard enabledInBuild else { return }
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard [.authorized, .provisional, .ephemeral].contains(settings.authorizationStatus) else {
            status = "Notifications are not allowed"; return
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
            status = "Enable notifications first"; return
        }
        let content = UNMutableNotificationContent()
        content.title = isDemo ? "AI Inspector · TEST" : "AI Inspector"
        content.body = isDemo ? "Test assignment. Tap to open the app." : "You have a new capture assignment. Open it to get started."
        content.sound = .default
        content.userInfo = ["schema_version": "0.1", "type": "capture.assigned",
                            "event_id": UUID().uuidString.lowercased(), "session_id": sessionID, "local_demo": isDemo]
        do {
            let trigger = isDemo ? UNTimeIntervalNotificationTrigger(timeInterval: 3, repeats: false) : nil
            try await center.add(UNNotificationRequest(identifier: key, content: content, trigger: trigger))
            // Persist after successful scheduling. Stable request IDs also replace crash-time retries.
            if !isDemo { delivered.append(key); UserDefaults.standard.set(delivered, forKey: "push.notifiedAssignments") }
            status = isDemo ? "A test notification will appear in 3 seconds. This is not an Inspector response." : "Assignment notification created on this phone"
        } catch { status = "Could not display the notification. The assignment is still available in the app." }
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
        guard let registerToken else { status = "APNs is ready; the Inspector connection is not configured yet"; return }
        guard let environment = Bundle.main.object(forInfoDictionaryKey: "AIInspectorAPNSEnvironment") as? String,
              ["development", "production"].contains(environment), let topic = Bundle.main.bundleIdentifier else {
            status = "APNs environment is not configured"; return
        }
        uploading = true
        defer { uploading = false }
        do {
            try await registerToken(PushRegistration(token: token, environment: environment, topic: topic))
            status = "Device registered for notifications"
        } catch { status = "Could not register push notifications with Inspector. We will retry when you open the app." }
        // If APNs rotated the token during the request, send the newer token too.
        if deviceToken != token { Task { await self.syncToken() } }
    }

    func failed() { status = "APNs is unavailable: check Push Notifications signing and your connection." }

    func receive(_ push: CapturePush) {
        guard push.eventID != lastEventID else { return }
        lastEventID = push.eventID
        pendingSessionID = push.sessionID
        pendingIsDemo = push.isDemo
        UserDefaults.standard.set(push.isDemo, forKey: "push.pendingIsDemo")
        UserDefaults.standard.set(push.sessionID, forKey: "push.pendingSession")
        status = push.isDemo ? "Test notification opened. There is no live assignment." : "Assignment received. The session needs to be verified with Inspector."
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
