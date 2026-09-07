"""Verify target static-lighting parity and measure ordinary renderer loading.

Runs isolated Ares instances. Diagnostic parity timings are never used as
ordinary load timings. The optional fixtures live entirely under the output
directory; authored content, active user settings and installed SDK stay intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import types
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
STAGES = ("textures", "retire", "geometry", "bake", "bake_verify", "lighting",
          "alternate_lighting", "restore", "reports", "gpu", "hud")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def records(text, kind):
    result = []
    for payload in re.findall(r"^DL64 " + re.escape(kind) + r" (.+)$", text, re.MULTILINE):
        tokens = [token.split("=", 1) for token in payload.split() if "=" in token]
        require(len(tokens) == len({key for key, _ in tokens}), "Duplicate field in " + kind)
        result.append(dict(tokens))
    return result


def one(text, kind):
    rows = records(text, kind)
    require(len(rows) == 1, "Expected exactly one complete " + kind + " marker")
    return rows[0]


def integer(row, key):
    value = row.get(key, "")
    require(re.fullmatch(r"[0-9]+", value) is not None, "Missing or invalid integer: " + key)
    return int(value)


def parse_stages(row, *, historical=False):
    stages = tuple(s for s in STAGES if not historical or s not in ("bake", "bake_verify"))
    require(row.get("scope") == "renderer_prepare_including_interrupts", "Unknown renderer timing scope")
    rate = integer(row, "ticks_per_second")
    require(rate > 0, "Invalid timer frequency")
    ticks = {stage: integer(row, stage + "_ticks") for stage in stages}
    ticks["total"] = integer(row, "total_ticks")
    require(ticks["total"] > 0 and sum(ticks[s] for s in stages) == ticks["total"], "Renderer stage sum differs from total")
    return {"ticks": ticks, "ticks_per_second": rate,
            "milliseconds": {name: value * 1000 / rate for name, value in ticks.items()},
            "scope": row["scope"], "stage_sum_exact": True}


def validate_manifest(manifest, mode, *, level_id):
    require(manifest.get("renderer") == "t3d", "Expected production T3D renderer")
    require(all(manifest.get(k) is False for k in ("autoplay", "capture", "debug_overlay", "scale_bench", "menu_test")),
            "Load measurement must use an otherwise ordinary game build")
    require(manifest.get("model_culling") is True, "Normal model culling required")
    require(manifest.get("lighting_bake_verify") is (mode == "verify"), "Verification build flag differs from requested case")
    require(manifest.get("disable_lighting_bake") is (mode == "bypass"), "Bypass build flag differs from requested case")
    levels = manifest.get("level_catalog", {}).get("levels", [])
    require(len(levels) == 1 and levels[0]["id"] == level_id, "Expected a single requested level")
    require(levels[0]["content"]["source_sha256"] == manifest.get("source_sha256"), "Catalog/content fingerprint mismatch")
    return levels[0]["content"]


def analyze(raw, manifest, mode, *, level_id, unconfigured=False, zero_textures=False, corrupt=False):
    content = validate_manifest(manifest, mode, level_id=level_id)
    text = raw.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    require(not any(marker in text for marker in ("RSP CRASH |", "DL64 capture_", "DL64 replay_")), "Unexpected diagnostic or crash")
    require("DL64 profile_end window=3\n" in text, "Boot did not reach three complete gameplay windows")
    start = one(text, "level_start")
    require(start.get("id") == level_id and start.get("preset") == (manifest.get("start_preset") or "default"), "Unexpected level or preset")
    geometry = one(text, "geometry_ready")
    for actual, expected in (("models", "models"), ("meshes", "meshes"), ("vertices", "instanced_vertices"), ("triangles", "instanced_triangles")):
        require(integer(geometry, actual) == content["counts"][expected], "Runtime geometry differs from cook: " + actual)
    prepared = one(text, "scene_prepared")
    textures = integer(prepared, "textures")
    require(textures == len(content["textures"]["textures"]), "Runtime texture count differs from cook")
    if zero_textures:
        require(textures == 0 and content.get("lighting"), "Zero-texture fixture lacks a night-lighting asset")
        assets = manifest["level_catalog"]["rom_assets"]
        require(assets and all(asset.get("kind") == "lighting" for asset in assets), "Lighting-only ROM has missing or unexpected filesystem assets")
    timing = parse_stages(one(text, "scene_load"))
    full = one(text, "level_load")
    require(full.get("scope") == "reset_to_ready_including_interrupts" and full.get("id") == level_id and
            full.get("preset") == start["preset"], "Unknown level reset timing scope or identity")
    rate, total = integer(full, "ticks_per_second"), integer(full, "total_ticks")
    require(rate == timing["ticks_per_second"] and total >= timing["ticks"]["total"], "Level reset total does not contain renderer timing")
    status = one(text, "lighting_bake")
    lighting = content.get("lighting")
    expected_status = "rejected" if corrupt else "absent" if unconfigured or not lighting else "disabled" if mode == "bypass" else "loaded"
    require(status.get("status") == expected_status, "Unexpected bake status: " + str(status))
    if expected_status == "absent":
        require(status.get("reason") == ("unconfigured" if unconfigured else "legacy"), "Unexpected fallback reason")
    elif expected_status == "disabled":
        require(status.get("reason") == "build_flag", "Unexpected bypass reason")
    elif corrupt:
        require(status.get("reason") == "checksum", "Corrupted fixture did not reach the CRC rejection path")
    if expected_status in ("loaded", "rejected"):
        require(status.get("path") == "rom:/" + lighting["path"].removeprefix("romfs/"), "Runtime bake content signature differs from cook")
        for key, expected in (("models", "static_models"), ("triangles", "static_triangles"), ("rgb_bytes", "rgb_bytes"), ("states", "states")):
            require(integer(status, key) == lighting[expected], "Loaded bake dimensions differ: " + key)
    diagnostic = records(text, "lighting_bake_verify")
    if mode == "verify" and lighting and not unconfigured:
        require(len(diagnostic) == 1, "Missing full target lighting parity diagnostic")
        row = diagnostic[0]
        for key, expected in (("models", lighting["static_models"]), ("triangles", lighting["static_triangles"]),
                              ("states", 2), ("bytes", lighting["static_triangles"] * 48), ("mismatches", 0), ("max_channel_delta", 0)):
            require(integer(row, key) == expected, "Incomplete or failed target byte parity: " + key)
        require(re.fullmatch(r"[0-9a-fA-F]{8}", row.get("baked_fnv1a", "")) is not None and
                row["baked_fnv1a"].lower() == row.get("reference_fnv1a", "").lower(), "Target lighting hashes differ or are missing")
    else:
        require(not diagnostic, "Unexpected expensive parity diagnostic in ordinary loading")
    return {"passed": True, "mode": mode, "level": level_id, "content_sha256": manifest["source_sha256"],
            "renderer_prepare": timing, "level_reset": {"ticks": total, "ticks_per_second": rate,
                "milliseconds": total * 1000 / rate, "scope": full["scope"]},
            "bake": status, "parity": diagnostic[0] if diagnostic else None, "textures": textures,
            "lighting_asset": {key: lighting[key] for key in ("path", "signature", "sha256", "bytes", "rgb_bytes", "static_models", "static_triangles", "states")} if lighting else None,
            "geometry": {k: integer(geometry, k) for k in ("models", "meshes", "vertices", "triangles")},
            "heap_bytes": int(start["heap"].split("/")[0]),
            "ordinary_load_timing": mode != "verify", "fixture_unconfigured": unconfigured,
            "fixture_zero_textures": zero_textures, "fixture_corrupt_crc": corrupt}


def validate_content(manifest, level, root):
    content = manifest["level_catalog"]["levels"][0]["content"]
    digest = hashlib.sha256(level.read_bytes())
    for dependency in content["dependencies"]:
        uri = dependency["uri"]
        path = (root / uri.removeprefix("code:") if uri.startswith("code:") else level.parent / uri).resolve()
        require(path.is_relative_to(root) and sha(path) == dependency["sha256"], "Content dependency changed: " + uri)
        digest.update(uri.encode()); digest.update(b"\0"); digest.update(path.read_bytes())
    require(digest.hexdigest() == content["source_sha256"] == manifest["source_sha256"], "Cooked content fingerprint differs")


def compare(before, after):
    require(before["content_sha256"] == after["content_sha256"], "Cannot compare different content fingerprints")
    require(before.get("ordinary_load_timing", True) and after["ordinary_load_timing"], "Parity diagnostics are not ordinary load timings")
    if before.get("source_hashes") and after.get("source_hashes"):
        runtime = lambda row: {key: value for key, value in row["source_hashes"].items() if key.startswith("src/")}
        require(runtime(before) == runtime(after), "Runtime sources changed between bypass and baked measurements")
    old = before.get("renderer_prepare") or parse_stages(before["record"], historical=True)
    new = after["renderer_prepare"]
    require(old["scope"] == new["scope"] and old["ticks_per_second"] == new["ticks_per_second"], "Load timer scopes/frequencies differ")
    old_ms, new_ms = old["milliseconds"]["total"], new["milliseconds"]["total"]
    return {"renderer_before_ms": old_ms, "renderer_after_ms": new_ms, "saved_ms": old_ms-new_ms,
            "speedup": old_ms/new_ms, "scope": new["scope"]}


def validate_historical_baseline(before, root=ROOT):
    require(before.get("passed") and before.get("stage_sum_exact"), "Historical baseline is not valid")
    parse_stages(before["record"], historical=True)
    artifacts = {}
    for kind, uri, expected in (("log", before["run"]["log"], before["log_sha256"]),
                                ("rom", before["rom"], before["rom_sha256"])):
        path = (root / uri.replace("\\", "/")).resolve()
        require(path.is_relative_to(root.resolve()), "Historical artifact escapes the workspace")
        require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, "Historical artifact lacks a SHA-256 identity")
        available = path.is_file()
        if available:
            require(sha(path) == expected, "Historical baseline " + kind + " differs from preserved evidence")
        artifacts[kind] = {"path": path.relative_to(root.resolve()).as_posix(), "sha256": expected,
                           "available_locally": available, "revalidated_locally": available}
    return artifacts


def frozen_inputs(root=ROOT):
    files = [p for folder in ("src", "tools") for p in (root / folder).rglob("*")
             if p.suffix in (".c", ".h", ".inc", ".def", ".py")]
    return {path.relative_to(root).as_posix(): sha(path) for path in sorted(files)}


@contextmanager
def frozen_tiny3d(destination):
    """Use verified copies; private fixture builds cannot rebuild shared RSP code."""
    import build_tiny3d
    reference = json.loads((ROOT / "build/tiny3d-dependency.json").read_text())
    require(sha(build_tiny3d.LIBRARY) == reference["library_sha256"], "Tiny3D library differs from its build evidence")
    stage = destination / "build/frozen-tiny3d"
    stage.mkdir(parents=True)
    shutil.copytree(build_tiny3d.BUILD_SOURCE / "src", stage / "src")
    library = stage / "libt3d.a"
    shutil.copy2(build_tiny3d.LIBRARY, library)
    patches = [destination / path.relative_to(ROOT) for path in build_tiny3d.patch_inputs()]
    for original, copied in zip(build_tiny3d.patch_inputs(), patches):
        require(sha(original) == sha(copied), "Fixture dependency patch changed during snapshot")
    hashes = {str(path): sha(path) for path in [library, *patches, *sorted((stage / "src").rglob("*.h"))]}
    facade = types.ModuleType("build_tiny3d")
    facade.REVISION, facade.BUILD_SOURCE = build_tiny3d.REVISION, stage
    facade.patch_inputs = lambda: patches
    facade.build_library = lambda sdk: library
    names = ("build_tiny3d", "tools.build_tiny3d")
    saved = {name: sys.modules.get(name) for name in names}
    try:
        for name in names: sys.modules[name] = facade
        yield hashes
        require(all(sha(Path(path)) == value for path, value in hashes.items()), "Frozen Tiny3D dependency changed")
    finally:
        for name, original in saved.items():
            if original is None: sys.modules.pop(name, None)
            else: sys.modules[name] = original


@contextmanager
def private_build_root(build, destination, *, unconfigured=False, corrupt=False):
    """Redirect this sequential build only; the parent restores globals on errors."""
    saved = build.ROOT, build.BUILD, build.prepare_bundle
    def prepare(*args, **kwargs):
        catalog, sources = saved[2](*args, **kwargs)
        if unconfigured:
            for entry in catalog["levels"]:
                header = Path(entry["cooked_dir"]) / "generated/demo_level.h"
                changed, count = re.subn(r'\.baked_lighting="rom:/lighting/lighting-[0-9a-f]{64}\.bin"',
                                         ".baked_lighting=NULL", header.read_text())
                require(count == 1, "Unconfigured fixture could not remove exactly one bake descriptor")
                header.write_text(changed)
        if corrupt:
            lighting = [asset for asset in catalog["rom_assets"] if asset["kind"] == "lighting"]
            require(len(lighting) == 1, "CRC fixture requires exactly one lighting asset")
            asset = lighting[0]
            output = Path(args[2])
            path = output / asset["path"]
            payload = bytearray(path.read_bytes())
            require(len(payload) > 64, "CRC fixture has no RGB payload")
            payload[-1] ^= 1  # Payload corruption with deliberately stale header CRC.
            path.write_bytes(payload)
            asset["sha256"] = sha(path)  # Build provenance describes the actual staged package.
        return catalog, sources
    build.ROOT, build.BUILD, build.prepare_bundle = destination, destination / "build", prepare
    try:
        yield
    finally:
        build.ROOT, build.BUILD, build.prepare_bundle = saved


def fixture_root(output, *, zero_textures):
    destination = output / "fixtures" / uuid.uuid4().hex
    destination.mkdir(parents=True)
    for folder in ("src", "tools", "content"):
        shutil.copytree(ROOT / folder, destination / folder,
                        ignore=shutil.ignore_patterns("*.blend", "*.blend1", "__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "dependencies.json", destination / "dependencies.json")
    if zero_textures:
        from asset_pack import resolve_asset_packs
        level = destination / "content/moonlit_courtyard.json"
        document, _, _ = resolve_asset_packs(json.loads(level.read_text()), level.parent)
        document.pop("asset_packs", None)
        for material in document["materials"]:
            material.pop("texture", None)
        level.write_text(json.dumps(document, indent=2) + "\n")
    return destination


def measure(args, level, mode, *, root=ROOT, unconfigured=False, zero_textures=False, corrupt=False):
    import build
    from smoke_ares import exercise
    inputs, target_inputs = frozen_inputs(), frozen_inputs(root)
    dependency_hashes = None
    options = dict(level=level, renderer="t3d", lighting_bake_verify=mode == "verify", disable_lighting_bake=mode == "bypass")
    if root == ROOT:
        rom = build.build_rom(args.sdk, **options)
    else:
        with frozen_tiny3d(root) as dependency_hashes:
            with private_build_root(build, root, unconfigured=unconfigured, corrupt=corrupt):
                rom = build.build_rom(args.sdk, **options)
    manifest = json.loads((root / "build" / rom.stem / "build.json").read_text())
    require(sha(rom) == manifest["rom_sha256"], "ROM differs from build manifest")
    validate_content(manifest, level, root)
    settings = ROOT / ".dev/ares/settings-8mb.bml"
    if not settings.is_file():
        settings = args.ares.parent / "settings.bml"
    name = "level-loading-" + level.stem + "-" + ("corrupt-crc" if corrupt else "unconfigured" if unconfigured else "zero-textures" if zero_textures else mode)
    run = exercise(args.ares, settings, rom, name, "DL64 profile_end window=3", args.timeout, sha(rom))
    log = ROOT / run["log"]
    result = analyze(log.read_bytes(), manifest, mode, level_id=level.stem, unconfigured=unconfigured, zero_textures=zero_textures, corrupt=corrupt)
    require(inputs == frozen_inputs(), "Runtime/build source changed during measurement")
    require(target_inputs == frozen_inputs(root), "Private target source changed during measurement")
    if dependency_hashes:
        require(all(sha(Path(path)) == value for path, value in dependency_hashes.items()), "Frozen dependency changed during measurement")
    validate_content(manifest, level, root)
    result.update(run=run, log_sha256=sha(log), rom_sha256=sha(rom),
                  build_signature=manifest["signature"], static_image_bytes=manifest["static_image_bytes"],
                  level_source_sha256=sha(level), source_hashes=target_inputs,
                  canonical_tool_source_hashes=inputs if root != ROOT else None,
                  frozen_dependency_hashes={relative(Path(p)): value for p, value in dependency_hashes.items()} if dependency_hashes else None,
                  packaged_assets=manifest["level_catalog"]["rom_assets"])
    (args.output / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"case": name, "renderer_ms": result["renderer_prepare"]["milliseconds"]["total"],
                      "level_ms": result["level_reset"]["milliseconds"], "parity": result["parity"]}), flush=True)
    return result


def publish_evidence(report, destination):
    """Keep hashes/measurements reviewable without local SDK paths or full logs."""
    keys = ("passed", "mode", "level", "content_sha256", "level_source_sha256", "renderer_prepare", "level_reset",
            "bake", "parity", "textures", "lighting_asset", "geometry", "heap_bytes", "ordinary_load_timing",
            "fixture_unconfigured", "fixture_zero_textures", "fixture_corrupt_crc", "log_sha256", "rom_sha256",
            "build_signature", "static_image_bytes", "packaged_assets")
    def compact(row):
        result = {key: row[key] for key in keys if key in row}
        result["run"] = {key: row["run"][key].replace("\\", "/") for key in ("log", "tested_rom", "rom_sha256", "settings_source_sha256")}
        result["runtime_source_hashes"] = {key: value for key, value in row["source_hashes"].items() if key.startswith("src/")}
        return result
    durable = {key: value for key, value in report.items() if key != "cases"}
    durable["verifier_sha256"] = sha(Path(__file__))
    durable["cases"] = {}
    for name, case in report["cases"].items():
        durable["cases"][name] = compact(case) if "bake" in case else {
            mode: compact(row) if "bake" in row else row for mode, row in case.items()}
    def portable(value):
        if isinstance(value, dict): return {key.replace("\\", "/"): portable(item) for key, item in value.items()}
        if isinstance(value, list): return [portable(item) for item in value]
        return value.replace("\\", "/") if isinstance(value, str) else value
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((json.dumps(portable(durable), indent=2) + "\n").encode())


def validate_saved_cases(report):
    """A fixture-only resume cannot promote stale normal-level measurements."""
    current = {key: value for key, value in frozen_inputs().items() if key.startswith("src/")}
    for case in report["cases"].values():
        if "bake" in case:  # Private fixtures are rebuilt by the resumed run.
            continue
        for row in case.values():
            if "bake" not in row:
                continue
            runtime = {key: value for key, value in row["source_hashes"].items() if key.startswith("src/")}
            require(runtime == current, "Saved level measurements use different runtime sources; rerun the complete suite")
            rom = (ROOT / row["run"]["tested_rom"]).resolve()
            log = (ROOT / row["run"]["log"]).resolve()
            require(rom.is_relative_to(ROOT) and log.is_relative_to(ROOT), "Saved measurement escapes workspace")
            require(sha(rom) == row["rom_sha256"] and sha(log) == row["log_sha256"], "Saved ROM/log identity changed")
            manifest = json.loads((ROOT / "build" / rom.stem / "build.json").read_text())
            require(manifest["rom_sha256"] == row["rom_sha256"], "Saved build manifest changed; rerun the complete suite")
            level = ROOT / "content" / (row["level"] + ".json")
            require(sha(level) == row["level_source_sha256"], "Saved level source changed")
            validate_content(manifest, level, ROOT)
            analyze(log.read_bytes(), manifest, row["mode"], level_id=row["level"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/level-loading")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--levels", nargs="*", help="Optional content filenames without .json; default is every registered level")
    parser.add_argument("--modes", nargs="+", choices=("ordinary", "bypass", "verify"), default=["ordinary", "bypass", "verify"])
    parser.add_argument("--skip-fixtures", action="store_true")
    parser.add_argument("--fixtures-only", action="store_true", help="Resume fixture checks after the normal level cases, preserving report.json")
    parser.add_argument("--before", type=Path, default=ROOT / "docs/evidence/level-loading-before.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "docs/evidence/level-loading.json")
    args = parser.parse_args()
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
    require(args.output.is_relative_to(ROOT / "build"), "Verification output must stay inside build/")
    from compile_bundle import read_manifest
    _, entries = read_manifest(ROOT / "content/level_bundle.json")
    levels = [entry["source"] for entry in entries]
    if args.levels:
        require(set(args.levels) <= {p.stem for p in levels}, "Unknown requested level")
        levels = [p for p in levels if p.stem in args.levels]
    report = {"schema_version": 1, "passed": False, "command": "python tools/verify_level_loading.py", "cases": {},
              "limitations": ["Ares emulated CPU elapsed timers include interrupts; original N64/M64 pending.",
                  "Renderer prepare and full level reset have distinct scopes; neither includes the first presented frame or host ROM opening.",
                  "Three gameplay windows prove completed startup, not steady gameplay performance.",
                  "Verify-mode computes both methods and is deliberately excluded from ordinary loading comparisons."]}
    if args.fixtures_only:
        report = json.loads((args.output / "report.json").read_text())
        validate_saved_cases(report)
        report["passed"] = False
        levels = []
    for level in levels:
        night = json.loads(level.read_text()).get("environment") is not None
        rows = {}
        for mode in args.modes:
            if mode != "ordinary" and not night:
                continue
            rows[mode] = measure(args, level, mode)
        if "ordinary" in rows and "bypass" in rows:
            rows["comparison"] = compare(rows["bypass"], rows["ordinary"])
        report["cases"][level.stem] = rows
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not args.skip_fixtures:
        for name, zero in (("zero-textures", True), ("unconfigured", False), ("corrupt-crc", False)):
            root = fixture_root(args.output, zero_textures=zero)
            report["cases"][name] = measure(args, root / "content/moonlit_courtyard.json", "ordinary",
                                            root=root, unconfigured=name == "unconfigured", zero_textures=zero, corrupt=name == "corrupt-crc")
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    after = report["cases"].get("sponza_courtyard", {}).get("ordinary")
    if after and args.before.is_file():
        before = json.loads(args.before.read_text())
        artifacts = validate_historical_baseline(before)
        report["historical_sponza_comparison"] = {**compare(before, after), "evidence": relative(args.before),
            "sha256": sha(args.before), "rom_sha256": before["rom_sha256"], "raw_artifacts": artifacts,
            "baseline_basis": "Checked-in historical CP0 measurements; available raw artifacts rehashed. Missing ignored artifacts are not claimed as freshly validated."}
    report["passed"] = True
    registered = [entry["source"] for entry in entries]
    report["complete_suite"] = all(
        all(mode in report["cases"].get(level.stem, {}) for mode in
            (("ordinary", "bypass", "verify") if json.loads(level.read_text()).get("environment") is not None else ("ordinary",)))
        for level in registered) and all(name in report["cases"] for name in ("zero-textures", "unconfigured", "corrupt-crc"))
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["complete_suite"]:
        publish_evidence(report, args.evidence)


if __name__ == "__main__":
    main()
