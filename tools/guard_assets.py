"""Export the saved guard rig/Actions, or open/create its Blender study source."""
from pathlib import Path
import argparse
import json
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'art/guard.blend')
    parser.add_argument('--output',type=Path,default=ROOT/'content/assets/guard')
    parser.add_argument('--blender',type=Path)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--open',action='store_true')
    group.add_argument('--make-demo',action='store_true',help='Create new source; refuses to replace an existing .blend')
    group.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--create-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        source=args.source.resolve();output=args.output.resolve()
        if source.suffix.lower()!='.blend': raise ValueError('Source must be a .blend file')
        assets=(ROOT/'content/assets').resolve()
        if not output.is_relative_to(assets) or output==assets: raise ValueError('Output must be a named directory below content/assets')
        if args.make_demo or args.create_worker:
            if not source.is_relative_to((ROOT/'art').resolve()): raise ValueError('New demo source must stay below art/')
            if source.exists(): raise ValueError('Refusing to overwrite existing source: '+str(source))
        elif not source.is_file(): raise ValueError('Saved source does not exist: '+str(source))
        if args.worker:
            functions=runpy.run_path(str(ROOT/'tools/blender/guard_character.py'),run_name='guard_export')
            result=functions['create'](source,output) if args.create_worker else functions['export'](output=output)
            print(json.dumps(result,indent=2));return 0
        # Blender runs --python scripts without adding their directory to
        # sys.path. The worker needs no host-side executable discovery helper.
        try:
            from blender_assets import find_blender
        except ModuleNotFoundError:
            from tools.blender_assets import find_blender
        blender=find_blender(args.blender)
        if args.open:
            subprocess.Popen([str(blender),str(source)],cwd=ROOT);return 0
        command=[str(blender),'--factory-startup','--background','--disable-autoexec','--python-exit-code','1']
        if not args.make_demo: command.append(str(source))
        command += ['--python',str(Path(__file__).resolve()),'--','--worker','--source',str(source),'--output',str(output)]
        if args.make_demo: command.append('--create-worker')
        subprocess.run(command,cwd=ROOT,check=True)
        return 0
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Guard asset operation failed: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':
    raise SystemExit(main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None))
