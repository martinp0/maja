import Foundation
import WatchConnectivity
#if canImport(WidgetKit)
import WidgetKit
#endif

/// Hands the server URL and token from the iPhone to the Watch over WatchConnectivity
/// (end-to-end encrypted between the paired devices). The Watch keeps the token in its own
/// Keychain, so the Watch app and complication work without the phone nearby.
final class ConfigSync: NSObject, WCSessionDelegate {
    static let shared = ConfigSync()
    private static let urlKey = "serverURL"
    private static let tokenKey = "token"
    private static let homeLatKey = "homeLat"
    private static let homeLngKey = "homeLng"

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    #if os(iOS)
    /// Sends the current settings to the Watch. Called after saving settings and on activation.
    func pushToWatch() {
        let s = WCSession.default
        guard WCSession.isSupported(), s.activationState == .activated, s.isPaired, s.isWatchAppInstalled else { return }
        var ctx: [String: Any] = [:]
        if let url = MajaSettings.serverURL { ctx[Self.urlKey] = url.absoluteString }
        if let token = Keychain.readToken() { ctx[Self.tokenKey] = token }
        if let home = MajaSettings.homeCoordinate {
            ctx[Self.homeLatKey] = home.latitude
            ctx[Self.homeLngKey] = home.longitude
        } else {
            ctx[Self.homeLatKey] = NSNull()
            ctx[Self.homeLngKey] = NSNull()
        }
        try? s.updateApplicationContext(ctx)
    }
    #endif

    // MARK: WCSessionDelegate

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        #if os(iOS)
        pushToWatch()
        #else
        apply(session.receivedApplicationContext)
        #endif
    }

    #if os(iOS)
    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { WCSession.default.activate() }
    func sessionWatchStateDidChange(_ session: WCSession) { pushToWatch() }
    #endif

    #if os(watchOS)
    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        apply(applicationContext)
    }

    private func apply(_ ctx: [String: Any]) {
        guard !ctx.isEmpty else { return }
        MajaSettings.serverURL = (ctx[Self.urlKey] as? String).flatMap(URL.init(string:))
        Keychain.saveToken(ctx[Self.tokenKey] as? String)
        MajaSettings.homeLatitude = (ctx[Self.homeLatKey] as? NSNumber)?.doubleValue
        MajaSettings.homeLongitude = (ctx[Self.homeLngKey] as? NSNumber)?.doubleValue
        MajaSettings.lastSnapshot = nil
        WidgetCenter.shared.reloadAllTimelines()
        DispatchQueue.main.async { NotificationCenter.default.post(name: .majaConfigChanged, object: nil) }
    }
    #endif
}

extension Notification.Name {
    static let majaConfigChanged = Notification.Name("majaConfigChanged")
}
