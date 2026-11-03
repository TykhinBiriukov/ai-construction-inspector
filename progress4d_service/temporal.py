"""Chronological product evidence, observed activity and conditional delay propagation."""
from datetime import datetime,timedelta
import copy
import numpy as np
import cv2
from .analysis import QuantityObservation,report
from .perception import geometric_visibility_gate


def resolve_appearance(products,previous,known,tasks):
    # Reuse previously observed material appearance only with independent
    # current surface support. This cannot turn an empty design location into
    # installed work, and does not infer a substrate behind another layer.
    for p in products:
        if p.get('evidence_version')=='multiview_v2' or p['state']!='uncertain' or not p.get('design_bounds_m') or p.get('requires_temporal_appearance'):continue
        size=np.diff(np.array(p['design_bounds_m']),axis=0)[0]
        if not (.01<=min(size)<.05 and np.prod(np.sort(size)[1:])>1):continue
        references=[q for q in previous.values() if q['task_id']==p['task_id'] and q['state']=='present' and not q.get('material_reference_evidence')]
        votes=0;used=[]
        for view in p['views']:
            if view.get('near_surface_tracks',0)<3 or not view.get('appearance_rgb'):continue
            lab=cv2.cvtColor(np.array([[view['appearance_rgb']]],np.float32)/255,cv2.COLOR_RGB2LAB)[0,0]
            match=None
            for q in references:
                for ref in q['views']:
                    if not ref.get('appearance_rgb'):continue
                    other=cv2.cvtColor(np.array([[ref['appearance_rgb']]],np.float32)/255,cv2.COLOR_RGB2LAB)[0,0]
                    if abs(lab[0]-other[0])<15 and np.linalg.norm(lab[1:]-other[1:])<3:match=q['id'];break
                if match:break
            if match:votes+=1;used.append(match)
        if votes>=2:
            p['state']='present';p['material_reference_evidence']=sorted(set(used))
            p['reason']='Current reconstructed panel surface and two-view appearance match previously directly observed material of the same task.'
    for p in products:
        if not p.get('requires_temporal_appearance') or p.get('candidate_visual_state')!='present':continue
        old=previous.get(p['id'])
        if not old:continue
        center=np.mean(p['design_bounds_m'],axis=0)
        prior_substrate=any(known.get(identity,{}).get('state')=='present' and identity in previous and
            np.linalg.norm(np.mean(previous[identity]['design_bounds_m'],axis=0)-center)<.12
            for dep in tasks[p['task_id']]['predecessors'] for identity in tasks[dep]['products'])
        previously_confirmed=known.get(p['id'],{}).get('state')=='present'
        if not prior_substrate and not previously_confirmed:continue
        votes=0;used=set()
        for view in p['views']:
            if not view.get('appearance_rgb') or not view.get('camera_ifc_m'):continue
            matches=[v for v in old['views'] if v.get('appearance_rgb') and v.get('camera_ifc_m') and v['photo'] not in used]
            if not matches:continue
            before=min(matches,key=lambda v:np.linalg.norm(np.array(v['camera_ifc_m'])-view['camera_ifc_m']))
            if np.linalg.norm(np.array(before['camera_ifc_m'])-view['camera_ifc_m'])>.6:continue
            colors=np.array([[view['appearance_rgb'],before['appearance_rgb']]],np.float32)/255
            lab=cv2.cvtColor(colors,cv2.COLOR_RGB2LAB)[0];delta=float(np.linalg.norm(lab[0]-lab[1]));chroma=float(np.linalg.norm(lab[0,1:]-lab[1,1:]))
            if (previously_confirmed and delta<6) or (prior_substrate and delta>6 and chroma>2):votes+=1;used.add(before['photo'])
        if votes>=2:
            p['state']='present';p['reason']='Two-view visual candidate corroborated by appearance history after an observed substrate, or unchanged previously confirmed coating.'
            p['temporal_evidence']=True
    # A change of appearance at an already observed substrate, combined with a
    # plausible coating, contradicts direct visibility of the original material.
    candidates=[p for p in products if p.get('requires_temporal_appearance') and p.get('candidate_visual_state')=='present']
    for p in products:
        if p['state']!='present' or not p.get('design_bounds_m'):continue
        old=previous.get(p['id'])
        if not old or not old.get('design_bounds_m'):continue
        size=np.diff(np.array(p['design_bounds_m']),axis=0)[0]
        if not (.01<=min(size)<.05 and np.prod(np.sort(size)[1:])>1):continue
        nearby=any(np.linalg.norm(np.mean(q['design_bounds_m'],axis=0)-np.mean(p['design_bounds_m'],axis=0))<.12 for q in candidates)
        if not nearby:continue
        changed=0
        for v in p['views']:
            refs=[x for x in old['views'] if x.get('camera_ifc_m') and x.get('appearance_rgb')]
            if not refs or not v.get('camera_ifc_m') or not v.get('appearance_rgb'):continue
            ref=min(refs,key=lambda x:np.linalg.norm(np.array(x['camera_ifc_m'])-v['camera_ifc_m']))
            if np.linalg.norm(np.array(ref['camera_ifc_m'])-v['camera_ifc_m'])>.6:continue
            lab=cv2.cvtColor(np.array([[v['appearance_rgb'],ref['appearance_rgb']]],np.float32)/255,cv2.COLOR_RGB2LAB)[0]
            if np.linalg.norm(lab[0]-lab[1])>6 and np.linalg.norm(lab[0,1:]-lab[1,1:])>2:changed+=1
        if old.get('possible_cover') or (old['state']=='present' and changed>=2) or any(v.get('near_surface_tracks',0)<3 for v in p['views']):
            p['state']='uncertain';p['possible_cover']=True
            p['reason']='A plausible finish conflicts with direct substrate visibility, without sufficient independent surface or appearance evidence; retained history is separate.'
    return geometric_visibility_gate(products)


def build_report(schedule,surveys,include_history=True,_resolved=False):
    tasks={t['id']:t for t in schedule['tasks']}
    known={};observations=[];snapshots=[];previous={};state_changes=[]
    ordered=sorted(surveys,key=lambda s:(datetime.fromisoformat(s['captured_at']),s['survey_id']))
    seen_dates=set();resolved_inputs=[]
    for survey in ordered:
        captured=datetime.fromisoformat(survey['captured_at'])
        # Same instant/zone ambiguity is rejected at submission; never double count.
        if captured in seen_dates:continue
        seen_dates.add(captured)
        raw=survey.get('result') or {};states=copy.deepcopy(raw.get('perception',{}).get('products',[]))
        if not _resolved:resolve_appearance(states,previous,known,tasks)
        resolved_inputs.append(dict(survey,result={'perception':{'products':states}}))
        current={p['id']:p for p in states}
        rows=[]
        for task_id,task in tasks.items():
            observed=[];retained=[];absent=[];unknown=[];removed=[];added=[];evidence=[]
            for identity in task['products']:
                product=current.get(identity);state=product.get('state') if product else None
                if state in ('present','absent'):
                    last=known.get(identity)
                    if last and last['state']=='present' and state=='absent':removed.append(identity)
                    if last and last['state']=='absent' and state=='present':added.append(identity)
                    known[identity]=dict(state=state,at=survey['captured_at'],job_id=survey['job_id'],evidence=product.get('views',[]))
                    (observed if state=='present' else absent).append(identity)
                    evidence.extend(f"{survey['job_id']}:{v['photo']}" for v in product.get('views',[]))
                elif known.get(identity,{}).get('state')=='present':retained.append(identity)
                else:unknown.append(identity)
            total=len(task['products'])
            direct=len(observed)+len(absent)
            if total and direct==total and evidence:
                observations.append(QuantityObservation(task_id=task_id,captured_at=captured,
                    installed=len(observed),total=total,unit='count',coverage=direct/total,
                    evidence=sorted(set(evidence)),source='visual_count'))
            rows.append(dict(task_id=task_id,name=task['name'],total_products=total,
                currently_observed_present=len(observed),currently_observed_absent=len(absent),
                retained_past_present=len(retained),unknown=len(unknown),direct_coverage=direct/total if total else 0,
                observed_removals=removed,known_present_product_ids=observed+retained,
                incomplete_proven=bool(absent),
                evidence=sorted(set(evidence))))
            if removed or added:state_changes.append(dict(task_id=task_id,to_date=survey['captured_at'],observed_product_delta=len(added)-len(removed),
                added_product_ids=added,removed_product_ids=removed,products_per_day=None,interpretation='Confirmed product-state transitions only; unobserved changes may also exist.'))
        snapshots.append(dict(survey_id=survey['survey_id'],job_id=survey['job_id'],captured_at=survey['captured_at'],tasks=rows,
            products=[{k:p[k] for k in ('id','state','reason','temporal_evidence') if k in p} for p in states]))
        previous.update(current)
    if not ordered:return dict(snapshots=[],tasks=[],activity=[])
    as_of=datetime.fromisoformat(ordered[-1]['captured_at']);summary=report(schedule,observations,as_of)
    # Separate schedule scenario: baseline elapsed durations, no invented rates.
    rows={r['task_id']:r for r in summary['tasks']};scenarios={}
    latest_observations={r['task_id']:r for r in snapshots[-1]['tasks']}
    for identity,row in rows.items():
        latest=latest_observations[identity]
        # A partial new survey can disprove old completion without providing a
        # complete quantity suitable for a rate. Never retain that completion.
        completion=row.get('completion_first_observed_at')
        unresolved_removal=bool(completion) and any(known.get(p,{}).get('state')=='absent' and datetime.fromisoformat(known[p]['at'])>datetime.fromisoformat(completion) for p in tasks[identity]['products'])
        if (latest['incomplete_proven'] and latest['direct_coverage']<1) or unresolved_removal:
            row.update(status='incomplete_partial_observation',forecast_finish=None,
                       conditional_rate_finish=None,forecast_basis=None)
            row.pop('completion_first_observed_at',None)
    for identity in schedule['topological_order']:
        task=tasks[identity];row=rows[identity]
        start=datetime.fromisoformat(task['start']);finish=datetime.fromisoformat(task['finish'])
        if start.tzinfo is None:start=start.replace(tzinfo=as_of.tzinfo)
        if finish.tzinfo is None:finish=finish.replace(tzinfo=as_of.tzinfo)
        candidate=None;basis=None;blockers=[]
        if row.get('forecast_finish'):
            candidate=datetime.fromisoformat(row['forecast_finish']);basis=row['forecast_basis']
        elif row.get('conditional_rate_finish'):
            candidate=datetime.fromisoformat(row['conditional_rate_finish']);blockers=list(row['dependency_blockers'])
            basis='Conditional rate scenario only: assumes unresolved predecessors are ready. Their completion is not established.'
        elif row.get('coverage',0)>=.9 and row.get('installed')==0 and row.get('stale_days',1)==0:
            earliest=max(start,as_of)
            for pred in task['predecessors']:
                known_finish=scenarios.get(pred,{}).get('finish')
                if known_finish:earliest=max(earliest,datetime.fromisoformat(known_finish))
                else:blockers.append(pred)
            if not blockers:
                candidate=earliest+(finish-start);basis='Scenario only: unstarted observed task retains baseline elapsed duration, begins after predecessors. No measured production rate.'
        # A separate what-if schedule remains possible with partial quantities.
        # It deliberately makes no claim about observed production rate.
        delayed_predecessor=any(scenarios.get(pred,{}).get('delay_days',0) and scenarios[pred]['delay_days']>0 for pred in task['predecessors'])
        if candidate is None and (latest_observations[identity]['incomplete_proven'] or delayed_predecessor):
            earliest=max(start,as_of);blockers=[]
            for pred in task['predecessors']:
                predicted=scenarios.get(pred,{}).get('finish')
                if predicted:earliest=max(earliest,datetime.fromisoformat(predicted))
                else:blockers.append(pred)
            candidate=earliest+(finish-start)
            basis='Scenario only: allow the entire baseline task duration from the report date or predecessor finish; unresolved predecessors are assumed ready. This is not a measured remaining duration or production rate.'
        scenarios[identity]=dict(task_id=identity,finish=candidate.isoformat() if candidate else None,basis=basis,
            delay_days=max(0,(candidate-finish).total_seconds()/86400) if candidate else None,
            dependency_blockers=blockers)
    activity=[]
    by_task={}
    for observation in observations:
        previous=by_task.get(observation.task_id)
        if previous:
            days=(observation.captured_at-previous.captured_at).total_seconds()/86400
            activity.append(dict(task_id=observation.task_id,from_date=previous.captured_at.isoformat(),to_date=observation.captured_at.isoformat(),
                observed_product_delta=observation.installed-previous.installed,products_per_day=(observation.installed-previous.installed)/days,
                interpretation='Change in directly observed installed IFC product count; not worker presence.'))
        by_task[observation.task_id]=observation
    complete_keys={(x['task_id'],x['to_date']) for x in activity}
    activity.extend(x for x in state_changes if (x['task_id'],x['to_date']) not in complete_keys)
    activity.sort(key=lambda x:(x['to_date'],x['task_id']))
    impacts=[]
    latest_rows={t['task_id']:t for t in snapshots[-1]['tasks']} if snapshots else {}
    for identity,task in tasks.items():
        finish=datetime.fromisoformat(task['finish'])
        if finish.tzinfo is None:finish=finish.replace(tzinfo=as_of.tzinfo)
        if as_of<=finish or not latest_rows.get(identity,{}).get('incomplete_proven'):continue
        affected=set();front=[identity]
        while front:
            parent=front.pop()
            for child in tasks.values():
                if parent in child['predecessors'] and child['id'] not in affected:affected.add(child['id']);front.append(child['id'])
        impacts.append(dict(task_id=identity,overdue_days=(as_of-finish).total_seconds()/86400,affected_successors=sorted(affected),
            basis='At least one assigned product is directly observed absent after the baseline finish; successor impact follows declared finish-start dependencies.'))
    if include_history:
        for i,snapshot in enumerate(snapshots):
            past=build_report(schedule,resolved_inputs[:i+1],include_history=False,_resolved=True) if i<len(snapshots)-1 else dict(delay_scenarios=list(scenarios.values()),delay_impacts=impacts)
            snapshot['delay_scenarios']=past['delay_scenarios'];snapshot['delay_impacts']=past['delay_impacts']
    return dict(summary,snapshots=snapshots,activity=activity,delay_scenarios=list(scenarios.values()),delay_impacts=impacts,
                quantity_basis='Count of IFC products with visual evidence; no claimed measured area or volume.',
                historical_evidence=known,limitations=summary['limitations']+['Occluded work retains dated past evidence; it is not newly measured.'])
