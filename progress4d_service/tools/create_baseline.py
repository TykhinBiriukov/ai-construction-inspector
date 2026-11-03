"""Create an explicit synthetic fit-out IFC schedule without modifying source."""
import argparse
import json
import uuid
from datetime import datetime,timedelta
from pathlib import Path
import ifcopenshell
import ifcopenshell.api


def main():
    p=argparse.ArgumentParser(); p.add_argument('source',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--elements',type=Path,required=True,help='Exact final scene mesh recipe')
    args=p.parse_args()
    if args.source.resolve()==args.output.resolve(): raise ValueError('Source must be preserved')
    f=ifcopenshell.open(args.source)
    # Existing door is a final-design placeholder; replace it with the scheduled
    # synthetic installation to avoid counting a pre-existing duplicate.
    for door in list(f.by_type('IfcDoor')):
        ifcopenshell.api.run('root.remove_product',f,product=door)
    schedule=ifcopenshell.api.run('sequence.add_work_schedule',f,name='Synthetic six-week room fit-out')
    body=next(c for c in f.by_type('IfcGeometricRepresentationSubContext') if c.ContextIdentifier=='Body')
    storey=f.by_type('IfcBuildingStorey')[0]
    start=datetime(2026,9,7,8)
    elements=json.loads(args.elements.read_text())
    phases=[('frame',0,6),('services',7,13),('boards',14,27),('finish',28,34),('floor',35,41),('door',35,41)]
    tasks={}
    specifications={
        'frame':'Galvanised metal stud or track. Must be directly visible; gypsum boards or paint covering the planned location are occlusion, not evidence of the metal member.',
        'services':'Dark electrical conduit or outlet box installed within the stud wall. A stud, board, screw or shadow is not this product.',
        'boards':'Green-grey paper-faced gypsum wallboard panel attached to the stud wall. Distinguish from bare concrete and from stored panels.',
        'finish':'Light ivory painted plaster coating over the wallboard. Raw green-grey gypsum paper, screws and unfinished board seams do NOT satisfy this finish.',
        'floor':'Timber floor plank laid flat at finished floor level as part of a continuous floor covering. Offcuts, workbench tops, tools and stacked sheets are NOT installed flooring.',
        'door':'Installed timber door assembly in the wall opening; distinguish the door leaf and its metal handle from an empty opening.'}
    for phase,begin,end in phases:
        task=ifcopenshell.api.run('sequence.add_task',f,work_schedule=schedule,name=phase,identification=phase)
        tt=ifcopenshell.api.run('sequence.add_task_time',f,task=task)
        ifcopenshell.api.run('sequence.edit_task_time',f,task_time=tt,attributes={
            'ScheduleStart':start+timedelta(days=begin),
            'ScheduleFinish':start+timedelta(days=end,hours=9), 'DurationType':'ELAPSEDTIME'})
        tasks[phase]=task
        for element in [e for e in elements if e['phase']==phase]:
            obj=ifcopenshell.api.run('root.create_entity',f,ifc_class='IfcBuildingElementProxy',name=element['name'])
            obj.Description=specifications[phase]
            obj.GlobalId=ifcopenshell.guid.compress(uuid.uuid5(uuid.NAMESPACE_URL,'progress4d-demo/'+element['name']).hex)
            ifcopenshell.api.run('spatial.assign_container',f,products=[obj],relating_structure=storey)
            representation=ifcopenshell.api.run('geometry.add_mesh_representation',f,context=body,
                vertices=[element['vertices']],faces=[element['faces']])
            ifcopenshell.api.run('geometry.assign_representation',f,product=obj,representation=representation)
            ifcopenshell.api.run('sequence.assign_process',f,relating_process=task,related_object=obj)
    for before,after in [('frame','services'),('services','boards'),('boards','finish'),('boards','floor'),('finish','door')]:
        ifcopenshell.api.run('sequence.assign_sequence',f,relating_process=tasks[before],related_process=tasks[after],sequence_type='FINISH_START')
    # Sequence assignment can cascade dates to the earliest possible start.
    # Preserve the explicit baseline, including intentionally scheduled slack.
    for phase,begin,end in phases:
        tt=tasks[phase].TaskTime
        tt.ScheduleStart=(start+timedelta(days=begin)).isoformat()
        tt.ScheduleFinish=(start+timedelta(days=end,hours=9)).isoformat()
        tt.ScheduleDuration=f'P{end-begin}DT9H'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    f.write(str(args.output))


if __name__=='__main__': main()
