"""Offline chronological migration. Default: inspect; --apply: verified copy-and-swap."""
import argparse
import copy
import csv
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.paths import NSO_WEB_MONTHLY_ROOT, NSO_WEB_RELEASE_REGISTRY_PATH
from src.web_layout import (inspect_manifests, read_json, sha256, safe_path,
                            planned_layouts, artifact_relative, period_info, LAYOUT_VERSION)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def build_plan(root, registry, findings=None):
    manifests, others = inspect_manifests(root)
    ids = [e['release_id'] for e in registry['releases']]
    if len(ids) != len(set(ids)) or set(ids) != set(manifests):
        raise RuntimeError('Registry/production manifest IDs differ. No files changed.')
    layouts = planned_layouts(manifests)
    plans, used_targets, used_sources = [], set(), set()
    used_folders = set()
    global_artifact_ids = set()
    for rid, (mp, original) in manifests.items():
        m = copy.deepcopy(original)
        folder = layouts[rid]['directory']
        if folder.casefold() in used_folders:
            raise RuntimeError(f'Release folder collision: {folder}')
        used_folders.add(folder.casefold())
        m['local_layout'] = layouts[rid]
        if m['artifact_count'] != len(m['artifacts']):
            raise RuntimeError(f'Artifact count mismatch: {rid}')
        files = []
        artifact_ids, source_urls = set(), set()
        for a in m['artifacts']:
            if a['artifact_id'] in artifact_ids or a['source_url'] in source_urls:
                raise RuntimeError(f'Duplicate artifact: {rid}')
            if a['artifact_id'] in global_artifact_ids:
                raise RuntimeError(f'Duplicate global artifact ID: {a["artifact_id"]}')
            global_artifact_ids.add(a['artifact_id'])
            artifact_ids.add(a['artifact_id']); source_urls.add(a['source_url'])
            nums = [r['revision_number'] for r in a['revisions']]
            if not nums or nums != list(range(1,len(nums)+1)) or a['current_revision'] != nums[-1]:
                raise RuntimeError(f'Invalid revisions: {rid}')
            for v in a['revisions']:
                src = safe_path(root, v['local_relative_path'])
                if not src.is_file():
                    raise RuntimeError(f'Missing source: {src}')
                actual_hash = sha256(src)
                if src.stat().st_size != v['byte_size'] or actual_hash != v['sha256']:
                    if findings is None:
                        raise RuntimeError(f'Altered source: {src}')
                    findings.append({'release_id':rid, 'title':m['release_snapshot']['title'],
                                     'path':v['local_relative_path'],
                                     'expected_bytes':v['byte_size'], 'actual_bytes':src.stat().st_size,
                                     'expected_sha256':v['sha256'], 'actual_sha256':actual_hash})
                if src in used_sources or not src.is_relative_to(mp.parent.resolve()):
                    raise RuntimeError(f'Duplicate/cross-release source path: {src}')
                used_sources.add(src)
                n = v['revision_number']
                rel = artifact_relative(m['local_layout'], a['artifact_id'], n, src.name).as_posix()
                if rel.casefold() in used_targets:
                    raise RuntimeError(f'Case-insensitive path collision: {rel}')
                used_targets.add(rel.casefold())
                files.append((src,rel,v['sha256']))
                v['local_relative_path'] = rel
        # Refuse to discard unknown files in any production directory.
        actual = {p.resolve() for p in mp.parent.rglob('*') if p.is_file() and p != mp}
        expected = {src for src,rel,h in files}
        if actual != expected:
            raise RuntimeError(f'Untracked files in {mp.parent}; inspect before migration.')
        plans.append((mp,m,folder,files))
    # Keep prototype/other folders byte-for-byte; no guessed IDs or deletion.
    return plans, others


def recover(root, journal):
    state = read_json(journal)
    backup = Path(state['backup']); stage = Path(state['stage'])
    # Recover only our fixed sibling paths, never arbitrary journal paths.
    if backup != root.parent / '.web_layout_backup' or stage != root.parent / '.web_layout_stage':
        raise RuntimeError('Unexpected recovery paths')
    if backup.exists():
        if root.exists():
            failed = root.parent / ('.web_layout_recovered_' + datetime.now().strftime('%Y%m%d%H%M%S%f'))
            root.rename(failed)
            print('Preserved interrupted output:',failed)
        backup.rename(root)
    elif not root.exists():
        raise RuntimeError('Neither original nor backup found; manual recovery required.')
    if stage.exists():
        shutil.rmtree(stage)
    journal.unlink()
    print('RECOVERED original source directory. No source bytes changed.')


def restore_originals(root, findings, web=None):
    """Restore only bytes matching the acquisition hash; retain changed local copies."""
    if web is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('web_acquisition_repair', ROOT/'scripts/04_web.py')
        web = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = web
        spec.loader.exec_module(web)
    manifests,_ = inspect_manifests(root)
    by_path = {r['local_relative_path']:a['source_url']
               for p,m in manifests.values() for a in m['artifacts'] for r in a['revisions']}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT/'data/local_backups'/('web_changed_'+stamp)
    session = web.build_session()
    results = []
    try:
        for finding in findings:
            relative = finding['path']
            current = safe_path(root,relative)
            temporary = current.with_name(current.name+'.restore.part')
            if temporary.exists():
                raise RuntimeError(f'Unexpected repair temporary file: {temporary}')
            result = {**finding,'status':'NOT_RESTORED'}
            try:
                status,_ = web.remote_request_with_retry(session,by_path[relative],temporary)
                if status == 304 or not temporary.exists():
                    raise RuntimeError('Full source bytes were not returned')
                if (sha256(temporary) != finding['expected_sha256'] or
                        temporary.stat().st_size != finding['expected_bytes']):
                    raise RuntimeError('Remote bytes do not match original hash; local file retained')
                if sha256(current)!=finding['actual_sha256']:
                    raise RuntimeError('Local file changed during repair; retained')
                saved=backup/relative
                saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(current,saved)
                if sha256(saved)!=finding['actual_sha256']:
                    raise RuntimeError('Backup verification failed')
                os.replace(temporary,current)
                result['status']='RESTORED_EXACT_ORIGINAL'
                result['backup']=str(saved.relative_to(ROOT))
            except Exception as exc:
                result['error']=str(exc)
            finally:
                temporary.unlink(missing_ok=True)
            results.append(result)
            print(result['status'],relative)
    finally:
        session.close()
    write_json(ROOT/'reports/qc/web_restore_results.json',results)
    if any(r['status']!='RESTORED_EXACT_ORIGINAL' for r in results):
        raise RuntimeError('Some original bytes could not be restored. See web_restore_results.json.')
    print(f'Restored {len(results)} exact originals; changed local copies retained in {backup}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply',action='store_true')
    group.add_argument('--recover',action='store_true')
    group.add_argument('--restore-originals',action='store_true',help='Download only mismatched artifacts; require exact original SHA-256')
    parser.add_argument('--audit-fonts',action='store_true',help='Read all current Excel/Word artifacts; no conversion')
    args = parser.parse_args()
    root = NSO_WEB_MONTHLY_ROOT
    journal = root.parent / '.web_layout_journal.json'
    if args.recover:
        if not journal.exists():
            print('No interrupted migration.'); return
        recover(root,journal); return
    if journal.exists():
        raise RuntimeError('Interrupted migration: run with --recover first.')
    findings = []
    plans,others = build_plan(root,read_json(NSO_WEB_RELEASE_REGISTRY_PATH), findings)
    report = ROOT/'reports/inventory/web_layout_plan.csv'
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['release_id','period','basis','old_path','new_path','sha256'])
        for mp,m,folder,files in plans:
            for src,rel,h in files:
                w.writerow([m['release_id'],m['local_layout']['period'],m['local_layout']['basis'],
                            src.relative_to(root.resolve()).as_posix(),rel,h])
    write_json(ROOT/'reports/qc/web_integrity_findings.json',findings)
    if args.audit_fonts:
        from src.web_audit import audit_fonts
        audit_fonts(root,ROOT/'reports/qc/web_font_audit.csv')
    changed = [(mp,m,folder,files) for mp,m,folder,files in plans
               if mp.parent.name != folder or read_json(mp) != m]
    unresolved = [m['release_id'] for mp,m,folder,files in plans if not m['local_layout']['period']]
    print(f'Production releases: {len(plans)} | revisions: {sum(len(x[3]) for x in plans)}')
    print(f'Releases to change: {len(changed)} | other manifests: {len(others)} | undated: {len(unresolved)}')
    print('Plan:', report)
    print(f'Integrity mismatches: {len(findings)}')
    if args.restore_originals:
        restore_originals(root,findings)
        return
    if args.apply and findings:
        raise RuntimeError('Migration BLOCKED: resolve the integrity findings first; source hashes were NOT rewritten.')
    if not args.apply or not changed:
        print('NO RAW CHANGES' if not args.apply else 'ALREADY MIGRATED');return
    stage=root.parent/'.web_layout_stage';backup=root.parent/'.web_layout_backup'
    if stage.exists() or backup.exists():
        raise RuntimeError('Existing staging/backup directory: preserve or relocate it before another migration.')
    # Preflight ownership of every destination, including case-insensitive Windows names.
    original_dirs = {mp.parent.resolve() for mp,m,folder,files in plans}
    existing = {p.name.casefold():p for p in root.iterdir()}
    for mp,m,folder,files in plans:
        clash=existing.get(folder.casefold())
        if clash and clash.resolve()!=mp.parent.resolve():
            raise RuntimeError(f'Destination exists: {clash}')
    total=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    if shutil.disk_usage(root.parent).free < total * 2 + 10_000_000:
        raise RuntimeError('Insufficient space for staging and temporary copied artifacts.')
    write_json(journal,{'backup':str(backup),'stage':str(stage),'created_at':datetime.now(timezone.utc).isoformat()})
    try:
        shutil.copytree(root,stage)
        for mp,m,folder,files in plans:
            # Source remains untouched until all target bytes and manifests validate.
            shutil.rmtree(stage/mp.parent.relative_to(root))
        for mp,m,folder,files in plans:
            for src,rel,h in files:
                target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(src,target)
                if sha256(target)!=h:raise RuntimeError(f'Copy hash mismatch: {rel}')
            write_json(stage/folder/'manifest.json',m)
        for p in others:
            # Separate legacy prototype folders from production, retaining internal names.
            relative=p.parent.relative_to(root)
            if len(relative.parts)==1:
                dest=stage/'_legacy_originals'/relative
                if dest.exists():raise RuntimeError(f'Legacy destination exists: {dest}')
                dest.parent.mkdir(exist_ok=True)
                (stage/relative).rename(dest)
        build_plan(stage,read_json(NSO_WEB_RELEASE_REGISTRY_PATH))
        root.rename(backup)
        stage.rename(root)
        journal.unlink()
    except BaseException:
        # Journal survives for explicit recovery, including power loss between renames.
        raise
    print('PASS: all artifact hashes preserved; all production paths resolve.')
    print('Original byte-for-byte directory retained:',backup)
    print('Registry unchanged: it contains identities/metadata, not artifact paths.')


if __name__=='__main__':
    main()
