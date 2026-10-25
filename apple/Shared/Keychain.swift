import Foundation
import Security

/// Token storage. The read-only API token lives only in the Keychain, in an access group shared
/// by the app and its widget (on iPhone) or the Watch app and its complication (on the Watch).
/// It is never written to UserDefaults, files or logs.
enum Keychain {
    private static let service = "cz.martinpohl.maja.api"
    private static let account = "read-token"

    /// "<TeamID>.cz.martinpohl.maja.shared", taken from Info.plist (MajaKeychainGroup), which
    /// XcodeGen fills from $(AppIdentifierPrefix).
    private static var accessGroup: String? {
        guard let g = Bundle.main.object(forInfoDictionaryKey: "MajaKeychainGroup") as? String,
              !g.isEmpty, !g.hasPrefix("$(") else { return nil }
        return g
    }

    private static func baseQuery() -> [String: Any] {
        var q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if let accessGroup { q[kSecAttrAccessGroup as String] = accessGroup }
        return q
    }

    static func readToken() -> String? {
        var q = baseQuery()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func saveToken(_ token: String?) -> Bool {
        SecItemDelete(baseQuery() as CFDictionary)
        guard let token, !token.isEmpty else { return true }
        var q = baseQuery()
        q[kSecValueData as String] = Data(token.utf8)
        // Readable by the widget in the background after the first unlock; never synced to iCloud.
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }
}
