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
from urllib import request

RESET_CREDITS_URL = 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits'


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


def fetch_banked_resets(home):
    """Read one account's spendable reset count from that home's credentials."""
    auth = json.loads((Path(home) / 'auth.json').read_text())
    tokens = auth.get('tokens') or {}
    access_token = tokens.get('access_token')
    account_id = tokens.get('account_id')
    if not access_token or not account_id:
        raise ValueError('Codex account credentials unavailable')
    headers = {'Authorization': 'Bearer ' + access_token,
               'ChatGPT-Account-Id': account_id,
               'Accept': 'application/json', 'Cache-Control': 'no-cache',
               'User-Agent': 'omarchy-usage-dashboard'}
    with request.urlopen(request.Request(RESET_CREDITS_URL, headers=headers), timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError('Unrecognized reset-credit response')
    count = payload.get('available_count')
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    credits = payload.get('credits')
    if not isinstance(credits, list):
        raise ValueError('Unrecognized reset-credit response')
    now = datetime.now(timezone.utc)
    total = 0
    for credit in credits:
        if not isinstance(credit, dict) or credit.get('status') != 'available':
            continue
        expiry = credit.get('expires_at')
        if expiry:
            try:
                if datetime.fromisoformat(str(expiry).replace('Z', '+00:00')) <= now:
                    continue
            except (ValueError, TypeError):
                pass
        total += 1
    return total


def repair(path, home):
    try:
        before = path.read_bytes()
        record = json.loads(before)
    except (OSError, ValueError):
        return False
    changed = False
    if record.get('usageStatusText') == 'Codex limits unavailable' and record.get('authHelpText') in ('account/read', 'account/rateLimits/read'):
        try:
            limits, tier = fetch(home)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            pass
        else:
            record.update(limits=limits, tierLabel=tier, usageStatusText='', authHelpText='Run `codex login` to authenticate.')
            changed = True
    # Custom collectors opt in by writing this field. Refresh it against the
    # matching account, even when the limits RPC succeeded. A failed read is
    # unknown, never a reason to keep displaying a possibly spent credit.
    if 'resetCreditsAvailable' in record:
        try:
            count = fetch_banked_resets(home)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            count = None
        if record['resetCreditsAvailable'] != count:
            record['resetCreditsAvailable'] = count
            changed = True
    if not changed:
        return False
    # A concurrent refresh owns a newer record; leave it alone.
    try:
        if path.read_bytes() != before:
            return False
    except OSError:
        return False
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
    # The main card belongs to the default home. A caller may itself be
    # running under another CODEX_HOME (for example a second Codex session).
    for agent, folder in homes(config, home / '.codex').items():
        path = usage / (agent + '.json')
        if path.exists():
            repair(path, folder)


if __name__ == '__main__':
    sys.exit(main())
