# Decision 0001: Require 8 MiB of RDRAM

Status: **Accepted**  
Date: 2026-09-05  
Scope: DarkLantern64 hardware and content memory baseline

## Context

The project targets original Nintendo 64 hardware, ModRetro M64, and compatible emulators. The project owner approved designing for expanded memory provided that M64 includes Expansion Pak functionality.

ModRetro explicitly lists built-in Expansion Pak support in the M64 features and FAQ. This confirms the condition for the decision. [Official M64 product page](https://modretro.com/products/m64), checked 2026-09-05.

Libdragon documents expanded N64 memory as 8 MiB and provides detection and early-error APIs. [N64 System Interface](https://libdragon.dev/ref/group__n64sys.html), checked 2026-09-05.

## Decision

DarkLantern64 requires **8 MiB (8,388,608 bytes) of total RDRAM**. A 4 MiB gameplay configuration is outside the supported scope.

| Target | Required configuration | Validation status |
| --- | --- | --- |
| Original N64 | Expansion Pak installed | Pending game implementation and hardware testing. |
| ModRetro M64 | Built-in Expansion Pak support enabled/available | Manufacturer capability verified; DarkLantern64 compatibility pending. |
| Emulator | Expanded memory enabled; Ares is the initial development target | Pending ROM and emulator testing. |

Original N64 performance is the working baseline. Extra M64 display features or overclocking are not assumed to increase the game's performance budget. M64 compatibility with our selected libdragon/rendering path must be tested separately.

## Runtime behavior

During startup, detect expanded memory before loading or touching expansion-dependent game data. Libdragon's `assert_memory_expanded()` supplies an initial error path; `is_memory_expanded()` permits a custom message later. The startup image and its initialization must remain capable of reaching that check on a 4 MiB system.

A machine without expanded memory should display a clear Expansion Pak requirement and stop cleanly. Validate that path with a 4 MiB emulator configuration as well as validating ordinary startup with 8 MiB.

## Budget consequences

Eight MiB is total physical memory, not the asset allowance or free heap. Account for code and static data, stacks, rendering buffers, audio buffers, simulation, resident assets, load/decompression scratch space, allocator overhead, and development instrumentation.

The first runtime should report memory use at boot, level load, and during play. Track peaks during loading, decompression, and transitions as well as steady state. Establish subsystem budgets and an explicit reserve from measurements; there is no verified allocation split yet.

Expanded memory supports richer content and simulation within those measurements. It does not remove rendering, CPU, RSP, or bandwidth constraints.

## Revisit if

Revisit this decision only if the target hardware changes or a concrete compatibility problem requires it. Update this record with the reason and consequences. Continue to record actual device, cartridge, firmware, emulator, and SDK versions when performing compatibility tests.

Return to the [project overview](../../README.md) or [architecture](../architecture.md).
