"""Stage AA/high-resolution diagnostic ROMs without changing the default game.

Snapshot a verified ordinary two-guard build, preserve its object/library/DFS
inputs, then recompile only staged main/render/profile sources. Unpaced 480i
source changes are not automatically full frames; optional fixed-field
publication measures coherent interlaced frames separately.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/video-study"
MODES = {"320-aa": (320, 240, True), "640-none": (640, 480, False), "640-aa": (640, 480, True)}
OBJECTS = ["game", "main", "dl_profile", "launch", "animation", "render_lighting",
           "render_batches", "render_transform", "render_texture_packing", "bundle_level_0",
           "shared_characters", "bundle", "render"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(args, env):
    args = [value.as_posix() if isinstance(value, Path) else str(value) for value in args]
    print("[run] " + subprocess.list2cmdline(args), flush=True)
    result = subprocess.run(args, env=env, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        print(result.stdout + result.stderr, flush=True)
        result.check_returncode()
    return result


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Source changed; expected exactly one staging anchor: " + before[:80])
    return text.replace(before, after, 1)


def snapshot(output, verification):
    destination = output / "baseline"
    evidence = json.loads(verification.read_text())
    if any(evidence.get("manifest", {}).get(key, False) for key in ("lighting_bake_verify", "disable_lighting_bake")):
        raise ValueError("Baseline is not ordinary: lighting bake diagnostics are enabled")
    if destination.exists():
        saved = json.loads((destination / "snapshot.json").read_text())
        requested = evidence.get("manifest", {})
        frozen = json.loads((destination / "build.json").read_text())
        if (not evidence.get("passed") or requested.get("rom_sha256") != saved["base_rom_sha256"]
                or requested.get("signature") != frozen["signature"]):
            raise ValueError("Requested verification differs from frozen baseline; use a new output directory")
        for name, digest in saved["files"].items():
            if sha(destination / name) != digest:
                raise ValueError("Frozen baseline changed: " + name)
        if sdk_identity(Path(frozen["sdk"])) != saved.get("sdk_identity"):
            raise ValueError("SDK inputs changed since baseline snapshot; use a new verified baseline/output directory")
        return destination
    if not evidence.get("passed"):
        raise ValueError("Baseline requires a passed ordinary 60 fps verification")
    manifest = evidence["manifest"]
    if manifest["renderer"] != "t3d" or any(manifest[key] for key in
            ("autoplay", "capture", "menu_test", "debug_overlay", "scale_bench")):
        raise ValueError("Baseline is not an ordinary Tiny3D ROM")
    if manifest["start_preset"] != "two-guards":
        raise ValueError("Expected the authored two-guards start")
    rom = ROOT / manifest["rom"]
    if sha(rom) != manifest["rom_sha256"]:
        raise ValueError("Baseline ROM no longer matches its verification")
    work = ROOT / "build" / rom.stem
    library = Path(manifest["renderer_dependency"]["library"])
    if sha(library) != manifest["renderer_dependency"]["library_sha256"]:
        raise ValueError("Tiny3D library differs from the verified baseline")
    destination.mkdir(parents=True)
    shutil.copytree(ROOT / "src", destination / "src")
    includes = Path(manifest["renderer_dependency"].get("include", str(ROOT / "external/tiny3d/src")))
    shutil.copytree(includes, destination / "tiny3d/src")
    shutil.copytree(work / "catalog", destination / "catalog")
    (destination / "objects").mkdir()
    object_names = OBJECTS + [name for name in ("static_lighting", "lighting_bake") if (work / (name + ".o")).exists()]
    for name in object_names:
        shutil.copyfile(work / (name + ".o"), destination / "objects" / (name + ".o"))
    shutil.copyfile(library, destination / "tiny3d/libt3d.a")
    for source, target in ((work / "textures.dfs", "textures.dfs"),
                           (work / "build.json", "build.json"),
                           (verification, "final-verification.json"), (rom, rom.name)):
        shutil.copyfile(source, destination / target)
    files = {p.relative_to(destination).as_posix(): sha(p) for p in sorted(destination.rglob("*")) if p.is_file()}
    write_json(destination / "snapshot.json", {"base_rom_sha256": sha(rom), "files": files,
        "sdk_identity": sdk_identity(Path(manifest["sdk"])),
        "source_snapshot_assurance": "Run only after the verified baseline's canonical sources are frozen. Source, object, library and report snapshots are retained."})
    return destination


def sdk_identity(sdk):
    files = [p for p in sorted((sdk/"mips64-elf/include").rglob("*")) if p.is_file()]
    files += [sdk/"mips64-elf/lib"/name for name in ("libdragon.a", "libdragonsys.a", "n64.ld")]
    files += [sdk/"bin"/(name+".exe") for name in ("mips64-elf-gcc", "mips64-elf-g++", "n64sym",
              "mips64-elf-strip", "n64elfcompress", "n64tool", "mips64-elf-size")]
    return {p.relative_to(sdk).as_posix():sha(p) for p in files}


PROFILE_OBSERVER = r'''
static uint32_t study_bases[3], study_base_count, study_stride;
static volatile uint32_t study_unknown, study_pairs, study_incomplete;
static volatile uint32_t study_vi_total;
static uint32_t study_previous, study_fields;
static bool study_seen, study_pair_counted;
static surface_t *volatile study_ready[3];
static volatile unsigned study_ready_read, study_ready_write, study_ready_count;

void dl_profile_video_ready(void *surface) {
    disable_interrupts();
    assert(study_ready_count<3);
    study_ready[study_ready_write]=(surface_t *)surface;
    study_ready_write=(study_ready_write+1)%3;
    ++study_ready_count;
    enable_interrupts();
}

void dl_profile_video_pace(void) {
    static uint32_t last_start;
    static bool started;
    if (started) while ((uint32_t)(study_vi_total-last_start)<2u) rspq_flush();
    last_start=study_vi_total;started=true;
}

void dl_profile_video_surface(const surface_t *surface) {
    uint32_t base = PhysicalAddr(surface->buffer) & 0x00ffffffu;
    disable_interrupts();
    unsigned i;
    for (i=0; i<study_base_count; ++i) if (study_bases[i]==base) break;
    if (i==study_base_count) {
        assert(study_base_count<3);
        study_bases[study_base_count++]=base;
    }
    study_stride=(*(volatile uint32_t *)0xA4400000 & 0x40u) ? surface->stride : 0;
    enable_interrupts();
}

static void profile_vi_callback(void) {
    ++study_vi_total;
#ifdef DL_VIDEO_STUDY_PACED
    /* Registering after display_init prepends us before its VI handler.
     * Only publish RDP-completed frames, always on the same field phase. */
    if (!(study_vi_total&1u) && study_ready_count) {
        surface_t *surface=study_ready[study_ready_read];
        study_ready_read=(study_ready_read+1)%3;--study_ready_count;
        display_show(surface);
    }
#endif
    uint32_t raw=*(volatile uint32_t *)0xA4400004 & 0x00ffffffu, base;
    if (!dl_video_base_origin(raw,study_bases,study_base_count,study_stride,&base)) {
        ++study_unknown;
        return;
    }
    if (study_stride) {
        /* The actual programmed row offset identifies the observed field.
         * VI_CURRENT already describes the next callback field at this point. */
        unsigned field=raw==base ? 1u : 2u;
        if (!study_seen || base!=study_previous) {
            if (study_seen && !study_pair_counted) ++study_incomplete;
            study_previous=base;study_fields=field;study_pair_counted=false;study_seen=true;
        } else study_fields|=field;
        if (study_fields==3 && !study_pair_counted) { ++study_pairs;study_pair_counted=true; }
    }
    dl_profile_vi_record(base);
}
'''


def stage_sources(base, destination, mode, field_pacing=1):
    width, height, antialias = MODES[mode]
    shutil.copytree(base / "src", destination / "src", dirs_exist_ok=True)
    shutil.copyfile(ROOT / "tools/video_study/vi_origin.h", destination / "src/vi_origin.h")
    originals = {name: (destination / "src" / name).read_text() for name in
                 ("main.c", "render.c", "render_t3d.inc", "dl_profile.c")}
    changed = dict(originals)
    filters = "FILTERS_RESAMPLE_ANTIALIAS" if antialias else "FILTERS_RESAMPLE"
    changed["main.c"] = replace_once(changed["main.c"],
        "display_init(RESOLUTION_320x240, DEPTH_16_BPP, 3, GAMMA_NONE, FILTERS_RESAMPLE);",
        f"display_init(RESOLUTION_{width}x{height}, DEPTH_16_BPP, 3, GAMMA_NONE, {filters});")
    changed["render_t3d.inc"] = replace_once(changed["render_t3d.inc"],
        "rdpq_mode_antialias(AA_NONE);", "rdpq_mode_antialias(" + ("AA_STANDARD" if antialias else "AA_NONE") + ");")
    if width == 640:
        changed["render.c"] = replace_once(changed["render.c"],
            "SCREEN_W=320, SCREEN_H=240, VIEW_TOP=27, VIEW_BOTTOM=192",
            "SCREEN_W=640, SCREEN_H=480, VIEW_TOP=54, VIEW_BOTTOM=384")
        changed["render.c"] = replace_once(changed["render.c"], "focal=164.0f", "focal=328.0f")
    changed["render.c"] = replace_once(changed["render.c"], '#include <libdragon.h>',
        '#include <libdragon.h>\nextern void dl_profile_video_surface(const surface_t *surface);\n'
        'extern void dl_profile_video_pace(void);\nextern void dl_profile_video_ready(void *surface);')
    # Both gameplay and menu can obtain surfaces; the selected diagnostic starts in gameplay.
    if changed["render.c"].count("surface_t *display=display_get();") != 2:
        raise ValueError("Unexpected display_get sites")
    changed["render.c"] = changed["render.c"].replace("surface_t *display=display_get();",
        "surface_t *display=display_get();dl_profile_video_surface(display);")
    if field_pacing == 2:
        changed["render.c"] = changed["render.c"].replace("dl_profile_video_surface(display);",
            "dl_profile_video_surface(display);dl_profile_video_pace();")
        if changed["render.c"].count("rdpq_detach_show();") != 4:
            raise ValueError("Unexpected detach/show sites")
        changed["render.c"] = changed["render.c"].replace("rdpq_detach_show();",
            "rdpq_detach_cb(dl_profile_video_ready,display);")
    profile = replace_once(changed["dl_profile.c"], '#include "dl_profile.h"',
        '#include "dl_profile.h"\n#include "vi_origin.h"')
    start = profile.index("static void profile_vi_callback(void) {")
    end = profile.index("\n#endif", start)
    profile = profile[:start] + PROFILE_OBSERVER.strip() + profile[end:]
    profile = replace_once(profile,
        '    assert(!(*(volatile uint32_t *)0xA4400000 & 0x40u)); /* interlaced origin offsets */\n', '')
    profile = replace_once(profile, "    VideoWindow observed = video;", "    VideoWindow observed = video;\n"
        "    uint32_t unknown=study_unknown, pairs=study_pairs, incomplete=study_incomplete;\n"
        "    study_unknown=study_pairs=study_incomplete=0;")
    anchor = '    report_begin(&writer, "profile_workload");'
    profile = replace_once(profile, anchor,
        '    report_begin(&writer, "video_study");\n'
        '    report_field(&writer, "interlaced", study_stride!=0);\n'
        '    report_field(&writer, "known_surfaces", study_base_count);\n'
        '    report_field(&writer, "unknown_origins", unknown);\n'
        '    report_field(&writer, "coherent_pairs", pairs);\n'
        '    report_field(&writer, "incomplete_sources", incomplete);\n'
        '    report_text(&writer, "\\n");\n' + anchor)
    changed["dl_profile.c"] = profile
    diffs = []
    for name, content in changed.items():
        (destination / "src" / name).write_text(content, encoding="utf-8")
        diffs.extend(difflib.unified_diff(originals[name].splitlines(True), content.splitlines(True),
                                        fromfile="baseline/"+name, tofile=mode+"/"+name))
    (destination / "changes.diff").write_text("".join(diffs), encoding="utf-8")


def build_variant(base, output, mode, field_pacing=1):
    manifest = json.loads((base / "build.json").read_text())
    tag = mode + ("-paced" if field_pacing==2 else "")
    destination = output / tag
    destination.mkdir(parents=True, exist_ok=True)
    stage_sources(base, destination, mode, field_pacing)
    sdk = Path(manifest["sdk"])
    bins, lib = sdk / "bin", sdk / "mips64-elf/lib"
    env = dict(os.environ, N64_INST=str(sdk), PATH=str(bins)+os.pathsep+os.environ.get("PATH", ""))
    common = ["-march=vr4300", "-mtune=vr4300", "-mabi=o64", "-std=gnu17", "-O2", "-g",
              "-Wall", "-Wextra", "-Werror", "-Wno-error=deprecated-declarations", "-falign-functions=32",
              "-ffunction-sections", "-fdata-sections", "-DN64", "-DDL_RENDER_T3D=1",
              "-I"+str(sdk/"mips64-elf/include"), "-I"+str(destination/"src"),
              "-I"+str(base/"catalog/generated"), "-I"+str(base/"tiny3d/src")]
    if field_pacing==2: common.append("-DDL_VIDEO_STUDY_PACED=1")
    objects = []
    object_names = OBJECTS + [name for name in ("static_lighting", "lighting_bake") if (base / "objects" / (name + ".o")).exists()]
    for name in object_names:
        obj = destination / (name + ".o")
        if name in ("main", "render", "dl_profile"):
            run([bins/"mips64-elf-gcc.exe", *common, "-c", destination/"src"/(name+".c"), "-o", obj], env)
        else:
            shutil.copyfile(base/"objects"/(name+".o"), obj)
        objects.append(obj)
    name = "DarkLantern64-video-" + tag
    elf = destination/(name+".elf")
    run([bins/"mips64-elf-g++.exe", "-mabi=o64", "-g", "-o", elf, *objects, base/"tiny3d/libt3d.a",
         "-L"+str(lib), "-lc", "-ldragon", "-lm", "-ldragonsys", "-Wl,-T,"+str(lib/"n64.ld"),
         "-Wl,--gc-sections,--wrap,__do_global_ctors", "-Wl,-Map="+str(destination/(name+".map"))], env)
    symbols = destination/(name+".sym")
    run([bins/"n64sym.exe", elf, symbols], env)
    raw, compressed = destination/"uncompressed", destination/"compressed"
    raw.mkdir(exist_ok=True); compressed.mkdir(exist_ok=True)
    run([bins/"mips64-elf-strip.exe", "-s", "-o", raw/"program.elf", elf], env)
    run([bins/"n64elfcompress.exe", "-o", compressed, "-c", "1", raw/"program.elf"], env)
    rom = destination/(name+".z64")
    run([bins/"n64tool.exe", "--title", "DarkLantern64", "--toc", "--output", rom, "--align", "256",
         compressed/"program.elf", "--align", "8", symbols, "--align", "16", base/"textures.dfs"], env)
    width, height, aa = MODES[mode]
    report = {"schema_version": 1, "mode": tag, "diagnostic_only": True,
        "base_rom_sha256": manifest["rom_sha256"], "base_build_signature": manifest["signature"],
        "source_sha256": manifest["source_sha256"], "rom": str(rom.relative_to(ROOT)),
        "rom_sha256": sha(rom), "rom_bytes": rom.stat().st_size,
        "width": width, "height": height, "interlaced": height==480, "rdp_aa": "standard" if aa else "none",
        "vi_filters": "resample_antialias" if aa else "resample", "gamma": "none", "dithering": "none",
        "display_buffers": 3, "color_plus_depth_bytes": width*height*2*4,
        "hud_scaled": False, "field_pacing":field_pacing,
        "frame_pacing": "render starts every two VI callbacks; completed surfaces published on fixed alternating VI phase; pacing charged to display_wait" if field_pacing==2
            else "uncapped; libdragon may swap on any video field",
        "tiny3d_revision": manifest["renderer_dependency"]["revision"],
        "tiny3d_library_sha256": sha(base/"tiny3d/libt3d.a"), "dfs_sha256": sha(base/"textures.dfs"),
        "sdk_library_sha256": {name:sha(lib/name) for name in ("libdragon.a", "libdragonsys.a", "n64.ld")},
        "staged_sources": {p.name:sha(p) for p in sorted((destination/"src").iterdir()) if p.is_file()},
        "objects": {p.name:sha(p) for p in objects}, "changes_sha256": sha(destination/"changes.diff"),
        "compile_flags": common, "static_size": run([bins/"mips64-elf-size.exe", elf], env).stdout}
    write_json(destination/"build.json", report)
    return rom, report


def measure(rom, provenance, windows, timeout):
    from smoke_ares import exercise
    from profile_report import make_report
    from verify_guard_performance import records
    executable = Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"])/"ares/ares.exe")))
    settings = ROOT/".dev/ares/settings-8mb.bml"
    if not settings.is_file(): settings = executable.parent/"settings.bml"
    result = exercise(executable, settings, rom, "video-"+provenance["mode"],
                      f"DL64 profile_end window={3+windows}", timeout, provenance["rom_sha256"])
    log = ROOT/result["log"]
    target = 30 if provenance["interlaced"] else 60
    profile = make_report(log.read_bytes(), log, target_fps=target, skip_windows=3, max_windows=windows)
    rows = [r for r in records(log.read_text(errors="replace"), "video_study") if 3<int(r["window"])<=3+windows]
    unknown = sum(int(r["unknown_origins"]) for r in rows)
    pairs = sum(int(r["coherent_pairs"]) for r in rows)
    incomplete = sum(int(r["incomplete_sources"]) for r in rows)
    workload = profile["workload"]
    actors = all(workload.get(f"{name}_{bound}")==2 for name in ("animated","drawn") for bound in ("min","max"))
    video = profile["video"]
    mode_matches = all(bool(int(r["interlaced"]))==provenance["interlaced"] for r in rows)
    cadence_observed = (video.get("tracked") and 59<=video.get("native_refresh_hz",0)<=61
                        and video.get("max_interval_ms",float("inf"))<25)
    text = log.read_text(errors="replace")
    ordinary_active = not any(token in text for token in
        ("DL64 capture_", "DL64 replay_", "RSP CRASH |", "caught=1", "complete=1"))
    valid = (len(rows)==windows and unknown==0 and mode_matches and cadence_observed and ordinary_active
             and all(int(r["known_surfaces"])==3 for r in rows)
             and profile["exact_frame_samples_available"] and actors and profile["audio_calls"]>0
             and profile["debug_states"]==[0])
    report = {"diagnostic_valid": valid, "provenance": provenance, "run": result, "profile": profile,
        "measured_windows": windows, "warmup_windows": 3, "work_budget_fps": target,
        "meets_work_budget": profile["distributions"]["work"]["over_budget_frames"]==0,
        "coherent_30fps_gate": (valid and incomplete==0 and pairs>0 and abs(pairs-video["vis"]/2)<=2
            and video["max_gap_vis"]==2) if provenance.get("field_pacing")==2 else None,
        "field_observation": {"unknown_origins":unknown,"coherent_source_pairs":pairs,
            "incomplete_sources":incomplete,"unique_source_changes_per_second":video["presented_fps"],
            "mode_matches_provenance":mode_matches,"native_field_cadence_observed":bool(cadence_observed),
            "coherent_source_pairs_per_second":pairs/video["seconds"] if provenance["interlaced"] else None},
        "limitations": ["Ares diagnostic; no original N64/M64 validation.",
            "Unpaced 480i can swap on either field: source changes/sec are not complete 480-line frame presentation.",
            "640 mode doubles world viewport dimensions and focal length, but retains unscaled HUD text/layout.",
            "Normal live guards, audio, lighting, geometry and textures; no capture freezes or simplified models.",
            "Post-VI visual review is separate; raw RDP framebuffer captures do not establish final AA appearance."]}
    write_json(rom.parent/"measurement.json", report)
    return report


def visual_session(rom, provenance):
    """Visible disposable session for a separate post-VI window capture.

    UI observation intentionally happens outside the timing run. The session
    owns its settings, controller mappings and child process, and closes after
    45 seconds even if the external screenshot workflow is interrupted.
    """
    from smoke_ares import isolate_settings
    executable = Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"])/"ares/ares.exe")))
    settings = ROOT/".dev/ares/settings-8mb.bml"
    if not settings.is_file(): settings = executable.parent/"settings.bml"
    directory = ROOT/".dev/ares/runs"/uuid.uuid4().hex
    directory.mkdir(parents=True)
    isolated, settings_hash = isolate_settings(settings,directory,8)
    copied = directory/rom.name
    shutil.copyfile(rom,copied)
    if sha(copied)!=provenance["rom_sha256"]:
        raise ValueError("Visual ROM changed after its build")
    report = {"visual_only":True,"rom_sha256":sha(copied),"settings_source_sha256":settings_hash,
              "directory":str(directory.relative_to(ROOT)),"closed":False}
    with (directory/"visual.log").open("w") as output, (directory/"visual.stderr.log").open("w") as errors:
        process = subprocess.Popen([str(executable),"--settings-file",str(isolated),"--no-file-prompt",str(copied)],
                                   cwd=ROOT,stdout=output,stderr=errors)
        try:
            print(json.dumps({"visual_session_started":str(directory),"owned_pid":process.pid}),flush=True)
            deadline = time.monotonic()+45
            while time.monotonic()<deadline:
                if (directory/"finish").exists(): break
                if process.poll() is not None:
                    raise RuntimeError("Visual Ares exited early")
                time.sleep(.25)
        finally:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
            report["closed"]=True
            write_json(rom.parent/"visual-session.json",report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--verification", type=Path, default=ROOT/"build/guard-60fps/final-verification.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--visual",action="store_true",help="Open an existing variant for a separate 45-second post-VI window review")
    parser.add_argument("--field-pacing",type=int,choices=(1,2),default=1,
                        help="Staged two-field pacing for coherent 640x480 interlaced diagnostics")
    parser.add_argument("--windows", type=int)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/"build"):
        parser.error("Diagnostic output must stay under this project's build directory")
    windows = args.windows if args.windows is not None else (30 if args.mode=="320-aa" else 10)
    if not 1<=windows<=60 or not 0<args.timeout<=600:
        parser.error("windows must be 1..60 and timeout 0..600 seconds")
    if args.field_pacing==2 and args.mode=="320-aa":
        parser.error("Two-field pacing is only for 640-mode diagnostics")
    if args.visual:
        if args.run: parser.error("Visual observation must be separate from measurement")
        tag=args.mode+("-paced" if args.field_pacing==2 else "")
        provenance=json.loads((output/tag/"build.json").read_text())
        visual_session(ROOT/provenance["rom"],provenance)
        return 0
    base = snapshot(output, args.verification)
    rom, provenance = build_variant(base, output, args.mode,args.field_pacing)
    report = measure(rom, provenance, windows, args.timeout) if args.run else None
    print(json.dumps({"rom":str(rom),"rom_sha256":provenance["rom_sha256"],
        "diagnostic_valid":report["diagnostic_valid"] if report else None,
        "measurement":str(rom.parent/"measurement.json") if report else None},indent=2))
    return 0 if report is None or report["diagnostic_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
