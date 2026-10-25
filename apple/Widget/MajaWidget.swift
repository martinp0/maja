import WidgetKit
import SwiftUI

/// Home Screen / Lock Screen widget (iPhone) and complication (Apple Watch): where the dog is,
/// distance from home, how old the fix is, battery. Same source file for both extensions.
struct DogEntry: TimelineEntry {
    let date: Date
    let snapshot: DogSnapshot?
    let message: String?
}

struct DogProvider: TimelineProvider {
    func placeholder(in context: Context) -> DogEntry { DogEntry(date: Date(), snapshot: .preview, message: nil) }

    func getSnapshot(in context: Context, completion: @escaping (DogEntry) -> Void) {
        if context.isPreview { return completion(placeholder(in: context)) }
        completion(DogEntry(date: Date(), snapshot: MajaSettings.lastSnapshot, message: nil))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<DogEntry>) -> Void) {
        Task {
            let entry: DogEntry
            do {
                entry = DogEntry(date: Date(), snapshot: try await MajaAPI.refresh(), message: nil)
            } catch {
                // Show the last known position rather than nothing.
                entry = DogEntry(date: Date(), snapshot: MajaSettings.lastSnapshot,
                                 message: MajaSettings.isConfigured ? "Offline" : "Nastav v appce")
            }
            // WidgetKit budgets refreshes; ask for one every 15 minutes.
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15 * 60))))
        }
    }
}

struct MajaWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "MajaDog", provider: DogProvider()) { entry in
            DogWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Mája")
        .description("Kde je pes: doma/venku, vzdálenost od domova a baterie.")
        #if os(watchOS)
        .supportedFamilies([.accessoryCircular, .accessoryRectangular, .accessoryInline, .accessoryCorner])
        #else
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
        #endif
    }
}

@main
struct MajaWidgetBundle: WidgetBundle {
    var body: some Widget { MajaWidget() }
}

private let home = Color(red: 0x24 / 255, green: 0x8a / 255, blue: 0x3d / 255)
private let away = Color(red: 0x9a / 255, green: 0x4d / 255, blue: 0)
private let bad = Color(red: 0xd7 / 255, green: 0, blue: 0x15 / 255)

struct DogWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: DogEntry

    private var snap: DogSnapshot? { entry.snapshot }
    private var color: Color {
        guard let s = snap else { return .secondary }
        return s.state == .home ? home : s.escaped ? bad : away
    }
    private var distance: String {
        guard let s = snap else { return "–" }
        return s.state == .home ? "doma" : Fmt.distance(s.distanceFromHomeM)
    }

    var body: some View {
        switch family {
        case .accessoryInline:
            Text(snap.map { "🐾 \(Fmt.state($0)) · \(distance)" } ?? "🐾 Mája")
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 0) {
                    Image(systemName: snap?.state == .home ? "house.fill" : "pawprint.fill").font(.caption)
                    Text(snap?.state == .home ? "doma" : shortDistance).font(.system(.caption2, design: .rounded).bold())
                        .minimumScaleFactor(0.6)
                }
            }
            .widgetAccentable()
        #if os(watchOS)
        case .accessoryCorner:
            Image(systemName: snap?.state == .home ? "house.fill" : "pawprint.fill")
                .widgetLabel { Text(snap.map { "\(Fmt.state($0)) \(distance)" } ?? "Mája") }
        #endif
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 1) {
                Text(snap?.petName ?? "Mája").font(.headline).widgetAccentable()
                Text(snap.map { "\(Fmt.state($0)) · \(distance)" } ?? (entry.message ?? "–"))
                Text(snap.map { "\(Fmt.ago($0.fixAt, now: entry.date))\($0.batteryPct.map { " · \($0) %" } ?? "")" } ?? "")
                    .foregroundStyle(.secondary)
            }
            .font(.caption)
            .frame(maxWidth: .infinity, alignment: .leading)
        default:
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Image(systemName: "pawprint.fill").foregroundStyle(color)
                    Text(snap?.petName ?? "Mája").font(.headline)
                    Spacer()
                    if let b = snap?.batteryPct { Text("\(b) %").font(.caption).foregroundStyle(b <= 15 ? bad : .secondary) }
                }
                Spacer()
                if let s = snap {
                    Text(Fmt.state(s)).font(.title2.bold()).foregroundStyle(color)
                    if s.state != .home { Text("\(Fmt.distance(s.distanceFromHomeM)) od domova").font(.subheadline) }
                    Text("Poloha \(Fmt.ago(s.fixAt, now: entry.date))").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(entry.message ?? "Otevři appku").font(.subheadline).foregroundStyle(.secondary)
                }
                if let m = entry.message, snap != nil { Text(m).font(.caption2).foregroundStyle(.secondary) }
            }
        }
    }

    private var shortDistance: String {
        guard let m = snap?.distanceFromHomeM else { return "?" }
        return m < 1000 ? "\(Int(m))m" : String(format: "%.1fkm", m / 1000)
    }
}
