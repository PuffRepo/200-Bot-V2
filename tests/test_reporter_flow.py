"""Routine confirmation and independent correction acceptance tests."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings
from scripts.solo_lab_audit import audit

EVIDENCE='https://discord.com/channels/1/2/3'
class ReporterFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3')
        with self.store.connect() as db:
            for i in range(1,13):
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,-1)',(-100000-i,-200000-i,f'LAB{i:02d}'))
        self.ratings=LabRatings(self.store)
    def submit(self, scores=None, evidence=EVIDENCE, replaces=None):
        return self.store.submit_table(111,3,' '.join(f'LAB{i:02d} {s}' for i,s in enumerate(scores or [82]*12,1)),evidence=evidence,replaces_table_id=replaces)
    def confirm(self, table):
        return self.ratings.apply(table,111,self.ratings.preview(table,routine=True)['digest'],routine=True)
    def reverse(self, table):
        request=self.ratings.request_reversal(table,111,'Wrong result',EVIDENCE)
        self.ratings.approve_reversal(request,222,self.ratings.reversal_preview(request)['digest'])
        return request
    def replacement(self, original, scores=None):
        table=self.submit(scores,replaces=original)
        self.store.review_table(table,222,True)
        return table
    def test_routine_once_with_evidence_and_audit(self):
        t=self.submit();self.confirm(t)
        with self.assertRaises(LabError):self.confirm(t)
        self.assertTrue(audit(self.store.path)['consistent'])
        self.assertEqual(LabRatings(LabStore(self.store.path)).ratings(),self.ratings.ratings())
    def test_other_actor_and_wrong_digest_blocked(self):
        t=self.submit();p=self.ratings.preview(t,routine=True)
        for actor,digest in [(222,p['digest']),(111,'wrong')]:
            with self.assertRaises(LabError):self.ratings.apply(t,actor,digest,routine=True)
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
    def test_optional_and_invalid_evidence(self):
        t=self.submit(evidence='')
        self.confirm(t)
        self.assertTrue(audit(self.store.path)['consistent'])
        with self.assertRaises(LabError):self.submit([83,81]+[82]*10,evidence='http://invalid')
    def test_routine_stale_preview(self):
        a=self.submit();b=self.submit([83,81]+[82]*10)
        stale=self.ratings.preview(b,routine=True)['digest'];self.confirm(a)
        with self.assertRaises(LabError):self.ratings.apply(b,111,stale,routine=True)
    def test_concurrent_routine_confirmation(self):
        t=self.submit();digest=self.ratings.preview(t,routine=True)['digest']
        def attempt(_):
            try:self.ratings.apply(t,111,digest,routine=True);return True
            except LabError:return False
        with ThreadPoolExecutor(max_workers=2) as p:
            self.assertEqual(sorted(p.map(attempt,range(2))),[False,True])
    def test_routine_failure_rolls_back_confirmation_and_ratings(self):
        t=self.submit();digest=self.ratings.preview(t,routine=True)['digest']
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail BEFORE INSERT ON rating_events BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ratings.apply(t,111,digest,routine=True)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM table_confirmations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM application_sequence').fetchone()[0],0)
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
    def test_flag_blocks_preexisting_preview_and_independent_clear(self):
        t=self.submit();digest=self.ratings.preview(t,routine=True)['digest']
        f=self.store.flag_table(t,333,'Check screenshot')
        with self.assertRaises(LabError):self.ratings.apply(t,111,digest,routine=True)
        for actor in (111,333):
            with self.assertRaises(LabError):self.store.resolve_flag(f,actor,False,'Checked')
        self.store.resolve_flag(f,222,False,'Screenshot agrees');self.confirm(t)
    def test_upheld_pending_dispute_rejects_and_duplicates_still_block(self):
        t=self.submit()
        with self.assertRaises(LabError):self.submit()
        f=self.store.flag_table(t,333,'Wrong scores');self.store.resolve_flag(f,222,True,'Evidence disagrees')
        with self.assertRaises(LabError):self.confirm(t)
    def test_applied_dispute_pauses_other_tables(self):
        a=self.submit();self.confirm(a);b=self.submit([83,81]+[82]*10)
        f=self.store.flag_table(a,333,'Wrong screenshot')
        with self.assertRaises(LabError):self.confirm(b)
        self.store.resolve_flag(f,222,False,'Scores checked');self.confirm(b)
    def test_placement_reversal_restores_null_and_preserves_history(self):
        t=self.submit();self.confirm(t);req=self.reverse(t)
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_events').fetchone()[0],12)
        with self.assertRaises(LabError):self.ratings.approve_reversal(req,222,'anything')
        self.assertTrue(audit(self.store.path)['consistent'])
    def test_reversal_self_approval_wrong_digest_and_season_switch(self):
        t=self.submit();self.confirm(t);self.store.flag_table(t,444,'Dispute');req=self.ratings.request_reversal(t,333,'Fix',EVIDENCE)
        p=self.ratings.reversal_preview(req)
        for actor,digest in [(111,p['digest']),(333,p['digest']),(222,'wrong')]:
            with self.assertRaises(LabError):self.ratings.approve_reversal(req,actor,digest)
        with self.assertRaises(LabError):self.store.start_lab_season('LAB-S8',111)
        with self.assertRaises(LabError):self.ratings.fixture_case('equal','LAB-S8',111)
        self.ratings.reject_reversal(req,222)
        self.assertTrue(all(r['mmr']==3750 for r in self.ratings.ratings()[1]))
    def test_reversal_failure_atomic_and_current_balance_mismatch(self):
        t=self.submit();self.confirm(t);req=self.ratings.request_reversal(t,111,'Fix',EVIDENCE)
        p=self.ratings.reversal_preview(req);before=self.ratings.ratings()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail BEFORE INSERT ON table_reversals BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ratings.approve_reversal(req,222,p['digest'])
        self.assertEqual(before,self.ratings.ratings())
        with self.store.connect() as db:db.execute('UPDATE season_ratings SET mmr=9999 WHERE user_id=-100001')
        with self.assertRaises(LabError):self.ratings.reversal_preview(req)
    def test_reversal_concurrency_and_revision_stale(self):
        t=self.submit();self.confirm(t);req=self.ratings.request_reversal(t,111,'Fix',EVIDENCE)
        digest=self.ratings.reversal_preview(req)['digest']
        with self.store.connect() as db:db.execute('UPDATE season_ratings SET revision=revision+1 WHERE user_id=-100001')
        with self.assertRaises(LabError):self.ratings.approve_reversal(req,222,digest)
        digest=self.ratings.reversal_preview(req)['digest']
        def attempt(_):
            try:self.ratings.approve_reversal(req,222,digest);return True
            except LabError:return False
        with ThreadPoolExecutor(max_workers=2) as p:self.assertEqual(sorted(p.map(attempt,range(2))),[False,True])
    def test_replacement_requires_independent_review_and_links(self):
        t=self.submit();self.confirm(t);self.store.flag_table(t,333,'Dispute');self.reverse(t)
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        with self.assertRaises(LabError):self.confirm(replacement)
        self.store.review_table(replacement,222,True)
        with self.assertRaises(LabError):self.ratings.apply(replacement,111,self.ratings.preview(replacement)['digest'])
        self.ratings.apply(replacement,222,self.ratings.preview(replacement)['digest'])
        self.assertTrue(audit(self.store.path)['consistent'])
    def test_older_table_requires_reverse_order_and_chronological_replay(self):
        a=self.submit();self.confirm(a);b=self.submit([83,81]+[82]*10);self.confirm(b)
        with self.assertRaises(LabError):self.ratings.request_reversal(a,111,'Fix',EVIDENCE)
        self.reverse(b);self.reverse(a)
        later=self.replacement(b,[83,81]+[82]*10)
        with self.assertRaises(LabError):self.ratings.preview(later)
        first=self.replacement(a,[84,80]+[82]*10)
        self.ratings.apply(first,222,self.ratings.preview(first)['digest'])
        self.ratings.apply(later,222,self.ratings.preview(later)['digest'])
        # Independent clean recomputation using the corrected placement baseline.
        from v2.rating import calculate_table
        from v2.rating import placement_mmr
        scores=[83,81]+[82]*10
        base=[placement_mmr(self.ratings.policy,s,'tier-all') for s in [84,80]+[82]*10]
        players=[{'user_id':-100000-i,'name':f'LAB{i:02d}','score':s,'mmr':m} for i,(s,m) in enumerate(zip(scores,base),1)]
        expected=calculate_table(self.ratings.policy,players,3,'tier-all')
        self.assertEqual([r['mmr'] for r in self.ratings.ratings()[1]],[r['after'] for r in expected])
        self.assertTrue(audit(self.store.path)['consistent'])
    def test_pre_update_order_is_not_guessed(self):
        t=self.submit();self.confirm(t)
        with self.store.connect() as db:db.execute('DELETE FROM application_sequence')
        with self.assertRaises(LabError):self.ratings.request_reversal(t,111,'Fix',EVIDENCE)
    def test_no_cancels_without_rating_changes_and_allows_resubmission(self):
        t=self.submit(evidence='');before=self.ratings.ratings()
        self.store.cancel_table(t,111)
        self.assertEqual(before,self.ratings.ratings())
        with self.assertRaises(LabError):self.confirm(t)
        self.confirm(self.submit(evidence=''))
        self.assertTrue(audit(self.store.path)['consistent'])
    def test_other_reporter_cannot_cancel_and_applied_table_cannot_cancel(self):
        t=self.submit(evidence='')
        with self.assertRaises(LabError):self.store.cancel_table(t,222)
        self.confirm(t)
        with self.assertRaises(LabError):self.store.cancel_table(t,111)
    def test_timeout_cancellation_preserves_placements(self):
        t=self.submit(evidence='')
        self.store.cancel_table(t,111,'Confirmation expired after five minutes')
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
        self.assertTrue(audit(self.store.path)['consistent'])
    def test_restart_releases_unconfirmed_button_preview_only(self):
        t=self.submit(evidence='');self.store.register_button_preview(t,111)
        other=self.submit([83,81]+[82]*10,evidence='')
        self.assertEqual(self.store.reset_button_previews(),1)
        self.assertEqual(self.store.reset_button_previews(),0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tables WHERE id=?',(other,)).fetchone()[0],'pending')
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
        self.assertIsInstance(self.submit(evidence=''),int)
    def test_restart_never_reverses_confirmed_table(self):
        t=self.submit(evidence='');self.store.register_button_preview(t,111);self.confirm(t)
        before=self.ratings.ratings()
        self.assertEqual(self.store.reset_button_previews(),0)
        self.assertEqual(before,self.ratings.ratings())
    def test_original_s06_invalid_input_checks_survive(self):
        raw=' '.join(f'LAB{i:02d} 82' for i in range(1,13))
        for fmt,scores,tier in [(5,raw,'tier-all'),(3,raw.replace('LAB01 82','LAB01 81'),'tier-all'),(3,raw.replace('LAB02','LAB01'),'tier-all'),(3,raw.replace('LAB12','UNKNOWN'),'tier-all'),(3,raw.replace('LAB01 82','LAB01 181'),'tier-all'),(3,raw,'wrong-tier')]:
            with self.assertRaises(LabError):self.store.submit_table(111,fmt,scores,tier,evidence=EVIDENCE)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tables').fetchone()[0],0)


    def test_combined_review_preview_and_atomic_application(self):
        t=self.submit();self.confirm(t);self.reverse(t)
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        preview=self.ratings.review_preview(replacement)
        self.assertTrue(preview['review_pending'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tables WHERE id=?',(replacement,)).fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM table_reviews WHERE table_id=?',(replacement,)).fetchone()[0],0)
        self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
        self.assertTrue(audit(self.store.path)['consistent'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT reviewer_id FROM table_reviews WHERE table_id=?',(replacement,)).fetchone()[0],222)
        with self.assertRaises(LabError):self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
    def test_combined_review_failure_rolls_back_validation_and_ratings(self):
        t=self.submit();self.confirm(t);self.reverse(t)
        replacement=self.submit([83,81]+[82]*10,replaces=t);preview=self.ratings.review_preview(replacement)
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_ui_apply BEFORE INSERT ON rating_events BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tables WHERE id=?',(replacement,)).fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM table_reviews WHERE table_id=?',(replacement,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications WHERE table_id=?',(replacement,)).fetchone()[0],0)
    def test_combined_review_preserves_original_requester_independence(self):
        t=self.submit();self.confirm(t);self.store.flag_table(t,555,'Dispute')
        request=self.ratings.request_reversal(t,333,'Correction',EVIDENCE)
        self.ratings.approve_reversal(request,222,self.ratings.reversal_preview(request)['digest'])
        replacement=self.store.submit_table(444,3,' '.join(f'LAB{i:02d} {score}' for i,score in enumerate([83,81]+[82]*10,1)),evidence=EVIDENCE,replaces_table_id=t)
        preview=self.ratings.review_preview(replacement)
        for actor in (111,333,444):
            with self.assertRaises(LabError):self.ratings.apply(replacement,actor,preview['digest'],review_pending=True)
        self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
    def test_combined_review_stale_snapshot_never_persists_validation(self):
        t=self.submit();self.confirm(t);self.reverse(t)
        replacement=self.submit([83,81]+[82]*10,replaces=t);preview=self.ratings.review_preview(replacement)
        with self.store.connect() as db:db.execute('UPDATE season_ratings SET revision=revision+1')
        with self.assertRaises(LabError):self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM table_reviews WHERE table_id=?',(replacement,)).fetchone()[0],0)

    def test_single_staff_corrects_own_report_and_audit_preserves_policy(self):
        t=self.submit();self.confirm(t)
        req=self.ratings.request_reversal(t,111,'Typo',EVIDENCE)
        preview=self.ratings.reversal_preview(req)
        self.assertFalse(preview['requires_independent'])
        self.ratings.approve_reversal(req,111,preview['digest'])
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        preview=self.ratings.review_preview(replacement)
        self.ratings.apply(replacement,111,preview['digest'],review_pending=True)
        self.assertTrue(audit(self.store.path)['consistent'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT requires_independent FROM table_reviews WHERE table_id=?',(replacement,)).fetchone()[0],0)
        self.store.flag_table(replacement,333,'Later dispute')
        self.assertTrue(audit(self.store.path)['consistent'])

    def test_single_staff_rejects_own_routine_correction_without_changes(self):
        t=self.submit();self.confirm(t);before=self.ratings.ratings()
        req=self.ratings.request_reversal(t,111,'Typo',EVIDENCE)
        self.ratings.reject_reversal(req,111)
        self.assertEqual(before,self.ratings.ratings())
        self.assertTrue(audit(self.store.path)['consistent'])

    def test_late_dispute_escalates_and_invalidates_routine_preview(self):
        t=self.submit();self.confirm(t)
        req=self.ratings.request_reversal(t,111,'Typo',EVIDENCE)
        old=self.ratings.reversal_preview(req)
        self.store.flag_table(t,333,'Disputed scores')
        for actor in (111,333):
            with self.assertRaises(LabError):self.ratings.approve_reversal(req,actor,old['digest'])
        with self.assertRaises(LabError):self.ratings.approve_reversal(req,222,old['digest'])
        fresh=self.ratings.reversal_preview(req)
        self.assertTrue(fresh['requires_independent'])
        self.ratings.approve_reversal(req,222,fresh['digest'])
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        fresh=self.ratings.review_preview(replacement)
        for actor in (111,333):
            with self.assertRaises(LabError):self.ratings.apply(replacement,actor,fresh['digest'],review_pending=True)
        self.ratings.apply(replacement,222,fresh['digest'],review_pending=True)
        self.assertTrue(audit(self.store.path)['consistent'])

    def test_dispute_policy_survives_cleared_flag_and_reversal(self):
        t=self.submit();self.confirm(t)
        flag=self.store.flag_table(t,333,'Check')
        req=self.ratings.request_reversal(t,111,'Typo',EVIDENCE)
        self.store.resolve_flag(flag,222,False,'Checked')
        preview=self.ratings.reversal_preview(req)
        self.assertTrue(preview['requires_independent'])
        with self.assertRaises(LabError):self.ratings.approve_reversal(req,111,preview['digest'])
        self.ratings.approve_reversal(req,222,preview['digest'])
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        preview=self.ratings.review_preview(replacement)
        with self.assertRaises(LabError):self.ratings.apply(replacement,111,preview['digest'],review_pending=True)
        self.ratings.apply(replacement,222,preview['digest'],review_pending=True)
        self.assertTrue(audit(self.store.path)['consistent'])

    def test_disputed_lineage_remains_independent_after_second_correction(self):
        t=self.submit();self.confirm(t)
        flag=self.store.flag_table(t,333,'Check')
        req=self.ratings.request_reversal(t,111,'Typo',EVIDENCE)
        self.store.resolve_flag(flag,222,False,'Checked')
        self.ratings.approve_reversal(req,222,self.ratings.reversal_preview(req)['digest'])
        replacement=self.submit([83,81]+[82]*10,replaces=t)
        self.ratings.apply(replacement,222,self.ratings.review_preview(replacement)['digest'],review_pending=True)
        next_req=self.ratings.request_reversal(replacement,111,'Another typo',EVIDENCE)
        preview=self.ratings.reversal_preview(next_req)
        self.assertTrue(preview['requires_independent'])
        with self.assertRaises(LabError):self.ratings.approve_reversal(next_req,111,preview['digest'])

    def test_old_schema_migration_preserves_history_and_adopts_routine_pending(self):
        t=self.submit();self.confirm(t)
        old_req=self.ratings.request_reversal(t,111,'Old rejected',EVIDENCE)
        self.ratings.reject_reversal(old_req,222)
        pending=self.ratings.request_reversal(t,111,'Current routine',EVIDENCE)
        with self.store.connect() as db:
            db.execute('ALTER TABLE table_reversal_requests DROP COLUMN requires_independent')
            db.execute('ALTER TABLE table_reviews DROP COLUMN requires_independent')
        fresh=LabRatings(LabStore(self.store.path))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT requires_independent FROM table_reversal_requests WHERE id=?',(old_req,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT requires_independent FROM table_reversal_requests WHERE id=?',(pending,)).fetchone()[0],0)
        fresh.approve_reversal(pending,111,fresh.reversal_preview(pending)['digest'])
        self.assertTrue(audit(self.store.path)['consistent'])
