"""Offline accounting, confirmation and correction regressions for /reduce_loss."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings
from v2.loss_reductions import LossReductions
from scripts.solo_lab_audit import audit

EVIDENCE = 'https://discord.com/channels/1/2/3'


class LossReductionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LabStore(Path(self.temp.name) / 'test.sqlite3')
        self.ratings = LabRatings(self.store)
        self.loss = LossReductions(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO lab_seasons(name,started_by) VALUES ('S9',999)")
            db.execute("UPDATE lab_settings SET value='S9' WHERE key='current_season'")
            for uid in range(1,13):
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)', (uid,uid,f'P{uid}'))
                db.execute("INSERT INTO season_ratings(season,user_id,mmr) VALUES ('S9',?,6000)", (uid,))
        self.table = self.apply_table()

    def apply_table(self, scores=None):
        scores = scores or [100,100,90,90,85,85,80,80,70,70,67,67]
        table = self.store.submit_table(999,2,' '.join(f'P{i} {s}' for i,s in enumerate(scores,1)), evidence=EVIDENCE)
        self.ratings.apply(table,999,self.ratings.preview(table,True)['digest'],True)
        return table

    def preview(self, ratio='1/2', table=None, uid=12, actor=999, reason='Disconnected halfway'):
        return self.loss.preview(table or self.table,uid,actor,ratio,reason)

    def reduce(self, ratio='1/2', table=None, uid=12, actor=999, reason='Disconnected halfway', digest=None):
        p = self.preview(ratio,table,uid,actor,reason)
        return self.loss.apply(p['table_id'],uid,actor,ratio,reason,digest or p['digest'])

    def mmr(self):
        return next(r['mmr'] for r in self.ratings.ratings()[1] if r['user_id']==12)

    def test_half_loss_audit_statistics_and_durable_sync(self):
        before = self.mmr()
        p = self.preview()
        self.assertEqual((p['original_delta'],p['retained_delta'],p['refund']),(-200,-100,100))
        self.assertEqual(self.mmr(),before)  # preview is read only
        self.reduce()
        self.assertEqual(self.mmr(),before+100)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM loss_reductions').fetchone()
            self.assertEqual((row['actor_id'],row['reason']),(999,'Disconnected halfway'))
            event=db.execute('SELECT * FROM rating_events WHERE table_id=? AND user_id=12',(self.table,)).fetchone()
            self.assertEqual(event['after_mmr']-event['before_mmr'],event['applied_delta'])
            self.assertEqual(event['applied_delta'],-200)
            adjustment=db.execute('SELECT * FROM balance_adjustments').fetchone()
            self.assertEqual((adjustment['delta'],adjustment['kind']),(100,'reduce_loss'))
            self.assertEqual(db.execute('SELECT pending FROM member_sync_jobs WHERE user_id=12').fetchone()[0],1)
        stats=self.ratings.player_statistics(12)
        self.assertEqual((stats['tables'],stats['original_delta'],stats['refunds'],stats['net_table_delta'],stats['average_delta']),(1,-200,100,-100,-100))
        self.assertTrue(audit(self.store.path)['consistent'])
        self.assertEqual(LabRatings(LabStore(self.store.path)).player_statistics(12),stats)

    def test_endpoints_and_noncompounding_after_restart(self):
        self.reduce('0')
        self.assertEqual(self.mmr(),6000)
        with self.assertRaises(LabError):LossReductions(LabStore(self.store.path)).preview(self.table,12,999,'1/2','Again')
        # A separate player can retain the full loss (zero credit), once.
        result=self.reduce('1',uid=11)
        self.assertEqual(result['refund'],0)
        with self.assertRaises(LabError):self.reduce('0',uid=11)

    def test_fraction_rounds_down_exactly_like_legacy(self):
        result=self.reduce('1/3')
        self.assertEqual((result['retained_delta'],result['refund']),(-67,133))

    def test_zero_credit_does_not_block_reversal_or_corrupt_statistics(self):
        self.assert_noop_reversible('1')

    def test_rounding_to_zero_credit_does_not_block_reversal(self):
        self.assert_noop_reversible('999/1000')

    def assert_noop_reversible(self,fraction):
        result=self.reduce(fraction)
        self.assertEqual(result['refund'],0)
        request=self.ratings.request_reversal(self.table,999,'Actual score correction',EVIDENCE)
        self.ratings.approve_reversal(request,998,self.ratings.reversal_preview(request)['digest'])
        self.assertEqual(self.mmr(),6000)
        self.assertTrue(audit(self.store.path)['consistent'])
        self.assertEqual(self.ratings.player_statistics(12)['tables'],0)

    def test_invalid_fraction_reason_and_self_refund(self):
        for ratio in ('-1/2','3/2','1/0','NaN','inf','1e-1','1/2/3','', '9'*100, None):
            with self.subTest(ratio=ratio),self.assertRaises(LabError):self.preview(ratio)
        for reason in ('',' '*3,'x'*129):
            with self.assertRaises(LabError):self.preview(reason=reason)
        with self.assertRaises(LabError):self.preview(actor=12)
        self.assertEqual(self.mmr(),5800)

    def test_wrong_target_status_and_season(self):
        for uid in (1,777):
            with self.assertRaises(LabError):self.preview(uid=uid)
        with self.assertRaises(LabError):self.preview(table=999)
        pending=self.store.submit_table(999,2,' '.join(f'P{i} 82' for i in range(1,13)))
        with self.assertRaises(LabError):self.preview(table=pending)
        with self.store.connect() as db:db.execute("UPDATE lab_settings SET value='LAB-INITIAL' WHERE key='current_season'")
        with self.assertRaises(LabError):self.preview()

    def test_placement_and_zero_loss_rejected(self):
        with self.store.connect() as db:db.execute("UPDATE season_ratings SET mmr=NULL WHERE user_id=12 AND season='S9'")
        placed=self.apply_table([82]*12)
        with self.assertRaises(LabError):self.preview(table=placed)
        tied=self.apply_table([83,81]+[82]*10)
        # Player 1 may gain/lose against a different placement; zero/positive never refundable.
        with self.store.connect() as db:
            zeros=db.execute('SELECT user_id FROM rating_events WHERE table_id=? AND applied_delta>=0',(tied,)).fetchall()
        self.assertTrue(zeros)
        for row in zeros:
            with self.assertRaises(LabError):self.preview(table=tied,uid=row[0])

    def test_stale_digest_actor_reason_and_policy_boundary(self):
        p=self.preview()
        with self.assertRaises(LabError):self.reduce(actor=998,digest=p['digest'])
        with self.assertRaises(LabError):self.reduce(reason='Different reason',digest=p['digest'])
        self.loss.moderation.deduct(999,12,20,'Penalty','test')
        with self.assertRaises(LabError):self.reduce(digest=p['digest'])
        self.reduce()
        self.assertEqual(self.mmr(),5880)

    def test_floor_uses_actual_loss_not_raw_loss(self):
        with self.store.connect() as db:
            db.execute("UPDATE season_ratings SET mmr=50 WHERE user_id=12 AND season='S9'")
        table=self.apply_table([101,99,90,90,85,85,80,80,70,70,67,67])
        result=self.reduce(table=table)
        self.assertEqual(result['original_delta'],-49)
        self.assertEqual((result['retained_delta'],result['refund'],self.mmr()),(-25,24,25))

    def test_old_table_credit_does_not_recompute_later_table(self):
        stale=self.preview()
        later=self.apply_table([82]*12)
        with self.store.connect() as db:
            original=[tuple(r) for r in db.execute('SELECT * FROM rating_events WHERE table_id=?',(later,))]
        before=self.mmr()
        with self.assertRaises(LabError):self.reduce(digest=stale['digest'])
        self.reduce()
        self.assertEqual(self.mmr(),before+100)
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM rating_events WHERE table_id=?',(later,))],original)
        with self.assertRaisesRegex(LabError,'accounting boundary'):
            self.ratings.request_reversal(later,999,'Fix',EVIDENCE)
        stats=self.ratings.player_statistics(12)
        self.assertEqual(stats['tables'],2)
        self.assertEqual(stats['net_table_delta'],stats['original_delta']+100)

    def test_new_tables_use_credit_and_can_reverse_until_boundary(self):
        self.reduce()
        later=self.store.submit_table(999,2,' '.join(f'P{i} 82' for i in range(1,13)),evidence=EVIDENCE)
        preview=self.ratings.preview(later,True)
        self.assertEqual(next(r['before'] for r in preview['rows'] if r['user_id']==12),5900)
        self.ratings.apply(later,999,preview['digest'],True)
        request=self.ratings.request_reversal(later,999,'Fix',EVIDENCE)
        self.ratings.approve_reversal(request,998,self.ratings.reversal_preview(request)['digest'])
        self.assertEqual(self.mmr(),5900)
        with self.assertRaisesRegex(LabError,'accounting boundary'):self.ratings.request_reversal(self.table,999,'Fix',EVIDENCE)
        self.assertEqual(self.ratings.player_statistics(12)['tables'],1)
        self.assertEqual(self.ratings.player_statistics(12)['net_table_delta'],-100)

    def test_disputes_pending_reversal_and_replay_block_credit(self):
        request=self.ratings.request_reversal(self.table,999,'Fix',EVIDENCE)
        with self.assertRaises(LabError):self.preview()
        self.ratings.reject_reversal(request,998)
        flag=self.store.flag_table(self.table,998,'Dispute')
        with self.assertRaises(LabError):self.preview()
        # Isolate replay protection without bypassing production code.
        with self.store.connect() as db:db.execute("UPDATE table_flags SET status='cleared' WHERE id=?",(flag,))
        request=self.ratings.request_reversal(self.table,999,'Fix',EVIDENCE)
        self.ratings.approve_reversal(request,998,self.ratings.reversal_preview(request)['digest'])
        with self.assertRaises(LabError):self.preview()

    def test_transaction_failure_rolls_back_balance_and_job(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail BEFORE INSERT ON loss_reductions BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.reduce()
        self.assertEqual(self.mmr(),5800)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM balance_adjustments').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM member_sync_jobs').fetchone()[0],0)

    def test_concurrent_duplicate_confirmation_credits_once(self):
        digest=self.preview()['digest']
        def apply(_):
            try:self.loss.apply(self.table,12,999,'1/2','Disconnected halfway',digest);return True
            except LabError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(apply,range(2))),[False,True])
        self.assertEqual(self.mmr(),5900)

    def test_credit_invalidates_pending_table_preview(self):
        pending=self.store.submit_table(999,2,' '.join(f'P{i} 82' for i in range(1,13)))
        digest=self.ratings.preview(pending,True)['digest']
        self.reduce()
        with self.assertRaises(LabError):self.ratings.apply(pending,999,digest,True)
        preview=self.ratings.preview(pending,True)
        self.assertEqual(next(r['before'] for r in preview['rows'] if r['user_id']==12),5900)

    def test_audit_detects_tampering_and_statistics_exclude_new_season(self):
        before=audit(self.store.path)['state_fingerprint']
        self.reduce()
        self.assertNotEqual(audit(self.store.path)['state_fingerprint'],before)
        with self.store.connect() as db:db.execute('UPDATE loss_reductions SET numerator=0')
        result=audit(self.store.path)
        self.assertFalse(result['consistent'])
        self.assertEqual(result['checks']['loss_fraction_errors'],1)
        with self.store.connect() as db:db.execute("UPDATE lab_settings SET value='LAB-INITIAL' WHERE key='current_season'")
        stats=self.ratings.player_statistics(12)
        self.assertEqual((stats['tables'],stats['refunds'],stats['net_table_delta']),(0,0,0))

    def test_sync_queue_failure_rolls_back_credit_and_audit(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_job BEFORE INSERT ON member_sync_jobs BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.reduce()
        self.assertEqual(self.mmr(),5800)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM balance_adjustments').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM loss_reductions').fetchone()[0],0)

    def test_zero_net_adjustment_cannot_bypass_reversal_protection(self):
        self.reduce()
        self.loss.moderation.deduct(999,12,100,'Penalty','offset-credit')
        self.assertEqual(self.mmr(),5800)
        with self.assertRaisesRegex(LabError,'accounting boundary'):self.ratings.request_reversal(self.table,999,'Fix',EVIDENCE)
