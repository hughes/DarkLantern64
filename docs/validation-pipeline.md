# Project acceptance pipeline

`python tools/validate_project.py` runs the tracked
[acceptance manifest](../tools/acceptance_manifest.json) against one captured
input set. Its JSON result is the acceptance decision; an old ROM, a partial
log, or a successful capture cannot stand in for a missing required check.

```powershell
# Portable tests, real editor/Native checks and all declared ROM scenarios
python tools/validate_project.py --host-audio-none --output build/project-acceptance.json

# Also retain the optional diagnostics-disabled counterfactual
python tools/validate_project.py --host-audio-none --include-optional --output build/project-comparison.json

# Useful partial runs; skipped required nodes remain visible and unaccepted
python tools/validate_project.py --profile portable --output build/portable-acceptance.json
python tools/validate_project.py --profile host --output build/host-acceptance.json
python tools/validate_project.py --skip-editor --skip-rom --output build/partial-acceptance.json

# Use another reviewed, tracked scenario declaration
python tools/validate_project.py --manifest tools/acceptance_manifest.json --output build/project-acceptance.json
```

The all profile requires the portable Python and C suites, editor authoring
tests, actual editor preview/diagnostic/Native Play verifiers, and the ROM
scenarios below. An optional diagnostics-disabled district comparison is
reported independently when selected with `--include-optional`: its failure
cannot be hidden or treated as a known fixture failure. It does not replace
the required shipped-default strict run.

| Scenario | Evidence class | What success establishes |
| --- | --- | --- |
| Probe Native and ROM replay | Functional | Every declared mission/stream field agrees, with numeric tolerance 0.002, on the same 1,070 recorded inputs and package |
| District ordinary route | Functional, legacy adapter | The existing input tour, collision/floor observations, persistence and restart checks pass |
| District performance route | Strict wall time, legacy adapter | Complete continuous route, maximum frame/interval 33.333333 ms, service CPU 8 ms, no dropped simulation ticks or loading barriers, and guest audio continuity |
| District framebuffer views | Capture only, legacy adapter | Three actual 320×240 renderer captures decode; timing and audio acceptance remain separate |
| Ordinary menu / 4 MiB gate | Lifecycle, legacy adapter | Menu runs without initializing gameplay; the expansion gate rejects gameplay at 4 MiB |
| Repeated menu switching | Lifecycle, legacy adapter | Every authored start launches/restarts three times, resume preserves mission state, and both heap paths stabilize exactly after warmup |

## Input and artifact identity

The pipeline captures a hashed source archive under `.dev/v5/<run>/inputs`
and executes at the stable project path. On Windows, read handles deny writes
and replacement of captured source files for the run. This keeps include paths
stable for the translation-unit cache. Source/dependency audits run around
each node and before publishing acceptance. On platforms without that Windows
guard, an audit detects changes and aborts acceptance; those platforms do not
claim write prevention. A project pipeline lock prevents concurrent pipeline
builds from racing over generated output.

The maintained README, workflow, architecture, project-status, character-animation
and bootstrap pages are captured and guarded too. The required capability check
consumes their marked sections; editing one after a run makes that evidence stale.
Generated `docs/evidence/` exports remain outside the source snapshot.

The exact parsed manifest bytes are checked again under the input guard and
copied into the run's artifacts. Startup hashes for the controller, snapshot
and scenario implementations are audited too, so an edited tool cannot be
bound to code already loaded by the interpreter. Relevant compiler/validation
environment values are recorded and checked during the run. Source directory
links and junctions are rejected, apart from excluded generated output; a
mutable asset alias cannot disappear from the inventory. Summary output inside
the project must remain under `build/` or `.dev/`.

Generated build/cook outputs and private verifier projects are separate from
the captured inputs. SDK/toolchain and external Tiny3D/LightEngine dependencies
have recorded identities and are audited too. Tiny3D's Git metadata is archived
and guarded with its source because the build uses the pinned checkout's HEAD
and tracked file list; transient Git locks and reflogs are excluded. Consumed
SDK headers/libraries, compiler internals, host baker/portable compiler tools
and MSYS2 build tools enter the audit. A changed input, dependency,
compiler, build recipe, or applicable flags changes the build identity. Object
cache hits reuse compatible translation units; they do not excuse stale content
or skip linker/cook identity checks.

Pipeline ROMs use stable short tags derived from the manifest build ID, such
as `DarkLantern64-p-district-performance.z64`. Long IDs include a deterministic
hash suffix to keep tags within 24 characters and generated Windows paths
bounded. The saved build report still records every effective variant flag;
the filename is not the build identity.

Each ROM variant records its exact build signature, ROM SHA-256, debug-map
identity and level catalog. ROM, build metadata and map are copied into the
run's artifacts before scenarios start. Each level's source/partition/package
binding is recorded separately; a bundle with several levels is not described
as one mission package. Each emulator run copies its tested ROM, isolated
settings, stdout/stderr and terminal status into its owned run directory.
Parsed results and captures are hashed and retained even when a scenario fails.

An OS-wide automation emulator lease permits one owned Ares process at a time,
including tools invoked outside this pipeline. Waiting for the lease is
reported as queued. On Windows the process starts suspended, enters the owned
kill-on-close job and is then resumed. Timeout, cancellation or owner death
cleans up that job before a new lease is granted. Interactive emulator sessions
are outside that job. Outer command jobs also contain editor/build descendants;
POSIX cleanup uses owned process groups and reports its more limited crash
containment separately.

`--host-audio-none` creates an owned settings copy with host output disabled;
it never changes the active user's profile. The report binds the source and
isolated settings hashes and labels disabled host output. Guest N64 audio
callbacks and underruns are still checked. This configuration certifies neither
audible host playback nor original hardware performance. The historical issue
#4 evidence records a WASAPI startup blockage and the owned-profile control
that established this distinction.

## Results and known fixtures

Nodes report `passed`, `failed`, `known_failure`, `skipped`, `incomplete`, or
`stale`. A required skip, timeout, missing completion, identity mismatch or
missing newly written result cannot establish acceptance. Profile selection
and `--skip-editor` / `--skip-rom` produce explicit required skips, rather than
silently claiming a full pass. Capture-only and fixed-dt results never certify
strict timing.

The manifest permits one existing Sponza fixture error by exact unittest ID
and terminal exception text. The report retains the entire traceback and calls
it `known_failure`, with `passed:false`; a new error in that testcase, a chained
new exception, or the same error in another testcase fails. Two optional
installed-asset skips and one Windows symlink-capability skip are declared by
exact testcase and skip reason. The symlink allowance matches only WinError
1314, the expected privilege message and that testcase's paired paths; another
error or another testcase remains incomplete. Other skips, including a missing
compiler, are incomplete. The report includes the tests actually executed,
known failures and declared skipped fixtures.

Python discovery uses the canonical `tools.*` module namespace. Direct script
invocation removes its script-directory import path before discovery, avoiding
duplicate compiler exception classes and mocks aimed at the wrong module.

Historical evidence is context only. The
[render diagnostics evidence](evidence/render-diagnostics.json) contains the
issue #4 default-enabled district strict pass and the diagnostics-disabled
counterfactual timing failure. The current pipeline runs and retains selected
comparisons again; it does not inherit those historical decisions. Unselected
optional nodes remain explicit skips in the report.
The [pipeline evidence](evidence/validation-pipeline.json) binds the validation
run used for this change, including any required failures or partial phases.

The recorded 2026-10-14 run `04503586c8d1` accepted all 22 required nodes:
21 passed and the Python suite retained its one declared Sponza fixture error.
Of 631 Python tests, 628 passed and two declared tests skipped. The six ROM
variants, actual editor checks, 1,070-tick host/ROM parity, menu lifecycle and
three framebuffer views passed. The complete strict run measured maxima of
32.0464 ms per frame, 32.322027 ms per interval and 7.990485 ms service CPU,
with no dropped ticks, loading waits or blocked inputs. The two optional
diagnostics-disabled nodes were explicitly unselected.

The evidence also retains two controlled incomplete attempts and one complete
failed attempt. Those failures exposed duplicate Python import namespaces,
Windows JSON sharing races and the ROM's integer flag representation in the
data verifier. They remain failed/incomplete records; the final acceptance
comes from a fresh run of every required check with the fixes included in its
captured source identity.

## Declare another content route

Add a route JSON with a start preset, stream boundary, input segments and data
expectations; add a `generic_replay` Native scenario to the manifest with that
route and mission. Add a ROM build using `prepared_replay` and the ordinary
`input_replay` instrumentation, then a ROM scenario consuming the same prepared
replay. No new level-specific C flag, input driver or parser is needed. Use the
[probe route](../tests/fixtures/stream_probe_route.json) as an example.

```json
{
  "description": "Walk, collect, return and restart",
  "start_preset": "default",
  "stream_boundary": "settled",
  "segments": [
    {"ticks": 60, "input": {"forward": 1}},
    {"ticks": 1, "input": {"use": true}},
    {"ticks": 1, "input": {"restart": true}},
    {"ticks": 30, "input": {}}
  ],
  "expectations": [
    {"id": "not-caught", "field": "caught", "op": "eq", "value": false},
    {"id": "restart-clears-loot", "select": "final", "field": "loot_value", "op": "eq", "value": 0}
  ]
}
```

Expectation fields use dotted object keys and numeric array indices. `select`
is `all`, `first`, `final`, or `after_match_until_input`; the last form starts
at the first `start_match` and stops before the specified input, such as a
restart. Relations are `eq`, `ne`, `gt`, `gte`, `lt`, `lte`; aggregate operations
are `contains_all`, `min_changes`, `constant`, and `any_gt`. Missing fields,
empty/incomplete traces, absent span boundaries, unknown operators and failed
expectations fail closed. Rules are data, never evaluated as Python code.

Native replay represents flags as JSON booleans, while the ROM protocol uses
integer 0/1. Equality recognizes those exact equivalent representations.
Other integers, floats, strings and general truthy conversions do not satisfy
boolean rules; numeric comparison operators remain numeric only.

Preparation cooks once, verifies DLM checksums/package SHA, binds the replay
fingerprint and hashes the route/replay. Native and ROM use those same inputs.
The ROM adapter verifies the built catalog contains that package and compares
every declared functional field from the generic replay records. Settled
service boundaries and fixed 60 Hz simulation intentionally remove platform
loading speed from this comparison. Use the independent strict performance
scenario to assess console frame budgets.

For adapter development, the helper has explicit `prepare`, `check`, and
`scenario` subcommands accepting JSON `--spec`, `--context`, and `--output`.
The orchestrator supplies paths, captured build artifacts and prepared inputs;
command arguments substitute declared tokens and never pass through a shell.
To test its failure and identity contracts:

```powershell
python -m unittest discover -s tests -p test_validation_scenarios.py -v
```
