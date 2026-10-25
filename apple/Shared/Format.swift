import Foundation

/// Czech formatting, same wording as the PWA.
enum Fmt {
    static let cs = Locale(identifier: "cs_CZ")

    static func distance(_ m: Double?) -> String {
        guard let m else { return "–" }
        if m < 1000 { return "\(Int(m.rounded())) m" }
        return (m / 1000).formatted(.number.precision(.fractionLength(1)).locale(cs)) + " km"
    }

    static func ago(_ date: Date?, now: Date = Date()) -> String {
        guard let date else { return "neznámo" }
        let s = Int(now.timeIntervalSince(date))
        if s < 60 { return "teď" }
        if s < 3600 { return "před \(s / 60) min" }
        if s < 86_400 { return "před \(s / 3600) h" }
        return date.formatted(.dateTime.day().month().hour().minute().locale(cs))
    }

    static func state(_ snap: DogSnapshot) -> String {
        switch snap.state {
        case .home: return "Doma"
        case .away: return snap.escaped ? "Utekla" : "Venku"
        case .unknown: return "Neznámo"
        }
    }

    /// Same labels as sensorLabel() in web/src/format.ts.
    static func sensor(_ s: String?) -> String? {
        switch (s ?? "").uppercased() {
        case "": return nil
        case "GPS": return "GPS"
        case "KNOWN_WIFI": return "Domácí WiFi"
        case "WIFI": return "WiFi"
        case "BLE": return "Bluetooth"
        case "GSM", "CELL": return "Mobilní síť"
        default: return s
        }
    }
}
