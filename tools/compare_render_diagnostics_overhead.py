"""Compare complete enabled/disabled runs of the same strict district workload.

Preserves both failures and every sample distribution. Differences between
whole runs include simulation/display phase variation; policy cost rows give a
separate direct elapsed measurement including interrupts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

try:
    from runtime_inspector import parse_lighting_records
except ModuleNotFoundError:
    from tools.runtime_inspector import parse_lighting_records

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = ("compiler", "sdk", "renderer", "renderer_dependency", "kernel_flags", "source_sha256",
           "runtime_trace", "autoplay", "debug_overlay", "capture", "scale_bench", "model_culling",
           "lighting_bake_verify", "disable_lighting_bake", "bundle", "start_level", "start_preset",
           "menu_test", "stream_test", "stream_test_capture", "bafford_stream_test",
           "bafford_stream_test_capture", "bafford_expanded_test", "bafford_expanded_test_capture",
           "bafford_district_test", "bafford_district_test_capture", "bafford_district_performance_test")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path, enabled, map_path=None):
    path = Path(path).resolve()
    report = json.loads(path.read_bytes())
    log = Path(report["log"])
    require(log.is_absolute() and log.is_file(), "Performance report lacks its original captured log")
    raw = log.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == report["log_sha256"], "Performance log differs from recorded hash")
    build_path = log.parent / "verification-build.json"
    build = json.loads(build_path.read_bytes())
    require(build.get("render_diagnostics") is enabled, "Wrong diagnostic logging flag in recorded build")
    require(build.get("signature") == report["build_signature"], "Performance report and build identity differ")
    require(build.get("rom_sha256") == report["rom_sha256"], "Performance report and ROM identity differ")
    snapshot = log.parent / Path(build["rom"]).name
    require(hashlib.sha256(snapshot.read_bytes()).hexdigest() == build["rom_sha256"], "Captured ROM snapshot is stale")
    mapping_path = Path(map_path).resolve() if map_path else ROOT / build["runtime_debug_map"]
    mapping_raw = mapping_path.read_bytes()
    require(hashlib.sha256(mapping_raw).hexdigest() == build["runtime_debug_map_sha256"], "Runtime map differs from the build's recorded identity")
    mapping = json.loads(mapping_raw)
    require(mapping["build_id"] == build["signature"] and mapping["rom_sha256"] == build["rom_sha256"], "Map is not bound to the tested ROM")
    lighting, findings = parse_lighting_records(raw.decode("utf-8", errors="replace"), mapping)
    require(not findings, "Incomplete diagnostic overhead capture: " + str(findings))
    require(bool(lighting) is enabled, "Enabled capture lacks policy rows or disabled capture unexpectedly emits them")
    require(report.get("complete") and report.get("configuration", {}).get("route") == "continuous" and
            report["configuration"].get("dt") == "wall", "Need a complete uninterrupted wall-time workload")
    costs = [row["cost"]["elapsed_ms"] for row in lighting]
    return dict(path=str(path), report=report, build=build, build_metadata=str(build_path),
                map=str(mapping_path), lighting=lighting,
                measured_policy_transport=dict(basis="measured_elapsed", samples=len(costs),
                    total_ms=sum(costs) if costs else None, max_ms=max(costs) if costs else None,
                    average_ms=sum(costs)/len(costs) if costs else None,
                    active_cpu_ms=None, scope="Policy rows and transport, includes interrupts; excludes each cost row."))


def compare(enabled_path, disabled_path, enabled_map=None, disabled_map=None):
    on, off = load(enabled_path, True, enabled_map), load(disabled_path, False, disabled_map)
    require(all(on["build"].get(key) == off["build"].get(key) for key in OPTIONS),
            "Build options/content/compiler differ beyond renderer diagnostic logging")
    require(on["report"]["mission_sha256"] == off["report"]["mission_sha256"], "Workload mission packages differ")
    require(on["report"]["configuration"] == off["report"]["configuration"] and
            on["report"]["limits"] == off["report"]["limits"], "Workload or strict timing limits differ")
    require(on["report"]["run"]["settings_source_sha256"] == off["report"]["run"]["settings_source_sha256"],
            "Enabled/disabled runs used different source emulator settings")
    require(on["report"].get("host_audio_output") == off["report"].get("host_audio_output"),
            "Host audio configuration differs between runs")
    differences = {}
    for metric in ("frame_ticks", "interval_ticks", "service_ticks", "service_cpu_ticks", "render_ticks"):
        a, b = on["report"]["frames"][metric], off["report"]["frames"][metric]
        differences[metric] = dict(enabled=a, disabled=b,
            enabled_minus_disabled_average_ms=a["average_ms"]-b["average_ms"],
            enabled_minus_disabled_max_ms=a["max_ms"]-b["max_ms"])
    accepted = bool(on["report"]["passed"] and off["report"]["passed"])
    return dict(schema_version=1, passed=accepted, measurement_complete=True, strict_pair_passed=accepted,
        scope="Two complete runs of the same continuous wall-time district workload; all samples retained.",
        mission_sha256=on["report"]["mission_sha256"], limits=on["report"]["limits"],
        enabled=on, disabled=off, whole_run_distributions=differences,
        host_audio_output=on["report"].get("host_audio_output", "unknown"),
        settings_source_sha256=on["report"]["run"]["settings_source_sha256"],
        measured_heap=dict(basis="runtime_allocator_measurement", enabled_peak_bytes=on["report"]["peak_heap_bytes"],
            disabled_peak_bytes=off["report"]["peak_heap_bytes"],
            enabled_minus_disabled_bytes=on["report"]["peak_heap_bytes"]-off["report"]["peak_heap_bytes"]),
        limitations=["Emulated CP0 timing; original N64 is not established.",
            "Whole-run distribution differences include frame/simulation/display phase variation and are not a causal per-frame CPU measurement.",
            "Direct policy construction/transport measurements include interrupts and exclude their own cost row.",
            "Disabled policy cost has no measured samples; unknown is not replaced by a fabricated zero.",
            "Known cook estimates and observed whole-runtime allocator peaks do not identify each unmeasured subsystem allocation."])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled", type=Path, required=True)
    parser.add_argument("--disabled", type=Path, required=True)
    parser.add_argument("--enabled-map", type=Path)
    parser.add_argument("--disabled-map", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/render-diagnostics-overhead.json")
    args = parser.parse_args(argv)
    require(args.output.resolve() not in {args.enabled.resolve(),args.disabled.resolve()},
            "Output must not overwrite an input performance report")
    report = compare(args.enabled, args.disabled, args.enabled_map, args.disabled_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"], output=str(args.output.resolve()),
        policy_transport=report["enabled"]["measured_policy_transport"]), indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
