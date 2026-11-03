// Uses the Inspector viewer's already pinned Three.js/esbuild toolchain.
import {build} from '../../ai_inspector/viewer/node_modules/esbuild/lib/main.js';
import {copyFile} from 'node:fs/promises';
const dir=new URL('.',import.meta.url);
await build({entryPoints:[new URL('surface-view.js',dir).pathname],outfile:new URL('surface.bundle.js',dir).pathname,bundle:true,format:'iife',globalName:'ProgressSurface',minify:true,legalComments:'linked'});
await copyFile(new URL('../../ai_inspector/viewer/node_modules/three/LICENSE',dir),new URL('three-LICENSE.txt',dir));
// Publish the same viewer to Inspector's same-origin static assets.
const {mkdir}=await import('node:fs/promises');
const embedded=new URL('../../ai_inspector/src/ai_inspector/web/progress4d/',dir);
await mkdir(embedded,{recursive:true});
for(const file of ['viewer.html','viewer.css','viewer.js','surface.bundle.js','surface.bundle.js.LEGAL.txt','three-LICENSE.txt']) await copyFile(new URL(file,dir),new URL(file,embedded));
