import { loadPyodide } from './vendor/pyodide/pyodide.mjs';
let py;
try {
 py = await loadPyodide({indexURL: new URL('./vendor/pyodide/', import.meta.url).href});
 self.postMessage({type:'ready'});
} catch(e) { self.postMessage({type:'boot-error',error:String(e)}); }
self.onmessage = async ({data}) => {
 if(!py) return;
 const {code,expected,token}=data;
 let output='';
 py.setStdout({batched: line => {output+=line+'\n'; if(output.length>24000)throw Error('Output limit exceeded');}});
 py.setStderr({batched: line => {output+=line+'\n'; if(output.length>24000)throw Error('Output limit exceeded');}});
 const ns=py.toPy({});
 try {
  // New namespace on every run; virtual files only. No Python executes on the host.
  await py.runPythonAsync(code,{globals:ns});
  let hasConcept=true;
  if(token && token!=='print') {
   const check=py.toPy({source:code,concept:token});
   try {hasConcept=py.runPython(`import ast\nimport tokenize\nimport io\ntree = ast.parse(source)\nany(t.string == concept for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.NAME)`,{globals:check});}
   finally{check.destroy();}
  }
  self.postMessage({type:'result',output:output.replace(/\n$/,''),passed:expected!==undefined && output.replace(/\n+$/,'')===expected.replace(/\n+$/,'') && hasConcept,hasConcept});
 } catch(e) {
  self.postMessage({type:'result',output,error:String(e),passed:false});
 } finally {ns.destroy();}
};
