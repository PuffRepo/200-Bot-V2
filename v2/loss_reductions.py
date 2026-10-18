"""One-time S9 loss credits; original applications remain immutable."""
import hashlib
import json
import re
from fractions import Fraction

from v2.lab_store import LabError
from v2.moderation import Moderation


class LossReductions:
    def __init__(self, store):
        # LabRatings must already have initialized the table ledger.
        self.store = store
        self.moderation = Moderation(store)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS member_sync_jobs(
                user_id INTEGER PRIMARY KEY, pending INTEGER NOT NULL DEFAULT 1,
                generation INTEGER NOT NULL DEFAULT 1, last_error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @staticmethod
    def fraction(value):
        if not isinstance(value, str) or not re.fullmatch(
            r'(?:\d{1,12}/\d{1,12}|[01](?:\.\d{1,12})?)', value.strip()
        ):
            raise LabError('Enter a fraction from 0 through 1, such as 1/2, 0, or 1.')
        try:
            result = Fraction(value.strip())
        except (ValueError, ZeroDivisionError):
            raise LabError('Enter a fraction from 0 through 1 with a nonzero denominator.') from None
        if not 0 <= result <= 1:
            raise LabError('The fraction of loss retained must be from 0 through 1.')
        return result

    def _preview(self, db, table_id, user_id, actor_id, fraction, reason):
        ratio = self.fraction(fraction)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 128:
            raise LabError('Record a reason of 1–128 characters.')
        if actor_id == user_id:
            raise LabError('You cannot reduce your own loss.')
        season = self.moderation._ready(db, user_id)
        table = db.execute('SELECT * FROM tables WHERE id=?', (table_id,)).fetchone()
        if season != 'S9' or table is None or table['season'] != 'S9' or table['status'] != 'applied':
            raise LabError('Choose an applied table in the active S9 season.')
        event = db.execute('SELECT * FROM rating_events WHERE table_id=? AND user_id=?', (table_id, user_id)).fetchone()
        if event is None or event['kind'] != 'table' or event['applied_delta'] is None or event['applied_delta'] >= 0:
            raise LabError('The player must have a negative applied loss on this table; placements cannot be refunded.')
        if event['before_mmr'] is None or event['after_mmr'] - event['before_mmr'] != event['applied_delta']:
            raise LabError('The original loss ledger is inconsistent; stop for investigation.')
        if db.execute('SELECT 1 FROM loss_reductions WHERE table_id=? AND user_id=?', (table_id, user_id)).fetchone():
            raise LabError('This player/table already has a loss reduction; repeated refunds are not allowed.')
        if db.execute('''SELECT 1 FROM rating_applications a JOIN tables t ON t.id=a.table_id
            LEFT JOIN application_sequence s ON s.table_id=a.table_id
            WHERE a.season=? AND t.status='applied' AND s.sequence IS NULL''', (season,)).fetchone():
            raise LabError('Application order is missing; stop for investigation.')
        current = db.execute('SELECT mmr,revision FROM season_ratings WHERE season=? AND user_id=?', (season, user_id)).fetchone()
        if current is None or current['mmr'] is None:
            raise LabError('The player must have an established current rating.')
        boundary = db.execute('''SELECT MAX(s.sequence) FROM application_sequence s
            JOIN tables t ON t.id=s.table_id WHERE t.season=? AND t.status='applied' ''', (season,)).fetchone()[0]
        if boundary is None:
            raise LabError('Application order is missing; stop for investigation.')
        original = event['applied_delta']
        retained = original * ratio.numerator // ratio.denominator
        refund = retained - original
        preview = dict(table_id=table_id, user_id=user_id, actor_id=actor_id,
                       season=season, reason=reason.strip(), numerator=ratio.numerator,
                       denominator=ratio.denominator, original_delta=original,
                       retained_delta=retained, refund=refund, before=current['mmr'],
                       after=current['mmr'] + refund, revision=current['revision'],
                       boundary_sequence=boundary, event=dict(event))
        preview['digest'] = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
        return preview

    def preview(self, table_id, user_id, actor_id, fraction, reason):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._preview(db, table_id, user_id, actor_id, fraction, reason)

    def apply(self, table_id, user_id, actor_id, fraction, reason, digest):
        """Caller must recheck Reporter access at confirmation, not just preview."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = self._preview(db, table_id, user_id, actor_id, fraction, reason)
            if digest != result['digest']:
                raise LabError('Loss preview changed. Review a fresh preview before confirming.')
            key = f'loss-reduction:{table_id}:{user_id}'
            self.moderation._adjust(db, 'S9', user_id, actor_id, result['refund'],
                                    'reduce_loss', result['reason'], key)
            adjustment = db.execute('SELECT id FROM balance_adjustments WHERE operation_key=?', (key,)).fetchone()[0]
            db.execute('''INSERT INTO loss_reductions(table_id,user_id,actor_id,reason,
                numerator,denominator,original_delta,retained_delta,adjustment_id,boundary_sequence,digest)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (table_id,user_id,actor_id,result['reason'],result['numerator'],result['denominator'],
                 result['original_delta'],result['retained_delta'],adjustment,result['boundary_sequence'],digest))
            # Persist retry work in the same transaction as the credit.
            if user_id > 0:
                db.execute('''INSERT INTO member_sync_jobs(user_id) VALUES (?)
                    ON CONFLICT(user_id) DO UPDATE SET pending=1,generation=member_sync_jobs.generation+1,
                    updated_at=CURRENT_TIMESTAMP''', (user_id,))
            return result
