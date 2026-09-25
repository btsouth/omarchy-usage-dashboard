#!/usr/bin/env python3
"""Exercise the real panel's model summary without opening a desktop window."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--capture-source', type=Path, help='save an offscreen source picker image')
parser.add_argument('--theme', choices=['dark', 'light'], default='dark')
args = parser.parse_args()
omarchy_ui = Path('/usr/share/omarchy/shell/Ui')
omarchy_commons = Path('/usr/share/omarchy/shell/Commons')
if not omarchy_ui.exists() or not omarchy_commons.exists():
    raise SystemExit('This check needs the installed Omarchy shell')

with tempfile.TemporaryDirectory(prefix='.panel-qa-', dir=repo) as temp:
    root = Path(temp)
    (root / 'Commons').symlink_to(omarchy_commons)
    ui = root / 'Ui'
    ui.mkdir()
    for source in omarchy_ui.iterdir():
        if source.name != 'KeyboardPanel.qml':
            (ui / source.name).symlink_to(source)
    # The compositor-backed popup cannot load on Qt's offscreen platform.
    # Keep the panel content and model calculation, with only that host stubbed.
    (ui / 'KeyboardPanel.qml').write_text('''import QtQuick
Item {
  property Item anchorItem
  property var owner
  property var bar
  property bool open: false
  property Item focusTarget
  property int contentWidth: 460
  property int contentHeight: 900
  function fittedContentWidth(value) { return value }
  function fittedContentHeight(value, maximum) { return Math.min(value, maximum) }
  width: contentWidth
  height: contentHeight
}
''')
    shutil.copytree(repo / 'plugin', root / 'plugin')
    panel_file = root / 'plugin/Panel.qml'
    panel_file.write_text(panel_file.read_text().replace(
        '  function modelTooltip(row) {',
        '  function qaSourcePicker() { return {label: providerSwitch.label, value: providerSwitch.value, text: providerSwitch.currentLabel()} }\n'
        '  function qaCaptureSource(path) { return providerSwitch.grabToImage(function(image) { image.saveToFile(path) }) }\n\n'
        '  function modelTooltip(row) {'))
    (root / 'shell.qml').write_text('''import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
  Window {
    width: 600
    height: 900
    visible: true
    color: "#151b18"
    Plugin.Panel { id: panel; width: 460; height: 20 }
  }
  IpcHandler {
    target: "panelqa"
    function inspect(): string {
      return JSON.stringify(panel.models.map(row => ({name: row.name, total: row.total,
        details: panel.modelTooltip(row)})))
    }
    function source(): string { return JSON.stringify(panel.qaSourcePicker()) }
    function selectCodex(): void { panel.selectedProviderId = "codex" }
    function captureSource(path: string): string { return String(panel.qaCaptureSource(path)) }
  }
}
''')
    usage = root / 'state/omarchy/agents/usage'
    usage.mkdir(parents=True)
    reset_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
    (usage / 'codex.json').write_text(json.dumps({
        'id': 'codex', 'name': 'Codex', 'activeDays': 1,
        'limits': [{'label': 'Weekly (7-day)', 'percent': 0.26, 'resetsAt': reset_at}],
        'modelUsage': {'gpt-6-sol': {'inputTokens': 100, 'outputTokens': 50,
                                     'cacheReadInputTokens': 200, 'cacheCreationInputTokens': 25}},
    }))
    (usage / 'claude.json').write_text(json.dumps({
        'id': 'claude', 'name': 'Claude', 'activeDays': 1,
        'limits': [{'label': 'Weekly (7-day)', 'percent': 0.58, 'resetsAt': reset_at}],
        'modelUsage': {'claude-sonnet-4': {'inputTokens': 20}},
    }))
    pins = root / 'config/omarchy/ai-usage/pinned-limit.json'
    pins.parent.mkdir(parents=True)
    pins.write_text(json.dumps({'pins': [{'provider': 'codex', 'label': 'Weekly (7-day)', 'title': 'Weekly'}]}))
    theme = root / '.local/state/omarchy/current/theme/colors.toml'
    theme.parent.mkdir(parents=True)
    theme.write_text('background = "#151b18"\nforeground = "#e8e6da"\naccent = "#7aaf92"\n'
                     if args.theme == 'dark' else
                     'background = "#faf7f0"\nforeground = "#292d32"\naccent = "#28654a"\n')
    refresh = root / '.local/bin/omarchy-usage-dashboard-refresh'
    refresh.parent.mkdir(parents=True)
    refresh.write_text('#!/bin/sh\nexit 0\n')
    refresh.chmod(0o755)
    env = dict(os.environ, HOME=temp, XDG_CONFIG_HOME=temp + '/config',
               XDG_STATE_HOME=temp + '/state', XDG_DATA_HOME=temp + '/data',
               AI_USAGE_ROOT=str(repo), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    proc = subprocess.Popen(['quickshell', '-p', str(root), '--no-color'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        rows = None
        for _ in range(40):
            if proc.poll() is not None:
                break
            call = subprocess.run(['quickshell', 'ipc', '-p', str(root), '--any-display',
                                   'call', 'panelqa', 'inspect'], env=env,
                                  capture_output=True, text=True)
            if call.returncode == 0:
                try:
                    rows = json.loads(call.stdout.strip())
                    if len(rows) == 2:
                        break
                except ValueError:
                    pass
            time.sleep(0.1)
        assert rows and [row['total'] for row in rows] == [375, 20], rows
        assert 'cache read 200' in rows[0]['details'], rows
        def ipc(method, *arguments):
            call = subprocess.run(['quickshell', 'ipc', '-p', str(root), '--any-display',
                                   'call', 'panelqa', method, *arguments], env=env,
                                  capture_output=True, text=True, check=True)
            return call.stdout.strip()
        source = json.loads(ipc('source'))
        assert source == {'label': 'SOURCE', 'value': 'all', 'text': 'All sources'}, source
        if args.capture_source:
            image_path = args.capture_source.resolve()
            image_path.unlink(missing_ok=True)
            assert ipc('captureSource', str(image_path)) == 'true'
            for _ in range(30):
                if image_path.exists(): break
                time.sleep(0.1)
            assert image_path.exists(), image_path
            print('Captured source picker:', image_path)
        ipc('selectCodex')
        focused = json.loads(ipc('source'))
        assert focused == {'label': 'SOURCE', 'value': 'codex', 'text': 'Main'}, focused
        print('Offscreen panel model rows, token details, and source picker passed')
    finally:
        proc.terminate()
        try:
            log = proc.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            log = proc.communicate()[0]
        if any(error in log for error in ('ReferenceError', 'TypeError', 'Unable to assign', 'Failed to load')):
            raise RuntimeError(log)
