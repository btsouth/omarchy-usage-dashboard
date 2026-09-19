import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).parents[1]
APP='omarchy-usage-dashboard'
PLUGIN='community.ai-usage-dashboard'

def with_stub(env,home):
    folder=home/'test-bin';folder.mkdir()
    stub=folder/'quickshell';stub.write_text('#!/bin/sh\nexit 0\n');stub.chmod(0o755)
    env['PATH']=str(folder)+os.pathsep+env['PATH']
    return env
class InstallationTests(unittest.TestCase):
    def test_clean_install_repeat_upgrade_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/"home with $literal 'quotes'";home.mkdir()
            env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_STATE_HOME=str(home/'state'),XDG_DATA_HOME=str(home/'data'))
            env=with_stub(env,home)
            shell=home/'config/omarchy/shell.json';shell.parent.mkdir(parents=True)
            original={'version':1,'bar':{'layout':{'right':[{'id':'omarchy.agents','custom':'keep'}]}}}
            shell.write_text(json.dumps(original))
            history=home/'state/omarchy/ai-usage/usage.sqlite';history.parent.mkdir(parents=True);history.write_bytes(b'keep metrics')
            prefs=home/'config/omarchy/ai-usage/settings.json';prefs.parent.mkdir(parents=True);prefs.write_text('{"monthlyPrices":{"codex":200}}')
            def run(*args,check=True): return subprocess.run(['python3',str(ROOT/'install.py'),*args,'--no-systemd'],env=env,capture_output=True,text=True,check=check)
            run('--with-plugin')
            registry=home/'state'/APP/'installation.json';before=registry.read_bytes()
            runtime=home/'data'/APP/'app';self.assertTrue((runtime/'catalog.json').exists())
            self.assertTrue((runtime/'muse-pricing.json').exists())
            self.assertTrue((runtime/'codex-pricing.json').exists())
            self.assertTrue((runtime/'ollama-pricing.json').exists())
            self.assertTrue((runtime/'clinepass-pricing.json').exists())
            run('--with-plugin');self.assertEqual(before,registry.read_bytes())
            launcher=home/'.local/bin'/APP
            self.assertEqual(shlex.split(launcher.read_text().splitlines()[1])[1],str(runtime/"launch.sh"))
            desktop=home/'data/applications'/f'{APP}.desktop'
            subprocess.run(['desktop-file-validate',str(desktop)],check=True,capture_output=True)
            # A later edit must stop upgrade before any earlier planned file changes.
            (runtime/'collector.py').write_text('# keep my edit\n')
            result=run('--with-plugin',check=False);self.assertNotEqual(result.returncode,0)
            self.assertIn('Preserving locally edited file',result.stderr)
            self.assertEqual(before,registry.read_bytes())
            result=run('--uninstall');self.assertIn('Kept later edit',result.stdout)
            self.assertEqual((runtime/'collector.py').read_text(),'# keep my edit\n')
            self.assertFalse(launcher.exists())
            self.assertEqual(history.read_bytes(),b'keep metrics')
            self.assertEqual(json.loads(prefs.read_text())['monthlyPrices']['codex'],200)
            self.assertEqual(json.loads(shell.read_text()),original)

    def test_clash_warning_names_builtin_and_uninstall_removes_customized_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home';home.mkdir()
            env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_STATE_HOME=str(home/'state'),XDG_DATA_HOME=str(home/'data'))
            env=with_stub(env,home)
            # The built-in widget's manifest lives under OMARCHY_PATH on a real
            # install; stage a fixture copy so the check runs off this machine.
            agents=home/'omarchy/shell/plugins/agents';agents.mkdir(parents=True)
            (agents/'manifest.json').write_text(json.dumps(
                {'id':'omarchy.agents','barWidget':{'aliases':['agents','model-usage']}}))
            env['OMARCHY_PATH']=str(home/'omarchy')
            shell=home/'config/omarchy/shell.json';shell.parent.mkdir(parents=True)
            shell.write_text(json.dumps({'version':1,'bar':{'layout':{'right':[]}}}))
            def run(*args,check=True): return subprocess.run(['python3',str(ROOT/'install.py'),*args,'--no-systemd'],env=env,capture_output=True,text=True,check=check)
            result=run('--with-plugin');self.assertEqual(result.returncode,0)
            self.assertIn('omarchy.agents (built-in)',result.stdout)
            # A user who followed the settings advice customized the entry;
            # uninstall must still remove it by id.
            layout=json.loads(shell.read_text())
            layout['bar']['layout']['right'][0]['providerOrder']=['codex']
            shell.write_text(json.dumps(layout))
            run('--uninstall')
            self.assertEqual(json.loads(shell.read_text())['bar']['layout']['right'],[])

    def test_dashboard_only_does_not_touch_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,HOME=tmp,XDG_CONFIG_HOME=tmp+'/config',XDG_STATE_HOME=tmp+'/state',XDG_DATA_HOME=tmp+'/data')
            env=with_stub(env,Path(tmp))
            subprocess.run(['python3',str(ROOT/'install.py'),'--no-systemd'],env=env,capture_output=True,check=True)
            self.assertFalse(Path(tmp,'config/omarchy/shell.json').exists())
            subprocess.run(['python3',str(ROOT/'install.py'),'--uninstall','--no-systemd'],env=env,capture_output=True,check=True)
            self.assertFalse(Path(tmp,'data',APP,'app').exists())
