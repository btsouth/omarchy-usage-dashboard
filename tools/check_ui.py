#!/usr/bin/env python3
"""Check account editing in an isolated offscreen Quickshell instance."""
import json, os, pathlib, shutil, subprocess, tempfile, time
root=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='usage-ui-qa-') as tmp:
 b=pathlib.Path(tmp); env=dict(os.environ, HOME=tmp,XDG_CONFIG_HOME=tmp+'/config',XDG_DATA_HOME=tmp+'/data',XDG_STATE_HOME=tmp+'/state',CODEX_HOME=tmp+'/codex',CLAUDE_CONFIG_DIR=tmp+'/claude',GROK_HOME=tmp+'/grok',PI_CODING_AGENT_DIR=tmp+'/pi',MUSE_HOME=tmp+'/muse',CURSOR_HOME=tmp+'/cursor',AI_USAGE_ROOT=str(root),AI_USAGE_DEMO='1',QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software')
 shutil.copytree(root/'ui',b/'ui'); p=b/'ui/shell.qml'; q=p.read_text().replace('implicitWidth: 1200','implicitWidth: 1000').replace('implicitHeight: 900','implicitHeight: 640')
 q=q.replace('function quit(): void', '''function qaAdd(): void { root.openSettings(); root.draftAccounts=[{id:"qa",label:"QA",directories:[{provider:"codex",path:"/tmp/qa/.codex"}]}] }
        function qaSave(): void { root.saveSettings() }
        function qaScroll(): void { settingsScroll.contentItem.contentY=620 }
        function qaClick(): void {
            function walk(item) {
                if (item.text === "Add folder" && item.clicked) { item.clicked(); return true }
                var kids=item.children || []
                for(var i=0;i<kids.length;i++) if(walk(kids[i])) return true
                return false
            }
            walk(captureRoot)
        }
        function qaFill(): void {
            function walk(item) {
                if (item.placeholderText === "Full agent home folder, e.g. /mnt/work/.codex" && item.text === "") { item.text="/tmp/qa-copy/.codex"; item.textEdited() }
                var kids=item.children || []
                for(var i=0;i<kids.length;i++) walk(kids[i])
            }
            walk(captureRoot)
        }
        function quit(): void''');p.write_text(q)
 proc=subprocess.Popen(['quickshell','-p',str(b/'ui'),'--no-color'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 def ipc(*args):
  r=subprocess.run(['quickshell','ipc','-p',str(b/'ui'),'--any-display','call','analytics',*args],env=env,capture_output=True,text=True)
  if r.returncode: raise RuntimeError(r.stderr+r.stdout)
 try:
  time.sleep(1.5);ipc('qaAdd');time.sleep(.4);ipc('qaClick');time.sleep(.3);ipc('qaFill');ipc('qaScroll');time.sleep(.3);ipc('capture',str(b/'account-editor.png'));time.sleep(.3);ipc('qaSave');time.sleep(1)
  settings=json.loads((b/'config/omarchy/ai-usage/settings.json').read_text())
  assert settings['accounts'][0]['directories']==[{'provider':'codex','path':'/tmp/qa/.codex'},{'provider':'codex','path':'/tmp/qa-copy/.codex'}],settings
  print('QML add-folder, field edit, save and reload passed at 1000x640')
 finally:
  proc.terminate();out=proc.communicate(timeout=5)[0]
  if any(e in out for e in ['ReferenceError','TypeError','Unable to assign','Failed to load']):raise RuntimeError(out)
