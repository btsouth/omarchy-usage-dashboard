#!/usr/bin/env python3
"""Local metrics only. Source transcripts are read, never modified or copied."""
import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import time
import tomllib
import subprocess
import urllib.request

HOME = Path.home()
STATE = Path(os.getenv('XDG_STATE_HOME', HOME / '.local/state')) / 'omarchy/ai-usage'
CONFIG = Path(os.getenv('XDG_CONFIG_HOME', HOME / '.config')) / 'omarchy/ai-usage/settings.json'
PROVIDERS = {'codex': 'Codex', 'claude': 'Claude', 'opencode-go': 'OpenCode Go'}
DEFAULTS = {'enabled': list(PROVIDERS), 'monthlyPrices': {}, 'codexHomes': [], 'claudeHomes': [], 'windowOpacity': 0.985}
FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'cacheWrite1h', 'reasoning')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def settings():
    try: return DEFAULTS | json.loads(CONFIG.read_text())
    except (OSError, ValueError): return dict(DEFAULTS)


def theme():
    current = STATE.parent / 'current/theme'
    palette, shell = {}, {}
    for path, target in [(current / 'colors.toml', palette), (current / 'shell.toml', shell),
                         (CONFIG.parent.parent / 'shell.toml', shell)]:
        try:
            data = tomllib.loads(path.read_text())
            for key, value in data.items():
                if isinstance(value, dict): target[key] = target.get(key, {}) | value
                else: target[key] = value
        except (OSError, ValueError): pass
    try: font = subprocess.check_output(['fc-match', 'monospace', '-f', '%{family}'], text=True, timeout=2).split(',')[0]
    except (OSError, subprocess.SubprocessError): font = 'monospace'
    return {'palette': palette, 'shell': shell, 'font': font}


def save_settings(value):
    clean = {'enabled': [p for p in value.get('enabled', []) if p in PROVIDERS],
             'monthlyPrices': {}, 'codexHomes': [], 'claudeHomes': [],
             'windowOpacity': max(0.55, min(1.0, float(value.get('windowOpacity', 0.985))))}
    for provider, amount in value.get('monthlyPrices', {}).items():
        if provider in PROVIDERS and amount is not None and 0 <= float(amount) <= 100000:
            clean['monthlyPrices'][provider] = float(amount)
    for key in ('codexHomes', 'claudeHomes'):
        clean[key] = sorted({str(Path(p).expanduser().absolute()) for p in value.get(key, []) if str(p).strip()})
    atomic_json(CONFIG, clean)
    return clean


def number(value):
    try: return max(0, int(value or 0))
    except (ValueError, TypeError): return 0


def timestamp(value):
    if isinstance(value, (int, float)): return int(value / 1000 if value > 10_000_000_000 else value)
    try: return int(dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (ValueError, TypeError): return 0


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def record(key, provider, session, ts, model, project, client, **tokens):
    return {'id': key, 'provider': provider, 'session': session, 'ts': timestamp(ts),
            'model': model or 'unknown', 'project': str(project or ''), 'client': client,
            **{f: number(tokens.get(f)) for f in FIELDS}}


def codex_records(path):
    session, project, client, model = path.stem, '', 'CLI', 'unknown'
    started = 0
    seen = set()
    have_metadata = False
    with path.open(errors='replace') as f:
        for raw in f:
            try: item = json.loads(raw)
            except ValueError: continue
            p = item.get('payload') or {}
            if not isinstance(p, dict): continue
            kind = item.get('type')
            if kind == 'session_meta':
                # Fork history embeds the parent's session_meta after the
                # child's header. It must not replace the child's identity.
                if have_metadata: continue
                have_metadata = True
                session = str(p.get('id') or p.get('session_id') or session)
                project = p.get('cwd') or project
                client = 'Desktop' if 'desktop' in str(p.get('originator', '')).lower() else 'CLI'
                started = timestamp(p.get('timestamp'))
            elif kind == 'turn_context':
                model = p.get('model') or p.get('model_slug') or model
                project = p.get('cwd') or project
            elif kind == 'event_msg' and p.get('type') == 'token_count':
                info = p.get('info') or {}
                u = info.get('last_token_usage') or {}
                total = info.get('total_token_usage')
                ts = timestamp(item.get('timestamp'))
                # Forked rollouts can contain inherited history. Ignore events
                # predating this session and repeated cumulative snapshots.
                if not u or not ts or (started and ts < started): continue
                fingerprint = digest(total) if total else digest(ts, u)
                if fingerprint in seen: continue
                seen.add(fingerprint)
                read, write = number(u.get('cached_input_tokens')), number(u.get('cache_write_input_tokens'))
                yield record(digest('codex', session, fingerprint), 'codex', session, ts, model,
                             project, client, input=max(0, number(u.get('input_tokens')) - read - write),
                             output=u.get('output_tokens'), cacheRead=read, cacheWrite=write,
                             reasoning=u.get('reasoning_output_tokens'))


def claude_records(path):
    with path.open(errors='replace') as f:
        for raw in f:
            try: item = json.loads(raw)
            except ValueError: continue
            m = item.get('message') or {}
            if item.get('type') != 'assistant' or not isinstance(m, dict): continue
            u = m.get('usage') or {}
            model = m.get('model') or 'unknown'
            if not u or model == '<synthetic>': continue
            session = str(item.get('sessionId') or item.get('session_id') or path.stem)
            msg_id = m.get('id') or item.get('uuid')
            key = digest('claude', msg_id, item.get('requestId')) if msg_id else digest('claude', session, item.get('timestamp'), u)
            yield record(key, 'claude', session, item.get('timestamp'), model, item.get('cwd'),
                         'Claude Code', input=u.get('input_tokens'), output=u.get('output_tokens'),
                         cacheRead=u.get('cache_read_input_tokens'), cacheWrite=u.get('cache_creation_input_tokens'),
                         cacheWrite1h=(u.get('cache_creation') or {}).get('ephemeral_1h_input_tokens'),
                         reasoning=(u.get('output_tokens_details') or {}).get('thinking_tokens'))


class Ledger:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        os.chmod(path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, provider TEXT, session TEXT, ts INTEGER,
            model TEXT, project TEXT, client TEXT,
            input INTEGER, output INTEGER, cacheRead INTEGER, cacheWrite INTEGER,
            cacheWrite1h INTEGER, reasoning INTEGER);
          CREATE INDEX IF NOT EXISTS events_time ON events(ts);
          CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER);
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        ''')

    def put(self, r):
        if not r['ts'] or not sum(r[f] for f in FIELDS[:4]): return
        keys = list(r)
        # Claude streams may repeat a message with a larger final usage count.
        update = ','.join(f'{f}=MAX(events.{f},excluded.{f})' for f in FIELDS)
        self.db.execute(f'INSERT INTO events ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)}) '
                        f'ON CONFLICT(id) DO UPDATE SET {update}', list(r.values()))

    def scan(self, cfg):
        warnings, sources = [], []
        for provider, roots, parser in (
            ('codex', [os.getenv('CODEX_HOME', str(HOME / '.codex'))] + cfg['codexHomes'], codex_records),
            ('claude', [os.getenv('CLAUDE_CONFIG_DIR', str(HOME / '.claude'))] + cfg['claudeHomes'], claude_records)):
            for root in sorted(set(roots)):
                root = Path(root).expanduser()
                folders = [root / 'sessions', root / 'archived_sessions'] if provider == 'codex' else [root / 'projects']
                for folder in folders:
                    files = list(folder.rglob('*.jsonl')) if folder.exists() else []
                    source = {'provider': provider, 'path': str(folder), 'files': len(files), 'exists': folder.exists(),
                              'latestFileAt': None, 'readErrors': 0, 'kind': 'archive' if folder.name == 'archived_sessions' else 'history'}
                    sources.append(source)
                    for p in files:
                        try:
                            stat = p.stat()
                            source['latestFileAt'] = max(source['latestFileAt'] or 0, stat.st_mtime)
                            old = self.db.execute('SELECT size,mtime FROM files WHERE path=?', (str(p),)).fetchone()
                            if old == (stat.st_size, stat.st_mtime_ns): continue
                            for r in parser(p): self.put(r)
                            self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)', (str(p), stat.st_size, stat.st_mtime_ns))
                        except OSError:
                            source['readErrors'] += 1
                            warnings.append(f'Could not read {p.name}')
        opencode = Path(os.getenv('XDG_DATA_HOME', HOME / '.local/share')) / 'opencode/opencode.db'
        sources.append({'provider': 'opencode-go', 'path': str(opencode), 'files': int(opencode.exists()), 'exists': opencode.exists(), 'kind': 'database'})
        if opencode.exists():
            try:
                conn = sqlite3.connect(opencode.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
                # Read metric fields only; do not fetch prompt or response text.
                query = '''SELECT m.id,m.session_id,m.time_created,s.directory,
                  json_extract(m.data,'$.modelID'),json_extract(m.data,'$.tokens')
                  FROM message m LEFT JOIN session s ON s.id=m.session_id
                  WHERE json_extract(m.data,'$.role')='assistant'
                  AND json_extract(m.data,'$.providerID')='opencode-go' '''
                for mid, sid, ts, project, model, raw in conn.execute(query):
                    u = json.loads(raw or '{}'); cache = u.get('cache') or {}
                    self.put(record(digest('opencode-go', mid), 'opencode-go', sid, ts, model, project, 'OpenCode',
                                    input=u.get('input'), output=number(u.get('output')) + number(u.get('reasoning')),
                                    reasoning=u.get('reasoning'), cacheRead=cache.get('read'), cacheWrite=cache.get('write')))
                conn.close()
            except (sqlite3.Error, ValueError):
                sources[-1]['readErrors'] = 1
                warnings.append('OpenCode database could not be read; retained previous records.')
        for source in sources:
            source['status'] = 'missing' if not source['exists'] else 'partial' if source.get('readErrors') else 'available'
        meta = {'sources': sources, 'warnings': warnings, 'scannedAt': time.time(), 'machine': 'demo-computer' if os.getenv('AI_USAGE_DEMO') == '1' else socket.gethostname()}
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('scan', json.dumps(meta)))
        self.db.commit()
        return meta


def load_rates():
    path = STATE / 'rates.json'
    # Bundled, attributed snapshot works offline. A user catalog can override it.
    source = path if path.exists() else Path(__file__).with_name('catalog.json')
    try:
        data = json.loads(source.read_text())
        if not isinstance(data.get('document'), dict): raise ValueError('Invalid catalog')
        data.setdefault('source', 'User pricing catalog')
    except (OSError, ValueError, AttributeError):
        data = {'document': {}, 'source': 'Pricing catalog unavailable', 'fetchedAtMs': None}
    try:
        official = json.loads(Path(__file__).with_name('pricing.json').read_text())
        data['document'].update(official['models'])
        data['source'] += ' + OpenCode Go official rates (' + official['verifiedAt'] + ')'
    except (OSError, ValueError): pass
    return data


def price(r, catalog):
    model = r['model']
    rate = catalog.get(r['provider'] + '/' + model) or catalog.get(model) or catalog.get('anthropic/' + model) or catalog.get('openai/' + model)
    if not rate: return None, None
    context = r['input'] + r['cacheRead'] + r['cacheWrite']
    suffix = ''
    for threshold, candidate in [(200000, '_above_200k_tokens'), (272000, '_above_272k_tokens')]:
        if context > threshold and 'input_cost_per_token' + candidate in rate: suffix = candidate
    def cost(key, fallback=None): return rate.get(key + suffix, rate.get(key, fallback))
    inp, out = cost('input_cost_per_token'), cost('output_cost_per_token')
    read = cost('cache_read_input_token_cost')
    write = cost('cache_creation_input_token_cost')
    write1h = cost('cache_creation_input_token_cost_above_1hr', None)
    parts = [(r['input'], inp), (r['output'], out), (r['cacheRead'], read),
             (max(0, r['cacheWrite'] - r['cacheWrite1h']), write), (r['cacheWrite1h'], write1h)]
    if any(n and not isinstance(v, (int, float)) for n, v in parts): return None, None
    value = sum(n * (v or 0) for n, v in parts)
    saving = max(0, r['cacheRead'] * ((inp or 0) - (read or 0)))
    return value, saving


def bucket():
    return {**{f: 0 for f in FIELDS}, 'tokens': 0, 'value': 0.0, 'cacheSavings': 0.0,
            'unpricedTokens': 0, 'requests': 0, 'sessions': set()}


def add(b, r, value, savings):
    for f in FIELDS: b[f] += r[f]
    total = sum(r[f] for f in FIELDS[:4])
    b['tokens'] += total; b['requests'] += 1; b['sessions'].add(r['provider'] + ':' + r['session'])
    if value is None: b['unpricedTokens'] += total
    else: b['value'] += value; b['cacheSavings'] += savings or 0


def finish(b):
    return b | {'sessions': len(b['sessions'])}


def go_quota(force=False):
    path = STATE / 'go-quota.json'
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        auth_path = Path(os.getenv('XDG_DATA_HOME', HOME / '.local/share')) / 'opencode/auth.json'
        auth = json.loads(auth_path.read_text()).get('opencode-go', {})
        key = auth.get('key') if auth.get('type') == 'api' else None
        if not key: raise ValueError('Connect OpenCode Go in OpenCode to read quota.')
        request = urllib.request.Request('https://opencode.ai/zen/go/v1/usage',
                    headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'Omarchy-AI-Usage/0.1'})
        with urllib.request.urlopen(request, timeout=12) as response: data = json.load(response)
        usage = data.get('usage')
        if not isinstance(usage, dict): raise ValueError('Go returned an unrecognized usage response.')
        windows = []
        for name, label in [('rolling', 'Session (5-hour)'), ('weekly', 'Weekly (7-day)'), ('monthly', 'Monthly')]:
            w = usage.get(name)
            if not isinstance(w, dict) or not isinstance(w.get('percent'), (float, int)): continue
            reset = w.get('resetsAt')
            if isinstance(reset, (float, int)): reset = dt.datetime.fromtimestamp(timestamp(reset), dt.timezone.utc).isoformat()
            windows.append({'label': label, 'percent': w['percent'] / 100, 'resetsAt': reset or ''})
        if not windows: raise ValueError('Go returned no recognized quota windows.')
        cached = {'limits': windows, 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': ''}
    except Exception as exc:
        # Do not expose credential-bearing request objects or raw response bodies.
        cached['error'] = str(exc) if isinstance(exc, ValueError) else 'Go quota unavailable. Check the connection in OpenCode.'
    cached['attemptedAt'] = time.time()
    atomic_json(path, cached)
    return cached


def quota(provider):
    if provider == 'opencode-go':
        try: d = json.loads((STATE / 'go-quota.json').read_text())
        except (OSError, ValueError): d = {}
        return {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('error', '')}
    p = STATE.parent / 'agents/usage' / (provider + '.json')
    try:
        d = json.loads(p.read_text())
        return {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('usageStatusText', ''), 'plan': d.get('tierLabel', '')}
    except (OSError, ValueError): return {'limits': [], 'error': 'No quota snapshot yet.'}


def report(ledger, cfg, days=7, provider='all', now=None, selection=None):
    selection = selection or {}
    today = now or dt.datetime.now().astimezone()
    start_date = today.date() - dt.timedelta(days=days - 1)
    # Local calendar boundaries, including DST transitions.
    start = dt.datetime.combine(start_date, dt.time()).timestamp()
    previous_start = dt.datetime.combine(start_date - dt.timedelta(days=days), dt.time()).timestamp()
    end = today.timestamp()
    previous_end = (dt.datetime.combine(today.date() - dt.timedelta(days=1), today.time().replace(tzinfo=None)).timestamp()
                    if days == 1 else start)
    summary, previous = bucket(), bucket()
    providers = {p: bucket() for p in cfg['enabled']}
    daily = {str(start_date + dt.timedelta(days=n)): {p: bucket() for p in providers} for n in range(days)}
    hour_start, hour_end = start, end
    if selection.get('day'):
        selected_date = dt.date.fromisoformat(selection['day'])
        hour_start = dt.datetime.combine(selected_date, dt.time()).timestamp()
        hour_end = min(end, dt.datetime.combine(selected_date + dt.timedelta(days=1), dt.time()).timestamp() - 1)
    hourly = [{'start': ts, 'label': dt.datetime.fromtimestamp(ts).strftime('%H:%M'),
               'title': dt.datetime.fromtimestamp(ts).astimezone().strftime('%H:%M %Z') + ' to ' +
                        (dt.datetime.fromtimestamp(ts + 3600).astimezone().strftime('%H:%M %Z') if ts + 3600 <= hour_end + 1 else 'now'),
               'providers': {p: bucket() for p in providers}}
              for ts in range(int(hour_start), int(hour_end) + 1, 3600)] if days == 1 or selection.get('day') else []
    models, projects, clients, sessions = {}, {}, {}, {}
    heatmap = collections.Counter()
    unknown = set()
    rates = load_rates()
    ledger.db.row_factory = sqlite3.Row
    earliest = ledger.db.execute('SELECT MIN(ts) FROM events').fetchone()[0]
    for row in ledger.db.execute('SELECT * FROM events WHERE ts>=? AND ts<=?', (min(previous_start, end - 365 * 86400), end)):
        r = dict(row); p = r['provider']
        if p not in providers or (provider != 'all' and p != provider): continue
        day = str(dt.datetime.fromtimestamp(r['ts']).date())
        if any((r[key] or ('Unknown project' if key == 'project' else '')) != selection[key] for key in ('model', 'project', 'client') if selection.get(key)): continue
        if selection.get('day') and day != selection['day']: continue
        heatmap[day] += sum(r[f] for f in FIELDS[:4])
        if r['ts'] < previous_start: continue
        value, savings = price(r, rates['document'])
        if r['ts'] < start:
            if r['ts'] <= previous_end: add(previous, r, value, savings)
            continue
        add(summary, r, value, savings); add(providers[p], r, value, savings)
        if day in daily: add(daily[day][p], r, value, savings)
        if hourly:
            index = int((r['ts'] - hour_start) // 3600)
            if 0 <= index < len(hourly): add(hourly[index]['providers'][p], r, value, savings)
        if value is None: unknown.add(r['model'])
        for group, key in [(models, (p, r['model'])), (projects, (p, r['project'] or 'Unknown project')),
                           (clients, (p, r['client'])), (sessions, (p, r['session']))]:
            b = group.setdefault(key, bucket())
            add(b, r, value, savings)
            if group is sessions:
                b['project'] = r['project'] or 'Unknown project'
                b['client'] = r['client']
                b['firstAt'] = min(b.get('firstAt', r['ts']), r['ts'])
                b['lastAt'] = max(b.get('lastAt', r['ts']), r['ts'])
    def rows(group):
        return sorted([finish(v) | {'provider': p, 'name': name} for (p, name), v in group.items()], key=lambda x: x['tokens'], reverse=True)
    try: coverage = json.loads(ledger.db.execute("SELECT value FROM metadata WHERE key='scan'").fetchone()[0])
    except (TypeError, ValueError): coverage = {}
    inventory = [dict(r) for r in ledger.db.execute(
        'SELECT provider,client,COUNT(DISTINCT session) AS sessions,MIN(ts) AS firstAt,MAX(ts) AS lastAt FROM events GROUP BY provider,client')]
    coverage['clients'] = inventory
    coverage['additionalHomes'] = len(cfg['codexHomes']) + len(cfg['claudeHomes'])
    return {'selection': selection, 'generatedAt': time.time(), 'period': {'days': days, 'start': str(start_date), 'end': str(today.date())},
            'summary': finish(summary), 'previous': finish(previous),
            'providers': [finish(b) | {'id': p, 'name': PROVIDERS[p], 'quota': quota(p), 'monthlyPrice': cfg['monthlyPrices'].get(p)}
                          for p, b in providers.items() if provider == 'all' or p == provider],
            'daily': [{'date': day, 'providers': {p: finish(b) for p, b in values.items()}} for day, values in daily.items()],
            'hourly': [h | {'providers': {p: finish(b) for p, b in h['providers'].items()}} for h in hourly],
            'models': rows(models), 'projects': rows(projects), 'clients': rows(clients), 'sessions': rows(sessions),
            'heatmap': dict(heatmap), 'unknownModels': sorted(unknown), 'coverage': coverage | {'earliest': earliest},
            'pricing': {'source': rates['source'], 'fetchedAtMs': rates.get('fetchedAtMs'),
                        'coveragePercent': 100 * (1 - summary['unpricedTokens']/summary['tokens']) if summary['tokens'] else None,
                        'unpriced': [r for r in rows(models) if r['unpricedTokens']]}, 'settings': cfg, 'theme': theme()}


def write_go_record(ledger):
    cfg = DEFAULTS | {'enabled': ['opencode-go']}
    data = report(ledger, cfg, days=7)
    summary = data['summary']; q = quota('opencode-go')
    today = str(dt.date.today())
    today_data = next((x['providers']['opencode-go'] for x in data['daily'] if x['date'] == today), finish(bucket()))
    record_data = {'schemaVersion': 1, 'id': 'opencode-go', 'name': 'OpenCode Go',
       'updatedAt': q.get('updatedAt'), 'ready': bool(q.get('limits') or summary['tokens']), 'hasLocalStats': True,
       'hasPromptStats': False, 'tierLabel': 'Go', 'limits': q.get('limits', []), 'usageStatusText': q.get('error', ''),
       'todayTotalTokens': today_data['tokens'], 'todayPrompts': today_data['requests'], 'todaySessions': today_data['sessions'],
       'totalPrompts': summary['requests'], 'totalSessions': summary['sessions'],
       'recentDays': [{'date': x['date'], 'messageCount': x['providers']['opencode-go']['tokens']} for x in data['daily']],
       'modelUsage': {}}
    # Existing popup labels model totals as all-time. Supply the full ledger.
    for model, inp, out, read, write in ledger.db.execute('SELECT model,SUM(input),SUM(output),SUM(cacheRead),SUM(cacheWrite) FROM events WHERE provider=? GROUP BY model', ('opencode-go',)):
        record_data['modelUsage'][model] = {'inputTokens': inp, 'outputTokens': out, 'cacheReadInputTokens': read, 'cacheCreationInputTokens': write}
    atomic_json(STATE.parent / 'agents/usage/opencode-go.json', record_data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['report', 'scan', 'go', 'settings'])
    parser.add_argument('--days', type=int, choices=[1, 7, 30, 90, 365], default=7)
    parser.add_argument('--provider', choices=['all', *PROVIDERS], default='all')
    for field in ('model', 'project', 'client', 'day'): parser.add_argument('--' + field)
    parser.add_argument('--save'); parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.action == 'settings':
        print(json.dumps(save_settings(json.loads(args.save)) if args.save else settings())); return
    with (STATE / 'collector.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = Ledger(STATE / 'usage.sqlite'); cfg = settings()
        if args.action in ('scan', 'report'): ledger.scan(cfg)
        if args.action in ('go', 'scan'):
            go_quota(args.force)
            if args.action == 'go': ledger.scan(cfg)
            write_go_record(ledger)
        if args.action == 'report': print(json.dumps(report(ledger, cfg, args.days, args.provider, selection={k: getattr(args, k) for k in ('model', 'project', 'client', 'day') if getattr(args, k)})))
        elif args.action == 'scan': print(json.dumps({'ok': True, 'events': ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}))
        elif args.action == 'go': print(json.dumps({'ok': not bool(quota('opencode-go').get('error'))}))
        ledger.db.close()


if __name__ == '__main__':
    main()
