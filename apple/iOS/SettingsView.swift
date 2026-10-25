import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    let onSave: () -> Void

    @State private var server = MajaSettings.serverURL?.absoluteString ?? ""
    @State private var token = ""
    @State private var hasToken = Keychain.readToken() != nil
    @State private var testing = false
    @State private var result: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://maja.example.com", text: $server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                } header: { Text("Server") } footer: {
                    Text("Adresa, na které otevíráš PWA. Mimo domácí síť musí být https.")
                }
                Section {
                    SecureField(hasToken ? "Uložený token (beze změny)" : "Token", text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    if hasToken {
                        Button("Smazat token", role: .destructive) {
                            Keychain.saveToken(nil); hasToken = false; token = ""
                        }
                    }
                } header: { Text("Read-only token") } footer: {
                    Text("Jedna z hodnot API_READ_TOKENS v .env na serveru (openssl rand -hex 32). Token umí jen číst polohu, baterii a doma/venku, povely ne. Ukládá se jen do Klíčenky tohoto zařízení a do Apple Watch.")
                }
                Section {
                    Button { Task { await test() } } label: {
                        HStack { Text("Ověřit připojení"); if testing { Spacer(); ProgressView() } }
                    }
                    .disabled(testing || MajaSettings.normalize(server) == nil || (token.isEmpty && !hasToken))
                    if let result { Text(result).font(.footnote) }
                }
            }
            .navigationTitle("Nastavení")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Zrušit") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Uložit") { save() }.disabled(MajaSettings.normalize(server) == nil)
                }
            }
        }
    }

    private func currentToken() -> String? { token.isEmpty ? Keychain.readToken() : token.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func test() async {
        guard let url = MajaSettings.normalize(server), let tok = currentToken() else { return }
        testing = true
        defer { testing = false }
        do {
            let snap = try await MajaAPI(baseURL: url, token: tok).fetchSnapshot()
            result = "✅ Funguje: \(snap.petName), \(Fmt.state(snap).lowercased())."
        } catch {
            result = "❌ \(error.localizedDescription)"
        }
    }

    private func save() {
        guard let url = MajaSettings.normalize(server) else { return }
        MajaSettings.serverURL = url
        if !token.isEmpty { Keychain.saveToken(token.trimmingCharacters(in: .whitespacesAndNewlines)) }
        onSave()
        dismiss()
    }
}
