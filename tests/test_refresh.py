import os
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
REFRESH = ROOT / 'refresh.sh'


class RefreshTests(unittest.TestCase):
    def run_refresh(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bin = tmp / 'bin'
            bin.mkdir()
            capture = tmp / 'args'
            # The packaged updater tolerates a failing collector the same way
            # refresh.sh does; exit nonzero here to prove the failure cannot
            # leak into refresh.sh's own exit status.
            updater = bin / 'omarchy-agent-usage-update'
            updater.write_text('#!/bin/bash\necho "$@" >> "$CAPTURE"\nexit 3\n')
            updater.chmod(0o755)
            env = dict(os.environ, PATH=str(bin) + ':/usr/bin:/bin', CAPTURE=str(capture),
                       HOME=str(tmp), XDG_CONFIG_HOME=str(tmp / 'config'),
                       XDG_STATE_HOME=str(tmp / 'state'), XDG_DATA_HOME=str(tmp / 'data'))
            result = subprocess.run(['bash', str(REFRESH), *args],
                                    env=env, capture_output=True, text=True)
            forwarded = capture.read_text().strip() if capture.exists() else ''
            collector_args = None
        return result, forwarded

    def test_forwards_panel_arguments_to_updater(self):
        result, forwarded = self.run_refresh('--limits-only', '--except', 'grok', 'codex')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(forwarded, '--limits-only --except grok codex')

    def test_forwards_force_and_limit_flags_verbatim(self):
        result, forwarded = self.run_refresh('--force', '--limits-only')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(forwarded, '--force --limits-only')


if __name__ == '__main__':
    unittest.main()
