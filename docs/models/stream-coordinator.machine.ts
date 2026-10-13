import {
  defineMachine, variable, scalarVar, enumType, boolType,
  lit, eq, and, or, not, setVar,
} from "tla-precheck";

/* A finite safety reference for the coordinator's two scene banks. The two
 * demand values abstract masks; one Boolean abstracts each membership snapshot
 * and job epoch. Cancellation always runs before that epoch is reused. This is
 * not a proof of handwritten C, byte decoding or platform graphics callbacks. */
const value = (name: string) => variable(name);
const phase = (name: string) => eq(value("phase"), lit(name));
const update = (name: string, expression: ReturnType<typeof variable>) => setVar(name, expression);
const candidate = () => not(phase("idle"));
const sameSnapshot = () => and(
  eq(value("candidateDemand"), value("demand")),
  eq(value("candidateGuards"), value("guards")),
  eq(value("candidateEpoch"), value("epoch")),
);

export default defineMachine({
  version: 2,
  moduleName: "StreamCoordinator",
  variables: {
    phase: scalarVar(enumType("idle", "assembling", "cpuReady", "gpuReady"), lit("idle")),
    demand: scalarVar(boolType(), lit(false)),
    residentDemand: scalarVar(boolType(), lit(false)),
    guards: scalarVar(boolType(), lit(false)),
    candidateDemand: scalarVar(boolType(), lit(false)),
    candidateGuards: scalarVar(boolType(), lit(false)),
    epoch: scalarVar(boolType(), lit(false)),
    candidateEpoch: scalarVar(boolType(), lit(false)),
    activeBank: scalarVar(boolType(), lit(false)),
    candidateBank: scalarVar(boolType(), lit(true)),
    cpuPins: scalarVar(boolType(), lit(false)),
    publishedDemand: scalarVar(boolType(), lit(false)),
    queuedUse: scalarVar(boolType(), lit(false)),
  },
  actions: {
    changeDemand: {
      params: {}, guard: lit(true),
      updates: [update("demand", not(value("demand")))],
    },
    readComplete: {
      params: {}, guard: not(eq(value("residentDemand"), value("demand"))),
      updates: [update("residentDemand", value("demand"))],
    },
    moveGuard: {
      params: {}, guard: lit(true), updates: [update("guards", not(value("guards")))],
    },
    begin: {
      params: {}, guard: and(phase("idle"), eq(value("residentDemand"), value("demand"))),
      updates: [update("phase", lit("assembling")), update("candidateDemand", value("demand")),
        update("candidateGuards", value("guards")), update("epoch", not(value("epoch"))),
        update("candidateEpoch", not(value("epoch"))), update("candidateBank", not(value("activeBank"))),
        update("cpuPins", lit(true))],
    },
    assemblyComplete: {
      params: {}, guard: phase("assembling"),
      updates: [update("phase", lit("cpuReady")), update("cpuPins", lit(false))],
    },
    rendererComplete: {
      params: {}, guard: and(phase("cpuReady"), sameSnapshot()),
      updates: [update("phase", lit("gpuReady"))],
    },
    publish: {
      params: {}, guard: and(phase("gpuReady"), sameSnapshot(), not(value("cpuPins"))),
      updates: [update("activeBank", value("candidateBank")), update("publishedDemand", value("candidateDemand")),
        update("phase", lit("idle"))],
    },
    cancel: {
      params: {}, guard: candidate(),
      updates: [update("phase", lit("idle")), update("cpuPins", lit(false))],
    },
    queueUse: {
      params: {}, guard: and(not(value("queuedUse")), not(eq(value("publishedDemand"), value("demand")))),
      updates: [update("queuedUse", lit(true))],
    },
    deliverUse: {
      params: {}, guard: and(value("queuedUse"), eq(value("publishedDemand"), value("demand")), phase("idle")),
      updates: [update("queuedUse", lit(false))],
    },
    reset: {
      params: {}, guard: lit(true),
      updates: [update("phase", lit("idle")), update("cpuPins", lit(false)), update("queuedUse", lit(false)),
        update("demand", lit(false))],
    },
  },
  invariants: {
    assemblyOwnsPins: {
      description: "Generation pins exist exactly while incremental assembly is active",
      formula: or(and(value("cpuPins"), phase("assembling")), and(not(value("cpuPins")), not(phase("assembling")))),
    },
    pendingBankIsInactive: {
      description: "An unpublished candidate cannot overwrite the published bank",
      formula: or(not(candidate()), not(eq(value("activeBank"), value("candidateBank")))),
    },
    candidateHasCurrentEpoch: {
      description: "Every live candidate belongs to the current nonreused job epoch",
      formula: or(not(candidate()), eq(value("epoch"), value("candidateEpoch"))),
    },
  },
  proof: {
    defaultTier: "pr",
    tiers: { pr: { domains: {}, budgets: { maxEstimatedStates: 40_000, maxEstimatedBranching: 30 }, graphEquivalence: true } },
  },
});
