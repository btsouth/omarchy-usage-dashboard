#!/usr/bin/env python3
"""Repair Codex limit records after Omarchy's app-server read times out.

Omarchy's RPC reader uses select() with a buffered text stream. A reply can
already be in Python's buffer when select() checks the file descriptor, making
account/read time out despite a healthy connection. Read bytes directly here.
"""

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


def rpc(proc, request_id, method, params=None, timeout=8):
    proc.stdin.write((json.dumps({'id': request_id, 'method': method,
                                  'params': params or {}}) + '\n').encode())
    proc.stdin.flush()
    deadline = time.monotonic() + timeout
    buffer = getattr(proc, '_usage_rpc_buffer', b'')
    while time.monotonic() < deadline:
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            proc._usage_rpc_buffer = buffer
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if message.get('id') == request_id:
                if message.get('error'):
                    raise RuntimeError(method)
                return message.get('result') or {}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if not select.select([proc.stdout], [], [], min(remaining, 0.25))[0]:
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buffer += chunk
        proc._usage_rpc_buffer = buffer
    raise TimeoutError(method)


def window(raw):
    if not isinstance(raw, dict) or raw.get('usedPercent') is None:
        return None
    minutes = int(raw.get('windowDurationMins') or 0)
    label = ('Weekly (7-day)' if minutes == 10080 else
             f'{minutes // 60}h window' if minutes and minutes % 60 == 0 else
             f'{minutes}m window' if minutes else 'Limit')
    reset = raw.get('resetsAt')
    return {'label': label, 'percent': float(raw['usedPercent']) / 100,
            'resetsAt': datetime.fromtimestamp(float(reset), timezone.utc).isoformat() if reset else ''}


def fetch(home):
    codex = shutil.which('codex')
    if not codex:
        raise FileNotFoundError('codex')
    env = os.environ.copy()
    env['CODEX_HOME'] = str(home)
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'):
        env.pop(key, None)
    proc = subprocess.Popen([codex, '-s', 'read-only', '-a', 'on-request', 'app-server'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    try:
        rpc(proc, 1, 'initialize', {'clientInfo': {'name': 'omarchy-usage-dashboard', 'version': '1'}})
        proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        account = (rpc(proc, 2, 'account/read').get('account') or {})
        limits = (rpc(proc, 3, 'account/rateLimits/read').get('rateLimits') or {})
        entries = [window(item) for item in (limits.get('primary'), limits.get('secondary'))]
        entries = [entry for entry in entries if entry]
        if not entries:
            raise ValueError('No Codex limit windows')
        return entries, str(limits.get('planType') or account.get('planType') or account.get('type') or '')
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def homes(config, default):
    result = {'codex': default}
    try:
        settings = json.loads(config.read_text())
    except (OSError, ValueError):
        return result
    for account in settings.get('accounts', []):
        folders = [item.get('path') for item in account.get('directories', [])
                   if item.get('provider') == 'codex' and item.get('path')]
        if len(folders) == 1 and account.get('id'):
            result['codex:' + str(account['id'])] = Path(folders[0])
            result[str(account['id'])] = Path(folders[0])
    return result


def repair(path, home):
    try:
        before = path.read_bytes()
        record = json.loads(before)
    except (OSError, ValueError):
        return False
    if record.get('usageStatusText') != 'Codex limits unavailable' or record.get('authHelpText') not in ('account/read', 'account/rateLimits/read'):
        return False
    try:
        limits, tier = fetch(home)
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return False
    # A concurrent refresh owns a newer record; leave it alone.
    try:
        if path.read_bytes() != before:
            return False
    except OSError:
        return False
    record.update(limits=limits, tierLabel=tier, usageStatusText='', authHelpText='Run `codex login` to authenticate.')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output, separators=(',', ':'))
            output.write('\n')
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main():
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')) / 'omarchy/ai-usage/settings.json'
    usage = Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')) / 'omarchy/agents/usage'
    for agent, folder in homes(config, Path(os.environ.get('CODEX_HOME', home / '.codex'))).items():
        path = usage / (agent + '.json')
        if path.exists():
            repair(path, folder)


if __name__ == '__main__':
    sys.exit(main())
