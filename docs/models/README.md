# Region lifetime verification

`region-lifetime.machine.ts` is a finite executable reference for the ownership
protocol in `src/world_stream.c`. The reference runs through the TLA PreCheck
interpreter on the host. The N64 implementation is handwritten C17; it does not
embed that interpreter or a generated database adapter.

The proof has two slots, seven lifecycle phases, one CPU borrower per slot, two
generation values, and independent RSP/RDP pending flags. These abstract a
positive pin count, nonwrapping generation counters, and submission serials
greater than their reported completion. Completion reports are trusted platform
inputs. The model does not prove that a platform adapter waits for actual DMA,
RSP or RDP completion.

The invariants prohibit a free buffer with owners, prohibit ownership before
publication, and prohibit changing generation while CPU or graphics ownership
remains. The host checker also verifies that every borrow starts in `RESIDENT`.
Existing CPU borrowers can finish and submit after retirement starts.

Install the pinned development dependency into the ignored `.dev` directory.
This does not add a root `package.json` or change the ROM build:

```powershell
npm install --prefix .dev/tla-precheck --cache .dev/tla-precheck/npm-cache --no-audit --no-fund tla-precheck@0.1.7
```

Node 18 or newer runs the checker. The C trace check requires a C17 compiler
(`gcc` by default; set `CC` to an executable path to choose another). The TLC
proof additionally requires Java 17 or newer and `tla2tools.jar`. A previously
installed jar at `$HOME/.tla-precheck/tla2tools.jar` is discovered automatically;
set `TLA2TOOLS_JAR` for another location. On Windows, put the actual Java `bin`
directory on `PATH` if the Oracle launcher shim does not work.

The recorded run used the local TLC jar with SHA256
`a89d5ef05d1abddab6acfda1dbace14e2e45e7960527ac186dd19c170a955080`,
whose reported version is `2026.03.12.220102 (rev: 0a0bffb)`, and Java 21.0.10.
If no jar is installed, obtain it using the TLA PreCheck setup instructions and
record its hash; the model checker is a separate dependency from the npm package.

Run from the repository root:

```powershell
node tools/check_region_lifetime.mjs --c --proof
```

Without flags, the command explores the interpreter graph and checks its
invariants. `--proof` runs TLA PreCheck `check` against a transpiled copy of the
same DSL and saves its unmodified certificate. `--c` compiles
`tests/region_lifetime_driver.c` with the actual decoder and region manager.
It replays every enabled transition in the one-slot projection through both
the interpreter and C, comparing observable state after every action.

The reference exposes `free` as a separate internal action; the C comparison
normalizes that action because C immediately drains eligible retiring slots.
Read stalls and errors, validation rejection, cancellation, submission during
retirement and independent graphics completion occur in the generated traces.
The C projection uses one CPU borrower and does not check allocator policy,
multiple simultaneous borrows or the two-slot pool; ordinary C runtime tests
cover those cases separately.

The checked-in evidence records 3,136 reachable states and 17,024 edges with
matching TLC and interpreter graphs. The C projection records 52 states,
148 transitions and 1,250 compared steps. These are finite ownership results,
not automatic C equivalence, a liveness proof, a content-validity proof or N64
performance acceptance.

Generated TLA+, CLI logs and C traces remain under
`build/region-lifetime-proof/`. The unmodified certificate is
`docs/evidence/region-lifetime.pr.certificate.json`; the reference/conformance
summary is `docs/evidence/region-lifetime-reference.json`. Re-run verification
after changing the model or implementation. Never edit generated proof artifacts
to change a result.
