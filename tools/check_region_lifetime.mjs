#!/usr/bin/env node
/* Dev-only executable reference. The portable C manager does not import TS. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, copyFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageRoot = resolve(root, ".dev/tla-precheck/node_modules/tla-precheck");
const packageMetadata = JSON.parse(await readFile(resolve(packageRoot, "package.json"), "utf8"));
assert.equal(packageMetadata.version, "0.1.7", "Install the documented tla-precheck@0.1.7 dependency");
const packageUrl = (relative) => pathToFileURL(resolve(packageRoot, relative)).href;
const ts = (await import(pathToFileURL(resolve(root, ".dev/tla-precheck/node_modules/typescript/lib/typescript.js")).href)).default;
const { resolveMachine } = await import(packageUrl("dist/core/proof.js"));
const { buildInitialState, enabled, step, exploreGraph, evaluateExpr, enumerateBindings, canonicalizeState } = await import(packageUrl("dist/core/interpreter.js"));
const modelPath = resolve(root, "docs/models/region-lifetime.machine.ts");
const source = await readFile(modelPath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText.replace('from "tla-precheck"', `from ${JSON.stringify(packageUrl("dist/core/dsl.js"))}`);
const modelFile = resolve(root, ".dev/tla-precheck/region-lifetime.machine.mjs");
await writeFile(modelFile, compiled);
const machine = resolveMachine((await import(pathToFileURL(modelFile).href)).default, "pr");
const graph = exploreGraph(machine);
let invariantChecks = 0;
for (const state of graph.states.values()) {
  for (const [name, invariant] of Object.entries(machine.invariants)) {
    assert.equal(evaluateExpr(machine, state, {}, invariant.formula), true, name);
    ++invariantChecks;
  }
  for (const [name, action] of Object.entries(machine.actions)) {
    for (const binding of enumerateBindings(machine, action)) {
      if (!enabled(machine, state, name, binding)) continue;
      const next = step(machine, state, name, binding);
      assert.ok(next);
      if (name === "borrow") assert.equal(state.phase[binding.s], "resident");
      if (name === "reserve") {
        for (const owner of ["cpu", "rsp", "rdp"]) assert.equal(state[owner][binding.s], false);
      }
      for (const slot of machine.domains.Slots) {
        if (state.cpu[slot] || state.rsp[slot] || state.rdp[slot]) {
          assert.equal(next.generation[slot], state.generation[slot], "generation changed while owned");
        }
      }
    }
  }
}

/* Representative lifetime reference; C tests exercise these same externally
 * observable phases, plus byte budgets and true monotonic serials. */
let state = buildInitialState(machine);
const run = (action, slot = "s1") => {
  assert.ok(enabled(machine, state, action, { s: slot }), action);
  state = step(machine, state, action, { s: slot });
};
for (const action of ["reserve", "startRead", "readComplete", "publish", "borrow", "submitRsp", "submitRdp", "retire", "release"]) run(action);
assert.equal(enabled(machine, state, "borrow", { s: "s1" }), false);
assert.equal(enabled(machine, state, "free", { s: "s1" }), false);
run("completeRsp");
assert.equal(enabled(machine, state, "free", { s: "s1" }), false);
run("completeRdp"); run("free"); run("reserve");

const report = {
  model: "docs/models/region-lifetime.machine.ts",
  source_sha256: createHash("sha256").update(source).digest("hex"),
  tool_version: packageMetadata.version,
  tier: "pr", slots: 2,
  estimated_states: machine.estimate.totalStateCount,
  interpreter_states: graph.states.size, interpreter_edges: graph.edges.length,
  invariant_checks: invariantChecks,
  interpreter_passed: true,
  c_equivalence_proven: false,
  limitations: [
    "Finite ownership abstraction: one CPU borrower, two generation values, boolean pending fences.",
    "Completion reports are trusted; platform DMA, RSP and RDP adapters are outside the model.",
    "C tests are conformance evidence, not automatically generated N64 semantics or a C equivalence proof.",
    "No liveness, memory-size, content-validation or hardware frame-budget proof.",
  ],
};
const output = resolve(root, "build/region-lifetime-proof");
await mkdir(output, { recursive: true });
if (process.argv.includes("--c")) {
  const executable = resolve(output, process.platform === "win32" ? "region_lifetime_driver.exe" : "region_lifetime_driver");
  const compile = spawnSync(process.env.CC ?? "gcc", [
    "-std=c17", "-Wall", "-Wextra", "-Werror", "-Isrc",
    "tests/region_lifetime_driver.c", "src/world_stream.c", "src/mission.c", "-o", executable,
  ], { cwd: root, encoding: "utf8" });
  if (compile.error) throw compile.error;
  assert.equal(compile.status, 0, compile.stdout + compile.stderr);

  /* Normalize the internal free transition because C immediately drains a
   * retiring slot when its final owner releases. Exhaustively cover every
   * enabled transition in the one-slot projection of the two-slot proof.
   * Slots are independent in this model; C pool allocation is tested elsewhere. */
  const normalize = (current) => enabled(machine, current, "free", { s: "s1" })
    ? step(machine, current, "free", { s: "s1" }) : current;
  const initial = buildInitialState(machine);
  const queue = [{ current: initial, trace: [] }];
  const seen = new Set([canonicalizeState(machine, initial)]);
  const expected = [];
  const input = [];
  let conformanceEdges = 0;
  const actions = Object.keys(machine.actions).filter((action) => action !== "free");
  for (let cursor = 0; cursor < queue.length; ++cursor) {
    const { current, trace } = queue[cursor];
    for (const action of actions) {
      if (!enabled(machine, current, action, { s: "s1" })) continue;
      const next = normalize(step(machine, current, action, { s: "s1" }));
      const key = canonicalizeState(machine, next);
      if (!seen.has(key)) { seen.add(key); queue.push({ current: next, trace: [...trace, action] }); }
      const operations = [...trace, action];
      input.push("reset 0");
      let reference = buildInitialState(machine);
      for (let operationIndex = 0; operationIndex < operations.length; ++operationIndex) {
        const operation = operations[operationIndex];
        if (operation === "readComplete" && operations[operationIndex + 1] === "abortValidate") input.push("corruptNextRead");
        input.push(operation);
        reference = normalize(step(machine, reference, operation, { s: "s1" }));
        expected.push({ operation, trace: operations, state: Object.fromEntries(
          Object.entries(reference).map(([name, value]) => [name, value.s1]),
        ) });
      }
      ++conformanceEdges;
    }
  }
  const driver = spawnSync(executable, [], {
    cwd: root, input: input.join("\n") + "\n", encoding: "utf8", maxBuffer: 4 * 1024 * 1024,
  });
  if (driver.error) throw driver.error;
  await writeFile(resolve(output, "c-traces.txt"), input.join("\n") + "\n");
  await writeFile(resolve(output, "c-output.txt"), driver.stdout + driver.stderr);
  const actual = driver.stdout.trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  assert.equal(driver.status, 0, `${driver.stderr}\nAt ${JSON.stringify(expected[actual.length])}`);
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < actual.length; ++index) {
    assert.deepEqual(actual[index], expected[index].state,
      `${expected[index].operation}: ${expected[index].trace.join(" -> ")}`);
  }
  report.c_projection_states = seen.size;
  report.c_conformance_edges = conformanceEdges;
  report.c_observed_steps = actual.length;
  report.c_conformance_passed = true;
}
if (process.argv.includes("--proof")) {
  const proof = spawnSync(process.execPath, [resolve(packageRoot, "bin/tla-precheck.js"), "check", modelFile, "--output-root", output], {
    cwd: root, env: process.env, encoding: "utf8", timeout: 150_000,
  });
  await writeFile(resolve(output, "cli-output.txt"), (proof.stdout ?? "") + (proof.stderr ?? ""));
  if (proof.status !== 0) {
    process.stdout.write(proof.stdout ?? "");
    process.stderr.write(proof.stderr ?? "");
  }
  if (proof.error) throw proof.error;
  assert.equal(proof.status, 0, "TLC check failed");
  const certificatePath = resolve(output, "RegionLifetime", "pr", "RegionLifetime.pr.certificate.json");
  const certificate = JSON.parse(await readFile(certificatePath, "utf8"));
  assert.equal(certificate.proofPassed, true);
  assert.equal(certificate.equivalent, true);
  report.tlc_proof_passed = true;
  report.interpreter_tla_graph_equivalent = true;
  report.machine_sha256 = certificate.machineSha256;
  await copyFile(certificatePath, resolve(root, "docs/evidence/region-lifetime.pr.certificate.json"));
}
await writeFile(resolve(output, "reference-report.json"), JSON.stringify(report, null, 2) + "\n");
await writeFile(resolve(root, "docs/evidence/region-lifetime-reference.json"), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report, null, 2));
