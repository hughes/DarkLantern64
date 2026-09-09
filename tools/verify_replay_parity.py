"""Compare every recorded simulation tick between native C and the actual ROM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid
from build import build_rom
from compile_level import compile_level
from smoke_ares import exercise

ROOT=Path(__file__).resolve().parents[1]

def compare(native,console,path="state"):
    # Compare discrete decisions exactly; numeric state uses a small explicit
    # tolerance for PC/MIPS libm and seven-decimal diagnostic serialization.
    if isinstance(console,dict):
        for key,value in console.items():compare(native[key],value,path+"."+key)
    elif isinstance(console,list):
        assert len(native)==len(console),path+": count differs"
        for i,(a,b) in enumerate(zip(native,console)):compare(a,b,path+f"[{i}]")
    elif isinstance(native,float):
        assert abs(native-console)<=.002,f"{path}: native={native} console={console}"
    else:assert native==console,f"{path}: native={native} console={console}"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level",type=Path,default=ROOT/"content/first_room.json")
    parser.add_argument("--replay",type=Path)
    args=parser.parse_args()
    directory=ROOT/"build/replay-parity"/uuid.uuid4().hex;directory.mkdir(parents=True)
    compile_level(args.level,directory/"content")
    package=directory/"content/generated/play.json"
    if args.replay:replay=args.replay
    else:
        data=json.loads(package.read_bytes())
        commands=[]
        # Includes movement, stance, jumping, a noise stimulus, turning, and
        # interaction, so guard state and collision are exercised together.
        for i in range(360):commands.append({"forward":1 if i<120 else 0,"strafe":.5 if 120<=i<180 else 0,
            "turn":.5 if 180<=i<240 else 0,"crouch":240<=i<300,"noise":i==100,"jump":i==50,"use":i==320})
        replay=directory/"input.json"
        replay.write_text(json.dumps({"format":"darklantern-replay","version":1,"tick_rate":60,
            "fingerprint":data["fingerprint"],"start_preset":"default","inputs":commands}))
    raw=subprocess.check_output([str(ROOT/"editor/bazel-bin/play_runner.exe"),str(package),str(replay),"--states"],text=True)
    native=json.loads(raw);(directory/"native.json").write_text(raw)
    rom=build_rom(Path(os.environ.get("N64_INST","C:/n64-toolchain")),level=args.level,input_replay=replay)
    result=exercise(Path(os.environ.get("ARES_EXE",str(Path(os.environ["LOCALAPPDATA"])/"ares/ares.exe"))),
        ROOT/".dev/ares/settings-8mb.bml",rom,"native-parity","DL64 input_replay_done",90)
    lines=(ROOT/result["log"]).read_text(errors="replace").splitlines()
    states=[json.loads(line.split("DL64 replay_tick ",1)[1]) for line in lines if "DL64 replay_tick " in line]
    assert len(states)==len(native["states"]),"Console replay trace is incomplete"
    for a,b in zip(native["states"],states):compare(a,b,f"tick {b['tick']}")
    result.update(ticks=len(states),passed=True,numeric_tolerance=.002,native=str(directory/"native.json"))
    (directory/"result.json").write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=="__main__":main()
