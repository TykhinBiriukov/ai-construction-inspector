"""Offline mesh viewer with separate translucent RANSAC plane overlays.

Only the browser display is normalized. Plane coordinates and mesh coordinates
share exactly the same translation and scale; no plane is added to the mesh.
"""

import base64
import colorsys
import json
from pathlib import Path

import numpy as np

from .segmented_mesh_export import UNKNOWN_COLOR, class_colors


def plane_color(plane_id):
    """A deterministic RGB color shared by a plane and its supporting faces."""
    return tuple(round(v*255) for v in colorsys.hsv_to_rgb((plane_id*.61803398875+.05) % 1, .68, .95))


def _encoded(array, dtype):
    return base64.b64encode(np.asarray(array, dtype=dtype).tobytes()).decode("ascii")


def export_wall_plane_viewer(output_path, vertices, faces, face_plane_id, planes, *,
                             up_axis="z", semantic_classes=None, labels=None):
    """Write one self-contained HTML file without altering input arrays.

    Plane rectangles describe observed support extents, not measured wall edges.
    Existing output files are protected; output_path is a file, not a directory.
    """
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Viewer already exists: {output}")
    vertices, faces, assignments = np.asarray(vertices, dtype=float), np.asarray(faces), np.asarray(face_plane_id)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer) or np.any(faces < 0) or np.any(faces >= len(vertices))
            or assignments.shape != (len(faces),) or not np.issubdtype(assignments.dtype, np.integer)
            or np.any(assignments < -1) or up_axis not in ("x", "y", "z")):
        raise ValueError("Expected finite triangle geometry, one plane ID per face and X, Y or Z up")
    if not isinstance(planes, list):
        raise ValueError("planes must be a list")
    lower, upper = vertices.min(axis=0), vertices.max(axis=0)
    center, radius = (lower+upper)*.5, max(float(np.linalg.norm(upper-lower)*.5), 1e-6)
    records, ids = [], set()
    for plane in planes:
        identity = plane.get("plane_id")
        corners, normal = np.asarray(plane.get("corners_world"), dtype=float), np.asarray(plane.get("normal"), dtype=float)
        offset = plane.get("offset_m")
        if (type(identity) is not int or identity < 0 or identity in ids or corners.shape != (4, 3)
                or normal.shape != (3,) or not np.isfinite(corners).all() or not np.isfinite(normal).all()
                or not np.isclose(np.linalg.norm(normal), 1., atol=1e-6)
                or type(offset) not in (int, float) or not np.isfinite(offset)
                or np.max(np.abs(corners@normal+offset)) > 1e-5):
            raise ValueError("Expected unique plane IDs, unit normals and coplanar finite corners")
        if np.linalg.norm(np.cross(corners[1]-corners[0], corners[3]-corners[0])) < 1e-10:
            raise ValueError("Plane rectangle must have nonzero area")
        ids.add(identity)
        records.append({**plane, "corners_world": corners.tolist(), "normal": normal.tolist(),
                        "color": plane_color(identity), "corners_normalized": ((corners-center)/radius).tolist(),
                        "center_normalized": ((corners.mean(axis=0)-center)/radius).tolist()})
    if any(int(identity) not in ids for identity in np.unique(assignments[assignments >= 0])):
        raise ValueError("Face refers to a missing plane")
    records.sort(key=lambda plane: plane["plane_id"])
    wall_candidates = [record for record in records if record.get("status") == "wall_candidate"]
    if wall_candidates:
        focus = np.concatenate([record["corners_normalized"] for record in wall_candidates])
        focus_lower, focus_upper = focus.min(axis=0), focus.max(axis=0)
        focus_center = (focus_lower+focus_upper)*.5
        focus_radius = max(float(np.linalg.norm(focus_upper-focus_lower)*.5), .05)
    else:
        focus_center, focus_radius = np.zeros(3), 1.
    support_colors = np.tile(np.asarray([*UNKNOWN_COLOR, 0], dtype=np.uint8), (len(faces), 1))
    for record in records:
        support_colors[assignments == record["plane_id"]] = [*record["color"], 255]
    semantic_colors = None
    if semantic_classes is not None:
        semantic_classes = np.asarray(semantic_classes)
        if (not isinstance(labels, (list, tuple)) or any(not isinstance(label, str) for label in labels)
                or len(set(labels)) != len(labels) or semantic_classes.shape != assignments.shape
                or not np.issubdtype(semantic_classes.dtype, np.integer)
                or np.any((semantic_classes < -1) | (semantic_classes >= len(labels)))):
            raise ValueError("Invalid semantic class IDs or labels")
        semantic_colors = support_colors.copy()
        semantic_colors[:, :3] = UNKNOWN_COLOR
        known = semantic_classes >= 0
        semantic_colors[known, :3] = class_colors(labels)[semantic_classes[known]]
    payload = {"schema_version": "1.0", "positions": _encoded((vertices-center)/radius, "<f4"),
               "faces": _encoded(faces, "<u4"), "face_plane_ids": _encoded(assignments, "<i4"),
               "support_colors": _encoded(support_colors, "u1"),
               "semantic_colors": _encoded(semantic_colors, "u1") if semantic_colors is not None else None,
               "semantic_labels": list(labels) if semantic_classes is not None else [],
               "vertex_count": len(vertices), "face_count": len(faces),
               "supported_face_count": int((assignments >= 0).sum()), "planes": records,
               "up_axis": up_axis, "normalization": {"center_world": center.tolist(), "radius_m": radius},
               "initial_camera": {"target": focus_center.tolist(), "distance": max(focus_radius*3.2, .15)},
               "bounds_world": [lower.tolist(), upper.tolist()],
               "plane_extent_meaning": "observed_support_bounds_not_wall_boundaries"}
    # Labels and candidate metadata may contain external text. JSON is data only.
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_VIEWER.replace("__DATA__", data), encoding="utf-8")
    return output


_VIEWER = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Плоскости стен · RANSAC</title><style>
*{box-sizing:border-box}body{margin:0;background:#111923;color:#eef3fa;font:14px system-ui,sans-serif;overflow:hidden}
canvas{display:block;width:100vw;height:100vh;touch-action:none}
aside{position:fixed;top:16px;left:16px;width:310px;padding:20px;border:1px solid #40516a;border-radius:14px;background:#182334f2;box-shadow:0 8px 32px #0005;max-height:calc(100vh - 32px);overflow:auto}
h1{font-size:20px;margin:0 0 10px}p{line-height:1.45;margin:9px 0;color:#c2cee0}label{display:block;margin:12px 0 5px}select,button{font:inherit;background:#293950;color:#f2f6fc;border:1px solid #536681;border-radius:6px;padding:7px;cursor:pointer}select,input[type=range]{width:100%}input[type=checkbox]{accent-color:#74b9f7}button{margin:10px 6px 0 0}.help{font-size:12px;color:#aebed3}.stat{font-size:13px;padding:10px;border-radius:6px;background:#101a28;line-height:1.6;white-space:pre-line}.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:8px}.status{position:fixed;bottom:16px;right:16px;max-width:50vw;padding:10px 14px;background:#182334ed;border-radius:8px}#error{color:#ffb3a5}output{float:right;color:#a9d5ff}.row{display:flex;gap:12px}.row label{flex:1}@media(max-width:720px){aside{width:235px;left:8px;top:8px;padding:13px;font-size:12px}h1{font-size:17px}.stat{font-size:11px}.status{font-size:11px;bottom:8px;right:8px}}
</style></head><body>
<canvas id="view" aria-label="Исходный mesh и полупрозрачные плоскости"></canvas>
<aside><h1>Плоскости стен</h1><p id="summary"></p><p class="help">Расчёт плоскостей — только по треугольникам с меткой SAM «стена».</p>
<label for="plane">Выбрать плоскость</label><select id="plane"><option value="all">Все плоскости</option></select>
<p id="plane-stats" class="stat"></p>
<div class="row"><label><input id="show-mesh" type="checkbox" checked> Mesh</label><label><input id="show-planes" type="checkbox" checked> Плоскости</label></div>
<label for="opacity">Непрозрачность плоскостей <output id="opacity-value">25%</output></label>
<input id="opacity" type="range" min="0" max="0.8" step="0.01" value="0.25">
<label for="mode">Цвет треугольников</label><select id="mode"><option value="support">По подтверждаемой плоскости</option><option value="plain">Нейтральный</option></select>
<label><input id="unknown" type="checkbox" checked> Остальные треугольники</label>
<label for="up">Вертикальная ось</label><select id="up"><option value="z">Z</option><option value="y">Y</option><option value="x">X</option></select>
<label for="cut">Срез сверху <output id="cut-value"></output></label><input id="cut" type="range">
<button id="reset">Сбросить вид</button><button id="uncut">Показать всю высоту</button>
<p class="help">Потяни — поворот · колесо — приближение.<br>Shift + потяни — перемещение.</p>
<p class="help">Прямоугольники показывают область найденных фрагментов. Это ещё не границы стен. Наложение проходит через пробелы, но не добавляет треугольники и не закрывает проёмы в mesh.</p>
<p id="error"></p></aside><div id="status" class="status">Загрузка…</div>
<script id="wall-plane-data" type="application/json">__DATA__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("wall-plane-data").textContent);
const el=id=>document.getElementById(id), canvas=el("view"), status=el("status");
const format=(value,digits=2)=>Number(value).toLocaleString("ru-RU",{maximumFractionDigits:digits});
el("summary").textContent=`${data.planes.length} плоскостей · ${format(data.face_count,0)} треугольников. С плоскостями согласованы ${format(100*data.supported_face_count/data.face_count,1)}%.`;
el("up").value=data.up_axis;
for(const plane of data.planes){
  const option=document.createElement("option"); option.value=String(plane.plane_id);
  option.textContent=`Плоскость ${plane.plane_id+1} · ${format(plane.support_area_m2)} м² фрагментов`;
  el("plane").append(option);
}
if(data.semantic_colors){const option=document.createElement("option");option.value="semantic";option.textContent="Метки SAM";el("mode").append(option)}
function updatePlaneStats(){
  const selected=el("plane").value;
  const plane=data.planes.find(item=>String(item.plane_id)===selected);
  if(!plane){el("plane-stats").textContent="Выбери плоскость для проверки её опорных треугольников и погрешности прилегания.";return}
  const name=plane.status==="wall_candidate"?"Кандидат в стену · есть поддержка SAM":"Вертикальная плоскость · тип не подтверждён";
  el("plane-stats").textContent=`${name}\nПлощадь фрагментов: ${format(plane.support_area_m2)} м²\nТреугольников: ${format(plane.support_face_count,0)}\nСреднеквадратичное отклонение: ${format(1000*plane.rmse_m,1)} мм\n95% расстояний ≤ ${format(1000*plane.p95_distance_m,1)} мм\nОбласть фрагментов: ${format(plane.width_m)} × ${format(plane.height_m)} м`;
}
function decode(text,Type){const raw=atob(text),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const norm=a=>{const length=Math.hypot(...a)||1;return a.map(value=>value/length)};
function multiply(a,b){const result=new Float32Array(16);for(let c=0;c<4;c++)for(let row=0;row<4;row++)for(let i=0;i<4;i++)result[c*4+row]+=a[i*4+row]*b[c*4+i];return result}
try {
  const gl=canvas.getContext("webgl",{antialias:true,alpha:false});
  if(!gl)throw Error("WebGL недоступен. Открой HTML в Chrome или Edge с аппаратным ускорением.");
  function shader(type,source){const result=gl.createShader(type);gl.shaderSource(result,source);gl.compileShader(result);if(!gl.getShaderParameter(result,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(result));return result}
  const program=gl.createProgram();
  gl.attachShader(program,shader(gl.VERTEX_SHADER,`
    attribute vec3 position; attribute vec3 normal; attribute vec4 color;
    uniform mat4 mvp; uniform vec3 upVector; uniform float overlayBias;
    varying vec3 n; varying vec4 c; varying float h;
    void main(){gl_Position=mvp*vec4(position,1.);gl_Position.z-=overlayBias*gl_Position.w;n=normal;c=color;h=dot(position,upVector);}
  `));
  gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`
    precision mediump float;
    varying vec3 n; varying vec4 c; varying float h;
    uniform float cut; uniform bool showUnknown; uniform int kind; uniform float opacity;
    void main(){
      if(h>cut || (kind==0 && !showUnknown && c.a<.5))discard;
      float light=kind==0 ? .64+.36*abs(dot(normalize(n),normalize(vec3(.35,.65,.8)))) : 1.;
      gl_FragColor=vec4(c.rgb*light,kind==1 ? opacity : 1.);
    }
  `));
  gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
  const attributes=Object.fromEntries(["position","normal","color"].map(name=>[name,gl.getAttribLocation(program,name)]));
  const uniforms=Object.fromEntries(["mvp","upVector","overlayBias","cut","showUnknown","kind","opacity"].map(name=>[name,gl.getUniformLocation(program,name)]));
  function buffer(values){const result=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,result);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);return result}
  function bindAttribute(name,source,size,type=gl.FLOAT,normalized=false){gl.bindBuffer(gl.ARRAY_BUFFER,source);gl.enableVertexAttribArray(attributes[name]);gl.vertexAttribPointer(attributes[name],size,type,normalized,0,0)}
  const positions=decode(data.positions,Float32Array),faces=decode(data.faces,Uint32Array);
  const facePlaneIds=decode(data.face_plane_ids,Int32Array),supportColors=decode(data.support_colors,Uint8Array);
  const semanticColors=data.semantic_colors?decode(data.semantic_colors,Uint8Array):null;
  const expanded=new Float32Array(faces.length*3),normals=new Float32Array(expanded.length),colors=new Uint8Array(faces.length*4);
  for(let face=0;face<faces.length/3;face++){
    const a=faces[face*3]*3,b=faces[face*3+1]*3,c=faces[face*3+2]*3;
    const ux=positions[b]-positions[a],uy=positions[b+1]-positions[a+1],uz=positions[b+2]-positions[a+2];
    const vx=positions[c]-positions[a],vy=positions[c+1]-positions[a+1],vz=positions[c+2]-positions[a+2];
    const nx=uy*vz-uz*vy,ny=uz*vx-ux*vz,nz=ux*vy-uy*vx,length=Math.hypot(nx,ny,nz)||1;
    for(let corner=0;corner<3;corner++){
      const source=faces[face*3+corner]*3,destination=(face*3+corner)*3;
      expanded[destination]=positions[source];expanded[destination+1]=positions[source+1];expanded[destination+2]=positions[source+2];
      normals[destination]=nx/length;normals[destination+1]=ny/length;normals[destination+2]=nz/length;
    }
  }
  const meshBuffers={position:buffer(expanded),normal:buffer(normals),color:buffer(colors)};
  function updateMeshColors(){
    const selected=el("plane").value,mode=el("mode").value;
    for(let face=0;face<faces.length/3;face++){
      const source=mode==="semantic"?semanticColors:supportColors;
      let rgb=mode==="plain"?[156,163,175]:[source[face*4],source[face*4+1],source[face*4+2]];
      if(selected!=="all" && facePlaneIds[face]!==Number(selected))rgb=rgb.map(value=>Math.round(value*.35+90*.65));
      const rgba=[...rgb,supportColors[face*4+3]];
      for(let corner=0;corner<3;corner++)colors.set(rgba,(3*face+corner)*4);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER,meshBuffers.color);gl.bufferSubData(gl.ARRAY_BUFFER,0,colors);
  }
  const planeBuffers=data.planes.map(plane=>{
    const corners=plane.corners_normalized;
    const triangles=new Float32Array([0,1,2,0,2,3].flatMap(index=>corners[index]));
    const lines=new Float32Array([0,1,1,2,2,3,3,0].flatMap(index=>corners[index]));
    return {plane,triangles:buffer(triangles),lines:buffer(lines)};
  });
  gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.clearColor(.055,.08,.12,1);
  let yaw=.75,pitch=.7,distance=data.initial_camera.distance,target=[...data.initial_camera.target],upAxis=data.up_axis,drag=null,queued=false;
  const axisIndex=()=>["x","y","z"].indexOf(upAxis);
  const upVector=()=>[0,1,2].map(index=>index===axisIndex()?1:0);
  function basis(){
    const cp=Math.cos(pitch),sp=Math.sin(pitch),cy=cp*Math.cos(yaw),sy=cp*Math.sin(yaw);
    const direction=upAxis==="z"?[cy,sy,sp]:upAxis==="y"?[cy,sp,sy]:[sp,cy,sy];
    const right=norm(cross(upVector(),direction));return {direction,right,up:cross(direction,right)};
  }
  function configureCut(){
    const axis=axisIndex(),min=data.bounds_world[0][axis],max=data.bounds_world[1][axis],range=Math.max(max-min,.01);
    el("cut").min=min;el("cut").max=max+range*.005;el("cut").step=range/300;el("cut").value=min+range*.85;
  }
  function draw(){
    queued=false;
    const dpr=Math.min(devicePixelRatio||1,2),width=Math.round(innerWidth*dpr),height=Math.round(innerHeight*dpr);
    if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}gl.viewport(0,0,width,height);
    const {direction:z,right:x,up:y}=basis(),eye=target.map((value,i)=>value+z[i]*distance);
    const view=new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]);
    const f=1/Math.tan(Math.PI/8),near=.01,far=100,projection=new Float32Array([f/(width/height),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);
    const cutWorld=Number(el("cut").value),cut=(cutWorld-data.normalization.center_world[axisIndex()])/data.normalization.radius_m;
    gl.uniformMatrix4fv(uniforms.mvp,false,multiply(projection,view));gl.uniform1f(uniforms.cut,cut);gl.uniform3f(uniforms.upVector,...upVector());
    gl.uniform1i(uniforms.showUnknown,el("unknown").checked?1:0);gl.uniform1f(uniforms.opacity,Number(el("opacity").value));
    gl.depthMask(true);gl.disable(gl.BLEND);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    if(el("show-mesh").checked){
      gl.uniform1i(uniforms.kind,0);gl.uniform1f(uniforms.overlayBias,0);
      bindAttribute("position",meshBuffers.position,3);bindAttribute("normal",meshBuffers.normal,3);bindAttribute("color",meshBuffers.color,4,gl.UNSIGNED_BYTE,true);
      gl.drawArrays(gl.TRIANGLES,0,faces.length);
    }
    if(el("show-planes").checked){
      const selected=el("plane").value;
      const visible=planeBuffers.filter(item=>selected==="all"||String(item.plane.plane_id)===selected);
      // Back-to-front ordering plus read-only depth makes overlapping overlays translucent.
      visible.sort((a,b)=>dot(a.plane.center_normalized,z)-dot(b.plane.center_normalized,z));
      gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);
      gl.disableVertexAttribArray(attributes.normal);gl.vertexAttrib3f(attributes.normal,0,0,1);
      gl.disableVertexAttribArray(attributes.color);
      gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(-1,-1);
      for(const item of visible){
        const rgb=item.plane.color.map(value=>value/255);
        gl.vertexAttrib4f(attributes.color,...rgb,1);gl.uniform1i(uniforms.kind,1);gl.uniform1f(uniforms.overlayBias,0);
        bindAttribute("position",item.triangles,3);gl.drawArrays(gl.TRIANGLES,0,6);
      }
      gl.disable(gl.POLYGON_OFFSET_FILL);
      for(const item of visible){
        const rgb=item.plane.color.map(value=>value/255);
        gl.vertexAttrib4f(attributes.color,...rgb,1);gl.uniform1i(uniforms.kind,2);gl.uniform1f(uniforms.overlayBias,.00002);
        bindAttribute("position",item.lines,3);gl.drawArrays(gl.LINES,0,8);
      }
      gl.depthMask(true);gl.disable(gl.BLEND);
    }
    el("cut-value").textContent=`${format(cutWorld)} м`;el("opacity-value").textContent=`${Math.round(Number(el("opacity").value)*100)}%`;
    status.textContent="Готово · полупрозрачные плоскости поверх исходной геометрии";document.body.dataset.ready="true";
  }
  function schedule(){if(!queued){queued=true;requestAnimationFrame(draw)}}
  canvas.addEventListener("pointerdown",event=>{drag={x:event.clientX,y:event.clientY,pan:event.shiftKey||event.button===2};canvas.setPointerCapture(event.pointerId)});
  canvas.addEventListener("pointermove",event=>{
    if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    if(drag.pan){const b=basis(),scale=distance/innerHeight;target=target.map((value,i)=>value-dx*scale*b.right[i]+dy*scale*b.up[i])}
    else{yaw-=dx*.006;pitch=Math.max(-1.48,Math.min(1.48,pitch+dy*.006))}
    drag.x=event.clientX;drag.y=event.clientY;schedule();
  });
  for(const name of ["pointerup","pointercancel"])canvas.addEventListener(name,()=>drag=null);
  canvas.addEventListener("contextmenu",event=>event.preventDefault());
  canvas.addEventListener("wheel",event=>{event.preventDefault();distance=Math.max(.15,Math.min(25,distance*Math.exp(event.deltaY*.001)));schedule()},{passive:false});
  el("plane").addEventListener("change",()=>{updatePlaneStats();updateMeshColors();schedule()});
  el("mode").addEventListener("change",()=>{updateMeshColors();schedule()});
  for(const id of ["show-mesh","show-planes","opacity","unknown","cut"])el(id).addEventListener("input",schedule);
  el("up").addEventListener("change",()=>{upAxis=el("up").value;configureCut();schedule()});
  el("uncut").addEventListener("click",()=>{el("cut").value=el("cut").max;schedule()});
  el("reset").addEventListener("click",()=>{yaw=.75;pitch=.7;distance=data.initial_camera.distance;target=[...data.initial_camera.target];configureCut();schedule()});
  addEventListener("resize",schedule);
  updatePlaneStats();updateMeshColors();configureCut();schedule();
} catch(error){el("error").textContent=error.message;status.textContent="Не удалось открыть 3D";document.body.dataset.error="true"}
</script></body></html>'''
