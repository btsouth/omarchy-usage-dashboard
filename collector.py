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
import select
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
             'gemini': 'Gemini CLI', 'opencode': 'OpenCode', 'pi': 'Pi', 'omp': 'Oh My Pi', 'muse': 'Muse',
             'ollama-cloud': 'Ollama Cloud', 'commandcode': 'CommandCode'}
HOME_KEYS = ('codexHomes', 'claudeHomes', 'grokHomes', 'geminiHomes', 'opencodeHomes', 'piHomes', 'ompHomes',
             'museHomes', 'hermesHomes')
# Provider ids used by the Hermes agent's own per-model usage table. Hermes
# bills the same routes this dashboard reads elsewhere, so its ledger is a
# source, not a separate provider. Two of these are the same product reached
# over different wire formats, so they map onto one dashboard provider: an
# account is not two accounts because one call used the Anthropic shape.
HERMES_ROUTES = ('opencode-go', 'ollama-cloud', 'commandcode', 'commandcode-anthropic')
HERMES_ROUTE_NAMES = {'commandcode-anthropic': 'commandcode'}
HERMES_TASKS = {'': 'conversation', 'title_generation': 'title generation', 'background_review': 'background review',
                'approval': 'approval', 'compression': 'context compression', 'vision': 'vision',
                'embedding': 'embedding'}
# Ollama Cloud reports quota as a fraction of each plan window. Legacy plans
# have session and weekly windows; credit plans have a monthly one.
OLLAMA_WINDOWS = {'session': 'Session (5-hour)', 'weekly': 'Weekly (7-day)', 'monthly': 'Monthly'}
# Stands in for a stored key in reports so the settings form can show that one
# exists without sending it back over the report channel. Never a valid key.
# One mask covers every provider's key field, since the form only needs to know
# that something is stored.
API_KEY_MASK = 'stored'
DEFAULTS = {'enabled': ['codex', 'claude', 'opencode-go'], 'monthlyPrices': {},
            **{key: [] for key in HOME_KEYS}, 'accounts': [], 'localAccountLabel': 'Local', 'windowOpacity': 0.985,
            'ledgerSyncDir': '', 'ledgerDeviceId': '', 'ollamaApiKey': '', 'commandcodeApiKey': ''}
FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'cacheWrite1h', 'reasoning')
# Bundled official rate tables merged over the catalog, in order. User
# rates.json entries still win over every file listed here.
OVERRIDES = (('pricing.json', 'OpenCode Go official rates'),
             ('muse-pricing.json', 'Muse official rates'),
             ('codex-pricing.json', 'Codex model rates'),
             ('ollama-pricing.json', 'Ollama Cloud model rates'),
             ('commandcode-pricing.json', 'CommandCode model rates'))


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


def masked_settings(cfg):
    """A copy of the settings safe to hand to the UI: any stored key is
    replaced by a mask. Derived from the key fields rather than written out at
    each call site, so a key added later cannot be echoed by omission."""
    return cfg | {field: (API_KEY_MASK if cfg.get(field) else '')
                  for field in ('ollamaApiKey', 'commandcodeApiKey')}


def stdin_payload(stream=None, timeout=5.0, idle=0.25):
    """The settings payload a client wrote to stdin, read without waiting for
    the end of input.

    A GUI client hands this process a live pipe and holds its write end open
    for as long as the process runs, so reading to the end of file blocks the
    save forever: no settings written, no exit, and a Save button that never
    comes back. Stop as soon as the bytes parse as JSON, and give up after
    `idle` seconds of silence, bounded by `timeout`, so a client that opens
    stdin and writes nothing still returns.
    """
    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or stream.isatty(): return None
    except (AttributeError, ValueError): return None
    deadline, chunks, wait = time.monotonic() + timeout, [], idle
    while True:
        wait = min(wait, deadline - time.monotonic())
        if wait <= 0: break
        try: ready = select.select([stream], [], [], wait)[0]
        except (OSError, ValueError): break
        if not ready: break
        try: chunk = os.read(stream.fileno(), 65536)
        except (AttributeError, OSError): break
        if not chunk: break  # The writer closed: that is the end of the payload.
        chunks.append(chunk)
        try: json.loads(b''.join(chunks))
        except (UnicodeDecodeError, ValueError): continue
        break
    return b''.join(chunks).decode('utf-8', 'replace').strip() or None


def save_settings(value):
    clean = {'enabled': [p for p in value.get('enabled', []) if p in PROVIDERS],
             'monthlyPrices': {}, **{key: [] for key in HOME_KEYS},
             'accounts': [], 'localAccountLabel': str(value.get('localAccountLabel') or 'Local').strip(),
             'ledgerSyncDir': '', 'ledgerDeviceId': str(value.get('ledgerDeviceId') or '').strip(),
             'ollamaApiKey': str(value.get('ollamaApiKey') or '').strip(),
             'commandcodeApiKey': str(value.get('commandcodeApiKey') or '').strip(),
             'windowOpacity': max(0.55, min(1.0, float(value.get('windowOpacity', 0.985))))}
    # The user's own key beats the environment and the key file, since typing
    # one in is deliberate. Settings stay nonsecret by default; these values
    # are the exception and are never echoed back over the settings channel.
    for field, mask, limit in (('ollamaApiKey', API_KEY_MASK, 200), ('commandcodeApiKey', API_KEY_MASK, 200)):
        if field not in value:
            # A client that does not send the field at all means "leave it
            # alone", not "clear it". The form sends the mask when untouched.
            clean[field] = str(settings().get(field) or '')
        elif clean[field] == mask:
            # A report round trip carries the mask, not the key, so an
            # unchanged field means "keep whatever is stored".
            clean[field] = str(settings().get(field) or '')
        if len(clean[field]) > limit: raise ValueError('Give the API key 200 characters or fewer.')
    if str(value.get('ledgerSyncDir') or '').strip():
        clean['ledgerSyncDir'] = str(Path(str(value['ledgerSyncDir'])).expanduser().absolute())
    if len(clean['ledgerDeviceId']) > 80: raise ValueError('Give the ledger device id 80 characters or fewer.')
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
    # A price key is a provider (local history) or a labelled account id.
    account_ids = {account['id'] for account in clean['accounts']}
    for name, amount in value.get('monthlyPrices', {}).items():
        if (name in PROVIDERS or name in account_ids) and amount is not None and 0 <= float(amount) <= 100000:
            clean['monthlyPrices'][name] = float(amount)
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


def muse_records(path):
    session, project = path.parent.name, ''
    with path.open(errors='replace') as f:
        for raw in f:
            try: outer = json.loads(raw)
            except ValueError: continue
            if not isinstance(outer, dict): continue
            # The first line is a retained_frame envelope whose children hold
            # the real records as encoded strings. The rest are direct.
            records = []
            if isinstance(outer.get('children'), list) and not isinstance(outer.get('payload'), dict):
                for child in outer['children']:
                    if not isinstance(child, dict): continue
                    try: records.append(json.loads(child.get('record_json', '')))
                    except (ValueError, AttributeError): continue
            else:
                records = [outer]
            for entry in records:
                if not isinstance(entry, dict): continue
                payload = entry.get('payload')
                if not isinstance(payload, dict): continue
                if payload.get('kind') in ('metadata', 'route_facts'):
                    info = payload.get('record')
                    if not isinstance(info, dict): info = {}
                    project = info.get('workspace_root') or info.get('cwd') or project
                    continue
                # Only model_completed carries per-model token usage. The
                # goal_usage_attribution provider rows duplicate the same
                # counts and its tool rows are zero, so they are ignored.
                event = payload.get('event')
                if not isinstance(event, dict) or event.get('kind') != 'model_completed': continue
                usage = event.get('usage')
                if not isinstance(usage, dict): continue
                stream = entry.get('stream')
                if not isinstance(stream, dict): stream = {}
                session = str(stream.get('id') or session)
                model = event.get('model') or 'unknown'
                response = event.get('response_id')
                key = digest('muse', response) if response else digest('muse', session, entry.get('recorded_at'), model)
                # input_tokens includes reused context, like Codex. The two
                # cache spellings report the same count; never add both.
                read = number(usage.get('cache_read_tokens')) or number(usage.get('cached_tokens'))
                yield record(key, 'muse', session, number(entry.get('recorded_at')) // 1000000,
                             model, project, 'Muse',
                             input=max(0, number(usage.get('input_tokens')) - read),
                             output=usage.get('output_tokens'), cacheRead=read,
                             cacheWrite=usage.get('cache_write_tokens'),
                             reasoning=usage.get('reasoning_tokens'))


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
    # Go keeps the app's own estimate as a fallback for models the catalog
    # does not cover yet. Catalog rates still win where they exist.
    r['reportedValue'] = reported_value(cost)
    return r


def hermes_ledger_path(root):
    return Path(root).expanduser() / 'state.db'


def hermes_records(path):
    """Per-route token totals from the Hermes agent's own usage table.

    Hermes bills OpenCode Go and Ollama Cloud through the same accounts this
    dashboard reads from the CLI apps, and writes nothing to those apps'
    histories, so its ledger is the only local record of that traffic. Rows
    are per session, model, route, and task; the table accumulates in place,
    so a row's id is namespaced by those keys and the ledger keeps the
    largest totals rather than adding a rescan.
    """
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute('PRAGMA table_info(session_model_usage)')}
        if not {'session_id', 'model', 'billing_provider', 'first_seen', 'last_seen', 'input_tokens'} <= columns:
            raise ValueError('Not a Hermes usage ledger')
        try: projects = {r[0]: r[1] for r in conn.execute('SELECT id, cwd FROM sessions')}
        except sqlite3.Error: projects = {}
        wanted = ','.join('?' for _ in HERMES_ROUTES)
        for row in conn.execute(f'SELECT * FROM session_model_usage WHERE billing_provider IN ({wanted})', HERMES_ROUTES):
            r = dict(row)
            # Collapse the wire-format variants onto one provider, keeping the
            # raw route on apiProvider for the Routes breakdown.
            route = r['billing_provider']
            provider = HERMES_ROUTE_NAMES.get(route, route)
            session, model = str(r['session_id']), r['model'] or 'unknown'
            task = str(r.get('task') or '')
            # Anchor the row at its first sighting. The table accumulates in
            # place, so using last_seen would move a row's whole total to a
            # later day on every rescan and rewrite the daily history.
            ts = timestamp(r.get('first_seen') or r.get('last_seen'))
            # Hermes records the provider's own completion_tokens, which already
            # include reasoning, so it must not be added again. Verified against
            # this table: of 36 rows carrying reasoning, none has reasoning
            # above output (max ratio 0.989), while OpenCode's disjoint counter
            # exceeds output in 59% of its rows. The row's id covers every key
            # of the source table's primary key so two distinct rows cannot
            # collapse and lose tokens under the ledger's MAX upsert.
            entry = record(digest('hermes', route, session, model, task,
                                  r.get('billing_base_url'), r.get('billing_mode')),
                           provider, session, ts, model,
                           projects.get(r['session_id']) or '', 'Hermes',
                           input=r.get('input_tokens'), output=r.get('output_tokens'),
                           reasoning=r.get('reasoning_tokens'), cacheRead=r.get('cache_read_tokens'),
                           cacheWrite=r.get('cache_write_tokens'))
            entry['apiProvider'] = route
            entry['modelCalls'] = number(r.get('api_call_count'))
            entry['reportedValue'] = reported_value(r.get('estimated_cost_usd'))
            yield {'row': entry, 'task': HERMES_TASKS.get(task, task or 'conversation')}
    finally:
        conn.close()


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
        current = self.db.execute("SELECT value FROM metadata WHERE key='provenanceVersion'").fetchone()
        if not current:
            # Re-index source locations once, without deleting retained metrics.
            self.db.execute('DELETE FROM files')
            self.db.execute("INSERT INTO metadata VALUES ('provenanceVersion','2')")
        elif current[0] != '2':
            # Version 2 corrects the Hermes parser: its output no longer has
            # reasoning added on top, its rows are anchored at first sighting,
            # and its event ids cover the source table's whole primary key.
            # Events are keyed by id, so the corrected values need those rows
            # re-read rather than upserted over the old ones.
            stale = [r[0] for r in self.db.execute(
                "SELECT DISTINCT event_id FROM event_sources WHERE path LIKE '%state.db'")]
            for event in stale:
                self.db.execute('DELETE FROM events WHERE id=?', (event,))
            self.db.execute("DELETE FROM event_sources WHERE path LIKE '%state.db'")
            self.db.execute("UPDATE metadata SET value='2' WHERE key='provenanceVersion'")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(events)')}
        for name in ('reportedCostTicks', 'modelCalls'):
            if name not in columns: self.db.execute(f'ALTER TABLE events ADD COLUMN {name} INTEGER')
        for name, kind in [('reportedValue', 'REAL'), ('apiProvider', 'TEXT')]:
            if name not in columns: self.db.execute(f'ALTER TABLE events ADD COLUMN {name} {kind}')

    def put(self, r, source=None, growing=False):
        if not r['ts'] or not sum(r[f] for f in FIELDS[:4]): return
        keys = list(r)
        # Claude streams may repeat a message with a larger final usage count.
        update = ','.join(f'{f}=MAX(events.{f},excluded.{f})' for f in FIELDS)
        # A source whose row accumulates in place also moves forward in time;
        # every other source keeps the timestamp it first recorded.
        if growing: update += ',ts=MIN(events.ts,excluded.ts)'
        update += ',reportedCostTicks=COALESCE(MAX(COALESCE(events.reportedCostTicks,0),excluded.reportedCostTicks),events.reportedCostTicks)'
        update += ',modelCalls=MAX(COALESCE(events.modelCalls,0),COALESCE(excluded.modelCalls,0))'
        update += ',reportedValue=COALESCE(MAX(COALESCE(events.reportedValue,0),excluded.reportedValue),events.reportedValue)'
        update += ',apiProvider=COALESCE(excluded.apiProvider,events.apiProvider)'
        self.db.execute(f'INSERT INTO events ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)}) '
                        f'ON CONFLICT(id) DO UPDATE SET {update}', list(r.values()))
        if source is not None:
            self.db.execute('INSERT OR IGNORE INTO event_sources VALUES (?,?)', (r['id'], str(source)))

    def sync_ledgers(self, cfg):
        # Each machine writes a consistent ledger snapshot (VACUUM INTO) to a
        # shared folder and imports the other snapshots. Event ids deduplicate;
        # local provenance always wins over a machine-prefixed copy.
        directory = str(cfg.get('ledgerSyncDir') or '').strip()
        if not directory: return []
        directory = Path(directory).expanduser()
        device = str(cfg.get('ledgerDeviceId') or '').strip() or socket.gethostname()
        warnings = []
        try: directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return ['Could not create the synced ledger folder.']
        snapshot = directory / (device + '.sqlite')
        signature = list(self.db.execute('SELECT COUNT(*),COALESCE(MAX(ts),0),COALESCE(SUM(input+output+cacheRead+cacheWrite),0) FROM events').fetchone())
        stored = self.db.execute("SELECT value FROM metadata WHERE key='ledgerSignature'").fetchone()
        if not stored or stored[0] != json.dumps(signature):
            temporary = directory / ('.' + device + '.tmp.sqlite')
            try:
                if temporary.exists(): temporary.unlink()
                self.db.commit()
                self.db.execute('VACUUM INTO ?', (str(temporary),))
                os.chmod(temporary, 0o600)
                os.replace(temporary, snapshot)
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('ledgerSignature', json.dumps(signature)))
            except (OSError, sqlite3.Error):
                warnings.append('Could not write the synced ledger snapshot.')
                if temporary.exists(): temporary.unlink(missing_ok=True)
        for path in sorted(directory.glob('*.sqlite')):
            if path.name == device + '.sqlite': continue
            stamp = None
            try:
                stamp = path.stat()
                if self.db.execute('SELECT size,mtime FROM files WHERE path=?', (str(path),)).fetchone() == (stamp.st_size, stamp.st_mtime_ns): continue
                self.import_ledger(path, path.stem)
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                warnings.append('Could not read synced ledger ' + path.name)
            # Record the attempted file so a broken snapshot warns once, not every scan.
            if stamp is not None:
                self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)', (str(path), stamp.st_size, stamp.st_mtime_ns))
        self.db.commit()
        return warnings

    def import_ledger(self, path, device):
        ours = {row[1] for row in self.db.execute('PRAGMA table_info(events)')}
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
        try:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute('PRAGMA table_info(events)')}
            if not {'id', 'provider', 'session', 'ts', 'model', 'project', 'client', *FIELDS} <= columns:
                raise ValueError('Not a usage ledger')
            for row in conn.execute('SELECT * FROM events'):
                self.put({key: row[key] for key in row.keys() if key in ours}, None)
            try: sources = list(conn.execute('SELECT event_id,path FROM event_sources'))
            except sqlite3.Error: sources = []
            for event, original in sources:
                # Keep one provenance per event so imported copies cannot
                # conflict with a local account or another machine.
                self.db.execute('INSERT INTO event_sources(event_id,path) SELECT ?,? '
                                'WHERE NOT EXISTS (SELECT 1 FROM event_sources WHERE event_id=?)',
                                (event, f'machine:{device}/{original}', event))
        finally:
            conn.close()

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
            ('omp', [str(HOME / '.omp/agent')] + cfg.get('ompHomes', []), lambda path: pi_records(path, 'omp')),
            ('muse', [os.getenv('MUSE_HOME') or str(Path(os.getenv('XDG_DATA_HOME', HOME / '.local/share')) / 'muse')] + cfg.get('museHomes', []), muse_records)):
            for root in sorted(set(roots)):
                root = Path(root).expanduser()
                folders = [root / 'sessions', root / 'archived_sessions'] if provider == 'codex' else [root / {'claude': 'projects', 'gemini': 'tmp'}.get(provider, 'sessions')]
                for folder in folders:
                    pattern = 'updates.jsonl' if provider == 'grok' else '*.json*' if provider == 'gemini' else 'session.jsonl' if provider == 'muse' else '*.jsonl'
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
        hermes_roots = [str(HOME / '.hermes')] + cfg.get('hermesHomes', [])
        for root in sorted(set(hermes_roots)):
            path = hermes_ledger_path(root)
            source = {'provider': 'hermes', 'path': str(path), 'files': int(path.exists()),
                      'exists': path.exists(), 'kind': 'database', 'clients': list(HERMES_ROUTES)}
            sources.append(source)
            if not path.exists(): continue
            try:
                for item in hermes_records(path):
                    # The task dimension the CLI apps do not record, kept in the
                    # client field so the breakdowns distinguish typed prompts
                    # from background work the agent did on its own.
                    entry = item['row']
                    if item['task'] != 'conversation':
                        entry['client'] = 'Hermes · ' + item['task']
                    self.put(entry, path.resolve(), growing=True)
            except (sqlite3.Error, ValueError, TypeError, AttributeError, KeyError):
                source['readErrors'] = 1
                warnings.append('Hermes database could not be read; retained previous records.')
        for source in sources:
            source['status'] = 'missing' if not source['exists'] else 'partial' if source.get('readErrors') else 'available'
        warnings.extend(self.sync_ledgers(cfg))
        meta = {'sources': sources, 'warnings': warnings, 'scannedAt': time.time(), 'machine': 'demo-computer' if os.getenv('AI_USAGE_DEMO') == '1' else socket.gethostname()}
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('scan', json.dumps(meta)))
        self.db.commit()
        return meta


def load_rates():
    path = STATE / 'rates.json'
    # Bundled, attributed snapshot works offline. Documented official overrides
    # come next. A user catalog always wins where it sets a rate.
    try:
        data = json.loads(Path(__file__).with_name('catalog.json').read_text())
        if not isinstance(data.get('document'), dict): raise ValueError('Invalid catalog')
    except (OSError, ValueError, AttributeError):
        data = {'document': {}, 'source': 'Pricing catalog unavailable', 'fetchedAtMs': None}
    else:
        data.setdefault('source', 'Bundled pricing catalog')
    for name, label in OVERRIDES:
        try:
            official = json.loads(Path(__file__).with_name(name).read_text())
            if not isinstance(official.get('models'), dict): raise ValueError('Invalid override')
            verified = official['verifiedAt']
            if not isinstance(verified, str): raise ValueError('Invalid override')
            data['document'].update(official['models'])
            data['source'] += f' + {label} (' + verified + ')'
        except (OSError, ValueError, TypeError, KeyError): pass
    # Preserve user rates on top of every bundled and official entry.
    if path.exists():
        try:
            user = json.loads(path.read_text())
            if not isinstance(user.get('document'), dict): raise ValueError('Invalid catalog')
            data['document'] = data['document'] | user['document']
            data['source'] = user.get('source', 'User pricing catalog') + ' + bundled and official models'
        except (OSError, ValueError, TypeError, KeyError, AttributeError): pass
    return data


def peak_rate(rate, ts):
    multiplier, hours = rate.get('peak_cost_multiplier'), rate.get('peak_hours_utc')
    if not ts or not isinstance(multiplier, (int, float)) or not isinstance(hours, list): return rate
    moment = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    if rate.get('peak_weekdays_only') and moment.weekday() >= 5: return rate
    if not any(isinstance(window, list) and len(window) == 2 and window[0] <= moment.hour < window[1] for window in hours): return rate
    return {key: (value * multiplier if isinstance(value, (int, float)) and 'cost' in key and 'token' in key else value)
            for key, value in rate.items()}


def price(r, catalog):
    if r['provider'] == 'grok':
        ticks = r.get('reportedCostTicks')
        return (ticks / 10_000_000_000, None) if ticks else (None, None)
    if r['provider'] in ('opencode', 'pi', 'omp') and r.get('reportedValue') is not None:
        return r['reportedValue'], None
    fallback = r.get('reportedValue') if r['provider'] == 'opencode-go' else None
    fallback = (fallback, None) if fallback is not None else (None, None)
    model = r['model']
    lookup = (r.get('apiProvider') or r['provider']) + '/' + model
    # CommandCode bills one resale table for the whole product, so both of its
    # wire-format routes read the same namespaced keys rather than falling
    # through to the lab's own list price for a same-named model.
    if r['provider'] == 'commandcode': lookup = 'commandcode/' + model
    rate = catalog.get(lookup) or catalog.get(model) or catalog.get('anthropic/' + model) or catalog.get('openai/' + model) or catalog.get('gemini/' + model)
    if not rate: return fallback
    # Internal models have no published rate and are not billed per token.
    if rate.get('internal'): return 0.0, None
    rate = peak_rate(rate, r['ts'])
    context = r['input'] + r['cacheRead'] + r['cacheWrite']
    suffix = ''
    for threshold, candidate in [(200000, '_above_200k_tokens'), (256000, '_above_256k_tokens'), (272000, '_above_272k_tokens')]:
        if context > threshold and 'input_cost_per_token' + candidate in rate: suffix = candidate
    def cost(key, fallback=None): return rate.get(key + suffix, rate.get(key, fallback))
    inp, out = cost('input_cost_per_token'), cost('output_cost_per_token')
    read = cost('cache_read_input_token_cost')
    write = cost('cache_creation_input_token_cost')
    write1h = cost('cache_creation_input_token_cost_above_1hr', None)
    parts = [(r['input'], inp), (r['output'], out), (r['cacheRead'], read),
             (max(0, r['cacheWrite'] - r['cacheWrite1h']), write), (r['cacheWrite1h'], write1h)]
    if any(n and not isinstance(v, (int, float)) for n, v in parts): return fallback
    value = sum(n * (v or 0) for n, v in parts)
    saving = max(0, r['cacheRead'] * ((inp or 0) - (read or 0)))
    return value, saving


def bucket():
    return {**{f: 0 for f in FIELDS}, 'tokens': 0, 'value': 0.0, 'cacheSavings': 0.0,
            'unpricedTokens': 0, 'requests': 0, 'modelCalls': 0, 'sessions': set()}


def add(b, r, value, savings):
    for f in FIELDS: b[f] += r[f]
    total = sum(r[f] for f in FIELDS[:4])
    b['tokens'] += total; b['requests'] += 1; b['sessions'].add(r['provider'] + ':' + r['session'])
    b['modelCalls'] += r.get('modelCalls') or 0
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


def account_quotas():
    # Labelled accounts may have their own agent usage records (for example a
    # second Codex home with its own collector). Index them by record id and
    # by record name so an account can pick up its limits when either matches.
    by_id, by_name = {}, {}
    try: paths = sorted((STATE.parent / 'agents/usage').glob('*.json'))
    except OSError: paths = []
    for path in paths:
        try: d = json.loads(path.read_text())
        except (OSError, ValueError): continue
        if not isinstance(d, dict) or not (d.get('limits') or d.get('usageStatusText')): continue
        record = {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('usageStatusText', ''), 'plan': d.get('tierLabel', '')}
        by_id[path.stem] = record
        if d.get('name'): by_name.setdefault(str(d['name']).casefold(), record)
    return by_id, by_name


def quota(provider):
    if provider in ('opencode', 'pi', 'omp'):
        return {'limits': [], 'error': 'Account limits belong to the underlying provider and are not collected here.'}
    if provider in ('opencode-go', 'grok', 'muse', 'ollama-cloud', 'commandcode'):
        try: d = json.loads((STATE / {'opencode-go': 'go-quota.json', 'grok': 'grok-quota.json', 'muse': 'muse-quota.json',
                                      'ollama-cloud': 'ollama-quota.json',
                                      'commandcode': 'commandcode-quota.json'}[provider]).read_text())
        except (OSError, ValueError): d = {}
        result = {'limits': d.get('limits', []), 'updatedAt': d.get('updatedAt'), 'error': d.get('error', '')}
        if provider in ('muse', 'ollama-cloud', 'commandcode'): result['plan'] = d.get('plan', '')
        return result
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


def muse_quota(force=False):
    path = STATE / 'muse-quota.json'
    auth_path = Path(os.getenv('XDG_CONFIG_HOME', HOME / '.config')) / 'muse/auth.json'
    try:
        stat = auth_path.stat()
        auth_version = [stat.st_mtime_ns, stat.st_size]
    except OSError: auth_version = None
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and cached.get('authVersion') == auth_version and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        auth = json.loads(auth_path.read_text())
        meta = (auth.get('providers') or {}).get('meta') or {}
        token = meta.get('access_token')
        if not token: raise QuotaUnavailable('Run muse login to read Muse quota.')
        base = str(meta.get('api_base_url') or 'https://api.meta.ai/v1').rstrip('/')
        if base.endswith('/v1'): base = base[:-len('/v1')]
        request = urllib.request.Request(base + '/muse-code/key', data=json.dumps({}).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                     'User-Agent': 'Omarchy-AI-Usage/0.1'})
        with urllib.request.urlopen(request, timeout=12) as response: minted = json.load(response)
        if not isinstance(minted, dict): raise ValueError('Muse returned an unrecognized quota response.')
        usage = minted.get('subs_usage')
        if not isinstance(usage, dict): raise ValueError('Muse returned no recognized quota windows.')
        windows = []
        window = usage.get('window')
        if isinstance(window, dict) and isinstance(window.get('used_percent'), (int, float)) and 0 <= window['used_percent'] <= 100:
            mins = window.get('window_duration_mins')
            label = f'Session ({mins // 60}-hour)' if isinstance(mins, int) and mins >= 60 and mins % 60 == 0 else 'Session'
            entry = {'label': label, 'percent': window['used_percent'] / 100}
            reset = window.get('resets_at')
            if reset: entry['resetsAt'] = dt.datetime.fromtimestamp(timestamp(reset), dt.timezone.utc).isoformat()
            windows.append(entry)
        weekly = usage.get('weekly')
        if isinstance(weekly, dict) and isinstance(weekly.get('used_percent'), (int, float)) and 0 <= weekly['used_percent'] <= 100:
            entry = {'label': 'Weekly (7-day)', 'percent': weekly['used_percent'] / 100}
            reset = weekly.get('resets_at')
            if reset: entry['resetsAt'] = dt.datetime.fromtimestamp(timestamp(reset), dt.timezone.utc).isoformat()
            windows.append(entry)
        if not windows: raise ValueError('Muse returned no recognized quota windows.')
        # Only display-safe fields enter the cache. Minted key material is
        # never persisted, and errors below never quote it either.
        cached = {'limits': windows, 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': '',
                  'plan': str(minted.get('subs_tier_name') or '')}
    except Exception as exc:
        cached['error'] = str(exc) if isinstance(exc, QuotaUnavailable) else 'Muse quota unavailable. Check your Muse login.'
    cached['attemptedAt'] = time.time()
    cached['authVersion'] = auth_version
    atomic_json(path, cached)
    return cached


def config_dir():
    """Settings and key files, resolved at call time so a test or an
    alternate XDG_CONFIG_HOME is honored."""
    return Path(os.getenv('XDG_CONFIG_HOME', HOME / '.config')) / 'omarchy/ai-usage'


def ollama_key(cfg=None):
    """Ollama Cloud API key, preferring a key the user supplied over one the
    machine happens to export. Nothing here writes or refreshes credentials."""
    cfg = settings() if cfg is None else cfg
    typed = str(cfg.get('ollamaApiKey') or '').strip()
    if typed: return typed
    from_env = str(os.getenv('OLLAMA_API_KEY') or '').strip()
    if from_env: return from_env
    for path in (config_dir() / 'ollama.key', Path(os.getenv('XDG_DATA_HOME', HOME / '.local/share')) / 'ollama/api_key'):
        try: value = path.read_text().strip()
        except OSError: continue
        if value: return value
    return ''


def ollama_quota(force=False):
    path = STATE / 'ollama-quota.json'
    key = ollama_key()
    key_file = config_dir() / 'ollama.key'
    try:
        stat = key_file.stat()
        key_version = [stat.st_mtime_ns, stat.st_size]
    except OSError: key_version = None
    # Throttle on the credential itself, not on one of the files that may hold
    # it: switching accounts in Settings or OLLAMA_API_KEY would otherwise show
    # the previous account's usage until the cache expired. Only a digest is
    # ever stored.
    key_version = {'file': key_version,
                   'key': hashlib.sha256(key.encode()).hexdigest()[:16] if key else None}
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and cached.get('keyVersion') == key_version and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        if not key: raise QuotaUnavailable('Add an Ollama Cloud API key in Settings to read its usage.')
        request = urllib.request.Request('https://ollama.com/api/usage',
            headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'Omarchy-AI-Usage/0.1'})
        with urllib.request.urlopen(request, timeout=12) as response: data = json.load(response)
        if not isinstance(data, dict): raise ValueError('Ollama returned an unrecognized usage response.')
        limits = data.get('limits')
        if not isinstance(limits, dict): raise ValueError('Ollama returned no recognized usage windows.')
        windows = []
        # Whatever windows the plan has. Legacy plans report session and
        # weekly; credit plans report a monthly one. The endpoint carries no
        # reset time, so the meters show a share without a countdown.
        for name, label in OLLAMA_WINDOWS.items():
            window = limits.get(name)
            if not isinstance(window, dict) or not isinstance(window.get('usage'), (int, float)): continue
            # A plan can report above 100% (overage). Clamp for the meter
            # rather than discarding the window, which would read as a
            # credential failure once it is the only window left.
            windows.append({'label': label, 'percent': min(1.0, max(0.0, float(window['usage']))),
                            'raw': float(window['usage'])} if window['usage'] > 1
                           else {'label': label, 'percent': float(window['usage'])})
        if not windows: raise ValueError('Ollama returned no recognized usage windows.')
        plan = data.get('plan') if isinstance(data.get('plan'), str) else ''
        cached = {'limits': windows, 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': '', 'plan': plan}
    except Exception as exc:
        # Never quote a credential-bearing request object, URL, or body.
        cached['error'] = str(exc) if isinstance(exc, QuotaUnavailable) else 'Ollama Cloud usage unavailable. Check the API key.'
    cached['attemptedAt'] = time.time()
    cached['keyVersion'] = key_version
    atomic_json(path, cached)
    return cached


def commandcode_key(cfg=None):
    """CommandCode API key, preferring one the user supplied over one the
    machine happens to export. Nothing here writes or refreshes credentials."""
    cfg = settings() if cfg is None else cfg
    typed = str(cfg.get('commandcodeApiKey') or '').strip()
    if typed: return typed
    from_env = str(os.getenv('COMMANDCODE_API_KEY') or '').strip()
    if from_env: return from_env
    for path in (config_dir() / 'commandcode.key',):
        try: value = path.read_text().strip()
        except OSError: continue
        if value: return value
    return ''


def commandcode_call(key, path):
    """One read-only GET against CommandCode's own endpoints. The key rides in
    a header; nothing here writes, refreshes, or logs a credential."""
    request = urllib.request.Request('https://api.commandcode.ai' + path,
        headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
                 'User-Agent': 'Omarchy-AI-Usage/0.1'})
    with urllib.request.urlopen(request, timeout=12) as response:
        data = json.load(response)
    if not isinstance(data, dict): raise ValueError('CommandCode returned an unrecognized usage response.')
    return data


def commandcode_quota(force=False):
    """CommandCode measures its allowance in credit value, not tokens: GOAT
    allows $14 in any 5 hours, $35 in any 7 days, and $70 a month. The windows
    carry a reset time each, and the subscription carries the billing period
    end, so unlike the other providers this card can show countdowns.

    Every figure is read from the API rather than assumed: the monthly cap is
    the remaining credits plus what this period has spent, which the endpoints
    agree on exactly, so a plan change needs no code change."""
    path = STATE / 'commandcode-quota.json'
    key = commandcode_key()
    key_file = config_dir() / 'commandcode.key'
    try:
        stat = key_file.stat()
        key_version = [stat.st_mtime_ns, stat.st_size]
    except OSError: key_version = None
    key_version = {'file': key_version,
                   'key': hashlib.sha256(key.encode()).hexdigest()[:16] if key else None}
    try: cached = json.loads(path.read_text())
    except (OSError, ValueError): cached = {}
    if not force and cached.get('keyVersion') == key_version and time.time() - cached.get('attemptedAt', 0) < 300: return cached
    try:
        if not key: raise QuotaUnavailable('Add a CommandCode API key in Settings to read its usage.')
        credits = commandcode_call(key, '/alpha/billing/credits')
        windows = credits.get('windowLimits') or {}
        limits = []
        # The endpoints express resets differently: the windows use epoch
        # milliseconds, the subscription an ISO string. Normalise to ISO, which
        # is what the panel's countdown reads.
        for name, label in (('fiveHour', '5 hours'), ('weekly', 'Weekly')):
            window = windows.get(name)
            if not isinstance(window, dict): continue
            used, cap = window.get('used'), window.get('cap')
            if not isinstance(used, (int, float)) or not isinstance(cap, (int, float)) or cap <= 0: continue
            reset = window.get('resetAt')
            limits.append({'label': label, 'percent': min(1.0, max(0.0, used / cap)),
                           'raw': used / cap,
                           'resetsAt': dt.datetime.fromtimestamp(timestamp(reset), dt.timezone.utc).isoformat() if timestamp(reset) else ''})
        monthly = (credits.get('credits') or {}).get('monthlyCredits')
        reset_at = ''
        plan = ''
        try:
            subscription = (commandcode_call(key, '/alpha/billing/subscriptions').get('data') or {})
            plan_id = subscription.get('planId')
            if isinstance(plan_id, str) and plan_id:
                # "individual-goat" reads as "GOAT" on the card.
                plan = plan_id.split('-')[-1].upper()
            reset_at = str(subscription.get('currentPeriodEnd') or '')
        except Exception: pass
        if isinstance(monthly, (int, float)):
            # What this billing period has spent, from the same source the CLI
            # shows. Remaining + spent is the period's allowance.
            try: spent = commandcode_call(key, '/alpha/usage/summary').get('totalCredits')
            except Exception: spent = None
            if isinstance(spent, (int, float)):
                cap = monthly + spent
                if cap > 0:
                    limits.append({'label': 'Monthly', 'percent': min(1.0, max(0.0, spent / cap)),
                                   'raw': spent / cap, 'resetsAt': reset_at})
        if not limits: raise ValueError('CommandCode returned no recognized usage windows.')
        cached = {'limits': limits, 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': '', 'plan': plan}
    except Exception as exc:
        # Never quote a credential-bearing request object, URL, or body.
        cached['error'] = str(exc) if isinstance(exc, QuotaUnavailable) else 'CommandCode usage unavailable. Check the API key.'
    cached['attemptedAt'] = time.time()
    cached['keyVersion'] = key_version
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
    cache, assignments, machines = {}, {}, set()
    for event, provider, path in ledger.db.execute('SELECT e.id,e.provider,s.path FROM events e JOIN event_sources s ON s.event_id=e.id'):
        key = (provider, path)
        if key not in cache:
            source = Path(path)
            match = next((aid for p, root, aid in roots if p == provider and source.is_relative_to(root)), None)
            if match is None and str(path).startswith('machine:'):
                match = str(path).split('/', 1)[0]
                machines.add(match)
            cache[key] = match or 'local'
        assignments.setdefault(event, set()).add(cache[key])
    for aid in sorted(machines): labels[aid] = aid.split(':', 1)[1]
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
    daily = {str(start_date + dt.timedelta(days=n)): {'providers': {p: bucket() for p in providers}, 'cards': {}} for n in range(days)}
    hour_start, hour_end = start, end
    if selection.get('day'):
        selected_date = dt.date.fromisoformat(selection['day'])
        hour_start = dt.datetime.combine(selected_date, dt.time()).timestamp()
        hour_end = min(end, dt.datetime.combine(selected_date + dt.timedelta(days=1), dt.time()).timestamp() - 1)
    hourly = [{'start': ts, 'label': dt.datetime.fromtimestamp(ts).strftime('%H:%M'),
               'title': dt.datetime.fromtimestamp(ts).astimezone().strftime('%H:%M %Z') + ' to ' +
                        (dt.datetime.fromtimestamp(ts + 3600).astimezone().strftime('%H:%M %Z') if ts + 3600 <= hour_end + 1 else 'now'),
               'providers': {p: bucket() for p in providers}, 'cards': {}}
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
        card_id = p + ':' + account
        if day in daily:
            add(daily[day]['providers'][p], r, value, savings)
            add(daily[day]['cards'].setdefault(card_id, bucket()), r, value, savings)
        if hourly:
            index = int((r['ts'] - hour_start) // 3600)
            if 0 <= index < len(hourly):
                add(hourly[index]['providers'][p], r, value, savings)
                add(hourly[index]['cards'].setdefault(card_id, bucket()), r, value, savings)
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
    # One overview card per account so labelled histories are never merged.
    # A named account uses only its own price; the local group uses the
    # provider price. Quota remains attached to the current login only.
    named_providers = {d['provider'] for account in cfg.get('accounts', []) for d in account['directories']}
    accounts_by_id, accounts_by_name = account_quotas()
    cards = []
    for p, b in providers.items():
        if provider != 'all' and p != provider: continue
        group = sorted(((aid, bucket) for (card_provider, aid), bucket in accounts.items() if card_provider == p),
                       key=lambda item: item[1]['tokens'], reverse=True)
        for shade, (aid, account_bucket) in enumerate(group):
            fin = finish(account_bucket)
            if aid == 'local':
                name = PROVIDERS[p] + (' · ' + labels['local'] if p in named_providers else '')
                monthly = cfg['monthlyPrices'].get(p)
                if selection.get('account'):
                    card_quota, scope = {'limits': [], 'error': 'View All accounts for current-login quota. History labels do not identify credentials.'}, ''
                else:
                    card_quota, scope = quota(p), 'Current login on this PC'
            else:
                name = PROVIDERS[p] + ' · ' + labels[aid]
                monthly = cfg['monthlyPrices'].get(aid)
                # A labelled account with its own agent record shows that
                # record's limits; otherwise quota stays with the login.
                own = None if aid in PROVIDERS else (accounts_by_id.get(str(aid)) or accounts_by_name.get(labels[aid].casefold()))
                if own is not None:
                    card_quota, scope = own, 'From its own usage record'
                else:
                    card_quota, scope = {'limits': [], 'error': 'Quota is shown for the current login only.'}, ''
            cards.append(fin | {'id': p + ':' + aid, 'provider': p, 'accountId': aid, 'name': name, 'monthlyPrice': monthly,
                                'shade': shade, 'shades': len(group),
                                'quota': card_quota, 'quotaScope': scope,
                                'valueShare': 100 * fin['value'] / summary['value'] if summary['value'] else None})
    # Model-level allowance for Go. The quota endpoint reports only aggregate
    # windows, so value against each model's documented monthly limit is
    # estimated from local history during the current monthly reset window.
    go_allowance = {'since': None, 'models': []}
    if 'opencode-go' in providers:
        monthly = next((limit for limit in quota('opencode-go').get('limits', []) if limit.get('label') == 'Monthly' and limit.get('resetsAt')), None)
        go_allowance['since'] = int(timestamp(monthly['resetsAt']) - 30 * 86400) if monthly else int(dt.datetime.combine(today.date().replace(day=1), dt.time()).timestamp())
        used = {}
        for row in ledger.db.execute('SELECT * FROM events WHERE provider=? AND ts>=? AND ts<=?', ('opencode-go', go_allowance['since'], end)):
            r = dict(row)
            rate = rates['document'].get('opencode-go/' + r['model']) or rates['document'].get(r['model'])
            if not isinstance(rate, dict) or not isinstance(rate.get('monthly_limit_usd'), (int, float)): continue
            entry = used.setdefault(r['model'], [rate, 0.0])
            entry[1] += price(r, rates['document'])[0] or 0
        for model, (rate, value) in sorted(used.items(), key=lambda item: item[1][1], reverse=True):
            limit, promo, promo_ends = rate['monthly_limit_usd'], False, ''
            if isinstance(rate.get('monthly_limit_promo_usd'), (int, float)) and rate.get('promo_ends') and today.date() <= dt.date.fromisoformat(rate['promo_ends']):
                limit, promo, promo_ends = rate['monthly_limit_promo_usd'], True, rate['promo_ends']
            go_allowance['models'].append({'model': model, 'value': value, 'limit': limit, 'promo': promo, 'promoEnds': promo_ends})
    # Per-card model rows from the local ledger. Providers whose quota endpoint
    # reports one aggregate number (Ollama Cloud, for example) draw a single
    # bar, so the local token totals are the only per-model view available.
    # Each card is scoped exactly as the card itself is: by account, and by the
    # same selection the report was asked for.
    card_models = {}
    for card in cards:
        rows_for_card = {}
        for row in ledger.db.execute(
                'SELECT * FROM events WHERE provider=? AND ts>=? AND ts<=?', (card['provider'], start, end)):
            r = dict(row)
            if assignments.get(r['id'], 'unassigned') != card['accountId']: continue
            if any((r[key] or ('Unknown project' if key == 'project' else card['provider'] if key == 'apiProvider' else '')) != selection[key]
                   for key in ('model', 'project', 'client', 'apiProvider') if selection.get(key)): continue
            if selection.get('day') and str(dt.datetime.fromtimestamp(r['ts']).date()) != selection['day']: continue
            value = price(r, rates['document'])[0]
            entry = rows_for_card.setdefault(r['model'], {'model': r['model'], 'tokens': 0, 'value': 0.0, 'unpriced': 0})
            total = sum(r[f] for f in FIELDS[:4])
            entry['tokens'] += total
            if value is None: entry['unpriced'] += total
            else: entry['value'] += value
        card_models[card['id']] = sorted(rows_for_card.values(), key=lambda item: item['tokens'], reverse=True)[:4]
    for card in cards: card['models'] = card_models.get(card['id'], [])
    return {'selection': selection, 'generatedAt': time.time(), 'period': {'days': days, 'start': str(start_date), 'end': str(today.date())},
            'accountOptions': account_options,
            'accounts': [r | {'accountId': r['name'], 'name': labels[r['name']]} for r in rows(accounts)],
            'accountWarning': 'Copies of the same history belong to different accounts. Move mirrored folders into one account.' if any(a == 'conflict' for p, a in accounts) else '',
            'availableProviders': [{'id': p, 'name': name} for p, name in PROVIDERS.items()],
            'summary': finish(summary), 'previous': finish(previous),
            'cards': cards, 'goAllowance': go_allowance,
            'providers': [finish(b) | {'id': p, 'name': PROVIDERS[p], 'quota': quota(p) if not selection.get('account') else {'limits': [], 'error': 'View All accounts for current-login quota. History labels do not identify credentials.'}, 'quotaScope': 'Current login on this PC',
                                      'valueShare': 100 * b['value'] / summary['value'] if summary['value'] else None,
                                      'monthlyPrice': cfg['monthlyPrices'].get(p) if provider_accounts[p] <= {'local'} else None}
                          for p, b in providers.items() if provider == 'all' or p == provider],
            'daily': [{'date': day, 'providers': {p: finish(b) for p, b in values['providers'].items()},
                       'cards': {card_id: finish(b) for card_id, b in values['cards'].items()}} for day, values in daily.items()],
            'hourly': [h | {'providers': {p: finish(b) for p, b in h['providers'].items()},
                            'cards': {card_id: finish(b) for card_id, b in h['cards'].items()}} for h in hourly],
            'routes': rows(routes), 'models': rows(models), 'projects': rows(projects), 'clients': rows(clients), 'sessions': rows(sessions),
            'heatmap': dict(heatmap), 'unknownModels': sorted(unknown), 'coverage': coverage | {'earliest': earliest},
            'pricing': {'source': rates['source'], 'fetchedAtMs': rates.get('fetchedAtMs'),
                        'coveragePercent': 100 * (1 - summary['unpricedTokens']/summary['tokens']) if summary['tokens'] else None,
                        'unpriced': [r for r in rows(models) if r['unpricedTokens']]},
            # Reports reach the bar panel and the dashboard window. The key
            # comes back masked so the settings form can show that one is
            # stored without echoing it.
            'settings': masked_settings(cfg), 'theme': theme()}


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
       'hasPromptStats': False, 'tierLabel': 'Go' if provider == 'opencode-go' else q.get('plan', '') if provider in ('muse', 'ollama-cloud', 'commandcode') else '', 'limits': q.get('limits', []), 'usageStatusText': q.get('error', ''),
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
    parser.add_argument('action', choices=['report', 'scan', 'go', 'settings', 'theme'])
    parser.add_argument('--days', type=int, choices=[1, 7, 30, 90, 365], default=7)
    parser.add_argument('--provider', choices=['all', *PROVIDERS], default='all')
    for field in ('model', 'project', 'client', 'apiProvider', 'day', 'account'): parser.add_argument('--' + field)
    parser.add_argument('--save', nargs='?', const=''); parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    # Theme reads stay out of the ledger path so a theme swap can repaint
    # without waiting on a history scan.
    if args.action == 'theme':
        print(json.dumps(theme())); return
    STATE.mkdir(parents=True, exist_ok=True)
    if args.action == 'settings':
        try:
            # The payload arrives on stdin when a client sends it that way, so
            # a key never has to appear in a command line. --save with a value
            # stays supported for scripted use.
            raw = args.save
            if raw is not None and not raw.strip():
                raw = stdin_payload()
                if raw is None:
                    # Nothing to write. Reporting the settings and exiting 0
                    # would close the window with "Settings saved" and save
                    # nothing, so this is the failure case.
                    print(json.dumps({'error': 'No settings were received to save.'})); raise SystemExit(1)
            # The settings channel is what writes the key, so it is also the
            # one place the key could leak back out through stdout. Every
            # answer masks it, as the usage reports already do.
            clean = save_settings(json.loads(raw)) if raw else settings()
            print(json.dumps(masked_settings(clean)))
        except (ValueError, TypeError, KeyError) as error:
            print(json.dumps({'error': str(error)})); raise SystemExit(1)
        return
    with (STATE / 'collector.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = Ledger(STATE / 'usage.sqlite'); cfg = settings()
        if args.action in ('scan', 'report'): ledger.scan(cfg)
        if not CONFIG.exists():
            found = {r[0] for r in ledger.db.execute('SELECT DISTINCT provider FROM events')}
            cfg = cfg | {'enabled': [p for p in PROVIDERS if p in found] or cfg['enabled']}
        if args.action in ('go', 'scan'):
            go_quota(args.force)
            if args.action == 'go': ledger.scan(cfg)
            write_agent_record(ledger, 'opencode-go')
            if args.action == 'scan' and 'grok' in cfg['enabled']:
                grok_quota(args.force)
                write_agent_record(ledger, 'grok')
            if args.action == 'scan' and 'muse' in cfg['enabled']:
                muse_quota(args.force)
                write_agent_record(ledger, 'muse')
            if args.action == 'scan' and 'ollama-cloud' in cfg['enabled']:
                ollama_quota(args.force)
                write_agent_record(ledger, 'ollama-cloud')
            if args.action == 'scan' and 'commandcode' in cfg['enabled']:
                commandcode_quota(args.force)
                write_agent_record(ledger, 'commandcode')
            if args.action == 'scan':
                for p in ('gemini', 'opencode', 'pi', 'omp'):
                    if p in cfg['enabled']: write_agent_record(ledger, p)
        if args.action == 'report': print(json.dumps(report(ledger, cfg, args.days, args.provider, selection={k: getattr(args, k) for k in ('model', 'project', 'client', 'apiProvider', 'day', 'account') if getattr(args, k)})))
        elif args.action == 'scan': print(json.dumps({'ok': True, 'events': ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}))
        elif args.action == 'go': print(json.dumps({'ok': not bool(quota('opencode-go').get('error'))}))
        ledger.db.close()


if __name__ == '__main__':
    main()
