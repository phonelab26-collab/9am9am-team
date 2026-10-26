// 9AM9AM-TEAM.app — le logiciel Mac.
//
// Une vraie fenêtre (pas de navigateur) qui affiche l'interface de conversation. Le moteur
// reste en Python : ce programme lance `mini_orchestrateur.py gui --no-browser`, lit
// l'adresse locale qu'il affiche, la charge dans un WKWebView (le moteur web de macOS), et
// arrête proprement le serveur — donc le travail en cours — quand on ferme la fenêtre.
//
// Modes de test (aucun n'est utilisé au quotidien) :
//   --project DIR       dossier du projet (sinon : Info.plist, puis dossier du .app)
//   --dry-run           serveur en mode démo (aucun vrai CLI appelé)
//   --send "texte"      écrit et envoie un message dans la page
//   --wait S            secondes d'attente avant la capture (défaut 3)
//   --snapshot F.png    enregistre une image de la page, affiche son état, puis quitte
//   --timeout S         quitte de force après S secondes
//   --render-icon F.png dessine l'icône et quitte
//   --log F.txt         journal de diagnostic du logiciel (démarrage, chargement de la page)

import Cocoa
import UniformTypeIdentifiers
import WebKit

struct Options {
    var dryRun = false
    var project: String?
    var send: String?
    var wait: Double = 3
    var snapshot: String?
    var timeout: Double?
    var renderIcon: String?
    var log: String?
}

func parseOptions() -> Options {
    var o = Options()
    var args = Array(CommandLine.arguments.dropFirst())
    while !args.isEmpty {
        let a = args.removeFirst()
        switch a {
        case "--dry-run": o.dryRun = true
        case "--project": o.project = args.isEmpty ? nil : args.removeFirst()
        case "--send": o.send = args.isEmpty ? nil : args.removeFirst()
        case "--wait": o.wait = Double(args.isEmpty ? "" : args.removeFirst()) ?? 3
        case "--snapshot": o.snapshot = args.isEmpty ? nil : args.removeFirst()
        case "--timeout": o.timeout = Double(args.isEmpty ? "" : args.removeFirst())
        case "--render-icon": o.renderIcon = args.isEmpty ? nil : args.removeFirst()
        case "--log": o.log = args.isEmpty ? nil : args.removeFirst()
        default: break  // macOS peut ajouter des arguments (-psn_…) : ignorés
        }
    }
    return o
}

func projectDirectory(_ o: Options) -> URL? {
    var candidates: [String] = []
    if let p = o.project { candidates.append(p) }
    if let p = Bundle.main.object(forInfoDictionaryKey: "ProjectPath") as? String { candidates.append(p) }
    candidates.append(Bundle.main.bundleURL.deletingLastPathComponent().path)
    for c in candidates {
        let url = URL(fileURLWithPath: c)
        if FileManager.default.fileExists(atPath: url.appendingPathComponent("mini_orchestrateur.py").path) {
            return url
        }
    }
    return nil
}

// MARK: - Icône (dessinée par le programme : aucune image à distribuer)

func drawIcon(size: CGFloat) -> NSImage {
    // Même langage que l'interface rétro : fond sombre, aplats, barre de titre orange, monospace.
    let ink = NSColor(calibratedRed: 0.059, green: 0.059, blue: 0.075, alpha: 1)      // --bg
    let orange = NSColor(calibratedRed: 0.914, green: 0.329, blue: 0.125, alpha: 1)   // --accent
    let green = NSColor(calibratedRed: 0.200, green: 0.820, blue: 0.478, alpha: 1)    // --term-green
    func label(_ s: String, _ points: CGFloat, _ color: NSColor, in rect: NSRect, dy: CGFloat) {
        let text = NSAttributedString(string: s, attributes: [
            .font: NSFont.monospacedSystemFont(ofSize: points, weight: .heavy),
            .foregroundColor: color,
        ])
        let box = text.size()
        text.draw(at: NSPoint(x: rect.midX - box.width / 2, y: rect.midY - box.height / 2 + dy))
    }
    return NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
        let body = rect.insetBy(dx: size * 0.06, dy: size * 0.06)
        let radius = size * 0.16
        let path = NSBezierPath(roundedRect: body, xRadius: radius, yRadius: radius)

        // Ombre portée dure, décalée (comme --hard-shadow de l'interface).
        NSColor(calibratedWhite: 0, alpha: 0.45).setFill()
        NSBezierPath(roundedRect: body.offsetBy(dx: size * 0.02, dy: -size * 0.025),
                     xRadius: radius, yRadius: radius).fill()

        NSGraphicsContext.current?.saveGraphicsState()
        path.addClip()
        ink.setFill()
        body.fill()
        let barHeight = body.height * 0.17
        let bar = NSRect(x: body.minX, y: body.maxY - barHeight, width: body.width, height: barHeight)
        orange.setFill()
        bar.fill()
        // Trois carrés de fenêtre dans la barre.
        let dot = barHeight * 0.36
        for i in 0..<3 {
            ink.setFill()
            NSRect(x: body.minX + size * 0.055 + CGFloat(i) * dot * 1.6,
                   y: bar.midY - dot / 2, width: dot, height: dot).fill()
        }
        // Filet intérieur (clippé : pas de débordement sur l'ombre), comme le cadre des cartes.
        orange.setStroke()
        path.lineWidth = max(2, size * 0.024)
        path.stroke()
        NSGraphicsContext.current?.restoreGraphicsState()

        let area = NSRect(x: body.minX, y: body.minY, width: body.width, height: body.height - barHeight)
        label("9AM", size * 0.27, orange, in: area, dy: size * 0.07)
        label(">_", size * 0.17, green, in: area, dy: -size * 0.12)
        return true
    }
}

func writePNG(_ image: NSImage, to path: String) -> Bool {
    guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { return false }
    return (try? png.write(to: URL(fileURLWithPath: path))) != nil
}

var logPath: String?

func diag(_ message: String) {
    guard let path = logPath else { return }
    let line = "\(Date()) \(message)\n"
    if let handle = FileHandle(forWritingAtPath: path) {
        handle.seekToEndOfFile()
        handle.write(line.data(using: .utf8) ?? Data())
        handle.closeFile()
    } else {
        try? line.write(toFile: path, atomically: false, encoding: .utf8)
    }
}

func htmlEscape(_ s: String) -> String {
    return s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

func jsString(_ s: String) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: [s]),
          let text = String(data: data, encoding: .utf8) else { return "\"\"" }
    return String(text.dropFirst().dropLast())
}

// MARK: - Application

final class AppController: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let options: Options
    let projectDir: URL
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var output = ""
    var serverURL: URL?
    var quitting = false
    var scenarioStarted = false

    init(options: Options, projectDir: URL) {
        self.options = options
        self.projectDir = projectDir
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        diag("démarrage : projet \(projectDir.path)")
        NSApp.setActivationPolicy(.regular)
        NSApp.applicationIconImage = drawIcon(size: 512)
        buildMenu()
        buildWindow()
        showPage(title: "Démarrage de l'équipe…", body: "Je lance le moteur, un instant.")
        startServer()
        NSApp.activate(ignoringOtherApps: true)
        if let t = options.timeout {
            DispatchQueue.main.asyncAfter(deadline: .now() + t) { NSApp.terminate(nil) }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { return true }

    // Quitter = arrêter le serveur (donc annuler le travail en cours) AVANT de partir :
    // sinon un worker resterait actif sans fenêtre pour le voir.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let p = server, p.isRunning, !quitting else { return .terminateNow }
        quitting = true
        showPage(title: "Arrêt en cours…", body: "Je termine proprement le travail en cours.")
        p.terminate()  // SIGTERM : le serveur annule le job, arrête les workers, puis sort
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(25)
            while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
            if p.isRunning {
                kill(p.processIdentifier, SIGKILL)
                p.waitUntilExit()
            }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    // MARK: fenêtre et menus

    func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "9AM9AM-TEAM"
        window.minSize = NSSize(width: 720, height: 520)
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("9AM9AM-TEAM.main")
        window.makeKeyAndOrderFront(nil)
    }

    func item(_ menu: NSMenu, _ title: String, _ action: Selector?, _ key: String = "",
              _ mods: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) {
        let it = NSMenuItem(title: title, action: action, keyEquivalent: key)
        it.keyEquivalentModifierMask = mods
        it.target = target
        menu.addItem(it)
    }

    func submenu(_ main: NSMenu, _ title: String) -> NSMenu {
        let holder = NSMenuItem()
        let menu = NSMenu(title: title)
        holder.submenu = menu
        main.addItem(holder)
        return menu
    }

    func buildMenu() {
        let main = NSMenu()
        let app = submenu(main, "9AM9AM-TEAM")
        item(app, "À propos de 9AM9AM-TEAM", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))
        app.addItem(.separator())
        item(app, "Masquer 9AM9AM-TEAM", #selector(NSApplication.hide(_:)), "h")
        app.addItem(.separator())
        item(app, "Quitter 9AM9AM-TEAM", #selector(NSApplication.terminate(_:)), "q")

        let edit = submenu(main, "Édition")
        item(edit, "Annuler", Selector(("undo:")), "z")
        item(edit, "Rétablir", Selector(("redo:")), "z", [.command, .shift])
        edit.addItem(.separator())
        item(edit, "Couper", #selector(NSText.cut(_:)), "x")
        item(edit, "Copier", #selector(NSText.copy(_:)), "c")
        item(edit, "Coller", #selector(NSText.paste(_:)), "v")
        item(edit, "Tout sélectionner", #selector(NSText.selectAll(_:)), "a")

        let view = submenu(main, "Présentation")
        item(view, "Recharger la page", #selector(reloadPage), "r", target: self)
        item(view, "Zoom avant", #selector(zoomIn), "+", target: self)
        item(view, "Zoom arrière", #selector(zoomOut), "-", target: self)
        item(view, "Taille réelle", #selector(zoomReset), "0", target: self)

        let win = submenu(main, "Fenêtre")
        item(win, "Réduire", #selector(NSWindow.performMiniaturize(_:)), "m")
        item(win, "Agrandir", #selector(NSWindow.performZoom(_:)))
        NSApp.mainMenu = main
        NSApp.windowsMenu = win
    }

    @objc func reloadPage() {
        if let url = serverURL { webView.load(URLRequest(url: url)) }
    }
    @objc func zoomIn() { webView.pageZoom = min(webView.pageZoom + 0.1, 3) }
    @objc func zoomOut() { webView.pageZoom = max(webView.pageZoom - 0.1, 0.5) }
    @objc func zoomReset() { webView.pageZoom = 1 }

    // MARK: serveur Python

    func startServer() {
        let script = projectDir.appendingPathComponent("mini_orchestrateur.py").path
        var args = [script]
        if options.dryRun { args.append("--dry-run") }
        args += ["gui", "--no-browser", "--parent", String(ProcessInfo.processInfo.processIdentifier)]
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = args
        p.currentDirectoryURL = projectDir
        var env = ProcessInfo.processInfo.environment
        // Homebrew Apple Silicon, Homebrew Intel, MacPorts : le PATH du Finder n'en contient aucun.
        let extra = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                     NSHomeDirectory() + "/.local/bin"]
        env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin"]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        p.standardInput = FileHandle.nullDevice
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            if data.isEmpty { h.readabilityHandler = nil; return }
            if let text = String(data: data, encoding: .utf8) {
                DispatchQueue.main.async { self?.consume(text) }
            }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverExited(status: proc.terminationStatus) }
        }
        do {
            try p.run()
            server = p
            diag("serveur lancé, pid \(p.processIdentifier)")
        } catch {
            diag("échec du lancement du serveur : \(error)")
            showPage(title: "Impossible de lancer le moteur", body: "Python n'a pas démarré : \(error.localizedDescription)")
        }
    }

    func consume(_ text: String) {
        output += text
        if output.count > 20000 { output = String(output.suffix(20000)) }
        guard serverURL == nil,
              let range = output.range(of: "http://127\\.0\\.0\\.1:[0-9]+/\\?h=[A-Za-z0-9_\\-]+", options: .regularExpression),
              let url = URL(string: String(output[range])) else { return }
        serverURL = url
        diag("adresse détectée : \(url.host ?? "?"):\(url.port ?? 0)")
        webView.load(URLRequest(url: url))
    }

    func serverExited(status: Int32) {
        diag("serveur terminé, code \(status), arrêt demandé : \(quitting)")
        if quitting { return }
        if status == 0 {  // « Quitter » dans la page : le serveur s'est arrêté normalement
            NSApp.terminate(nil)
            return
        }
        showPage(title: "Le moteur s'est arrêté", body: "Code \(status). Ferme puis rouvre 9AM9AM-TEAM.",
                 details: output)
    }

    func showPage(title: String, body: String, details: String = "") {
        let pre = details.isEmpty ? "" : "<pre>\(htmlEscape(String(details.suffix(4000))))</pre>"
        let html = """
        <!doctype html><meta charset="utf-8"><style>
        :root { color-scheme: light dark; }
        body { font: 16px -apple-system, sans-serif; display: grid; place-items: center; height: 100vh;
               margin: 0; text-align: center; background: Canvas; color: CanvasText; }
        main { max-width: 640px; padding: 24px; } h1 { font-size: 20px; margin: 0 0 8px; }
        p { color: GrayText; margin: 0; } pre { text-align: left; white-space: pre-wrap; font-size: 12px;
               max-height: 240px; overflow: auto; margin-top: 16px; padding: 10px; border-radius: 8px;
               background: color-mix(in srgb, CanvasText 8%, Canvas); }
        </style><main><h1>\(htmlEscape(title))</h1><p>\(htmlEscape(body))</p>\(pre)</main>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: navigation (seule l'interface locale peut s'afficher ici)

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        diag("navigation demandée : \(url.scheme ?? "?")://\(url.host ?? "")")
        if url.scheme == "about" || url.scheme == "data" || url.host == "127.0.0.1" {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)  // un lien externe s'ouvre dans le navigateur, pas ici
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        diag("page chargée : \(webView.url?.host ?? "(page interne)")")
        if webView.url?.host == "127.0.0.1" && !scenarioStarted { runScenarioIfRequested() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        diag("échec de chargement : \(error.localizedDescription)")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        diag("le processus d'affichage s'est arrêté")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        diag("échec de navigation : \(error.localizedDescription)")
        showPage(title: "La page ne s'affiche pas", body: error.localizedDescription, details: output)
    }

    // MARK: boîtes de dialogue JavaScript (sans elles, confirm() répond toujours « non »)

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Annuler")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // MARK: sélecteur de fichiers (sans lui, <input type=file> ne fait rien :
    // le bouton « Parcourir… » de l'onglet workspace resterait muet).

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.allowedContentTypes = []  // tout type : le filtrage (texte, taille) est fait côté page/serveur
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // MARK: vérification automatique (--snapshot)

    func runScenarioIfRequested() {
        guard options.snapshot != nil else { return }
        scenarioStarted = true
        diag("scénario de test lancé")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            if let text = self.options.send {
                let js = "var i = document.getElementById('input'); i.value = \(jsString(text)); "
                    + "i.dispatchEvent(new Event('input')); document.getElementById('send').click(); 'envoyé'"
                self.webView.evaluateJavaScript(js) { result, error in
                    print("ENVOI:", (result as? String) ?? "échec \(String(describing: error))")
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + self.options.wait) { self.snapshotAndQuit() }
        }
    }

    func snapshotAndQuit() {
        let js = """
        JSON.stringify({titre: document.title, cartes: document.querySelectorAll('.card').length,
          messages: document.querySelectorAll('.msg').length, evenements: document.querySelectorAll('.ev').length,
          saisie: !!document.getElementById('input'), largeur: window.innerWidth})
        """
        webView.evaluateJavaScript(js) { result, error in
            print("PAGE:", (result as? String) ?? "échec \(String(describing: error))")
            self.webView.takeSnapshot(with: nil) { image, error in
                if let image = image, let path = self.options.snapshot, writePNG(image, to: path) {
                    print("IMAGE:", path)
                } else {
                    print("IMAGE: échec \(String(describing: error))")
                }
                fflush(stdout)
                NSApp.terminate(nil)
            }
        }
    }
}

// MARK: - Point d'entrée

let options = parseOptions()
logPath = options.log

if let path = options.renderIcon {
    exit(writePNG(drawIcon(size: 1024), to: path) ? 0 : 1)
}

let application = NSApplication.shared
guard let projectDir = projectDirectory(options) else {
    application.setActivationPolicy(.regular)
    let alert = NSAlert()
    alert.messageText = "Dossier du projet introuvable"
    alert.informativeText = "Place 9AM9AM-TEAM.app dans le dossier du projet (à côté de mini_orchestrateur.py), "
        + "ou reconstruis-le avec app/build.sh."
    alert.runModal()
    exit(2)
}
let controller = AppController(options: options, projectDir: projectDir)
application.delegate = controller
application.run()
