"""Recompute retained opening dimensions against the laser-derived IFC.

Usage: python verify_reference.py /path/to/geometry-real-room-v1 --output result.json
Uses standard library only; reads source bytes without modifying the package.
This verifies source binding and numerical comparison, not unseen object boundaries.
"""
import argparse,csv,hashlib,json,math,re
from pathlib import Path

def verify(root):
    root=Path(root)
    load=lambda name:json.loads((root/name).read_text())
    hashfile=lambda name:hashlib.sha256((root/name).read_bytes()).hexdigest()
    manifest=load('result-manifest.json');ifc=(root/'reference.ifc').read_text()
    checksums={}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split(maxsplit=1);checksums[name.lstrip('*').removeprefix('./')]=digest
    for name in ['result-manifest.json','reference.ifc','reports/mesh_boundaries/objects.json','demo/geometry-demo/result.json']:
        if checksums.get(name)!=hashfile(name):raise ValueError('Package integrity mismatch: '+name)
    if hashfile('reference.ifc')!=manifest['ifc_sha256']:raise ValueError('IFC identity mismatch')
    references={}
    for kind,body in re.findall(r'=IFC(DOOR|WINDOW)\(([^\n]+)\);',ifc):
        fields=next(csv.reader([body],quotechar="'",skipinitialspace=True))
        references[fields[0]]={'kind':kind,'height':float(fields[8]),'width':float(fields[9])}
    boundaries={o['object_id']:o for o in load('reports/mesh_boundaries/objects.json')['objects']}
    compact=load('demo/geometry-demo/result.json')
    # Packaged viewer projection may wrap the compact result.
    if 'objects' not in compact:raise ValueError('Unsupported compact result layout')
    checks=[];unmatched=[]
    for obj in compact['objects']:
        guid=obj.get('ifc_guid');oid=obj.get('scan_object_id') or obj['id']
        if guid not in references:unmatched.append(oid);continue
        boundary=boundaries[oid];corners=boundary['candidate_corners_world_m']
        measured={'width':math.dist(corners[0],corners[1]),'height':math.dist(corners[1],corners[2])}
        for dimension in ('width','height'):
            values=obj['dimensions'][dimension]
            observed=measured[dimension];reference=references[guid][dimension]
            if values['scan'] is None:raise ValueError('Matched object has no retained measurement')
            if not math.isclose(observed,values['scan'],abs_tol=1e-6):raise ValueError('Contour length differs from reported scan measurement')
            if not math.isclose(reference,values['ifc'],abs_tol=1e-6):raise ValueError('IFC property differs from reported reference')
            if not math.isclose(observed-reference,values['difference'],abs_tol=1e-6):raise ValueError('Reported delta arithmetic mismatch')
            checks.append({'object_id':oid,'ifc_guid':guid,'dimension':dimension,'reference_m':reference,'observed_m':observed,'signed_difference_mm':(observed-reference)*1000,'absolute_difference_mm':abs(observed-reference)*1000,'reference_comparison_verified':True,'boundary_supported_on_all_sides':obj['quality']['boundary_supported_on_all_sides'],'absolute_accuracy_verified':False,'verification_scope':'IFC identity, retained contour length, reference dimension and delta recomputed; boundary correspondence not independently established'})
    return {'kind':'reference_dimension_verification','source_session_id':manifest['session_id'],'geometry_job_id':manifest['job_id'],'ifc_sha256':manifest['ifc_sha256'],'reference_provenance':'IFC constructed from laser measurements, confirmed by project team on 2026-11-04','checks':checks,'unmatched_objects':unmatched,'all_numeric_checks_passed':bool(checks),'source_hashes':{f:hashfile(f) for f in ['reference.ifc','reports/mesh_boundaries/objects.json','demo/geometry-demo/result.json']},'whole_element_verification':'Not established: retained boundary evidence is partial; no hidden surface is declared verified.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=verify(a.package);a.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'checks':len(r['checks']),'passed':r['all_numeric_checks_passed']}))
