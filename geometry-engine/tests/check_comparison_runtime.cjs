const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const payload=html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const source=html.split('</script><script>')[1].split('</script>')[0];
let now=0,queue=[];const nodes=new Map();
const gl=new Proxy({}, {get:(o,k)=>{if(k==='getShaderParameter'||k==='getProgramParameter')return ()=>true;if(k==='getAttribLocation')return ()=>1;if(k==='getUniformLocation')return (_,n)=>n;if(k===k.toUpperCase())return 1;return ()=>({})}});
function node(){return {style:{},dataset:{},classList:{toggle(){}},append(){},addEventListener(){},setPointerCapture(){},getBoundingClientRect(){return {left:0,width:1500,height:900}},getContext(){return gl},clientHeight:900,checked:true,value:'.75',textContent:''}}
const ctx={console,Float32Array,Uint32Array,Uint8Array,Math,JSON,Map,Set,Image:class{},performance:{now:()=>now},devicePixelRatio:1,atob:s=>Buffer.from(s,'base64').toString('binary'),requestAnimationFrame:cb=>queue.push(cb),addEventListener(){},document:{body:node(),querySelector:()=>node(),createElement:node,createTextNode:t=>t,getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());const n=nodes.get(id);if(id==='data')n.textContent=payload;return n}}};ctx.window=ctx;
vm.createContext(ctx);vm.runInContext(source,ctx);
function tick(t){now=t;const jobs=queue;queue=[];jobs.forEach(f=>f(t));}
tick(0);assert.equal(ctx.document.body.dataset.error,undefined);assert.equal(ctx.document.body.dataset.ready,'true');

nodes.get('overlay').onclick();tick(450);assert(Math.abs(vm.runInContext('spread',ctx)-.5)<.001);tick(900);assert.equal(vm.runInContext('spread',ctx),0);
nodes.get('split').onclick();tick(1350);assert(Math.abs(vm.runInContext('spread',ctx)-.5)<.001);
nodes.get('overlay').onclick();tick(1800);assert(Math.abs(vm.runInContext('spread',ctx)-.25)<.001);tick(2250);assert.equal(vm.runInContext('spread',ctx),0);
console.log('DOM/WebGL-call contract, intermediate animation positions and mid-flight reversal passed (mock GL, not a visual browser test).');

if(JSON.parse(payload).raw_mesh){const before=vm.runInContext('yaw',ctx);const canvas=nodes.get('view');canvas.onpointerdown({clientX:1250,clientY:400,button:0,pointerId:1});canvas.onpointermove({clientX:1300,clientY:450});canvas.onpointerup();tick(2300);assert.notEqual(vm.runInContext('yaw',ctx),before);assert(!source.includes('rawCamera'));assert(source.includes("gl.uniform1f(uni.cut,(+el('cut').value-center[2])/radius)"));assert(source.includes("pane(compareWidth,canvas.width-compareWidth,'raw',0)"));console.log('RGB pane uses shared camera and cut, with zero slide offset.');}
