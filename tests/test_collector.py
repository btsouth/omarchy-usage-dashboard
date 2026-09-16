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

    def muse_event(self, usage=None, response='resp_test1', recorded_us=1788791137057784,
                     model='muse-spark-1.3-contributor', session='test-session'):
        event = {'kind': 'model_completed', 'model': model, 'duration_ms': 1000,
                 'usage': usage if usage is not None else {
                     'input_tokens': 42733, 'output_tokens': 184, 'reasoning_tokens': 100,
                     'cache_read_tokens': 29425, 'cache_write_tokens': 0, 'cached_tokens': 29425}}
        if response is not None: event['response_id'] = response
        return {'schema_version': 1, 'id': 'event-id', 'stream': {'kind': 'session', 'id': session},
                'sequence': 56, 'recorded_at': recorded_us, 'record_type': 'event',
                'payload_type': 'runtime.session', 'payload_schema_version': 1,
                'payload': {'kind': 'run', 'run_id': 'run-1', 'event': event}}

    def muse_session(self, name, entries):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for entry in entries:
            if isinstance(entry, list):
                lines.append(json.dumps({'retained_frame': 'session_permission_transaction',
                    'frame_schema_version': 1, 'outer_log_ordinal': 1, 'transaction_id': 't',
                    'children': [{'child_index': i, 'record_json': json.dumps(e)}
                                 for i, e in enumerate(entry)]}))
            else:
                lines.append(json.dumps(entry))
        path.write_text('\n'.join(lines) + '\n')
        return path

    def test_muse_envelope_unwrap_mapping_and_microsecond_ts(self):
        meta = {'schema_version': 1, 'id': 'meta', 'stream': {'kind': 'session', 'id': 's'},
                'sequence': 3, 'recorded_at': 1788791037153195, 'record_type': 'event',
                'payload_type': 'runtime.session.metadata', 'payload_schema_version': 1,
                'payload': {'kind': 'metadata', 'record': {'workspace_root': '/project'}}}
        path = self.muse_session('2026/09/07/s/session.jsonl', [[meta], self.muse_event(session='s')])
        records = list(c.muse_records(path))
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual((r['provider'], r['session'], r['model'], r['project'], r['client']),
                         ('muse', 's', 'muse-spark-1.3-contributor', '/project', 'Muse'))
        self.assertEqual(r['ts'], 1788791137)
        self.assertEqual(r['input'], 42733 - 29425)
        self.assertEqual((r['output'], r['cacheRead'], r['cacheWrite'], r['reasoning']), (184, 29425, 0, 100))
        # Reasoning stays separate from output; the cache spellings are one count.
        self.assertEqual(sum(r[f] for f in c.FIELDS[:4]), 42733 + 184)

    def test_muse_legacy_cached_tokens_spelling(self):
        usage = {'input_tokens': 100, 'output_tokens': 20, 'cached_tokens': 70}
        path = self.muse_session('s/session.jsonl', [self.muse_event(usage=usage)])
        r = next(c.muse_records(path))
        self.assertEqual((r['input'], r['cacheRead']), (30, 70))

    def test_muse_malformed_shapes_do_not_abort_file(self):
        bad_stream = self.muse_event()
        bad_stream['stream'] = 'not-a-dict'
        bad_record = self.muse_event(response='resp_second')
        bad_record['payload'] = {'kind': 'metadata', 'record': ['not', 'a', 'dict']}
        good = self.muse_event(response='resp_third')
        path = self.muse_session('s/session.jsonl', [bad_stream, bad_record, good,
            {'children': [{'record_json': 'not json'}, 'not-a-dict'], 'payload': {'kind': 'run'}}])
        records = list(c.muse_records(path))
        self.assertEqual(len(records), 2)
        self.assertEqual({r['session'] for r in records}, {'s', 'test-session'})

    def test_muse_ignores_attribution_and_unrelated_rows(self):
        def attribution(family, reported):
            return {'schema_version': 1, 'id': family, 'stream': {'kind': 'session', 'id': 's'},
                    'sequence': 57, 'recorded_at': 1788791137052738, 'record_type': 'event',
                    'payload_type': 'runtime.session', 'payload_schema_version': 1,
                    'payload': {'kind': 'run', 'run_id': 'run-1', 'event': {
                        'kind': 'goal_usage_attribution',
                        'record': {'quantity': {'input_tokens': 42733, 'output_tokens': 184,
                                               'reasoning_tokens': 100, 'cached_tokens': 29425,
                                               'reported': reported, 'unit': 'tokens'},
                                   'usage_family': family}}}}
        path = self.muse_session('s/session.jsonl',
            [attribution('provider', True), attribution('tool', False), {'heartbeat': True}])
        self.assertEqual(list(c.muse_records(path)), [])

    def test_muse_copied_response_ids_merge(self):
        entries = [self.muse_event()]
        first = self.muse_session('one/session.jsonl', entries)
        second = self.muse_session('two/session.jsonl', entries)
        ledger = c.Ledger(self.root / 'muse.sqlite')
        for path in [first, second]:
            for r in c.muse_records(path): ledger.put(r)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        ledger.db.close()

    def test_muse_retry_without_response_id_collapses(self):
        small = self.muse_event(response=None, usage={'input_tokens': 10, 'output_tokens': 1})
        grown = self.muse_event(response=None, usage={'input_tokens': 30, 'output_tokens': 3})
        path = self.muse_session('s/session.jsonl', [small, grown])
        ledger = c.Ledger(self.root / 'retry.sqlite')
        for r in c.muse_records(path): ledger.put(r)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*),SUM(input),SUM(output) FROM events').fetchone(), (1, 30, 3))
        ledger.db.close()

    def test_muse_scan_covers_dated_and_subagent_files(self):
        home = self.root / 'muse'
        self.muse_session('muse/sessions/2026/09/07/aaa/session.jsonl', [self.muse_event(session='aaa')])
        self.muse_session('muse/sessions/2026/09/07/aaa/subagent/bbb/session.jsonl',
                          [self.muse_event(session='bbb', response='resp_sub')])
        ledger = c.Ledger(self.root / 'scan.sqlite')
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
                'MUSE_HOME': str(home), 'CODEX_HOME': str(self.root / 'codex'),
                'CLAUDE_CONFIG_DIR': str(self.root / 'claude'), 'GROK_HOME': str(self.root / 'grok'),
                'PI_CODING_AGENT_DIR': str(self.root / 'pi'), 'XDG_DATA_HOME': str(self.root / 'data')}):
            ledger.scan(c.DEFAULTS)
            ledger.scan(c.DEFAULTS)
        rows = ledger.db.execute("SELECT session,input FROM events WHERE provider='muse' ORDER BY session").fetchall()
        self.assertEqual([r[0] for r in rows], ['aaa', 'bbb'])
        ledger.db.close()

    def muse_auth(self, config_home, token='dca:test-access-token-secret'):
        home = self.root / config_home
        home.mkdir(parents=True, exist_ok=True)
        (home / 'muse/auth.json').parent.mkdir(parents=True, exist_ok=True)
        (home / 'muse/auth.json').write_text(json.dumps(
            {'schema_version': 1, 'providers': {'meta': {
                'access_token': token, 'api_base_url': 'https://api.meta.ai/v1/',
                'api_key': 'LLM|test-minted-key-secret', 'mechanism': 'oauth',
                'obtained_via': 'device_code'}}}))
        return home

    def minted_key(self, usage='default'):
        if usage == 'default':
            usage = {'window': {'used_percent': 16, 'window_duration_mins': 300, 'resets_at': 1788791137},
                     'weekly': {'used_percent': 14, 'resets_at': 1789344000}, 'tier': '1'}
        return {'api_key': 'LLM|test-minted-key-secret', 'subs_tier_name': 'Muse Code Power Usage',
                'subs_usage': usage}

    def muse_urlopen(self, minted):
        import io
        def fake(request, timeout=12):
            assert request.full_url == 'https://api.meta.ai/muse-code/key', request.full_url
            assert request.get_method() == 'POST'
            assert 'dca:test-access-token-secret' in request.get_header('Authorization')
            return io.BytesIO(json.dumps(minted).encode())
        return fake

    def test_muse_quota_maps_windows_tier_and_hides_key_material(self):
        config = self.muse_auth('config')
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'XDG_CONFIG_HOME': str(config)}):
            with patch.object(c.urllib.request, 'urlopen', side_effect=self.muse_urlopen(self.minted_key())) as request:
                quota = c.muse_quota(True)
                self.assertEqual(request.call_count, 1)
            self.assertEqual(quota['error'], '')
            self.assertEqual(quota['plan'], 'Muse Code Power Usage')
            by_label = {w['label']: w for w in quota['limits']}
            self.assertAlmostEqual(by_label['Session (5-hour)']['percent'], .16)
            self.assertAlmostEqual(by_label['Weekly (7-day)']['percent'], .14)
            self.assertEqual(by_label['Weekly (7-day)']['resetsAt'],
                             dt.datetime.fromtimestamp(1789344000, dt.timezone.utc).isoformat())
            raw = (c.STATE / 'muse-quota.json').read_text()
            self.assertNotIn('dca:test-access-token-secret', raw)
            self.assertNotIn('LLM|test-minted-key-secret', raw)
            self.assertNotIn('dca:test-access-token-secret', json.dumps(quota))
            self.assertEqual(c.quota('muse')['plan'], 'Muse Code Power Usage')

    def test_muse_quota_missing_auth_reports_login_and_keeps_stale(self):
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root / 'empty')}), patch.object(c.urllib.request, 'urlopen') as request:
            c.atomic_json(c.STATE / 'muse-quota.json', {'limits': [{'label': 'Weekly (7-day)', 'percent': .1}], 'updatedAt': 'old'})
            quota = c.muse_quota(True)
            self.assertIn('login', quota['error'])
            self.assertEqual(quota['limits'][0]['percent'], .1)
            request.assert_not_called()

    def test_muse_quota_errors_do_not_expose_credentials(self):
        import io
        config = self.muse_auth('config')
        def failing(request, timeout=12):
            raise c.urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'bad dca:test-access-token-secret'))
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'XDG_CONFIG_HOME': str(config)}), patch.object(c.urllib.request, 'urlopen', side_effect=failing):
            quota = c.muse_quota(True)
            self.assertNotIn('dca:test-access-token-secret', json.dumps(quota))
            self.assertNotIn('LLM|test-minted-key-secret', json.dumps(quota))
            with patch.object(c.urllib.request, 'urlopen', side_effect=self.muse_urlopen({'subs_usage': {}})):
                quota = c.muse_quota(True)
                self.assertIn('quota', quota['error'])
                self.assertNotIn('dca:test-access-token-secret', json.dumps(quota))

    def test_muse_login_invalidates_cached_error(self):
        config = self.muse_auth('config')
        auth = config / 'muse/auth.json'
        with patch.object(c, 'STATE', self.root / 'state'), patch.dict('os.environ', {'XDG_CONFIG_HOME': str(config)}):
            c.atomic_json(c.STATE / 'muse-quota.json', {'error': 'stale', 'attemptedAt': 0, 'authVersion': None})
            with patch.object(c.urllib.request, 'urlopen', side_effect=self.muse_urlopen(self.minted_key())) as request:
                self.assertEqual(c.muse_quota()['error'], '')
                self.assertEqual(request.call_count, 1)
                # A second call inside the throttle window reuses the cache.
                self.assertEqual(c.muse_quota()['error'], '')
                self.assertEqual(request.call_count, 1)
                # Re-login (changed auth file) bypasses the throttle.
                auth.write_text(auth.read_text() + ' ')
                self.assertEqual(c.muse_quota()['error'], '')
                self.assertEqual(request.call_count, 2)

    def test_muse_scan_refreshes_quota_before_record(self):
        import contextlib
        import io as stdlib_io
        import sys
        home = self.root / 'musehome'
        (home / 'sessions').mkdir(parents=True)
        config = self.muse_auth('config')
        with patch.object(c, 'STATE', self.root / 'state'), patch.object(c, 'CONFIG', self.root / 'settings.json'), patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
                'MUSE_HOME': str(home), 'XDG_CONFIG_HOME': str(config), 'XDG_DATA_HOME': str(self.root / 'data'),
                'CODEX_HOME': str(self.root / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
                'GROK_HOME': str(self.root / 'grok'), 'PI_CODING_AGENT_DIR': str(self.root / 'pi')}), patch.object(
                c.urllib.request, 'urlopen', side_effect=self.muse_urlopen(self.minted_key())), patch.object(
                sys, 'argv', ['collector.py', 'scan']):
            c.save_settings(c.DEFAULTS | {'enabled': ['muse']})
            with contextlib.redirect_stdout(stdlib_io.StringIO()):
                c.main()
            record = json.loads((c.STATE.parent / 'agents/usage/muse.json').read_text())
            self.assertAlmostEqual(record['limits'][1]['percent'], .14)
            self.assertEqual(record['tierLabel'], 'Muse Code Power Usage')
            quota_file = json.loads((c.STATE / 'muse-quota.json').read_text())
            self.assertEqual(quota_file['limits'], record['limits'])

    def test_muse_settings_round_trip_keeps_home(self):
        with patch.object(c, 'CONFIG', self.root / 'settings.json'):
            config = c.save_settings(c.DEFAULTS | {'museHomes': ['/mounted/.local/share/muse']})
            self.assertEqual(config['museHomes'], ['/mounted/.local/share/muse'])
            again = c.save_settings(config)
            self.assertEqual(again['museHomes'], ['/mounted/.local/share/muse'])

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
        # Go keeps the app's recorded estimate as a fallback for unlisted models.
        self.assertEqual(report['summary']['unpricedTokens'], 0)
        self.assertAlmostEqual(report['summary']['value'], .6)
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

    def test_account_prices_save_and_unknown_price_keys_drop(self):
        with patch.object(c, 'CONFIG', self.root / 'settings.json'):
            config = c.save_settings(c.DEFAULTS | {'enabled': ['codex'], 'monthlyPrices': {'codex': 200, 'work': 20, 'ghost': 5},
                'accounts': [{'id': 'work', 'label': 'Work', 'directories': [{'provider': 'codex', 'path': '/work'}]}]})
            self.assertEqual(config['monthlyPrices'], {'codex': 200, 'work': 20})

    def test_supplemental_go_rates_price_popular_models(self):
        with patch.object(c, 'STATE', self.root):
            rates = c.load_rates()['document']
        for model, expected in [('glm-5.3', 1.4 + 4.4 + 0.26),
                                ('deepseek-v4.1-flash', 0.15 + 0.6 + 0.003)]:
            with self.subTest(model=model):
                rec = c.record('x', 'opencode-go', 's', '2026-09-04T12:00:00Z', model, '/p', 'OpenCode',
                               input=1_000_000, output=1_000_000, cacheRead=1_000_000)
                self.assertAlmostEqual(c.price(rec, rates)[0], expected, places=9)
        free = c.record('x', 'opencode-go', 's', '2026-09-04T12:00:00Z', 'ox-alpha-free', '/p', 'OpenCode', input=1000)
        self.assertEqual(c.price(free, rates)[0], 0)

    def test_catalog_rates_still_win_over_recorded_go_cost(self):
        with patch.object(c, 'STATE', self.root):
            rates = c.load_rates()['document']
        rec = c.record('x', 'opencode-go', 's', '2026-09-04T12:00:00Z', 'glm-5.3-flash', '/p', 'OpenCode', input=1_000_000)
        rec['reportedValue'] = 99.0
        self.assertAlmostEqual(c.price(rec, rates)[0], 0.15, places=9)

    def test_go_peak_hours_double_deepseek_rates(self):
        with patch.object(c, 'STATE', self.root):
            rates = c.load_rates()['document']
        def value(ts):
            rec = c.record('x', 'opencode-go', 's', ts, 'deepseek-v4.1-flash', '/p', 'OpenCode',
                           input=1_000_000, output=1_000_000, cacheRead=1_000_000)
            return c.price(rec, rates)[0]
        off_peak = value('2026-09-08T15:00:00Z')
        peak = value('2026-09-08T02:00:00Z')
        weekend = value('2026-09-12T02:00:00Z')
        self.assertAlmostEqual(off_peak, 0.15 + 0.6 + 0.003, places=9)
        self.assertAlmostEqual(peak, off_peak * 2, places=9)
        self.assertAlmostEqual(weekend, off_peak, places=9)

    def test_internal_codex_model_is_zero_and_not_unpriced(self):
        with patch.object(c, 'STATE', self.root):
            rates = c.load_rates()['document']
        auto_review = c.record('x', 'codex', 's', '2026-09-04T12:00:00Z', 'codex-auto-review', '/p', 'CLI', input=1000)
        self.assertEqual(c.price(auto_review, rates), (0.0, None))
        spark = c.record('x', 'codex', 's', '2026-09-04T12:00:00Z', 'gpt-5.3-codex-spark', '/p', 'CLI', input=1_000_000)
        self.assertAlmostEqual(c.price(spark, rates)[0], 1.75, places=9)

    def test_go_allowance_uses_monthly_window_and_promo(self):
        ledger = c.Ledger(self.root / 'allowance.sqlite')
        ledger.put(c.opencode_record('m1', 's', '2026-09-14T12:00:00Z', '/p', 'deepseek-v4.1-flash', 'opencode-go', {'input': 1_000_000}, 0))
        ledger.put(c.opencode_record('m2', 's', '2026-09-02T12:00:00Z', '/p', 'glm-5.3-flash', 'opencode-go', {'input': 1_000_000}, 0))
        ledger.put(c.opencode_record('m3', 's', '2026-08-01T12:00:00Z', '/p', 'glm-5.3-flash', 'opencode-go', {'input': 9_000_000}, 0))
        rates = {'source': 'test', 'document': {
            'opencode-go/deepseek-v4.1-flash': {'input_cost_per_token': .000001, 'output_cost_per_token': 0, 'cache_read_input_token_cost': 0,
                                                'monthly_limit_usd': 15, 'monthly_limit_promo_usd': 60, 'promo_ends': '2026-09-20'},
            'opencode-go/glm-5.3-flash': {'input_cost_per_token': .000002, 'output_cost_per_token': 0, 'cache_read_input_token_cost': 0,
                                          'monthly_limit_usd': 60}}}
        cfg = c.DEFAULTS | {'enabled': ['opencode-go']}
        with patch.object(c, 'load_rates', return_value=rates), patch.object(c, 'theme', return_value={}), \
             patch.object(c, 'quota', return_value={'limits': [{'label': 'Monthly', 'resetsAt': '2026-10-01T00:00:00+00:00'}]}):
            data = c.report(ledger, cfg, 7, now=dt.datetime(2026, 9, 15).astimezone())
        allowance = {row['model']: row for row in data['goAllowance']['models']}
        self.assertEqual(sorted(allowance), ['deepseek-v4.1-flash', 'glm-5.3-flash'])
        self.assertAlmostEqual(allowance['deepseek-v4.1-flash']['value'], 1.0)
        self.assertEqual((allowance['deepseek-v4.1-flash']['limit'], allowance['deepseek-v4.1-flash']['promo']), (60, True))
        self.assertAlmostEqual(allowance['glm-5.3-flash']['value'], 2.0)
        self.assertEqual((allowance['glm-5.3-flash']['limit'], allowance['glm-5.3-flash']['promo']), (60, False))
        ledger.db.close()

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

    def test_muse_contributor_and_standard_rates(self):
        with patch.object(c, 'STATE', self.root / 'state'), patch.object(c, 'HOME', self.root):
            catalog = c.load_rates()['document']
            contributor = c.record('m', 'muse', 's', 1, 'muse-spark-1.3-contributor', '', 'Muse',
                                   input=1000000, output=1000000, cacheRead=1000000)
            self.assertAlmostEqual(c.price(contributor, catalog)[0], 0.1 + 0.2 + 0.002)
            standard = c.record('m', 'muse', 's', 1, 'muse-spark-1.3', '', 'Muse',
                                input=1000000, output=1000000, cacheRead=1000000)
            self.assertAlmostEqual(c.price(standard, catalog)[0], 1.25 + 4.25 + 0.15)
            self.assertIn('Muse official rates', c.load_rates()['source'])

    def test_muse_unknown_model_and_cache_write_stay_unpriced(self):
        with patch.object(c, 'STATE', self.root / 'state'), patch.object(c, 'HOME', self.root):
            catalog = c.load_rates()['document']
            unknown = c.record('m', 'muse', 's', 1, 'muse-spark-9', '', 'Muse', input=100)
            self.assertEqual(c.price(unknown, catalog), (None, None))
            # No published cache-write rate exists for Muse models.
            written = c.record('m', 'muse', 's', 1, 'muse-spark-1.3-contributor', '', 'Muse',
                               input=100, cacheWrite=5)
            self.assertEqual(c.price(written, catalog), (None, None))

    def test_user_catalog_overrides_official_rates(self):
        with patch.object(c, 'STATE', self.root / 'state'):
            c.atomic_json(self.root / 'state/rates.json', {'source': 'custom', 'document': {
                'muse/muse-spark-1.3-contributor': {'input_cost_per_token': 1},
                'opencode-go/glm-5.3-flash': {'input_cost_per_token': 2}}})
            catalog = c.load_rates()['document']
            self.assertEqual(catalog['muse/muse-spark-1.3-contributor']['input_cost_per_token'], 1)
            self.assertEqual(catalog['opencode-go/glm-5.3-flash']['input_cost_per_token'], 2)
            self.assertIn('custom', c.load_rates()['source'])

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
