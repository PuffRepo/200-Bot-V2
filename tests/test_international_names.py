import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from v2.lab_store import LabStore, LabError, player_name, parse_scores
from v2.lab_ratings import LabRatings
from v2.registry_import import make_plan, apply_plan
from v2.table_image import results_layout, render_table, render_results_local, fetch_lorenzi_results
from v2.name_fonts import name_runs, font_catalog, required_points

NAMES=['Will','will','Quentîn','SK★Marcus','やまもと','한국어','العربية','हिन्दी','ไทย','👩🏽\u200d💻','louis shoggs','[JP] "A+B"!']

class InternationalNamesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'production.sqlite3'

    def source(self,names):
        return {'summary':{'approved_carry_forward_total':len(names)},'members':[
            {'discord_id':str(i),'display_name':name,'username':'unused','migration_group':'rank_carry_forward','all_role_ids':[],'historical_registration_records':[]}
            for i,name in enumerate(names,1)]}

    def test_import_preserves_exact_codepoints_and_case(self):
        names=NAMES+[' é ','e\u0301','é','!NetflixMain','@everyone']
        source=self.source(names);plan=make_plan(source)
        self.assertEqual(plan['summary']['name_changes'],0)
        self.assertEqual(plan['exact_duplicates'],[])
        apply_plan(source,plan,self.path)
        with LabStore(self.path).connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT name FROM players ORDER BY user_id')],names)
            self.assertEqual(db.execute('SELECT user_id FROM players WHERE name=?',('Will',)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT user_id FROM players WHERE name=?',('will',)).fetchone()[0],2)

    def test_only_exact_duplicates_block_and_need_explicit_resolution(self):
        source=self.source(['bobby','bobby','Bobby','Café!'])
        plan=make_plan(source)
        self.assertEqual(plan['exact_duplicates'],[{'name':'bobby','discord_ids':['1','2']}])
        with self.assertRaisesRegex(LabError,'Exact duplicate'):apply_plan(source,plan,self.path,True)
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(LabError,'Only exact'):make_plan(source,{'4':'Cafe'})
        fixed=make_plan(source,{'2':'bobby 2'})
        with self.assertRaisesRegex(LabError,'accept name changes'):apply_plan(source,fixed,self.path)
        apply_plan(source,fixed,self.path,True)

    def test_score_forms_preserve_names_and_distinct_scores(self):
        values=[81,83]+[82]*10
        pairs=list(zip(NAMES,values))
        for raw in (json.dumps(pairs,ensure_ascii=False),'\n'.join(f'{n}\t{s}' for n,s in pairs),'\n'.join(f'{n} {s}' for n,s in pairs)):
            self.assertEqual(parse_scores(raw,3),pairs)
            points={p['name']:p['score'] for g in results_layout(3,raw,[{'name':n} for n in NAMES]) for p in g['players']}
            self.assertEqual(points['Will'],81);self.assertEqual(points['will'],83)
        pairs[0]=(' leading and trailing ',81)
        self.assertEqual(parse_scores(json.dumps(pairs),3),pairs)
        self.assertEqual(parse_scores('\n'.join(f'{n}\t{s}' for n,s in pairs),3),pairs)
        with self.assertRaises(LabError):parse_scores(json.dumps([pairs[0]]*12),3)

    def test_end_to_end_identity_rating_and_cards(self):
        source=self.source(NAMES);apply_plan(source,make_plan(source),self.path)
        store=LabStore(self.path);ratings=LabRatings(store)
        raw=json.dumps([[n,81 if i==0 else 83 if i==1 else 82] for i,n in enumerate(NAMES)],ensure_ascii=False)
        table=store.submit_table(999,3,raw)
        preview=ratings.preview(table,True)
        ratings.apply(table,999,preview['digest'],True)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT user_id) FROM rating_events WHERE table_id=?',(table,)).fetchone()[0],12)
        for renderer in (render_table,render_results_local):
            self.assertTrue(renderer(table,3,'tier-all',raw,preview['rows']).startswith(b'\x89PNG'))
        with patch('v2.table_image.urlopen') as network:
            with self.assertRaises(ValueError):fetch_lorenzi_results(3,raw,preview['rows'])
            network.assert_not_called()

    def test_fonts_cover_representative_scripts_and_combining_emoji(self):
        catalog=dict(font_catalog())
        for name in NAMES+['e\u0301','Ελληνικά','Україна']:
            for text,path in name_runs(name):
                self.assertTrue(required_points(text)<=catalog[path],repr(text))

    def test_names_reject_only_size_blank_and_unsafe_controls(self):
        for n in ('a\nb','a\tb','a\x00b','\ud800',' ', 'a'*33):
            with self.assertRaises(LabError):player_name(n)
        for n in NAMES+[' x ', 'e\u0301', 'a'*32, 'a\u200db']:
            self.assertEqual(player_name(n),n)

    def test_existing_nocase_database_migrates_with_backup_and_foreign_keys(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("CREATE TABLE players(user_id INTEGER PRIMARY KEY,mkc_id INTEGER UNIQUE NOT NULL,name TEXT NOT NULL COLLATE NOCASE UNIQUE,verified_by INTEGER NOT NULL,verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP); CREATE TABLE identity_links(user_id INTEGER REFERENCES players(user_id)); INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES(1,42,'Will',9); INSERT INTO identity_links VALUES(1);")
        store=LabStore(self.path)
        with store.connect() as db:
            db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES(2,NULL,'will',9)")
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('SELECT mkc_id FROM players WHERE user_id=1').fetchone()[0],42)
        backups=list(self.path.parent.glob('*before-exact-names*'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],1)
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        LabStore(self.path);self.assertEqual(len(list(self.path.parent.glob('*before-exact-names*'))),1)
