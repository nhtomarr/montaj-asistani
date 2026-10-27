import SwiftUI

@main
struct JarvisApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

struct ContentView: View {
    @StateObject private var call = VoiceCall()
    @AppStorage("server") private var server = "https://37-27-41-45.sslip.io"
    @AppStorage("token") private var token = ""
    @State private var showSettings = false

    private var tint: Color { call.phase == .speaking ? Color(red: 0.29, green: 1, blue: 0.63) : Color(red: 0.36, green: 0.78, blue: 1) }

    var body: some View {
        ZStack {
            Color(red: 0.008, green: 0.04, blue: 0.08).ignoresSafeArea()
            VStack(spacing: 18) {
                HStack {
                    Spacer()
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape").font(.title3).foregroundColor(tint.opacity(0.6))
                    }
                }
                Text("J.A.R.V.I.S").font(.system(size: 17, weight: .semibold, design: .monospaced)).kerning(7).foregroundColor(tint)
                Text(call.phase.rawValue).font(.system(size: 13, design: .monospaced)).kerning(3).foregroundColor(tint.opacity(0.8))
                Spacer()
                Orb(level: CGFloat(call.level), color: tint, busy: call.phase == .connecting)
                    .frame(width: 270, height: 270)
                    .contentShape(Circle())
                    .onTapGesture { if !call.connected { startCall() } }
                Spacer()
                VStack(spacing: 8) {
                    if !call.userText.isEmpty {
                        Text("› " + call.userText).font(.footnote).foregroundColor(tint.opacity(0.75))
                    }
                    Text(call.agentText).font(.body).foregroundColor(.white)
                }
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, minHeight: 100, alignment: .top)
                if let e = call.error {
                    Text(e).font(.footnote).foregroundColor(.red).multilineTextAlignment(.center)
                }
                Button(action: { call.connected ? call.stop() : startCall() }) {
                    Text(call.connected ? "Zəngi bitir" : "📞  Zəng et")
                        .font(.headline)
                        .padding(.horizontal, 40).padding(.vertical, 17)
                        .background(call.connected ? Color.red : Color(red: 0.29, green: 1, blue: 0.63))
                        .foregroundColor(call.connected ? .white : .black)
                        .clipShape(Capsule())
                }
            }
            .padding(24)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings) { SettingsView(server: $server, token: $token) }
        .onAppear { if token.isEmpty { showSettings = true } }
        .onOpenURL { url in
            // jarvis://setup?t=AÇAR  və ya  jarvis://call (Siri / Arxaya toxun qısayolu üçün)
            let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
            if let t = comps?.queryItems?.first(where: { $0.name == "t" })?.value, !t.isEmpty { token = t }
            if url.host == "call" { startCall() }
        }
    }

    private func startCall() {
        guard !token.isEmpty else { showSettings = true; return }
        call.start(server: server, token: token)
    }
}

struct Orb: View {
    var level: CGFloat
    var color: Color
    var busy: Bool

    var body: some View {
        TimelineView(.animation) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate * (busy ? 4 : 1)
            ZStack {
                Circle().stroke(color.opacity(0.55), lineWidth: 2)
                    .rotationEffect(.degrees(t * 40))
                Circle().trim(from: 0, to: 0.82).stroke(color.opacity(0.35), style: StrokeStyle(lineWidth: 2, dash: [6, 8]))
                    .padding(24).rotationEffect(.degrees(-t * 26))
                Circle().stroke(color.opacity(0.22), lineWidth: 1).padding(54)
                Circle()
                    .fill(RadialGradient(colors: [color.opacity(0.95), color.opacity(0.25), .clear], center: .center,
                                         startRadius: 2, endRadius: 70))
                    .padding(80)
                    .scaleEffect(0.85 + level * 0.9)
                    .shadow(color: color.opacity(0.5), radius: 30)
                    .animation(.linear(duration: 0.08), value: level)
            }
        }
    }
}

struct SettingsView: View {
    @Binding var server: String
    @Binding var token: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("Server")) {
                    TextField("https://...", text: $server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                Section(header: Text("Açar"), footer: Text("Telegram-da /panel yazın — linkdəki t= hissəsindən sonrakı açarı bura yapışdırın.")) {
                    TextField("açar", text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            }
            .navigationTitle("Ayarlar")
            .toolbar { Button("Hazır") { dismiss() } }
        }
    }
}
