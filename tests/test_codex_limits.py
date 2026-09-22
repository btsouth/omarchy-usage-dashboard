import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class CodexLimitRepairTests(unittest.TestCase):
    def test_repairs_failed_named_account_with_buffered_rpc_replies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            codex = bin_dir / 'codex'
            codex.write_text('''#!/usr/bin/python3
import sys
for line in sys.stdin:
    if '"id": 1' in line:
        sys.stdout.write('{"id":1,"result":{}}\\n')
    elif '"id": 2' in line:
        # A notification and reply arrive in one write. A buffered readline
        # can consume both but return only the first line to select's caller.
        sys.stdout.write('{"method":"notice"}\\n{"id":2,"result":{"account":{"planType":"pro"}}}\\n')
    elif '"id": 3' in line:
        sys.stdout.write('{"id":3,"result":{"rateLimits":{"primary":{"usedPercent":7,"windowDurationMins":10080,"resetsAt":1790000000}}}}\\n')
    sys.stdout.flush()
''')
            codex.chmod(0o755)
            config = root / 'config/omarchy/ai-usage/settings.json'
            config.parent.mkdir(parents=True)
            second = root / 'second/.codex'
            second.mkdir(parents=True)
            config.write_text(json.dumps({'accounts': [{'id': 'codex-second', 'directories':
                                [{'provider': 'codex', 'path': str(second)}]}]}))
            usage = root / 'state/omarchy/agents/usage'
            usage.mkdir(parents=True)
            record = usage / 'codex-second.json'
            record.write_text(json.dumps({'id': 'codex-second', 'usageStatusText':
                'Codex limits unavailable', 'authHelpText': 'account/read', 'limits': [],
                'todayPrompts': 4}))
            env = dict(os.environ, HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_STATE_HOME=str(root / 'state'), PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['python3', str(ROOT / 'codex_limits.py')], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            fixed = json.loads(record.read_text())
            self.assertEqual(fixed['usageStatusText'], '')
            self.assertEqual(fixed['tierLabel'], 'pro')
            self.assertEqual(fixed['limits'][0]['percent'], 0.07)
            self.assertEqual(fixed['todayPrompts'], 4)


if __name__ == '__main__':
    unittest.main()
