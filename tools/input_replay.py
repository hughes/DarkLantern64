"""Validated tick commands shared by the native runner and diagnostic ROM builds."""
import json
import math
from pathlib import Path

AXES=("forward","strafe","turn","look")
BUTTONS=("crouch","use","noise","restart","jump")

def load(path):
    replay=json.loads(Path(path).read_bytes())
    if replay.get("format")!="darklantern-replay" or replay.get("version")!=1 or replay.get("tick_rate")!=60:
        raise ValueError("Unsupported input replay")
    rows=replay.get("inputs")
    if not isinstance(rows,list) or not 1<=len(rows)<=36000:
        raise ValueError("Replay must contain 1..36000 ticks")
    for row in rows:
        if not isinstance(row,dict) or set(row)-set(AXES+BUTTONS):raise ValueError("Unknown input fields")
        for key in AXES:
            value=row.get(key,0)
            if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1:raise ValueError("Input axis must be -1..1")
        for key in BUTTONS:
            if type(row.get(key,False)) is not bool:raise ValueError("Input buttons must be boolean")
    return replay

def header(replay):
    try:
        from compile_level import f
    except ModuleNotFoundError:
        from tools.compile_level import f
    rows=[]
    for row in replay["inputs"]:
        rows.append("{"+",".join(f(row.get(key,0)) for key in AXES)+","+",".join(str(row.get(key,False)).lower() for key in BUTTONS)+"}")
    return "static const DlInput dl_replay_inputs[]={\n"+",\n".join(rows)+"\n};\n#define DL_REPLAY_TICKS "+str(len(rows))+"\n"
