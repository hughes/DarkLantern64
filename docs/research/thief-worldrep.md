# Original Thief worldrep import

Research and local validation: 2026-10-12. The importer reads the user's installed
game files without modifying them. The parser is an independent implementation of
binary layouts; it embeds no third-party implementation or proprietary assets.

## Format provenance

- [OpenDarkEngine `darkdb.h`](https://github.com/volca02/openDarkEngine/blob/master/src/base/file/darkdb.h)
  and [tag-file reader](https://github.com/volca02/openDarkEngine/blob/master/src/base/file/FileGroup.cpp)
  establish the database header, inventory, and chunk envelopes. The layout
  credits Telliamed's DarkUtils.
- [OpenDarkEngine `WRTypes.h`](https://github.com/volca02/openDarkEngine/blob/master/src/services/worldrep/WRTypes.h)
  describes the packed cell, polygon, texturing, lightmap, and BSP records.
- [OpenDarkEngine `WRCell.cpp`](https://github.com/volca02/openDarkEngine/blob/master/src/services/worldrep/WRCell.cpp)
  establishes cell record ordering, convex triangle fans, textured portals,
  polygon-relative texture origins, and the inverse-basis UV calculation.
- [OpenDarkEngine `LightsForCell.cpp`](https://github.com/volca02/openDarkEngine/blob/master/src/services/worldrep/LightsForCell.cpp)
  establishes animation masks, pixel layers, and trailing per-cell light IDs.
- [OpenDarkEngine `WorldRepService.cpp`](https://github.com/volca02/openDarkEngine/blob/master/src/services/worldrep/WorldRepService.cpp)
  establishes the extra-plane/BSP arrays, leaf flag, child sentinel, and reversed
  split-plane flag.
- [KeepersCompound mission viewer](https://jayrude.dev/gitea/KeepersCompound/thief-mission-viewer/src/commit/d55231178e5e82220227214c9e9422466f3ad175/project/code/TMV/Mission.cs)
  independently demonstrates texture token paths and separates solid polygons
  from rendered water portals.
- [Telliamed's worldrep investigation](https://www.ttlg.com/FORUMS/showthread.php?s=8f7ed05c71d099aca3816e9e10d6bd28&t=104775)
  documents the original fixed 768 + 32 light slots, grayscale/RGB record sizes,
  and the downstream animation-cell mapping/CSG data.

Only original `WR` and `WRRGB` worldreps are supported. NewDark `WREXT` has
different headers, texturing records, and lighting tables and is rejected with an
explicit error. An installed NewDark executable does not imply that its original
missions have been converted: the installed Thief Gold missions inspected here
still contain `WR`.

## Packed little-endian layout

All records use little-endian integers/floats with **no alignment padding**.
Python format strings below omit the leading `<`.

| Record | Size | Python format / meaning |
| --- | ---: | --- |
| Database header | 272 | `3I256sI`: TOC offset, zero, one, reserved, `0xEFBEADDE` |
| TOC | variable | `I` count followed by `12sII` name, envelope offset, payload bytes |
| Chunk envelope | 24 | `12sIII`: name, major version, minor version, reserved |
| WR header | 8 | `II`: legacy data-size field, cell count |
| Cell header | 31 | `7BIHBB4f`: vertex/poly/render/portal/plane counts, medium, flags, portal vertex count, index count, animated-light count, flow group, sphere center/radius |
| Vertex | 12 | `3f`, source Z up |
| Polygon | 8 | `4BH2B`: flags, vertex count, plane index, CLUT, target cell, motion index, unknown |
| Render polygon | 48 | `6fhhBBH4f`: U/V basis, signed U/V offsets, texture ID, origin corner, cache value, scale, center |
| Plane | 16 | `4f`: normal and distance |
| Lightmap descriptor | 20 | `hhHBBIII`: offsets, row width, height, byte width, two ignored pointers, animation mask |
| BSP node | 20 | `IiiII`: parent/flags, cell, plane, front, back |
| Original WR light | 40 | `10f`: position, direction, grayscale intensity, cone cosines, radius |
| Original WRRGB light | 48 | `12f`: position, direction, RGB intensity, cone cosines, radius |

Cell payload order is header; vertices; polygons; render polygons; `I` index-list
length; byte indices; planes; `H` animated-light IDs; descriptors; per-face pixel
layers; `I` light-ID count; `H` light IDs. Each face has one static pixel layer plus
one layer per set bit in its animation mask. WR pixels are one byte; WRRGB pixels
are two bytes. Row width is the 16-bit field and is preserved independently from
the 8-bit width copy.

After all cells are the `I` extra-plane count and planes, then `I` BSP count and
nodes. A node's upper byte contains flags; lower 24 bits contain the parent index.
Flag 1 identifies a leaf (front = cell ID), flag 4 reverses the split-plane sense.
`0xFFFFFF` is the absent parent/child sentinel. Split planes reference a cell plane,
or an extra plane when the cell field is negative. The reader validates references
and preserves the tree rather than substituting a brush approximation.

The compiled light table begins after the BSP with static/dynamic counts, 800
fixed slots, and a count followed by pairs of 16-bit animation-cell mappings.
The 32 scratch slots may contain stale nonzero data even when dynamic count is
zero, so only explicitly active slots are returned as ordinary lights. The
remaining CSG data is retained as opaque bytes. The legacy WR data-size field is
retained as provenance rather than used as a file cursor: observed values do not
equal serialized cell/BSP byte totals.

`TXLIST` has a 12-byte size/item/token-count header, 16-byte C-string path tokens,
and 20-byte items (four 1-based byte token IDs followed by a 16-byte C-string).
Token 0 terminates the path. Bytes beyond a name's first NUL can contain garbage.

## Geometry and texture interpretation

Render records correspond to the first polygons. Portals occupy the last
`portal_count` polygons. The ranges overlap at water boundaries; therefore solid
faces are `min(render_count, polygon_count - portal_count)`. Omitting the overlap
removes water surfaces while retaining submerged sewer architecture.

Cell planes point into traversable space in the inspected missions. Source corner
winding usually points in the opposite direction, with collinear corners and a
few exceptional faces. Scene construction should calculate actual polygon
orientation and match the inward plane normal. A global assumption about corner
order produces inverted surfaces. WR polygons are convex, so a triangle fan is an
exact representation; removing redundant collinear corners preserves the shape.

Texture axes are world-space basis vectors, not projection coefficients. For
`d = corner - origin_corner`, use the inverse Gram matrix of U/V to calculate the
basis coefficients, add signed offset / 4096, and multiply by 64 / source-image
dimension. This also handles rotated/skewed mappings. UVs should be calculated in
raw coordinates before scene scaling. The exposed `flip_v` option adapts the
image-origin convention for Blender. Stored render `scale` is preserved and is
not an extra multiplier in this mapping.

The parser preserves raw DromEd units. A real-world unit scale is an authoring
choice: the format itself does not encode a meter conversion. Scale all
architecture, entity positions, light offsets, light radii, and portals uniformly.

## Local mission findings

`RES/strings.crf` contains `english/TITLES.STR` identifying mission 1 as **A Keeper's
Training** and mission 2 as **Lord Bafford's Manor**. The first campaign mission is
therefore `miss2.mis`; `miss1.mis` is the tutorial.

| Measure | Training (`miss1.mis`) | Bafford (`miss2.mis`) |
| --- | ---: | ---: |
| Cells | 1,104 | 4,991 |
| Cell-local vertices | 19,302 | 87,001 |
| All polygon records | 11,759 | 52,708 |
| Render polygons | 6,067 | 26,982 |
| Solid render polygons | 6,059 | 26,652 |
| Directed portal polygons | 5,700 | 26,056 |
| Fan triangles, including water | 17,108 | 76,390 |
| Fan triangles, excluding water | 17,062 | 75,266 |
| Water cells / rendered water faces | 5 / 8 | 159 / 330 |
| BSP nodes / extra BSP planes | 3,512 / 0 | 16,381 / 0 |
| Compiled static light records | 139 | 365 |
| TXLIST items | 75 | 146 |

Bafford raw bounds are X `[-150, 154.5]`, Y `[-201.5, 172.625]`, and Z
`[-77.75, 70]`. Texture 249 marks 570 sky faces (polygon flag 4). Textures 247 and
248 appear only on rendered water portals in this mission (92 each). Texture 0
is one null-textured narrow face and should retain its geometry with a fallback
material; texture 1 is ordinary `fam/core/BIGBL2` masonry.

All solid non-sky faces were checked for valid finite UV bases. Both original
missions successfully parsed with vertex, portal, plane, and BSP reference
validation. `tests/test_thief_dark.py` uses synthetic fixtures covering chunk
envelopes, water/render overlap, lightmap layer/stride alignment, BSP leaves,
skewed UVs, origin offsets, corrupt indices, truncation, resource paths, and static
versus scratch light slots without requiring game assets in CI.
