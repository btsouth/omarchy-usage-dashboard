#!/usr/bin/env python3
"""Local metrics only. Source transcripts are read, never modified or copied."""
import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import math
import json
import os
from pathlib import Path
import socket
import struct
import sqlite3
import sys
import tempfile
import time
import tomllib
import subprocess
import urllib.request
import urllib.parse
import uuid

HOME = Path.home()
STATE = Path(os.getenv('XDG_STATE_HOME', HOME / '.local/state')) / 'omarchy/ai-usage'
CONFIG = Path(os.getenv('XDG_CONFIG_HOME', HOME / '.config')) / 'omarchy/ai-usage/settings.json'
PROVIDERS = {'codex': 'Codex', 'claude': 'Claude', 'opencode-go': 'OpenCode Go', 'grok': 'Grok Build',
             'gemini': 'Gemini CLI', 'opencode': 'OpenCode', 'pi': 'Pi', 'omp': 'Oh My Pi', 'cursor': 'Cursor'}
HOME_KEYS = ('codexHomes', 'claudeHomes', 'grokHomes', 'geminiHomes', 'opencodeHomes', 'piHomes', 'ompHomes')
DEFAULTS = {'enabled': ['codex', 'claude', 'opencode-go'], 'monthlyPrices': {},
            **{key: [] for key in HOME_KEYS}, 'accounts': [], 'localAccountLabel': 'Local', 'windowOpacity': 0.985}
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
             'monthlyPrices': {}, **{key: [] for key in HOME_KEYS},
             'accounts': [], 'localAccountLabel': str(value.get('localAccountLabel') or 'Local').strip(),
             'windowOpacity': max(0.55, min(1.0, float(value.get('windowOpacity', 0.985))))}
    for provider, amount in value.get('monthlyPrices', {}).items():
        if provider in PROVIDERS and amount is not None and 0 <= float(amount) <= 100000:
            clean['monthlyPrices'][provider] = float(amount)
    for key in HOME_KEYS:
        clean[key] = sorted({str(Path(p).expanduser().absolute()) for p in value.get(key, []) if str(p).strip()})
    if not clean['localAccountLabel'] or len(clean['localAccountLabel']) > 80: raise ValueError('Give the local history group a name of 1 to 80 characters.')
    if clean['localAccountLabel'].casefold() in ('unassigned history', 'needs review'): raise ValueError('Choose a different local account name.')
    labels, ids, paths = {clean['localAccountLabel'].casefold(), 'unassigned history', 'needs review'}, {'local', 'unassigned', 'conflict'}, {}
    for account in value.get('accounts', []):
        label = str(account.get('label') or '').strip()
        aid = str(account.get('id') or uuid.uuid4())
        if not label or len(label) > 80: raise ValueError('Give each account a name of 1 to 80 characters.')
        if label.casefold() in labels or aid in ids: raise ValueError('Account names must be unique.')
        labels.add(label.casefold()); ids.add(aid)
        directories = []
        for directory in account.get('directories', []):
            provider, raw = directory.get('provider'), str(directory.get('path') or '').strip()
            if provider not in PROVIDERS or not raw: raise ValueError('Choose a source and folder for every account directory.')
            if not Path(raw).expanduser().is_absolute(): raise ValueError('Use a full path for each account folder.')
            path = str(Path(raw).expanduser().absolute())
            key = (provider, str(Path(path).resolve()))
            if key in paths and paths[key] != aid: raise ValueError('The same source folder cannot belong to two accounts.')
            paths[key] = aid
            if {'provider': provider, 'path': path} not in directories: directories.append({'provider': provider, 'path': path})
        if not directories: raise ValueError('Add at least one source folder to each account.')
        clean['accounts'].append({'id': aid, 'label': label, 'directories': directories})
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


def grok_records(path):
    try: summary = json.loads(path.with_name('summary.json').read_text())
    except (OSError, ValueError): summary = {}
    if not isinstance(summary, dict): summary = {}
    project = (summary.get('info') or {}).get('cwd') or urllib.parse.unquote(path.parent.parent.name)
    with path.open(errors='replace') as f:
        for raw in f:
            if 'turn_completed' not in raw: continue
            try: event = json.loads(raw)
            except ValueError: continue
            if not isinstance(event, dict): continue
            params = event.get('params') or {}
            if not isinstance(params, dict): continue
            update = params.get('update') or {}
            if not isinstance(update, dict): continue
            if update.get('sessionUpdate') != 'turn_completed': continue
            usage = update.get('usage')
            # Partial subagent totals cannot supply an input/output split.
            if not isinstance(usage, dict): continue
            meta = params.get('_meta') or {}
            session = str(params.get('sessionId') or path.parent.name)
            ts = meta.get('agentTimestampMs') or event.get('timestamp')
            # Event IDs survive copied/forked history, even when session IDs change.
            key = meta.get('eventId') or digest(session, update.get('prompt_id') or [ts, usage])
            models = usage.get('modelUsage') or {
                (update.get('_meta') or {}).get('modelId') or summary.get('current_model_id') or 'unknown': usage}
            if not isinstance(models, dict): continue
            for model, counts in models.items():
                if not isinstance(counts, dict): continue
                read, write = number(counts.get('cachedReadTokens')), number(counts.get('cacheCreationTokens'))
                r = record(digest('grok', key, model), 'grok', session, ts, model, project, 'Grok Build',
                           input=max(0, number(counts.get('inputTokens')) - read - write),
                           output=counts.get('outputTokens'), cacheRead=read, cacheWrite=write,
                           reasoning=counts.get('reasoningTokens'))
                ticks = counts.get('costUsdTicks', usage.get('costUsdTicks') if len(models) == 1 else None)
                r['reportedCostTicks'] = number(ticks) or None
                r['modelCalls'] = number(counts.get('modelCalls', usage.get('modelCalls') if len(models) == 1 else None))
                yield r


def json_records(path):
    with path.open(errors='replace') as stream:
        if path.suffix == '.json':
            value = json.load(stream)
            if isinstance(value, dict): yield value
        else:
            for line in stream:
                try: value = json.loads(line)
                except ValueError: continue
                if isinstance(value, dict): yield value


def gemini_records(path):
    session, project = path.stem, ''
    # The CLI may migrate a JSON conversation to an append-only JSONL file.
    # Stable message IDs deduplicate both copies. Rewind records do not erase
    # tokens already spent; repeated message updates merge in the ledger.
    for entry in json_records(path):
        metadata = entry.get('$set', entry)
        session = str(metadata.get('sessionId') or session)
        project = metadata.get('projectHash') or project
        messages = metadata.get('messages', [entry])
        for message in messages:
            if not isinstance(message, dict) or message.get('type') != 'gemini': continue
            usage = message.get('tokens')
            if not isinstance(usage, dict): continue
            inp, out = number(usage.get('input')), number(usage.get('output'))
            read, thoughts = number(usage.get('cached')), number(usage.get('thoughts'))
            tool = number(usage.get('tool'))
            # Only add tool-prompt tokens when the source total confirms they
            # are outside input. Never add a subset twice.
            if number(usage.get('total')) == inp + out + thoughts + tool: inp += tool
            key = message.get('id') or digest(session, message.get('timestamp'), usage)
            yield record(digest('gemini', key), 'gemini', session, message.get('timestamp'),
                         message.get('model'), 'Gemini project ' + project if project else '', 'Gemini CLI',
                         input=max(0, inp - read), output=out + thoughts, cacheRead=read, reasoning=thoughts)


CURSOR_API = 'https://api2.cursor.sh/aiserver.v1.DashboardService/'


class CursorApiUnavailable(ValueError):
    """A credential-free message suitable for display."""


def cursor_token():
    # The desktop app stores its session in ItemTable; the value is used
    # verbatim as a Bearer token and is never logged, printed, or persisted.
    database = Path(os.getenv('CURSOR_HOME', str(HOME / '.config/Cursor'))) / 'User/globalStorage/state.vscdb'
    if not database.exists(): return None
    conn = None
    try:
        conn = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
        row = conn.execute("SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'").fetchone()
        token = row[0] if row else None
        return token if isinstance(token, str) and token else None
    except (sqlite3.Error, ValueError, TypeError, AttributeError, OSError): return None
    finally:
        if conn is not None: conn.close()


def cursor_api_error(exc):
    if getattr(exc, 'code', None) == 401:
        return 'Cursor sign-in expired. Sign in to Cursor desktop to refresh usage.'
    return 'Cursor cloud usage unavailable. Check the connection and try again.'


def cursor_post(token, method, body):
    request = urllib.request.Request(CURSOR_API + method, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                 'User-Agent': 'Omarchy-AI-Usage/0.1'})
    with urllib.request.urlopen(request, timeout=15) as response: data = json.load(response)
    if not isinstance(data, dict): raise CursorApiUnavailable('Cursor returned an unrecognized usage response.')
    return data


def cursor_summary(token):
    try: return cursor_post(token, 'GetCurrentPeriodUsage', {})
    except CursorApiUnavailable: raise
    except Exception as exc: raise CursorApiUnavailable(cursor_api_error(exc))


def event_ms(value):
    # The API carries millisecond timestamps as digit strings; timestamp()
    # only folds int/float milliseconds, so coerce strings first.
    text = str(value).strip()
    try: return int(text)
    except (ValueError, TypeError): pass
    try: return int(float(text))
    except (ValueError, TypeError): pass
    return int(timestamp(value) * 1000)


def cursor_api_record(ev):
    if not isinstance(ev, dict): ev = {}
    ms = event_ms(ev.get('timestamp'))
    usage = ev.get('tokenUsage')
    if not isinstance(usage, dict): usage = {}
    conv = ev.get('conversationId') or 'cloud'
    model = str(ev.get('model') or 'unknown')
    try: cents = float(usage.get('totalCents') or 0)
    except (ValueError, TypeError): cents = 0
    if not math.isfinite(cents): cents = 0
    r = record(digest('cursor-api', ms, model, conv, number(usage.get('inputTokens')),
                      number(usage.get('outputTokens')), number(usage.get('cacheReadTokens'))),
               'cursor', conv, ms // 1000, model, '',
               'Cloud' if ev.get('isHeadless') else 'Cursor',
               input=usage.get('inputTokens'), output=usage.get('outputTokens'),
               cacheRead=usage.get('cacheReadTokens'))
    r['turns'] = 1
    # A literal float: reported_value() maps 0 to None, which would unprice
    # free rows. Non-chargeable events already carry 0 cents.
    r['reportedValue'] = max(0.0, cents / 100)
    return r


def cursor_fetch_page(token, body):
    try: return cursor_post(token, 'GetFilteredUsageEvents', body)
    except Exception as exc:
        if getattr(exc, 'code', None) == 400 and body.get('pageSize', 0) > 100:
            return cursor_post(token, 'GetFilteredUsageEvents', dict(body, pageSize=100))
        raise


def cursor_walk(token, end=None, stop_ms=0, cycle_ms=0):
    """Buffered walk newest-first. Returns (records, oldest_ms, complete).
    Nothing is written here; the caller commits. Raises CursorApiUnavailable."""
    records, seen, oldest, complete = [], set(), None, False
    try:
        for _ in range(40):
            body = {'pageSize': 1000}
            if end is not None: body['endDate'] = end
            try: data = cursor_fetch_page(token, body)
            except CursorApiUnavailable: raise
            except Exception as exc: raise CursorApiUnavailable(cursor_api_error(exc))
            page = data.get('usageEventsDisplay')
            if not isinstance(page, list): raise CursorApiUnavailable('Cursor returned an unrecognized usage response.')
            fresh = 0
            for ev in page:
                r = cursor_api_record(ev)
                if r['id'] in seen: continue
                seen.add(r['id']); records.append(r); fresh += 1
            stamps = [event_ms(e.get('timestamp')) for e in page if isinstance(e, dict)]
            stamps = [s for s in stamps if s > 0]
            if not stamps: complete = True; break
            oldest = min(stamps)
            if len(page) < 1000 or not fresh: complete = True; break
            if stop_ms and oldest <= stop_ms: complete = True; break
            if cycle_ms and oldest < cycle_ms: complete = True; break
            if oldest < (time.time() - 400 * 86400) * 1000: complete = True; break
            end = oldest - 1
        else: return records, oldest, False
    except CursorApiUnavailable: raise
    except Exception as exc: raise CursorApiUnavailable(cursor_api_error(exc))
    return records, oldest, complete


def purge_legacy_cursor(ledger):
    # Branch-era local rows are identified by provenance, never by shape, so
    # no API row can match. Both tables stay consistent (see put()).
    ledger.db.execute("DELETE FROM events WHERE provider='cursor' AND id IN "
                      "(SELECT event_id FROM event_sources WHERE path LIKE '%state.vscdb')")
    ledger.db.execute("DELETE FROM event_sources WHERE path LIKE '%state.vscdb' "
                      "AND event_id NOT IN (SELECT id FROM events)")


def cursor_usage(ledger, force=False):
    source = {'provider': 'cursor', 'path': 'cursor-api', 'files': 0, 'exists': True, 'kind': 'cloud'}
    path = STATE / 'cursor-usage.json'
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and time.time() - cached.get('attemptedAt', 0) < 300: return source, []
    token = cursor_token()
    if token is None:
        source['readErrors'] = 1
        return source, ['Cursor desktop sign-in not found. Sign in to Cursor to collect cloud usage.']
    try:
        cycle_ms = event_ms(cursor_summary(token).get('billingCycleStart'))
        stop_ms, end = 0, None
        if cached.get('billingCycleStart') == cycle_ms and isinstance(cached.get('newestTs'), int):
            stop_ms = cached['newestTs']
            if cached.get('fullPullPending') and isinstance(cached.get('resumeFloorMs'), int):
                end = cached['resumeFloorMs'] - 1
        if end is None:
            records, oldest, complete = cursor_walk(token, end, stop_ms, cycle_ms)
        else:
            # Resuming history below the floor: the incremental stop would
            # fire on the first older page, so the resume walk runs
            # stop-free to a natural stop. A top-up walk first captures
            # arrivals newer than the cached newest marker.
            top, _, top_complete = cursor_walk(token, None, stop_ms, cycle_ms)
            res, oldest, res_complete = cursor_walk(token, end, 0, cycle_ms)
            records, complete = top + res, top_complete and res_complete
        newest = max([cached.get('newestTs') or 0] + [r['ts'] * 1000 for r in records])
        if not complete:
            for r in records: ledger.put(r, 'cursor-api')
            cached = {'attemptedAt': time.time(), 'billingCycleStart': cycle_ms,
                      'newestTs': newest, 'fullPullPending': True,
                      'resumeFloorMs': oldest or 0, 'error': ''}
            atomic_json(path, cached)
            source['readErrors'] = 1
            return source, ['Cursor cloud history is incomplete; the next refresh resumes where this one stopped.']
        for r in records: ledger.put(r, 'cursor-api')
        if records: purge_legacy_cursor(ledger)
        cached = {'attemptedAt': time.time(), 'billingCycleStart': cycle_ms,
                  'newestTs': newest, 'fullPullPending': False,
                  'resumeFloorMs': 0, 'error': ''}
        atomic_json(path, cached)
        return source, []
    except Exception as exc:
        cached['attemptedAt'] = time.time()
        cached['error'] = str(exc) if isinstance(exc, CursorApiUnavailable) else 'Cursor cloud usage unavailable. Check the connection and try again.'
        atomic_json(path, cached)
        source['readErrors'] = 1
        return source, ['Cursor cloud usage unavailable; showing previous records.']


def reported_value(value):
    try:
        amount = float(value)
        return amount if math.isfinite(amount) and amount > 0 else None
    except (TypeError, ValueError): return None


def pi_records(path, provider='pi'):
    session, project = path.stem, ''
    for entry in json_records(path):
        if entry.get('type') == 'session':
            session, project = str(entry.get('id') or session), entry.get('cwd') or project
            continue
        message = entry.get('message') or {}
        if entry.get('type') != 'message' or message.get('role') != 'assistant': continue
        usage = message.get('usage')
        if not isinstance(usage, dict): continue
        ts = message.get('timestamp') or entry.get('timestamp')
        model = message.get('model') or 'unknown'
        # Pi forks retain short entry IDs and original timestamps. Include both
        # so copies merge without collisions between unrelated sessions.
        key = digest(provider, entry.get('id'), ts, model) if entry.get('id') else digest(provider, session, ts, usage)
        r = record(key, provider, session, ts, model, project, PROVIDERS[provider],
                   input=usage.get('input'), output=usage.get('output'), cacheRead=usage.get('cacheRead'),
                   cacheWrite=usage.get('cacheWrite'), cacheWrite1h=usage.get('cacheWrite1h'), reasoning=usage.get('reasoning'))
        r['apiProvider'] = str(message.get('provider') or '')
        r['reportedValue'] = reported_value((usage.get('cost') or {}).get('total'))
        yield r


def opencode_record(mid, sid, ts, project, model, route, usage, cost):
    provider = 'opencode-go' if route == 'opencode-go' else 'opencode'
    cache = usage.get('cache') or {}
    r = record(digest(provider, mid), provider, sid, ts, model, project, 'OpenCode',
               input=usage.get('input'), output=number(usage.get('output')) + number(usage.get('reasoning')),
               reasoning=usage.get('reasoning'), cacheRead=cache.get('read'), cacheWrite=cache.get('write'))
    r['apiProvider'] = str(route or 'unknown')
    r['reportedValue'] = reported_value(cost) if provider == 'opencode' else None
    return r


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
          CREATE TABLE IF NOT EXISTS event_sources(event_id TEXT, path TEXT, PRIMARY KEY(event_id,path));
        ''')
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='provenanceVersion'").fetchone():
            # Re-index source locations once, without deleting retained metrics.
            self.db.execute('DELETE FROM files')
            self.db.execute("INSERT INTO metadata VALUES ('provenanceVersion','1')")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(events)')}
        for name in ('reportedCostTicks', 'modelCalls', 'turns'):
            if name not in columns: self.db.execute(f'ALTER TABLE events ADD COLUMN {name} INTEGER')
        for name, kind in [('reportedValue', 'REAL'), ('apiProvider', 'TEXT')]:
            if name not in columns: self.db.execute(f'ALTER TABLE events ADD COLUMN {name} {kind}')

    def put(self, r, source=None):
        if not r['ts'] or (not sum(r[f] for f in FIELDS[:4]) and not r.get('turns')): return
        keys = list(r)
        # Claude streams may repeat a message with a larger final usage count.
        update = ','.join(f'{f}=MAX(events.{f},excluded.{f})' for f in FIELDS)
        update += ',reportedCostTicks=COALESCE(MAX(COALESCE(events.reportedCostTicks,0),excluded.reportedCostTicks),events.reportedCostTicks)'
        update += ',modelCalls=MAX(COALESCE(events.modelCalls,0),COALESCE(excluded.modelCalls,0))'
        update += ',turns=MAX(COALESCE(events.turns,0),COALESCE(excluded.turns,0))'
        update += ',reportedValue=COALESCE(MAX(COALESCE(events.reportedValue,0),excluded.reportedValue),events.reportedValue)'
        update += ',apiProvider=COALESCE(excluded.apiProvider,events.apiProvider)'
        self.db.execute(f'INSERT INTO events ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)}) '
                        f'ON CONFLICT(id) DO UPDATE SET {update}', list(r.values()))
        if source is not None:
            self.db.execute('INSERT OR IGNORE INTO event_sources VALUES (?,?)', (r['id'], str(source)))

    def scan(self, cfg):
        cfg = dict(cfg)
        for account in cfg.get('accounts', []):
            for directory in account['directories']:
                key = ('opencode' if directory['provider'] == 'opencode-go' else directory['provider']) + 'Homes'
                cfg[key] = cfg.get(key, []) + [directory['path']]
        warnings, sources = [], []
        for provider, roots, parser in (
            ('codex', [os.getenv('CODEX_HOME', str(HOME / '.codex'))] + cfg['codexHomes'], codex_records),
            ('claude', [os.getenv('CLAUDE_CONFIG_DIR', str(HOME / '.claude'))] + cfg['claudeHomes'], claude_records),
            ('grok', [os.getenv('GROK_HOME', str(HOME / '.grok'))] + cfg.get('grokHomes', []), grok_records),
            ('gemini', [str(HOME / '.gemini')] + cfg.get('geminiHomes', []), gemini_records),
            ('pi', [os.getenv('PI_CODING_AGENT_DIR', str(HOME / '.pi/agent'))] + cfg.get('piHomes', []), pi_records),
            ('omp', [str(HOME / '.omp/agent')] + cfg.get('ompHomes', []), lambda path: pi_records(path, 'omp'))):
            for root in sorted(set(roots)):
                root = Path(root).expanduser()
                folders = [root / 'sessions', root / 'archived_sessions'] if provider == 'codex' else [root / {'claude': 'projects', 'gemini': 'tmp'}.get(provider, 'sessions')]
                for folder in folders:
                    pattern = 'updates.jsonl' if provider == 'grok' else '*.json*' if provider == 'gemini' else '*.jsonl'
                    files = sorted(folder.rglob(pattern)) if folder.exists() else []
                    if provider == 'gemini': files = [p for p in files if p.suffix in ('.json', '.jsonl') and 'chats' in p.relative_to(folder).parts[:-1]]
                    source = {'provider': provider, 'path': str(folder), 'files': len(files), 'exists': folder.exists(),
                              'latestFileAt': None, 'readErrors': 0, 'kind': 'archive' if folder.name == 'archived_sessions' else 'history'}
                    sources.append(source)
                    for p in files:
                        try:
                            stat = p.stat()
                            source['latestFileAt'] = max(source['latestFileAt'] or 0, stat.st_mtime)
                            old = self.db.execute('SELECT size,mtime FROM files WHERE path=?', (str(p),)).fetchone()
                            if old == (stat.st_size, stat.st_mtime_ns): continue
                            for r in parser(p): self.put(r, p.resolve())
                            self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)', (str(p), stat.st_size, stat.st_mtime_ns))
                        except (OSError, ValueError, TypeError, AttributeError):
                            source['readErrors'] += 1
                            warnings.append(f'Could not read {p.name}')
        opencode_roots = [str(Path(os.getenv('XDG_DATA_HOME', HOME / '.local/share')) / 'opencode')] + cfg.get('opencodeHomes', [])
        for root in sorted(set(opencode_roots)):
            root = Path(root).expanduser()
            opencode = root / 'opencode.db'
            source = {'provider': 'opencode', 'path': str(opencode), 'files': int(opencode.exists()), 'exists': opencode.exists(), 'kind': 'database'}
            sources.append(source)
            if opencode.exists():
                conn = None
                try:
                    conn = sqlite3.connect(opencode.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
                    # Metric fields only. Go has its own card and stable IDs;
                    # other routes stay under OpenCode and never enter Go totals.
                    query = """SELECT m.id,m.session_id,m.time_created,s.directory,
                      json_extract(m.data,'$.modelID'),json_extract(m.data,'$.providerID'),
                      json_extract(m.data,'$.tokens'),json_extract(m.data,'$.cost')
                      FROM message m LEFT JOIN session s ON s.id=m.session_id
                      WHERE json_extract(m.data,'$.role')='assistant' """
                    for mid, sid, ts, project, model, route, raw, cost in conn.execute(query):
                        self.put(opencode_record(mid, sid, ts, project, model, route, json.loads(raw or '{}'), cost), opencode.resolve())
                except (sqlite3.Error, ValueError, TypeError, AttributeError):
                    source['readErrors'] = 1
                    warnings.append('OpenCode database could not be read; retained previous records.')
                finally:
                    if conn is not None: conn.close()
            legacy = root / 'storage/message'
            if legacy.exists():
                files = sorted(legacy.rglob('*.json'))
                source = {'provider': 'opencode', 'path': str(legacy), 'files': len(files), 'exists': True, 'kind': 'legacy history'}
                sources.append(source)
                for path in files:
                    try:
                        for item in json_records(path):
                            if item.get('role') != 'assistant': continue
                            self.put(opencode_record(item.get('id') or path.stem, item.get('sessionID') or path.parent.name,
                                (item.get('time') or {}).get('created'), (item.get('path') or {}).get('cwd'),
                                item.get('modelID'), item.get('providerID'), item.get('tokens') or {}, item.get('cost')), path.resolve())
                    except (OSError, ValueError, TypeError, AttributeError):
                        source['readErrors'] = source.get('readErrors', 0) + 1
        if 'cursor' in cfg.get('enabled', []):
            csource, cwarnings = cursor_usage(self)
            sources.append(csource)
            warnings.extend(cwarnings)
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
    # Preserve user rates while filling newly supported models from the bundle.
    if path.exists():
        try:
            bundled = json.loads(Path(__file__).with_name('catalog.json').read_text())
            data['document'] = bundled['document'] | data['document']
            data['source'] += ' + bundled models'
        except (OSError, ValueError, TypeError, KeyError): pass
    try:
        official = json.loads(Path(__file__).with_name('pricing.json').read_text())
        data['document'].update(official['models'])
        data['source'] += ' + OpenCode Go official rates (' + official['verifiedAt'] + ')'
    except (OSError, ValueError): pass
    return data


def price(r, catalog):
    if r['provider'] == 'grok':
        ticks = r.get('reportedCostTicks')
        return (ticks / 10_000_000_000, None) if ticks else (None, None)
    if r['provider'] in ('opencode', 'pi', 'omp', 'cursor') and r.get('reportedValue') is not None:
        return r['reportedValue'], None
    model = r['model']
    rate = catalog.get((r.get('apiProvider') or r['provider']) + '/' + model) or catalog.get(model) or catalog.get('anthropic/' + model) or catalog.get('openai/' + model) or catalog.get('gemini/' + model)
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
            'unpricedTokens': 0, 'requests': 0, 'modelCalls': 0, 'turns': 0, 'sessions': set()}


def add(b, r, value, savings):
    for f in FIELDS: b[f] += r[f]
    total = sum(r[f] for f in FIELDS[:4])
    b['tokens'] += total; b['requests'] += 1; b['sessions'].add(r['provider'] + ':' + r['session'])
    b['modelCalls'] += r.get('modelCalls') or 0
    b['turns'] += r.get('turns') or 0
    if value is None: b['unpricedTokens'] += total
    else: b['value'] += value; b['cacheSavings'] += savings or 0


def finish(b):
    return b | {'sessions': len(b['sessions']), 'tokensPerSession': b['tokens'] / len(b['sessions']) if b['sessions'] else None,
                'valuePerSession': b['value'] / len(b['sessions']) if b['sessions'] else None}


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
    if provider in ('opencode', 'pi', 'omp'):
        return {'limits': [], 'error': 'Account limits belong to the underlying provider and are not collected here.'}
    if provider in ('opencode-go', 'grok', 'cursor'):
        names = {'opencode-go': 'go-quota.json', 'grok': 'grok-quota.json', 'cursor': 'cursor-quota.json'}
        try: d = json.loads((STATE / names[provider]).read_text())
        except (OSError, ValueError): d = {}
        return {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('error', '')}
    p = STATE.parent / 'agents/usage' / (provider + '.json')
    try:
        d = json.loads(p.read_text())
        return {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('usageStatusText', ''), 'plan': d.get('tierLabel', '')}
    except (OSError, ValueError): return {'limits': [], 'error': 'Local token history only. No account quota snapshot is available.'}


class QuotaUnavailable(ValueError):
    """A credential-free message suitable for display."""


def proto_fields(data):
    index = 0
    def varint():
        nonlocal index
        value = 0
        for shift in range(0, 70, 7):
            if index >= len(data): raise ValueError('Truncated quota response.')
            byte = data[index]; index += 1
            value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError('Invalid quota response.')
    while index < len(data):
        key = varint(); wire = key & 7
        if not key >> 3: raise ValueError('Invalid quota field.')
        if wire == 0: value = varint()
        else:
            length = varint() if wire == 2 else {1: 8, 5: 4}.get(wire)
            if length is None or index + length > len(data): raise ValueError('Invalid quota field.')
            value = data[index:index + length]; index += length
        yield key >> 3, wire, value


def grok_billing(data):
    index, config, percent, reset = 0, False, 0.0, None
    while index < len(data):
        if index + 5 > len(data): raise ValueError('Truncated Grok quota response.')
        flag = data[index]; length = int.from_bytes(data[index + 1:index + 5], 'big'); index += 5
        payload = data[index:index + length]; index += length
        if len(payload) != length: raise ValueError('Truncated Grok quota response.')
        if flag == 128:
            for line in payload.decode('ascii').splitlines():
                name, _, value = line.partition(':')
                if name.strip().lower() == 'grpc-status' and value.strip() != '0':
                    raise QuotaUnavailable('Grok quota unavailable. Run grok login if the session expired.')
        elif flag == 0:
            for n, wire, body in proto_fields(payload):
                if n != 1 or wire != 2: continue
                config = True
                for field, kind, value in proto_fields(body):
                    if field == 1 and kind == 5: percent = struct.unpack('<f', value)[0]
                    if field == 5 and kind == 2:
                        reset = next((v for n, w, v in proto_fields(value) if n == 1 and w == 0), None)
        else: raise ValueError('Unsupported Grok quota response.')
    if not config or not 0 <= percent < float('inf'): raise ValueError('Unrecognized Grok quota response.')
    limit = {'label': 'Weekly', 'percent': percent / 100}
    if reset: limit['resetsAt'] = dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat()
    return [limit]


def grok_quota(force=False):
    path = STATE / 'grok-quota.json'
    auth_path = Path(os.getenv('GROK_HOME', HOME / '.grok')) / 'auth.json'
    try:
        stat = auth_path.stat()
        auth_version = [stat.st_mtime_ns, stat.st_size]
    except OSError: auth_version = None
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and cached.get('authVersion') == auth_version and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        auth = json.loads(auth_path.read_text())
        entry = next((v for v in auth.values() if isinstance(v, dict) and v.get('key')), {})
        if not entry: raise QuotaUnavailable('Run grok login to read Grok quota.')
        expires = timestamp(entry.get('expires_at'))
        if expires and expires <= time.time(): raise QuotaUnavailable('Grok sign-in expired. Run grok login to refresh quota.')
        request = urllib.request.Request('https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig',
            data=bytes(5), headers={'Authorization': 'Bearer ' + entry['key'],
                'Content-Type': 'application/grpc-web+proto', 'x-grpc-web': '1',
                'Origin': 'https://grok.com', 'User-Agent': 'Omarchy-AI-Usage/0.1'})
        with urllib.request.urlopen(request, timeout=12) as response: limits = grok_billing(response.read())
        cached = {'limits': limits, 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': ''}
    except Exception as exc:
        cached['error'] = str(exc) if isinstance(exc, QuotaUnavailable) else 'Grok quota unavailable. Check your Grok login.'
    cached['attemptedAt'] = time.time()
    cached['authVersion'] = auth_version
    atomic_json(path, cached)
    return cached


def cursor_quota(force=False):
    path = STATE / 'cursor-quota.json'
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        token = cursor_token()
        if token is None: raise CursorApiUnavailable('Sign in to Cursor desktop to read Cursor quota.')
        summary = cursor_summary(token)
        plan = summary.get('planUsage')
        if not isinstance(plan, dict): raise CursorApiUnavailable('Cursor returned an unrecognized quota response.')
        percent = plan.get('totalPercentUsed')
        if not isinstance(percent, (int, float)): raise CursorApiUnavailable('Cursor returned an unrecognized quota response.')
        end = event_ms(summary.get('billingCycleEnd'))
        resets = dt.datetime.fromtimestamp(end // 1000, dt.timezone.utc).isoformat() if end > 0 else ''
        cached = {'limits': [{'label': 'Billing cycle', 'percent': percent / 100, 'resetsAt': resets}],
                  'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'error': str(summary.get('displayMessage') or '')}
    except Exception as exc:
        cached['error'] = str(exc) if isinstance(exc, CursorApiUnavailable) else 'Cursor quota unavailable. Check your Cursor sign-in.'
    cached['attemptedAt'] = time.time()
    atomic_json(path, cached)
    return cached


def account_assignments(ledger, cfg):
    labels = {'local': cfg.get('localAccountLabel', 'Local'), 'unassigned': 'Unassigned history', 'conflict': 'Needs review'}
    roots = []
    for account in cfg.get('accounts', []):
        labels[account['id']] = account['label']
        for directory in account['directories']:
            roots.append((directory['provider'], Path(directory['path']).expanduser().resolve(), account['id']))
    roots.sort(key=lambda item: len(item[1].parts), reverse=True)
    cache, assignments = {}, {}
    for event, provider, path in ledger.db.execute('SELECT e.id,e.provider,s.path FROM events e JOIN event_sources s ON s.event_id=e.id'):
        key = (provider, path)
        if key not in cache:
            source = Path(path)
            cache[key] = next((aid for p, root, aid in roots if p == provider and source.is_relative_to(root)), 'local')
        assignments.setdefault(event, set()).add(cache[key])
    resolved = {}
    for event, ids in assignments.items():
        named = ids - {'local'}
        resolved[event] = 'conflict' if len(named) > 1 else next(iter(named)) if named else 'local'
    return labels, resolved


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
    models, projects, clients, sessions, routes, accounts = {}, {}, {}, {}, {}, {}
    labels, assignments = account_assignments(ledger, cfg)
    provider_accounts = {p: set() for p in providers}
    heatmap = collections.Counter()
    unknown = set()
    rates = load_rates()
    ledger.db.row_factory = sqlite3.Row
    earliest = ledger.db.execute('SELECT MIN(ts) FROM events').fetchone()[0]
    for row in ledger.db.execute('SELECT * FROM events WHERE ts>=? AND ts<=?', (min(previous_start, end - 365 * 86400), end)):
        r = dict(row); p = r['provider']
        account = assignments.get(r['id'], 'unassigned')
        if selection.get('account') and account != selection['account']: continue
        if p not in providers or (provider != 'all' and p != provider): continue
        day = str(dt.datetime.fromtimestamp(r['ts']).date())
        if any((r[key] or ('Unknown project' if key == 'project' else p if key == 'apiProvider' else '')) != selection[key] for key in ('model', 'project', 'client', 'apiProvider') if selection.get(key)): continue
        if selection.get('day') and day != selection['day']: continue
        heatmap[day] += sum(r[f] for f in FIELDS[:4])
        if r['ts'] < previous_start: continue
        value, savings = price(r, rates['document'])
        if r['ts'] < start:
            if r['ts'] <= previous_end: add(previous, r, value, savings)
            continue
        add(summary, r, value, savings); add(providers[p], r, value, savings)
        provider_accounts[p].add(account)
        if day in daily: add(daily[day][p], r, value, savings)
        if hourly:
            index = int((r['ts'] - hour_start) // 3600)
            if 0 <= index < len(hourly): add(hourly[index]['providers'][p], r, value, savings)
        if value is None: unknown.add(r['model'])
        for group, key in [(models, (p, r['model'])), (projects, (p, r['project'] or 'Unknown project')),
                           (clients, (p, r['client'])), (sessions, (p, r['session'])),
                           (routes, (p, r.get('apiProvider') or p)), (accounts, (p, account))]:
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
    has_unassigned = ledger.db.execute('SELECT 1 FROM events e WHERE NOT EXISTS (SELECT 1 FROM event_sources s WHERE s.event_id=e.id) LIMIT 1').fetchone() is not None
    account_options = [{'id': aid, 'label': label} for aid, label in labels.items()
                       if aid not in ('conflict', 'unassigned') or aid in assignments.values() or (aid == 'unassigned' and has_unassigned)]
    coverage['additionalHomes'] = sum(len(cfg.get(k, [])) for k in HOME_KEYS) + sum(len(a['directories']) for a in cfg.get('accounts', []))
    return {'selection': selection, 'generatedAt': time.time(), 'period': {'days': days, 'start': str(start_date), 'end': str(today.date())},
            'accountOptions': account_options,
            'accounts': [r | {'accountId': r['name'], 'name': labels[r['name']]} for r in rows(accounts)],
            'accountWarning': 'Copies of the same history belong to different accounts. Move mirrored folders into one account.' if any(a == 'conflict' for p, a in accounts) else '',
            'availableProviders': [{'id': p, 'name': name} for p, name in PROVIDERS.items()],
            'summary': finish(summary), 'previous': finish(previous),
            'providers': [finish(b) | {'id': p, 'name': PROVIDERS[p], 'quota': quota(p) if not selection.get('account') else {'limits': [], 'error': 'View All accounts for current-login quota. History labels do not identify credentials.'}, 'quotaScope': 'Current login on this PC',
                                      'valueShare': 100 * b['value'] / summary['value'] if summary['value'] else None,
                                      'monthlyPrice': cfg['monthlyPrices'].get(p) if provider_accounts[p] <= {'local'} else None}
                          for p, b in providers.items() if provider == 'all' or p == provider],
            'daily': [{'date': day, 'providers': {p: finish(b) for p, b in values.items()}} for day, values in daily.items()],
            'hourly': [h | {'providers': {p: finish(b) for p, b in h['providers'].items()}} for h in hourly],
            'routes': rows(routes), 'models': rows(models), 'projects': rows(projects), 'clients': rows(clients), 'sessions': rows(sessions),
            'heatmap': dict(heatmap), 'unknownModels': sorted(unknown), 'coverage': coverage | {'earliest': earliest},
            'pricing': {'source': rates['source'], 'fetchedAtMs': rates.get('fetchedAtMs'),
                        'coveragePercent': 100 * (1 - summary['unpricedTokens']/summary['tokens']) if summary['tokens'] else None,
                        'unpriced': [r for r in rows(models) if r['unpricedTokens']]}, 'settings': cfg, 'theme': theme()}


def write_agent_record(ledger, provider):
    cfg = DEFAULTS | {'enabled': [provider]}
    data = report(ledger, cfg, days=7)
    summary = data['summary']; q = quota(provider)
    total_records, total_sessions = ledger.db.execute(
        'SELECT COUNT(*),COUNT(DISTINCT session) FROM events WHERE provider=?', (provider,)).fetchone()
    active_dates = [r[0] for r in ledger.db.execute(
        "SELECT DISTINCT date(ts,'unixepoch','localtime') FROM events WHERE provider=? ORDER BY 1", (provider,))]
    today = str(dt.date.today())
    today_data = next((x['providers'][provider] for x in data['daily'] if x['date'] == today), finish(bucket()))
    record_data = {'schemaVersion': 1, 'id': provider, 'name': PROVIDERS[provider],
       'updatedAt': q.get('updatedAt'), 'ready': bool(q.get('limits') or total_records), 'hasLocalStats': True,
       'hasPromptStats': False, 'tierLabel': 'Go' if provider == 'opencode-go' else '', 'limits': q.get('limits', []), 'usageStatusText': q.get('error', ''),
       'todayTotalTokens': today_data['tokens'], 'todayPrompts': today_data['requests'], 'todaySessions': today_data['sessions'],
       'totalPrompts': total_records, 'totalSessions': total_sessions,
       'activeDays': len(active_dates), 'activeDates': active_dates,
       'recentDays': [{'date': x['date'], 'messageCount': x['providers'][provider]['tokens']} for x in data['daily']],
       'modelUsage': {}}
    # Existing popup labels model totals as all-time. Supply the full ledger.
    for model, inp, out, read, write in ledger.db.execute('SELECT model,SUM(input),SUM(output),SUM(cacheRead),SUM(cacheWrite) FROM events WHERE provider=? GROUP BY model', (provider,)):
        record_data['modelUsage'][model] = {'inputTokens': inp, 'outputTokens': out, 'cacheReadInputTokens': read, 'cacheCreationInputTokens': write}
    atomic_json(STATE.parent / ('agents/usage/' + provider + '.json'), record_data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['report', 'scan', 'go', 'settings'])
    parser.add_argument('--days', type=int, choices=[1, 7, 30, 90, 365], default=7)
    parser.add_argument('--provider', choices=['all', *PROVIDERS], default='all')
    for field in ('model', 'project', 'client', 'apiProvider', 'day', 'account'): parser.add_argument('--' + field)
    parser.add_argument('--save'); parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.action == 'settings':
        try: print(json.dumps(save_settings(json.loads(args.save)) if args.save else settings()))
        except (ValueError, TypeError, KeyError) as error:
            print(json.dumps({'error': str(error)})); raise SystemExit(1)
        return
    with (STATE / 'collector.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = Ledger(STATE / 'usage.sqlite'); cfg = settings()
        if not CONFIG.exists():
            # Auto-enable runs before the scan so sources with recorded
            # history are collected on a fresh install, as documented.
            found = {r[0] for r in ledger.db.execute('SELECT DISTINCT provider FROM events')}
            cfg = cfg | {'enabled': [p for p in PROVIDERS if p in found] or cfg['enabled']}
        if args.action in ('scan', 'report'): ledger.scan(cfg)
        if args.action in ('go', 'scan'):
            go_quota(args.force)
            if args.action == 'go': ledger.scan(cfg)
            write_agent_record(ledger, 'opencode-go')
            if args.action == 'scan' and 'grok' in cfg['enabled']:
                grok_quota(args.force)
                write_agent_record(ledger, 'grok')
            if args.action == 'scan' and 'cursor' in cfg['enabled']:
                cursor_quota(args.force)
            if args.action == 'scan':
                for p in ('gemini', 'opencode', 'pi', 'omp', 'cursor'):
                    if p in cfg['enabled']: write_agent_record(ledger, p)
        if args.action == 'report': print(json.dumps(report(ledger, cfg, args.days, args.provider, selection={k: getattr(args, k) for k in ('model', 'project', 'client', 'apiProvider', 'day', 'account') if getattr(args, k)})))
        elif args.action == 'scan': print(json.dumps({'ok': True, 'events': ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}))
        elif args.action == 'go': print(json.dumps({'ok': not bool(quota('opencode-go').get('error'))}))
        ledger.db.close()


if __name__ == '__main__':
    main()
