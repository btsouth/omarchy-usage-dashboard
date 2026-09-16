import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

Scope {
    id: root
    readonly property var palette: data && data.theme ? data.theme.palette : ({})
    readonly property color base: palette.background || "#111c18"
    readonly property color ink: palette.foreground || "#c1c497"
    readonly property color bright: palette.light_foreground || ink
    readonly property color accent: palette.accent || "#509475"
    readonly property color muted: Qt.alpha(ink, 0.84)
    readonly property color edge: Qt.alpha(ink, 0.14)
    readonly property color bg: Qt.alpha(base, settingsOpen ? opacitySlider.value : data ? Number(data.settings.windowOpacity || 0.985) : 0.985)
    readonly property color surface: Qt.alpha(palette.lighter_background || "#23372b", 0.28)
    readonly property string fontFamily: data && data.theme ? data.theme.font : "monospace"
    readonly property string helper: (Quickshell.env("AI_USAGE_ROOT") || decodeURIComponent(Qt.resolvedUrl("..").toString().replace(/^file:\/\//, ""))) + "/collector.py"
    property var data: null
    property int days: 7
    property string provider: "all"
    property string metric: "tokens"
    property var selection: ({})
    property var navigation: []
    function goBack() {
        if (!navigation.length) return
        var stack = navigation.slice(); var old = stack.pop(); navigation = stack
        selection = old.selection; provider = old.provider; breakdown = old.breakdown
        tableLimit = 20; refresh()
    }
    property int tableLimit: 20
    function drill(field, name, providerId) {
        navigation = navigation.concat([{selection:selection,provider:provider,breakdown:breakdown}])
        var next = Object.assign({}, selection); next[field] = name
        selection = next
        if (providerId) provider = providerId
        breakdown = "sessions"; tableLimit = 20; refresh()
    }
    function clearSelection() { navigation = []; selection = ({}); tableLimit = 20; refresh() }
    function when(ts) { return ts ? Qt.formatDateTime(new Date(ts*1000), "MMM d, yyyy HH:mm") : "No recorded activity" }
    property string breakdown: "models"
    property bool settingsOpen: false
    property string error: ""
    property bool pending: false
    property var draftEnabled: ["codex", "claude", "opencode-go"]
    property string notice: ""
    property var providerOptions: data ? data.availableProviders || [] : []
    property var draftPrices: ({})
    property var draftHomes: ({})
    property var draftAccounts: []
    property string draftLocalLabel: "Local"
    property string draftLedgerSyncDir: ""
    property string draftLedgerDeviceId: ""
    property string saveError: ""
    property var homeOptions: [
        {key:"codexHomes",name:"Codex homes",example:"/mnt/other-computer/.codex"},
        {key:"claudeHomes",name:"Claude homes",example:"/mnt/other-computer/.claude"},
        {key:"grokHomes",name:"Grok homes",example:"/mnt/other-computer/.grok"},
        {key:"geminiHomes",name:"Gemini homes",example:"/mnt/other-computer/.gemini"},
        {key:"opencodeHomes",name:"OpenCode data folders",example:"/mnt/other-computer/.local/share/opencode"},
        {key:"piHomes",name:"Pi agent folders",example:"/mnt/other-computer/.pi/agent"},
        {key:"ompHomes",name:"Oh My Pi agent folders",example:"/mnt/other-computer/.omp/agent"},
        {key:"museHomes",name:"Muse homes",example:"/mnt/other-computer/.local/share/muse"}]
    function providerName(id) { var p = providerOptions.find(p => p.id === id); return p ? p.name : id }
    function accountSources(account) {
        var names = []
        ;(account.directories || []).forEach(d => { var n = providerName(d.provider); if (names.indexOf(n) < 0) names.push(n) })
        return names.join(" + ")
    }
    function luminance(c) {
        function linear(v) { return v <= 0.04045 ? v/12.92 : Math.pow((v+0.055)/1.055,2.4) }
        return 0.2126*linear(c.r)+0.7152*linear(c.g)+0.0722*linear(c.b)
    }
    function colorFor(id) {
        var raw = ({codex: palette.bright_cyan || "#8cd3cb", claude: palette.bright_red || "#db9f9c",
            "opencode-go": palette.bright_yellow || "#e5c736", grok: palette.bright_blue || "#9cb8db",
            gemini: palette.bright_magenta || "#c6a0d5", opencode: palette.bright_green || "#a7c080",
            pi: palette.bright_white || "#d4d4d4", omp: palette.red || "#d88b68",
            muse: palette.blue || "#7aa2f7"})[id] || root.ink
        var color=Qt.darker(raw,1), background=luminance(root.base)
        for (var i=0;i<16;i++) {
            var value=luminance(color)
            if ((Math.max(value,background)+0.05)/(Math.min(value,background)+0.05)>=4.5) break
            color=background>0.179 ? Qt.darker(color,1.15) : Qt.lighter(color,1.15)
        }
        return color
    }
    function accountColor(provider, shade) {
        var base = colorFor(provider)
        if (!shade) return base
        var factor = Math.min(1.6, 1 + 0.5 * shade)
        return luminance(root.base) > 0.179 ? Qt.darker(base, factor) : Qt.lighter(base, factor)
    }
    function compact(n) {
        n = Number(n || 0)
        return n >= 1e9 ? (n/1e9).toFixed(2)+"B" : n >= 1e6 ? (n/1e6).toFixed(1)+"M" : n >= 1e3 ? (n/1e3).toFixed(1)+"K" : String(Math.round(n))
    }
    function money(n) { return "$" + Number(n || 0).toLocaleString(Qt.locale("en_US"), 'f', 2) }
    function shortDate(value) { return value ? Qt.formatDate(new Date(value+"T12:00:00"),"MMM d") : "" }
    function display(b) { return metric === "tokens" ? compact(b ? b.tokens : 0) : b && b.tokens > 0 && b.unpricedTokens === b.tokens ? "Unpriced" : money(b ? b.value : 0) }
    function amount(b) { return b ? (metric === "tokens" ? b.tokens : b.value) : 0 }
    function valueText(b) { return b.unpricedTokens === b.tokens && b.tokens > 0 ? "Unpriced" : money(b.value) + (b.unpricedTokens ? " + unpriced" : "") }
    function refresh() {
        if (scan.running) { pending = true; return }
        error = ""
        scan.command = ["python3", helper, "report", "--days", String(days), "--provider", provider]
        for (var field in selection) scan.command = scan.command.concat(["--"+field, selection[field]])
        scan.running = true
    }
    function refreshLive() {
        if (!live.running) live.running = true
        root.refresh()
    }
    function resetText(value) {
        var seconds = (Date.parse(value) - Date.now()) / 1000
        if (!isFinite(seconds)) return "Reset unavailable"
        if (seconds <= 0) return "Awaiting reset update"
        return "Resets in " + (seconds >= 86400 ? Math.floor(seconds/86400)+"d " : "") + (seconds >= 3600 ? Math.floor(seconds/3600)%24+"h" : Math.ceil(seconds/60)+"m")
    }
    function quotaAge(q) {
        var age = (Date.now() - Date.parse(q.updatedAt))/60000
        return !isFinite(age) ? "No quota update" : age > 30 ? "Stale quota · " + Math.floor(age) + "m ago" : "Quota updated " + Math.max(0, Math.floor(age)) + "m ago"
    }
    function openSettings() {
        notice = ""
        var s = data ? data.settings : {}
        draftEnabled = (s.enabled || ["codex", "claude", "opencode-go"]).slice()
        draftAccounts = JSON.parse(JSON.stringify(s.accounts || []))
        draftLocalLabel = s.localAccountLabel || "Local"
        draftLedgerSyncDir = s.ledgerSyncDir || ""
        draftLedgerDeviceId = s.ledgerDeviceId || ""
        var prices = {}, homes = {}
        providerOptions.forEach(p => prices[p.id] = s.monthlyPrices && s.monthlyPrices[p.id] !== undefined ? String(s.monthlyPrices[p.id]) : "")
        draftAccounts.forEach(a => prices[a.id] = s.monthlyPrices && s.monthlyPrices[a.id] !== undefined ? String(s.monthlyPrices[a.id]) : "")
        homeOptions.forEach(h => homes[h.key] = (s[h.key] || []).join("\n"))
        draftPrices = prices
        draftHomes = homes
        opacitySlider.value = s.windowOpacity || 0.985
        settingsOpen = true
    }
    function saveSettings() {
        var prices = {}
        var fields = draftPrices
        for (var p in fields) {
            if (fields[p].trim() === "") continue
            var n = Number(fields[p])
            if (!isFinite(n) || n < 0 || n > 100000) { notice = "Enter a valid monthly price, or leave it blank."; return }
            prices[p] = n
        }
        saveError = ""
        var s = {accounts: draftAccounts, localAccountLabel: draftLocalLabel, enabled: draftEnabled, monthlyPrices: prices, windowOpacity: opacitySlider.value,
                 ledgerSyncDir: draftLedgerSyncDir, ledgerDeviceId: draftLedgerDeviceId}
        homeOptions.forEach(h => s[h.key] = (draftHomes[h.key] || "").split("\n").filter(x => x.trim()).map(x => x.trim()))
        save.command = ["python3", helper, "settings", "--save", JSON.stringify(s)]
        save.running = true
    }
    Process {
        id: scan
        stdout: StdioCollector { onStreamFinished: {
            try { root.data = JSON.parse(text); root.error = "" } catch(e) { root.error = "Could not load usage data. Try refreshing." }
        } }
        stderr: StdioCollector { onStreamFinished: { if (text.trim()) console.warn(text.trim()) } }
        onExited: function(code) {
            if (code !== 0) root.error = "The scan failed. Previously loaded data is still shown."
            if (root.pending) { root.pending = false; root.refresh() }
        }
    }
    Process {
        id: save
        stdout: StdioCollector { onStreamFinished: { try { root.saveError = JSON.parse(text).error || "" } catch(e) {} } }
        onExited: function(code) {
            if (code === 0) { root.settingsOpen = false; root.selection = ({}); root.navigation = []; root.provider = "all"; root.notice = "Settings saved"; root.refresh() }
            else root.notice = root.saveError || "Settings could not be saved."
        }
    }
    Process {
        id: live
        command: Quickshell.env("AI_USAGE_DEMO") === "1" ? ["python3", helper, "report"] : ["bash", helper.replace(/collector\.py$/, "refresh.sh"), "--force"]
        onExited: root.refresh()
    }
    Timer { interval: 300000; repeat: true; running: window.visible; onTriggered: root.refresh() }
    FileView {
        path: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME")+"/.local/state")+"/omarchy/current/theme/colors.toml"
        watchChanges: true; printErrors: false
        onFileChanged: root.refresh()
    }
    FileView {
        path: Quickshell.env("HOME")+"/.config/omarchy/shell.toml"
        watchChanges: true; printErrors: false
        onFileChanged: root.refresh()
    }
    IpcHandler {
        target: "analytics"
        function quit(): void { Qt.quit() }
        function refresh(): void { root.refreshLive() }
        function showWindow(): int {
            // Hot reload or a compositor close can leave visible true without a mapped window.
            if (!window.backingWindowVisible) window.visible = false
            Qt.callLater(function() { window.visible = true; root.refresh() })
            return Quickshell.processId
        }
        function capture(path: string): void { captureRoot.grabToImage(result => result.saveToFile(path)) }
        function captureTooltip(path: string): void { chartTip.contentItem.grabToImage(result => result.saveToFile(path)) }
        function account(id: string): void { root.drill("account", id, "") }
        function preferences(): void { root.openSettings() }
        function overview(): void { root.settingsOpen = false }
        function period(days: int): void { root.days = days; root.refresh() }
        function scrollTo(y: int): void { scroll.contentItem.contentY = y }
        function tooltip(): void { chart.hovered=2; chart.pointerX=chart.width/2; chartTip.open() }
        function clearTooltip(): void { chart.hovered=-1 }
    }

    component Label: Text {
        color: root.ink
        font.family: root.fontFamily
        font.pixelSize: 13
        renderType: Text.NativeRendering
    }
    component Sub: Label { color: root.muted; font.pixelSize: 12 }
    component Choice: Button {
        id: control
        property bool selected: false
        implicitHeight: 34
        leftPadding: 14; rightPadding: 14
        Accessible.name: text
        background: Rectangle {
            radius: 3
            color: control.down ? Qt.alpha(root.ink,0.18) : control.selected ? Qt.alpha(root.ink,0.13) : control.hovered ? Qt.alpha(root.ink,0.07) : "transparent"
            Behavior on color { ColorAnimation { duration: 110 } }
            border.color: control.activeFocus ? root.accent : control.selected ? Qt.alpha(root.ink,0.26) : "transparent"
        }
        contentItem: Label { text: control.text; color: control.selected ? root.bright : root.muted; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    }
    component Card: Rectangle { color: root.surface; border.color: root.edge; radius: 4 }
    component HoverTip: ToolTip {
        id: tip
        property string heading: ""
        property string detail: ""
        property var rows: []
        delay: 120
        timeout: -1
        margins: 12
        implicitWidth: 330
        closePolicy: Popup.NoAutoClose
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
        background: Item {}
        padding: 0
        contentItem: Rectangle {
            implicitWidth: 330
            implicitHeight: tooltipColumn.implicitHeight+32
            color: root.base
            border.color: Qt.alpha(root.ink,0.28)
            radius: 4
            Rectangle { x: 1; y: 1; width: parent.width-2; height: 2; color: Qt.alpha(root.accent,0.65) }
            Column {
                id: tooltipColumn
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16
                spacing: 11
                Label { width: parent.width; text: tip.heading; wrapMode: Text.Wrap; font.pixelSize: 12; font.weight: Font.Medium; color: root.bright }
                Rectangle { width: parent.width; height: 1; color: root.edge; visible: tip.rows.length>0 }
                Repeater {
                    model: tip.rows
                    RowLayout {
                        required property var modelData
                        width: tooltipColumn.width; spacing: 12
                        Rectangle { implicitWidth: 5; implicitHeight: 5; color: modelData.color || root.accent; radius: 1 }
                        Sub { text: modelData.label; Layout.fillWidth: true; font.pixelSize: 11 }
                        Label { text: modelData.value; font.pixelSize: 12 }
                    }
                }
                Sub { width: parent.width; text: tip.detail; visible: text!==""; wrapMode: Text.Wrap; font.pixelSize: 10 }
            }
        }
    }
    component QuietScrollBar: ScrollBar {
        id: control
        policy: ScrollBar.AsNeeded
        padding: 1
        implicitWidth: 7
        background: Item {}
        contentItem: Rectangle {
            implicitWidth: 4; implicitHeight: 30; radius: 2
            color: control.pressed ? root.accent : Qt.alpha(root.ink,control.hovered?0.4:0.2)
            opacity: control.active || control.hovered ? 1 : 0.45
            Behavior on opacity { NumberAnimation { duration: 150 } }
        }
    }
    component Field: TextField {
        color: root.ink; font.family: root.fontFamily; font.pixelSize: 13; placeholderTextColor: root.muted
        selectionColor: Qt.alpha(root.accent,0.4); selectedTextColor: root.ink
        padding: 12; selectByMouse: true
        background: Rectangle { radius: 3; color: Qt.alpha(root.base, 0.5); border.color: parent.activeFocus ? root.accent : root.edge }
    }
    component Homes: TextArea {
        color: root.ink; font.family: root.fontFamily; font.pixelSize: 12; placeholderTextColor: root.muted
        selectionColor: Qt.alpha(root.accent,0.4); selectedTextColor: root.ink
        padding: 12; selectByMouse: true; wrapMode: TextEdit.NoWrap
        background: Rectangle { radius: 3; color: Qt.alpha(root.base, 0.5); border.color: parent.activeFocus ? root.accent : root.edge }
    }
    FloatingWindow {
        id: window
        title: "AI Usage"
        color: "transparent"
        implicitWidth: 1200
        implicitHeight: 900
        minimumSize: Qt.size(1000, 640)
        visible: true
        Component.onCompleted: root.refresh()
        Shortcut { sequence: "Alt+Left"; onActivated: root.goBack() }
        Shortcut { sequence: "Ctrl+Q"; onActivated: Qt.quit() }
        Shortcut { sequence: "Ctrl+R"; onActivated: root.refresh() }
        Shortcut { sequence: "Escape"; onActivated: { if(root.settingsOpen) root.settingsOpen = false; else if(root.navigation.length) root.goBack(); else window.visible = false } }
        Rectangle {
            id: captureRoot
            anchors.fill: parent
            color: root.bg
        ColumnLayout {
            id: dashboardBody
            anchors.fill: parent
            anchors.margins: 26
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    implicitWidth: 36; implicitHeight: 36; radius: 3; color: Qt.alpha(root.accent, 0.14)
                    Label { anchors.centerIn: parent; text: "󱚣"; color: root.ink; font.pixelSize: 24 }
                }
                Column {
                    Layout.fillWidth: true; Layout.minimumWidth: 150
                    spacing: 3
                    Label { text: root.settingsOpen ? "Preferences" : "AI usage"; font.pixelSize: 21; font.weight: Font.Medium }
                    Sub { width: parent.width; elide: Text.ElideRight; text: Quickshell.env("AI_USAGE_DEMO") === "1" ? "Demo data · no local history" : (root.data ? root.data.settings.enabled.map(p => root.providerName(p)).join(" · ") : "Local AI usage") }
                }
                Item { Layout.fillWidth: true }
                Sub { text: scan.running || live.running ? "Updating usage…" : root.data ? root.data.period.start + "  to  " + root.data.period.end : "Loading…" }
                Choice { text: root.settingsOpen ? "Cancel" : "Settings"; onClicked: root.settingsOpen ? root.settingsOpen = false : root.openSettings() }
                Choice { visible: root.settingsOpen; text: "Save preferences"; selected: true; enabled: !save.running; onClicked: root.saveSettings() }
                Choice { visible: !root.settingsOpen; text: "Refresh"; enabled: !scan.running && !live.running; onClicked: root.refreshLive() }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.edge }
            Label { visible: root.notice !== ""; text: root.notice; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: root.accent }
            Label { visible: root.error !== ""; text: root.error; color: root.colorFor("claude"); Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                visible: !root.settingsOpen
                Layout.fillWidth: true
                spacing: 6
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: [{id: "all", name: "Overview"}].concat(root.data ? root.data.settings.enabled.map(p => ({id:p,name:root.providerName(p)})) : [])
                        Choice { required property var modelData; text: modelData.name; selected: root.provider === modelData.id; onClicked: { root.provider = modelData.id; root.refresh() } }
                    }
                }
                Repeater {
                    model: [1,7,30,90,365]
                    Choice { required property int modelData; text: modelData === 1 ? "Today" : modelData === 365 ? "Year" : modelData + "d"; selected: root.days === modelData; onClicked: { root.days = modelData; root.navigation = []; root.selection = root.selection.account ? {account:root.selection.account} : ({}); root.refresh() } }
                }
            }
            Flow { Layout.fillWidth: true; spacing: 8; visible: !root.settingsOpen && !!root.data
                Choice { text: "All accounts"; selected: !root.selection.account; onClicked: { var s=Object.assign({},root.selection); delete s.account; root.selection=s; root.refresh() } }
                Repeater { model: root.data ? root.data.accountOptions : []
                    Choice { required property var modelData; text: modelData.label; selected: root.selection.account===modelData.id; onClicked: root.drill("account",modelData.id,"") }
                }
            }
            Sub { Layout.fillWidth: true; visible: !root.settingsOpen && !!root.data && !!root.data.accountWarning; text: root.data ? root.data.accountWarning : ""; wrapMode: Text.WordWrap }
            RowLayout {
                visible: !root.settingsOpen && Object.keys(root.selection).length > 0
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; elide: Text.ElideMiddle; text: Object.keys(root.selection).map(k => k+": "+(k === "account" ? (root.data.accountOptions.find(a=>a.id===root.selection[k]) || {}).label || root.selection[k] : root.selection[k])).join(" · ") }
                Choice { visible: root.navigation.length > 0; text: "Back"; onClicked: root.goBack() }
                Choice { text: "Clear filters"; onClicked: root.clearSelection() }
            }
            ScrollView {
                id: scroll
                ScrollBar.vertical: QuietScrollBar { parent: scroll; x: scroll.width-width; height: scroll.height }
                visible: !root.settingsOpen
                enabled: !scan.running
                opacity: scan.running && root.data ? 0.6 : 1
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                Column {
                    width: scroll.availableWidth
                    spacing: 18
                    Card {
                        width: parent.width; height: pricingNote.implicitHeight + 28
                        Column {
                            id: pricingNote
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 6
                            Label { width: parent.width; wrapMode: Text.WordWrap; text: root.data ? (root.data.summary.tokens === 0 ? "No activity in this period. Add a history folder in Settings or use a supported coding agent." : root.data.summary.unpricedTokens ? root.compact(root.data.summary.unpricedTokens)+" tokens have no complete price. API value is a partial estimate." : "All recorded tokens in this view have an API-value estimate.") : "Checking pricing coverage…"; color: root.data && root.data.summary.unpricedTokens ? root.colorFor("claude") : root.ink }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.data ? "History on "+(root.data.coverage.machine || "this computer")+" · scanned "+root.when(root.data.coverage.scannedAt)+" · "+(root.data.pricing.coveragePercent===null ? "No activity" : (root.data.summary.unpricedTokens && root.data.pricing.coveragePercent>99.9 ? ">99.9" : root.data.pricing.coveragePercent.toFixed(1))+"% of tokens priced") : "" }
                        }
                    }
                    RowLayout {
                        width: parent.width; spacing: 18
                        Card {
                            Layout.preferredWidth: 320; Layout.fillHeight: true; implicitHeight: 330
                            Column {
                                anchors.fill: parent; anchors.margins: 22; spacing: 12
                                Sub { text: root.metric === "tokens" ? "PROCESSED TOKENS" : "ESTIMATED API VALUE"; font.letterSpacing: 1.2 }
                                Label { text: root.data ? root.display(root.data.summary) : "…"; font.pixelSize: 36; font.weight: Font.Medium }
                                Sub { text: root.data ? root.data.summary.sessions + " sessions · " + root.compact(root.data.summary.requests) + " usage records" + (root.metric === "value" && root.data.summary.unpricedTokens ? " · partial value" : "") : "Scanning local history" }
                                Rectangle { width: parent.width; height: 1; color: root.edge }
                                Label {
                                    text: {
                                        if (!root.data) return ""
                                        if (Object.keys(root.selection).length) return "Filtered activity"
                                        if (root.metric !== "tokens" && (root.data.summary.unpricedTokens || root.data.previous.unpricedTokens)) return "Value comparison incomplete"
                                        var cur=root.amount(root.data.summary), prev=root.amount(root.data.previous)
                                        return prev > 0 ? (cur >= prev ? "↑ " : "↓ ") + Math.abs((cur/prev-1)*100).toFixed(1) + (root.days === 1 ? "% vs yesterday so far" : "% vs previous period") : "No previous-period baseline"
                                    }
                                    color: root.accent
                                }
                                Label { width: parent.width; wrapMode: Text.WordWrap; text: root.data && root.data.summary.tokens ? (root.data.summary.cacheRead/root.data.summary.tokens*100).toFixed(1)+"% cached input reused" : "No recorded tokens"; color: root.colorFor("codex") }
                                Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.data ? root.compact(root.data.summary.output)+" output tokens, including reasoning" : "" }
                                Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.metric === "tokens" ? "Processed tokens count reused context on every request. This is not a count of unique text." : "Catalog rates or the app’s recorded API estimate. This is not your bill." }
                            }
                    }
                    Label { text: "Synced machines"; font.pixelSize: 16 }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Point every machine at one synced folder. Each writes its own ledger snapshot and imports the others, so totals, charts, heatmap, and model breakdowns cover all of them. Only token counters and names are shared; prompts, paths outside the ledger, and credentials stay local." }
                    Field { width: parent.width; text: root.draftLedgerSyncDir; placeholderText: "Shared folder, e.g. ~/Sync/ai-usage"; onTextEdited: root.draftLedgerSyncDir = text; Accessible.name: "Synced ledger folder" }
                    Field { width: 260; text: root.draftLedgerDeviceId; placeholderText: "Device id, e.g. desktop"; onTextEdited: root.draftLedgerDeviceId = text; Accessible.name: "Synced ledger device id" }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "The device id names this machine's snapshot ("+(root.draftLedgerDeviceId||"hostname")+".sqlite). Leave it blank to use the hostname. Other machines appear as extra accounts you can filter." }
                    Card {
                            Layout.fillWidth: true; Layout.fillHeight: true; implicitHeight: 248
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 18; spacing: 10
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: root.selection.day ? "Hourly activity · "+root.selection.day : root.days === 1 ? "Hourly activity · today" : "Daily activity"; font.weight: Font.DemiBold }
                                    Item { Layout.fillWidth: true }
                                    Choice { text: "Tokens"; selected: root.metric === "tokens"; onClicked: root.metric = "tokens" }
                                    Choice { text: "API value"; selected: root.metric === "value"; onClicked: root.metric = "value" }
                                }
                                Canvas {
                                    id: chart
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    property int hovered: -1
                                    property real pointerX: width/2
                                    property bool hourly: root.data ? root.data.hourly.length > 0 : false
                                    property var series: root.data ? (hourly ? root.data.hourly || [] : root.data.daily) : []
                                    property string metric: root.metric
                                    onSeriesChanged: requestPaint()
                                    onMetricChanged: requestPaint()
                                    onHoveredChanged: requestPaint()
                                    onWidthChanged: requestPaint()
                                    onHeightChanged: requestPaint()
                                    onPaint: {
                                        var ctx=getContext("2d"); ctx.reset()
                                        var w=width, h=height, left=48, top=10, bottom=h-26, plot=w-left-10
                                        if (!root.data || root.data.summary.tokens === 0) {
                                            ctx.font="13px \""+root.fontFamily+"\"";ctx.fillStyle=root.muted;ctx.textAlign="center"
                                            ctx.fillText(root.data ? "No activity in this period" : "Reading local history…",w/2,h/2)
                                            return
                                        }
                                        var cards=root.data ? root.data.cards : [], ids=cards.map(c=>c.id), max=1
                                        for (var d of series) for (var id of ids) max=Math.max(max,root.amount(d.cards[id]))
                                        ctx.font="10px \""+root.fontFamily+"\""; ctx.lineWidth=1
                                        for(var t=0;t<3;t++) {
                                            var y=top+(bottom-top)*t/2
                                            ctx.strokeStyle=root.edge;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w,y);ctx.stroke()
                                            ctx.fillStyle=root.muted;ctx.fillText(root.metric==="tokens"?root.compact(max*(1-t/2)):"$"+root.compact(max*(1-t/2)),0,y+4)
                                        }
                                        if (hourly) {
                                            var group=plot/Math.max(1,series.length), bw=Math.min(22,group*0.7/Math.max(1,ids.length)), gap=4
                                            for(var i=0;i<series.length;i++) {
                                                var center=left+group*(i+0.5), totalWidth=ids.length*bw+(ids.length-1)*gap
                                                for(var j=0;j<ids.length;j++) {
                                                    var barHeight=root.amount(series[i].cards[ids[j]])/max*(bottom-top)
                                                    var barAlpha=cards[j].shade===0?0.8:cards[j].shade===1?0.55:0.38
                                                    ctx.fillStyle=Qt.alpha(root.accountColor(cards[j].provider,cards[j].shade),hovered===i?1:barAlpha)
                                                    ctx.fillRect(center-totalWidth/2+j*(bw+gap),bottom-barHeight,bw,barHeight)
                                                }
                                                if(series.length<=8 || i%3===0 || i===series.length-1) {
                                                    ctx.fillStyle=root.muted;ctx.textAlign="center";ctx.fillText(series[i].label,center,h-5);ctx.textAlign="left"
                                                }
                                            }
                                        } else {
                                            for (var k=0;k<ids.length;k++) {
                                                ctx.beginPath()
                                                for(var i=0;i<series.length;i++) {
                                                    var x=left+(series.length===1?plot/2:i*plot/(series.length-1))
                                                    var yy=bottom-root.amount(series[i].cards[ids[k]])/max*(bottom-top)
                                                    if(i===0)ctx.moveTo(x,yy);else ctx.lineTo(x,yy)
                                                }
                                                ctx.strokeStyle=root.accountColor(cards[k].provider,cards[k].shade);ctx.lineWidth=cards[k].shade===0?2.3:1.8
                                                ctx.setLineDash(cards[k].shades>1 && cards[k].shade>0 ? (cards[k].shade===1?[5,4]:[2,2]) : [])
                                                ctx.stroke();ctx.setLineDash([])
                                                if(series.length===1){ctx.beginPath();ctx.arc(x,yy,4,0,2*Math.PI);ctx.fillStyle=root.accountColor(cards[k].provider,cards[k].shade);ctx.fill()}
                                                else if(ids.length <= 3 && cards[k].shade===0) {ctx.lineTo(left+plot,bottom);ctx.lineTo(left,bottom);ctx.closePath();ctx.fillStyle=Qt.alpha(root.accountColor(cards[k].provider,cards[k].shade),0.07);ctx.fill()}
                                            }
                                            ctx.fillStyle=root.muted
                                            if(series.length){ctx.fillText(series[0].date.slice(5),left,h-5);ctx.fillText(series[series.length-1].date.slice(5),w-42,h-5)}
                                        }
                                        if(hovered>=0 && hovered<series.length){var xx=hourly?left+(hovered+0.5)*plot/series.length:left+(series.length===1?plot/2:hovered*plot/(series.length-1));ctx.strokeStyle=Qt.alpha(root.ink,0.3);ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,bottom);ctx.stroke()}

                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        onPositionChanged: mouse => { chart.pointerX=mouse.x; chart.hovered=Math.max(0,Math.min(chart.series.length-1,(chart.hourly ? Math.floor((mouse.x-48)/(width-58)*chart.series.length) : Math.round((mouse.x-48)/(width-58)*Math.max(0,chart.series.length-1))))) }
                                        onExited: chart.hovered=-1
                                        cursorShape: chart.hourly ? Qt.ArrowCursor : Qt.PointingHandCursor
                                        onClicked: { if (!chart.hourly && chart.hovered>=0 && chart.hovered<chart.series.length) root.drill("day",chart.series[chart.hovered].date,"") }
                                    }
                                    HoverTip {
                                        id: chartTip
                                        parent: chart
                                        visible: chart.hovered>=0 && chart.hovered<chart.series.length
                                        x: chart.pointerX > chart.width/2 ? 48 : Math.max(0,chart.width-width-12)
                                        y: 16
                                        heading: chart.hovered>=0 && chart.hovered<chart.series.length ? (chart.hourly ? chart.series[chart.hovered].title : Qt.formatDate(new Date(chart.series[chart.hovered].date+"T12:00:00"),"dddd, MMM d")) : ""
                                        rows: chart.hovered>=0 && chart.hovered<chart.series.length && root.data ? root.data.cards.map(c=>({label:c.name,value:root.display(chart.series[chart.hovered].cards[c.id]),color:root.accountColor(c.provider,c.shade)})) : []
                                        detail: (chart.hourly ? "This hour · " : "") + (root.metric === "tokens" ? "Processed tokens, including cached input" : "Estimated API value, not your bill")
                                    }
                                }
                            }
                        }
                    }
                    GridLayout {
                        width: parent.width; columns: root.data ? (root.data.cards.length > 3 ? 2 : Math.max(1,root.data.cards.length)) : 3; rowSpacing: 14; columnSpacing: 14
                        Repeater {
                            model: root.data ? root.data.cards : []
                            Card {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: providerColumn.implicitHeight + 36
                                Layout.fillHeight: true
                                Column {
                                    id: providerColumn
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
                                    spacing: 10
                                    Row {
                                        spacing: 8
                                        Rectangle { width: 8; height: 8; radius: 4; color: root.accountColor(modelData.provider, modelData.shade); anchors.verticalCenter: parent.verticalCenter }
                                        Label { text: modelData.name; font.pixelSize: 16; font.weight: Font.DemiBold }
                                    }
                                    Row {
                                        spacing: 10
                                        Label { text: root.compact(modelData.tokens); font.pixelSize: 26; font.weight: Font.Medium }
                                        Sub { text: "tokens"; anchors.bottom: parent.bottom; anchors.bottomMargin: 3 }
                                    }
                                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.valueText(modelData) + " API value · " + modelData.sessions + " sessions" }
                                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: modelData.valueShare === null || (modelData.tokens > 0 && modelData.unpricedTokens === modelData.tokens) ? "No priced API value" : modelData.valueShare.toFixed(1)+"% of priced API value" }
                                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: modelData.sessions ? root.compact(modelData.tokensPerSession)+" tokens · "+root.money(modelData.valuePerSession)+" priced value / recorded session" : "No recorded sessions" }
                                    Rectangle { width: parent.width; height: 3; radius: 2; color: root.edge
                                        Rectangle { width: parent.width*(root.data.summary.tokens ? modelData.tokens/root.data.summary.tokens : 0); height: 3; radius: 2; color: root.accountColor(modelData.provider, modelData.shade) }
                                    }
                                    Sub {
                                        width: parent.width; wrapMode: Text.WordWrap
                                        text: modelData.monthlyPrice !== null ? root.money(modelData.monthlyPrice)+"/month plan · "+(modelData.monthlyPrice>0?(modelData.value/modelData.monthlyPrice).toFixed(1)+"× plan price in this period":"no plan charge") : "Monthly plan price not set"
                                    }
                                    Sub { visible: modelData.provider === "grok"; width: parent.width; wrapMode: Text.WordWrap; text: root.compact(modelData.modelCalls || 0)+" model calls · "+modelData.requests+" usage records" }
                                    Sub { visible: !root.selection.account && modelData.quotaScope !== ""; text: modelData.quotaScope }
                                    Repeater {
                                        model: modelData.quota.limits || []
                                        Column {
                                            required property var modelData
                                            width: providerColumn.width; spacing: 4
                                            RowLayout { width: parent.width
                                                Sub { text: modelData.label }
                                                Item { Layout.fillWidth: true }
                                                Label { text: (modelData.percent*100).toFixed(0)+"% used"; font.pixelSize: 11 }
                                            }
                                            Rectangle { width: parent.width; height: 4; radius: 2; color: root.edge
                                                Rectangle { height: 4; radius: 2; width: parent.width*Math.min(1,Math.max(0,modelData.percent)); color: modelData.percent>=0.9 ? root.colorFor("claude") : Qt.alpha(root.ink,0.55) }
                                            }
                                            Sub { text: root.resetText(modelData.resetsAt); font.pixelSize: 10 }
                                        }
                                    }
                                    Column {
                                        visible: modelData.provider === "opencode-go" && !!root.data && root.data.goAllowance.models.length > 0
                                        width: providerColumn.width; spacing: 5
                                        Sub { text: "Model allowance · this month" }
                                        Repeater {
                                            model: root.data ? root.data.goAllowance.models.slice(0,4) : []
                                            Column {
                                                required property var modelData
                                                width: providerColumn.width; spacing: 2
                                                RowLayout { width: parent.width; spacing: 8
                                                    Sub { text: modelData.model; Layout.fillWidth: true; elide: Text.ElideRight }
                                                    Label { text: root.money(modelData.value)+" / "+root.money(modelData.limit); font.pixelSize: 10 }
                                                }
                                                Rectangle { width: parent.width; height: 3; radius: 2; color: root.edge
                                                    Rectangle { height: 3; radius: 2
                                                        width: parent.width*Math.min(1, modelData.limit > 0 ? modelData.value/modelData.limit : 0)
                                                        color: modelData.value >= modelData.limit*0.9 ? root.colorFor("claude") : Qt.alpha(root.ink,0.55) }
                                                }
                                                Sub { visible: modelData.promo; text: "4x promo through "+root.shortDate(modelData.promoEnds); font.pixelSize: 9 }
                                            }
                                        }
                                        Sub { visible: !!root.data && root.data.goAllowance.models.length > 4; text: "+"+(root.data.goAllowance.models.length-4)+" more models used this month"; font.pixelSize: 9 }
                                    }
                                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: modelData.quota.error || root.quotaAge(modelData.quota); font.pixelSize: 10 }
                                }
                            }
                        }
                    }
                    Card {
                        width: parent.width; height: 90
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 20; spacing: 15
                            Repeater {
                                model: [{name:"Uncached input",key:"input"},{name:"Cached input",key:"cacheRead"},{name:"Cache writes",key:"cacheWrite"},{name:"Output",key:"output"},{name:"Known cache savings",key:"cacheSavings"}]
                                Column {
                                    required property var modelData
                                    Layout.fillWidth: true; spacing: 8
                                    Sub { text: modelData.name }
                                    Label { text: root.data ? (modelData.key === "cacheSavings" ? root.money(root.data.summary[modelData.key]) : root.compact(root.data.summary[modelData.key])) : "…"; font.pixelSize: 21 }
                                }
                            }
                        }
                    }
                    Card {
                        width: parent.width; height: tableColumn.implicitHeight + 36
                        Column {
                            id: tableColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 10
                            RowLayout {
                                width: parent.width
                                Label { text: "Breakdown"; font.pixelSize: 16; font.weight: Font.DemiBold }
                                Item { Layout.fillWidth: true }
                                Repeater { model: ["models","projects","clients","routes","accounts","sessions"]
                                    Choice { required property string modelData; text: modelData[0].toUpperCase()+modelData.slice(1); selected: root.breakdown===modelData; onClicked: { root.breakdown=modelData; root.tableLimit=20 } }
                                }
                            }
                            RowLayout { width: parent.width
                                Sub { text: root.breakdown === "models" ? "MODEL" : root.breakdown === "projects" ? "PROJECT" : root.breakdown === "sessions" ? "SESSION / PROJECT" : root.breakdown === "routes" ? "SOURCE ROUTE" : root.breakdown === "accounts" ? "ACCOUNT" : "CLIENT"; Layout.fillWidth: true }
                                Sub { text: "TOKENS"; Layout.preferredWidth: 95; horizontalAlignment: Text.AlignRight }
                                Sub { text: "API VALUE"; Layout.preferredWidth: 150; horizontalAlignment: Text.AlignRight }
                                Sub { text: "CACHE READ"; Layout.preferredWidth: 95; horizontalAlignment: Text.AlignRight }
                            }
                            Repeater {
                                model: root.data ? (root.data[root.breakdown] || []).slice(0,root.tableLimit) : []
                                Rectangle {
                                    required property var modelData
                                    width: tableColumn.width; height: root.breakdown === "sessions" ? 62 : 43; color: rowHover.hovered ? Qt.alpha(root.ink,0.04) : "transparent"
                                    Behavior on color { ColorAnimation { duration: 100 } }
                                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.edge; opacity: 0.55 }
                                    RowLayout {
                                        anchors.fill: parent; spacing: 12
                                        Rectangle { implicitWidth: 6; implicitHeight: 6; radius: 1; color: root.colorFor(modelData.provider) }
                                        Label {
                                            id: modelLabel
                                            Layout.fillWidth: true; elide: Text.ElideMiddle
                                            activeFocusOnTab: root.breakdown !== "sessions"
                                            color: activeFocus ? root.bright : root.ink
                                            Accessible.role: Accessible.Button
                                            Accessible.name: "Explore "+modelData.name
                                            Keys.onReturnPressed: { if (root.breakdown !== "sessions") root.drill(root.breakdown === "models" ? "model" : root.breakdown === "projects" ? "project" : root.breakdown === "routes" ? "apiProvider" : root.breakdown === "accounts" ? "account" : "client", root.breakdown === "accounts" ? modelData.accountId : modelData.name, modelData.provider) }
                                            text: root.breakdown === "sessions" ? (modelData.project.split("/").filter(x=>x).pop() || "/")+" · "+modelData.name.slice(0,12)+"\n"+modelData.client+" · "+root.when(modelData.lastAt) : root.breakdown === "projects" ? modelData.name.split('/').filter(x=>x).pop() || "/" : modelData.name
                                            HoverTip {
                                                parent: modelLabel
                                                visible: rowHover.hovered
                                                y: parent.height+10
                                                heading: root.breakdown === "sessions" ? modelData.project+"\n"+modelData.name : modelData.name
                                                detail: modelData.sessions+" sessions · "+root.compact(modelData.requests)+" usage records"
                                                rows: [{label:"Uncached input",value:root.compact(modelData.input)},
                                                       {label:"Cached input",value:root.compact(modelData.cacheRead)},
                                                       {label:"Cache writes",value:root.compact(modelData.cacheWrite)},
                                                       {label:"Output",value:root.compact(modelData.output)}]
                                            }
                                            HoverHandler { id: rowHover; cursorShape: root.breakdown === "sessions" ? Qt.ArrowCursor : Qt.PointingHandCursor }
                                            TapHandler { onTapped: { if (root.breakdown !== "sessions") root.drill(root.breakdown === "models" ? "model" : root.breakdown === "projects" ? "project" : root.breakdown === "routes" ? "apiProvider" : root.breakdown === "accounts" ? "account" : "client", root.breakdown === "accounts" ? modelData.accountId : modelData.name, modelData.provider) } }
                                        }
                                        Label { text: root.compact(modelData.tokens); Layout.preferredWidth: 95; horizontalAlignment: Text.AlignRight }
                                        Label { text: root.valueText(modelData); Layout.preferredWidth: 150; horizontalAlignment: Text.AlignRight; color: modelData.unpricedTokens ? root.colorFor("claude") : root.ink }
                                        Sub { text: (modelData.tokens ? modelData.cacheRead/modelData.tokens*100 : 0).toFixed(1)+"%"; Layout.preferredWidth: 95; horizontalAlignment: Text.AlignRight }
                                    }
                                }
                            }
                            Choice { visible: !!root.data && (root.data[root.breakdown] || []).length > root.tableLimit; text: "Show 20 more"; onClicked: root.tableLimit += 20 }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: "A recorded session is not a completed task. Averages cover this period; priced value excludes unknown prices." }
                            Sub { visible: !!root.data && !(root.data[root.breakdown] || []).length; text: "No recorded activity in this period." }
                        }
                    }
                    Card {
                        width: parent.width; height: 150
                        Column {
                            anchors.fill: parent; anchors.margins: 18; spacing: 12
                            RowLayout { width: parent.width
                                Label { text: "Activity over the past year"; font.pixelSize: 15; font.weight: Font.DemiBold }
                                Item { Layout.fillWidth: true }
                                Sub { text: "Darker to brighter = more tokens" }
                            }
                            Canvas {
                                id: heatmap
                                width: parent.width; height: 80
                                property var activity: root.data ? root.data.heatmap : ({})
                                onActivityChanged: requestPaint()
                                onWidthChanged: requestPaint()
                                property string hoverText: ""
                                property real pointerX: 0
                                property string hoverDate: ""
                                onPaint: {
                                    var ctx=getContext('2d'); ctx.reset()
                                    var today=new Date();today.setHours(12,0,0,0)
                                    var max=1;for(var key in activity)max=Math.max(max,activity[key])
                                    var cell=Math.min(15,(width-10)/53), sy=11
                                    for(var i=0;i<371;i++){
                                        var day=new Date(today);day.setDate(day.getDate()-370+i)
                                        var k=Qt.formatDate(day,'yyyy-MM-dd'),n=activity[k]||0
                                        ctx.fillStyle=n?Qt.alpha(root.colorFor("codex"),0.18+0.82*Math.sqrt(n/max)):Qt.alpha(root.ink,0.065)
                                        ctx.fillRect(Math.floor(i/7)*cell,(i%7)*sy,cell-3,8)
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true
                                    onPositionChanged: mouse => {
                                        var cell=Math.min(15,(width-10)/53), i=Math.floor(mouse.x/cell)*7+Math.floor(mouse.y/11)
                                        var d=new Date();d.setDate(d.getDate()-370+i)
                                        var key=Qt.formatDate(d,'yyyy-MM-dd')
                                        heatmap.pointerX=mouse.x;heatmap.hoverDate=key
                                        heatmap.hoverText=i>=0&&i<371?(heatmap.activity[key]?root.compact(heatmap.activity[key])+" tokens":"No recorded activity"):""
                                    }
                                    onExited: heatmap.hoverText=""
                                }
                                HoverTip {
                                    parent: heatmap
                                    visible: heatmap.hoverText!==""
                                    x: Math.max(0,Math.min(heatmap.width-width,heatmap.pointerX+16))
                                    y: -implicitHeight-10
                                    heading: heatmap.hoverDate ? Qt.formatDate(new Date(heatmap.hoverDate+"T12:00:00"),"dddd, MMM d, yyyy") : ""
                                    detail: heatmap.hoverText
                                    implicitWidth: 280
                                }
                            }
                        }
                    }
                    Card {
                        width: parent.width; height: coverageColumn.implicitHeight+36
                        Column {
                            id: coverageColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 9
                            Label { text: "Data coverage"; font.pixelSize: 15; font.weight: Font.DemiBold }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.data ? "Local history on "+root.data.coverage.machine+". Additional configured homes: "+(root.data.coverage.additionalHomes||0)+". A scan reads available folders; it does not sync another machine. Normal ChatGPT chats are not included." : "Reading sources…" }
                            Repeater {
                                model: root.data ? root.data.coverage.sources || [] : []
                                Column {
                                    required property var modelData
                                    width: coverageColumn.width; spacing: 4
                                    Label { width: parent.width; elide: Text.ElideMiddle; font.pixelSize: 12; text: modelData.path; color: modelData.status === "available" ? root.ink : root.colorFor("claude") }
                                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: (modelData.status || "Unknown")+" · "+modelData.files+(modelData.kind === "database" ? " database" : " files")+(modelData.latestFileAt ? " · latest file change "+root.when(modelData.latestFileAt) : "") }
                                }
                            }
                            Label { text: "Indexed clients · all retained history"; font.pixelSize: 14 }
                            Repeater {
                                model: root.data ? root.data.coverage.clients || [] : []
                                Sub { required property var modelData; width: coverageColumn.width; wrapMode: Text.WordWrap; text: modelData.provider+" / "+modelData.client+" · "+modelData.sessions+" sessions · latest event "+root.when(modelData.lastAt) }
                            }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: root.data ? "Pricing: "+root.data.pricing.source+(root.data.pricing.fetchedAtMs?" · "+Qt.formatDateTime(new Date(root.data.pricing.fetchedAtMs),"MMM d, yyyy"):"")+". Estimates use this catalog's rates, not historical billing rates." : "" }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Grok, OpenCode, Pi, and Oh My Pi use recorded API estimates when available. Their recorded totals do not provide cache savings. Usage records are message snapshots or completed Grok turns, not equivalent request counts. Turns without detailed usage are excluded." }
                            Repeater {
                                model: root.data ? root.data.pricing.unpriced || [] : []
                                Sub { required property var modelData; width: coverageColumn.width; wrapMode: Text.WordWrap; text: modelData.provider+" / "+modelData.name+": "+root.compact(modelData.unpricedTokens)+" unpriced tokens" }
                            }
                            Label { width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; color: root.colorFor("claude"); visible: !!root.data && root.data.unknownModels.length>0; text: root.data ? "Unpriced models: "+root.data.unknownModels.join(", ")+". Their tokens are included; their API value is not." : "" }
                            Label { width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; color: root.colorFor("claude"); visible: !!root.data && (root.data.coverage.warnings||[]).length>0; text: root.data ? (root.data.coverage.warnings||[]).join("\n") : "" }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Today compares with yesterday up to the same local time. Longer ranges compare with the full preceding calendar period. Saved metrics remain when transcripts are archived or removed." }
                        }
                    }
                    Item { width: 1; height: 8 }
                }
            }
            ScrollView {
                id: settingsScroll
                ScrollBar.vertical: QuietScrollBar { parent: settingsScroll; x: settingsScroll.width-width; height: settingsScroll.height }
                visible: root.settingsOpen
                Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                Column {
                    width: settingsScroll.availableWidth; spacing: 18
                    Label { text: "Make it yours"; font.pixelSize: 24; font.weight: Font.DemiBold }
                    Sub { text: "Analytics preferences stay on this machine. Credentials remain in their existing apps." }
                    Label { text: "Visible providers"; font.pixelSize: 16 }
                    Flow { width: parent.width; spacing: 10
                        Repeater { model: root.providerOptions
                            Choice {
                                required property var modelData
                                text: modelData.name; selected: root.draftEnabled.indexOf(modelData.id)>=0
                                onClicked: { var a=root.draftEnabled.slice();var i=a.indexOf(modelData.id);if(i>=0)a.splice(i,1);else a.push(modelData.id);root.draftEnabled=a }
                            }
                        }
                    }
                    Label { text: "Window transparency"; font.pixelSize: 16 }
                    RowLayout {
                        width: 460; spacing: 18
                        Slider {
                            id: opacitySlider
                            from: 0.55; to: 1; stepSize: 0.005
                            implicitHeight: 36
                            Layout.minimumHeight: 36
                            hoverEnabled: true
                            snapMode: Slider.SnapAlways
                            Layout.fillWidth: true
                            Accessible.name: "Window opacity"
                            background: Rectangle { x: opacitySlider.leftPadding; y: opacitySlider.topPadding + opacitySlider.availableHeight/2-2; width: opacitySlider.availableWidth; height: 3; color: root.edge
                                Rectangle { width: parent.width*opacitySlider.visualPosition; height: 3; color: root.accent }
                            }
                            handle: Rectangle { implicitWidth: 16; implicitHeight: 16; x: opacitySlider.leftPadding + opacitySlider.visualPosition*(opacitySlider.availableWidth-width); y: opacitySlider.topPadding+opacitySlider.availableHeight/2-height/2; width: 16; height: 16; radius: 3; color: opacitySlider.pressed ? root.accent : root.ink; border.color: opacitySlider.activeFocus ? root.bright : root.edge }
                        }
                        Sub { text: (opacitySlider.value*100).toFixed(1)+"% opacity" }
                    }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Drag to preview. Save preferences to keep it. At 100%, the background is fully opaque." }
                    Label { text: "Monthly subscription prices · USD"; font.pixelSize: 16 }
                    Sub { text: "Optional. API value is compared with this price; it is not an invoice or a billing-cycle calculation." }
                    Flow { width: parent.width; spacing: 14
                        Repeater { model: root.providerOptions.filter(p => root.draftEnabled.indexOf(p.id)>=0)
                            Column {
                                required property var modelData
                                spacing: 6
                                Sub { text: modelData.name }
                                Field { width: 170; placeholderText: "Not set"; Accessible.name: modelData.name+" monthly price"
                                    text: root.draftPrices[modelData.id] || ""
                                    onTextEdited: root.draftPrices[modelData.id] = text
                                }
                            }
                        }
                        Repeater { model: root.draftAccounts
                            Column {
                                required property var modelData
                                spacing: 6
                                Sub { text: (modelData.label || "Untitled account") + (root.accountSources(modelData) ? " · " + root.accountSources(modelData) : "") }
                                Field { width: 170; placeholderText: "Not set"; Accessible.name: (modelData.label || "Account")+" monthly price"
                                    text: root.draftPrices[modelData.id] || ""
                                    onTextEdited: root.draftPrices[modelData.id] = text
                                }
                            }
                        }
                    }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Prices on a provider apply to the local history group; prices on a labelled account apply only to that account." }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Grok quota uses its existing login. If it expires, run grok login. The dashboard never changes credentials." }
                    Label { text: "History accounts"; font.pixelSize: 16 }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Label agent home folders by account. Keep mirrored folders under the same account. Labels do not switch logins; quota is only for the current login on this PC." }
                    Field { width: 260; text: root.draftLocalLabel; placeholderText: "Local account name"; onTextEdited: root.draftLocalLabel = text; Accessible.name: "Local account name" }
                    Repeater { model: root.draftAccounts
                        Column {
                            id: accountEditor
                            required property var modelData
                            required property int index
                            width: parent.width; spacing: 10
                            RowLayout { width: parent.width
                                Field { Layout.fillWidth: true; text: accountEditor.modelData.label; placeholderText: "Account name, e.g. Work"; onTextEdited: root.draftAccounts[accountEditor.index].label = text }
                                Choice { text: "Remove account"; onClicked: { var a=root.draftAccounts.slice(); a.splice(accountEditor.index,1); root.draftAccounts=a } }
                            }
                            Repeater { model: accountEditor.modelData.directories
                                RowLayout {
                                    required property var modelData
                                    required property int index
                                    width: accountEditor.width
                                    ComboBox { Layout.preferredWidth: 170; model: root.providerOptions; textRole: "name"; valueRole: "id"; currentIndex: root.providerOptions.findIndex(p=>p.id===modelData.provider)
                                        font.family: root.fontFamily; font.pixelSize: 12; implicitHeight: 42; palette.button: root.base; palette.buttonText: root.ink; palette.window: root.base; palette.text: root.ink; palette.highlight: root.accent
                                        onActivated: root.draftAccounts[accountEditor.index].directories[index].provider = currentValue
                                    }
                                    Field { Layout.fillWidth: true; text: modelData.path; placeholderText: "Full agent home folder, e.g. /mnt/work/.codex"; onTextEdited: root.draftAccounts[accountEditor.index].directories[index].path = text }
                                    Choice { text: "Remove"; onClicked: { root.draftAccounts[accountEditor.index].directories.splice(index,1); root.draftAccounts=JSON.parse(JSON.stringify(root.draftAccounts)) } }
                                }
                            }
                            Choice { text: "Add folder"; onClicked: { root.draftAccounts[accountEditor.index].directories.push({provider:"codex",path:""}); root.draftAccounts=JSON.parse(JSON.stringify(root.draftAccounts)) } }
                            Rectangle { width: parent.width; height: 1; color: root.edge }
                        }
                    }
                    Choice { text: "Add account"; onClicked: { root.draftAccounts=root.draftAccounts.concat([{id:"account-"+Date.now()+"-"+Math.random().toString(36).slice(2,8),label:"",directories:[{provider:"codex",path:""}]}]) } }
                    Label { text: "Unlabelled additional history folders"; font.pixelSize: 16 }
                    Sub { width: parent.width; wrapMode: Text.WordWrap; text: "One full home-folder path per line, such as /mnt/other-computer/.codex. Use folders you have already mounted or synced. No remote connection is made. Copies with stable session IDs are deduplicated." }
                    Repeater { model: root.homeOptions.filter(h => root.draftEnabled.indexOf(h.key.replace(/Homes$/, ""))>=0 || (h.key === "opencodeHomes" && root.draftEnabled.indexOf("opencode-go")>=0))
                        Column {
                            required property var modelData
                            width: parent.width; spacing: 6
                            Sub { text: modelData.name }
                            Homes { width: parent.width; height: 70; placeholderText: modelData.example; Accessible.name: modelData.name
                                text: root.draftHomes[modelData.key] || ""
                                onTextChanged: root.draftHomes[modelData.key] = text
                            }
                        }
                    }
                    Card {
                        width: parent.width; height: 88
                        Column { anchors.fill: parent; anchors.margins: 16; spacing: 8
                            Label { text: "OpenCode Go connection" }
                            Sub { width: parent.width; wrapMode: Text.WordWrap; text: "Uses the existing OpenCode Go API key in OpenCode. No cookie is needed. Quota is refreshed in the background; reconnect in OpenCode if authentication expires." }
                        }
                    }


                }
            }
        }
        }
    }
}
