"""Temporary SQLite and local flag assets only, no profile/network calls."""
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from v2.countries import CODES, country_code, player_countries, set_country
from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings
from v2.table_image import draw_flag, lorenzi_query, render_table, render_results_local


class CountriesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3');self.ratings=LabRatings(self.store)
        with self.store.connect() as db:
            for uid in range(1,13):
                db.execute('INSERT INTO players(user_id,name,verified_by) VALUES (?,?,999)',(uid,f'P{uid}'))
            db.execute('CREATE TABLE registry_import_audit(user_id INTEGER PRIMARY KEY,source_json TEXT)')
    def source(self,uid,records):
        with self.store.connect() as db:
            db.execute('INSERT INTO registry_import_audit VALUES (?,?)',(uid,json.dumps({'historical_registration_records':records,'private':'SECRET'})))
    def countries(self):
        with self.store.connect() as db:return player_countries(db)
    def test_imported_countries_require_one_unambiguous_valid_code(self):
        self.source(1,[{'country_code':'jp'},{'country_code':'JP'}])
        self.source(2,[{'country_code':'JP'},{'country_code':'US'}])
        self.source(3,[{'country_code':'ZZ'}]);self.source(4,[{'name':'Japan'}])
        self.assertEqual(self.countries(),{1:'JP'})
    def test_staff_override_and_clear_are_audited_without_rating_changes(self):
        self.source(1,[{'country_code':'JP'}])
        self.assertEqual(set_country(self.store,1,' us ',99,'Verified profile'),'US')
        self.assertEqual(self.countries()[1],'US')
        set_country(self.store,1,'-',99,'Player correction')
        self.assertEqual(self.countries()[1],'')
        with self.store.connect() as db:
            rows=db.execute('SELECT before_code,after_code,actor_id,reason FROM player_country_audit ORDER BY id').fetchall()
            self.assertEqual(tuple(rows[0]),('JP','US',99,'Verified profile'))
            self.assertEqual(tuple(rows[1]),('US','',99,'Player correction'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM season_ratings').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        for bad in ['ZZ','USA','../us','[JP]']:
            with self.assertRaises(LabError):set_country(self.store,1,bad,99,'Invalid input')
        with self.assertRaises(LabError):set_country(self.store,99,'JP',99,'Unknown player')
    def test_preview_stable_identity_and_local_and_lorenzi_flags(self):
        set_country(self.store,1,'JP',99,'Verified profile')
        raw=' '.join(f'P{i} 82' for i in range(1,13))
        table=self.store.submit_table(999,3,raw)
        rows=self.ratings.preview(table,True)['rows']
        self.assertEqual(rows[0]['country_code'],'JP')
        self.assertEqual(lorenzi_query(3,raw,rows).count('[JP]'),1)
        self.assertNotIn('SECRET',lorenzi_query(3,raw,rows))
        for render in (render_table,render_results_local):
            marked=render(table,3,'tier-all',raw,rows)
            blank=render(table,3,'tier-all',raw,[{**r,'country_code':''} for r in rows])
            self.assertNotEqual(marked,blank)
            with Image.open(BytesIO(marked)) as image:self.assertEqual(image.size,(1180,750))
    def test_read_only_older_database_needs_no_migration_to_read_countries(self):
        import sqlite3
        self.source(1,[{'country_code':'FR'}])
        with self.store.connect() as db:
            db.execute('DROP TABLE player_country_audit')
            db.execute('DROP TABLE player_countries')
        before=self.store.path.read_bytes()
        with sqlite3.connect(self.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            self.assertEqual(player_countries(db),{1:'FR'})
        self.assertEqual(before,self.store.path.read_bytes())

    def test_assets_cover_iso_codes_and_bad_codes_never_open_files(self):
        root=Path(__file__).resolve().parents[1]/'images/flags'
        for code in CODES:self.assertTrue((root/(code.lower()+'.png')).is_file(),code)
        for value in [None,{},'ZZ','../us','USA']:
            self.assertEqual(country_code(value),'')
            image=Image.new('RGB',(40,40),'white');draw_flag(image,value,(20,20))
            self.assertEqual(image.getpixel((20,20)),(255,255,255))
        image=Image.new('RGB',(40,40),'white');draw_flag(image,'JP',(20,20))
        self.assertNotEqual(image.getpixel((20,20)),(255,255,255))
