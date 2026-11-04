"""Recompute descriptive stage metrics from saved evidence; no model or service calls."""
import json, hashlib, statistics, platform
from pathlib import Path
from datetime import datetime
root=Path(__file__).resolve().parent
read=lambda name:json.loads((root/name).read_text())
frames=read('cameras.json')['frames']
assert len(frames)==77 and len({r['frame_id'] for r in frames})==77
statuses=('accepted_raw','accepted_refined','rejected')
assert all(r['status'] in statuses for r in frames)
def summarize(values):
 if not values:return None
 return {'n_frames':len(values),'mean_frame_median_mm':statistics.mean(v['median_m'] for v in values)*1000,'median_frame_median_mm':statistics.median(v['median_m'] for v in values)*1000,'mean_frame_p90_mm':statistics.mean(v['p90_m'] for v in values)*1000,'mean_frame_agreement':statistics.mean(v['agreement'] for v in values)}
rows=[]
for r in frames:
 before=r['before']['held_out'];proposal=r['proposal']['held_out']
 selected=None if r['status']=='rejected' else proposal if r['status']=='accepted_refined' else before
 rows.append({'frame_id':r['frame_id'],'status':r['status'],'reasons':r['reasons'],'before':{k:v for k,v in before.items() if k!='tiles'},'proposal':{k:v for k,v in proposal.items() if k!='tiles'},'selected':None if selected is None else {k:v for k,v in selected.items() if k!='tiles'}})
retained=[r for r in rows if r['selected'] is not None];refined=[r for r in rows if r['status']=='accepted_refined'];rejected=[r for r in rows if r['selected'] is None]
first=read('sam-first.json');last=read('sam-final.json');history=read('history.json')
identity=['model','parameters','source_manifest_sha256','detection_count','class_counts','selected_count','processed_count']
assert all(first[k]==last[k] for k in identity)
timings={}
for j in history['jobs']:
 parse=lambda t:datetime.fromisoformat(t)
 total=(parse(j['finished_at'])-parse(j['started_at'])).total_seconds()
 stage=next(s for s in history['stages'] if s['job_id']==j['id'] and s['name']=='segmentation')
 sam=None if stage['started_at'] is None else (parse(stage['finished_at'])-parse(stage['started_at'])).total_seconds()
 timings[j['id']]={'total_seconds':total,'segmentation_stage_seconds':sam}
result={'kind':'descriptive_reanalysis_of_saved_diagnostics','execution':{'platform':platform.system(),'machine':platform.machine(),'python':platform.python_version()},'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.is_file() and p.name in ['cameras.json','sam-first.json','sam-final.json','history.json','protocol.txt','analyze.py']},'projection':{'counts':{s:sum(r['status']==s for r in rows) for s in statuses},'retained_fraction':len(retained)/len(rows),'raw_all_frames':summarize([r['before'] for r in rows]),'same_retained_frames_raw':summarize([r['before'] for r in retained]),'same_retained_frames_selected':summarize([r['selected'] for r in retained]),'refined_subset_raw':summarize([r['before'] for r in refined]),'refined_subset_selected':summarize([r['selected'] for r in refined]),'rejected_subset_raw':summarize([r['before'] for r in rejected]),'rejected_subset_proposals':summarize([r['proposal'] for r in rejected]),'rows':rows},'sam':{'equal_fields':identity,'first_cache':first['inference_cache'],'final_cache':last['inference_cache'],'timings':timings},'limits':['One development capture; 77 correlated frames, not 77 independent trials','Held-out pixels share the reconstruction source; residual is not independent physical accuracy','Retained cohort is selected by gates; report coverage and rejected rows alongside improvement','Not a rerun of the full historical pipeline; saved raw/selected diagnostic branches','Historical cold/warm timings do not isolate all environmental factors']}
(root/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result['projection'].items() if k!='rows'},indent=2))
