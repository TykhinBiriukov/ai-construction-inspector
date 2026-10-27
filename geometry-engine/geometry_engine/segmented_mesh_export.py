"""Export face labels with unchanged geometry and an offline WebGL viewer.

PLY face properties are authoritative. Vertex display colors use area-weighted
majority labels for viewers that ignore face colors; the HTML viewer colors each
triangle directly and therefore preserves label boundaries exactly.
"""

import base64
import colorsys
import json
from pathlib import Path

import numpy as np


UNKNOWN_COLOR = (156, 163, 175)
CLASS_COLORS = {"wall": (246, 155, 55), "window": (42, 194, 222),
                "door": (87, 132, 245), "floor": (155, 191, 100),
                "ceiling": (213, 190, 135)}


def class_colors(labels):
    """Return deterministic uint8 RGB colors, in the supplied class order."""
    return np.asarray([CLASS_COLORS.get(label, tuple(round(v * 255) for v in
                      colorsys.hsv_to_rgb((index * .61803398875) % 1, .58, .94)))
                      for index, label in enumerate(labels)], dtype=np.uint8).reshape(-1, 3)


def _vertex_colors(vertices, faces, classes, palette):
    triangle = vertices[faces]
    areas = np.linalg.norm(np.cross(triangle[:, 1] - triangle[:, 0],
                                    triangle[:, 2] - triangle[:, 0]), axis=1) * .5
    winning_weight = np.zeros(len(vertices), dtype=float)
    colors = np.tile(np.asarray(UNKNOWN_COLOR, dtype=np.uint8), (len(vertices), 1))
    # Unknown wins equal weights, so a boundary vertex is not overclaimed.
    for label in [-1, *range(len(palette))]:
        selected = classes == label
        if not selected.any():
            continue
        weights = np.bincount(faces[selected].ravel(), weights=np.repeat(areas[selected], 3),
                              minlength=len(vertices))
        replace = weights > winning_weight
        colors[replace] = UNKNOWN_COLOR if label < 0 else palette[label]
        winning_weight[replace] = weights[replace]
    return colors


def _encoded(array, dtype):
    return base64.b64encode(np.asarray(array, dtype=dtype).tobytes()).decode("ascii")


def export_segmented_mesh(output_dir, vertices, faces, face_class_ids,
                          face_instance_ids, face_confidence, labels, objects):
    """Write mesh.ply and viewer.html; paths returned are relative to output_dir."""
    output = Path(output_dir)
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces)
    classes = np.asarray(face_class_ids)
    instances = np.asarray(face_instance_ids)
    confidence = np.asarray(face_confidence, dtype=float)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all() or faces.ndim != 2 or faces.shape[1] != 3
            or not np.issubdtype(faces.dtype, np.integer) or not len(faces)
            or np.any(faces < 0) or np.any(faces >= len(vertices))
            or len(vertices) > np.iinfo(np.int32).max):
        raise ValueError("Expected finite vertices and valid triangle indices")
    if (not isinstance(labels, (tuple, list)) or any(not isinstance(label, str) for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("labels must be distinct class names")
    if (classes.shape != (len(faces),) or instances.shape != classes.shape or confidence.shape != classes.shape
            or not np.issubdtype(classes.dtype, np.integer) or not np.issubdtype(instances.dtype, np.integer)
            or np.any(classes < -1) or np.any(classes >= len(labels)) or np.any(instances < -1)
            or np.any(instances > np.iinfo(np.int32).max)
            or not np.isfinite(confidence).all() or np.any((confidence < 0) | (confidence > 1))):
        raise ValueError("Invalid per-face labels, object IDs or confidence")
    for name in ("mesh.ply", "viewer.html"):
        if (output / name).exists():
            raise FileExistsError(f"Export already exists: {output / name}")
    palette = class_colors(labels)
    face_colors = np.tile(np.asarray(UNKNOWN_COLOR, dtype=np.uint8), (len(faces), 1))
    known = classes >= 0
    face_colors[known] = palette[classes[known]]
    vertex_colors = _vertex_colors(vertices, faces, classes, palette)
    storage = "<f4" if np.array_equal(vertices.astype(np.float32).astype(np.float64), vertices) else "<f8"
    scalar = "float" if storage == "<f4" else "double"
    vertex_records = np.empty(len(vertices), dtype=[("xyz", storage, 3), ("rgb", "u1", 3)])
    vertex_records["xyz"], vertex_records["rgb"] = vertices, vertex_colors
    face_records = np.empty(len(faces), dtype=[("count", "u1"), ("indices", "<i4", 3),
                          ("class_id", "<i4"), ("instance_id", "<i4"),
                          ("confidence", "<f4"), ("rgb", "u1", 3)])
    face_records["count"], face_records["indices"] = 3, faces
    face_records["class_id"], face_records["instance_id"] = classes, instances
    face_records["confidence"], face_records["rgb"] = confidence, face_colors
    header = ("ply\nformat binary_little_endian 1.0\n"
              "comment class_id and instance_id use -1 for unknown\n"
              "comment face order matches the prepared mesh; vertex colors are display approximations\n"
              f"element vertex {len(vertices)}\nproperty {scalar} x\nproperty {scalar} y\nproperty {scalar} z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\n"
              f"element face {len(faces)}\nproperty list uchar int vertex_indices\n"
              "property int class_id\nproperty int instance_id\nproperty float confidence\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "mesh.ply").open("wb") as handle:
        handle.write(header.encode("ascii"))
        vertex_records.tofile(handle)
        face_records.tofile(handle)

    lower, upper = vertices.min(axis=0), vertices.max(axis=0)
    center = (lower + upper) * .5
    radius = max(float(np.linalg.norm(upper - lower) * .5), 1e-6)
    normalized = (vertices - center) / radius
    class_rgba = np.column_stack((face_colors, known.astype(np.uint8) * 255))
    object_rgba = np.tile(np.asarray([*UNKNOWN_COLOR, 0], dtype=np.uint8), (len(faces), 1))
    for identity in np.unique(instances[instances >= 0]):
        color = tuple(round(v * 255) for v in colorsys.hsv_to_rgb((int(identity) * .61803398875 + .09) % 1, .67, .96))
        selected = (instances == identity) & known
        object_rgba[selected] = [*color, 255]
    payload = {"positions": _encoded(normalized, "<f4"), "faces": _encoded(faces, "<u4"),
               "class_colors": _encoded(class_rgba, "u1"), "object_colors": _encoded(object_rgba, "u1"),
               "vertex_count": len(vertices), "face_count": len(faces), "object_count": len(objects),
               "known_face_count": int(known.sum()), "dimensions_m": (upper-lower).tolist(),
               "classes": [{"name": label, "color": palette[i].tolist(), "faces": int((classes == i).sum())}
                           for i, label in enumerate(labels)]}
    # A class name can originate from an external prompt; never place raw HTML in a script.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    (output / "viewer.html").write_text(_VIEWER.replace("__DATA__", data), encoding="utf-8")
    return {"mesh": "mesh.ply", "viewer": "viewer.html"}


_VIEWER = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сегментированная сцена</title><style>
*{box-sizing:border-box}body{margin:0;background:#121a26;color:#e9eef5;font:14px system-ui,sans-serif;overflow:hidden}
canvas{display:block;width:100vw;height:100vh;touch-action:none}aside{position:fixed;top:18px;left:18px;width:270px;padding:20px;border:1px solid #43516a;border-radius:14px;background:#182334f0;box-shadow:0 5px 30px #0005;max-height:calc(100vh - 36px);overflow:auto}
h1{font-size:19px;margin:0 0 14px}p{line-height:1.5;color:#bbc8da;margin:8px 0 14px}label{display:block;margin:15px 0 6px}select,button,a.button{font:inherit;background:#27374d;color:#f4f7ff;border:1px solid #52637d;border-radius:6px;padding:8px;cursor:pointer}select{width:100%}input[type=range]{width:100%}button{margin-top:12px}a{color:#9cceff}.legend{display:flex;gap:9px;align-items:center;margin:9px 0}.swatch{width:13px;height:13px;border-radius:3px;flex:none}.count{color:#9cabc1;margin-left:auto}.help{font-size:12px;margin-top:16px}.status{position:fixed;bottom:20px;right:20px;padding:11px 16px;background:#182334e8;border-radius:8px;max-width:50vw}input[type=checkbox]{accent-color:#71b8ff}#error{color:#ffb5a8}@media(max-width:700px){aside{width:215px;left:8px;top:8px;padding:13px;font-size:12px}h1{font-size:16px}.status{font-size:12px;bottom:8px;right:8px}}</style></head>
<body><canvas id="view" aria-label="Трёхмерная сцена с разметкой"></canvas><aside>
<h1>Сегментированная сцена</h1><p id="summary"></p>
<label for="mode">Показывать цвета</label><select id="mode"><option value="class">По классу объекта</option><option value="object">По отдельным объектам</option></select>
<div id="legend"></div><label><input type="checkbox" id="unknown" checked> Неподтверждённые поверхности</label>
<label for="up">Вертикальная ось</label><select id="up"><option value="z">Z</option><option value="y">Y</option></select>
<label for="cut">Срез сверху</label><input id="cut" type="range" min="-1.05" max="1.05" step="0.01" value="1.05">
<button id="reset">Сбросить вид</button><p class="help">Потяни мышью — поворот.<br>Колесо — приближение.<br>Shift + потяни — перемещение.<br>Срез помогает заглянуть внутрь комнаты.</p>
<p class="help">Цветные участки подтверждены данными. Серый — недостаточно данных или конфликт. Объекты являются кандидатами и требуют проверки.</p>
<a href="mesh.ply" download>Скачать mesh.ply</a><p id="error"></p></aside><div class="status" id="status">Загрузка сцены…</div>
<script id="mesh-data" type="application/json">__DATA__</script><script>
"use strict";
const data=JSON.parse(document.getElementById('mesh-data').textContent),canvas=document.getElementById('view'),status=document.getElementById('status');
document.getElementById('summary').textContent=`${data.face_count.toLocaleString('ru-RU')} треугольников · ${data.object_count} объектов. Размечено ${(100*data.known_face_count/data.face_count).toFixed(1)}% треугольников.`;
const translations={wall:'Стена',window:'Окно',door:'Дверь',floor:'Пол',ceiling:'Потолок'};
for(const item of [...data.classes,{name:'Неподтверждено',color:[156,163,175],faces:data.face_count-data.known_face_count}]){let row=document.createElement('div');row.className='legend';let swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=`rgb(${item.color.join(',')})`;let label=document.createElement('span');label.textContent=translations[item.name]||item.name;let count=document.createElement('span');count.className='count';count.textContent=item.faces.toLocaleString('ru-RU');row.append(swatch,label,count);document.getElementById('legend').append(row)}
function decode(text,Type){const raw=atob(text),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
let gl;try{gl=canvas.getContext('webgl',{antialias:true,alpha:false});if(!gl)throw Error('WebGL недоступен. Открой файл в Chrome или Edge с включённым аппаратным ускорением.');
const shader=(type,source)=>{let s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s};
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,`attribute vec3 position;attribute vec3 normal;attribute vec4 color;uniform mat4 mvp;uniform float upZ;varying vec3 n;varying vec4 c;varying float h;void main(){gl_Position=mvp*vec4(position,1.);n=normal;c=color;h=mix(position.y,position.z,upZ);}`));
gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`precision mediump float;varying vec3 n;varying vec4 c;varying float h;uniform float cut;uniform bool showUnknown;void main(){if(h>cut||(!showUnknown&&c.a<.5))discard;float light=.64+.36*abs(dot(normalize(n),normalize(vec3(.35,.65,.8))));gl_FragColor=vec4(c.rgb*light,1.);}`));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const positions=decode(data.positions,Float32Array),faces=decode(data.faces,Uint32Array),classColors=decode(data.class_colors,Uint8Array),objectColors=decode(data.object_colors,Uint8Array);
const expanded=new Float32Array(faces.length*3),normals=new Float32Array(expanded.length),colors=new Uint8Array(faces.length*4);
for(let f=0;f<faces.length/3;f++){const a=faces[3*f]*3,b=faces[3*f+1]*3,c=faces[3*f+2]*3;const ux=positions[b]-positions[a],uy=positions[b+1]-positions[a+1],uz=positions[b+2]-positions[a+2],vx=positions[c]-positions[a],vy=positions[c+1]-positions[a+1],vz=positions[c+2]-positions[a+2];let nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx,len=Math.hypot(nx,ny,nz)||1;for(let j=0;j<3;j++){const vertex=faces[3*f+j]*3,target=(3*f+j)*3;expanded[target]=positions[vertex];expanded[target+1]=positions[vertex+1];expanded[target+2]=positions[vertex+2];normals[target]=nx/len;normals[target+1]=ny/len;normals[target+2]=nz/len;colors.set(classColors.subarray(f*4,f*4+4),(3*f+j)*4)}}
function attribute(name,array,size,type,normalized){const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,array,gl.STATIC_DRAW);let location=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,type,normalized,0,0);return buffer}
attribute('position',expanded,3,gl.FLOAT,false);attribute('normal',normals,3,gl.FLOAT,false);const colorBuffer=attribute('color',colors,4,gl.UNSIGNED_BYTE,true);
const mvpLocation=gl.getUniformLocation(program,'mvp'),cutLocation=gl.getUniformLocation(program,'cut'),unknownLocation=gl.getUniformLocation(program,'showUnknown'),upLocation=gl.getUniformLocation(program,'upZ');
gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.clearColor(.055,.08,.12,1);
let yaw=.75,pitch=.55,distance=3.2,target=[0,0,0],upZ=true,drag=null,queued=false;
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2],norm=a=>{let d=Math.hypot(...a)||1;return a.map(v=>v/d)};
function basis(){const cp=Math.cos(pitch),direction=upZ?[cp*Math.cos(yaw),cp*Math.sin(yaw),Math.sin(pitch)]:[cp*Math.cos(yaw),Math.sin(pitch),cp*Math.sin(yaw)];let right=norm(cross(upZ?[0,0,1]:[0,1,0],direction));return {direction,right,up:cross(direction,right)}}
function multiply(a,b){const r=new Float32Array(16);for(let c=0;c<4;c++)for(let row=0;row<4;row++)for(let i=0;i<4;i++)r[c*4+row]+=a[i*4+row]*b[c*4+i];return r}
function draw(){queued=false;const dpr=Math.min(devicePixelRatio||1,2),width=Math.round(innerWidth*dpr),height=Math.round(innerHeight*dpr);if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}gl.viewport(0,0,width,height);const {direction:z,right:x,up:y}=basis(),eye=target.map((v,i)=>v+z[i]*distance);let view=new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]);const f=1/Math.tan(Math.PI/8),near=.01,far=100,projection=new Float32Array([f/(width/height),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);gl.uniformMatrix4fv(mvpLocation,false,multiply(projection,view));gl.uniform1f(cutLocation,Number(document.getElementById('cut').value));gl.uniform1f(upLocation,upZ?1:0);gl.uniform1i(unknownLocation,document.getElementById('unknown').checked?1:0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.drawArrays(gl.TRIANGLES,0,faces.length);status.textContent='Готово · обработка и просмотр на этом компьютере';document.body.dataset.ready='true'}
function schedule(){if(!queued){queued=true;requestAnimationFrame(draw)}}
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;let dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(drag.pan){let b=basis(),scale=distance/innerHeight;target=target.map((v,i)=>v-dx*scale*b.right[i]+dy*scale*b.up[i])}else{yaw-=dx*.006;pitch=Math.max(-1.48,Math.min(1.48,pitch+dy*.006))}drag.x=e.clientX;drag.y=e.clientY;schedule()});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('wheel',e=>{e.preventDefault();distance=Math.max(.15,Math.min(25,distance*Math.exp(e.deltaY*.001)));schedule()},{passive:false});
document.getElementById('mode').addEventListener('change',e=>{const chosen=e.target.value==='class'?classColors:objectColors;for(let f=0;f<faces.length/3;f++)for(let j=0;j<3;j++)colors.set(chosen.subarray(f*4,f*4+4),(3*f+j)*4);gl.bindBuffer(gl.ARRAY_BUFFER,colorBuffer);gl.bufferSubData(gl.ARRAY_BUFFER,0,colors);document.getElementById('legend').style.display=e.target.value==='class'?'block':'none';schedule()});
document.getElementById('up').addEventListener('change',e=>{upZ=e.target.value==='z';schedule()});for(const id of ['cut','unknown'])document.getElementById(id).addEventListener('input',schedule);document.getElementById('reset').addEventListener('click',()=>{yaw=.75;pitch=.55;distance=3.2;target=[0,0,0];document.getElementById('cut').value='1.05';schedule()});addEventListener('resize',schedule);schedule();
}catch(error){document.getElementById('error').textContent=error.message;status.textContent='Не удалось открыть 3D';document.body.dataset.error='true'}
</script></body></html>'''
