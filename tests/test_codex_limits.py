import json
import os
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import codex_limits

ROOT = Path(__file__).parents[1]


class CodexLimitRepairTests(unittest.TestCase):
    def test_banked_resets_use_each_accounts_auth_and_clear_spent_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_home = root / '.codex'
            second_home = root / 'second/.codex'
            for folder, account in ((main_home, 'main-account'), (second_home, 'second-account')):
                folder.mkdir(parents=True)
                (folder / 'auth.json').write_text(json.dumps({'tokens': {
                    'access_token': account + '-token', 'account_id': account}}))
            config = root / 'config/omarchy/ai-usage/settings.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'accounts': [{'id': 'codex-second', 'directories':
                [{'provider': 'codex', 'path': str(second_home)}]}]}))
            usage = root / 'state/omarchy/agents/usage'
            usage.mkdir(parents=True)
            for agent in ('codex', 'codex-second'):
                (usage / (agent + '.json')).write_text(json.dumps({
                    'id': agent, 'limits': [{'label': 'Weekly (7-day)', 'percent': 0.2}],
                    'resetCreditsAvailable': 1}))
            seen = []
            def response(req, timeout):
                token = req.get_header('Authorization')
                account = req.get_header('Chatgpt-account-id')
                seen.append((token, account))
                count = 1 if account == 'main-account' else 0
                return io.BytesIO(json.dumps({'available_count': count}).encode())
            env = {'HOME': str(root), 'XDG_CONFIG_HOME': str(root / 'config'),
                   'XDG_STATE_HOME': str(root / 'state'), 'CODEX_HOME': str(second_home)}
            with patch.dict(os.environ, env), patch.object(codex_limits.request, 'urlopen', side_effect=response):
                codex_limits.main()
            self.assertEqual(seen, [('Bearer main-account-token', 'main-account'),
                                    ('Bearer second-account-token', 'second-account')])
            self.assertEqual(json.loads((usage / 'codex.json').read_text())['resetCreditsAvailable'], 1)
            self.assertEqual(json.loads((usage / 'codex-second.json').read_text())['resetCreditsAvailable'], 0)

    def test_banked_resets_carry_the_earliest_live_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auth.json').write_text(json.dumps({'tokens': {
                'access_token': 'token', 'account_id': 'account'}}))
            record = root / 'codex.json'
            record.write_text(json.dumps({'id': 'codex', 'limits': [], 'resetCreditsAvailable': 0}))
            credits = [{'status': 'available', 'expires_at': '2099-11-01T00:00:00Z'},
                       {'status': 'available', 'expires_at': '2099-10-22T20:37:57Z'},
                       {'status': 'available', 'expires_at': '2000-01-01T00:00:00Z'},
                       {'status': 'redeemed', 'expires_at': '2099-01-01T00:00:00Z'}]
            body = json.dumps({'available_count': 2, 'credits': credits}).encode()
            with patch.object(codex_limits.request, 'urlopen', return_value=io.BytesIO(body)):
                self.assertTrue(codex_limits.repair(record, root))
            saved = json.loads(record.read_text())
            self.assertEqual(saved['resetCreditsAvailable'], 2)
            self.assertEqual(saved['resetCreditsExpiresAt'], '2099-10-22T20:37:57+00:00')

    def test_failed_credit_lookup_marks_count_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auth.json').write_text(json.dumps({'tokens': {
                'access_token': 'token', 'account_id': 'account'}}))
            record = root / 'codex.json'
            record.write_text(json.dumps({'id': 'codex', 'limits': [], 'resetCreditsAvailable': 1}))
            with patch.object(codex_limits.request, 'urlopen', side_effect=OSError('offline')):
                self.assertTrue(codex_limits.repair(record, root))
            saved = json.loads(record.read_text())
            self.assertIsNone(saved['resetCreditsAvailable'])
            self.assertEqual(saved['resetCreditsExpiresAt'], '')

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
