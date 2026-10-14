#!/usr/bin/env node
/* Execute the DSL interpreter, then optionally TLC, without editing generated code. */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile,writeFile,mkdir,copyFile} from "node:fs/promises";
import {resolve,dirname} from "node:path";
import {fileURLToPath,pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";
const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const pkg=resolve(root,".dev/tla-precheck/node_modules/tla-precheck");
const packageUrl=file=>pathToFileURL(resolve(pkg,file)).href;
const metadata=JSON.parse(await readFile(resolve(pkg,"package.json"),"utf8"));
assert.equal(metadata.version,"0.1.7");
const ts=(await import(pathToFileURL(resolve(root,".dev/tla-precheck/node_modules/typescript/lib/typescript.js")).href)).default;
const {resolveMachine}=await import(packageUrl("dist/core/proof.js"));
const {exploreGraph,evaluateExpr,enumerateBindings,enabled,step,buildInitialState}=await import(packageUrl("dist/core/interpreter.js"));
const source=await readFile(resolve(root,"docs/models/emulator-lease.machine.ts"),"utf8");
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
  .replace('from "tla-precheck"',`from ${JSON.stringify(packageUrl("dist/core/dsl.js"))}`);
const model=resolve(root,".dev/tla-precheck/emulator-lease.machine.mjs");await writeFile(model,compiled);
const machine=resolveMachine((await import(pathToFileURL(model).href)).default,"pr");
const graph=exploreGraph(machine);let invariantChecks=0,releaseChecks=0,recoveryChecks=0;
for(const state of graph.states.values()){
  for(const [name,invariant] of Object.entries(machine.invariants)){
    assert.equal(evaluateExpr(machine,state,{},invariant.formula),true,name);++invariantChecks;
  }
  for(const [name,action] of Object.entries(machine.actions))for(const binding of enumerateBindings(machine,action)){
    if(!enabled(machine,state,name,binding))continue;
    const next=step(machine,state,name,binding);assert.equal(next.interactiveAlive,true);
    if(name==="release"){assert.equal(state.process[binding.r],false);assert.equal(next.owner,null);++releaseChecks;}
    if(name==="recoverJob"){assert.equal(state.phase[binding.r],"dead");assert.equal(next.process[binding.r],false);++recoveryChecks;}
    for(const owner of Object.keys(state.process))if(owner!==binding.r)assert.equal(next.process[owner],state.process[owner]);
  }
}
// Interpreter executes the observed lease state trace, rather than merely
// validating an independently hand-implemented transition table. Event logs
// are optional conformance evidence; they do not prove the Python/Win32 code.
const traceIndex=process.argv.indexOf("--trace");let traceSteps=0;
if(traceIndex>=0){
  const run=JSON.parse(await readFile(resolve(process.argv[traceIndex+1]),"utf8"));let state=buildInitialState(machine);
  const actions={queued:["request"],acquired:["acquire"],registered:["spawnSuspended","register"],
    running:["resume"],stopping:["beginStop"],stopped:["stopped"],released:["release"],acquisition_failed:["timeout"]};
  for(const event of run.lease_events){
    assert.ok(actions[event.state],`Unknown lease event ${event.state}`);
    for(const action of actions[event.state]){assert.ok(enabled(machine,state,action,{r:"owner1"}),action);
      state=step(machine,state,action,{r:"owner1"});++traceSteps;}
  }
}
const output=resolve(root,"build/emulator-lease-proof");await mkdir(output,{recursive:true});
const report={model:"docs/models/emulator-lease.machine.ts",source_sha256:createHash("sha256").update(source).digest("hex"),
  tool_version:metadata.version,tier:"pr",interpreter_states:graph.states.size,interpreter_edges:graph.edges.length,
  invariant_checks:invariantChecks,release_checks:releaseChecks,recovery_checks:recoveryChecks,
  interpreter_passed:true,trace_interpreter_steps:traceSteps,python_os_equivalence_proven:false,
  limitations:["Two abstract owners and one owned child per owner; finite safety only, no scheduling fairness or wall-clock proof.",
    "Windows mutex/job binding, process identity, thread enumeration and abrupt-crash ordering require separate OS refinement tests.",
    "POSIX flock releases on owner death, but its child crash containment is weaker than the supported Windows job backend.",
    "No database-owned state or generated SQL adapter is applicable; OS observations drive retained run status."]};
if(process.argv.includes("--proof")){
  const proof=spawnSync(process.execPath,[resolve(pkg,"bin/tla-precheck.js"),"check",model,"--output-root",output],
    {cwd:root,env:process.env,encoding:"utf8",timeout:150000});
  await writeFile(resolve(output,"cli-output.txt"),(proof.stdout??"")+(proof.stderr??""));
  if(proof.error)throw proof.error;assert.equal(proof.status,0,(proof.stdout??"")+(proof.stderr??""));
  const certificatePath=resolve(output,"EmulatorLease/pr/EmulatorLease.pr.certificate.json");
  const certificate=JSON.parse(await readFile(certificatePath,"utf8"));assert.equal(certificate.proofPassed,true);assert.equal(certificate.equivalent,true);
  report.tlc_proof_passed=true;report.interpreter_tla_graph_equivalent=true;report.machine_sha256=certificate.machineSha256;
  await copyFile(certificatePath,resolve(root,"docs/evidence/emulator-lease.pr.certificate.json"));
}
if(!process.argv.includes("--trace-only")){
  await writeFile(resolve(output,"reference-report.json"),JSON.stringify(report,null,2)+"\n");
  await writeFile(resolve(root,"docs/evidence/emulator-lease-reference.json"),JSON.stringify(report,null,2)+"\n");
}
console.log(JSON.stringify(report,null,2));
