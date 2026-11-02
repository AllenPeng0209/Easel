import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const ts=require('typescript');
const code=ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/ProfilePage.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const text=node=>typeof node==='string'||typeof node==='number'?String(node):!node||typeof node!=='object'?'':[node.props?.children].flat(Infinity).map(text).join('');
function view({personas=[],persona='',role='operator'}={}){
 const fibers=new Map(),events=[],writes=[];let current,effects=[],tree;
 const hooks={useState(initial){const f=current,i=f.cursor++;if(!(i in f.slots))f.slots[i]=typeof initial==='function'?initial():initial;return[f.slots[i],value=>{f.slots[i]=typeof value==='function'?value(f.slots[i]):value;}];},
 useRef(initial){const f=current,i=f.cursor++;if(!(i in f.slots))f.slots[i]={current:initial};return f.slots[i];},
 useEffect(effect,deps){const f=current,i=f.cursor++,old=f.slots[i];if(!old||!deps||deps.some((d,n)=>!Object.is(d,old.deps[n]))){f.slots[i]={deps};effects.push(()=>{old?.cleanup?.();f.slots[i].cleanup=effect();});}}};
 const api={fetchPersonas:async()=>personas,fetchPersonaFiles:async()=>({files:[{filename:'identity.md',content:'已保存的身份定位'},{filename:'platforms.md',content:'已保存的平台资料'}]}),savePersonaFile:async(...args)=>{writes.push(args);return{ok:true};},deletePersona:async()=>({ok:true}),fetchAccountAnalytics:async()=>{throw Error('UI rendering must not collect accounts');}};
 const jsx=(type,props,key)=>({type,props,key});const exports={};
 runInNewContext(code,{exports,Date,Error,Promise,setTimeout:()=>0,clearTimeout(){},window:{confirm:()=>true},require:name=>name==='react'?hooks:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:name==='../lib/api'?api:name==='./ProjectSpace'?{useIpPermissions:()=>({canOperate:role!=='viewer'})}:name==='../lib/sanitize'?{renderMarkdown:value=>value}:new Proxy({},{get:(_,key)=>String(key)})});
 const props={persona,onNewProfile:()=>events.push(['create']),onUsePersona:name=>events.push(['use',name]),onDeleted:name=>events.push(['delete',name])};
 function render(){effects=[];const result=[];function walk(node,path){if(!node||typeof node!=='object')return;if(typeof node.type==='function'){const id=path+':'+node.type.name+':'+(node.key||'');if(!fibers.has(id))fibers.set(id,{slots:[],cursor:0});current=fibers.get(id);current.cursor=0;walk(node.type(node.props),id);return;}result.push(node);[node.props?.children].flat(Infinity).forEach((child,index)=>walk(child,path+'/'+index));}walk({type:exports.default,props},'root');tree=result;const pending=effects;effects=[];pending.forEach(run=>run());return result;}
 return{render,events,writes,get nodes(){return tree;},async ready(){for(let n=0;n<4;n++){render();await settle();}return render();}};
}

test('the empty profile page provides one clear creation action',async()=>{
 const ui=view();const nodes=await ui.ready();const create=nodes.filter(n=>n.type==='button'&&text(n).includes('新建画像'));
 assert.equal(create.length,1,'The empty page must not duplicate its creation action');
 create[0].props.onClick();assert.deepEqual(ui.events,[['create']]);
});

test('the empty profile page explains the six dimensions without fabricated saved profiles',async()=>{
 const ui=view();const nodes=await ui.ready();const explanations=nodes.filter(n=>n.type==='article'&&n.props['aria-label']);
 assert.equal(explanations.length,6,'A compact guide should explain what a profile contains');
 assert.ok(!nodes.some(n=>n.props?.className?.includes('profile-summary')));
});

test('opening a saved profile does not switch the conversation identity',async()=>{
 const ui=view({personas:[{name:'教育画像',description:'教育内容与家庭沟通'}]});let nodes=await ui.ready();
 nodes.find(n=>n.type==='button'&&text(n)==='查看 / 编辑').props.onClick();await ui.ready();
 assert.deepEqual(ui.events,[]);assert.ok(ui.nodes.some(n=>n.props?.dangerouslySetInnerHTML?.__html==='已保存的身份定位'));
});

test('the refreshed detail editor still saves an intentionally emptied dimension',async()=>{
 const ui=view({persona:'教育画像'});let nodes=await ui.ready();
 nodes.find(n=>n.type==='button'&&text(n).includes('编辑资料')).props.onClick();nodes=ui.render();
 nodes.find(n=>n.type==='textarea').props.onChange({target:{value:''}});nodes=ui.render();
 await nodes.find(n=>n.type==='button'&&text(n)==='保存').props.onClick();
 assert.deepEqual(ui.writes,[['教育画像','identity.md','']]);
});
