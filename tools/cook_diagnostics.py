"""Source-addressable estimates; renderer choices mirror lighting_policy.c.

These records describe cooked inputs and fixed capacities, not measured frame
time or a certification of total target memory. Host cache sizes are separate.
"""
from __future__ import annotations

import hashlib
import json
import copy


CAPS = dict(region_count=32, entity_count=2048, material_count=64, directory_bytes=65536,
            resident_regions=5, triangle_count=1440, group_count=64,
            floor_count=128, collision_records=400, model_count=128,
            scene_vertices=6144, scene_triangles=4096, chunk_bytes=65536)


def source_identity(document):
    value = dict(document)
    for key in ("assets", "materials", "entities"):
        if isinstance(value.get(key), list):
            value[key] = sorted(value[key], key=lambda row: row.get("id", ""))
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def lighting_decision(*, night, role, animated=False, triangles=0,
                      inline_payload=False, incomplete_inline_payload=False,
                      doors=0, bake_enabled=True, standalone_status="unconfigured"):
    """The initial and later door-change decisions are deliberately separate."""
    eligible = bool(night and role not in ("guard", "objective") and not animated)
    inline = bool(night and bake_enabled and doors <= 1 and inline_payload)
    if not night:
        mode = {"guard": "legacy_actor_probes", "objective": "legacy_objective_emissive"}.get(role, "legacy_scalar")
        reason = role if role in ("guard", "objective") else "animated_mesh" if animated else "legacy_environment"
    elif role in ("guard", "objective"):
        mode, reason = ("actor_body_probes" if role == "guard" else "objective_probe"), role
    elif animated:
        mode, reason = "live_night", "animated_mesh"
    elif inline:
        mode, reason = "inline_two_state", "inline_payload"
    elif bake_enabled and standalone_status == "loaded":
        mode, reason = "standalone_baked", "standalone_asset"
    else:
        mode = "live_night"
        reason = ("disabled_build" if not bake_enabled else "independent_doors" if doors > 1
                  else "incomplete_inline_payload" if incomplete_inline_payload
                  else "standalone_rejected" if standalone_status == "rejected" else "night_without_bake")
    change_mode, change_reason = mode, reason
    if eligible:
        if doors > 1:
            change_mode, change_reason = "live_night", "independent_doors"
        elif mode == "live_night":
            change_mode, change_reason = "runtime_two_state", "cached_single_door_states"
    return dict(mode=mode, reason=reason, change_mode=change_mode, change_reason=change_reason,
                static_eligible=eligible, inline_eligible=inline, triangles=triangles)


def counts(entries, *, changed=False):
    modes, cases = {}, {}
    for row in entries:
        mode, reason = (row["change_mode"], row["change_reason"]) if changed else (row["mode"], row["reason"])
        value = modes.setdefault(mode, dict(models=0, triangles=0))
        value["models"] += 1
        value["triangles"] += row["triangles"]
        value = cases.setdefault((mode, reason), dict(mode=mode, reason=reason, models=0, triangles=0))
        value["models"] += 1
        value["triangles"] += row["triangles"]
    return modes, [cases[key] for key in sorted(cases)]


def renderer_report(entries, *, door_ids, identity, standalone_status="unconfigured", bake_enabled=True):
    modes, cases = counts(entries)
    changes, change_cases = counts(entries, changed=True)
    return dict(schema_version=1, basis="cooked_prediction", identity=identity,
                door_object_ids=sorted(door_ids), door_object_count=len(door_ids),
                settings=dict(bake_enabled=bake_enabled, standalone_status=standalone_status),
                model_entries=entries, mode_counts=modes, cases=cases,
                change_mode_counts=changes, change_cases=change_cases, actual=None,
                scope="All declared models; working-set rows bound potential membership. Camera visibility, current actor membership and frame time require runtime measurement.")


def resident_renderer(document, state, *, standalone_status="unconfigured", bake_enabled=True):
    doors = [row["id"] for row in state["objects"] if row["kind"] == "door"]
    entries = []
    for index, entity in enumerate(state["models"]):
        mesh = state["meshes"][entity["model"]]
        entries.append(dict(entity_id=entity["id"], model_index=index, source_entity_ids=[entity["id"]],
            role=entity["kind"], mesh=entity["model"], material=entity["material"], inline_payload=False,
            environment_enabled=document.get("environment") is not None, animated_mesh=entity["model"] in state["characters"],
            decision_contributors=doors if len(doors)>1 else [],
            **lighting_decision(night=document.get("environment") is not None, role=entity["kind"],
                animated=entity["model"] in state["characters"], triangles=len(mesh["indices"]) // 3,
                doors=len(doors), bake_enabled=bake_enabled, standalone_status=standalone_status)))
    return renderer_report(entries, door_ids=doors, standalone_status=standalone_status, bake_enabled=bake_enabled,
        identity=dict(renderer_source_sha256=state.get("renderer_source_sha256", source_identity(document)),
                      source_sha256=None, source_file_sha256=None, package_sha256=None, build_id=None))


def apply_renderer_settings(report, *, bake_enabled=None, standalone_status=None):
    """Return a fresh prediction for explicit build/loader assumptions.

    Build settings never alter package identity or imply measured evidence.
    The editor can use this on draft diagnostics before launching a renderer.
    """
    result = copy.deepcopy(report)
    settings = dict(result["settings"])
    if bake_enabled is not None:
        settings["bake_enabled"] = bool(bake_enabled)
    if standalone_status is not None:
        if standalone_status not in ("loaded", "rejected", "unconfigured"):
            raise ValueError("Unknown standalone lighting loader status")
        settings["standalone_status"] = standalone_status
    entries = result["model_entries"]
    for row in entries:
        row.update(lighting_decision(night=row["environment_enabled"], role=row["role"],
            animated=row["animated_mesh"], triangles=row["triangles"], inline_payload=row["inline_payload"],
            incomplete_inline_payload=row.get("incomplete_inline_payload", False),
            doors=result["door_object_count"], **settings))
    result["settings"] = settings
    result["mode_counts"], result["cases"] = counts(entries)
    result["change_mode_counts"], result["change_cases"] = counts(entries, changed=True)
    result["actual"] = None
    return result


def witness(row):
    return {key: row.get(key, [] if key != "anchor" else None)
            for key in ("anchor", "gates", "source_regions", "regions", "groups", "contributors")}


def bridge_report(report):
    """Collect every adapter failure without invalidating portable package bytes.

    Recompute counts from current region rows: callers may inspect a modified
    report and validation must never trust a stale attached diagnostic.
    """
    caps = dict(CAPS)
    if report.get("visibility") != "authored-sets":
        caps["region_count"] = 2
    regions = report["regions"]
    by_id = {row["id"]: row for row in regions}
    maxima, failures = {}, []
    rows = []

    def record(metric, usage, context, message, *, invalid=False):
        cap = caps.get(metric)
        current = maxima.get(metric)
        item = dict(witness(context), usage=usage, cap=cap)
        if current is None or usage > current["usage"]:
            maxima[metric] = dict(usage=usage, cap=cap, witnesses=[item])
        elif usage == current["usage"]:
            current["witnesses"].append(item)
        if invalid or cap is not None and usage > cap:
            failures.append(dict(item, metric=metric, code="capacity_" + metric, message=message))

    world = dict(anchor=next((row["source_id"] for row in regions if row["id"] == report["entry_region"]), None),
        gates=[row["id"] for row in report.get("transitions", [])],
        source_regions=[row["source_id"] for row in regions], regions=list(by_id),
        groups=[group for row in regions for group in row.get("groups", [])],
        contributors=[dict(id=row["source_id"], kind=row["kind"], home_region=by_id[row["home_region"]]["source_id"])
                      for row in report["entity_ids"]])
    authored = report.get("visibility") == "authored-sets"
    record("region_count", len(regions), world,
           "Authored ROM policy requires 1..32 regions" if authored else "Stream ROM bridge requires exactly 2 regions",
           invalid=not (1 <= len(regions) <= 32 if authored else len(regions) == 2))
    record("entity_count", len(report["entity_ids"]), world, "Stream directory exceeds 2048 entities or 65536 bytes")
    record("directory_bytes", report["directory_bytes"], world, "Stream directory exceeds 2048 entities or 65536 bytes")
    record("material_count", len(report["material_ids"]), world, "Stream ROM bridge permits at most 64 materials")
    for working in report["working_sets"]:
        selected = [by_id[ident] for ident in working["regions"]]
        triangles = sum(row["triangle_count"] for row in selected)
        groups = sum(len(row.get("groups", row["material_ids"])) for row in selected)
        floors = sum(row.get("floor_count", 0) for row in selected)
        boxes = sum(row["collider_count"] for row in selected)
        usage = dict(resident_regions=len(selected), triangle_count=triangles, group_count=groups,
            floor_count=floors, collision_records=boxes + floors + working["barrier_count"],
            model_count=working["model_count"] - working["group_count"] + groups,
            scene_vertices=working["scene_vertices"] - working["triangle_count"] * 3 + triangles * 3,
            scene_triangles=working["scene_triangles"] - working["triangle_count"] + triangles)
        renderer = copy.deepcopy(working.get("renderer", {}))
        entries = renderer.get("model_entries", [])
        cache = usage["scene_triangles"] * 48 if any(row.get("environment_enabled") for row in entries) and any(
            row["mode"] != "inline_two_state" for row in entries) else 0
        renderer["renderer_night_cache_bytes"] = cache
        usage.update(chunk_total_bytes=sum(row["chunk_bytes"] for row in selected), renderer_night_cache_bytes=cache)
        label = "Working set " + ",".join(working["source_regions"]) + " from " + working["anchor"]
        messages = dict(resident_regions="Reachable working set exceeds 5 resident region slots",
            triangle_count=label + ": at most 1440 resident static triangles",
            group_count=label + ": at most 64 nonempty region/material groups",
            floor_count=label + ": at most 128 floor triangles",
            collision_records=label + ": boxes + floors + unavailable-region barriers exceed 400")
        for metric, value in usage.items():
            record(metric, value, working, messages.get(metric, label + ": rendered models/vertices/triangles exceed 128/6144/4096"))
        rows.append(dict(witness(working), usage=usage, caps={metric: caps.get(metric) for metric in usage},
            renderer=renderer, scene_triangles=usage["scene_triangles"], scene_vertices=usage["scene_vertices"],
            violations=[metric for metric, value in usage.items() if metric in caps and value > caps[metric]]))
    # A declared union can exceed the subset rows in legacy reports.
    if report["max_resident_regions"] > maxima["resident_regions"]["usage"]:
        record("resident_regions", report["max_resident_regions"], world, "Reachable working set exceeds 5 resident region slots")
    for region in regions:
        context = dict(anchor=region["source_id"], gates=[], source_regions=[region["source_id"]],
            regions=[region["id"]], groups=region.get("groups", []), contributors=region.get("contributors", []))
        record("chunk_bytes", region["chunk_bytes"], context, "Stream ROM bridge permits at most 65536 bytes per region chunk")
    return dict(schema_version=1, basis="cooked_estimate", valid=not failures, caps=caps,
                working_sets=rows, maxima=maxima, failures=failures)
