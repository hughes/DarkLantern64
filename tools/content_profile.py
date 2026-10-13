"""Explicit host-only budgets for large imported source studies.

The saved document must opt in, and the caller must be an authoring operation.
ROM and simulation validation retain their normal capacities and reject a study.
"""
STUDY_PROFILE = 'external-scale-study'
STUDY_LIMITS = dict(models=8192, entities=65536, materials=512, lights=4096, floors=32768,
                    colliders=32768, enemies=128, interactive_objects=4096,
                    scene_vertices=262144, scene_triangles=131072,
                    level_bytes=128 * 1024 * 1024, exchange_bytes=128 * 1024 * 1024)
STUDY_PLAY_REASON = ('This external-scale-study is an authoring-only source study. '
                     'View it in the editor or Blender; use a bounded runtime level for Native Play or ROM builds.')
STREAM_PROFILE = 'streamed-mission'
# Total authoring data, not an allocation for a resident runtime scene. The
# mission cooker separately checks every reachable bounded working set.
STREAM_LIMITS = dict(models=1024, entities=2048, colliders=2048, floors=2048,
                     scene_vertices=131072, scene_triangles=32768,
                     level_bytes=64 * 1024 * 1024, exchange_bytes=64 * 1024 * 1024)
STREAM_PLAY_REASON = ('This streamed-mission source requires a mission partition and bounded working-set cooking. '
                      'Use the mission bundle for ROM builds; resident compilation and Native Play are unavailable.')


def is_study(document):
    return isinstance(document, dict) and document.get('authoring_profile') == STUDY_PROFILE


def is_streamed(document):
    return isinstance(document, dict) and document.get('authoring_profile') == STREAM_PROFILE


def content_limits(document, geometry_limits, *, authoring=False, streaming=False):
    if not isinstance(document, dict):
        raise ValueError('Expected a level document object')
    result = dict(geometry_limits, entities=256, materials=64, lights=16, colliders=256, floors=128,
                  enemies=16, interactive_objects=64, level_bytes=8 * 1024 * 1024,
                  exchange_bytes=8 * 1024 * 1024)
    profile = document.get('authoring_profile')
    if profile is None:
        return result
    if profile == STREAM_PROFILE:
        if not (authoring or streaming):
            raise ValueError(STREAM_PLAY_REASON)
        result.update(STREAM_LIMITS)
        return result
    if profile != STUDY_PROFILE:
        raise ValueError('authoring_profile: unsupported profile; expected external-scale-study or streamed-mission')
    if not authoring:
        raise ValueError(STUDY_PLAY_REASON)
    result.update(STUDY_LIMITS)
    return result
