import Foundation

/// Non-secret settings and the last snapshot, shared through the App Group so the widget and
/// complication can show something even when the server is unreachable.
enum MajaSettings {
    static let appGroup = "group.cz.martinpohl.maja"
    static var defaults: UserDefaults { UserDefaults(suiteName: appGroup) ?? .standard }

    private static let serverKey = "serverURL"
    private static let homeLatKey = "homeLat"
    private static let homeLngKey = "homeLng"
    private static let snapshotKey = "lastSnapshot"

    /// Base URL of the Mája server, e.g. https://maja.example.com (no /api suffix).
    static var serverURL: URL? {
        get { defaults.string(forKey: serverKey).flatMap(URL.init(string:)) }
        set { defaults.set(newValue?.absoluteString, forKey: serverKey) }
    }

    static var lastSnapshot: DogSnapshot? {
        get {
            guard let raw = defaults.data(forKey: snapshotKey) else { return nil }
            return try? JSONDecoder().decode(DogSnapshot.self, from: raw)
        }
        set { defaults.set(newValue.flatMap { try? JSONEncoder().encode($0) }, forKey: snapshotKey) }
    }

    static var isConfigured: Bool { serverURL != nil && Keychain.readToken() != nil }

    /// Optional home circle, stored only on the device. Read tokens do not receive home coordinates.
    static var homeLatitude: Double? {
        get { (defaults.object(forKey: homeLatKey) as? NSNumber)?.doubleValue }
        set { setCoord(newValue, forKey: homeLatKey) }
    }

    static var homeLongitude: Double? {
        get { (defaults.object(forKey: homeLngKey) as? NSNumber)?.doubleValue }
        set { setCoord(newValue, forKey: homeLngKey) }
    }

    static var homeCoordinate: (latitude: Double, longitude: Double)? {
        guard let homeLatitude, let homeLongitude else { return nil }
        return (homeLatitude, homeLongitude)
    }

    static func parseCoord(_ raw: String, range: ClosedRange<Double>) -> Double? {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: ".")
        guard !t.isEmpty, let v = Double(t), range.contains(v) else { return nil }
        return v
    }

    private static func setCoord(_ value: Double?, forKey key: String) {
        if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
    }

    /// Accepts "maja.example.com", "https://maja.example.com/" or ".../api". Plain http is allowed
    /// only for local addresses (ATS allows local networking); anything else must be https.
    static func normalize(_ input: String) -> URL? {
        var s = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "https://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix("/api") { s.removeLast(4) }
        guard let url = URL(string: s), let host = url.host, let scheme = url.scheme?.lowercased() else { return nil }
        if scheme == "https" { return url }
        if scheme == "http" && isLocal(host) { return url }
        return nil
    }

    private static func isLocal(_ host: String) -> Bool {
        host == "localhost" || host.hasSuffix(".local") || host.hasPrefix("192.168.") || host.hasPrefix("10.")
            || host.hasPrefix("127.") || host.range(of: #"^172\.(1[6-9]|2\d|3[01])\."#, options: .regularExpression) != nil
    }
}
