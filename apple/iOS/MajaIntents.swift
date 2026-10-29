import AppIntents
import WidgetKit

/// „Kde je Mája?“ ze Zkratek, Spotlightu, tlačítka Akce nebo Siri (Siri jen v podporovaném jazyce,
/// čeština zatím ne). Odpoví bez otevření appky; když server neodpoví, použije poslední známou polohu.
struct DogStatusIntent: AppIntent {
    static var title: LocalizedStringResource = "Kde je pes"
    static var description = IntentDescription("Řekne, jestli je pes doma, jak daleko je, jak stará je poloha a kolik má obojek baterie.")
    static var openAppWhenRun = false

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let snapshot: DogSnapshot
        do {
            snapshot = try await MajaAPI.refresh()
            WidgetCenter.shared.reloadAllTimelines()
        } catch {
            guard let cached = MajaSettings.lastSnapshot else { throw error }
            snapshot = cached
        }
        let text = Self.summary(snapshot)
        return .result(value: text, dialog: "\(text)")
    }

    static func summary(_ snap: DogSnapshot, now: Date = Date()) -> String {
        var parts = ["\(snap.petName): \(Fmt.state(snap))"]
        if snap.state != .home, let d = snap.distanceFromHomeM { parts.append("\(Fmt.distance(d)) od domova") }
        parts.append("poloha \(Fmt.ago(snap.fixAt, now: now))")
        if let b = snap.batteryPct { parts.append("baterie \(b) %" + (snap.charging == true ? " (nabíjí se)" : "")) }
        return parts.joined(separator: ", ") + "."
    }
}

/// Otevře appku rovnou na mapě (pro tlačítko Akce a Zkratky).
struct OpenDogIntent: AppIntent {
    static var title: LocalizedStringResource = "Otevřít Máju"
    static var description = IntentDescription("Otevře appku s mapou a aktuální polohou psa.")
    static var openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult { .result() }
}

struct MajaShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: DogStatusIntent(),
            phrases: [
                "Where is my dog in \(.applicationName)",
                "Check my dog in \(.applicationName)",
                "\(.applicationName) status",
            ],
            shortTitle: "Kde je pes",
            systemImageName: "pawprint.fill"
        )
        AppShortcut(
            intent: OpenDogIntent(),
            phrases: ["Open the dog map in \(.applicationName)"],
            shortTitle: "Mapa psa",
            systemImageName: "map.fill"
        )
    }

    static var shortcutTileColor: ShortcutTileColor = .blue
}
