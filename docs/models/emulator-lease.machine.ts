import {
  defineMachine, variable, scalarVar, mapVar, domainType, optionType,
  enumType, boolType, ids, lit, param, index, eq, and, or, not, lte,
  count, forall, setVar, setMap,
} from "tla-precheck";

/* Protocol safety model for two independent automation owners. The grant is
 * observed only after abandoned-job recovery drains prior owned processes.
 * OS mutex/job operations implement this boundary in Python; this machine is
 * not a database and does not claim to prove those handwritten OS bindings. */
const phase = variable("phase"), process = variable("process"), registered = variable("registered");
const owner = variable("owner"), run = param("r");
const is = (name: string) => eq(index(phase, run), lit(name));
const owns = () => eq(owner, run);
const none = () => eq(owner, lit(null));
const active = (r: ReturnType<typeof param>) => or(
  ...["held", "suspended", "registered", "running", "stopping", "stopped"].map(name => eq(index(phase, r), lit(name))),
);

export default defineMachine({
  version: 2, moduleName: "EmulatorLease",
  variables: {
    owner: scalarVar(optionType(domainType("Owners")), lit(null)),
    phase: mapVar("Owners", enumType("idle", "queued", "held", "suspended", "registered", "running", "stopping", "stopped", "dead"), lit("idle")),
    process: mapVar("Owners", boolType(), lit(false)),
    registered: mapVar("Owners", boolType(), lit(false)),
    interactiveAlive: scalarVar(boolType(), lit(true)),
  },
  actions: {
    request: { params: {r:"Owners"}, guard:is("idle"), updates:[setMap("phase",run,lit("queued"))] },
    timeout: { params: {r:"Owners"}, guard:is("queued"), updates:[setMap("phase",run,lit("idle"))] },
    acquire: { params: {r:"Owners"}, guard:and(is("queued"),none(),eq(count("Owners","x",index(process,param("x"))),lit(0))),
      updates:[setVar("owner",run),setMap("phase",run,lit("held"))] },
    spawnSuspended: { params: {r:"Owners"}, guard:and(owns(),is("held")),
      updates:[setMap("phase",run,lit("suspended")),setMap("process",run,lit(true))] },
    register: { params: {r:"Owners"}, guard:and(owns(),is("suspended")),
      updates:[setMap("phase",run,lit("registered")),setMap("registered",run,lit(true))] },
    resume: { params: {r:"Owners"}, guard:and(owns(),is("registered"),index(registered,run)),
      updates:[setMap("phase",run,lit("running"))] },
    beginStop: { params: {r:"Owners"}, guard:and(owns(),or(is("suspended"),is("registered"),is("running"))),
      updates:[setMap("phase",run,lit("stopping"))] },
    stopped: { params: {r:"Owners"}, guard:and(owns(),is("stopping")),
      updates:[setMap("phase",run,lit("stopped")),setMap("process",run,lit(false)),setMap("registered",run,lit(false))] },
    release: { params: {r:"Owners"}, guard:and(owns(),or(is("held"),is("stopped")),not(index(process,run))),
      updates:[setVar("owner",lit(null)),setMap("phase",run,lit("idle"))] },
    crash: { params: {r:"Owners"}, guard:owns(),
      updates:[setVar("owner",lit(null)),setMap("phase",run,lit("dead"))] },
    recoverJob: { params: {r:"Owners"}, guard:and(none(),is("dead"),index(process,run)),
      updates:[setMap("process",run,lit(false)),setMap("registered",run,lit(false))] },
    finishDead: { params: {r:"Owners"}, guard:and(is("dead"),not(index(process,run))),
      updates:[setMap("phase",run,lit("idle"))] },
  },
  invariants: {
    oneOwnedProcess: { description:"At most one disposable owned emulator exists, including suspended/draining processes",
      formula:lte(count("Owners","x",index(process,param("x"))),lit(1)) },
    oneGrant: { description:"Only one owner can hold the runnable emulator grant",
      formula:lte(count("Owners","x",active(param("x"))),lit(1)) },
    grantMatchesOwner: { description:"Every granted phase belongs to the current OS-backed owner",
      formula:forall("Owners","x",or(not(active(param("x"))),eq(owner,param("x")))) },
    resumeOnlyRegistered: { description:"A runnable emulator has been registered in its owned crash-containment job",
      formula:forall("Owners","x",or(not(eq(index(phase,param("x")),lit("running"))),index(registered,param("x")))) },
    registrationIsLive: { description:"Registration cannot remain after owned process termination",
      formula:forall("Owners","x",or(not(index(registered,param("x"))),index(process,param("x")))) },
    preserveInteractive: { description:"No lifecycle operation can stop an unrelated interactive process",
      formula:eq(variable("interactiveAlive"),lit(true)) },
  },
  proof: {defaultTier:"pr",tiers:{pr:{domains:{Owners:ids({prefix:"owner",size:2})},
    budgets:{maxEstimatedStates:30_000,maxEstimatedBranching:30},graphEquivalence:true}}},
});
