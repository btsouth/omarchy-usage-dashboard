import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class _HTTPError(Exception):
    def __init__(self, code): super().__init__('HTTP ' + str(code)); self.code = code


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def transcript(self, name, entries):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(e) + '\n' for e in entries))
        return path

    def codex_event(self, ts='2026-09-04T12:00:00Z', total=100):
        return {'type': 'event_msg', 'timestamp': ts, 'payload': {'type': 'token_count', 'info': {
            'last_token_usage': {'input_tokens': 100, 'cached_input_tokens': 70,
                                'output_tokens': 20, 'reasoning_output_tokens': 10},
            'total_token_usage': {'input_tokens': total, 'output_tokens': 20}}}}

    def test_codex_repeated_snapshot_cache_and_reasoning(self):
        event = self.codex_event()
        path = self.transcript('session.jsonl', [event, self.codex_event('2026-09-04T12:01:00Z')])
        records = list(c.codex_records(path))
        self.assertEqual(len(records), 1)
        self.assertEqual(sum(records[0][f] for f in c.FIELDS[:4]), 120)
        self.assertEqual(records[0]['input'], 30)

    def test_archive_move_does_not_duplicate(self):
        path = self.transcript('active.jsonl', [{'type': 'session_meta', 'payload': {'id': 'stable'}}, self.codex_event()])
        ledger = c.Ledger(self.root / 'test.sqlite')
        for r in c.codex_records(path): ledger.put(r)
        moved = path.with_name('archived.jsonl'); path.rename(moved)
        for r in c.codex_records(moved): ledger.put(r)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        moved.unlink(); ledger.db.commit()
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        ledger.db.close()

    def test_fork_inherited_history_is_excluded(self):
        path = self.transcript('fork.jsonl', [{'type': 'session_meta', 'payload': {
            'id': 'child', 'timestamp': '2026-09-04T12:00:00Z', 'forked_from_id': 'parent'}},
            self.codex_event('2026-09-03T12:00:00Z'), self.codex_event(total=200)])
        self.assertEqual(len(list(c.codex_records(path))), 1)

    def test_embedded_parent_metadata_cannot_replace_child(self):
        path = self.transcript('fork.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'child', 'timestamp': '2026-09-04T12:00:00Z'}},
            {'type': 'session_meta', 'payload': {'id': 'parent', 'timestamp': '2026-09-03T12:00:00Z'}},
            self.codex_event('2026-09-03T12:00:00Z'), self.codex_event(total=200)])
        records = list(c.codex_records(path))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['session'], 'child')

    def test_claude_stream_chunks_upsert_final_usage(self):
        def event(out): return {'type': 'assistant', 'timestamp': '2026-09-04T12:00:00Z',
             'sessionId': 's', 'requestId': 'req', 'message': {'id': 'msg', 'model': 'claude-test',
             'usage': {'input_tokens': 10, 'output_tokens': out, 'cache_creation_input_tokens': 20}}}
        path = self.transcript('claude.jsonl', [event(1), event(30), event(10)])
        ledger = c.Ledger(self.root / 'test.sqlite')
        for r in c.claude_records(path): ledger.put(r)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*),SUM(output) FROM events').fetchone(), (1, 30))
        ledger.db.close()

    def test_unknown_price_is_not_zero(self):
        r = c.record('1', 'codex', 's', 1, 'missing', '', 'CLI', input=100)
        self.assertEqual(c.price(r, {}), (None, None))
        self.assertEqual(c.price(r, {'missing': {'input_cost_per_token': 0, 'output_cost_per_token': 0}}), (0, 0))

    def grok_event(self, session='s', event='event', usage=None):
        return {'timestamp': 1788580000, 'params': {'sessionId': session, '_meta': {'eventId': event},
                'update': {'sessionUpdate': 'turn_completed', 'prompt_id': 'prompt', 'usage': usage or {
                    'inputTokens': 1000, 'cachedReadTokens': 800, 'outputTokens': 100,
                    'reasoningTokens': 40, 'modelCalls': 17, 'costUsdTicks': 5912850000}}}}

    def test_grok_cache_reasoning_cost_and_copied_events(self):
        path = self.transcript('project/session/updates.jsonl', [self.grok_event(), self.grok_event('fork')])
        ledger = c.Ledger(self.root / 'grok.sqlite')
        for r in c.grok_records(path): ledger.put(r)
        ledger.db.row_factory = sqlite3.Row
        rows = ledger.db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows), 1)
        r = dict(rows[0])
        self.assertEqual((r['input'], r['cacheRead'], r['output'], r['reasoning'], r['modelCalls']), (200, 800, 100, 40, 17))
        self.assertEqual(sum(r[f] for f in c.FIELDS[:4]), 1100)
        self.assertAlmostEqual(c.price(r, {})[0], .591285)
        ledger.db.close()

    def test_grok_per_model_usage_does_not_duplicate_aggregate(self):
        usage = {'inputTokens': 300, 'outputTokens': 30, 'costUsdTicks': 3000000000,
                 'modelUsage': {'one': {'inputTokens': 100, 'outputTokens': 10, 'costUsdTicks': 1000000000},
                                'two': {'inputTokens': 200, 'outputTokens': 20}}}
        path = self.transcript('updates.jsonl', [self.grok_event(usage=usage)])
        rows = list(c.grok_records(path))
        self.assertEqual(sum(r['input'] + r['output'] for r in rows), 330)
        self.assertEqual(c.price(rows[0], {})[0], .1)
        self.assertEqual(c.price(rows[1], {}), (None, None))
        usage['modelUsage'].pop('one')
        path = self.transcript('updates.jsonl', [self.grok_event(usage=usage)])
        self.assertEqual(c.price(next(c.grok_records(path)), {})[0], .3)

    def test_grok_missing_usage_is_not_invented(self):
        event = self.grok_event(); event['params']['update'].pop('usage')
        path = self.transcript('updates.jsonl', [event, self.grok_event(usage={'inputTokens': 5})])
        rows = list(c.grok_records(path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(c.price(rows[0], {'unknown': {'input_cost_per_token': 1}}), (None, None))

    def test_grok_billing_requires_valid_message_and_success(self):
        def frame(data, flag=0): return bytes([flag]) + len(data).to_bytes(4, 'big') + data
        body = b'\x0a\x05\x0d' + c.struct.pack('<f', 42.5)
        self.assertEqual(c.grok_billing(frame(body))[0]['percent'], .425)
        self.assertEqual(c.grok_billing(frame(b'\x0a\x00'))[0]['percent'], 0)
        for raw in [b'', frame(b''), frame(body)[:-1], frame(body) + frame(b'grpc-status: 16\r\n', 128)]:
            with self.assertRaises(ValueError): c.grok_billing(raw)

    def test_grok_expired_auth_stays_untouched_and_retains_stale_quota(self):
        home = self.root / 'grok'; home.mkdir()
        auth = home / 'auth.json'; auth.write_text(json.dumps({'account': {'key': 'test', 'expires_at': '2000-01-01T00:00:00Z'}}))
        before = auth.read_bytes()
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'GROK_HOME': str(home)}), patch.object(c.urllib.request, 'urlopen') as request:
            c.atomic_json(c.STATE / 'grok-quota.json', {'limits': [{'percent': .3}], 'updatedAt': '2000-01-01T00:00:00Z'})
            quota = c.grok_quota(True)
            self.assertIn('expired', quota['error'])
            self.assertEqual(quota['limits'][0]['percent'], .3)
            request.assert_not_called()
        self.assertEqual(auth.read_bytes(), before)

    def test_grok_request_errors_do_not_expose_credentials(self):
        home = self.root / 'grok'; home.mkdir()
        (home / 'auth.json').write_text(json.dumps({'account': {'key': 'private-test-key'}}))
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'GROK_HOME': str(home)}), patch.object(c.urllib.request, 'urlopen', side_effect=ValueError('Invalid header: private-test-key')):
            quota = c.grok_quota(True)
            self.assertNotIn('private-test-key', json.dumps(quota))

    def test_grok_login_invalidates_cached_signout(self):
        import io
        home = self.root / 'grok'; home.mkdir()
        auth = home / 'auth.json'
        auth.write_text(json.dumps({'account': {'key': 'old', 'expires_at': '2000-01-01T00:00:00Z'}}))
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'GROK_HOME': str(home)}):
            self.assertIn('expired', c.grok_quota()['error'])
            auth.write_text(json.dumps({'account': {'key': 'new-valid-login'}}))
            with patch.object(c.urllib.request, 'urlopen', return_value=io.BytesIO(b'\x00\x00\x00\x00\x02\x0a\x00')) as request:
                self.assertEqual(c.grok_quota()['error'], '')
                request.assert_called_once()

    def test_existing_ledger_migrates_without_losing_events(self):
        path = self.root / 'old.sqlite'; db = sqlite3.connect(path)
        db.execute('CREATE TABLE events (id TEXT PRIMARY KEY, provider, session, ts, model, project, client, input, output, cacheRead, cacheWrite, cacheWrite1h, reasoning)')
        db.execute("INSERT INTO events VALUES ('old','codex','s',1,'m','','CLI',10,20,0,0,0,0)")
        db.commit(); db.close()
        ledger = c.Ledger(path)
        self.assertEqual(ledger.db.execute('SELECT input,output,reportedCostTicks FROM events').fetchone(), (10, 20, None))
        ledger.db.close()

    def test_grok_bar_keeps_history_visible_during_quiet_week(self):
        ledger = c.Ledger(self.root / 'bar.sqlite')
        ledger.put(c.record('old', 'grok', 's', '2020-01-01T12:00:00Z', 'grok', '', 'Grok Build', input=10))
        with patch.object(c, 'STATE', self.root / 'state'), patch.object(c, 'quota', return_value={'limits': []}):
            c.write_agent_record(ledger, 'grok')
            bar = json.loads((c.STATE.parent / 'agents/usage/grok.json').read_text())
            self.assertTrue(bar['ready'])
            self.assertEqual((bar['totalSessions'], bar['totalPrompts'], bar['activeDays']), (1, 1, 1))
            self.assertEqual(bar['todayTotalTokens'], 0)
        ledger.db.close()

    def test_cache_price_and_long_context(self):
        r = c.record('1', 'codex', 's', 1, 'm', '', 'CLI', input=200000, cacheRead=100000, output=10)
        catalog = {'m': {'input_cost_per_token': 1e-6, 'input_cost_per_token_above_272k_tokens': 2e-6,
                 'output_cost_per_token': 3e-6, 'cache_read_input_token_cost': 0.1e-6}}
        self.assertAlmostEqual(c.price(r, catalog)[0], 0.41003)

    def test_periods_and_provider_filters(self):
        ledger = c.Ledger(self.root / 'test.sqlite')
        now = dt.datetime(2026, 9, 5, 12).astimezone()
        for key, provider, date in [('a', 'codex', '2026-09-05T10:00:00'), ('b', 'claude', '2026-09-04T10:00:00'),
                                    ('c', 'codex', '2026-08-29T10:00:00')]:
            ledger.put(c.record(key, provider, key, date, 'unknown', '', 'CLI', input=100))
        with patch.object(c, 'load_rates', return_value={'document': {}, 'source': 'test'}):
            report = c.report(ledger, c.DEFAULTS, 7, now=now)
            self.assertEqual(report['summary']['tokens'], 200)
            self.assertEqual(report['previous']['tokens'], 100)
            self.assertEqual(report['summary']['unpricedTokens'], 200)
            self.assertEqual(c.report(ledger, c.DEFAULTS, 7, 'codex', now)['summary']['tokens'], 100)
        ledger.db.close()

    def test_go_scanner_excludes_other_providers_and_counts_reasoning(self):
        data_dir = self.root / 'data'; path = data_dir / 'opencode/opencode.db'
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.executescript('CREATE TABLE message(id,session_id,time_created,data); CREATE TABLE session(id,directory);')
        db.execute('INSERT INTO session VALUES (?,?)', ('s', '/project'))
        for provider in ['opencode-go', 'openai', 'anthropic', 'openrouter']:
            data = {'role': 'assistant', 'providerID': provider, 'modelID': 'm',
                    'tokens': {'input': 10, 'output': 20, 'reasoning': 5, 'cache': {'read': 30, 'write': 0}}}
            db.execute('INSERT INTO message VALUES (?,?,?,?)', (provider, 's', 1788580000000, json.dumps(data)))
        db.commit(); db.close()
        ledger = c.Ledger(self.root / 'ledger.sqlite')
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {'XDG_DATA_HOME': str(data_dir),
              'CODEX_HOME': str(self.root / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude')}):
            ledger.scan(c.DEFAULTS)
            ledger.scan(c.DEFAULTS)
        self.assertEqual(ledger.db.execute("SELECT COUNT(*),SUM(output),SUM(cacheRead) FROM events WHERE provider='opencode-go'").fetchone(), (1, 25, 30))
        self.assertEqual(ledger.db.execute("SELECT COUNT(*),SUM(output) FROM events WHERE provider='opencode'").fetchone(), (3, 75))
        ledger.db.close()

    def test_gemini_migration_rewind_and_stream_updates(self):
        message = {'id': 'gemini-msg', 'type': 'gemini', 'timestamp': '2026-09-04T12:00:00Z',
                   'model': 'gemini-3.8-flash', 'tokens': {'input': 100, 'cached': 70, 'output': 20, 'thoughts': 10, 'total': 130}}
        legacy = self.root / 'session-old.json'
        legacy.write_text(json.dumps({'sessionId': 's', 'projectHash': 'project', 'messages': [message]}))
        modern = self.transcript('session-new.jsonl', [{'sessionId': 's', 'projectHash': 'project'},
            message | {'tokens': None}, message, {'$rewindTo': 'gemini-msg'}, {'$set': {'messages': [message]}}])
        ledger = c.Ledger(self.root / 'gemini.sqlite')
        for path in [legacy, modern]:
            for record in c.gemini_records(path): ledger.put(record)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*),SUM(input),SUM(output),SUM(cacheRead),SUM(reasoning) FROM events').fetchone(), (1, 30, 30, 70, 10))
        ledger.db.close()

    def test_gemini_scans_nested_subagents_and_additional_home(self):
        root = self.root / 'gemini'
        message = {'id': 'subagent-message', 'type': 'gemini', 'timestamp': 1788580000,
                   'tokens': {'input': 100, 'output': 20, 'thoughts': 10, 'tool': 5, 'total': 135}}
        self.transcript('gemini/tmp/project/chats/parent/agent.jsonl', [{'sessionId': 'subagent', 'projectHash': 'p'}, message])
        ledger = c.Ledger(self.root / 'scan.sqlite')
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
            'CODEX_HOME': str(self.root / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'GROK_HOME': str(self.root / 'grok'), 'PI_CODING_AGENT_DIR': str(self.root / 'pi'), 'XDG_DATA_HOME': str(self.root / 'data')}):
            ledger.scan(c.DEFAULTS | {'geminiHomes': [str(root)]})
            ledger.scan(c.DEFAULTS | {'geminiHomes': [str(root)]})
        self.assertEqual(ledger.db.execute("SELECT COUNT(*),SUM(input+output) FROM events WHERE provider='gemini'").fetchone(), (1, 135))
        ledger.db.close()

    def cursor_fixture(self):
        path = Path(__file__).parent / 'fixtures/cursor-api.sample.json'
        return json.loads(path.read_text())['usageEventsDisplay']

    def test_cursor_fixture_contract_and_total_parser(self):
        events = self.cursor_fixture()
        self.assertEqual(len(events), 3)
        for ev in events:
            extra = dict(ev, futureUnknownKey={'nested': [1]})
            r = c.cursor_api_record(extra)
            self.assertEqual(r['provider'], 'cursor')
            self.assertTrue(r['ts'] > 0)
        # Degenerate inputs never raise; unparsable timestamps yield ts 0,
        # which put() drops.
        for bad in ({}, {'tokenUsage': None}, {'timestamp': 'not-a-time'}, {'tokenUsage': {'totalCents': 'nan-x'}}):
            self.assertEqual(c.cursor_api_record(bad)['ts'], 0)

    def test_cursor_record_mapping_tokens_cost_and_client(self):
        first, free, twin = self.cursor_fixture()
        r = c.cursor_api_record(first)
        self.assertEqual((r['input'], r['output'], r['cacheRead']), (300, 2, 205056))
        self.assertEqual(r['ts'], 1788815371)
        self.assertAlmostEqual(r['reportedValue'], 0.206292)
        self.assertEqual((r['turns'], r['model'], r['client'], r['session']), (1, 'synth-model-a', 'Cloud', 'synth-conv-1'))
        f = c.cursor_api_record(free)
        self.assertEqual(f['reportedValue'], 0.0)
        self.assertIsNotNone(f['reportedValue'])
        self.assertEqual((f['client'], f['session']), ('Cursor', 'cloud'))
        # Same millisecond, different tokens: distinct records.
        self.assertNotEqual(r['id'], c.cursor_api_record(twin)['id'])
        # Refetch stability across int/str/float ms forms.
        alt = dict(twin, timestamp=1788815371513.0)
        self.assertEqual(c.cursor_api_record(twin)['id'], c.cursor_api_record(alt)['id'])
        # None and '' conversation ids canonicalize to the same record.
        blank = c.cursor_api_record(dict(twin, conversationId=''))
        none = c.cursor_api_record(dict(twin, conversationId=None))
        self.assertEqual(blank['id'], none['id'])
        self.assertEqual((blank['session'], none['session']), ('cloud', 'cloud'))

    def test_cursor_event_ms_coercion(self):
        for form in ('1788815371513', 1788815371513, '1788815371513.0', 1788815371513.0):
            self.assertEqual(c.event_ms(form), 1788815371513)
        self.assertEqual(c.event_ms('2026-08-24T22:25:30Z'), 1787610330000)
        self.assertEqual(c.event_ms('garbage'), 0)

    def test_cursor_price_uses_reported_value_including_zero(self):
        ledger = c.Ledger(self.root / 'price.sqlite')
        for ev in self.cursor_fixture(): ledger.put(c.cursor_api_record(ev))
        ledger.db.row_factory = sqlite3.Row
        values = sorted(round(c.price(dict(row), {})[0], 6) for row in ledger.db.execute('SELECT * FROM events'))
        self.assertEqual(values, [0.0, 0.035, 0.206292])
        ledger.db.close()

    def test_cursor_walk_stops_on_short_page_and_boundary_repeat(self):
        first, _, _ = self.cursor_fixture()
        calls = []
        full = {'usageEventsDisplay': [first] * 1000}
        pages = [full, {'usageEventsDisplay': [first]}]
        def fake_post(token, method, body):
            calls.append((method, dict(body)))
            return pages[min(len(calls) - 1, 1)]
        with patch.object(c, 'cursor_post', fake_post):
            records, oldest, complete = c.cursor_walk('tok')
        self.assertTrue(complete)
        self.assertEqual(len(records), 1)
        self.assertEqual(oldest, 1788815371513)
        # The full first page keeps walking with an exclusive bound; the
        # repeated boundary row then yields zero new ids and halts the walk.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]['endDate'], 1788815371512)

    def test_cursor_walk_short_first_page_commits(self):
        first, _, _ = self.cursor_fixture()
        with patch.object(c, 'cursor_post', return_value={'usageEventsDisplay': [first]}):
            records, oldest, complete = c.cursor_walk('tok')
        self.assertTrue(complete)
        self.assertEqual(len(records), 1)
        self.assertEqual(oldest, 1788815371513)

    def test_cursor_walk_retries_smaller_page_on_400(self):
        seen = []
        def fake_post(token, method, body):
            seen.append(body.get('pageSize'))
            if body.get('pageSize') == 1000:
                raise _HTTPError(400)
            return {'usageEventsDisplay': []}
        with patch.object(c, 'cursor_post', fake_post):
            records, _, complete = c.cursor_walk('tok')
        self.assertTrue(complete)
        self.assertEqual(records, [])
        self.assertEqual(seen, [1000, 100])

    def test_cursor_walk_failure_writes_nothing_and_keeps_cache(self):
        ledger = c.Ledger(self.root / 'fail.sqlite')
        ledger.put(c.record('old', 'cursor', 's', 1788810000, 'm', '', 'Cursor', input=1), 'cursor-api')
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value='tok'), \
             patch.object(c, 'cursor_summary', return_value={'billingCycleStart': '1786479485000'}), \
             patch.object(c, 'cursor_walk', side_effect=c.CursorApiUnavailable('down')):
            source, warnings = c.cursor_usage(ledger, force=True)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        self.assertEqual(source.get('readErrors'), 1)
        self.assertTrue(warnings)
        cached = json.loads((self.root / 'state/cursor-usage.json').read_text())
        self.assertNotIn('newestTs', cached)
        ledger.db.close()

    def test_cursor_purge_removes_legacy_rows_on_success(self):
        ledger = c.Ledger(self.root / 'purge.sqlite')
        legacy = c.record('legacy-1', 'cursor', 's', 1788810000, 'unknown', '', 'Cursor')
        legacy['turns'] = 1
        ledger.put(legacy, '/home/u/.config/Cursor/User/globalStorage/state.vscdb')
        first, _, _ = self.cursor_fixture()
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value='tok'), \
             patch.object(c, 'cursor_summary', return_value={'billingCycleStart': '1786479485000'}), \
             patch.object(c, 'cursor_walk', return_value=([c.cursor_api_record(first)], 1788815371513, True)):
            source, warnings = c.cursor_usage(ledger, force=True)
        self.assertEqual(warnings, [])
        rows = ledger.db.execute("SELECT id FROM events WHERE provider='cursor'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], 'legacy-1')
        self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM event_sources WHERE path LIKE '%state.vscdb'").fetchone()[0], 0)
        ledger.db.close()

    def test_cursor_empty_success_keeps_legacy_rows(self):
        ledger = c.Ledger(self.root / 'empty.sqlite')
        legacy = c.record('legacy-1', 'cursor', 's', 1788810000, 'unknown', '', 'Cursor')
        legacy['turns'] = 1
        ledger.put(legacy, '/home/u/.config/Cursor/User/globalStorage/state.vscdb')
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value='tok'), \
             patch.object(c, 'cursor_summary', return_value={'billingCycleStart': '1786479485000'}), \
             patch.object(c, 'cursor_walk', return_value=([], 1788815371513, True)):
            source, warnings = c.cursor_usage(ledger, force=True)
        self.assertEqual(warnings, [])
        self.assertNotIn('readErrors', source)
        self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM events WHERE provider='cursor'").fetchone()[0], 1)
        ledger.db.close()

    def test_cursor_resume_walks_past_stop_and_tops_up(self):
        first, _, twin = self.cursor_fixture()
        new = dict(first, timestamp='1788829289000', conversationId='new-conv')
        ledger = c.Ledger(self.root / 'resume.sqlite')
        calls = []
        def fake_walk(token, end=None, stop_ms=0, cycle_ms=0):
            calls.append((end, stop_ms))
            if end is None:
                return ([c.cursor_api_record(new)], None, True)
            # History pages all older than the cached newest marker: the
            # resume walk must not stop on the incremental bound.
            self.assertEqual(stop_ms, 0)
            return ([c.cursor_api_record(first)], 1788815371513, True)
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value='tok'), \
             patch.object(c, 'cursor_summary', return_value={'billingCycleStart': '1786479485000'}), \
             patch.object(c, 'cursor_walk', fake_walk):
            (self.root / 'state').mkdir(parents=True, exist_ok=True)
            (self.root / 'state/cursor-usage.json').write_text(json.dumps(
                {'attemptedAt': 0, 'billingCycleStart': 1786479485000, 'newestTs': 1788815371513,
                 'fullPullPending': True, 'resumeFloorMs': 1788815371514, 'error': ''}))
            source, warnings = c.cursor_usage(ledger, force=True)
        self.assertEqual(warnings, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)
        cached = json.loads((self.root / 'state/cursor-usage.json').read_text())
        self.assertFalse(cached['fullPullPending'])
        self.assertEqual(cached['newestTs'], 1788829289 * 1000)
        ledger.db.close()

    def test_main_scan_auto_enable_collects_found_providers(self):
        seed = c.Ledger(self.root / 'state/usage.sqlite')
        legacy = c.record('legacy-1', 'cursor', 's', 1788810000, 'unknown', '', 'Cursor')
        legacy['turns'] = 1
        seed.put(legacy, '/home/u/.config/Cursor/User/globalStorage/state.vscdb')
        seed.db.commit()
        seed.db.close()
        used = []
        def fake_usage(ld, force=False):
            used.append(True)
            return ({'provider': 'cursor', 'path': 'cursor-api', 'files': 0, 'exists': True, 'kind': 'cloud'}, [])
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'CONFIG', self.root / 'missing-settings.json'), \
             patch.object(c, 'HOME', self.root), \
             patch.object(c, 'cursor_usage', fake_usage), \
             patch.object(c, 'cursor_quota', return_value={}), \
             patch.object(c, 'go_quota', return_value={}), \
             patch.object(c, 'grok_quota', return_value={}), \
             patch('sys.argv', ['collector.py', 'scan']):
            c.main()
        self.assertTrue(used)

    def test_cursor_token_missing_warns_and_skips_network(self):
        ledger = c.Ledger(self.root / 'notoken.sqlite')
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value=None), \
             patch.object(c, 'cursor_post', side_effect=AssertionError('no network')):
            source, warnings = c.cursor_usage(ledger, force=True)
        self.assertEqual(source.get('readErrors'), 1)
        self.assertTrue(any('sign in' in w.lower() for w in warnings))
        ledger.db.close()

    def test_cursor_scan_runs_usage_when_enabled(self):
        ledger = c.Ledger(self.root / 'scan.sqlite')
        seen = []
        def fake_usage(ld, force=False):
            seen.append(ld is ledger)
            return ({'provider': 'cursor', 'path': 'cursor-api', 'files': 0, 'exists': True, 'kind': 'cloud'}, [])
        with patch.object(c, 'cursor_usage', fake_usage):
            meta = ledger.scan(c.DEFAULTS | {'enabled': ['cursor']})
        self.assertTrue(seen)
        kinds = [s for s in meta['sources'] if s['provider'] == 'cursor']
        self.assertEqual(kinds[0]['status'], 'available')
        ledger.db.close()

    def test_cursor_quota_maps_summary_and_errors(self):
        summary = {'billingCycleStart': '1786479485000', 'billingCycleEnd': '1789157885000',
                   'planUsage': {'totalPercentUsed': 100}, 'displayMessage': "You've hit your usage limit"}
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value='tok'), \
             patch.object(c, 'cursor_summary', return_value=summary):
            got = c.cursor_quota(force=True)
            self.assertEqual(c.quota('cursor')['limits'], got['limits'])
        self.assertEqual(got['limits'][0]['label'], 'Billing cycle')
        self.assertEqual(got['limits'][0]['percent'], 1.0)
        self.assertTrue(got['limits'][0]['resetsAt'].startswith('2026-'))
        self.assertIn('usage limit', got['error'])
        with patch.object(c, 'STATE', self.root / 'state'), \
             patch.object(c, 'cursor_token', return_value=None):
            denied = c.cursor_quota(force=True)
        self.assertTrue(denied['error'])
        self.assertIn('expired', c.cursor_api_error(_HTTPError(401)))
        self.assertIn('unavailable', c.cursor_api_error(ValueError('x')))

    def test_migration_adds_turns_and_put_keeps_only_turns(self):
        ledger = c.Ledger(self.root / 'mig.sqlite')
        ledger.db.execute('ALTER TABLE events DROP COLUMN turns')
        ledger.db.commit()
        ledger.db.close()
        fresh = c.Ledger(self.root / 'mig.sqlite')
        columns = {r[1] for r in fresh.db.execute('PRAGMA table_info(events)')}
        self.assertIn('turns', columns)
        empty = c.record('x', 'codex', 's', 1, 'm', '', 'CLI')
        fresh.put(empty)
        turn = dict(empty, id='y', provider='cursor', turns=1)
        fresh.put(turn)
        self.assertEqual(fresh.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        fresh.db.close()

    def test_pi_and_omp_copied_branches_preserve_spend(self):
        entry = {'type': 'message', 'id': 'short-id', 'timestamp': '2026-09-04T12:00:00Z',
                 'message': {'role': 'assistant', 'model': 'custom', 'provider': 'openrouter',
                   'usage': {'input': 10, 'output': 20, 'reasoning': 5, 'cacheRead': 30,
                             'cacheWrite': 5, 'cacheWrite1h': 2, 'cost': {'total': .25}}}}
        original = self.transcript('pi-original.jsonl', [{'type': 'session', 'id': 's', 'cwd': '/project'}, entry])
        fork = self.transcript('pi-fork.jsonl', [{'type': 'session', 'id': 'fork', 'cwd': '/project'}, entry])
        ledger = c.Ledger(self.root / 'pi.sqlite')
        for source in ['pi', 'omp']:
            for path in [original, fork]:
                for r in c.pi_records(path, source): ledger.put(r)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*),SUM(input+output+cacheRead+cacheWrite) FROM events').fetchone(), (2, 130))
        ledger.db.row_factory = sqlite3.Row
        for row in ledger.db.execute('SELECT * FROM events'):
            self.assertEqual(c.price(dict(row), {}), (.25, None))
        ledger.db.close()

    def test_opencode_routes_and_logged_cost(self):
        ledger = c.Ledger(self.root / 'routes.sqlite')
        for route in ['opencode-go', 'openrouter', 'anthropic']:
            ledger.put(c.opencode_record(route, 's', '2026-09-04T12:00:00Z', '/project', 'custom', route,
                                        {'input': 10, 'output': 20, 'reasoning': 5}, .2))
        cfg = c.DEFAULTS | {'enabled': ['opencode-go', 'opencode']}
        report = c.report(ledger, cfg, 7, now=dt.datetime(2026, 9, 5).astimezone())
        self.assertEqual(report['summary']['tokens'], 105)
        self.assertEqual(report['summary']['unpricedTokens'], 35)
        self.assertAlmostEqual(report['summary']['value'], .4)
        filtered = c.report(ledger, cfg, 7, now=dt.datetime(2026, 9, 5).astimezone(), selection={'apiProvider': 'openrouter'})
        self.assertEqual(filtered['summary']['tokens'], 35)
        self.assertEqual(len(report['routes']), 3)
        ledger.db.close()

    def test_expanded_settings_keep_prices_and_home_lists(self):
        with patch.object(c, 'CONFIG', self.root / 'settings.json'):
            config = c.save_settings(c.DEFAULTS | {'enabled': list(c.PROVIDERS),
                'monthlyPrices': {'codex': 200, 'gemini': 20, 'pi': None}, 'ompHomes': ['/mounted/.omp/agent']})
            self.assertEqual(config['monthlyPrices'], {'codex': 200, 'gemini': 20})
            self.assertEqual(config['ompHomes'], ['/mounted/.omp/agent'])
            self.assertEqual(config['enabled'], list(c.PROVIDERS))

    def test_opencode_legacy_and_database_copies_merge(self):
        root = self.root / 'data/opencode'; root.mkdir(parents=True)
        item = {'id': 'm1', 'sessionID': 's', 'role': 'assistant', 'providerID': 'openrouter',
                'modelID': 'custom', 'time': {'created': 1788580000000}, 'path': {'cwd': '/project'},
                'tokens': {'input': 10, 'output': 20}, 'cost': .25}
        path = root / 'storage/message/s/m1.json'; path.parent.mkdir(parents=True); path.write_text(json.dumps(item))
        db = sqlite3.connect(root / 'opencode.db')
        db.executescript('CREATE TABLE message(id,session_id,time_created,data); CREATE TABLE session(id,directory);')
        db.execute('INSERT INTO session VALUES (?,?)', ('s', '/project'))
        db.execute('INSERT INTO message VALUES (?,?,?,?)', ('m1', 's', 1788580000000, json.dumps(item)))
        db.commit(); db.close()
        ledger = c.Ledger(self.root / 'legacy.sqlite')
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
            'CODEX_HOME': str(self.root / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'GROK_HOME': str(self.root / 'grok'), 'PI_CODING_AGENT_DIR': str(self.root / 'pi'), 'XDG_DATA_HOME': str(self.root / 'data')}):
            ledger.scan(c.DEFAULTS)
            ledger.scan(c.DEFAULTS)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*),SUM(input+output),SUM(reportedValue) FROM events').fetchone(), (1, 30, .25))
        ledger.db.close()

    def test_gemini_catalog_fills_user_catalog_gaps_without_repricing(self):
        with patch.object(c, 'STATE', self.root):
            c.atomic_json(self.root / 'rates.json', {'source': 'custom', 'document': {'custom-model': {'input_cost_per_token': 1}}})
            catalog = c.load_rates()['document']
            self.assertEqual(catalog['custom-model']['input_cost_per_token'], 1)
            record = c.record('g', 'gemini', 's', 1, 'gemini-3.8-flash', '', 'Gemini CLI', input=1000000, output=1000000, cacheRead=1000000)
            self.assertAlmostEqual(c.price(record, catalog)[0], 4.575)

    def test_today_uses_hourly_buckets_without_future_zero_hours(self):
        ledger = c.Ledger(self.root / 'today.sqlite')
        now = dt.datetime(2026, 9, 5, 1, 30).astimezone()
        for key, when, tokens in [('a', '2026-09-05T00:15:00', 100),
                                  ('b', '2026-09-05T01:10:00', 200),
                                  ('prior', '2026-09-04T01:10:00', 50),
                                  ('later-prior', '2026-09-04T02:10:00', 999),
                                  ('future', '2026-09-05T02:10:00', 999)]:
            ledger.put(c.record(key, 'codex', key, when, 'm', '', 'CLI', input=tokens))
        with patch.object(c, 'load_rates', return_value={'document': {}, 'source': 'test'}):
            r = c.report(ledger, c.DEFAULTS, 1, now=now)
        self.assertEqual([h['label'] for h in r['hourly']], ['00:00', '01:00'])
        self.assertEqual([h['providers']['codex']['tokens'] for h in r['hourly']], [100, 200])
        self.assertEqual(r['summary']['tokens'], 300)
        self.assertEqual(r['previous']['tokens'], 50)
        self.assertTrue(r['hourly'][-1]['title'].endswith('to now'))
        ledger.db.close()

    def test_drilldown_reconciles_sessions_and_partial_pricing(self):
        ledger = c.Ledger(self.root / 'detail.sqlite')
        now = dt.datetime(2026, 9, 5, 12).astimezone()
        for key, model, project, inp in [('a', 'known', '/one', 100), ('b', 'unknown', '/one', 200), ('c', 'known', '/two', 300)]:
            ledger.put(c.record(key, 'codex', key, '2026-09-05T10:00:00', model, project, 'CLI', input=inp))
        rates = {'source':'test', 'document':{'known':{'input_cost_per_token':0.01}}}
        with patch.object(c, 'load_rates', return_value=rates):
            whole = c.report(ledger, c.DEFAULTS, 7, now=now)
            detail = c.report(ledger, c.DEFAULTS, 7, now=now, selection={'project':'/one', 'day':'2026-09-05'})
            empty = c.report(ledger, c.DEFAULTS, 7, now=now, selection={'model':'absent'})
        self.assertEqual(whole['summary']['tokens'], sum(s['tokens'] for s in whole['sessions']))
        self.assertEqual(detail['summary']['tokens'], 300)
        self.assertEqual(detail['summary']['value'], 1)
        self.assertEqual(detail['summary']['unpricedTokens'], 200)
        self.assertEqual(detail['pricing']['unpriced'][0]['name'], 'unknown')
        self.assertAlmostEqual(detail['pricing']['coveragePercent'], 100/3)
        self.assertEqual(len(detail['sessions']), 2)
        self.assertEqual(len(detail['hourly']), 13)
        self.assertEqual(sum(h['providers']['codex']['tokens'] for h in detail['hourly']), detail['summary']['tokens'])
        self.assertEqual(empty['sessions'], [])
        self.assertIsNone(empty['pricing']['coveragePercent'])
        ledger.db.close()

    def test_missing_source_does_not_claim_fresh_history(self):
        ledger = c.Ledger(self.root / 'coverage.sqlite')
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
                'XDG_DATA_HOME':str(self.root/'data'), 'CODEX_HOME':str(self.root/'codex'),
                'CLAUDE_CONFIG_DIR':str(self.root/'claude')}):
            meta = ledger.scan(c.DEFAULTS)
        self.assertTrue(meta['scannedAt'])
        self.assertTrue(all(s['status'] == 'missing' for s in meta['sources']))
        self.assertTrue(all(s.get('latestFileAt') is None for s in meta['sources']))
        ledger.db.close()

    def test_bundled_catalog_works_without_t3_or_user_state(self):
        with patch.object(c, 'STATE', self.root / 'state'), patch.object(c, 'HOME', self.root):
            rates=c.load_rates()
        self.assertGreater(len(rates['document']), 100)
        self.assertNotIn('T3', rates['source'])
        record=c.record('x','codex','s',1,'gpt-4.1','','CLI',input=100,output=10)
        self.assertIsNotNone(c.price(record,rates['document'])[0])


if __name__ == '__main__': unittest.main()
