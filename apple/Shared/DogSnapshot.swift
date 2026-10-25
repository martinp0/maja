import Foundation
import CoreLocation

/// Everything the app, widget and complication show, combined from the server's
/// /api/trackers, /api/trackers/{id}/latest and /api/geofence/state.
struct DogSnapshot: Codable, Equatable, Sendable {
    enum HomeState: String, Codable, Sendable { case home, away, unknown }

    var petName: String
    var trackerId: String
    var latitude: Double?
    var longitude: Double?
    var accuracyM: Double?
    var sensor: String?
    var fixAt: Date?
    var batteryPct: Int?
    var charging: Bool?
    var state: HomeState
    /// Away and further than GEOFENCE_ESCAPE_M ("Utekla" in the PWA).
    var escaped: Bool = false
    var stateSince: Date?
    /// Distance from home in metres (server-side, needs HOME_LAT/HOME_LON on the server).
    var distanceFromHomeM: Double?
    var homeLatitude: Double?
    var homeLongitude: Double?
    var homeRadiusM: Double?
    var fetchedAt: Date

    var coordinate: CLLocationCoordinate2D? {
        guard let latitude, let longitude else { return nil }
        return CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }

    var homeCoordinate: CLLocationCoordinate2D? {
        guard let homeLatitude, let homeLongitude else { return nil }
        return CLLocationCoordinate2D(latitude: homeLatitude, longitude: homeLongitude)
    }

    func distance(from location: CLLocation) -> Double? {
        guard let latitude, let longitude else { return nil }
        return location.distance(from: CLLocation(latitude: latitude, longitude: longitude))
    }

    static let preview = DogSnapshot(
        petName: "Luna", trackerId: "DEMO", latitude: 40.7740, longitude: -73.9708, accuracyM: 12, sensor: "GPS",
        fixAt: Date().addingTimeInterval(-120), batteryPct: 64, charging: false, state: .away, escaped: false,
        stateSince: Date().addingTimeInterval(-1800), distanceFromHomeM: 1240, homeLatitude: 40.7681,
        homeLongitude: -73.9819, homeRadiusM: 80, fetchedAt: Date()
    )
}
