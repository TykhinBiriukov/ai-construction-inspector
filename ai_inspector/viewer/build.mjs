import {build} from 'esbuild';
import {copyFile,mkdir} from 'node:fs/promises';
const out='../src/ai_inspector/web/vendor';
await mkdir(out,{recursive:true});
await build({entryPoints:['viewer.js'],outfile:out+'/documents.js',bundle:true,format:'iife',globalName:'ProjectDocuments',minify:true,legalComments:'linked',platform:'browser'});
await copyFile('node_modules/web-ifc/web-ifc.wasm',out+'/web-ifc.wasm');
await copyFile('node_modules/pdfjs-dist/build/pdf.worker.min.mjs',out+'/pdf.worker.min.mjs');

for(const [name,license] of [['three','LICENSE'],['web-ifc','LICENSE.md'],['pdfjs-dist','LICENSE']]) await copyFile('node_modules/'+name+'/'+license,out+'/'+name+'-LICENSE.txt');
