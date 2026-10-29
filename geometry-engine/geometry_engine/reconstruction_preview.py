"""Portable, offline visualization of reconstructed geometry (ARKit world, Y up)."""

import base64
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _encoded(array, dtype):
    return base64.b64encode(np.asarray(array, dtype=dtype).tobytes()).decode("ascii")


def _rotation(yaw, pitch):
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    return np.array([[cy, 0, sy], [-sp*sy, cp, sp*cy], [-cp*sy, -sp, cp*cy]])


def _preview(path, positions, colors, cameras, lower_y, upper_y):
    width, height = 650, 600
    canvas = Image.new("RGB", (width*2, height+110), (17, 24, 36))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 18)
        small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    # Display-only ceiling cut, shared with the default interactive view.
    keep = positions[:, 1] <= lower_y+.88*(upper_y-lower_y)
    p, c = positions[keep], colors[keep]
    stride = max(1, int(np.ceil(len(p)/250_000)))
    p, c = p[::stride], c[::stride]
    for panel, (yaw, pitch, title) in enumerate([
            (0, -np.pi/2, "Top view: reconstructed surface samples + camera path"),
            (-.65, -.6, "Oblique view: reconstructed surface samples")]):
        rotation = _rotation(yaw, pitch)
        q = p @ rotation.T
        cp = cameras @ rotation.T
        scale = min(width, height)*.46
        if len(q):
            xy = np.rint(q[:, :2]*[scale, -scale]+[width/2, height/2]).astype(int)
            inside = (xy[:, 0]>=0)&(xy[:, 0]<width)&(xy[:, 1]>=0)&(xy[:, 1]<height)
            xy, z, rgb = xy[inside], q[inside, 2], c[inside]
            # Paint far to near; colors come directly from the reconstructed mesh.
            order = np.argsort(z, kind="stable")
            image = np.full((height, width, 3), [17, 24, 36], dtype=np.uint8)
            image[xy[order, 1], xy[order, 0]] = rgb[order]
            canvas.paste(Image.fromarray(image), (panel*width, 42))
        draw.text((panel*width+15, 12), title, fill="white", font=font)
        if panel == 0 and len(cp):
            path_xy = cp[:, :2]*[scale, -scale]+[panel*width+width/2, 42+height/2]
            if len(path_xy)>1:
                draw.line([tuple(v) for v in path_xy], fill=(255, 159, 60), width=2)
            for x,y in path_xy:
                draw.ellipse((x-2,y-2,x+2,y+2), fill=(255, 181, 80))
    draw.text((15,height+52), "Y-up world coordinates. Top 12% hidden for visibility only; exported geometry is unchanged.", font=small, fill=(195,207,225))
    draw.text((15,height+77), "Open viewer.html to orbit the full mesh. Preview is diagnostic, not a guarantee of dimensional accuracy.", font=small, fill=(195,207,225))
    canvas.save(path)


def write_reconstruction_preview(output_dir, vertices, colors, faces, camera_positions):
    """Write viewer.html and preview.png; return relative paths.

    The interactive point mode displays mesh vertices, explicitly labelled as such.
    This helper does not modify the reconstruction, simplify faces, or fill holes.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    colors = np.asarray(colors)
    faces = np.asarray(faces)
    cameras = np.asarray(camera_positions, dtype=np.float64).reshape(-1,3)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all() or colors.shape != vertices.shape
            or not np.isfinite(colors).all() or faces.ndim != 2 or faces.shape[1] != 3
            or not np.issubdtype(faces.dtype, np.integer)
            or np.any(faces < 0) or np.any(faces >= len(vertices))
            or not np.isfinite(cameras).all()):
        raise ValueError("Expected finite colored vertices, triangle indices, and camera positions")
    if np.issubdtype(colors.dtype, np.floating):
        colors = np.rint(np.clip(colors,0,1)*255).astype(np.uint8)
    else:
        colors = np.clip(colors,0,255).astype(np.uint8)
    lower, upper = vertices.min(axis=0), vertices.max(axis=0)
    center = (lower+upper)*.5
    radius = max(float(np.linalg.norm(upper-lower)*.5),1e-6)
    positions = (vertices-center)/radius
    camera_positions = (cameras-center)/radius
    ymin, ymax = float((lower[1]-center[1])/radius), float((upper[1]-center[1])/radius)
    payload = dict(positions=_encoded(positions,"<f4"), colors=_encoded(colors,"u1"),
                   faces=_encoded(faces,"<u4"), cameras=_encoded(camera_positions,"<f4"),
                   vertexCount=len(vertices), faceCount=len(faces), cameraCount=len(cameras),
                   ymin=ymin, ymax=ymax, bounds=[lower.tolist(),upper.tolist()])
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("viewer.html", "preview.png"):
        if (output/name).exists():
            raise FileExistsError(f"Preview already exists: {output/name}")
    (output/"viewer.html").write_text(_HTML.replace("__DATA__",json.dumps(payload,separators=(",",":"))),encoding="utf-8")
    _preview(output/"preview.png",positions,colors,camera_positions,ymin,ymax)
    return {"viewer":"viewer.html", "preview":"preview.png"}


_HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RGB-D reconstruction</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#111824;color:#e5edf7;font:14px system-ui}canvas{display:block;width:100vw;height:100vh;touch-action:none}
aside{position:absolute;top:16px;left:16px;max-width:340px;padding:16px;background:#182332ed;border:1px solid #415065;border-radius:10px}h1{font-size:18px;margin:0 0 8px}p{color:#bac9db;line-height:1.45;margin:8px 0}label{display:block;margin:10px 0}select,button{background:#28384e;color:white;border:1px solid #65778e;border-radius:5px;padding:6px}input[type=range]{width:160px;vertical-align:middle}#error{color:#ffb1b1}
</style><canvas id="view"></canvas><aside><h1>RGB-D reconstruction</h1><div id="stats"></div>
<label>Display <select id="mode"><option value="mesh">Mesh</option><option value="points">Mesh vertices</option></select></label>
<label><input type="checkbox" id="trajectory" checked> Camera trajectory</label>
<label>Visible height <input type="range" id="cut" min="0" max="100" value="88"> <span id="cutValue">88%</span></label>
<button id="reset">Reset view</button><button id="top">Top view</button>
<p>Drag: orbit · Shift-drag: pan · Scroll: zoom</p><p>Y up. Height cut affects the display only. Exported geometry is unchanged.</p><div id="error"></div></aside>
<script>
'use strict';const data=__DATA__;
const canvas=document.querySelector('#view'),error=document.querySelector('#error');
const gl=canvas.getContext('webgl2',{antialias:true});
function decode(text,Type){const b=atob(text),a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return new Type(a.buffer)}
if(!gl){error.textContent='WebGL2 is unavailable. Open preview.png or mesh.ply instead.'}else{try{
const vs=`#version 300 es
in vec3 position;in vec3 color;uniform vec4 view;uniform vec2 pan;uniform float aspect;uniform float pointSize;
out vec3 rgb;out vec3 local;out float worldHeight;
void main(){float cy=cos(view.x),sy=sin(view.x),cp=cos(view.y),sp=sin(view.y);float x=cy*position.x+sy*position.z;float z=-sy*position.x+cy*position.z;
vec3 p=vec3(x,cp*position.y+sp*z,-sp*position.y+cp*z);local=p;worldHeight=position.y;rgb=color;
gl_Position=vec4((p.x*view.z+pan.x)/aspect,p.y*view.z+pan.y,-p.z*.25,1.);gl_PointSize=pointSize;}`;
const fs=`#version 300 es
precision highp float;in vec3 rgb;in vec3 local;in float worldHeight;uniform float cutoff;uniform int kind;out vec4 result;
void main(){if(kind!=2&&worldHeight>cutoff)discard;if(kind==1&&length(gl_PointCoord-vec2(.5))>.5)discard;
float light=1.;if(kind==0){vec3 n=cross(dFdx(local),dFdy(local));float l=length(n);if(l>1e-9)light=.65+.35*abs(dot(n/l,normalize(vec3(.4,.6,1.))));}
result=vec4(kind==2?vec3(1.,.6,.2):rgb*light,1.);}`;
function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const loc={};for(const n of ['view','pan','aspect','pointSize','cutoff','kind'])loc[n]=gl.getUniformLocation(program,n);
function buffer(array,target=gl.ARRAY_BUFFER){const b=gl.createBuffer();gl.bindBuffer(target,b);gl.bufferData(target,array,gl.STATIC_DRAW);return b}
const meshVAO=gl.createVertexArray();gl.bindVertexArray(meshVAO);
buffer(decode(data.positions,Float32Array));const ap=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(ap);gl.vertexAttribPointer(ap,3,gl.FLOAT,false,0,0);
buffer(decode(data.colors,Uint8Array));const ac=gl.getAttribLocation(program,'color');gl.enableVertexAttribArray(ac);gl.vertexAttribPointer(ac,3,gl.UNSIGNED_BYTE,true,0,0);
buffer(decode(data.faces,Uint32Array),gl.ELEMENT_ARRAY_BUFFER);
const cameraVAO=gl.createVertexArray();gl.bindVertexArray(cameraVAO);buffer(decode(data.cameras,Float32Array));gl.enableVertexAttribArray(ap);gl.vertexAttribPointer(ap,3,gl.FLOAT,false,0,0);gl.disableVertexAttribArray(ac);gl.vertexAttrib3f(ac,1,.6,.2);
let yaw=-.65,pitch=-.6,zoom=1.55,pan=[0,0],drag=null;
function render(){const dpr=Math.min(devicePixelRatio||1,2),w=Math.round(canvas.clientWidth*dpr),h=Math.round(canvas.clientHeight*dpr);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}gl.viewport(0,0,w,h);gl.clearColor(.067,.094,.14,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
gl.uniform4f(loc.view,yaw,pitch,zoom,0);gl.uniform2fv(loc.pan,pan);gl.uniform1f(loc.aspect,w/h);gl.uniform1f(loc.cutoff,data.ymin+(data.ymax-data.ymin)*Number(document.querySelector('#cut').value)/100+1e-5);gl.uniform1f(loc.pointSize,2*dpr);
gl.bindVertexArray(meshVAO);const points=document.querySelector('#mode').value==='points';gl.uniform1i(loc.kind,points?1:0);if(points)gl.drawArrays(gl.POINTS,0,data.vertexCount);else gl.drawElements(gl.TRIANGLES,data.faceCount*3,gl.UNSIGNED_INT,0);
if(document.querySelector('#trajectory').checked){gl.bindVertexArray(cameraVAO);gl.uniform1i(loc.kind,2);gl.uniform1f(loc.pointSize,5*dpr);gl.drawArrays(gl.LINE_STRIP,0,data.cameraCount);gl.drawArrays(gl.POINTS,0,data.cameraCount)}}
document.querySelector('#stats').textContent=`${data.vertexCount.toLocaleString()} vertices · ${data.faceCount.toLocaleString()} triangles · ${data.cameraCount} cameras`;
canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag[0],dy=e.clientY-drag[1];drag=[e.clientX,e.clientY];if(e.shiftKey||e.buttons===2){pan[0]+=2*dx/canvas.clientHeight;pan[1]-=2*dy/canvas.clientHeight}else{yaw+=dx*.008;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,pitch+dy*.008))}render()});
canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.1,Math.min(40,zoom*Math.exp(-e.deltaY*.001)));render()},{passive:false});
for(const id of ['mode','trajectory','cut'])document.querySelector('#'+id).addEventListener('input',()=>{document.querySelector('#cutValue').textContent=document.querySelector('#cut').value+'%';render()});
document.querySelector('#reset').onclick=()=>{yaw=-.65;pitch=-.6;zoom=1.55;pan=[0,0];render()};document.querySelector('#top').onclick=()=>{yaw=0;pitch=-Math.PI/2;pan=[0,0];render()};window.addEventListener('resize',render);render();
}catch(e){error.textContent=e.message}}
</script></html>'''
