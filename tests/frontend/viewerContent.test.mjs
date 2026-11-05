import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));

function page(name, role = 'viewer', fixtures = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/components/${name}.tsx`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  let index = 0, effectIndex = 0, pending = [];
  const slots = [], effects = [], writes = [];
  const same = (a,b) => a && b && a.length === b.length && a.every((value,index)=>value===b[index]);
  const hooks = {
    useState(initial) { const id=index++;if (!(id in slots))slots[id]=typeof initial==='function'?initial():initial;
      return [slots[id],value=>{slots[id]=typeof value==='function'?value(slots[id]):value;}]; },
    useRef(initial) { const id=index++;if (!(id in slots))slots[id]={current:initial};return slots[id]; },
    useCallback(fn,deps) {const id=index++;if(!slots[id]||!same(slots[id].deps,deps))slots[id]={fn,deps};return slots[id].fn;},
    useMemo(fn) {return fn();},
    useEffect(fn,deps) {const id=effectIndex++;const old=effects[id];if(!old||!same(old.deps,deps))pending.push(()=>{old?.cleanup?.();effects[id]={deps,cleanup:fn()};});},
  };
  const jsx=(type,props)=>({type,props});
  const api={fetchSchedule:async()=>fixtures.schedule||[],fetchScheduleContext:async()=>({suggestions:[]}),
    fetchOutputs:async()=>fixtures.outputs||[],fetchOutputContent:async()=>({content:'read-only text',isBinary:false}),mediaUrl:path=>`/api/media/${path}`};
  for(const method of ['createSchedule','updateSchedule','deleteSchedule','deleteOutput']) {
    api[method]=async(...args)=>{writes.push({method,args});return {};};
  }
  const module={};
  const permissions={role,canOperate:role!=='viewer',canManageAccounts:role==='owner',isPlatformAdmin:false};
  runInNewContext(compiled,{exports:module,Date,Map,Set,Promise,setTimeout:()=>1,clearTimeout(){},alert(){},window:{confirm:()=>true},
    require:name=>name==='react'?hooks:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:name.includes('ProjectSpace')?{useIpPermissions:()=>permissions}
      :name.includes('/api')?api:name.includes('sanitize')?{renderMarkdown:content=>content}:new Proxy({},{get:(_,key)=>key}),
  });
  const flatten=tree=>{const result=[];const walk=node=>{if(!node||typeof node!=='object')return;result.push(node);[node.props?.children].flat(Infinity).forEach(walk);};walk(tree);return result;};
  const render=(props={})=>{index=0;effectIndex=0;const tree=module.default(props);const tasks=pending;pending=[];tasks.forEach(task=>task());return flatten(tree);};
  return {render,writes,permissions};
}
const text=node=>typeof node==='string'?node:[node?.props?.children].flat(Infinity).map(child=>typeof child==='string'?child:child?text(child):'').join('');

test('viewer calendar opens existing item details and an empty day never opens an editor', async()=>{
  const now=new Date();const date=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  const ui=page('CalendarPage','viewer',{schedule:[{id:'schedule',date,title:'已安排内容',status:'scheduled',kind:'content',note:'查看此备注',platform:'抖音'}]});
  ui.render();await settle();let nodes=ui.render();
  const empty=nodes.find(node=>node.props?.className?.startsWith('cal-cell')&&!text(node).includes('已安排内容'));
  empty.props.onClick();nodes=ui.render();
  assert.ok(!nodes.some(node=>node.type==='input'||node.type==='textarea'));
  nodes.find(node=>node.props?.className==='cal-event').props.onClick({stopPropagation(){}});
  nodes=ui.render();
  assert.ok(nodes.some(node=>node.props?.role==='dialog'));
  assert.ok(nodes.some(node=>text(node).includes('查看此备注')));
  assert.ok(!nodes.some(node=>node.type==='button'&&/新增|保存|删除/.test(text(node))));
  assert.equal(ui.writes.length,0);
});

test('viewer output preview keeps media readable while disabling HTML scripts and deletion', async()=>{
  const ui=page('OutputsPage','viewer',{outputs:[{name:'成品.html',path:'内容/成品.html',type:'file',kind:'text',mtime:1}]});
  ui.render();await settle();let nodes=ui.render();
  assert.ok(!nodes.some(node=>node.type==='button'&&node.props.title==='删除'));
  nodes.find(node=>node.props?.className==='card card-hover gcard').props.onClick();
  nodes=ui.render();const iframe=nodes.find(node=>node.type==='iframe');
  assert.ok(iframe);assert.equal(iframe.props.sandbox,'');assert.equal(iframe.props.allow,undefined);
  assert.ok(!nodes.some(node=>node.type==='button'&&text(node).includes('删除')));
  assert.equal(ui.writes.length,0);
});

test('operator output library retains deletion and its guarded confirmation path', async()=>{
  const ui=page('OutputsPage','operator',{outputs:[{name:'成品.png',path:'内容/成品.png',type:'file',kind:'image'}]});
  ui.render();await settle();const nodes=ui.render();
  await nodes.find(node=>node.type==='button'&&node.props.title==='删除').props.onClick({stopPropagation(){}});
  assert.equal(ui.writes[0].method,'deleteOutput');assert.equal(ui.writes[0].args[0],'内容/成品.png');
});

test('viewer mutation handlers reject direct invocation even when writable form data already exists', async()=>{
  const writes=[];
  const base={canOperate:false,form:{title:'stale form',date:'2026-11-02'},editing:{id:'saved'},editId:'saved',
    EMPTY:{},NEXT:{pending:'doing'},filter:'all',load(){},setSaving(){},setForm(){},setEditing(){},setEditId(){},setDayView(){},setViewing(){},showToast(){},
    useCallback:fn=>fn,window:{confirm(){writes.push('confirmation');return true;}},
  };
  for(const name of ['createSchedule','updateSchedule','deleteSchedule','deleteOutput'])base[name]=async()=>{writes.push(name);};
  for(const [file,names] of [['CalendarPage',['save','remove']],['OutputsPage',['remove']]]) {
    const source=readFileSync(new URL(`../../web/frontend/src/components/${file}.tsx`,import.meta.url),'utf8');
    const parsed=ts.createSourceFile(`${file}.tsx`,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    const declarations=new Map();
    const visit=node=>{if(ts.isVariableDeclaration(node)&&names.includes(node.name.getText(parsed)))declarations.set(node.name.getText(parsed),node.getText(parsed));ts.forEachChild(node,visit);};visit(parsed);
    const code=names.map(name=>`const ${declarations.get(name)}; exports.${name}=${name};`).join('\n');
    const output=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
    const actions={};runInNewContext(output,{...base,exports:actions});
    for(const name of names)await actions[name]({id:'saved',title:'stale',status:'pending',note:''},{stopPropagation(){}});
  }
  assert.deepEqual(writes,[]);
});
