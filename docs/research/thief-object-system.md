# Thief object-system inspiration

Research date: 2026-09-05. Status: historical reference and proposed lessons for DarkLantern64.

## Historical findings

Thief: The Dark Project used Looking Glass's **Dark Object System**, principally designed by Marc “Mahk” LeBlanc, within the Dark Engine. Tom Leonard's 1999 postmortem describes a database of objects, properties, and relationships with common editing, persistence, and versioning support. Programmers exposed building blocks; designers authored object composition and the type hierarchy. This enabled substantial independent content development, although implementation and hierarchy management required considerable effort. [Leonard, original postmortem](https://media.gdcvault.com/GD_Mag_Archives/GDM_July_1999.pdf)

LeBlanc's retrospective describes five particularly useful concepts:

- **Object IDs:** an object's identity was an integer; it did not require a large universal object structure.
- **Properties:** data-only values or records lived in maps keyed by object ID. Systems could iterate objects possessing a property, with storage chosen for that property's access pattern.
- **Archetypes:** designer-authored prototypes supplied inherited defaults through a data-defined hierarchy.
- **Metaproperties:** reusable property bundles expressed materials, capabilities, vulnerabilities, and runtime statuses. Objects combined one archetype with multiple metaproperties.
- **Links:** typed, directed relationships carried optional data, supported queries in either direction, and disappeared when an endpoint was destroyed. Inventories and patrol routes used them.

Terrain also shared material semantics through representative objects associated with textures. This let surfaces and ordinary objects participate in common interaction rules. These details were checked against a third-party transcription of LeBlanc's primary speech; the video was not directly reviewed. [LeBlanc, Game Entities in Thief: The Dark Project](https://www.youtube.com/watch?v=5di7jmHKAQs), [transcript reproduction](https://rpghq.org/forums/viewtopic.php?t=4942)

“Early ECS” is useful shorthand. Dark's **archetype** meant an inherited prototype; Unity's ECS uses that word to group entities with the same component types. Adopting the authoring concept does not select a runtime storage architecture. [Unity, Archetypes concepts](https://docs.unity.cn/Packages/com.unity.entities@1.0/manual/concepts-archetypes.html)

## Stealth simulation inspiration

Leonard's sensory-system paper describes awareness relationships that retain time, location, visibility information, and cached calculations. Vision, semantically tagged sounds propagated through world geometry, prior knowledge, and configuration contribute to awareness. Reaction delays and gradual decay create readable uncertainty. These are useful foundations for stealth tuning. The paper discusses AI senses; it does not establish that the sensory pipeline is the Act/React object-interaction mechanism. [Leonard, Building an AI Sensory System](https://www.gamedeveloper.com/programming/building-an-ai-sensory-system-examining-the-design-of-i-thief-the-dark-project-i-)

## Proposed project lessons

LightEngine should make templates, traits, material semantics, and relationships easy to author and inspect. Show inherited-value origins and links so designers can explain an object's behavior.

Compile that expressive content into bounded runtime data within the mandatory 8 MiB memory baseline. Distinguish shared configuration from mutable simulation state. A room-and-portal graph is a candidate representation for acoustic propagation; its quality and cost need a prototype.

Expose awareness history, hearing events, visibility inputs, and resource costs during iteration. Favor a small vocabulary of consistent interactions that designers can combine into varied situations.

See the [architecture](../architecture.md) and [project overview](../../README.md) for project decisions.
