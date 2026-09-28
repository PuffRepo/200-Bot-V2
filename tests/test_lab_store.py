import tempfile
import unittest
from pathlib import Path

from v2.lab_store import LabError, LabStore, mkc_id, parse_scores


class LabStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = LabStore(Path(self.temp.name) / 'lab.sqlite3')

    def test_verification_review_is_atomic_and_prevents_duplicate_identity(self):
        with self.assertRaises(LabError):
            mkc_id('https://mkcentral.com.attacker.example/registry/players/profile?id=17')
        request = self.db.request_verify(101, 'https://mkcentral.com/en-us/registry/players/profile?id=17')
        with self.assertRaises(LabError):
            self.db.review(request, 101, True, 'First')
        with self.assertRaises(LabError):
            self.db.review(request, 202, True, 'bad name!')
        with self.db.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM requests WHERE id=?',(request,)).fetchone()[0], 'pending')
        self.assertEqual(self.db.review(request,202,True,'First'), 'approved')
        with self.assertRaises(LabError):
            self.db.request_verify(303,'https://mkcentral.com/registry/players/profile?id=17')
        with self.assertRaises(LabError):
            self.db.review(request,202,True,'First')

    def test_name_strike_and_table_are_isolated_from_mmr(self):
        for user, name in ((101,'First'),(102,'Second')):
            req=self.db.request_verify(user,f'https://mkcentral.com/registry/players/profile?id={user}')
            self.db.review(req,999,True,name)
        req=self.db.request_name(101,'Changed')
        with self.assertRaises(LabError):
            self.db.request_name(101,'Again')
        self.db.review(req,999,True)
        scores=' '.join(f'{name} 82' for name in ['Changed','Second']+[f'P{i}' for i in range(10)])
        with self.assertRaises(LabError):
            self.db.submit_table(999,2,scores)
        for i in range(10):
            req=self.db.request_verify(i+500,f'https://mkcentral.com/registry/players/profile?id={i+500}')
            self.db.review(req,999,True,f'P{i}')
        table=self.db.submit_table(999,2,scores)
        strike=self.db.strike(999,101,'Staff reviewed',0)
        with self.db.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tables WHERE id=?',(table,)).fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT mmr_penalty FROM strikes WHERE id=?',(strike,)).fetchone()[0],0)
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(players)')],
                             ['user_id','mkc_id','name','verified_by','verified_at'])
        with self.assertRaises(LabError):
            parse_scores(scores.replace('Second 82','Second 81'),2)


if __name__ == '__main__':
    unittest.main()
