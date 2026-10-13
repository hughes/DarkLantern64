"""Explicit host-only budgets for large imported source studies.

The saved document must opt in, and the caller must be an authoring operation.
ROM and simulation validation retain their normal capacities and reject a study.
"""
STUDY_PROFILE = 'external-scale-study'
STUDY_LIMITS = dict(models=8192, entities=65536, materials=512, lights=4096,
                    colliders=32768, enemies=128, interactive_objects=4096,
                    scene_vertices=262144, scene_triangles=131072,
                    level_bytes=128 * 1024 * 1024, exchange_bytes=128 * 1024 * 1024)
STUDY_PLAY_REASON = ('This external-scale-study is an authoring-only source study. '
                     'View it in the editor or Blender; use a bounded runtime level for Native Play or ROM builds.')


def is_study(document):
    return isinstance(document, dict) and document.get('authoring_profile') == STUDY_PROFILE


def content_limits(document, geometry_limits, *, authoring=False):
    if not isinstance(document, dict):
        raise ValueError('Expected a level document object')
    result = dict(geometry_limits, entities=256, materials=64, lights=16, colliders=256,
                  enemies=16, interactive_objects=64, level_bytes=8 * 1024 * 1024,
                  exchange_bytes=8 * 1024 * 1024)
    profile = document.get('authoring_profile')
    if profile is None:
        return result
    if profile != STUDY_PROFILE:
        raise ValueError('authoring_profile: unsupported profile; expected external-scale-study')
    if not authoring:
        raise ValueError(STUDY_PLAY_REASON)
    result.update(STUDY_LIMITS)
    return result
