# AI Inspector iPhone

Current integration: [server contract](../docs/inspector-server-integration.md), [implementation and remaining checks](../docs/mobile-integration-status.md).

Native SwiftUI / ARKit app, deployment target iOS 17. iPhone 11 supports RGB-only observations; compatible LiDAR devices support depth and optional local Geometry ZIP export. Simulator cannot validate live ARKit/LiDAR.

Open `AIInspector.xcodeproj`. In the app's settings enter Inspector HTTPS origin, device ID and device token. Token is stored in Keychain; do not put it in source or Info.plist. No Supabase Auth login is required in the selected signed-upload transport. A new assigned session comes from Inspector; the waiting screen does not create demo sessions.

Local tests from repository root:

```sh
xcrun swiftc -module-cache-path /tmp/inspector-swift-cache ios/AIInspector/Observation.swift ios/AIInspector/SessionLifecycle.swift ios/AIInspector/GeometryExport.swift ios/Checks/main.swift -o /tmp/inspector-checks
/tmp/inspector-checks
```

Existing captures remain in the app's Documents/sessions directory. A failed upload never deletes local originals. The mock Geometry server validates exchange only; it does not reconstruct a room.
