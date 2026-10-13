#!/usr/bin/env node
/* Dev-only finite safety reference; the ROM executes portable C. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, copyFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const packageRoot=resolve(root,".dev/tla-precheck/node_modules/tla-precheck");
const metadata=JSON.parse(await readFile(resolve(packageRoot,"package.json"),"utf8"));
assert.equal(metadata.version,"0.1.7","Install documented tla-precheck@0.1.7");
const packageUrl=(file)=>pathToFileURL(resolve(packageRoot,file)).href;
const ts=(await import(pathToFileURL(resolve(root,".dev/tla-precheck/node_modules/typescript/lib/typescript.js")).href)).default;
const {resolveMachine}=await import(packageUrl("dist/core/proof.js"));
const {exploreGraph,evaluateExpr,enumerateBindings,enabled,step}=await import(packageUrl("dist/core/interpreter.js"));
const source=await readFile(resolve(root,"docs/models/stream-coordinator.machine.ts"),"utf8");
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}})
  .outputText.replace('from "tla-precheck"',`from ${JSON.stringify(packageUrl("dist/core/dsl.js"))}`);
const modelFile=resolve(root,".dev/tla-precheck/stream-coordinator.machine.mjs");
await writeFile(modelFile,compiled);
const machine=resolveMachine((await import(pathToFileURL(modelFile).href)).default,"pr");
const graph=exploreGraph(machine);
let invariantChecks=0,publishChecks=0,cancelChecks=0;
for(const state of graph.states.values()){
  for(const [name,invariant] of Object.entries(machine.invariants)){
    assert.equal(evaluateExpr(machine,state,{},invariant.formula),true,name);++invariantChecks;
  }
  for(const [name,action] of Object.entries(machine.actions))for(const binding of enumerateBindings(machine,action)){
    if(!enabled(machine,state,name,binding))continue;
    const next=step(machine,state,name,binding);
    if(name==="publish"){
      assert.equal(state.phase,"gpuReady");assert.equal(state.candidateDemand,state.demand);
      assert.equal(state.candidateGuards,state.guards);assert.equal(state.candidateEpoch,state.epoch);
      assert.equal(state.cpuPins,false);assert.equal(next.activeBank,state.candidateBank);++publishChecks;
    }
    if(name==="cancel"||name==="reset"){
      assert.equal(next.activeBank,state.activeBank);assert.equal(next.publishedDemand,state.publishedDemand);
      assert.equal(next.cpuPins,false);assert.equal(next.phase,"idle");++cancelChecks;
    }
    if(name!=="publish")assert.equal(next.activeBank,state.activeBank,"unpublished work changed active bank");
  }
}
const output=resolve(root,"build/stream-coordinator-proof");await mkdir(output,{recursive:true});
const report={model:"docs/models/stream-coordinator.machine.ts",source_sha256:createHash("sha256").update(source).digest("hex"),
  tool_version:metadata.version,tier:"pr",interpreter_states:graph.states.size,interpreter_edges:graph.edges.length,
  invariant_checks:invariantChecks,publish_checks:publishChecks,cancel_checks:cancelChecks,interpreter_passed:true,
  c_equivalence_proven:false,limitations:["Two abstract demand and guard membership values, two bank IDs and a cancellation-safe job epoch.",
    "Platform callbacks, DMA/fences, content/CRC, wall clock budgets and liveness are outside this model.",
    "The N64 and native implementations use handwritten C; C synthetic tests supply separate conformance evidence."]};
if(process.argv.includes("--proof")){
  const proof=spawnSync(process.execPath,[resolve(packageRoot,"bin/tla-precheck.js"),"check",modelFile,"--output-root",output],
    {cwd:root,env:process.env,encoding:"utf8",timeout:150000});
  await writeFile(resolve(output,"cli-output.txt"),(proof.stdout??"")+(proof.stderr??""));
  if(proof.error)throw proof.error;
  assert.equal(proof.status,0,(proof.stdout??"")+(proof.stderr??""));
  const path=resolve(output,"StreamCoordinator/pr/StreamCoordinator.pr.certificate.json");
  const certificate=JSON.parse(await readFile(path,"utf8"));assert.equal(certificate.proofPassed,true);assert.equal(certificate.equivalent,true);
  report.tlc_proof_passed=true;report.interpreter_tla_graph_equivalent=true;report.machine_sha256=certificate.machineSha256;
  await copyFile(path,resolve(root,"docs/evidence/stream-coordinator.pr.certificate.json"));
}
await writeFile(resolve(output,"reference-report.json"),JSON.stringify(report,null,2)+"\n");
await writeFile(resolve(root,"docs/evidence/stream-coordinator-reference.json"),JSON.stringify(report,null,2)+"\n");
console.log(JSON.stringify(report,null,2));
