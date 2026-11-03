"""Self-contained scan-vs-BIM preview; display normalization never alters data.

The caller supplies meshes in the same metre, Z-up coordinate system. This viewer
does not register, measure or reinterpret them. Rejected registration always
disables deviation colouring, even when stale deviations were supplied.
"""
import base64
import json
from pathlib import Path

import numpy as np


def _encode(values, dtype):
    return base64.b64encode(np.asarray(values, dtype=dtype).tobytes()).decode('ascii')


def _mesh(vertices, faces):
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all() or faces.ndim != 2
            or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer)
            or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError('Expected finite vertices and valid triangle indices')
    return vertices, faces


def export_scan_bim_viewer(output_path, vertices, faces, deviations_m, compared_mask,
                          reference_vertices, reference_faces, report):
    """Export one offline HTML file with scan/IFC toggles and signed cm colours.

    ``compared_mask`` is boolean, one entry per scan face. NaN is permitted only
    on unexamined faces. ``report['status']`` must be ``completed`` or
    ``registration_rejected``. Signed distances follow caller-supplied normals;
    the default explanation deliberately avoids claiming inside/outside meaning.
    """
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(output)
    vertices, faces = _mesh(vertices, faces)
    reference_vertices, reference_faces = _mesh(reference_vertices, reference_faces)
    deviations, compared = np.asarray(deviations_m, dtype=float), np.asarray(compared_mask)
    if (deviations.shape != (len(faces),) or compared.shape != deviations.shape
            or compared.dtype != np.bool_ or not np.isfinite(deviations[compared]).all()
            or np.isinf(deviations).any()):
        raise ValueError('Expected one deviation and boolean comparison flag per face')
    if not isinstance(report, dict) or report.get('status') not in ('completed', 'registration_rejected'):
        raise ValueError('Report must explicitly declare completed or registration_rejected')
    blocked = report['status'] != 'completed'
    enabled = compared & (not blocked)
    lower = np.minimum(vertices.min(axis=0), reference_vertices.min(axis=0))
    upper = np.maximum(vertices.max(axis=0), reference_vertices.max(axis=0))
    center = (lower + upper)*.5
    radius = max(float(np.linalg.norm(upper-lower)*.5), 1e-6)
    payload = {
        'schema_version': '1.0', 'unit': 'meter', 'up_axis': 'z', 'blocked': blocked,
        'positions': _encode((vertices-center)/radius, '<f4'), 'faces': _encode(faces, '<u4'),
        'reference_positions': _encode((reference_vertices-center)/radius, '<f4'),
        'reference_faces': _encode(reference_faces, '<u4'),
        'deviations_m': _encode(np.where(enabled, deviations, 0), '<f4'),
        'compared_mask': _encode(enabled, 'u1'),
        'face_count': len(faces), 'compared_face_count': int(enabled.sum()),
        'reference_face_count': len(reference_faces),
        'normalization': {'center_world': center.tolist(), 'radius_m': radius},
        'bounds_world': [lower.tolist(), upper.tolist()], 'report': report,
    }
    # Report is untrusted display data; textContent is used everywhere in JS.
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).replace('<', '\\u003c')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_HTML.replace('__DATA__', encoded), encoding='utf-8')
    return output


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scan and IFC: deviations</title><style>
*{box-sizing:border-box}body{margin:0;background:#101823;color:#edf3fa;font:14px system-ui,sans-serif;overflow:hidden}
canvas{display:block;width:100vw;height:100vh;touch-action:none}aside{position:fixed;left:16px;top:16px;width:310px;padding:20px;background:#182334f2;border:1px solid #44546b;border-radius:12px;max-height:calc(100vh - 32px);overflow:auto}h1{font-size:21px;margin:0 0 12px}p{line-height:1.5;color:#c1cede}label{display:block;margin:13px 0 7px}input[type=range]{width:100%}input[type=checkbox]{accent-color:#65bff5}button{background:#293d57;color:#eef5ff;border:1px solid #5b7291;border-radius:6px;padding:8px 12px;font:inherit;cursor:pointer;margin:8px 5px 0 0}.help{font-size:12px;color:#a8b9cf}.gradient{height:16px;border-radius:4px;background:linear-gradient(90deg,#2767df,#46d28c,#df492c)}.ticks{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;font-size:12px;margin-top:5px}.unknown{display:inline-block;width:13px;height:13px;background:#999fa8;margin-right:7px}.badge{position:fixed;bottom:16px;right:16px;background:#182334ed;padding:10px 14px;border-radius:7px}#warning{padding:12px;background:#53281e;border:1px solid #f29978;border-radius:7px;color:#ffe1d4;font-weight:600}#warning[hidden]{display:none}#error{color:#ffb7a7;white-space:pre-wrap}details{margin-top:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px ui-monospace,monospace;color:#aabdd4}.swatch{display:inline-block;width:22px;height:2px;background:#66dcf1;margin:0 6px 3px 0}output{float:right;color:#bfe3ff}@media(max-width:700px){aside{width:235px;padding:13px;left:8px;top:8px;font-size:12px}h1{font-size:18px}.badge{font-size:11px;max-width:45%}}
</style></head><body>
<canvas id="view" aria-label="Scan and design IFC geometry"></canvas><aside>
<h1>Scan and IFC</h1><div id="warning" hidden>ALIGNMENT UNVERIFIED. Measurements and deviation coloring are disabled.</div>
<p id="summary"></p><p id="rejection" class="help"></p>
<label><input id="scan" type="checkbox" checked> Original scan after alignment</label>
<label><input id="reference" type="checkbox"> <span class="swatch"></span>IFC · triangle edges</label>
<label><input id="deviation" type="checkbox" checked> Deviation colors</label>
<label><input id="unknown" type="checkbox"> Unverified regions</label>
<button id="results-only">Verified regions only</button><button id="full-scene">Entire scene</button>
<p class="help">Unverified regions are initially hidden. Visible fragments show the actual comparison coverage, not entire walls. Green is close to zero; blue and red represent opposite deviation signs.</p>
<div id="scale-section"><label for="range">Color scale limit <output id="range-value">3 cm</output></label>
<input id="range" type="range" min="1" max="30" step="1" value="3">
<div class="gradient"></div><div class="ticks"><span id="negative">−10 cm</span><span>0</span><span id="positive">+10 cm</span></div>
<p class="help" id="sign">The sign follows the design surface orientation. Colors saturate beyond the scale limit.</p></div>
<p class="help"><span class="unknown"></span>Gray means no verified correspondence, not zero deviation.</p>
<label for="cut">Height clipping <output id="cut-value"></output></label><input id="cut" type="range">
<button id="reset">Overview</button><button id="top">Top view</button>
<p class="help">Drag to rotate. Shift + drag or right-drag to pan. Scroll to zoom. Height clipping reveals the room interior.</p>
<p class="help">Colors represent the observed-surface calculation. They do not verify hidden regions or replace alignment-quality checks.</p>
<details><summary>Calculation report</summary><pre id="report"></pre></details><p id="error"></p></aside>
<div id="status" class="badge">Loading...</div>
<script id="scan-bim-data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('scan-bim-data').textContent),el=id=>document.getElementById(id),canvas=el('view');
el('report').textContent=JSON.stringify(data.report,null,2);
el('summary').textContent=data.blocked?'Only the proposed scan position relative to IFC is displayed.':`${data.compared_face_count.toLocaleString('en-US')} of ${data.face_count.toLocaleString('en-US')} triangles compared (${(100*data.compared_face_count/data.face_count).toFixed(1)}%).`;
if(data.blocked){el('unknown').checked=true;el('reference').checked=true;el('results-only').disabled=true;el('warning').hidden=false;el('deviation').checked=false;el('deviation').disabled=true;el('scale-section').hidden=true;document.body.dataset.registration='rejected';const reason=data.report.reason||data.report.reasons||data.report.registration?.reasons;el('rejection').textContent=reason?(typeof reason==='string'?reason:JSON.stringify(reason)):'';}
else document.body.dataset.registration='completed';
if(typeof data.report.deviation_sign==='string')el('sign').textContent=data.report.deviation_sign+' Colors saturate beyond the scale limit.';
const center=data.normalization.center_world,radius=data.normalization.radius_m;
const zlow=data.bounds_world[0][2],zhigh=data.bounds_world[1][2];el('cut').min=zlow-.01;el('cut').max=zhigh+.01;el('cut').step=Math.max((zhigh-zlow)/500,.001);el('cut').value=zhigh+.01;
function decode(text,Type){const raw=atob(text),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2],norm=a=>{const d=Math.hypot(...a)||1;return a.map(v=>v/d)};
function multiply(a,b){const r=new Float32Array(16);for(let c=0;c<4;c++)for(let row=0;row<4;row++)for(let i=0;i<4;i++)r[c*4+row]+=a[i*4+row]*b[c*4+i];return r}
try{
const gl=canvas.getContext('webgl',{antialias:true,alpha:false});if(!gl)throw Error('WebGL unavailable. Open the HTML in Chrome or Edge with hardware acceleration.');
function shader(type,src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,`attribute vec3 position;attribute vec3 normal;attribute vec4 color;uniform mat4 mvp;uniform float bias;varying vec3 n;varying vec4 c;varying float h;void main(){gl_Position=mvp*vec4(position,1.);gl_Position.z-=bias*gl_Position.w;n=normal;c=color;h=position.z;}`));
gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`precision mediump float;varying vec3 n;varying vec4 c;varying float h;uniform float cut;uniform bool showUnknown;uniform bool lines;void main(){if(h>cut||(!lines&&!showUnknown&&c.a<.5))discard;float light=lines?1.:(.7+.3*abs(dot(normalize(n),normalize(vec3(.35,.65,.8)))));gl_FragColor=vec4(c.rgb*light,1.);}`));
gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const attrs=Object.fromEntries(['position','normal','color'].map(k=>[k,gl.getAttribLocation(program,k)])),uni=Object.fromEntries(['mvp','cut','showUnknown','lines','bias'].map(k=>[k,gl.getUniformLocation(program,k)]));
const positions=decode(data.positions,Float32Array),faces=decode(data.faces,Uint32Array),deviations=decode(data.deviations_m,Float32Array),compared=decode(data.compared_mask,Uint8Array);
const refPositions=decode(data.reference_positions,Float32Array),refFaces=decode(data.reference_faces,Uint32Array);
function buffer(a){const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,a,gl.STATIC_DRAW);return b}
function expandedMesh(p,f,wire){const count=f.length*(wire?2:1),xyz=new Float32Array(count*3),normals=new Float32Array(count*3);for(let i=0;i<f.length/3;i++){const a=[...p.slice(f[i*3]*3,f[i*3]*3+3)],b=[...p.slice(f[i*3+1]*3,f[i*3+1]*3+3)],c=[...p.slice(f[i*3+2]*3,f[i*3+2]*3+3)],n=norm(cross(b.map((v,j)=>v-a[j]),c.map((v,j)=>v-a[j])));const order=wire?[a,b,b,c,c,a]:[a,b,c];order.forEach((v,j)=>{xyz.set(v,(i*order.length+j)*3);normals.set(n,(i*order.length+j)*3)})}return {positions:buffer(xyz),normals:buffer(normals),count}}
const scan=expandedMesh(positions,faces,false),ref=expandedMesh(refPositions,refFaces,true),scanColors=new Uint8Array(faces.length*4),refColors=new Uint8Array(ref.count*4);
for(let i=0;i<ref.count;i++)refColors.set([102,220,241,255],i*4);ref.colors=buffer(refColors);scan.colors=buffer(scanColors);
function updateColors(){const limit=Number(el('range').value)/100,colour=el('deviation').checked&&!data.blocked;for(let i=0;i<faces.length/3;i++){let c=[153,159,168,0];if(compared[i]){c=[180,190,202,255];if(colour){const t=Math.min(1,Math.abs(deviations[i])/limit),end=deviations[i]<0?[39,103,223]:[223,73,44];c=[...end.map((v,k)=>Math.round([70,210,140][k]*(1-t)+v*t)),255]}}for(let j=0;j<3;j++)scanColors.set(c,(i*3+j)*4)}gl.bindBuffer(gl.ARRAY_BUFFER,scan.colors);gl.bufferSubData(gl.ARRAY_BUFFER,0,scanColors);const cm=el('range').value;el('range-value').textContent=cm+' cm';el('negative').textContent='−'+cm+' cm';el('positive').textContent='+'+cm+' cm';}
function bind(name,b,size,type=gl.FLOAT,normalized=false){gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.enableVertexAttribArray(attrs[name]);gl.vertexAttribPointer(attrs[name],size,type,normalized,0,0)}
function renderMesh(m,wire){bind('position',m.positions,3);bind('normal',m.normals,3);bind('color',m.colors,4,gl.UNSIGNED_BYTE,true);gl.uniform1i(uni.lines,wire?1:0);gl.uniform1f(uni.bias,wire?.00015:0);gl.drawArrays(wire?gl.LINES:gl.TRIANGLES,0,m.count)}
gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.clearColor(.055,.08,.12,1);
let yaw=.75,pitch=.55,distance=3.2,target=[0,0,0],drag=null,queued=false;
function basis(){const cp=Math.cos(pitch),direction=[cp*Math.cos(yaw),cp*Math.sin(yaw),Math.sin(pitch)],right=norm(cross([0,0,1],direction));return {direction,right,up:cross(direction,right)}}
function draw(){queued=false;const dpr=Math.min(devicePixelRatio||1,2),width=Math.round(innerWidth*dpr),height=Math.round(innerHeight*dpr);if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}gl.viewport(0,0,width,height);const {direction:z,right:x,up:y}=basis(),eye=target.map((v,i)=>v+z[i]*distance);const view=new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]),f=1/Math.tan(Math.PI/8),near=.01,far=100,projection=new Float32Array([f/(width/height),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);gl.uniformMatrix4fv(uni.mvp,false,multiply(projection,view));gl.uniform1f(uni.cut,(Number(el('cut').value)-center[2])/radius);gl.uniform1i(uni.showUnknown,el('unknown').checked?1:0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(el('scan').checked)renderMesh(scan,false);if(el('reference').checked)renderMesh(ref,true);el('cut-value').textContent=Number(el('cut').value).toLocaleString('en-US',{maximumFractionDigits:2})+' m';el('status').textContent=data.blocked?'ALIGNMENT REJECTED: measurements disabled':'Ready: IFC coordinates in meters';document.body.dataset.ready='true'}
function schedule(){if(!queued){queued=true;requestAnimationFrame(draw)}}
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(drag.pan){const b=basis(),scale=distance/innerHeight;target=target.map((v,i)=>v-dx*scale*b.right[i]+dy*scale*b.up[i])}else{yaw-=dx*.006;pitch=Math.max(-1.56,Math.min(1.56,pitch+dy*.006))}drag.x=e.clientX;drag.y=e.clientY;schedule()});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('wheel',e=>{e.preventDefault();distance=Math.max(.1,Math.min(40,distance*Math.exp(e.deltaY*.001)));schedule()},{passive:false});
el('results-only').onclick=()=>{el('unknown').checked=false;el('reference').checked=false;el('scan').checked=true;schedule()};el('full-scene').onclick=()=>{el('unknown').checked=true;el('reference').checked=true;el('scan').checked=true;schedule()};
for(const id of ['scan','reference','unknown','cut'])el(id).addEventListener('input',schedule);for(const id of ['range','deviation'])el(id).addEventListener('input',()=>{updateColors();schedule()});
el('reset').onclick=()=>{yaw=.75;pitch=.55;distance=3.2;target=[0,0,0];el('cut').value=zhigh+.01;schedule()};el('top').onclick=()=>{pitch=1.56;target=[0,0,0];schedule()};addEventListener('resize',schedule);updateColors();schedule();
}catch(error){el('error').textContent=error.message;el('status').textContent='Could not open the 3D view';document.body.dataset.error='true'}
</script></body></html>'''
