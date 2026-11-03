"""Plot every predefined cohort; no exclusion of failed or ambiguous cases."""
import json,statistics,hashlib
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
root=Path('/work');d=json.loads((root/'results.json').read_text());historical=json.loads(Path('/historical/metrics.json').read_text())
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold','figure.facecolor':'white','axes.facecolor':'#fafbfc','axes.grid':True,'grid.alpha':.18,'savefig.bbox':'tight'})
colors={'raw':'#64748b','ungated':'#b35c29','selected':'#087f8c'}
figdir=root/'figures';figdir.mkdir(exist_ok=True)
def save(fig,name):
 for ext in ['png','svg']:fig.savefig(figdir/(name+'.'+ext),dpi=180)
 plt.close(fig)
def mean(values):return statistics.mean(values) if values else None
summary={'pose':{},'wall':{},'input_sha256':hashlib.sha256((root/'results.json').read_bytes()).hexdigest()}
for kind in dict.fromkeys(r['stratum'] for r in d['pose_cases']):
 rows=[r for r in d['pose_cases'] if r['stratum']==kind];summary['pose'][kind]={'total':len(rows)}
 for method in ['raw_error','ungated_error','selected_error']:
  errors=[r[method] for r in rows if r[method] is not None];summary['pose'][kind][method]={'accepted':len(errors),'mean_translation_mm':mean([x['translation_mm'] for x in errors]),'max_translation_mm':max([x['translation_mm'] for x in errors],default=None),'mean_rotation_deg':mean([x['rotation_deg'] for x in errors]),'outside_threshold':sum(x['translation_mm']>10 or x['rotation_deg']>.5 for x in errors)}
for method in ['nearest_surface','camera_side']:
 v=[x[method]['absolute_error_mm'] for x in d['wall_cases'] if x[method]['absolute_error_mm'] is not None];summary['wall'][method]={'n':len(v),'mean_absolute_error_mm':mean(v),'max_absolute_error_mm':max(v),'above_1mm':sum(x>1 for x in v)}
(root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
# Real-room paired diagnostics: same accepted frames on both axes.
rows=historical['projection']['rows'];kept=[x for x in rows if x['selected'] is not None]
fig,ax=plt.subplots(figsize=(8,5));limit=max(r['before']['median_m']*1000 for r in kept)*1.08
ax.plot([0,limit],[0,limit],'--',color='#94a3b8',lw=1,label='No change')
for status,c,label in [('accepted_raw',colors['raw'],'Original pose retained (50)'),('accepted_refined',colors['selected'],'Refined pose selected (18)')]:
 q=[x for x in kept if x['status']==status];ax.scatter([x['before']['median_m']*1000 for x in q],[x['selected']['median_m']*1000 for x in q],c=c,s=40,alpha=.85,label=label)
ax.set(xlabel='Original per-frame median residual (mm)',ylabel='Selected per-frame median residual (mm)',title='Real-room projection consistency: 68 paired frames');ax.legend(frameon=False);fig.text(.12,-.035,'Mean: 10.99 → 7.93 mm (−27.8%). 9/77 views rejected. Internal mesh consistency, not physical ground truth.',fontsize=9)
save(fig,'real-room-projection')
# Known camera ground truth, show competing proposal and actual selected policy.
rows=[r for r in d['pose_cases'] if r['stratum']=='moderate'];fig,(a,b)=plt.subplots(1,2,figsize=(11,4.8),gridspec_kw={'width_ratios':[1,1.8]});methods=['raw_error','ungated_error','selected_error'];names=['Original\nposes','Ungated\nproposal','Current\npolicy'];vals=[mean([r[k]['translation_mm'] for r in rows]) for k in methods];bars=a.bar(names,vals,color=list(colors.values()),width=.65)
for bar,v in zip(bars,vals):a.annotate(f'{v:.2f}',(bar.get_x()+bar.get_width()/2,max(v,.3)),xytext=(0,5),textcoords='offset points',ha='center')
a.set(ylabel='Mean translation error (mm)',ylim=(0,82),title='All 12 room cases')
for method,c,label in zip(methods,colors.values(),['Original','Ungated proposal','Current policy']):b.plot(range(12),[r[method]['translation_mm'] for r in rows],marker='o',ms=4,color=c,label=label)
b.set(xlabel='Predeclared case index',ylabel='Translation error (mm)',title='Every room case, including the missed correction');b.xaxis.set_major_locator(MaxNLocator(integer=True));b.legend(frameon=False,fontsize=9)
fig.suptitle('Known camera ground truth: pose correction',y=1.04,fontsize=14,fontweight='bold');fig.text(.12,-.04,'Generated room geometry, 2 mm depth noise. Current mean: 70.16 → 5.07 mm; case 04 retains the original pose.',fontsize=9);fig.tight_layout();save(fig,'camera-ground-truth')
# Surface matching ambiguity.
fig,axs=plt.subplots(1,3,figsize=(12,4),sharex=True,sharey=True)
for ax,t in zip(axs,[.12,.20,.30]):
 q=sorted([r for r in d['wall_cases'] if r['thickness_m']==t],key=lambda r:r['true_offset_m']);x=[r['true_offset_m']*1000 for r in q]
 ax.plot(x,x,'--',color='#94a3b8',label='Known interior offset')
 for key,c,label,marker in [('nearest_surface',colors['ungated'],'Nearest surface','x'),('camera_side',colors['selected'],'Camera-side selection','o')]:ax.plot(x,[r[key]['signed_offset_m']*1000 for r in q],marker=marker,color=c,label=label,lw=1.6)
 ax.set(title=f'Wall thickness {t*1000:.0f} mm',xlabel='True interior displacement (mm)')
axs[0].set_ylabel('Reported signed displacement (mm)');axs[-1].legend(frameon=False,fontsize=8);fig.suptitle('Known surface displacement: selecting the correct side of the wall',y=1.04,fontsize=14,fontweight='bold');fig.text(.1,-.04,'18 analytic stress cases. Errors >1 mm: nearest surface 9/18; camera-side selection 0/18. Not scanner accuracy.',fontsize=9);fig.tight_layout();save(fig,'surface-ground-truth')
# Accepted accuracy vs explicit refusal; all 26 cases included.
fig,ax=plt.subplots(figsize=(9,4.8));fig.subplots_adjust(bottom=.30);labels=['Original poses','Ungated proposal','Current policy'];counts=[]
for method in methods:
 e=[r[method] for r in d['pose_cases']];rejected=sum(x is None for x in e);bad=sum(x is not None and (x['translation_mm']>10 or x['rotation_deg']>.5) for x in e);counts.append((26-rejected-bad,bad,rejected))
left=np.zeros(3)
for i,(c,label) in enumerate([('#087f8c','Accepted within threshold'),('#c46a35','Accepted outside threshold'),('#a8b2c0','Rejected / no output')]):
 values=[r[i] for r in counts];ax.barh(labels,values,left=left,color=c,label=label,height=.55)
 for j,v in enumerate(values):
  if v:ax.text(left[j]+v/2,j,str(v),ha='center',va='center',color='white',fontweight='bold')
 left+=values
ax.set(xlabel='Number of predefined cases (26 total)',xlim=(0,26),title='Accuracy–coverage tradeoff, including stress and ambiguous cases');ax.invert_yaxis();ax.legend(loc='upper center',bbox_to_anchor=(.5,-.20),ncol=3,frameon=False,fontsize=9);fig.text(.12,.035,'Threshold fixed in protocol: translation ≤10 mm and rotation ≤0.5°.\nIncludes two unobservable tangential-drift cases.',fontsize=9);save(fig,'accuracy-coverage')
print(json.dumps(summary,indent=2))
