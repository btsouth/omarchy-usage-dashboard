import QtQuick

Text {
    id: counter
    property double targetTokens: 0
    property bool dataReady: false
    property bool animateChanges: true
    property string scopeKey: ""
    property bool seeded: false
    property string seededScope: ""
    property double displayedTokens: 0

    text: Math.round(displayedTokens).toLocaleString(Qt.locale("en_US"), "f", 0)

    function sync() {
        if (!dataReady) {
            roll.stop()
            seeded = false
            return
        }
        var next = Math.max(0, Math.round(Number(targetTokens) || 0))
        if (!seeded || seededScope !== scopeKey || !animateChanges || next <= displayedTokens) {
            roll.stop()
            displayedTokens = next
        } else if (next !== displayedTokens) {
            roll.stop()
            roll.from = displayedTokens
            roll.to = next
            roll.start()
        }
        seeded = true
        seededScope = scopeKey
    }

    // Coalesce a new feed and a source/day change before deciding whether to
    // roll. A new view starts at its real value instead of sweeping from zero.
    onTargetTokensChanged: Qt.callLater(sync)
    onDataReadyChanged: Qt.callLater(sync)
    onScopeKeyChanged: Qt.callLater(sync)
    onAnimateChangesChanged: Qt.callLater(sync)
    Component.onCompleted: Qt.callLater(sync)

    NumberAnimation {
        id: roll
        target: counter
        property: "displayedTokens"
        duration: 1200
        easing.type: Easing.OutCubic
    }
}
