"""Stage a saved scene with the installed-source add-on, without autoexec."""
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from darklantern64_export.core import require
from darklantern64_export.scene_bridge import export_scene, metadata


def export(project, report):
    project = Path(project).resolve()
    source = Path(bpy.data.filepath).resolve()
    require(source.is_relative_to(project) and source.suffix.lower() == '.blend',
            'Open a saved .blend checkout inside the expected project')
    data = metadata(bpy.context.scene)
    require(Path(data['project']).resolve() == project,
            'This .blend belongs to another project; make a checkout in the expected project')
    result = export_scene(bpy.context)
    # Persist the revision so the next process cannot publish a conflicting
    # copy of revision 1. Blender's normal source backup policy still applies.
    bpy.ops.wm.save_as_mainfile(filepath=str(source), compress=True)
    Path(report).write_text(json.dumps(dict(blend=str(source), exchange=data['exchange'],
        scene_id=result['scene_id'], revision=result['revision'],
        entities=len(result['entities']), meshes=len(result['meshes']),
        collision_review=result.get('collision_review', [])), indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    arguments = sys.argv[sys.argv.index('--') + 1:]
    require(len(arguments) == 2, 'Expected project root and export report paths')
    export(*arguments)
