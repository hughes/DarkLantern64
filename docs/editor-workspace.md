# Editor workspace

The default workspace has **Objects**, **Viewport**, and **Inspector**. Choose an
object in the list or viewport to select its inspector, bounding box and gizmo.
Ctrl-click adds to the project selection; the gizmo acts on the active object.
Use **Blockout & Groups** for a transform of the whole selection.

| Task | Entry point |
| --- | --- |
| Open a level; save | File |
| Create, duplicate or manage levels | File → Levels |
| Add primitives, props or gameplay objects | Create |
| Undo, redo, duplicate, delete | Edit |
| Translate, rotate or scale gizmo | View → Transform mode; Ctrl+T cycles |
| Lighting or animation preview | Window → Environment / Animation |
| Test the current draft | Play on the menu bar |
| Pause, step, restart or stop | Menu bar during Play |
| Inspect runtime state | Inspect during Play; Window → Diagnostics → Play Inspector |
| Build or launch the console version | Build |
| Reopen a closed panel | Window |
| Restore the three-panel workspace | Window → Restore default workspace |
| Keyboard and camera controls | Help → Controls |

Interaction and collision fields are sections of the selected object's
Inspector. The separate Interactions panel and duplicate engine Entity List /
Selected Entity panels are removed from the project workspace. Asset import,
blockout, animation, environment, audio planning and diagnostics remain available
on demand. Collision and guard-sight overlays start off and can be enabled in View.

Visibility persists in `.dev/editor/project/workspace-v2.json`; docking persists
in `layout-project-v2.ini` alongside it. Earlier layouts remain untouched. Play
temporarily hides authoring panels and restores their preferences on Stop.

## Engine and project responsibilities

LightEngine owns docking, viewport rendering, selection/bounds/gizmos, transform
modes and host menu hooks. A host may replace the stock menu in Edit and Play,
hide duplicate engine editors and opt into renderer resource diagnostics.
Programmatic selection suppresses the callback echo to the host; viewport picks
still notify it. Picking is confined to the viewport and editing gizmos are
disabled in Play.

The engine hooks are published for review in [LightEngine PR #19](https://github.com/hughes/LightEngine/pull/19).

DarkLantern owns panel names/defaults, canonical source IDs, level management,
guard and interaction fields, ROM builds, audio planning and the runtime
inspector. These semantics are not introduced into LightEngine.

## Inspection and verification

`get_layout` reports panel visibility, active windows, docking, menus and the
actual engine `viewport_selection` ID. Join that ID to `inspect_scene` source
IDs to check selection across the boundary. `set_panel` accepts
`{"name":"Play Inspector","visible":true}`; `reset_layout` restores defaults.
These commands work through the existing bounded `tools/editorctl.py` interface.

`python tools/verify_workspace.py` launches a private copied project and checks
defaults, every panel's visibility, selection synchronization, canonical preview
movement/undo, Play transitions and persistence through restart. It checks that
saved content is unchanged. Its transform check uses the preview command path;
it does not synthesize a mouse drag.
