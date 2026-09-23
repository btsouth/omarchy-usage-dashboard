import QtQuick
import Quickshell.Io

// One agent's usage record, read straight off the data file that
// omarchy-agent-usage-update maintains. The panel never learns how the
// numbers were made — a record that appears in the usage directory is an
// agent, whoever wrote it.
Item {
  id: root
  visible: false

  property string agentId: ""
  property string path: ""
  property var record: null

  FileView {
    path: root.path
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parse(text())
    onLoadFailed: root.record = null
  }

  function parse(content) {
    try {
      var parsed = JSON.parse(String(content || ""))
      // Keep the last successful window visible while the dashboard retries
      // Omarchy's intermittent Codex RPC timeout. Mark it as stale so the
      // panel does not present the old percentage as a fresh reading.
      if (parsed && root.record && parsed.id === root.record.id
          && parsed.usageStatusText === "Codex limits unavailable"
          && (parsed.authHelpText === "account/read" || parsed.authHelpText === "account/rateLimits/read")
          && root.record.limits && root.record.limits.length > 0) {
        parsed.limits = root.record.limits
        parsed.tierLabel = root.record.tierLabel
        parsed.usageStatusText = ""
        parsed.limitsStale = true
        parsed.retryAdvised = true
      }
      // Omarchy's own collector rewrites the record without banked resets,
      // and the dashboard refresh adds them back a moment later. Keep the
      // last count through that gap so the line does not blink out on every
      // refresh. A record that says null (a failed read) still clears it.
      if (parsed && root.record && parsed.id === root.record.id
          && parsed.resetCreditsAvailable === undefined
          && root.record.resetCreditsAvailable !== undefined) {
        parsed.resetCreditsAvailable = root.record.resetCreditsAvailable
        parsed.resetCreditsExpiresAt = root.record.resetCreditsExpiresAt
      }
      if (parsed && root.record && parsed.id === root.record.id
          && parsed.resetCreditsExpiresAt === undefined && parsed.resetCreditsAvailable !== null
          && root.record.resetCreditsExpiresAt !== undefined)
        parsed.resetCreditsExpiresAt = root.record.resetCreditsExpiresAt
      root.record = parsed && typeof parsed === "object" ? parsed : null
    } catch (e) {
      console.warn("agents", "Ignoring bad usage record", root.path, e)
      root.record = null
    }
  }
}
