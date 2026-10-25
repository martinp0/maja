import SwiftUI
import MapKit
import CoreLocation

struct DogView: View {
    @EnvironmentObject private var model: DogModel
    @Environment(\.scenePhase) private var phase
    @StateObject private var me = MyLocation()
    @State private var showSettings = false
    @State private var camera: MapCameraPosition = .automatic

    var body: some View {
        NavigationStack {
            Group {
                if !model.configured {
                    ContentUnavailableView {
                        Label("Připoj svůj server", systemImage: "pawprint")
                    } description: {
                        Text("Zadej adresu serveru Mája a read-only token z API_READ_TOKENS.")
                    } actions: {
                        Button("Nastavit") { showSettings = true }.buttonStyle(.borderedProminent)
                    }
                } else if let snap = model.snapshot {
                    content(snap)
                } else if let err = model.error {
                    ContentUnavailableView("Nejde načíst", systemImage: "wifi.exclamationmark", description: Text(err))
                } else {
                    ProgressView("Načítám…")
                }
            }
            .navigationTitle(model.snapshot?.petName ?? "Mája")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Nastavení")
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView { model.settingsSaved() } }
            .refreshable { await model.refresh() }
            .task(id: phase) {
                // Poll every 30 s while the app is in the foreground, like the PWA.
                guard phase == .active else { return }
                while !Task.isCancelled {
                    await model.refresh()
                    try? await Task.sleep(for: .seconds(30))
                }
            }
        }
    }

    @ViewBuilder
    private func content(_ snap: DogSnapshot) -> some View {
        ScrollView {
            VStack(spacing: 12) {
                if let coord = snap.coordinate {
                    Map(position: $camera) {
                        if let home = snap.homeCoordinate {
                            MapCircle(center: home, radius: snap.homeRadiusM ?? 80)
                                .foregroundStyle(Color.majaHome.opacity(0.15))
                                .stroke(Color.majaHome, lineWidth: 1)
                        }
                        if let acc = snap.accuracyM, acc > 15 {
                            MapCircle(center: coord, radius: acc).foregroundStyle(Color.majaTint.opacity(0.12))
                        }
                        Annotation(snap.petName, coordinate: coord) {
                            Image(systemName: "pawprint.circle.fill")
                                .font(.title).foregroundStyle(.white, Color.majaTint)
                        }
                        UserAnnotation()
                    }
                    .frame(height: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .onAppear { camera = .region(MKCoordinateRegion(center: coord, latitudinalMeters: 600, longitudinalMeters: 600)) }
                }

                StatusCard(snap: snap, distanceFromMe: me.location.flatMap(snap.distance(from:)))

                if let err = model.error {
                    Label(err, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(Color.majaBad)
                }

                HStack {
                    if let coord = snap.coordinate {
                        Button {
                            let item = MKMapItem(placemark: MKPlacemark(coordinate: coord))
                            item.name = snap.petName
                            item.openInMaps(launchOptions: [MKLaunchOptionsDirectionsModeKey: MKLaunchOptionsDirectionsModeWalking])
                        } label: { Label("Navigovat", systemImage: "figure.walk") }
                        .buttonStyle(.borderedProminent)
                    }
                    if me.status == .notDetermined {
                        Button { me.request() } label: { Label("Vzdálenost ode mě", systemImage: "location") }
                            .buttonStyle(.bordered)
                    }
                }
            }
            .padding()
        }
    }
}

struct StatusCard: View {
    let snap: DogSnapshot
    let distanceFromMe: Double?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(Fmt.state(snap))
                    .font(.title2.bold())
                    .foregroundStyle(snap.state == .home ? Color.majaHome : snap.escaped ? Color.majaBad : Color.majaAway)
                if let since = snap.stateSince { Text("· \(Fmt.ago(since))").foregroundStyle(.secondary) }
                Spacer()
                if let b = snap.batteryPct {
                    Label("\(b) %", systemImage: snap.charging == true ? "battery.100.bolt" : batteryIcon(b))
                        .foregroundStyle(b <= 15 ? Color.majaBad : .primary)
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow { Text("Od domova").foregroundStyle(.secondary); Text(Fmt.distance(snap.distanceFromHomeM)) }
                if let d = distanceFromMe {
                    GridRow { Text("Ode mě").foregroundStyle(.secondary); Text(Fmt.distance(d)) }
                }
                GridRow { Text("Poslední poloha").foregroundStyle(.secondary); Text(Fmt.ago(snap.fixAt)) }
                if let s = Fmt.sensor(snap.sensor) {
                    GridRow {
                        Text("Zdroj").foregroundStyle(.secondary)
                        Text(snap.accuracyM.map { "\(s) · ±\(Int($0)) m" } ?? s)
                    }
                }
            }
            .font(.subheadline)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 16))
    }

    private func batteryIcon(_ b: Int) -> String {
        b > 75 ? "battery.100" : b > 50 ? "battery.75" : b > 25 ? "battery.50" : b > 10 ? "battery.25" : "battery.0"
    }
}

/// Optional "distance from me". Location is used only on the device and never sent anywhere.
@MainActor
final class MyLocation: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published private(set) var location: CLLocation?
    @Published private(set) var status: CLAuthorizationStatus
    private let manager = CLLocationManager()

    override init() {
        status = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        if status == .authorizedWhenInUse || status == .authorizedAlways { manager.startUpdatingLocation() }
    }

    func request() { manager.requestWhenInUseAuthorization() }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let s = manager.authorizationStatus
        Task { @MainActor in
            self.status = s
            if s == .authorizedWhenInUse || s == .authorizedAlways { self.manager.startUpdatingLocation() }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let last = locations.last else { return }
        Task { @MainActor in self.location = last }
    }
}
