import SwiftUI
import MapKit
import WidgetKit

@main
struct MajaWatchApp: App {
    init() { ConfigSync.shared.activate() }

    var body: some Scene {
        WindowGroup { WatchDogView() }
    }
}

@MainActor
final class WatchModel: ObservableObject {
    @Published private(set) var snapshot: DogSnapshot? = MajaSettings.lastSnapshot
    @Published private(set) var error: String?
    @Published private(set) var configured = MajaSettings.isConfigured

    func refresh() async {
        configured = MajaSettings.isConfigured
        guard configured else { return }
        do {
            let old = snapshot
            snapshot = try await MajaAPI.refresh()
            error = nil
            if old != snapshot { WidgetCenter.shared.reloadAllTimelines() }
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct WatchDogView: View {
    @StateObject private var model = WatchModel()
    @Environment(\.scenePhase) private var phase

    private let home = Color(red: 0x24 / 255, green: 0x8a / 255, blue: 0x3d / 255)
    private let away = Color(red: 0x9a / 255, green: 0x4d / 255, blue: 0)
    private let bad = Color(red: 0xd7 / 255, green: 0, blue: 0x15 / 255)

    var body: some View {
        NavigationStack {
            Group {
                if !model.configured {
                    VStack(spacing: 8) {
                        Image(systemName: "iphone").font(.title2)
                        Text("Nastav server a token v appce Mája na iPhonu.").multilineTextAlignment(.center).font(.footnote)
                    }
                } else if let s = model.snapshot {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(Fmt.state(s)).font(.title3.bold())
                                .foregroundStyle(s.state == .home ? home : s.escaped ? bad : away)
                            if s.state != .home { Text("\(Fmt.distance(s.distanceFromHomeM)) od domova") }
                            Text("Poloha \(Fmt.ago(s.fixAt))").font(.footnote).foregroundStyle(.secondary)
                            if let b = s.batteryPct { Label("\(b) %", systemImage: "battery.50").font(.footnote) }
                            if let c = s.coordinate {
                                Map(initialPosition: .region(MKCoordinateRegion(center: c, latitudinalMeters: 500, longitudinalMeters: 500))) {
                                    if let h = s.homeCoordinate {
                                        MapCircle(center: h, radius: s.homeRadiusM ?? 80).foregroundStyle(home.opacity(0.2))
                                    }
                                    Marker(s.petName, systemImage: "pawprint.fill", coordinate: c)
                                }
                                .frame(height: 120)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                            }
                            if let e = model.error { Text(e).font(.caption2).foregroundStyle(bad) }
                        }
                    }
                } else if let e = model.error {
                    Text(e).font(.footnote)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle(model.snapshot?.petName ?? "Mája")
        }
        .task(id: phase) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(30))
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .majaConfigChanged)) { _ in
            Task { await model.refresh() }
        }
    }
}
