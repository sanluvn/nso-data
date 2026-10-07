"""Offline regression tests: paths, migration, recovery and production reconciliation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.web_layout import period_info, WebLayout, sha256, safe_path


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m


migration=load('migration_test','08_layout.py')
web=load('acquisition_test','04_web.py')


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.project=Path(self.tmp.name)
        self.root=self.project/'raw/monthly';self.root.mkdir(parents=True)
        self.rid='nso_release_'+'1'*32;self.aid='nso_artifact_'+'2'*32
        self.snapshot={'title':'Báo cáo quý III và 9 tháng năm 2002','reference_period':None}
        self.entry={'release_id':self.rid,'current':self.snapshot}
        self.file=self.root/self.rid/'artifacts/test.xls';self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'fixture original bytes')
        self.manifest={'manifest_type':'nso_web_release_artifact_manifest','release_id':self.rid,
                       'release_snapshot':self.snapshot,'artifact_count':1,
                       'artifacts':[{'artifact_id':self.aid,'artifact_type':'excel',
                                     'source_url':'https://example.invalid/test.xls','source_filename':'test.xls',
                                     'current_revision':1,'revisions':[{'revision_number':1,'byte_size':self.file.stat().st_size,
                                     'sha256':sha256(self.file),'local_relative_path':self.file.relative_to(self.root).as_posix()}]}]}
        migration.write_json(self.file.parent.parent/'manifest.json',self.manifest)
        self.registry=self.project/'registry.json';migration.write_json(self.registry,{'releases':[self.entry]})
        self.patches=[patch.object(migration,'ROOT',self.project),patch.object(migration,'NSO_WEB_MONTHLY_ROOT',self.root),
                      patch.object(migration,'NSO_WEB_RELEASE_REGISTRY_PATH',self.registry)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def run_cli(self,*args):
        with patch.object(sys,'argv',['08_layout.py',*args]):migration.main()
    def test_periods(self):
        cases=[('Báo cáo tháng Một năm 2026',None,'2026-01'),
               ('Báo cáo tháng Năm và 5 tháng năm 2026',None,'2026-05'),
               ('Báo cáo tháng Mười Một và 11 tháng năm 2025',None,'2025-11'),
               ('Báo cáo quý III và 9 tháng năm 2025','9/2025','2025-09'),
               ('Báo cáo quý IV và năm 2022','2022','2022-12'),
               ('Cập nhật ngày 11/9/2023',None,None),
               ('Báo cáo tháng 4 năm 2020','5/2020',None)]
        for title,ref,expected in cases:
            self.assertEqual(period_info({'title':title,'reference_period':ref})['period'],expected)
    def test_migration_idempotence_and_backup(self):
        original_registry=self.registry.read_bytes()
        self.run_cli('--apply')
        plans,_=migration.build_plan(self.root,{'releases':[self.entry]})
        self.assertTrue(plans[0][0].parent.name == '2002-09')
        self.assertEqual(plans[0][1]['artifacts'][0]['source_filename'],'test.xls')
        self.assertEqual(self.registry.read_bytes(),original_registry)
        self.assertTrue((self.root.parent/'.web_layout_backup'/self.rid/'artifacts/test.xls').exists())
        before={str(p):sha256(p) for p in self.root.rglob('*') if p.is_file()}
        self.run_cli('--apply')
        self.assertEqual(before,{str(p):sha256(p) for p in self.root.rglob('*') if p.is_file()})
    def test_modified_bytes_block_apply(self):
        self.file.write_bytes(b'locally modified bytes')
        self.run_cli()
        with self.assertRaisesRegex(RuntimeError,'BLOCKED'):self.run_cli('--apply')
        self.assertEqual(self.file.read_bytes(),b'locally modified bytes')
    def test_recovery_between_swaps(self):
        backup=self.root.parent/'.web_layout_backup';stage=self.root.parent/'.web_layout_stage'
        stage.mkdir();journal=self.root.parent/'.web_layout_journal.json'
        migration.write_json(journal,{'backup':str(backup),'stage':str(stage)})
        self.root.rename(backup)
        with self.assertRaises(RuntimeError):WebLayout(self.root)
        self.run_cli('--recover')
        self.assertTrue(self.file.exists());self.assertFalse(journal.exists())
    def test_exact_hash_repair_retains_local_copy(self):
        from types import SimpleNamespace
        original=self.file.read_bytes();self.file.write_bytes(b'local edits')
        findings=[];migration.build_plan(self.root,{'releases':[self.entry]},findings)
        def remote(session,url,temp,headers=None):
            temp.write_bytes(original);return 200,{}
        fake=SimpleNamespace(build_session=lambda:SimpleNamespace(close=lambda:None),remote_request_with_retry=remote)
        migration.restore_originals(self.root,findings,web=fake)
        self.assertEqual(self.file.read_bytes(),original)
        copies=list((self.project/'data/local_backups').rglob('test.xls'))
        self.assertEqual(len(copies),1);self.assertEqual(copies[0].read_bytes(),b'local edits')
        migration.build_plan(self.root,{'releases':[self.entry]})
    def test_repair_rejects_changed_remote(self):
        from types import SimpleNamespace
        self.file.write_bytes(b'local edits')
        findings=[];migration.build_plan(self.root,{'releases':[self.entry]},findings)
        def remote(session,url,temp,headers=None):
            temp.write_bytes(b'new remote version');return 200,{}
        fake=SimpleNamespace(build_session=lambda:SimpleNamespace(close=lambda:None),remote_request_with_retry=remote)
        with self.assertRaises(RuntimeError):migration.restore_originals(self.root,findings,web=fake)
        self.assertEqual(self.file.read_bytes(),b'local edits')
    def test_duplicate_release_and_artifact_names_stable(self):
        entries=[self.entry,{'release_id':'nso_release_'+'3'*32,
                  'current':{**self.snapshot,'publication_url':'https://example.invalid/second'}}]
        empty=self.project/'empty'
        layout=WebLayout(empty)
        layout.register_all(entries)
        self.assertEqual(layout.directory(self.rid).name,'2002-09')
        self.assertEqual(layout.directory(entries[1]['release_id']).name,'2002-09_02')
        first=layout.target(self.rid,self.aid,'excel',1,'a.xls')
        second=layout.target(self.rid,'nso_artifact_'+'4'*32,'excel',1,'b.xls')
        self.assertEqual(first.name,'2002-09_bang-so-lieu.xls')
        self.assertEqual(second.name,'2002-09_bang-so-lieu_02.xls')
        self.assertEqual(layout.target(self.rid,self.aid,'excel',1,'renamed.xls'),first)
        directory=layout.directory(self.rid);directory.mkdir(parents=True)
        manifest={**self.manifest,'local_layout':layout.layouts[self.rid]}
        migration.write_json(directory/'manifest.json',manifest)
        reopened=WebLayout(empty)
        self.assertEqual(reopened.target(self.rid,'nso_artifact_'+'4'*32,'excel',1,'b.xls'),second)
    def test_clean_download_deterministic_without_uuid_order(self):
        entries=[{'release_id':'nso_release_'+'a'*32,'current':{**self.snapshot,'publication_url':'https://example.invalid/a'}},
                 {'release_id':'nso_release_'+'b'*32,'current':{**self.snapshot,'publication_url':'https://example.invalid/b'}}]
        one=WebLayout(self.project/'one');one.register_all(entries)
        changed=[{**e,'release_id':'nso_release_'+str(i+5)*32} for i,e in enumerate(entries)]
        two=WebLayout(self.project/'two');two.register_all(list(reversed(changed)))
        self.assertEqual([one.directory(e['release_id']).name for e in entries],
                         [two.directory(e['release_id']).name for e in changed])
    def test_path_escape(self):
        for p in ['../outside','C:\\secret','/tmp/other']:
            with self.assertRaises(ValueError):safe_path(self.root,p)
    def test_production_existing_304_changed_new(self):
        self.run_cli('--apply')
        candidate=web.ArtifactCandidate(artifact_url='https://example.invalid/test.xls',
                     anchor_text='data',artifact_type='excel',source_filename='test.xls')
        discovery=type('Discovery',(),{'artifacts':(candidate,)})()
        with patch.object(web,'SERIES_ROOT',self.root),patch.object(web,'_WEB_LAYOUT',None):
            with patch.object(web,'remote_request_with_retry',return_value=(304,{})):
                stats=web.reconcile_release_artifacts(None,self.entry,discovery)
            self.assertEqual(stats['unchanged_304'],1)
            def changed(session,url,temp,headers=None):
                temp.write_bytes(b'new source revision');return 200,{}
            with patch.object(web,'remote_request_with_retry',side_effect=changed):
                stats=web.reconcile_release_artifacts(None,self.entry,discovery)
            self.assertEqual(stats['changed'],1)
            m=web.load_release_manifest(self.rid);web.validate_manifest(m)
            self.assertIn('/r0002/2002-09_bang-so-lieu.xls',m['artifacts'][0]['revisions'][1]['local_relative_path'])
            entry={'release_id':'nso_release_'+'3'*32,'current':{'title':'Báo cáo tháng 10 năm 2002'}}
            with patch.object(web,'remote_request_with_retry',side_effect=changed):
                web.reconcile_release_artifacts(None,entry,discovery)
            m=web.load_release_manifest(entry['release_id']);web.validate_manifest(m)
            self.assertTrue(web.get_release_directory(entry['release_id']).name == '2002-10')


if __name__=='__main__':unittest.main()
