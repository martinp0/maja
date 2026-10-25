import Foundation

/// Read-only client for the Mája server. Sends `Authorization: Bearer <token>`; the server accepts
/// it only for GET on the read endpoints (see API_READ_TOKENS in .env.example).
struct MajaAPI: Sendable {
    let baseURL: URL
    let token: String

    enum APIError: LocalizedError {
        case notConfigured, unauthorized, forbidden, noTracker, server(Int), badResponse

        var errorDescription: String? {
            switch self {
            case .notConfigured: return "Nastav adresu serveru a token."
            case .unauthorized: return "Server token nepřijal. Zkontroluj API_READ_TOKENS v .env."
            case .forbidden: return "Token nemá na tohle právo."
            case .noTracker: return "Na serveru není žádný obojek."
            case .server(let code): return "Server odpověděl chybou \(code)."
            case .badResponse: return "Odpovědi serveru nerozumím."
            }
        }
    }

    static func fromSettings() throws -> MajaAPI {
        guard let url = MajaSettings.serverURL, let token = Keychain.readToken() else { throw APIError.notConfigured }
        return MajaAPI(baseURL: url, token: token)
    }

    private static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral // no cookies, no cache on disk
        c.timeoutIntervalForRequest = 15
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: c)
    }()

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        var comps = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, resp) = try await Self.session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw APIError.badResponse }
        switch http.statusCode {
        case 200..<300: break
        case 401: throw APIError.unauthorized
        case 403: throw APIError.forbidden
        default: throw APIError.server(http.statusCode)
        }
        do { return try Self.decoder.decode(T.self, from: data) } catch { throw APIError.badResponse }
    }

    // MARK: Wire types (subset of web/src/api.ts)

    private struct Tracker: Decodable {
        let tracker_id: String
        let pet_name: String?
        let battery_pct: Int?
    }

    private struct Latest: Decodable {
        struct Position: Decodable { let ts: Date; let lat: Double; let lng: Double; let accuracy_m: Double?; let sensor_used: String? }
        struct HW: Decodable { let battery_pct: Int?; let charging: Bool? }
        let position: Position?
        let hw: HW?
    }

    private struct Geofence: Decodable {
        struct Home: Decodable { let lat: Double; let lng: Double }
        let enabled: Bool
        let state: String?
        let since: Date?
        let distance_m: Double?
        let home_radius_m: Double?
        let home: Home?
        let in_home_zone: Bool?
        let escaped: Bool?
    }

    // MARK: Snapshot

    func fetchSnapshot() async throws -> DogSnapshot {
        let trackers: [Tracker] = try await get("api/trackers")
        guard let t = trackers.first else { throw APIError.noTracker }
        async let latestReq: Latest = get("api/trackers/\(t.tracker_id)/latest")
        async let geoReq: Geofence = get("api/geofence/state", query: [URLQueryItem(name: "tracker_id", value: t.tracker_id)])
        let latest = try await latestReq
        let geo = try? await geoReq // geofence is optional on the server

        var state = DogSnapshot.HomeState(rawValue: geo?.state ?? "") ?? .unknown
        if state == .unknown, let inZone = geo?.in_home_zone { state = inZone ? .home : .away }
        return DogSnapshot(
            petName: t.pet_name ?? "Pes",
            trackerId: t.tracker_id,
            latitude: latest.position?.lat,
            longitude: latest.position?.lng,
            accuracyM: latest.position?.accuracy_m,
            sensor: latest.position?.sensor_used,
            fixAt: latest.position?.ts,
            batteryPct: latest.hw?.battery_pct ?? t.battery_pct,
            charging: latest.hw?.charging,
            state: state,
            escaped: geo?.escaped ?? false,
            stateSince: geo?.since,
            distanceFromHomeM: geo?.distance_m,
            homeLatitude: geo?.home?.lat,
            homeLongitude: geo?.home?.lng,
            homeRadiusM: geo?.home_radius_m,
            fetchedAt: Date()
        )
    }

    /// Fetches and caches the snapshot for the widget/complication.
    static func refresh() async throws -> DogSnapshot {
        let snap = try await fromSettings().fetchSnapshot()
        MajaSettings.lastSnapshot = snap
        return snap
    }

    /// FastAPI emits ISO 8601 with microseconds and an offset ("2026-10-19T10:00:00.123456+00:00").
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { dec in
            let s = try dec.singleValueContainer().decode(String.self)
            if let date = parseISO(s) { return date }
            throw DecodingError.dataCorrupted(.init(codingPath: dec.codingPath, debugDescription: "Bad date \(s)"))
        }
        return d
    }()

    static func parseISO(_ raw: String) -> Date? {
        // Trim fractional seconds to milliseconds, which ISO8601DateFormatter understands.
        var s = raw.replacingOccurrences(of: #"(\.\d{3})\d+"#, with: "$1", options: .regularExpression)
        if s.hasSuffix("Z") == false, s.range(of: #"[+-]\d{2}:\d{2}$"#, options: .regularExpression) == nil { s += "Z" }
        let withFrac = ISO8601DateFormatter()
        withFrac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = withFrac.date(from: s) { return d }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: s)
    }
}
