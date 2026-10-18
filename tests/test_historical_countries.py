"""Archived SQL is parsed as inert text; fixtures never run a database engine."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.extract_historical_countries import extract,player_rows,main
from v2.lab_store import LabError

SCHEMA='''CREATE TABLE `player` (
 `player_id` bigint,
 `player_name` varchar(128),
 `country_code` varchar(3)
) ENGINE=InnoDB;
'''

class HistoricalCountryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def dump(self,name,values):
        path=self.root/name
        path.write_text(SCHEMA+'INSERT INTO `player` VALUES '+values+';\n',encoding='utf-8')
        return path
    def test_escaped_unicode_names_and_sql_punctuation_are_inert(self):
        text=SCHEMA+"INSERT INTO `player` VALUES (123,'やま, (と); O\\'Brien','jp'),(124,'O''Brien','US');\nDROP TABLE player;"
        self.assertEqual(list(player_rows(text)),[('123','jp'),('124','US')])
    def test_multiple_insert_statements_all_read(self):
        text=SCHEMA+"INSERT INTO `player` VALUES (123,'A','JP');\nINSERT INTO `player` VALUES (124,'B','US');"
        self.assertEqual(len(list(player_rows(text))),2)
    def test_conflicts_and_invalid_values_excluded_no_latest_season_guess(self):
        a=self.dump('s5.sql',"(123,'A','JP'),(124,'B','US'),(125,'C','ZZ'),(126,'D',NULL)")
        b=self.dump('s7.sql',"(123,'Renamed','FR'),(124,'B','us'),(125,'C','JP'),(126,'D','')")
        result=extract([a,b])
        self.assertEqual(result['candidates'],[{'discord_id':'124','country_code':'US'}])
        self.assertEqual(result['summary'],dict(historical_players=4,unambiguous_candidates=1,conflicting_players=1,invalid_source_players=1,no_country_players=1))
        self.assertEqual(result['conflicts'][0]['countries'],['FR','JP'])
        self.assertNotIn('Renamed',json.dumps(result))
    def test_truncated_unsupported_or_invalid_id_rejected(self):
        for value in ["(123,'A','JP')", "(123,'A');", "(123,CONCAT('A'),'JP');", "(-1,'A','JP');"]:
            with self.subTest(value=value):
                with self.assertRaises(LabError):list(player_rows(SCHEMA+'INSERT INTO `player` VALUES '+value))
        with self.assertRaises(LabError):list(player_rows('CREATE TABLE other(id INT);'))
    def test_export_writes_country_only_csv_and_preserves_inputs_and_outputs(self):
        source=self.dump('s7.sql',"(123,'Not exported','JP')");before=source.read_bytes()
        output=self.root/'review'
        with patch('sys.argv',['extract','--sql',str(source),'--output',str(output)]),patch('builtins.print'):
            self.assertEqual(main(),0)
        with (output/'countries.csv').open(newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream)),[{'discord_id':'123','country_code':'JP'}])
        self.assertEqual(source.read_bytes(),before)
        existing=(output/'countries.csv').read_bytes()
        with patch('sys.argv',['extract','--sql',str(source),'--output',str(output)]),patch('builtins.print'):
            self.assertEqual(main(),1)
        self.assertEqual(existing,(output/'countries.csv').read_bytes())
    def test_real_archives_have_complete_consistent_extraction(self):
        root=Path(__file__).resolve().parents[1]
        result=extract([root/'sql'/f's{s}200lounge-export.sql' for s in (5,6,7)])
        self.assertEqual([s['players'] for s in result['sources']],[1709,2627,3786])
        self.assertEqual(result['summary']['historical_players'],3823)
        self.assertEqual(result['summary']['unambiguous_candidates'],3819)
        self.assertEqual(result['summary']['conflicting_players'],3)
        self.assertEqual(result['summary']['invalid_source_players'],1)
        self.assertEqual(len({r['discord_id'] for r in result['candidates']}),3819)

    def test_public_feed_overlap_uses_exact_ids_and_counts_missing_only(self):
        from datetime import datetime,timezone
        from scripts.preview_historical_countries import public_coverage
        historical={'candidates':[{'discord_id':'1','country_code':'JP'},{'discord_id':'2','country_code':'US'},{'discord_id':'99','country_code':'CA'}],
                    'conflicts':[{'discord_id':'3'}],'invalid':[{'discord_id':'4'}]}
        data={'schema_version':1,'season':'S9','generated_at':datetime.now(timezone.utc).isoformat(),
              'players':[{'user_id':str(i),'country_code':'FR' if i==2 else ''} for i in range(1,6)]}
        report=public_coverage(historical,data)
        self.assertEqual(report['current_players'],5)
        self.assertEqual(report['historical_candidates_matching_current_players'],2)
        self.assertEqual(report['potential_missing_countries_to_fill'],1)
        self.assertEqual(report['current_players_with_historical_conflicts'],1)
        self.assertEqual(report['current_players_with_invalid_historical_country'],1)
        self.assertEqual(report['current_players_without_usable_historical_country'],3)
        self.assertNotIn('discord_id',json.dumps(report))
        for mutate in [lambda d:d.update(season='S7'),lambda d:d.update(generated_at='2000-01-01T00:00:00+00:00'),lambda d:d['players'].append(d['players'][0]),lambda d:d['players'][0].update(user_id=1)]:
            copy=json.loads(json.dumps(data));mutate(copy)
            with self.assertRaises(LabError):public_coverage(historical,copy)


    def test_standalone_railway_preview_matches_full_pipeline_without_writes(self):
        import contextlib,io
        from v2.lab_store import LabStore
        from v2.countries import set_country
        from v2.bulk_countries import preview,digest
        root=Path(__file__).resolve().parents[1]
        shell=(root/'scripts/preview_historical_countries_railway.sh').read_text()
        source=shell.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        historical=extract([root/'sql'/f's{s}200lounge-export.sql' for s in (5,6,7)])
        store=LabStore(self.root/'test.sqlite3')
        ids=[int(row['discord_id']) for row in historical['candidates'][:4]]
        with store.connect() as db:
            for uid in ids:db.execute('INSERT INTO players(user_id,name,verified_by) VALUES (?,?,99)',(uid,f'Fixture {uid}'))
        set_country(store,ids[0],'-',99,'Staff cleared flag')
        set_country(store,ids[1],'JP',99,'Staff verified country')
        with store.connect() as db:expected=preview(db,historical['candidates'])
        before=store.path.read_bytes()
        code=source.replace("root = Path('/app')",f'root = Path({str(root)!r})').replace("database = Path('/app/private/production.sqlite3')",f'database = Path({str(store.path)!r})')
        out=io.StringIO()
        with contextlib.redirect_stdout(out):exec(compile(code,'standalone_preview','exec'),{'__name__':'__main__'})
        report=json.loads(out.getvalue())
        self.assertEqual(report['digest'],digest(expected))
        self.assertEqual(report['candidate_status_counts']['fill_missing'],2)
        self.assertEqual(report['candidate_status_counts']['protected_staff_record'],2)
        self.assertEqual(store.path.read_bytes(),before)
        # Fail closed before opening SQLite if a deployed archive differs.
        badroot=self.root/'changed';(badroot/'sql').mkdir(parents=True)
        (badroot/'sql/s5200lounge-export.sql').write_text('changed')
        guarded=code.replace(f'root = Path({str(root)!r})',f'root = Path({str(badroot)!r})')
        with self.assertRaisesRegex(SystemExit,'Archive differs'):
            exec(compile(guarded,'standalone_preview','exec'),{'__name__':'__main__'})
        self.assertEqual(store.path.read_bytes(),before)
