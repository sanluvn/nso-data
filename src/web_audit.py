"""Read-only font inventory; legacy-font evidence is not a full encoding verdict."""
import csv
from collections import Counter
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
from src.web_layout import inspect_manifests, safe_path


def legacy(name):
    return name.casefold().startswith(('.vn', 'vni-'))


def audit_fonts(root, destination):
    import xlrd
    import openpyxl
    rows = []
    manifests,_ = inspect_manifests(root)
    for rid,(mp,m) in manifests.items():
        for a in m['artifacts']:
            v = next(r for r in a['revisions'] if r['revision_number']==a['current_revision'])
            p = safe_path(root,v['local_relative_path'])
            if p.suffix.lower() not in {'.xls','.xlsx','.doc','.docx'}:
                continue
            fonts, text_count, legacy_count, samples = Counter(),0,0,[]
            status=''; error=''
            try:
                if p.suffix.lower()=='.xls':
                    b=xlrd.open_workbook(p,formatting_info=True,logfile=open_log())
                    try:
                        for s in b.sheets():
                            for row in s:
                                for c in row:
                                    if c.ctype!=xlrd.XL_CELL_TEXT or not c.value.strip():continue
                                    font=b.font_list[b.xf_list[c.xf_index].font_index].name
                                    text_count+=1;fonts[font]+=1
                                    # Run-level fonts are checked separately below using coordinates.
                                    if legacy(font):
                                        legacy_count+=1
                                        if len(samples)<3:samples.append(c.value[:90])
                            for (row,col),runs in s.rich_text_runlist_map.items():
                                for offset,index in runs:
                                    font=b.font_list[index].name
                                    if legacy(font):fonts[font]+=0  # Flag font without inflating cell counts.
                    finally:b.release_resources()
                    status='LEGACY_FONT_TEXT' if any(legacy(f) for f in fonts) else 'NO_LEGACY_FONT_DETECTED'
                elif p.suffix.lower()=='.xlsx':
                    b=openpyxl.load_workbook(p,read_only=True,data_only=False)
                    try:
                        for s in b:
                            for row in s:
                                for c in row:
                                    if not isinstance(c.value,str) or c.data_type=='f' or not c.value.strip():continue
                                    font=c.font.name or ''
                                    text_count+=1;fonts[font]+=1
                                    if legacy(font):
                                        legacy_count+=1
                                        if len(samples)<3:samples.append(c.value[:90])
                    finally:b.close()
                    # Count advertised run fonts as evidence too (streaming reader omits rich text).
                    with zipfile.ZipFile(p) as z:
                        for name in z.namelist():
                            if name=='xl/sharedStrings.xml' or name.startswith('xl/worksheets/') and name.endswith('.xml'):
                                for element in ET.fromstring(z.read(name)).iter():
                                    if element.tag.rsplit('}',1)[-1]=='rFont':
                                        font=element.attrib.get('val','')
                                        if legacy(font):fonts[font]+=0
                    status='LEGACY_FONT_TEXT' if any(legacy(f) for f in fonts) else 'NO_LEGACY_FONT_DETECTED'
                elif p.suffix.lower()=='.docx':
                    with zipfile.ZipFile(p) as z:
                        for name in z.namelist():
                            if name.startswith('word/') and name.endswith('.xml'):
                                tree=ET.fromstring(z.read(name))
                                for e in tree.iter():
                                    tag=e.tag.rsplit('}',1)[-1]
                                    if tag=='rFonts':
                                        for key,font in e.attrib.items():
                                            if key.rsplit('}',1)[-1] in {'ascii','hAnsi','eastAsia','cs'}:fonts[font]+=1
                    status='LEGACY_FONT_DECLARED' if any(legacy(f) for f in fonts) else 'NO_LEGACY_FONT_DETECTED'
                else:
                    # A binary DOC font-table signature is evidence, not run-level text extraction.
                    data=p.read_bytes()
                    for font in ['.VnTime','.VnTimeH','.VnArial','.VnArialH','.VnAvant','.VnAvantH']:
                        if font.encode('utf-16le')+b'\x00\x00' in data:fonts[font]+=1
                    for enc in ('latin1','utf-16le'):
                        for font in re.findall(r'VNI-[A-Za-z]+',data.decode(enc,errors='ignore')):fonts[font]+=1
                    status='LEGACY_FONT_BINARY_EVIDENCE' if fonts else 'DOC_NOT_FULLY_ASSESSED'
            except Exception as exc:
                status='READ_ERROR';error=f'{type(exc).__name__}: {exc}'
            rows.append({'release_id':rid,'title':m['release_snapshot']['title'],
                         'path':v['local_relative_path'],'status':status,
                         'text_cells':text_count,'legacy_font_cells':legacy_count,
                         'fonts':'; '.join(sorted(fonts)),'examples':' | '.join(samples),'error':error})
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['path','status'])
        w.writeheader();w.writerows(rows)
    print('Font audit:',dict(Counter(r['status'] for r in rows)))
    print('Details:',destination)
    return rows


def open_log():
    import io
    return io.StringIO()
