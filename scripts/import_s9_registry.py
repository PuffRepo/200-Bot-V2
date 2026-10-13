"""Preview or create an offline S9 production registry; no Discord calls."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from v2.registry_import import make_plan,apply_plan

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--preview-dir',type=Path)
    p.add_argument('--names',type=Path,help='Optional JSON mapping Discord IDs to requested unique names')
    p.add_argument('--apply-plan',type=Path)
    p.add_argument('--database',type=Path)
    p.add_argument('--accept-name-changes',action='store_true')
    a=p.parse_args()
    try:
        source=json.loads(a.source.read_text(encoding='utf-8'))
        if a.apply_plan:
            if not a.database or a.preview_dir or a.names:raise ValueError('Apply requires --database; use the reviewed plan without preview/name options.')
            plan=json.loads(a.apply_plan.read_text(encoding='utf-8'))
            result=apply_plan(source,plan,a.database,a.accept_name_changes)
            print(f"Created {a.database}: {result['players']} registered players; S9 unplaced. No Discord changes.")
        else:
            if not a.preview_dir or a.database or a.accept_name_changes:raise ValueError('Preview requires --preview-dir; database and acceptance options are apply-only.')
            names=json.loads(a.names.read_text(encoding='utf-8')) if a.names else {}
            plan=make_plan(source,names)
            a.preview_dir.mkdir(parents=True,exist_ok=False)
            (a.preview_dir/'import-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
            changes=[{k:r[k] for k in ('discord_id','current_display_name','name')} for r in plan['players'] if r['name_changed']]
            (a.preview_dir/'name-review.json').write_text(json.dumps({'exact_duplicates':plan['exact_duplicates'],'approved_duplicate_resolutions':changes},ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"Preview only: {plan['summary']}. Review name-review.json. No database or Discord changes.")
        return 0
    except Exception as e:print(f'Import stopped: {e}');return 2
if __name__=='__main__':raise SystemExit(main())
