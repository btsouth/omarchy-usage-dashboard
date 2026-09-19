import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for patcher in (patch.object(c, 'STATE', self.root / 'state'),
                        patch.object(c, 'CONFIG', self.root / 'settings.json'),
                        patch.object(c, 'HOME', self.root),
                        patch.dict(os.environ, {key: str(self.root / key.lower()) for key in (
                            'HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME',
                            'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'GROK_HOME', 'PI_CODING_AGENT_DIR', 'MUSE_HOME', 'CURSOR_HOME')})):
            patcher.start(); self.addCleanup(patcher.stop)
        self.ledger = c.Ledger(self.root / 'usage.sqlite')
        self.now = dt.datetime(2026, 9, 5, 18).astimezone()
        self.cfg = c.DEFAULTS | {'enabled': ['codex'], 'accounts': [
            {'id': 'work', 'label': 'Work', 'directories': [{'provider': 'codex', 'path': str(self.root / 'work')}]},
            {'id': 'personal', 'label': 'Personal', 'directories': [{'provider': 'codex', 'path': str(self.root / 'personal')}]}]}

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def add(self, key, folder, tokens=100, session=None):
        self.ledger.put(c.record(key, 'codex', session or key, self.now.isoformat(), 'gpt-4.1', '/project', 'CLI', input=tokens), self.root / folder / 'sessions/log.jsonl')

    def report(self, account=None, days=7):
        with patch.object(c, 'quota', return_value={'limits': [{'label': 'Local quota'}]}), \
             patch.object(c, 'account_quotas', return_value=({}, {})), patch.object(c, 'theme', return_value={}):
            return c.report(self.ledger, self.cfg, days=days, now=self.now, selection={'account': account} if account else {})

    def test_account_filters_reconcile_and_relabel_without_rescan(self):
        self.add('a', 'work', 100)
        self.add('b', 'personal', 300)
        self.add('c', 'local', 50)
        data = self.report()
        self.assertEqual(data['summary']['tokens'], 450)
        self.assertEqual(sum(self.report(a)['summary']['tokens'] for a in ['work', 'personal', 'local']), 450)
        self.assertEqual(self.report('work')['summary']['tokens'], 100)
        self.cfg['accounts'][0]['label'] = 'Client work'
        self.assertEqual(next(a['name'] for a in self.report()['accounts'] if a['accountId'] == 'work'), 'Client work')
        self.assertEqual(self.report()['summary']['tokens'], 450)

    def test_mirrors_deduplicate_and_cross_account_conflict_is_visible(self):
        self.add('same', 'work')
        self.add('same', 'work/mirror')
        self.add('same', 'local')
        self.assertEqual(self.report('work')['summary']['tokens'], 100)
        self.add('same', 'personal')
        data = self.report()
        self.assertEqual(data['summary']['tokens'], 100)
        self.assertEqual(self.report('conflict')['summary']['tokens'], 100)
        self.assertTrue(data['accountWarning'])
        self.assertEqual(self.report('work')['summary']['tokens'], 0)

    def test_imported_account_never_inherits_local_quota_or_price(self):
        self.cfg['monthlyPrices'] = {'codex': 200}
        self.add('a', 'work')
        data = self.report('work')['providers'][0]
        self.assertEqual(data['quota']['limits'], [])
        self.assertIsNone(data['monthlyPrice'])
        self.assertIsNone(self.report()['providers'][0]['monthlyPrice'])
        self.assertEqual(self.report()['providers'][0]['quotaScope'], 'Current login on this PC')
        self.assertIsNone(self.report('work')['cards'][0]['monthlyPrice'])

    def test_overview_cards_split_accounts_and_use_their_own_prices(self):
        self.cfg['monthlyPrices'] = {'codex': 200, 'personal': 20}
        self.add('a', 'work', 100)
        self.add('b', 'personal', 300)
        self.add('c', 'local', 50)
        cards = self.report()['cards']
        self.assertEqual([card['accountId'] for card in cards], ['personal', 'work', 'local'])
        self.assertEqual([card['name'] for card in cards],
                         ['Codex · Personal', 'Codex · Work', 'Codex · Local'])
        self.assertEqual([card['monthlyPrice'] for card in cards], [20, None, 200])
        self.assertEqual(cards[2]['quota']['limits'], [{'label': 'Local quota'}])
        self.assertEqual(cards[0]['quota']['limits'], [])
        self.assertEqual([card['provider'] for card in cards], ['codex', 'codex', 'codex'])
        self.assertEqual(self.report('personal')['cards'][0]['accountId'], 'personal')
        self.assertEqual(len(self.report('personal')['cards']), 1)

    def test_daily_and_hourly_split_series_by_account(self):
        self.add('a', 'work', 100)
        self.add('b', 'personal', 300)
        self.add('c', 'local', 50)
        data = self.report()
        today = data['daily'][-1]
        self.assertEqual(today['providers']['codex']['tokens'], 450)
        self.assertEqual(today['cards']['codex:personal']['tokens'], 300)
        self.assertEqual(today['cards']['codex:work']['tokens'], 100)
        self.assertEqual(today['cards']['codex:local']['tokens'], 50)
        self.assertEqual([card['id'] for card in data['cards']], ['codex:personal', 'codex:work', 'codex:local'])
        self.assertEqual([card['shade'] for card in data['cards']], [0, 1, 2])
        self.assertEqual([card['shades'] for card in data['cards']], [3, 3, 3])
        hourly = self.report(days=1)['hourly']
        self.assertEqual(sum(h['providers']['codex']['tokens'] for h in hourly), 450)
        self.assertEqual(sum(h['cards'].get('codex:personal', {}).get('tokens', 0) for h in hourly), 300)

    def test_named_account_quota_comes_from_its_own_record(self):
        self.add('a', 'work', 100)
        self.add('b', 'personal', 100)
        self.add('c', 'local', 50)
        state = self.root / 'state/ai-usage'
        usage = state.parent / 'agents/usage'
        usage.mkdir(parents=True)
        (usage / 'work.json').write_text(json.dumps({'name': 'Work',
            'limits': [{'label': 'Weekly (7-day)', 'percent': .95}], 'updatedAt': '2026-09-15T20:00:00+00:00'}))
        (usage / 'other.json').write_text(json.dumps({'name': 'Personal',
            'limits': [{'label': 'Weekly (7-day)', 'percent': .5}]}))
        (usage / 'broken.json').write_text('not json')
        with patch.object(c, 'STATE', state), patch.object(c, 'quota', return_value={'limits': [{'label': 'Local quota'}]}), \
             patch.object(c, 'theme', return_value={}):
            cards = {card['accountId']: card for card in c.report(self.ledger, self.cfg, now=self.now)['cards']}
        self.assertEqual(cards['work']['quota']['limits'][0]['percent'], .95)
        self.assertEqual(cards['work']['quotaScope'], 'From its own usage record')
        self.assertEqual(cards['personal']['quota']['limits'][0]['percent'], .5)
        self.assertEqual(cards['local']['quota']['limits'], [{'label': 'Local quota'}])
        self.assertEqual(cards['local']['quotaScope'], 'Current login on this PC')
        self.assertIn('current login', self.report('work')['cards'][0]['quota']['error'])

    def test_session_averages_and_priced_share(self):
        self.add('a', 'local', 100, 'one')
        self.add('b', 'local', 300, 'one')
        self.add('c', 'local', 200, 'two')
        data = self.report()
        self.assertEqual(data['summary']['sessions'], 2)
        self.assertEqual(data['summary']['tokensPerSession'], 300)
        self.assertAlmostEqual(data['summary']['valuePerSession'], data['summary']['value'] / 2)
        self.assertEqual(data['providers'][0]['valueShare'], 100)

    def test_missing_provenance_is_unassigned(self):
        self.ledger.put(c.record('old', 'codex', 'old', self.now.isoformat(), 'gpt-4.1', '', 'CLI', input=100))
        self.assertEqual(self.report('unassigned')['summary']['tokens'], 100)
        self.assertEqual(self.report('local')['summary']['tokens'], 0)

    def test_settings_validate_before_writing(self):
        with patch.object(c, 'CONFIG', self.root / 'settings.json'):
            saved = c.save_settings(self.cfg)
            self.assertEqual(saved['accounts'][0]['label'], 'Work')
            self.cfg['accounts'][1]['directories'] = self.cfg['accounts'][0]['directories']
            with self.assertRaisesRegex(ValueError, 'same source folder'):
                c.save_settings(self.cfg)
            self.assertEqual(c.settings()['accounts'], saved['accounts'])

    def test_named_directories_are_scanned_incrementally(self):
        import json
        home = self.root / 'work'
        path = home / 'sessions/test.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('\n'.join(json.dumps(r) for r in [
            {'type': 'session_meta', 'payload': {'id': 'stable-account-session'}},
            {'type': 'event_msg', 'timestamp': self.now.isoformat(), 'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {'input_tokens': 100, 'output_tokens': 20},
                'total_token_usage': {'input_tokens': 100, 'output_tokens': 20}}}}]))
        with patch.object(c, 'HOME', self.root), patch.dict('os.environ', {
            'CODEX_HOME': str(self.root / 'local'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'GROK_HOME': str(self.root / 'grok'), 'PI_CODING_AGENT_DIR': str(self.root / 'pi'),
            'XDG_DATA_HOME': str(self.root / 'data')}):
            self.ledger.scan(self.cfg)
            self.ledger.scan(self.cfg)
        self.assertEqual(self.report('work')['summary']['tokens'], 120)
        self.assertEqual(self.report()['summary']['tokens'], 120)

    def test_provenance_migration_preserves_events_and_reindexes_files(self):
        self.add('old', 'work')
        self.ledger.db.execute("DELETE FROM metadata WHERE key='provenanceVersion'")
        self.ledger.db.execute("INSERT INTO files VALUES ('old',1,1)")
        self.ledger.db.commit()
        other = c.Ledger(self.root / 'usage.sqlite')
        self.assertEqual(other.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        self.assertEqual(other.db.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)
        other.db.close()
