import SwiftUI
import WidgetKit

@main
struct MajaApp: App {
    @StateObject private var model = DogModel()

    init() { ConfigSync.shared.activate() }

    var body: some Scene {
        WindowGroup {
            DogView()
                .environmentObject(model)
                .tint(.majaTint)
        }
    }
}

extension Color {
    // Colours from web/src/styles.css
    static let majaTint = Color(red: 0, green: 0x7a / 255, blue: 1)          // --tint
    static let majaHome = Color(red: 0x24 / 255, green: 0x8a / 255, blue: 0x3d / 255) // --home
    static let majaAway = Color(red: 0x9a / 255, green: 0x4d / 255, blue: 0)          // --away
    static let majaBad = Color(red: 0xd7 / 255, green: 0, blue: 0x15 / 255)           // --bad
}

@MainActor
final class DogModel: ObservableObject {
    @Published private(set) var snapshot: DogSnapshot? = MajaSettings.lastSnapshot
    @Published private(set) var error: String?
    @Published private(set) var loading = false
    @Published var configured = MajaSettings.isConfigured

    func refresh() async {
        guard MajaSettings.isConfigured else { configured = false; return }
        configured = true
        loading = true
        defer { loading = false }
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

    func settingsSaved() {
        configured = MajaSettings.isConfigured
        snapshot = nil
        MajaSettings.lastSnapshot = nil
        ConfigSync.shared.pushToWatch()
        WidgetCenter.shared.reloadAllTimelines()
        Task { await refresh() }
    }
}
