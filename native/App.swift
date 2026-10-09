import AppKit
import WebKit
import UniformTypeIdentifiers

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backend: Process?
    var baseURL: URL?
    var startup = Data()
    var terminating = false
    var started = false
    var logHandle: FileHandle?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let other = NSRunningApplication.runningApplications(withBundleIdentifier: "labs.nightly.stradale-studio").first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            other.activate(options: [.activateAllWindows]); NSApp.terminate(nil); return
        }
        let menu = NSMenu()
        let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About Stradale Studio", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Stradale Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem); let editMenu = NSMenu(title: "Edit"); editItem.submenu = editMenu
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        NSApp.mainMenu = menu
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "native")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self; webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Stradale Studio"; window.minSize = NSSize(width: 850, height: 650)
        window.contentView = webView; window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<html style='background:#131713;color:#bddfa2;font:18px -apple-system'><body style='padding:60px'>Starting Stradale Studio…</body></html>", baseURL: nil)
        launchBackend()
    }

    func launchBackend() {
        do {
            let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Stradale Studio")
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            let logURL = support.appendingPathComponent("app.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            logHandle = try FileHandle(forWritingTo: logURL)
            let process = Process()
            process.executableURL = Bundle.main.resourceURL!.appendingPathComponent("backend/stradale-server")
            process.arguments = ["--data-dir", support.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
            process.environment = ProcessInfo.processInfo.environment.merging(["STRADALE_TOKEN": UUID().uuidString + UUID().uuidString]) { _, new in new }
            let pipe = Pipe(); process.standardOutput = pipe; process.standardError = logHandle
            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil; return }
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.startup.append(data)
                    if !self.started, let text = String(data: self.startup, encoding: .utf8), let line = text.split(separator: "\n").first(where: { $0.hasPrefix("READY ") }), let url = URL(string: String(line.dropFirst(6))) {
                        self.started = true
                        self.baseURL = URL(string: "http://127.0.0.1:\(url.port!)/")
                        self.webView.load(URLRequest(url: url))
                        self.startup.removeAll()
                    }
                }
            }
            process.terminationHandler = { [weak self] process in
                DispatchQueue.main.async {
                    guard let self else { return }
                    if self.terminating { NSApp.reply(toApplicationShouldTerminate: true) }
                    else { self.showError("The local processor stopped (code \(process.terminationStatus)). Reopen the app. The queue is saved. Log: \(logURL.path)") }
                }
            }
            backend = process
            try process.run()
            DispatchQueue.main.asyncAfter(deadline: .now() + 45) { [weak self] in
                guard let self, !self.started, !self.terminating else { return }
                self.showError("The local processor did not start. See \(logURL.path).")
            }
        } catch { showError(error.localizedDescription) }
    }

    func showError(_ message: String) {
        let alert = NSAlert(); alert.messageText = "Stradale Studio"; alert.informativeText = message; alert.runModal()
    }

    func trusted(_ url: URL?) -> Bool {
        guard let url, let baseURL else { return false }
        return url.scheme == baseURL.scheme && url.host == baseURL.host && url.port == baseURL.port
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        decisionHandler(trusted(url) || (baseURL == nil && url?.scheme == "about") ? .allow : .cancel)
    }

    func reply(_ id: String, _ value: Any = NSNull(), error: String? = nil) {
        let args: [Any] = [id, value, error as Any? ?? NSNull()]
        guard let data = try? JSONSerialization.data(withJSONObject: args), let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.nativeResponse(...\(json))", completionHandler: nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, trusted(message.frameInfo.request.url),
              let body = message.body as? [String: Any], let action = body["action"] as? String, let id = body["id"] as? String else { return }
        if action == "chooseFolder" {
            let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
            panel.canCreateDirectories = body["kind"] as? String == "output"
            panel.prompt = "Choose folder"
            panel.message = body["kind"] as? String == "output" ? "Choose a separate folder for the results." : "Choose the folder with your car photos."
            panel.beginSheetModal(for: window) { response in self.reply(id, response == .OK ? (panel.url?.path as Any? ?? NSNull()) : NSNull()) }
        } else if action == "openFolder", let path = body["path"] as? String {
            var directory: ObjCBool = false
            if FileManager.default.fileExists(atPath: path, isDirectory: &directory), directory.boolValue {
                NSWorkspace.shared.open(URL(fileURLWithPath: path)); reply(id, true)
            } else { reply(id, error: "That output folder is not available.") }
        } else if action == "saveImage" || action == "saveText" {
            let content: Data?
            if action == "saveImage" { content = (body["image"] as? String).flatMap { Data(base64Encoded: $0) } }
            else { content = (body["text"] as? String)?.data(using: .utf8) }
            guard let content else { reply(id, error: "The export data is not valid."); return }
            let panel = NSSavePanel(); panel.nameFieldStringValue = action == "saveImage" ? "stradale.png" : "stradale-report.csv"
            panel.allowedContentTypes = action == "saveImage" ? [.png] : [.commaSeparatedText]
            panel.beginSheetModal(for: window) { response in
                guard response == .OK, let url = panel.url else { self.reply(id); return }
                do { try content.write(to: url, options: .atomic); self.reply(id, url.path) }
                catch { self.reply(id, error: error.localizedDescription) }
            }
        } else { reply(id, error: "Unknown app action.") }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.png, .jpeg, .webP]; panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let backend, backend.isRunning else { return .terminateNow }
        if terminating { return .terminateLater }
        terminating = true; backend.terminate()
        window.title = "Stradale Studio — saving queue…"
        DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
            if backend.isRunning { kill(backend.processIdentifier, SIGKILL); NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
