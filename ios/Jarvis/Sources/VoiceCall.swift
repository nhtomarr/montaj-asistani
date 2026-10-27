import AVFoundation
import Foundation

/// Jarvis ilə zəng: ElevenLabs WebSocket + AVAudioEngine.
/// Fon audio rejimi sayəsində telefon kilidli olanda və başqa proqrama keçəndə də işləyir.
final class VoiceCall: ObservableObject {
    enum Phase: String {
        case idle = "ZƏNGƏ HAZIR", connecting = "QOŞULUR…", listening = "DİNLƏYİRƏM", speaking = "DANIŞIRAM", ended = "ZƏNG BİTDİ"
    }

    @Published var phase: Phase = .idle
    @Published var connected = false
    @Published var userText = ""
    @Published var agentText = ""
    @Published var level: Float = 0
    @Published var error: String?

    private var ws: URLSessionWebSocketTask?
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var converter: AVAudioConverter?
    private let sendFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private var playFormat = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
    private var playUntil = Date.distantPast
    private var ticker: Timer?
    private let lock = NSLock()

    init() {
        // Adi telefon zəngi (və ya başqa audio) gələndə Jarvis dərhal mikrofonu buraxır — zəngə mane olmasın
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil,
                                               queue: .main) { [weak self] note in
            guard let self, self.connected,
                  let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
            self.error = nil
            self.stop()
        }
    }

    // MARK: - Zəng

    func start(server: String, token: String) {
        guard !connected, phase != .connecting else { return }
        error = nil
        userText = ""
        agentText = ""
        phase = .connecting
        AVAudioSession.sharedInstance().requestRecordPermission { granted in
            DispatchQueue.main.async {
                guard granted else {
                    self.fail("Mikrofon icazəsi yoxdur: Ayarlar → Jarvis → Mikrofon")
                    return
                }
                Task { await self.connect(server: server, token: token) }
            }
        }
    }

    private func connect(server: String, token: String) async {
        do {
            let base = server.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            guard var comps = URLComponents(string: base + "/api/jarvis/voice-ws") else { throw JarvisError("Server ünvanı səhvdir") }
            comps.queryItems = [URLQueryItem(name: "t", value: token.trimmingCharacters(in: .whitespacesAndNewlines))]
            guard let url = comps.url else { throw JarvisError("Server ünvanı səhvdir") }
            let (data, resp) = try await URLSession.shared.data(from: url)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 403 { throw JarvisError("Açar səhvdir — Ayarlarda yoxlayın") }
            guard code == 200 else { throw JarvisError("Server cavabı: \(code)") }
            let json = (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            guard let signed = json["signed_url"] as? String, let wsURL = URL(string: signed) else {
                throw JarvisError("Səs bağlantısı alınmadı")
            }
            let key = json["session_key"] as? String ?? "none"

            try startAudio()
            let task = URLSession.shared.webSocketTask(with: wsURL)
            setWS(task)
            task.resume()
            send(["type": "conversation_initiation_client_data", "dynamic_variables": ["session_key": key]])
            receive(task)
            DispatchQueue.main.async {
                self.connected = true
                self.phase = .listening
                self.startTicker()
            }
        } catch {
            DispatchQueue.main.async { self.fail(error.localizedDescription) }
        }
    }

    func stop() {
        let task = currentWS()
        setWS(nil)
        task?.cancel(with: .normalClosure, reason: nil)
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        DispatchQueue.main.async {
            self.ticker?.invalidate()
            self.ticker = nil
            self.connected = false
            self.level = 0
            if self.error == nil { self.phase = .ended } else { self.phase = .idle }
        }
    }

    private func fail(_ message: String) {
        error = message
        stop()
    }

    // MARK: - Audio

    private func startAudio() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat,
                                options: [.defaultToSpeaker, .allowBluetooth, .allowBluetoothA2DP])
        try session.setActive(true)

        let input = engine.inputNode
        try? input.setVoiceProcessingEnabled(true)  // əks-səsi (echo) kəsir
        if !engine.attachedNodes.contains(player) { engine.attach(player) }
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)

        let inFormat = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inFormat, to: sendFormat)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buffer, _ in
            self?.onMic(buffer)
        }
        engine.prepare()
        try engine.start()
        player.play()
    }

    private func onMic(_ buffer: AVAudioPCMBuffer) {
        guard currentWS() != nil, let conv = converter else { return }
        let ratio = sendFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: sendFormat, frameCapacity: capacity) else { return }
        var given = false
        var err: NSError?
        conv.convert(to: out, error: &err) { _, status in
            if given {
                status.pointee = .noDataNow
                return nil
            }
            given = true
            status.pointee = .haveData
            return buffer
        }
        guard err == nil, out.frameLength > 0, let ch = out.int16ChannelData else { return }
        let n = Int(out.frameLength)
        var peak = 0
        for i in 0..<n { peak = max(peak, abs(Int(ch[0][i]))) }
        let data = Data(bytes: ch[0], count: n * MemoryLayout<Int16>.size)
        send(["user_audio_chunk": data.base64EncodedString()])
        let lv = min(1, Float(peak) / 12000)
        DispatchQueue.main.async { if self.phase != .speaking { self.level = lv } }
    }

    private func play(_ data: Data) {
        let n = data.count / 2
        guard n > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(n)),
              let dst = buf.floatChannelData?[0] else { return }
        buf.frameLength = AVAudioFrameCount(n)
        var peak: Float = 0
        data.withUnsafeBytes { raw in
            let src = raw.bindMemory(to: Int16.self)
            for i in 0..<n {
                let v = Float(Int16(littleEndian: src[i])) / 32768
                dst[i] = v
                peak = max(peak, abs(v))
            }
        }
        player.scheduleBuffer(buf, completionHandler: nil)
        if !player.isPlaying { player.play() }
        let dur = Double(n) / playFormat.sampleRate
        DispatchQueue.main.async {
            self.playUntil = max(self.playUntil, Date()).addingTimeInterval(dur)
            self.phase = .speaking
            self.level = min(1, peak * 1.6)
        }
    }

    private func startTicker() {
        ticker?.invalidate()
        ticker = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self, self.connected else { return }
            if self.phase == .speaking && Date() > self.playUntil {
                self.phase = .listening
                self.level = 0
            }
        }
    }

    // MARK: - WebSocket

    private func setWS(_ t: URLSessionWebSocketTask?) { lock.lock(); ws = t; lock.unlock() }
    private func currentWS() -> URLSessionWebSocketTask? { lock.lock(); defer { lock.unlock() }; return ws }

    private func send(_ obj: [String: Any]) {
        guard let task = currentWS(), let d = try? JSONSerialization.data(withJSONObject: obj),
              let s = String(data: d, encoding: .utf8) else { return }
        task.send(.string(s)) { _ in }
    }

    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self, self.currentWS() === task else { return }
            switch result {
            case .failure(let e):
                DispatchQueue.main.async {
                    if self.connected { self.fail("Bağlantı kəsildi: \(e.localizedDescription)") }
                }
            case .success(let msg):
                var text: String?
                switch msg {
                case .string(let s): text = s
                case .data(let d): text = String(data: d, encoding: .utf8)
                @unknown default: break
                }
                if let t = text, let d = t.data(using: .utf8),
                   let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
                    self.handle(j)
                }
                self.receive(task)
            }
        }
    }

    private func handle(_ m: [String: Any]) {
        switch m["type"] as? String ?? "" {
        case "ping":
            if let ev = m["ping_event"] as? [String: Any], let id = ev["event_id"] {
                send(["type": "pong", "event_id": id])
            }
        case "audio":
            if let ev = m["audio_event"] as? [String: Any], let b = ev["audio_base_64"] as? String,
               let d = Data(base64Encoded: b) {
                play(d)
            }
        case "agent_response":
            if let ev = m["agent_response_event"] as? [String: Any], let s = ev["agent_response"] as? String {
                DispatchQueue.main.async { self.agentText = VoiceCall.clean(s) }
            }
        case "user_transcript":
            if let ev = m["user_transcription_event"] as? [String: Any], let s = ev["user_transcript"] as? String {
                DispatchQueue.main.async {
                    self.userText = s
                    self.agentText = ""
                }
            }
        case "interruption":
            player.stop()
            player.play()
            DispatchQueue.main.async {
                self.playUntil = .distantPast
                self.phase = .listening
            }
        case "conversation_initiation_metadata":
            if let ev = m["conversation_initiation_metadata_event"] as? [String: Any],
               let f = ev["agent_output_audio_format"] as? String,
               let rate = Double(f.split(separator: "_").last ?? ""), rate != playFormat.sampleRate,
               let fmt = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1) {
                playFormat = fmt
                engine.disconnectNodeOutput(player)
                engine.connect(player, to: engine.mainMixerNode, format: fmt)
                player.play()
            }
        default:
            break
        }
    }

    static func clean(_ s: String) -> String {
        s.replacingOccurrences(of: #"\[[a-zA-Z ]{2,20}\]\s*"#, with: "", options: .regularExpression)
    }
}

struct JarvisError: LocalizedError {
    let message: String
    init(_ m: String) { message = m }
    var errorDescription: String? { message }
}
