import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const ts=require('typescript');
const source=ts.createSourceFile('App.tsx',readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=source.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const declaration=app.body.statements.find(node=>ts.isVariableStatement(node)&&node.declarationList.declarations.some(item=>item.name.getText(source)==='renderPage'));
function page(currentPage){
  const code=ts.transpileModule(declaration.getText(source)+'\nrenderPage();',{compilerOptions:{target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}}).outputText;
  return runInNewContext(code,{exports:{},currentPage,canOperate:true,isPlatformAdmin:true,selectedPersona:'',interactionEntry:0,setCurrentPage(){},
    AccountsPage:'accounts',PublishPage:'publish',publicationHandoff:null,consumePublicationHandoff(){},InteractionCenterPage:'interactions',Suspense:'suspense',OperationsPage:'broad-workbench',
    require:()=>({jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})})});
}
test('account management renders its own account page',()=>assert.equal(page('accounts').type,'accounts'));
test('publishing renders its own editor',()=>assert.equal(page('publish').type,'publish'));
test('interaction navigation opens a native inbox directly',()=>{
  const result=page('interactions');
  assert.equal(result.type,'suspense');
  assert.equal(result.props.children.type,'interactions');
});
