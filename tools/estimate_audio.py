#!/usr/bin/env python3
"""Estimate planned audio allocations; this does not inspect cooked assets or RAM.

Schema 1 is deliberately strict. All sizes are bytes; rates are sample frames per
second and window_ms is milliseconds at the declared maximum playback rate.
Assets describe a catalogue, voices describe independent playback reservations,
contexts select reservations/resident banks/effects, and transitions union them.
An explicit persistent buffer pool remains allocated when voices stop. Its sizes
are manual provisioning, not a reconstruction of libdragon's allocator defaults.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODEL_VERSION = "1.0.0"
MAX_VALUE = 2**40


class AudioBudgetError(ValueError):
    """Invalid planning manifest."""


def require(condition, message):
    if not condition:
        raise AudioBudgetError(message)


def fields(value, names, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    missing, extra = set(names) - value.keys(), value.keys() - set(names)
    require(not missing, f"{label}: missing fields {sorted(missing)}")
    require(not extra, f"{label}: unknown fields {sorted(extra)}")


def integer(value, label, minimum=0, maximum=MAX_VALUE):
    require(type(value) is int and minimum <= value <= maximum,
            f"{label}: expected integer in [{minimum}, {maximum}]")
    return value


def string(value, label):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 2048,
            f"{label}: expected nonempty text (at most 2048 characters)")


def identifier(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value),
            f"{label}: expected stable ASCII identifier")


def array(value, label, maximum=256):
    require(isinstance(value, list) and len(value) <= maximum,
            f"{label}: expected list of at most {maximum} entries")


def references(value, known, label):
    array(value, label)
    for item in value:
        identifier(item, label)
        require(item in known, f"{label}: unknown reference {item}")
    require(len(set(value)) == len(value), f"{label}: duplicate reference")


def catalogue(items, names, label):
    array(items, label)
    result = {}
    for item in items:
        fields(item, names, label)
        identifier(item["id"], f"{label}.id")
        require(item["id"] not in result, f"{label}: duplicate ID {item['id']}")
        string(item["label"], f"{label}.label")
        result[item["id"]] = item
    return result


def round_up(value, quantum):
    return (value + quantum - 1) // quantum * quantum


def validate(data):
    fields(data, ("schema_version", "status", "description", "output", "budget",
                  "buffer_pool", "allowances", "assets", "voices", "effects",
                  "contexts", "transitions"), "manifest")
    integer(data["schema_version"], "schema_version", 1, 1)
    require(data["status"] == "planning_only", "status: must be planning_only")
    string(data["description"], "description")
    output = data["output"]
    fields(output, ("sample_rate_hz", "buffer_frames", "buffer_count", "padding_bytes", "note"), "output")
    integer(output["sample_rate_hz"], "output.sample_rate_hz", 3000, 192000)
    integer(output["buffer_frames"], "output.buffer_frames", 2, 1048576)
    require(output["buffer_frames"] % 2 == 0, "output.buffer_frames: stereo PCM DMA requires an even frame count")
    integer(output["buffer_count"], "output.buffer_count", 2, 32)
    integer(output["padding_bytes"], "output.padding_bytes")
    string(output["note"], "output.note")
    budget = data["budget"]
    if budget is not None:
        fields(budget, ("ram_bytes", "status", "note"), "budget")
        integer(budget["ram_bytes"], "budget.ram_bytes", 1)
        require(budget["status"] == "illustrative", "budget.status: must be illustrative; no accepted budget is implied")
        string(budget["note"], "budget.note")
    pool = data["buffer_pool"]
    fields(pool, ("decoded_bytes", "decoder_workspace_bytes", "channel_capacity", "retention", "note"), "buffer_pool")
    for name in ("decoded_bytes", "decoder_workspace_bytes"):
        integer(pool[name], f"buffer_pool.{name}")
    integer(pool["channel_capacity"], "buffer_pool.channel_capacity", 1, 32)
    require(pool["retention"] == "persistent", "buffer_pool.retention: must be persistent")
    string(pool["note"], "buffer_pool.note")
    allowances = data["allowances"]
    fields(allowances, ("runtime_bytes", "allocator_bytes", "unmeasured_bytes", "reserve_bytes", "unmeasured_items", "note"), "allowances")
    for name in ("runtime_bytes", "allocator_bytes", "unmeasured_bytes", "reserve_bytes"):
        integer(allowances[name], f"allowances.{name}")
    array(allowances["unmeasured_items"], "allowances.unmeasured_items")
    for item in allowances["unmeasured_items"]:
        string(item, "allowances.unmeasured_items")
    require(len(set(allowances["unmeasured_items"])) == len(allowances["unmeasured_items"]), "allowances.unmeasured_items: duplicate item")
    require(not allowances["unmeasured_bytes"] or allowances["unmeasured_items"], "allowances: name costs covered by unmeasured_bytes")
    string(allowances["note"], "allowances.note")
    assets = catalogue(data["assets"], ("id", "label", "codec", "sample_rate_hz", "sample_frames", "channels", "bits_per_sample", "encoded_payload_bytes", "container_bytes", "residency", "resident_padding_bytes", "shared_state_bytes", "decoder_workspace_bytes", "decode_block_frames", "note"), "assets")
    for asset in assets.values():
        tag = f"asset {asset['id']}"
        require(asset["codec"] in ("pcm", "vadpcm", "opus"), f"{tag}: unsupported codec")
        integer(asset["sample_rate_hz"], f"{tag}.sample_rate_hz", 1, 192000)
        integer(asset["sample_frames"], f"{tag}.sample_frames", 1)
        integer(asset["channels"], f"{tag}.channels", 1, 2)
        integer(asset["bits_per_sample"], f"{tag}.bits_per_sample", 8, 16)
        require(asset["bits_per_sample"] in (8, 16), f"{tag}: bits_per_sample must be 8 or 16")
        for name in ("container_bytes", "resident_padding_bytes", "shared_state_bytes", "decoder_workspace_bytes"):
            integer(asset[name], f"{tag}.{name}")
        integer(asset["decode_block_frames"], f"{tag}.decode_block_frames", 1, 65536)
        require(asset["residency"] in ("stream", "encoded", "decoded"), f"{tag}: unsupported residency")
        string(asset["note"], f"{tag}.note")
        if asset["codec"] == "opus":
            integer(asset["encoded_payload_bytes"], f"{tag}: Opus needs declared encoded_payload_bytes", 1)
            require(asset["decoder_workspace_bytes"] > 0, f"{tag}: Opus needs declared nonzero decoder_workspace_bytes")
            require(asset["sample_rate_hz"] == 48000 and asset["decode_block_frames"] == 960,
                    f"{tag}: checked Opus converter uses 48000 Hz / 960-frame blocks")
        else:
            require(asset["encoded_payload_bytes"] is None, f"{tag}: PCM/VADPCM payload is calculated; use null")
            require(asset["decode_block_frames"] == (32 if asset["codec"] == "vadpcm" else 1), f"{tag}: expected codec block size")
        require(asset["codec"] == "pcm" or asset["bits_per_sample"] == 16, f"{tag}: compressed formats decode to 16 bits")
    voices = catalogue(data["voices"], ("id", "label", "asset", "window_ms", "max_playback_rate_hz", "buffer_bits", "padding_bytes", "note"), "voices")
    for voice in voices.values():
        tag = f"voice {voice['id']}"
        references([voice["asset"]], assets, f"{tag}.asset")
        asset = assets[voice["asset"]]
        integer(voice["window_ms"], f"{tag}.window_ms", 1, 60000)
        integer(voice["max_playback_rate_hz"], f"{tag}.max_playback_rate_hz", asset["sample_rate_hz"], 384000)
        integer(voice["buffer_bits"], f"{tag}.buffer_bits", asset["bits_per_sample"], 16)
        require(voice["buffer_bits"] in (8, 16), f"{tag}: buffer_bits must be 8 or 16")
        integer(voice["padding_bytes"], f"{tag}.padding_bytes")
        string(voice["note"], f"{tag}.note")
    effects = catalogue(data["effects"], ("id", "label", "sample_rate_hz", "delay_ms", "channels", "sample_bytes", "state_bytes", "scratch_bytes", "note"), "effects")
    for effect in effects.values():
        for name, low, high in (("sample_rate_hz", 1, 192000), ("delay_ms", 0, 60000), ("channels", 1, 32), ("sample_bytes", 1, 8), ("state_bytes", 0, MAX_VALUE), ("scratch_bytes", 0, MAX_VALUE)):
            integer(effect[name], f"effect {effect['id']}.{name}", low, high)
        string(effect["note"], f"effect {effect['id']}.note")
    contexts = catalogue(data["contexts"], ("id", "label", "voices", "resident_assets", "effects", "note"), "contexts")
    require(bool(contexts), "contexts: at least one planning context is required")
    for context in contexts.values():
        for field, known in (("voices", voices), ("resident_assets", assets), ("effects", effects)):
            references(context[field], known, f"context {context['id']}.{field}")
        for key in context["resident_assets"]:
            require(assets[key]["residency"] != "stream", f"context {context['id']}: stream asset {key} cannot be a resident bank")
        for key in context["voices"]:
            asset = assets[voices[key]["asset"]]
            require(asset["residency"] == "stream" or asset["id"] in context["resident_assets"], f"context {context['id']}: voice {key} needs resident asset {asset['id']}")
        string(context["note"], f"context {context['id']}.note")
    transitions = catalogue(data["transitions"], ("id", "label", "contexts", "extra_bytes", "note"), "transitions")
    require(not (contexts.keys() & transitions.keys()), "scenario IDs must be unique across contexts and transitions")
    for transition in transitions.values():
        references(transition["contexts"], contexts, f"transition {transition['id']}.contexts")
        require(len(transition["contexts"]) >= 2, "transition: needs at least two different contexts")
        integer(transition["extra_bytes"], "transition.extra_bytes")
        string(transition["note"], "transition.note")
    return assets, voices, effects, contexts, transitions


def estimate_audio(data):
    """Return deterministic JSON-compatible planning estimates; never 'pass' RAM."""
    assets, voices, effects, contexts, transitions = validate(data)
    asset_rows, voice_rows, effect_rows = {}, {}, {}
    for key, asset in sorted(assets.items()):
        decoded = asset["sample_frames"] * asset["channels"] * asset["bits_per_sample"] // 8
        payload = asset["encoded_payload_bytes"]
        basis = "declared_payload"
        if asset["codec"] == "pcm":
            payload, basis = decoded, "pcm_formula"
        elif asset["codec"] == "vadpcm":
            payload = round_up(asset["sample_frames"], 32) // 16 * 9 * asset["channels"]
            basis = "vadpcm_formula_padded_to_32_frames"
        resident = 0
        if asset["residency"] != "stream":
            resident = decoded if asset["residency"] == "decoded" else payload + asset["container_bytes"]
            resident = round_up(resident + asset["resident_padding_bytes"], 16)
        asset_rows[key] = {"id": key, "codec": asset["codec"], "encoded_payload_bytes": payload,
                           "container_bytes": asset["container_bytes"], "decoded_asset_bytes": decoded,
                           "resident_bytes": resident, "payload_basis": basis,
                           "residency": asset["residency"], "shared_state_bytes": round_up(asset["shared_state_bytes"], 16)}
    for key, voice in sorted(voices.items()):
        asset = assets[voice["asset"]]
        frames = (voice["max_playback_rate_hz"] * voice["window_ms"] + 999) // 1000
        frame_bytes = asset["channels"] * voice["buffer_bits"] // 8
        # Lookahead is requested BEFORE the decoder rounds to whole blocks.
        frames += (voice["padding_bytes"] + frame_bytes - 1) // frame_bytes
        frames = round_up(frames, asset["decode_block_frames"])
        window = round_up(frames * frame_bytes, 16)
        workspace = 0 if asset["residency"] == "decoded" else round_up(asset["decoder_workspace_bytes"], 16)
        voice_rows[key] = {"id": key, "asset": voice["asset"], "mixer_channels": asset["channels"],
                           "decoded_window_frames": frames, "decoded_window_bytes": window,
                           "decoder_workspace_bytes": workspace}
    for key, effect in sorted(effects.items()):
        frames = (effect["sample_rate_hz"] * effect["delay_ms"] + 999) // 1000
        delay = round_up(frames * effect["channels"] * effect["sample_bytes"], 16)
        state, scratch = round_up(effect["state_bytes"], 16), round_up(effect["scratch_bytes"], 16)
        effect_rows[key] = {"id": key, "delay_bytes": delay, "state_bytes": state,
                            "scratch_bytes": scratch, "total_bytes": delay + state + scratch}
    output, pool, allowance = data["output"], data["buffer_pool"], data["allowances"]
    output_bytes = round_up(output["buffer_frames"] * 4 + output["padding_bytes"], 16) * output["buffer_count"]
    budget = data["budget"]["ram_bytes"] if data["budget"] is not None else None
    scenarios = []
    specs = [(x, "context", [x["id"]], 0) for x in contexts.values()]
    specs += [(x, "transition", x["contexts"], x["extra_bytes"]) for x in transitions.values()]
    for spec, kind, context_ids, extra in sorted(specs, key=lambda entry: entry[0]["id"]):
        selected = [contexts[x] for x in context_ids]
        voice_ids = sorted({x for context in selected for x in context["voices"]})
        resident_ids = sorted({x for context in selected for x in context["resident_assets"]})
        asset_ids = sorted(set(resident_ids) | {voices[x]["asset"] for x in voice_ids})
        effect_ids = sorted({x for context in selected for x in context["effects"]})
        decoded = sum(voice_rows[x]["decoded_window_bytes"] for x in voice_ids)
        decoder = sum(voice_rows[x]["decoder_workspace_bytes"] for x in voice_ids)
        channels = sum(voice_rows[x]["mixer_channels"] for x in voice_ids)
        decoded_pool, decoder_pool = round_up(pool["decoded_bytes"], 16), round_up(pool["decoder_workspace_bytes"], 16)
        shortage = max(0, decoded - decoded_pool) + max(0, decoder - decoder_pool)
        parts = {"resident_asset_bytes": sum(asset_rows[x]["resident_bytes"] for x in resident_ids),
                 "shared_asset_state_bytes": sum(asset_rows[x]["shared_state_bytes"] for x in asset_ids),
                 "decoded_window_bytes": decoded, "decoder_workspace_bytes": decoder,
                 "retained_decoded_pool_bytes": max(0, decoded_pool - decoded),
                 "retained_decoder_pool_bytes": max(0, decoder_pool - decoder),
                 "output_buffer_bytes": output_bytes,
                 "dsp_bytes": sum(effect_rows[x]["total_bytes"] for x in effect_ids),
                 "runtime_bytes": allowance["runtime_bytes"], "allocator_bytes": allowance["allocator_bytes"],
                 "unmeasured_allowance_bytes": allowance["unmeasured_bytes"],
                 "transition_extra_bytes": extra, "reserve_bytes": allowance["reserve_bytes"]}
        total = sum(parts.values())
        state = "no_budget" if budget is None else ("over_budget" if total > budget else "within_budget_unverified")
        if shortage or channels > pool["channel_capacity"]:
            state = "pool_exceeded"
        scenarios.append({"id": spec["id"], "label": spec["label"], "kind": kind,
                          "context_ids": sorted(context_ids), "voice_ids": voice_ids, "asset_ids": asset_ids,
                          "resident_asset_ids": resident_ids, "effect_ids": effect_ids, "mixer_channels": channels,
                          "components": parts, "required_decoded_window_bytes": decoded,
                          "required_decoder_workspace_bytes": decoder, "pool_shortfall_bytes": shortage,
                          "channel_shortfall": max(0, channels - pool["channel_capacity"]),
                          "total_ram_bytes": total, "budget_bytes": budget,
                          "remaining_bytes": None if budget is None else budget - total,
                          "budget_status": state, "conditional_budget_state": state,
                          "coverage": {"complete": not allowance["unmeasured_items"], "measured": False,
                                       "unmeasured_items": sorted(allowance["unmeasured_items"])}})
    peak = max(scenarios, key=lambda row: row["total_ram_bytes"])
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    return {"schema_version": 1, "estimator_model_version": MODEL_VERSION,
            "kind": "audio_memory_estimate", "status": "planning_only",
            "source_sha256": hashlib.sha256(canonical).hexdigest(),
            "description": data["description"],
            "provenance": {"assets": "declared example metadata; no waveform files inspected",
                           "allocation": "planning formulas and explicit persistent pool; not a runtime heap measurement",
                           "units": {"memory": "bytes", "sample_rate_hz": "sample frames/second", "window_ms": "milliseconds"},
                           "alignment_bytes": 16,
                           "sdk_reference": "libdragon 7a82f8e50e82ad4601d530801630d8bd0d2fcd00"},
            "assumptions": {"output": output, "budget": data["budget"], "buffer_pool": pool, "allowances": allowance,
                            "output_format": "signed 16-bit stereo PCM",
                            "output_buffer_duration_ms": output["buffer_frames"] * 1000 / output["sample_rate_hz"],
                            "notes": ["Voice windows use explicit playback rate limits, not authored source rate alone.",
                                      "Voice padding_bytes requests decoded lookahead before codec-block and 16-byte allocation rounding.",
                                      "Pool allocation remains resident in every context and transition, including silent contexts.",
                                      "Different voice IDs reserve independent simultaneous playback even when asset IDs match.",
                                      "shared_state_bytes means immutable reusable metadata only; mutable decode history/open-player state belongs to per-playback decoder_workspace_bytes.",
                                      "Resident decoded assets still require per-playback mixer windows in the checked SDK.",
                                      "Shared effect IDs denote one allocation; two independent room tails need different IDs.",
                                      "Resident banks and effect lifetimes must include retained tails/prefetch explicitly in contexts or transitions.",
                                      "Runtime allowance must include compiled audio code, stacks, queues, pointer tables and unlisted state.",
                                      "Window sizing is a proposed policy; poll size, pitch limits, decoder lookahead and exact SDK allocation need verification.",
                                      "Positive headroom is conditional planning headroom, not verified fit or whole-machine free memory."]},
            "summary": {"rom_encoded_payload_bytes": sum(x["encoded_payload_bytes"] for x in asset_rows.values()),
                        "rom_container_overhead_bytes": sum(x["container_bytes"] for x in asset_rows.values()),
                        "peak_ram_bytes": peak["total_ram_bytes"], "peak_scenario_id": peak["id"], "budget_bytes": budget},
            "assets": list(asset_rows.values()), "voices": list(voice_rows.values()),
            "effects": list(effect_rows.values()), "scenarios": scenarios}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON: duplicate key {key}")
        result[key] = value
    return result


def build_report(source=ROOT / "content/audio_budget.json", output=ROOT / "build/generated/audio_memory_report.json"):
    """Validate before replacing the report; unchanged deterministic output is kept."""
    source, output = Path(source), Path(output)
    require(source.resolve() != output.resolve(), "output: must differ from the source manifest")
    data = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    report = estimate_audio(data)
    encoded = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    if not output.exists() or output.read_text(encoding="utf-8") != encoded:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                             prefix=output.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "content/audio_budget.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/generated/audio_memory_report.json")
    parser.add_argument("--stdout", action="store_true", help="Print the full deterministic report as JSON")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.manifest, args.output)
    except (AudioBudgetError, OSError, ValueError) as exc:
        print(f"Audio memory estimate failed: {exc}", file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        peak = report["summary"]
        print(f"Planning only: peak {peak['peak_ram_bytes']} bytes in {peak['peak_scenario_id']}; report {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
