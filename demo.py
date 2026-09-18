#!/usr/bin/env python3
"""Run the dashboard with generated data in a temporary, isolated home."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--view', choices=['overview', 'settings', 'accounts', 'today'], default='overview')
parser.add_argument('--capture',type=Path,help='render a PNG offscreen and exit')
parser.add_argument('--agents',help='comma-separated providers to enable, default all')
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='usage-dashboard-demo-') as tmp:
    base=Path(tmp);env=dict(os.environ)
    env.update(HOME=tmp,XDG_STATE_HOME=str(base/'state'),XDG_CONFIG_HOME=str(base/'config'),
               XDG_DATA_HOME=str(base/'data'),CODEX_HOME=str(base/'codex'),CLAUDE_CONFIG_DIR=str(base/'claude'),GROK_HOME=str(base/'grok'),PI_CODING_AGENT_DIR=str(base/'pi'),
               AI_USAGE_ROOT=str(ROOT),AI_USAGE_DEMO='1')
    if args.capture: env.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software')
    os.environ.update(env)
    spec=importlib.util.spec_from_file_location('demo_collector',ROOT/'collector.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
    providers=[p for p in (args.agents.split(',') if args.agents else c.PROVIDERS) if p in c.PROVIDERS] or list(c.PROVIDERS)
    c.atomic_json(c.CONFIG, c.DEFAULTS | {'enabled': providers, 'accounts': [{'id':'work','label':'Work','directories':[{'provider':p,'path':str(base/'work'/p)} for p in providers]}]})
    ledger=c.Ledger(c.STATE/'usage.sqlite');rng=random.Random(7);now=dt.datetime.now().astimezone()
    for offset in range(7):
        date=now.date()-dt.timedelta(days=offset)
        for provider,model in [('codex','gpt-4.1'),('claude','claude-sonnet-4-20250514'),('opencode-go','glm-5.3-flash'),('grok','grok-demo'),('gemini','gemini-3.8-flash'),('opencode','example-model'),('pi','example-model'),('omp','example-model'),('muse','muse-spark-1.3-contributor'),('ollama-cloud','deepseek-v4.1-flash'),('commandcode','deepseek/deepseek-v4.1-flash'),('clinepass','cline-pass/deepseek-v4.1-flash')]:
            for index in range(rng.randint(15,50)):
                when=dt.datetime.combine(date,dt.time()).astimezone()+dt.timedelta(minutes=index*2)
                if when>now:continue
                ledger.put(c.record(f'{provider}-{offset}-{index}',provider,f'{provider}-{offset}-{index//8}',when.isoformat(),model,
                    '/demo/'+rng.choice(['website','notes','weather']),'CLI' if provider=='codex' else 'Claude Code' if provider=='claude' else 'Muse' if provider=='muse' else 'Hermes' if provider=='ollama-cloud' else 'Hermes' if provider in ('commandcode','clinepass') else 'OpenCode',
                    input=rng.randint(500,2000),output=rng.randint(100,1000),cacheRead=rng.randint(10000,100000)), base/('work' if index % 2 else 'local')/provider/'sessions/demo.jsonl')
    ledger.db.execute("UPDATE events SET reportedCostTicks=120000000,modelCalls=3 WHERE provider='grok'")
    ledger.db.execute("UPDATE events SET reportedValue=0.012,apiProvider='example-provider' WHERE provider IN ('opencode','pi','omp')")
    ledger.db.commit();ledger.db.close()
    # Copy QML into an isolated path so IPC cannot target the real dashboard.
    import shutil
    ui=base/'ui';shutil.copytree(ROOT/'ui',ui)
    proc=subprocess.Popen(['quickshell','-p',str(ui),'--no-color'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        if args.capture:
            output=args.capture.resolve();output.parent.mkdir(parents=True,exist_ok=True)
            output.unlink(missing_ok=True)
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError(proc.stdout.read())
                result=subprocess.run(['quickshell','ipc','-p',str(ui),'--any-display','call','analytics','capture',str(output)],capture_output=True,text=True,env=env)
                if output.exists():break
                time.sleep(0.1)
            if not output.exists():raise RuntimeError('Timed out rendering demo')
            # Allow the initial report to finish, then capture the populated view.
            time.sleep(1)
            if args.view != 'overview':
                command = ['preferences'] if args.view == 'settings' else ['period', '1'] if args.view == 'today' else ['account', 'work']
                subprocess.run(['quickshell','ipc','-p',str(ui),'--any-display','call','analytics',*command],check=True,env=env)
                time.sleep(0.8)
            subprocess.run(['quickshell','ipc','-p',str(ui),'--any-display','call','analytics','capture',str(output)],check=True,env=env)
            time.sleep(0.3);print(output)
        else:
            print('Demo data only. Press Ctrl+C here to exit.')
            proc.wait()
    except KeyboardInterrupt: pass
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log = proc.stdout.read()
        if 'ReferenceError' in log or 'TypeError' in log or 'Unable to assign' in log or 'Failed to load' in log:
            raise RuntimeError(log)
