"""Read-only lab consistency audit and before/after restart fingerprint."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

TABLES=('players','player_countries','player_country_audit','requests','strikes','tables','mmr_adjustments','table_reviews',
        'lab_seasons','lab_settings','season_ratings','rating_applications',
        'rating_events','lab_fixture_runs')

def audit(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        checks={'quick_check_ok':db.execute('PRAGMA quick_check').fetchall()==[('ok',)],
                'foreign_key_errors':len(db.execute('PRAGMA foreign_key_check').fetchall())}
        state={}
        counts={}
        for name in TABLES:
            # Fixed allowlist, never an identifier supplied by the user.
            rows=db.execute(f'SELECT * FROM {name}').fetchall()
            rows=sorted(rows,key=lambda r:json.dumps(r))
            state[name]=rows
            counts[name]=len(rows)
        for name in ('table_confirmations','table_cancellations','table_button_previews','table_flags','table_publications','application_sequence','table_reversal_requests','table_reversals','balance_adjustments','strike_removals','member_restrictions','table_retractions','loss_reductions'):
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone():
                state[name]=sorted(db.execute(f'SELECT * FROM {name}').fetchall(),key=lambda r:json.dumps(r))
                counts[name]=len(state[name])
        if 'balance_adjustments' in counts:
            checks['adjustment_balance_errors']=db.execute("SELECT COUNT(*) FROM balance_adjustments WHERE (before_mmr IS NULL AND (after_mmr IS NOT NULL OR delta!=0)) OR (before_mmr IS NOT NULL AND (after_mmr IS NULL OR after_mmr<1 OR after_mmr-before_mmr!=delta))").fetchone()[0]
            if 'loss_reductions' in counts:
                checks['loss_reduction_errors']=db.execute('''SELECT COUNT(*) FROM loss_reductions r
                    LEFT JOIN rating_events e ON e.table_id=r.table_id AND e.user_id=r.user_id
                    LEFT JOIN tables t ON t.id=r.table_id
                    LEFT JOIN balance_adjustments a ON a.id=r.adjustment_id
                    LEFT JOIN application_sequence s ON s.sequence=r.boundary_sequence
                    WHERE e.table_id IS NULL OR a.id IS NULL OR s.sequence IS NULL
                    OR t.season!='S9' OR t.status NOT IN ('applied','reversed')
                    OR (t.status='reversed' AND r.retained_delta!=r.original_delta) OR e.kind!='table'
                    OR r.original_delta!=e.applied_delta OR r.original_delta>=0
                    OR r.denominator<=0 OR r.numerator<0 OR r.numerator>r.denominator
                    OR r.retained_delta>0 OR r.retained_delta<r.original_delta
                    OR a.kind!='reduce_loss' OR a.user_id!=r.user_id OR a.actor_id!=r.actor_id
                    OR a.season!='S9' OR a.reason!=r.reason
                    OR a.delta!=r.retained_delta-r.original_delta''').fetchone()[0]
                checks['loss_fraction_errors']=sum(
                    denominator<=0 or numerator<0 or numerator>denominator
                    or retained!=original*numerator//denominator
                    for original,numerator,denominator,retained in db.execute(
                        'SELECT original_delta,numerator,denominator,retained_delta FROM loss_reductions'))
                checks['orphan_loss_credits']=db.execute("SELECT COUNT(*) FROM balance_adjustments a LEFT JOIN loss_reductions r ON r.adjustment_id=a.id WHERE a.kind='reduce_loss' AND r.table_id IS NULL").fetchone()[0]
        season=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
        checks['applications_not_12_events']=db.execute('SELECT COUNT(*) FROM (SELECT a.table_id FROM rating_applications a LEFT JOIN rating_events e ON e.table_id=a.table_id GROUP BY a.table_id HAVING COUNT(e.user_id)!=12)').fetchone()[0]
        checks['application_status_errors']=db.execute("SELECT COUNT(*) FROM rating_applications a JOIN tables t ON t.id=a.table_id WHERE t.status NOT IN ('applied','reversed') OR t.season!=a.season").fetchone()[0]
        checks['applied_tables_missing_application']=db.execute("SELECT COUNT(*) FROM tables t LEFT JOIN rating_applications a ON a.table_id=t.id WHERE t.status='applied' AND a.table_id IS NULL").fetchone()[0]
        checks['event_balance_errors']=db.execute("SELECT COUNT(*) FROM rating_events WHERE after_mmr<1 OR (kind='table' AND (before_mmr IS NULL OR applied_delta IS NULL OR after_mmr-before_mmr!=applied_delta)) OR (kind='placement' AND (before_mmr IS NOT NULL OR applied_delta IS NOT NULL)) OR kind NOT IN ('table','placement')").fetchone()[0]
        checks['invalid_ratings']=db.execute('SELECT COUNT(*) FROM season_ratings WHERE mmr IS NOT NULL AND mmr<1').fetchone()[0]
        checks['applied_participant_errors']=db.execute('SELECT COUNT(*) FROM rating_events e JOIN tables t ON t.id=e.table_id WHERE NOT EXISTS (SELECT 1 FROM json_each(t.participant_ids) p WHERE p.value=e.user_id)').fetchone()[0]
        review_policy='r.requires_independent' if 'requires_independent' in {r[1] for r in db.execute('PRAGMA table_info(table_reviews)')} else '1'
        reversal_policy='r.requires_independent' if 'requires_independent' in {r[1] for r in db.execute('PRAGMA table_info(table_reversal_requests)')} else '1'
        if 'table_confirmations' in counts:
            checks['duplicate_review_applications']=db.execute(f"SELECT COUNT(*) FROM rating_applications a JOIN tables t ON t.id=a.table_id WHERE NOT (EXISTS (SELECT 1 FROM table_confirmations c WHERE c.table_id=t.id AND c.actor_id=t.reporter_id AND a.actor_id=c.actor_id AND c.digest=a.snapshot_digest AND t.replaces_table_id IS NULL) OR EXISTS (SELECT 1 FROM table_reviews r WHERE r.table_id=t.id AND r.decision='validated' AND ({review_policy}=0 AND t.replaces_table_id IS NOT NULL OR {review_policy}=1 AND r.reviewer_id!=t.reporter_id AND a.actor_id!=t.reporter_id)))").fetchone()[0]
        else:
            checks['duplicate_review_applications']=db.execute(f"SELECT COUNT(*) FROM rating_applications a JOIN tables t ON t.id=a.table_id LEFT JOIN table_reviews r ON r.table_id=t.id WHERE r.table_id IS NULL OR r.decision!='validated' OR ({review_policy}=1 AND (r.reviewer_id=t.reporter_id OR a.actor_id=t.reporter_id)) OR ({review_policy}=0 AND t.replaces_table_id IS NULL)").fetchone()[0]
        if 'table_cancellations' in counts:
            checks['cancellation_errors']=db.execute("SELECT COUNT(*) FROM table_cancellations c JOIN tables t ON t.id=c.table_id WHERE t.status!='rejected' OR c.actor_id!=t.reporter_id OR EXISTS (SELECT 1 FROM rating_applications a WHERE a.table_id=t.id)").fetchone()[0]
        if 'table_reversals' in counts:
            checks['reversal_errors']=db.execute(f"SELECT COUNT(*) FROM table_reversals v JOIN tables t ON t.id=v.table_id JOIN table_reversal_requests r ON r.id=v.request_id WHERE t.status!='reversed' OR r.status!='approved' OR r.table_id!=v.table_id OR v.actor_id!=r.reviewer_id OR ({reversal_policy}=1 AND (v.actor_id=t.reporter_id OR v.actor_id=r.requester_id))").fetchone()[0]
            checks['reversed_tables_missing_audit']=db.execute("SELECT COUNT(*) FROM tables t LEFT JOIN table_reversals v ON v.table_id=t.id WHERE t.status='reversed' AND v.table_id IS NULL").fetchone()[0]

    fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
    ok=checks['quick_check_ok'] and all(value==0 for key,value in checks.items() if key!='quick_check_ok')
    return {'audit_version':2,'consistent':bool(ok),'current_season':season,
            'row_counts':counts,'checks':checks,'state_fingerprint':fingerprint}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path('lab-v2.sqlite3'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--compare',type=Path)
    args=parser.parse_args()
    if args.output and (args.output.resolve()==args.db.resolve() or
                        args.compare and args.output.resolve()==args.compare.resolve()):
        parser.error('Output cannot replace the database or comparison file.')
    if args.output and args.output.exists():
        parser.error('Output exists. Use a new filename so earlier evidence is preserved.')
    try:
        report=audit(args.db)
        if args.compare:
            previous=json.loads(args.compare.read_text())
            report['matches_previous_state']=report['state_fingerprint']==previous['state_fingerprint']
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        return 0 if report['consistent'] and report.get('matches_previous_state',True) else 1
    except (OSError,sqlite3.Error,ValueError,KeyError) as error:
        parser.exit(2,f'Audit failed: {error}\nInstall the current S9 lab build and check the database path.\n')

if __name__=='__main__':raise SystemExit(main())
