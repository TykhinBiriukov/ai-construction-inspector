"""Deterministic schedule reporting from evidence-backed quantity observations.

No perception happens here. Callers must provide quantities from an independently
validated measurement adapter; evaluator truth is not an allowed runtime source.
"""
from datetime import datetime,timedelta
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator


class QuantityObservation(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    task_id:str
    captured_at:datetime
    installed:float=Field(ge=0)
    total:float=Field(gt=0)
    unit:Literal['m','m2','m3','count']
    coverage:float=Field(ge=0,le=1)
    evidence:list[str]=Field(min_length=1)
    source:Literal['measured_geometry','visual_count']
    @model_validator(mode='after')
    def validate_quantity(self):
        if self.installed>self.total: raise ValueError('Installed quantity exceeds defined scope')
        if self.captured_at.tzinfo is None: raise ValueError('Timezone required')
        return self


def report(schedule,observations,as_of:datetime):
    if as_of.tzinfo is None: raise ValueError('Timezone required')
    tasks={t['id']:t for t in schedule['tasks']}
    groups={identity:[] for identity in tasks}
    for obs in observations:
        if obs.task_id not in groups: raise ValueError('Unknown task')
        if obs.captured_at>as_of: raise ValueError('Observation is later than report date')
        groups[obs.task_id].append(obs)
    results={}
    for identity in schedule['topological_order']:
        task=tasks[identity]
        series=sorted(groups[identity],key=lambda o:o.captured_at)
        if len({o.captured_at for o in series})!=len(series):
            raise ValueError('Conflicting observations at same timestamp')
        if len({(o.total,o.unit) for o in series})>1:
            raise ValueError('Scope/units changed; explicit baseline revision required')
        latest=series[-1] if series else None
        result=dict(task_id=identity,name=task['name'],status='no_data',
                    forecast_finish=None,rate_per_day=None,quantity_delta=None,
                    forecast_basis=None,dependency_blockers=[],evidence=[])
        finish=datetime.fromisoformat(task['finish'])
        # IFC dates may be local. They need an explicit reporting timezone;
        # this initial adapter uses the caller's timezone consistently.
        if finish.tzinfo is None: finish=finish.replace(tzinfo=as_of.tzinfo)
        if latest:
            result.update(observed_at=latest.captured_at.isoformat(),evidence=latest.evidence,
                          installed=latest.installed,total=latest.total,unit=latest.unit,
                          coverage=latest.coverage)
            adequate=latest.coverage>=.9
            result['status']='observed_complete' if adequate and latest.installed==latest.total else ('in_progress' if adequate else 'inconclusive')
            result['overdue_at_observation']=adequate and latest.captured_at>finish and latest.installed<latest.total
            result['stale_days']=(as_of-latest.captured_at).total_seconds()/86400
            if result['status']=='observed_complete':
                completion=latest
                for earlier in reversed(series[:-1]):
                    if earlier.coverage<.9 or earlier.installed!=earlier.total:break
                    completion=earlier
                result['forecast_finish']=completion.captured_at.isoformat()
                result['completion_first_observed_at']=completion.captured_at.isoformat()
                result['forecast_basis']='First complete observation since the last observed incomplete state; exact installation time is unknown. Reconfirmation does not move completion later.'
            elif adequate and len(series)>=2 and series[-2].coverage>=.9:
                previous=series[-2]
                days=(latest.captured_at-previous.captured_at).total_seconds()/86400
                delta=latest.installed-previous.installed
                rate=delta/days
                result.update(quantity_delta=delta,rate_per_day=rate,
                              rework_or_removal_observed=delta<0)
                if rate>0:
                    # Do not assume unobserved progress since the last survey.
                    estimate=as_of+timedelta(days=(latest.total-latest.installed)/rate)
                    result['conditional_rate_finish']=estimate.isoformat()
                    for predecessor in task['predecessors']:
                        pred=results[predecessor]
                        if pred['forecast_finish'] is None:
                            result['dependency_blockers'].append(predecessor)
                        elif pred['status']!='observed_complete':
                            pred_finish=datetime.fromisoformat(pred['forecast_finish'])
                            estimate=max(estimate,pred_finish+timedelta(days=(latest.total-latest.installed)/rate))
                    if not result['dependency_blockers']:
                        result['forecast_finish']=estimate.isoformat()
                        result['forecast_basis']='Last two adequately observed quantities; constant rate, no assumed progress after last survey; finish-start dependencies.'
        results[identity]=result
    return dict(as_of=as_of.isoformat(),tasks=list(results.values()),
                limitations=['No resource levelling, work calendars, contractual certification or probabilistic confidence interval.',
                             'Forecasts are conditional estimates, not schedule commitments.',
                             'Coverage is an evidence-quality gate, not physical completion.'])
