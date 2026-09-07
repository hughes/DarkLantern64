#!/usr/bin/env python3
"""Send a bounded local command to a running DarkLantern64 LightEngine editor.

Examples:
  python tools/editorctl.py inspect
  python tools/editorctl.py list_levels
  python tools/editorctl.py open_level --args-file open-level.json
  python tools/editorctl.py play_level --timeout 180
  python tools/editorctl.py play_menu --timeout 180
  python tools/editorctl.py set_entity --args '{"id":"store-guard","patch":{"speed":1.1}}'
  python tools/editorctl.py import_asset_pack --args-file asset-pack-import.json
  python tools/editorctl.py add_prop --args-file prop-placement.json
  python tools/editorctl.py add_enemy --args-file enemy-placement.json
  python tools/editorctl.py duplicate_enemy --args-file enemy-copy.json
  python tools/editorctl.py add_waypoint --args-file waypoint-placement.json
  python tools/editorctl.py delete_entity --args-file object-delete.json
  python tools/editorctl.py set_environment --args-file environment-patch.json
  python tools/editorctl.py set_material --args-file material-patch.json
  python tools/editorctl.py save
  python tools/editorctl.py build --timeout 180
  python tools/editorctl.py capture
  python tools/editorctl.py get_layout
  python tools/editorctl.py get_audio_memory
  python tools/editorctl.py get_animation
  python tools/editorctl.py set_animation --args-file character-pose.json
  python tools/editorctl.py focus_character --args-file character-id.json
  python tools/editorctl.py set_audio_config --args-file audio-patch.json
  python tools/editorctl.py recalculate_audio
There is no evaluation or arbitrary command operation. Arguments are JSON data.
The default queue follows the project editor's active level. open_level takes
{"file":"moonlit_courtyard.json"}; create_level takes name/title/in_bundle;
duplicate_level also takes file. delete_level requires file and confirmed:true.
Unsaved edits must be saved before command-driven switching. --level targets
an isolated diagnostic editor only; it does not select a level in the project UI.
add_prop accepts an optional loot_highlight boolean (default false); set_entity
can patch loot_highlight on a static model prop. Save + Cook before playing to
see its gentle brightness pulse in the N64 game.
set_animation takes id plus clip/time/playing, blend_clip/blend, and bounded
head_yaw/head_pitch in degrees. It changes preview playback only. Set playing
false before deterministic captures; get_animation includes sampled matrices.
focus_character takes id and frames its current pose in the editor viewport.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ("inspect", "list_levels", "open_level", "create_level", "duplicate_level", "delete_level", "update_level", "play_level", "play_menu", "import_asset_pack", "add_prop", "set_entity", "add_enemy", "duplicate_enemy", "add_waypoint", "delete_entity",
              "set_environment", "set_material", "set_preview_transform", "get_animation", "set_animation", "focus_character", "save", "build", "capture",
              "get_layout", "reset_layout", "reload", "get_audio_memory", "set_audio_config",
              "recalculate_audio", "quit")


def command_queue(root, level=None):
    """Match the editor's selected-level queue without touching another scene."""
    root = Path(root).resolve()
    queue = root / ".dev/editor"
    if level is None:
        return queue / "project"
    level = Path(level)
    source = (level if level.is_absolute() else root / level).resolve()
    if source.parent != (root / "content").resolve() or source.suffix != ".json":
        raise ValueError("Choose a JSON level directly inside this project's content directory")
    if not source.stem or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in source.stem):
        raise ValueError("Level filename must use only ASCII letters, digits, underscores or hyphens")
    if source == (root / "content/first_room.json").resolve():
        return queue
    return queue / "scenes" / source.stem


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--args", default="{}", help="JSON object; no executable expressions")
    parser.add_argument("--args-file", type=Path, help="Read JSON arguments from a file (avoids shell quoting)")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--level", type=Path, help="Target an isolated diagnostic editor launched with --level; default targets the project editor")
    parsed = parser.parse_args()
    try:
        arguments = json.loads(parsed.args_file.read_text(encoding="utf-8") if parsed.args_file else parsed.args)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
        if not 0 < parsed.timeout <= 3600:
            raise ValueError("timeout must be between 0 and 3600 seconds")
        queue = command_queue(parsed.root, parsed.level)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    command_id = uuid.uuid4().hex
    requests, responses = queue/"requests", queue/"responses"
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    now = time.time()
    request = {"id":command_id,"op":parsed.operation,"args":arguments,
               "created_at":now,"expires_at":now+parsed.timeout}
    temp = requests/f".{command_id}.tmp"
    destination = requests/f"{command_id}.json"
    temp.write_text(json.dumps(request), encoding="utf-8")
    os.replace(temp, destination)
    response = responses/f"{command_id}.json"
    deadline = time.monotonic()+parsed.timeout
    while time.monotonic() < deadline:
        if response.exists():
            result = json.loads(response.read_text(encoding="utf-8"))
            if result.get("id") != command_id:
                print("Response ID mismatch",file=sys.stderr)
                return 1
            print(json.dumps(result,indent=2))
            return 0 if result.get("ok") else 1
        time.sleep(.1)
    print(json.dumps({"id":command_id,"ok":False,"error":"Timed out. Editor may be closed or busy. Check response before retrying a mutation.","response":str(response)}),file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
