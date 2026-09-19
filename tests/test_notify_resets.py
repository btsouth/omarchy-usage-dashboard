import json
import os
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
NOTIFIER = ROOT / 'notify-resets.sh'

INCLUDED = ['Weekly', 'Weekly (7-day)', 'Monthly']
EXCLUDED = ['Session (5-hour)', '5 hours', '5h window', '30m window']


class NotifyResetsTests(unittest.TestCase):
    def run_notifier(self, state, *args):
        stub = state / 'bin'
        stub.mkdir(exist_ok=True)
        calls = state / 'calls'
        sender = stub / 'notify-send'
        sender.write_text('#!/bin/bash\necho "$@" >> "$FAKE_LOG"\n')
        sender.chmod(0o755)
        env = dict(os.environ, XDG_STATE_HOME=str(state), FAKE_LOG=str(calls),
                   PATH=str(stub) + os.pathsep + os.environ['PATH'])
        for run in ('2026-09-19T10:00:00+00:00', '2026-09-19T15:00:01+00:00'):
            percent = 0.9 if '10:00' in run else 0.1
            (state / 'omarchy/agents/usage/p.json').write_text(json.dumps(
                {'id': 'p', 'name': 'P', 'resetCreditsAvailable': 0,
                 'limits': [{'label': self.label, 'percent': percent, 'resetsAt': run}]}))
            result = subprocess.run(['bash', str(NOTIFIER), '--provider', 'p', *args],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
        return calls.read_text() if calls.exists() else ''

    def check(self, label, notified):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'omarchy/agents/usage').mkdir(parents=True)
            self.label = label
            out = self.run_notifier(state)
        self.assertEqual(bool(out.strip()), notified, label)

    def test_long_windows_notify(self):
        for label in INCLUDED:
            self.check(label, True)

    def test_short_windows_stay_silent(self):
        for label in EXCLUDED:
            self.check(label, False)

    def test_banked_credits_notify_once_and_do_not_opt_in_other_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            usage = state / 'omarchy/agents/usage'; usage.mkdir(parents=True)
            stub = state / 'bin'; stub.mkdir()
            sender = stub / 'notify-send'
            sender.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$FAKE_LOG"\n')
            sender.chmod(0o755)
            calls = state / 'calls'
            env = dict(os.environ, HOME=tmp, XDG_STATE_HOME=tmp,
                       FAKE_LOG=str(calls), PATH=str(stub) + os.pathsep + os.environ['PATH'])
            def run(credits):
                for provider in ('codex', 'cursor'):
                    (usage / (provider + '.json')).write_text(json.dumps(
                        {'id': provider, 'name': provider, 'resetCreditsAvailable': credits, 'limits': []}))
                subprocess.run(['bash', str(NOTIFIER)], env=env, check=True, capture_output=True)
            run(1); self.assertFalse(calls.exists())
            run(2); run(2); run(1)
            lines = calls.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn('Banked resets: 1 -> 2', lines[0])
            self.assertNotIn('cursor', lines[0])

    def test_lock_holder_skips_second_run(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            usage = state / 'omarchy/agents/usage'
            usage.mkdir(parents=True)
            (usage / 'codex.json').write_text(json.dumps(
                {'id': 'codex', 'name': 'Codex',
                 'limits': [{'label': 'Weekly', 'percent': 0.9,
                             'resetsAt': '2026-09-19T10:00:00+00:00'}]}))
            lock = open(state / 'omarchy/agents/reset-snapshot.lock', 'w')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            env = dict(os.environ, XDG_STATE_HOME=str(state))
            result = subprocess.run(['bash', str(NOTIFIER)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertFalse((state / 'omarchy/agents/reset-snapshot.json').exists())
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


if __name__ == '__main__':
    unittest.main()
