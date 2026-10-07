"""Chronological local paths; source identities and original filenames stay unchanged."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

MANIFEST_TYPE = 'nso_web_release_artifact_manifest'
LAYOUT_VERSION = 'short-v2'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    # Reject Windows paths even when validating on Linux.
    if not relative or '\\' in relative or ':' in relative:
        raise ValueError(f'Unsafe relative path: {relative!r}')
    p = (Path(root) / relative).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError(f'Path outside source root: {relative}')
    return p


def period_info(snapshot):
    """Folder period is release coverage end, not each observation's period."""
    title = unicodedata.normalize('NFC', snapshot.get('title') or '').casefold()
    ref = unicodedata.normalize('NFC', snapshot.get('reference_period') or '').casefold().strip()
    explicit = re.fullmatch(r'(?:tháng\s*)?(0?[1-9]|1[0-2])[/\-](\d{4})', ref)
    from_ref = f'{explicit[2]}-{int(explicit[1]):02d}' if explicit else None
    years = set(re.findall(r'\bnăm\s+((?:19|20)\d{2})\b', title))
    from_title = None
    if len(years) == 1:
        year = next(iter(years))
        m = re.search(r'\btháng\s+(1[0-2]|0?[1-9])\b', title)
        month = int(m[1]) if m else None
        if month is None:
            q = re.search(r'\bquý\s+(iv|iii|ii|i|[1-4])\b', title)
            if q:
                month = {'i':3,'ii':6,'iii':9,'iv':12,'1':3,'2':6,'3':9,'4':12}[q[1]]
        if month is None:
            words = {'mười hai':12, 'mười một':11, 'mười':10, 'chín':9,
                     'tám':8, 'bảy':7, 'sáu':6, 'năm':5, 'tư':4, 'bốn':4,
                     'ba':3, 'hai':2, 'một':1}
            for word, number in words.items():
                if re.search(r'\btháng\s+' + word + r'\b', title):
                    month = number
                    break
        if month:
            from_title = f'{year}-{month:02d}'
    if from_ref and from_title and from_ref != from_title:
        return {'period': None, 'basis':'conflicting_metadata', 'reference_candidate':from_ref,
                'title_candidate':from_title}
    return {'period': from_ref or from_title,
            'basis': 'reference_period' if from_ref else ('title' if from_title else 'unresolved')}


def allocate_name(base, occupied):
    number = 1
    name = base
    while name.casefold() in occupied:
        number += 1
        name = f'{base}_{number:02d}'
    occupied.add(name.casefold())
    return name


def release_key(item):
    rid, snapshot = item
    return (period_info(snapshot)['period'] or 'UNDATED',
            snapshot.get('publication_date') or '', snapshot.get('publication_url') or '',
            snapshot.get('title') or '', rid)


def document_label(kind):
    return {'excel':'bang-so-lieu','word':'loi-van','pdf':'tai-lieu',
            'archive':'tep-nen'}.get(kind,'tai-lieu')


def allocate_stem(layout, aid, kind):
    stems = layout.setdefault('artifact_stems', {})
    if aid not in stems:
        base = (layout['period'] or 'UNDATED') + '_' + document_label(kind)
        stems[aid] = allocate_name(base, {s.casefold() for s in stems.values()})
    return stems[aid]


def artifact_relative(layout, aid, revision, filename):
    extension = Path(filename).suffix.lower()
    if extension not in {'.xls','.xlsx','.doc','.docx','.pdf','.zip','.rar','.7z'}:
        raise ValueError(f'Unsupported artifact extension: {filename}')
    stem = layout['artifact_stems'][aid]
    filename = stem + extension
    if revision == 1:
        return Path(layout['directory'])/'artifacts'/filename
    return Path(layout['directory'])/'revisions'/stem/f'r{revision:04d}'/filename


def validate_layout(layout):
    if not re.fullmatch(r'(?:\d{4}-\d{2}|UNDATED)(?:_\d{2,})?',layout['directory']):
        raise RuntimeError('Invalid short release directory')
    stems=list(layout.get('artifact_stems',{}).values())
    if len({s.casefold() for s in stems})!=len(stems):
        raise RuntimeError('Duplicate allocated artifact names')
    if any(not re.fullmatch(r'(?:\d{4}-\d{2}|UNDATED)_[a-z-]+(?:_\d{2,})?',s) for s in stems):
        raise RuntimeError('Unsafe artifact stem')


def planned_layouts(manifests):
    import copy
    layouts, occupied = {}, set()
    for rid,(p,m) in manifests.items():
        layout=m.get('local_layout',{})
        if layout.get('version')==LAYOUT_VERSION:
            validate_layout(layout)
            if layout['directory'].casefold() in occupied:
                raise RuntimeError('Duplicate allocated release directories')
            if layout['directory'] != p.parent.name:
                raise RuntimeError('Manifest layout/directory disagreement')
            layouts[rid]=copy.deepcopy(layout)
            occupied.add(layout['directory'].casefold())
    ordered=sorted(((rid,m['release_snapshot']) for rid,(p,m) in manifests.items()),key=release_key)
    for rid,snapshot in ordered:
        if rid not in layouts:
            info=period_info(snapshot)
            layouts[rid]={'version':LAYOUT_VERSION,**info,
                          'directory':allocate_name(info['period'] or 'UNDATED',occupied),
                          'artifact_stems':{}}
        for artifact in sorted(manifests[rid][1]['artifacts'],key=lambda a:(a['source_url'],a['artifact_id'])):
            allocate_stem(layouts[rid],artifact['artifact_id'],artifact['artifact_type'])
    return layouts


def inspect_manifests(root):
    production, other = {}, []
    for path in sorted(Path(root).rglob('manifest.json')):
        m = read_json(path)
        if m.get('manifest_type') != MANIFEST_TYPE:
            other.append(path)
            continue
        rid = m['release_id']
        if rid in production:
            raise RuntimeError(f'Duplicate production release: {rid}')
        production[rid] = (path, m)
    return production, other


class WebLayout:
    """Persist display names in manifests; source identity never depends on filenames."""
    def __init__(self, root):
        import copy
        self.root=Path(root)
        if (self.root.parent/'.web_layout_journal.json').exists():
            raise RuntimeError('Interrupted layout migration: run 08_layout.py --recover first.')
        production,_=inspect_manifests(root)
        self.directories={rid:p.parent for rid,(p,m) in production.items()}
        self.layouts={rid:copy.deepcopy(m['local_layout']) for rid,(p,m) in production.items()
                      if m.get('local_layout',{}).get('version')==LAYOUT_VERSION}
        for rid,layout in self.layouts.items():
            validate_layout(layout)
            if layout['directory']!=self.directories[rid].name:
                raise RuntimeError('Manifest layout/directory disagreement')
        self.versioned=set(self.layouts)
        self.snapshots={}

    def register(self,entry):
        self.snapshots[entry['release_id']]=entry['current']

    def register_all(self,entries):
        for entry in entries:self.register(entry)
        for rid,snapshot in sorted(self.snapshots.items(),key=release_key):
            self.directory(rid)

    def directory(self,rid):
        if rid not in self.directories:
            if rid not in self.snapshots:raise RuntimeError(f'No snapshot for {rid}')
            info=period_info(self.snapshots[rid])
            used={p.name.casefold() for p in self.root.iterdir()} if self.root.exists() else set()
            used.update(p.name.casefold() for p in self.directories.values())
            name=allocate_name(info['period'] or 'UNDATED',used)
            self.directories[rid]=self.root/name
            self.layouts[rid]={'version':LAYOUT_VERSION,**info,'directory':name,'artifact_stems':{}}
            self.versioned.add(rid)
        return self.directories[rid]

    def target(self,rid,aid,kind,revision,filename):
        directory=self.directory(rid)
        if rid in self.versioned:
            allocate_stem(self.layouts[rid],aid,kind)
            return self.root/artifact_relative(self.layouts[rid],aid,revision,filename)
        if Path(filename).name!=filename or '\\' in filename or ':' in filename:
            raise ValueError(f'Unsafe filename: {filename}')
        if revision==1:return directory/'artifacts'/filename
        return directory/'revisions'/aid/f'r{revision:04d}'/filename
