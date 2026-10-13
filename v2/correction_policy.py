"""One staff operator for ordinary corrections; disputed results remain independent."""
from v2.lab_store import LabError

def lineage(db,table_id):
    return [r[0] for r in db.execute('''WITH RECURSIVE chain(id) AS (
        SELECT id FROM tables WHERE id=? UNION
        SELECT t.replaces_table_id FROM tables t JOIN chain c ON c.id=t.id WHERE t.replaces_table_id IS NOT NULL)
        SELECT id FROM chain''',(table_id,))]

def flagged(db,table_id):
    return any(db.execute("SELECT 1 FROM table_flags WHERE table_id=? AND status IN ('open','upheld')",(tid,)).fetchone() for tid in lineage(db,table_id))

def disputed_lineage(db,table_id):
    return flagged(db,table_id) or any(db.execute("SELECT 1 FROM table_reversal_requests WHERE table_id=? AND status='approved' AND requires_independent=1",(tid,)).fetchone() for tid in lineage(db,table_id))

def reversal_independent(db,request):
    return bool(request['requires_independent'] or disputed_lineage(db,request['table_id']))

def assert_reversal_reviewer(db,request,actor_id):
    required=reversal_independent(db,request)
    if required:
        parties={request['requester_id'],db.execute('SELECT reporter_id FROM tables WHERE id=?',(request['table_id'],)).fetchone()[0]}
        for tid in lineage(db,request['table_id']):parties.update(r[0] for r in db.execute('SELECT actor_id FROM table_flags WHERE table_id=?',(tid,)))
        if actor_id in parties:raise LabError('A disputed correction requires staff independent of its reporter, requester and flagger.')
    return required

def replacement_independent(db,table_id):
    table=db.execute('SELECT reporter_id,replaces_table_id FROM tables WHERE id=?',(table_id,)).fetchone()
    if not table:raise LabError('Table not found.')
    if table['replaces_table_id'] is None:return True
    request=db.execute('SELECT r.* FROM table_reversals v JOIN table_reversal_requests r ON r.id=v.request_id WHERE v.table_id=?',(table['replaces_table_id'],)).fetchone()
    if not request:raise LabError('Replacement is missing its audited original reversal.')
    return bool(request['requires_independent'] or disputed_lineage(db,table_id))

def assert_replacement_reviewer(db,table_id,actor_id):
    required=replacement_independent(db,table_id)
    table=db.execute('SELECT reporter_id,replaces_table_id FROM tables WHERE id=?',(table_id,)).fetchone()
    if required:
        parties={table['reporter_id']}
        if table['replaces_table_id'] is not None:
            request=db.execute('SELECT r.requester_id,t.reporter_id FROM table_reversals v JOIN table_reversal_requests r ON r.id=v.request_id JOIN tables t ON t.id=v.table_id WHERE v.table_id=?',(table['replaces_table_id'],)).fetchone()
            parties.update((request['requester_id'],request['reporter_id']))
        for tid in lineage(db,table_id):parties.update(r[0] for r in db.execute('SELECT actor_id FROM table_flags WHERE table_id=?',(tid,)))
        if actor_id in parties:raise LabError('A disputed correction requires an independent staff reviewer.')
    return required
