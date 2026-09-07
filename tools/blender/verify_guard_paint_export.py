"""Prove packed paint survives image save, blend save, and fresh CLI export.

Run with ordinary Python for the complete saved-file proof. Running this script
inside a private Blender process performs its in-memory and private-save stage.
Canonical art and content are fingerprinted and never used as output paths.
"""
from pathlib import Path
import argparse
import hashlib
import json
import runpy
import shutil
import subprocess
import sys

try:
    import bpy
except ModuleNotFoundError:
    bpy = None

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_paths():
    return [ROOT / 'art/guard.blend', ROOT / 'content/assets/guard/guard.character.json',
            ROOT / 'content/assets/guard/guard-atlas.png', ROOT / 'content/assets/guard/guard-atlas-painted.png',
            ROOT / 'content/assets/guard/pack.json']


def blender_main():
    paths = canonical_paths()
    original = {str(path): sha(path) for path in paths}
    helpers = runpy.run_path(str(ROOT / 'tools/blender/guard_character.py'), run_name='guard_paint_check')
    obj = next(o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get('dl64_character_id') == 'basic-guard')
    material = obj.data.materials[0]
    image = next(n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    assert image.packed_file is not None, 'Authoring image is not portable/packed'
    assert material['dl64_texture_export_name'] == 'guard-atlas-painted.png'
    out = ROOT / 'build/guard-atlas-study/paint-export'
    helpers['export'](output=out / 'before')
    # Editing the loaded packed image models an artist's unsaved paint strokes.
    # Mutate a small area, export, and reload the output to check actual pixels.
    width, height = image.size
    pixel = (height // 2 * width + width // 2) * 4
    image.pixels[pixel:pixel + 4] = (1, 0, 1, 1)
    image.update()
    helpers['export'](output=out / 'painted')
    before = out / 'before/guard-atlas-painted.png'
    after = out / 'painted/guard-atlas-painted.png'
    assert sha(before) != sha(after), 'Exporter ignored edits to packed authoring pixels'
    reloaded = bpy.data.images.load(str(after), check_existing=False)
    color = list(reloaded.pixels[pixel:pixel + 4])
    assert color[0] > .99 and color[1] < .01 and color[2] > .99 and color[3] > .99, color
    assert (out / 'before/guard.character.json').read_bytes() == paths[1].read_bytes()
    assert (out / 'painted/guard.character.json').read_bytes() == paths[1].read_bytes()
    material['dl64_texture_export_name'] = '../escape.png'
    try:
        helpers['export'](output=out / 'rejected')
    except ValueError as error:
        assert 'PNG filename' in str(error)
    else:
        raise AssertionError('Unsafe texture export filename accepted')
    assert not (out / 'rejected').exists(), 'Invalid metadata wrote output before validation'
    material['dl64_texture_export_name'] = 'guard-atlas-painted.png'

    # Make a second distinguishable stroke, then use the documented menu
    # operation. Quarantine every loaded image path before Save All Images:
    # even an unexpectedly dirty unrelated image cannot write canonical art.
    private = out / 'saved-project'
    textures = private / 'art/textures'
    textures.mkdir(parents=True, exist_ok=True)
    for index, loaded in enumerate(bpy.data.images):
        if loaded.source in ('FILE', 'GENERATED'):
            loaded.filepath_raw = str(textures / ('image-%03d.png' % index))
    image.pixels[pixel:pixel + 4] = (0, 1, 1, 1)
    image.update()
    saved = bpy.ops.image.save_all_modified()
    assert saved == {'FINISHED'}, saved
    assert image.packed_file is not None and not image.is_dirty
    private_source = private / 'art/painted-guard.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(private_source), check_existing=False)
    assert original == {str(path): sha(path) for path in paths}, 'Paint check modified canonical source'
    report = {'passed': True, 'packed_authoring_image': True, 'painted_pixels_exported': True,
              'invalid_export_filename_rejected_before_write': True, 'character_roundtrip_identical': True,
              'canonical_files_unchanged': True, 'canonical_sha256': original,
              'private_save': {'image_save_operation': 'bpy.ops.image.save_all_modified',
                               'packed_after_save': True, 'source': str(private_source),
                               'source_sha256': sha(private_source),
                               'pixel_top_left_xy': [width // 2, height - 1 - height // 2],
                               'expected_rgba8': [0, 255, 255, 255]}}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


def host_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'tools'))
    from blender_assets import find_blender
    from PIL import Image
    blender = find_blender(args.blender)
    original = {str(path): sha(path) for path in canonical_paths()}
    out = ROOT / 'build/guard-atlas-study/paint-export'
    out.mkdir(parents=True, exist_ok=True)
    private = out / 'saved-project'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with (out / 'private-save.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(blender), '--factory-startup', '--background', '--disable-autoexec',
                        str(ROOT / 'art/guard.blend'), '--python-exit-code', '1', '--python', str(Path(__file__).resolve())],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120,
                       creationflags=flags)
    report = json.loads((out / 'report.json').read_bytes())
    # Stage byte-identical command and worker files under a private project so
    # the CLI's real output-directory guard can run unchanged. No writable
    # folder is created beneath canonical content/assets for this test.
    tool_hashes = {}
    for relative in ('tools/guard_assets.py', 'tools/blender_assets.py', 'tools/blender/guard_character.py'):
        copied = private / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, copied)
        assert sha(copied) == sha(ROOT / relative)
        tool_hashes[relative] = sha(copied)
    exported = private / 'content/assets/guard'
    with (out / 'fresh-cli-export.log').open('w', encoding='utf-8') as log:
        subprocess.run([sys.executable, str(private / 'tools/guard_assets.py'),
                        '--source', report['private_save']['source'], '--output', str(exported),
                        '--blender', str(blender)], cwd=private, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=120, creationflags=flags)
    painted = exported / 'guard-atlas-painted.png'
    with Image.open(painted) as png:
        actual = list(png.convert('RGBA').getpixel(tuple(report['private_save']['pixel_top_left_xy'])))
    assert actual == report['private_save']['expected_rgba8'], actual
    assert (exported / 'guard.character.json').read_bytes() == (ROOT / 'content/assets/guard/guard.character.json').read_bytes()
    assert original == {str(path): sha(path) for path in canonical_paths()}, 'Saved workflow changed canonical files'
    report['saved_file_workflow'] = {
        'passed': True, 'fresh_process_guard_assets_cli': True,
        'packed_paint_survives_save_reopen_export': True, 'exported_rgba8': actual,
        'character_roundtrip_identical': True, 'canonical_files_unchanged': True,
        'private_exported_texture': str(painted), 'private_exported_texture_sha256': sha(painted),
        'copied_cli_and_worker_sha256': tool_hashes,
        'logs': ['build/guard-atlas-study/paint-export/private-save.log',
                 'build/guard-atlas-study/paint-export/fresh-cli-export.log'],
    }
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    blender_main() if bpy is not None else host_main()
