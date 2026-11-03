"""Strict IFC4 baseline extraction; never infers schedules from geometry."""
from datetime import datetime
import ifcopenshell
import ifcopenshell.util.unit


def read_schedule(path):
    model=ifcopenshell.open(str(path))
    schedules=model.by_type('IfcWorkSchedule')
    if len(schedules)!=1:
        raise ValueError('Exactly one explicit IfcWorkSchedule is required')
    controlled=set()
    for rel in schedules[0].Controls:
        controlled.update(o.id() for o in rel.RelatedObjects if o.is_a('IfcTask'))
    def descend(task):
        for rel in task.IsNestedBy:
            for child in rel.RelatedObjects:
                if child.is_a('IfcTask') and child.id() not in controlled:
                    controlled.add(child.id()); descend(child)
    for identity in list(controlled): descend(model.by_id(identity))
    tasks=[]
    for identity in sorted(controlled):
        task=model.by_id(identity)
        time=task.TaskTime
        if not time or not time.ScheduleStart or not time.ScheduleFinish:
            raise ValueError(f'Task {task.GlobalId} has no explicit planned dates')
        start=datetime.fromisoformat(time.ScheduleStart)
        finish=datetime.fromisoformat(time.ScheduleFinish)
        if finish < start: raise ValueError('Task finish precedes start')
        products=[]
        for rel in task.OperatesOn:
            products.extend(o.GlobalId for o in rel.RelatedObjects if o.is_a('IfcProduct'))
        predecessors=[]
        for rel in task.IsSuccessorFrom:
            if rel.RelatingProcess.id() not in controlled:
                raise ValueError('Dependency points outside selected schedule')
            if rel.SequenceType!='FINISH_START' or rel.TimeLag:
                raise ValueError('Initial release supports finish-start dependencies without lag only')
            predecessors.append(rel.RelatingProcess.GlobalId)
        tasks.append(dict(id=task.GlobalId,name=task.Name,start=time.ScheduleStart,
                          finish=time.ScheduleFinish,products=sorted(set(products)),
                          predecessors=predecessors))
    if not tasks: raise ValueError('Schedule has no assigned tasks')
    pending={t['id']:set(t['predecessors']) for t in tasks}
    order=[]
    while pending:
        ready=sorted(k for k,v in pending.items() if not v)
        if not ready: raise ValueError('Schedule dependency cycle')
        order.extend(ready)
        for k in ready: del pending[k]
        for deps in pending.values(): deps.difference_update(ready)
    return dict(id=schedules[0].GlobalId,tasks=tasks,topological_order=order,
                length_unit_scale=ifcopenshell.util.unit.calculate_unit_scale(model))
