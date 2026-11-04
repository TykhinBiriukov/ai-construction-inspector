const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function viewer(){
 const matrices=[],frames=[];
 const gl=new Proxy({uniformMatrix4fv(_name,_transpose,m){matrices.push([...m]);},getShaderParameter:()=>true,getProgramParameter:()=>true,getExtension:()=>null},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
 const element=()=>({style:{},dataset:{},classList:{toggle(){}},append(){},replaceChildren(){},addEventListener(){},parentElement:{},checked:true,value:'.75',clientWidth:1200,clientHeight:650});
 const canvas=Object.assign(element(),{getContext:()=>gl,getBoundingClientRect:()=>({width:1200,height:650}),setPointerCapture(){}});
 const controls=new Map([['view',canvas]]);
 const host=Object.assign(element(),{querySelector(selector){const id=selector.match(/data-control="([^"]+)"/)?.[1]||selector;if(!controls.has(id))controls.set(id,element());return controls.get(id);}});
 const layer=(id,side,category,bounds)=>({id,side,category,bounds,visible:true,lines:[bounds[0],bounds[1]]});
 const data={bounds:[[0,0,0],[10,3.3,2.8]],raw_mesh:layer('raw','raw','wall',[[0,0,0],[10,3.3,2.8]]),findings:[],layers:[layer('room','ifc','wall',[[0,0,0],[7.2,3.3,2.8]]),layer('door','ifc','door',[[.2,3.3,0],[1.1,3.3,2.1]]),layer('observed','scan','door',[[.3,3.3,0],[1.2,3.3,2.1]])]};
 const context={window:{},document:{createElement:element,createTextNode:text=>text},devicePixelRatio:1,performance:{now:()=>0},requestAnimationFrame:cb=>(frames.push(cb),frames.length),cancelAnimationFrame(){},ResizeObserver:class{observe(){}disconnect(){}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/ai_inspector/web/demo-comparison.js'),'utf8'),context);
 const api=context.window.DemoComparison.create(host,data,['observed']);
 const flush=()=>{while(frames.length)frames.shift()();return matrices.at(-3);};
 const rotate=()=>{canvas.onpointerdown({clientX:0,clientY:0,button:0,pointerId:1});canvas.onpointermove({clientX:200,clientY:25});canvas.onpointerup();return flush();};
 return {api,canvas,controls,flush,rotate};
}
const pivot=m=>[m[12]/m[15],m[13]/m[15]];
function samePoint(a,b){for(let i=0;i<2;i++)assert(Math.abs(a[i]-b[i])<1e-6,`pivot moved: ${a} -> ${b}`);}
test('initial evidence highlight preserves the room-centred overview',()=>{
 const v=viewer();const before=v.flush();v.api.selectPair('observed','door',{focusView:false});samePoint(pivot(before),pivot(v.flush()));samePoint(pivot(before),[0,0]);samePoint(pivot(before),pivot(v.rotate()));
});
test('detail focus and pan do not relocate the room rotation axis',()=>{
 const v=viewer();v.flush();v.api.selectPair('observed','door');const focused=pivot(v.flush());samePoint(focused,pivot(v.rotate()));
 v.canvas.onpointerdown({clientX:0,clientY:0,button:2,pointerId:1});v.canvas.onpointermove({clientX:70,clientY:30});v.canvas.onpointerup();const panned=pivot(v.flush());samePoint(panned,pivot(v.rotate()));
 v.controls.get('all').onclick();samePoint(pivot(v.flush()),[0,0]);
});
