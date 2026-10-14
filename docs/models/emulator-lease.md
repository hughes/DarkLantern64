# Emulator lease lifetime

`tools/smoke_ares.py:exercise` acquires the host lease before launching a
disposable emulator. All project directories use the same key,
`DarkLantern64.Ares.Automation.v1`. Windows uses a global OS mutex; POSIX uses
an exclusive flock on a fixed `/tmp` path. A bounded wait records a queued
state and fails without launching if the lease cannot be acquired. The lock
is released by the OS when its owner process dies. The Python process guard
also prevents recursive acquisition through different imports or threads.

On Windows, the emulator starts suspended. Its owned `Popen` process joins the
named automation job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before its
primary thread resumes. Registration failures clean up that suspended child.
Normal, failed and cancelled runs stop and wait for their owned process,
drain owned job descendants, then release the mutex. Abandoned-owner recovery
drains any remaining members of the prior automation job before granting a
new runnable emulator. Interactive Ares instances are outside that job and
are never selected by process name or terminated by this workflow.

`OwnedProcessJob` provides the same Windows suspended assignment for a
verifier command and its descendants without acquiring the Ares lease. An
outer verifier job can contain an inner leased emulator job on Windows 8 or
newer. POSIX verifier cleanup signals only its separately created process
group. POSIX flock crash recovery covers the lock; it does not provide the
Windows job's abrupt-owner-death child containment guarantee.

```python
from tools.emulator_lease import OwnedProcessJob
with OwnedProcessJob() as job:
    process = job.spawn(command, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate(timeout=60)
```

The `exercise` positional API remains compatible. Optional keywords are
`lease_owner`, `provenance`, `lease_wait_seconds` and `lease_state_callback`.
Both success and failure retain an isolated settings file, ROM snapshot,
stdout/stderr logs and `run-status.json`. Failures raise
`AresRunError(RuntimeError)` with a structured `.result`; lease and emulator
timeouts have distinct `failure_kind` values. Partial heap/CPU samples remain
available even when no terminal marker appears. `elapsed_seconds` covers the
owned process lifetime including cleanup; lease queue time is separate.
These wall times are not a target frame-time measurement. Reporting callback
failures cannot bypass process cleanup.

The DSL model `emulator-lease.machine.ts` has two owners, a singleton grant,
one abstract owned process per owner, suspended registration, stopping,
owner death and job recovery. Its invariants prohibit multiple owned
processes/grants, running before registration and changes to the unrelated
interactive process. TLC and the TypeScript interpreter explore 63 states
and 192 edges with matching graphs. The interpreter also executes an actual
observed successful lease trace in the OS test suite.

This is a finite protocol safety proof. The Python implementation derives its
states from OS ownership and process observations; it does not mutate
machine-owned database tables. No database adapter is applicable. The proof
does not establish Python/OS equivalence, process-ID semantics, Windows API
correctness, thread enumeration, clock deadlines, scheduling fairness,
unbounded descendants or hardware performance. The separate 16-test OS suite
uses real disposable Python processes to check serialization across project
directories, re-entry, owner crashes, nested job cleanup, timeout evidence,
and preservation of an unrelated process. It launches no Ares instance and
builds no ROM.

Use the existing pinned development dependency at `.dev/tla-precheck`:

```powershell
$env:TLA2TOOLS_JAR = "$PWD/.dev/tla-precheck/tla2tools.jar"
$env:PATH = "C:/Program Files/Java/jdk-21.0.10/bin;" + $env:PATH
node tools/check_emulator_lease.mjs --proof
python tools/verify_emulator_lease.py --evidence docs/evidence/emulator-lease-os.json
```

The actual Java bin path avoids the installed Oracle launcher shim. No new
global packages are required. Generated proof artifacts stay under
`build/emulator-lease-proof`; unmodified certificates and separately labelled
OS refinement evidence are retained in `docs/evidence/`.
