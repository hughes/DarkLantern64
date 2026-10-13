import {
  defineMachine, variable, mapVar, enumType, boolType,
  lit, param, index, eq, and, or, not, forall, setMap, ids,
} from "tla-precheck";

/* Finite reference for world_stream.c, not the N64 executable implementation.
 * Two interchangeable generation values abstract monotonic nonwrapping handles.
 * One CPU borrower abstracts cpu_pins > 0; RSP/RDP booleans abstract outstanding
 * last_submission > completed_serial. Completion is a trusted platform event.
 * Work counters, CRC/record correctness, IO deadlines and liveness are outside
 * this ownership model. C drains retiring slots immediately when eligible;
 * free below exposes that drain as a separate internal transition. */
const slot = param("s");
const at = (name: string) => index(variable(name), slot);
const phase = (name: string) => eq(at("phase"), lit(name));
const owned = () => or(at("cpu"), at("rsp"), at("rdp"));
const mutate = (name: string, value: ReturnType<typeof lit>) => setMap(name, slot, value);
const transition = (from: string, to: string) => ({
  params: { s: "Slots" }, guard: phase(from),
  updates: [mutate("phase", lit(to))],
});

export default defineMachine({
  version: 2,
  moduleName: "RegionLifetime",
  variables: {
    phase: mapVar("Slots", enumType("free", "requested", "reading", "validating", "resident", "retiring", "failed"), lit("free")),
    generation: mapVar("Slots", boolType(), lit(false)),
    ownerGeneration: mapVar("Slots", boolType(), lit(false)),
    cpu: mapVar("Slots", boolType(), lit(false)),
    rsp: mapVar("Slots", boolType(), lit(false)),
    rdp: mapVar("Slots", boolType(), lit(false)),
  },
  actions: {
    reserve: {
      params: { s: "Slots" }, guard: and(phase("free"), not(owned())),
      updates: [mutate("phase", lit("requested")), mutate("generation", not(at("generation")))],
    },
    startRead: transition("requested", "reading"),
    readComplete: transition("reading", "validating"),
    publish: transition("validating", "resident"),
    abortRead: transition("reading", "failed"),
    abortValidate: transition("validating", "failed"),
    cancelUnpublished: {
      params: { s: "Slots" },
      guard: and(or(phase("requested"), phase("reading"), phase("validating"), phase("failed")), not(owned())),
      updates: [mutate("phase", lit("free"))],
    },
    borrow: {
      params: { s: "Slots" }, guard: and(phase("resident"), not(at("cpu"))),
      updates: [mutate("cpu", lit(true)), mutate("ownerGeneration", at("generation"))],
    },
    release: {
      params: { s: "Slots" }, guard: and(or(phase("resident"), phase("retiring")), at("cpu")),
      updates: [mutate("cpu", lit(false))],
    },
    submitRsp: {
      params: { s: "Slots" }, guard: and(or(phase("resident"), phase("retiring")), at("cpu")),
      updates: [mutate("rsp", lit(true))],
    },
    submitRdp: {
      params: { s: "Slots" }, guard: and(or(phase("resident"), phase("retiring")), at("cpu")),
      updates: [mutate("rdp", lit(true))],
    },
    completeRsp: {
      params: { s: "Slots" }, guard: at("rsp"), updates: [mutate("rsp", lit(false))],
    },
    completeRdp: {
      params: { s: "Slots" }, guard: at("rdp"), updates: [mutate("rdp", lit(false))],
    },
    retire: transition("resident", "retiring"),
    free: {
      params: { s: "Slots" }, guard: and(phase("retiring"), not(owned())),
      updates: [mutate("phase", lit("free"))],
    },
  },
  invariants: {
    noFreeWhileOwned: {
      description: "A free buffer has no CPU, RSP or RDP owner",
      formula: forall("Slots", "s", or(not(phase("free")), not(owned()))),
    },
    ownershipRequiresPublication: {
      description: "Only published or retiring buffers may have owners",
      formula: forall("Slots", "s", or(not(owned()), phase("resident"), phase("retiring"))),
    },
    noGenerationReuseWhileReferenced: {
      description: "CPU and submitted graphics ownership retain the same generation",
      formula: forall("Slots", "s", or(not(owned()), eq(at("generation"), at("ownerGeneration")))),
    },
  },
  proof: {
    defaultTier: "pr",
    tiers: {
      pr: {
        domains: { Slots: ids({ prefix: "s", size: 2 }) },
        budgets: { maxEstimatedStates: 60_000, maxEstimatedBranching: 100 },
        graphEquivalence: true,
      },
    },
  },
});
