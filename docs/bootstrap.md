# Development environment and bootstrap

Run the read-only doctor from the repository root:

```powershell
python tools/development_environment.py
python tools/development_environment.py --no-probe
```

It resolves Python, Pillow, host GCC, the N64 SDK/compiler, MSYS2 bash,
Bazel/Bazelisk, Git, editor C++/Vulkan prerequisites, pinned dependency
locations, the editor, Native runner and Ares. Each tool has its resolved path,
selection source, availability, version or an explicit unknown version, probe
status and a repair action. Compiler/Python/bash version commands are bounded;
Ares, editor executables and Bazelisk are never launched by discovery.
`readiness` checks prerequisites, rather than certifying a successful build.
Dependency commits are declared expectations; discovery does not certify a
checkout's revision or a cached Tiny3D library's compatibility.

Explicit `DL64_HOST_CC`, `N64_INST`, `MSYS2_BASH`, `ARES_EXE`, `VULKAN_SDK`,
`CXX` and `BAZEL` settings are authoritative. A missing configured path remains
missing and the report explains how to repair it. An optional JSON object passed
with `--configured paths.json` overrides environment settings using
`host_compiler`, `n64_sdk`, `msys2_bash`, `ares`, `vulkan_sdk`, `host_cxx`,
`bazel`, `python`, `editor` and `runner` keys. Relative paths resolve from the
project root. No discovery command downloads or installs dependencies.

The supported development host is Windows. Use Python 3.11+ with Pillow; host
GCC comes from PATH, `C:/ProgramData/mingw64/mingw64/bin/gcc.exe`, or MSYS2
UCRT64, in that order. The build and lighting cooker use that same selection.
For the desktop editor, install Git, Bazelisk, Visual Studio C++ build tools and
Vulkan SDK. Bazel can discover an installed MSVC compiler outside a developer
shell; the doctor also inspects its actual installation paths.

ROM builds use the installed project libdragon fork SDK, normally
`C:/n64-toolchain`; set `N64_INST` for another location. The SDK is not
automatically provisioned at a pinned revision. The default Tiny3D renderer
also needs MSYS2 bash and its pinned source checkout. Prepare dependencies
explicitly after installing their prerequisites:

```powershell
python tools/setup_editor.py
python tools/build_tiny3d.py --fetch
# Or prepare the editor and Tiny3D together:
python tools/setup_editor.py --tiny3d
```

These preparation commands may fetch source and build local dependencies.
Ordinary ROM builds do not fetch Tiny3D. Existing checkouts at the wrong
revision are reported without resetting edits. Install Ares separately and
open it once to create settings before emulator checks; `ARES_EXE` selects its
executable. Blender and a local Thief installation are optional content tools.
Generated/local commercial assets are absent from a clean checkout and their
availability is separate from code test failures.

Run Python tests with the standard entry point:

```powershell
python tools/run_tests.py
# A focused subset retains its discovery pattern in the result:
python tools/run_tests.py --pattern test_development_environment.py
```

The runner starts a UTF-8 child process with a unique project-owned TEMP/TMP,
TMPDIR and Python cache under `.dev/test-runs/`, then stops owned descendants
and removes that temporary tree. It retains each structured report under
`build/test-results/<run-id>.json` and publishes the latest report to
`build/test-results/latest.json`. Reports include the discovered test scope,
source-file inventory/hash and acceptance-manifest identity. Inputs changed
during execution produce a stale result. Failure and skip classification comes
from the same [acceptance manifest](../tools/acceptance_manifest.json) used by
the project pipeline: only its exact Sponza test ID and terminal exception are
an accepted `known_failure`; the failure remains visible and is not a pure
pass. Declared absent local assets and OS capabilities have separate lists.
Any new failure, undeclared skip or zero-test discovery returns nonzero.

`python tools/build.py --test` runs this Python entry point followed by portable
C checks. The [validation pipeline](validation-pipeline.md) delegates its
Python check to the same runner. Full editor and ROM acceptance still requires
the full pipeline; a Python subset is not project-wide certification.

Exercise discovery and runner bootstrap without ignored local state:

```powershell
python tools/verify_bootstrap.py --output build/bootstrap.json
```

The verifier makes a private source copy under `.dev/bootstrap/`, excluding
`.git/`, build products, external checkouts and ignored content. It deliberately
configures missing compiler/SDK/editor/emulator paths and checks their explicit
unavailable status, then runs doctor, runner, manifest-classifier, project-status
and capability-documentation test subsets with fresh TEMP directories. The
project-status tests include deliberately stale source, partition and artifact
fixtures. The report binds copied source and test
results and records reuse of the installed Python/Pillow. It makes no full-suite,
SDK, editor, emulator or hardware acceptance claim. Missing SDK tools and the
ignored TLA interpreter can produce undeclared skips in a full clean-copy test
run; those remain incomplete until their prerequisites are prepared.
