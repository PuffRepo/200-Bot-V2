"""Execute the standalone apply against temporary SQLite only."""
import contextlib
import io
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import unittest
from v2.lab_store import LabStore
from v2.lab_ratings import LabRatings
from v2.moderation import Moderation
from v2.countries import set_country
from v2.bulk_countries import preview,digest
from scripts.extract_historical_countries import extract

ROOT=Path(__file__).resolve().parents[1]

class ReviewedCountryApplyTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3')
        LabRatings(self.store);Moderation(self.store)
        historical=extract([ROOT/'sql'/f's{s}200lounge-export.sql' for s in (5,6,7)])
        self.ids=[int(r['discord_id']) for r in historical['candidates'][:4]]
        with self.store.connect() as db:
            season=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            for uid in self.ids:
                db.execute('INSERT INTO players(user_id,name,verified_by) VALUES (?,?,99)',(uid,f'Fixture {uid}'))
                db.execute('INSERT INTO season_ratings(season,user_id,mmr) VALUES (?,?,6000)',(season,uid))
        set_country(self.store,self.ids[0],'-',99,'Staff-cleared flag')
        set_country(self.store,self.ids[1],'JP',99,'Staff-verified country')
        with self.store.connect() as db:self.reviewed=digest(preview(db,historical['candidates']))
        shell=(ROOT/'scripts/apply_reviewed_countries_railway.sh').read_text()
        self.source=shell.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        self.source=self.source.replace("root = Path('/app')",f'root = Path({str(ROOT)!r})').replace("database = Path('/app/private/production.sqlite3')",f'database = Path({str(self.store.path)!r})')
        # Only synthetic fixtures change the pinned production digest/count/actor.
        self.source=re.sub(r"^ACTOR_DISCORD_ID = '[0-9]*'", "ACTOR_DISCORD_ID = '99'", self.source, flags=re.M)
        self.source=self.source.replace("REVIEWED_DIGEST = '6399307e5a458a1f5a9bbefac2fefd8a6355f5e3f5183fd53af6c52926ac3c78'",f'REVIEWED_DIGEST = {self.reviewed!r}')
        self.source=self.source.replace('EXPECTED_UPDATES = 1685','EXPECTED_UPDATES = 2')
    def snapshot(self,exclude_country=False):
        with self.store.connect() as db:
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name:[tuple(r) for r in db.execute('SELECT * FROM "'+name+'"')] for name in names
                    if not exclude_country or name not in ('player_countries','player_country_audit','sqlite_sequence')}
    def execute(self,source=None):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):exec(compile(source or self.source,'reviewed_country_apply','exec'),{'__name__':'__main__'})
        return json.loads(out.getvalue())
    def test_only_country_metadata_changes_and_retry_cannot_repeat(self):
        before=self.snapshot(True)
        report=self.execute()
        self.assertEqual(report['countries_updated'],2)
        self.assertEqual(report['actor_id'],'99')
        self.assertEqual(report['with_country'],3)
        self.assertEqual(before,self.snapshot(True))
        with self.store.connect() as db:
            audit=db.execute('SELECT actor_id,reason FROM player_country_audit ORDER BY id DESC LIMIT 2').fetchall()
            self.assertEqual([r['actor_id'] for r in audit],[99,99])
            self.assertTrue(all(self.reviewed in r['reason'] for r in audit))
            self.assertEqual(db.execute('SELECT country_code FROM player_countries WHERE user_id=?',(self.ids[0],)).fetchone()[0],'')
        after=self.snapshot()
        with self.assertRaisesRegex(SystemExit,'preview changed'):self.execute()
        self.assertEqual(after,self.snapshot())
    def test_stale_country_preview_prevents_every_write(self):
        set_country(self.store,self.ids[2],'FR',99,'Concurrent correction')
        before=self.snapshot()
        with self.assertRaisesRegex(SystemExit,'preview changed'):self.execute()
        self.assertEqual(before,self.snapshot())
    def test_audit_failure_rolls_back_whole_batch(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_country_audit BEFORE INSERT ON player_country_audit BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
        before=self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic audit failure'):self.execute()
        self.assertEqual(before,self.snapshot())
    def test_actor_missing_or_wrong_count_prevents_every_write(self):
        before=self.snapshot()
        with self.assertRaisesRegex(SystemExit,'real staff Discord'):
            self.execute(self.source.replace("ACTOR_DISCORD_ID = '99'", "ACTOR_DISCORD_ID = ''"))
        with self.assertRaisesRegex(SystemExit,'batch size differs'):
            self.execute(self.source.replace('EXPECTED_UPDATES = 2','EXPECTED_UPDATES = 3'))
        self.assertEqual(before,self.snapshot())
