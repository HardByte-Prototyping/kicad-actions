# 🧊 GLB export

`pcb_output_glb` exports the board as a binary glTF file ready to drop into a
web renderer such as PlayCanvas, three.js or Babylon.

```yaml
- name: Export board as GLB
  uses: actions-for-kicad/kicad-actions@v2-k10.0
  with:
    pcb_file_name: ./board.kicad_pcb
    pcb_output_glb: true
    pcb_output_glb_file_name: board.glb

- name: Upload GLB
  uses: actions/upload-artifact@v7
  with:
    path: ./board.glb
    archive: false
```

## Which copper gets exported

`kicad-cli` can export tracks, zones, pads and inner copper layers, and by
default this action exports **only pads**.

Once the soldermask is opaque — which is what the post-processing makes it, and
what a real board looks like — tracks, zones and inner layers are completely
hidden. Exporting them adds geometry that is never visible: on a small test
board, turning tracks and zones on roughly doubles the triangle count for no
change to the render. Pads stay on because they are the copper that shows
through the mask openings.

If you want the routing visible, the way it is on a real board where the mask
is a thin translucent coat over the copper, turn on `pcb_output_glb_tracks` and
`pcb_output_glb_zones` and set `pcb_output_glb_mask_opacity` to `0.83`, KiCad's
own value. Only the mask stays blended; the board body and silkscreen remain
opaque, so tracks and tented vias read as tinted relief rather than as bare
copper. See [`pcb_output_glb_mask_opacity`](#pcb_output_glb_mask_opacity).

## What the post-processing does

`kicad-cli` produces its GLB through OpenCASCADE, which targets CAD viewers.
Several of its choices look wrong in a real-time renderer, so
`pcb_output_glb_optimize` (on by default) rewrites the glTF metadata. Geometry
is never touched by this pass; reducing it is
[`pcb_output_glb_optimize_mesh`](#mesh-optimization)'s job.

| Issue | What KiCad emits | What the renderer does with it | Fix |
| --- | --- | --- | --- |
| Component materials | `baseColorFactor` only, no `metallicFactor` or `roughnessFactor` | glTF defaults both to `1.0`, so every part is fully metallic and fully rough — dark and muddy under image-based lighting | Fill in plausible PBR values, detecting bare metal by colour |
| Transparency | board, soldermask and silkscreen are all `alphaMode: BLEND` | Transparent meshes are depth-sorted per object, so the stacked layers z-fight and pop while orbiting | Make them opaque. With `pcb_output_glb_mask_opacity` below 1 the mask alone stays blended, which a single layer above opaque geometry survives |
| Backface culling | `doubleSided: true` on everything | Culling is disabled, doubling fragment cost | Turn it off, having verified triangle winding matches the vertex normals |
| Naming | nodes are OpenCASCADE label paths (`=>[0:1:1:4]`), materials are `mat_0`…`mat_n` | Nothing in the scene can be addressed from engine script | Name them `Board`, `SolderMask_Front`, `Silkscreen_Front`, `Pads`, `Copper_Front`, and components after their reference designator |
| Origin | the board sits offset from the origin, in metres | The model orbits around a pivot outside itself | Recentre on the board bounding box |

The resulting scene graph looks like this, and every name is stable:

```
PCB
├── Board
├── SolderMask_Front
├── SolderMask_Back
├── Silkscreen_Front
├── Copper_Front
├── Copper_Back
├── Pads
├── D101
│   └── D101_Model
└── J3                      # multi-solid STEP model
    └── J3_Assembly
        ├── J3_Model
        └── J3_Model_2
```

A component whose 3D model contains several solids gets an extra assembly
level, because that is how OpenCASCADE structures it. Those intermediate nodes
are named after the part they belong to rather than left as label paths.

Two-sided layers are split by geometry, not by mesh order: the layers are
ranked by the height of their bounding-box centre, so `Copper_Front` is the
copper that is actually on top and stays that name across exports. With
`pcb_output_glb_inner_copper: true` the layers in between come out as
`Copper_1`, `Copper_2` and so on, front to back. If a board somehow yields
several copper meshes at the same height there is nothing to rank them by, and
they fall back to `Copper` and `Copper_2` in mesh order.

Set `pcb_output_glb_optimize: false` to get `kicad-cli`'s output untouched.

## Component materials

Board layers get materials named after themselves — `Board`, `SolderMask_Front`
and so on. Component materials are named after their colour instead, for
example `Component_EDBE51`. That is deliberate: a running index would shift
whenever the board gains or loses a part, silently retargeting any material
override written against it. A colour-derived name stays put.

Because `kicad-cli` emits no `metallicFactor` for model-derived materials, the
metal-versus-plastic call has to be guessed from colour, and
`pcb_output_glb_detect_metals` does that by treating unsaturated mid-grey as
bare metal. It reliably catches tinned leads and shielding cans, and reliably
rejects black IC bodies and ceramic capacitors.

It cannot catch everything, and this is a limitation of the input rather than
of the rule:

- **Coloured metals** — gold plating and copper are saturated, so they fall
  outside an unsaturated-grey test.
- **Shared materials** — one material can serve both a white plastic housing
  and a nickel-plated connector shell. There is a single colour for both
  surfaces, so no colour test can separate them.

Rather than guess harder, use `pcb_output_glb_metal_colors` to name the
offenders once. Run the export, look at the material list, and list the
colours that should be metal:

```yaml
pcb_output_glb_metal_colors: "EDBE51,97A3DA,FFFFFF"
```

Colours are matched against component materials only, so a colour that also
appears on a board layer — `FFFFFF` is both a connector shell and the
silkscreen — affects only the components. Entries that match nothing are
reported as a warning, which catches typos.

## Component 3D models

This fork builds on `kicad/kicad:10.0-full`, which ships the stock KiCad 3D
model library at `/usr/share/kicad/3dmodels`, so footprints referencing
`${KICAD10_3DMODEL_DIR}` and friends export with their meshes.

The plain `kicad/kicad:10.0` tag upstream uses does **not**: the official
Dockerfile clones `kicad-packages3D` only under `--build-arg include_3d=true`,
which is what the `-full` tags are built with. On the plain tag every
standard-library model resolves to a path that does not exist and is silently
dropped, leaving a bare board — the failure is invisible because a missing
model is not an error.

Parts outside the stock library still need their models committed alongside the
project and referenced with `${KIPRJMOD}` or a relative path. That remains the
right approach for vendor meshes; it is no longer required for stock parts.

The action also aliases the legacy `KICAD6_3DMODEL_DIR` through
`KICAD11_3DMODEL_DIR` variables to the current version's model directory.
KiCad only defines the variable for its own major version, so a board authored
in KiCad 8 that refers to `${KICAD8_3DMODEL_DIR}` would otherwise lose every
component when exported by KiCad 10 — the GUI migrates those references, but
`kicad-cli` does not.

## Vias

`pcb_output_glb_cut_vias` drills the via holes through the board body and is on
by default. Via barrels alone do not read as holes: with the body left solid a
via is a copper ring lying on an unbroken surface. The barrels come from the
conductor layers, the hole comes from this, and you want both.

## Making a dark board look dark

A translucent mask is translucent over the *whole* board, not only over copper,
so KiCad's default tan substrate shows through as brown everywhere the copper
is absent — the board reads brown rather than black even when the stackup says
`(color "Black")`, because the mask colour is doing what it is told and the
substrate underneath is not black.

`pcb_output_glb_board_color` overrides the substrate. For a black-mask board:

```yaml
pcb_output_glb_tracks: true
pcb_output_glb_zones: true
pcb_output_glb_mask_opacity: "0.83"
pcb_output_glb_board_color: "1A1A1A"
```

Bare areas then read as the mask colour, while copper still shows through
tinted. The substrate stays opaque; only its colour changes.

## Framing the rendered image

`kicad-cli` aims the camera at the board origin, not at the board's visual
centre, so a rotated or off-origin board sits off-centre in the frame and can
run off the sides. Choosing a zoom to compensate does not really fix it — the
problem is where the board sits, not how big it is, and the board outline does
not account for perspective, component height or where the origin happens to
be.

`pcb_output_image_autoframe` measures the render instead. On a transparent
background every pixel the board covers has a non-zero alpha, so the board's
screen-space bounding box is exactly its non-transparent extent; cropping to
that box centres the board by construction and trims the dead margin with it.

```yaml
pcb_output_image_background: transparent
pcb_output_image_autoframe: true
pcb_output_image_autoframe_margin: "0.04"
```

It changes the output dimensions: the result is the board plus the margin, not
the requested width and height. Render generously and let the crop decide the
final size. It does not scale — a tight crop of a transparent PNG is what a
consumer wants anyway, and resampling without an imaging library would mean
shipping a resampler.

Needs a PNG with an alpha channel. An opaque background, a JPEG, or anything
that is not an 8-bit RGBA PNG warns and leaves the image as rendered, because a
missing crop is cosmetic and a corrupted release asset is not.

## Exporting a WebP

`pcb_output_webp` is its own export, the way `pcb_output_image` and
`pcb_output_svg` are. It does not need `pcb_output_image`, and it carries its
own side, size, framing and quality rather than borrowing that block's.

```yaml
pcb_output_webp: true
pcb_output_webp_file_name: board.webp
pcb_output_webp_side: top
pcb_output_webp_width: 1600
pcb_output_webp_height: 900
```

`kicad-cli` renders PNG and JPEG and nothing else, so the export renders a PNG
and converts it. The PNG is an intermediate: it is written outside the
workspace and removed, so the export produces exactly one file and nothing a
later `upload-artifact` step can glob up by accident.

The settings are separate on purpose. A web asset is usually not the picture
you want archived — smaller, cropped tighter, often a different angle — and
tying the two together would mean neither could move without the other. Set
both blocks to the same values and you get the same picture in both formats,
at the cost of rendering it twice.

`pcb_output_webp_width` and `pcb_output_webp_height` are a request, not a
promise: kicad-cli renders close to them but not exactly, returning 784x592 for
a requested 800x600. If something downstream needs an exact canvas — a shop
grid, say — resize there rather than assuming these values land.

### Board colours

`kicad-cli pcb render` does not use the board's stackup colours unless asked:
`--use-board-stackup-colors` is opt-in, and without it every board renders in
the 3D viewer preset's colours — a green soldermask whatever
`(color "Black")` in the stackup says. The colour is read and then not used.

`pcb_output_webp_use_board_stackup_colors`, and
`pcb_output_image_use_board_stackup_colors` for the image export, pass that
flag. Both default to `false`, which is what kicad-cli does on its own.

It needs the stackup to actually carry colours, which only happens once the
physical stackup has been set in Board Setup. Check a board with:

```sh
grep -A30 '(stackup' board.kicad_pcb | grep color
```

Nothing there means the colour is not in the design file at all, and no flag
will conjure it.

### Framing and quality

`pcb_output_webp_autoframe` crops to the board before converting, the same
measurement [Framing the rendered image](#framing-the-rendered-image)
describes, done on the intermediate where the alpha channel still says where
the board is. It needs the background left at `default` or set to
`transparent`; an opaque one warns and converts the render as-is.

Two quality settings, because there are two lossy steps and they are unrelated:

| Input | Default | What it sets |
| --- | --- | --- |
| `pcb_output_webp_quality` | `basic` | how the board is raytraced — `basic`, `high`, `user`, the same values as `pcb_output_image_quality` |
| `pcb_output_webp_encode_quality` | `82` | how the WebP is compressed, 0–100 |

The names mirror the image block deliberately, so a copied render setting keeps
working. Passing `82` to the render quality is caught with a message naming the
other input.

Alpha is always encoded losslessly whatever the encode quality, because a
transparent render gets composited over a page background and that is exactly
where a lossy alpha channel shows up — as a halo tracing the board outline.
`pcb_output_webp_lossless` makes the colour lossless too; on a board render,
which is flat colour and sharp silkscreen, that costs less than it would on a
photograph, but the file is still several times the lossy one.

The encoder is `cwebp`, which the action's image installs. A missing `cwebp`
fails the export rather than warning, and fails before the render rather than
after it, so a typo does not cost a minute of raytracing to discover.

## Mesh optimization

`kicad-cli` emits one glTF primitive per OpenCASCADE face. A real 170 mm
carrier board comes out of the exporter like this:

| | exported | after optimization |
| --- | --- | --- |
| File size | 49.4 MB | **3.8 MB** |
| Draw calls | 62,553 | **174** |
| Accessors | 138,849 | 522 |
| Triangles | 979,626 | 857,176 |

Sixteen triangles per draw call is what makes the raw export unusable in a
real-time renderer, and it is not the triangle count that does it. The
accessor table needed to describe 62,553 primitives is the other half: it put
22 MB of JSON in that 49 MB file, with the copper layer alone accounting for
16,005 primitives and pads another 11,548.

`pcb_output_glb_optimize_mesh` (on by default) runs the export through
[gltfpack](https://github.com/zeux/meshoptimizer/tree/master/gltf), which
merges the primitives that share a material, welds and quantizes the vertices,
and applies meshopt compression. It runs after the post-processing, because it
preserves the names it is given and merges nothing across them.

**Names survive it.** Every node the post-processing creates — `PCB`, `Board`,
`SolderMask_Front`, `Copper_Front`, `Pads`, each reference designator — and
every colour-derived material name like `Component_EDBE51` is still there and
still addressable afterwards. gltfpack is always run with `-kn -km` for
exactly this reason, and those flags are deliberately not exposed as inputs:
without them it merges across nodes and renames what it merged, which would
undo the naming and silently break every material override and engine script
written against it. They cost merging opportunities, and the names are worth
more. One thing does change — the *mesh* names are dropped, so address the
geometry through the node, which is the durable handle.

### What the renderer needs

The default output declares two extensions:

| Extension | From | Needed by |
| --- | --- | --- |
| `KHR_mesh_quantization` | `pcb_output_glb_quantize` | any loader; widely supported |
| `EXT_meshopt_compression` | `pcb_output_glb_compression` | a registered meshopt decoder |

PlayCanvas supports both out of the box. three.js needs `MeshoptDecoder`
registered on the `GLTFLoader`. For a loader that can do neither, set
`pcb_output_glb_compression: none` and `pcb_output_glb_quantize: false` — the
merging is what matters most and it needs no extension at all.

Measured on the same board:

| Setting | Size | Extensions |
| --- | --- | --- |
| default (`meshopt`) | 3.8 MB | quantization + compression |
| `meshopt-high` | 3.3 MB | quantization + compression |
| `compression: none` | 19.2 MB | quantization |
| `quantize: false` | 9.1 MB | compression |
| `simplify: 0.5` | 2.4 MB | quantization + compression |
| `optimize_mesh: false` | 49.4 MB | none |

`meshopt-high` costs nothing at decode time; it spends longer choosing the
encoding. There is no reason not to use it other than export time, which is
under two seconds either way.

### Simplification

`pcb_output_glb_simplify` decimates the mesh to a fraction of its triangles and
is **off by default**, because everything else on this page is lossless and
this is not. It is the knob to reach for only once the file is still too large
with compression on — on the board above, halving the triangles saved 1.4 MB
against 3.8, while merging and compression had already saved 45.6.

`pcb_output_glb_simplify_error` bounds the deviation, defaulting to 1% of the
mesh size; simplification stops there even if the ratio is not reached. Border
vertices are locked whenever simplification runs, because a board is many
separate solids meeting at shared edges, and collapsing a vertex on such an
edge opens a gap that reads as a hole rather than as a lower triangle count.

### Precision

`pcb_output_glb_position_bits` sets the quantization grid, 14 bits by default,
which resolves about 0.01 mm across a 170 mm board — finer than the export's
own `pcb_output_glb_min_distance` default of 0.01 mm. Dropping to 10 bits took
that board from 3.8 MB to 2.7 MB, at 0.17 mm per step, which is visible on
silkscreen edges. Leave it alone unless the file size matters more than the
board does.

## Notes for PlayCanvas

- glTF units are metres, so a 100 mm board arrives as 0.1 units. Either scale
  the entity in PlayCanvas or export with `pcb_output_glb_scale: 1000`.
- The model has no UV channels, because KiCad emits none. Materials are flat
  colours, which is enough for a board but means image textures cannot be
  applied without generating UVs first.
- The mesh is meshopt compressed by default, so register the meshopt decoder
  with the loader. See [Mesh optimization](#mesh-optimization).

# 📥 GLB inputs

## `pcb_output_glb`

Required: `false`\
Default: `false`\
\
Description: Run the GLB (binary glTF) export of the PCB. See
[GLB export](#-glb-export) for what the defaults do and why.

## `pcb_output_glb_file_name`

Required: `false`\
Default: `pcb.glb`\
\
Description: Output file name of GLB PCB. Must end in `.glb`.

## `pcb_output_glb_soldermask`

Required: `false`\
Default: `true`\
\
Description: Export the soldermask layers. Without this KiCad flat-colours the
board body instead of producing a real mask surface.

## `pcb_output_glb_silkscreen`

Required: `false`\
Default: `true`\
\
Description: Export the silkscreen graphics as flat faces.

## `pcb_output_glb_pads`

Required: `false`\
Default: `true`\
\
Description: Export pads. This is the copper you actually see, through the
soldermask openings.

## `pcb_output_glb_tracks`

Required: `false`\
Default: `false`\
\
Description: Export tracks and vias. These sit under an opaque soldermask and
contribute nothing to the visible result, so they are off by default.

## `pcb_output_glb_zones`

Required: `false`\
Default: `false`\
\
Description: Export copper zones. Hidden under the soldermask, as with tracks.

## `pcb_output_glb_inner_copper`

Required: `false`\
Default: `false`\
\
Description: Export inner copper layers. These are never visible from outside
the board.

## `pcb_output_glb_components`

Required: `false`\
Default: `true`\
\
Description: Include component 3D models. See
[Component 3D models](#component-3d-models) — stock KiCad models come from the
`-full` base image; anything outside the stock library comes from your
repository.

## `pcb_output_glb_board_only`

Required: `false`\
Default: `false`\
\
Description: Export only the bare board, with no components.

## `pcb_output_glb_component_filter`

Required: `false`\
\
Description: Only include component 3D models matching this comma-separated
list of reference designators. Wildcards supported, for example `U*,J1`.

## `pcb_output_glb_net_filter`

Required: `false`\
\
Description: Only include copper items belonging to nets matching this
wildcard.

## `pcb_output_glb_no_dnp`

Required: `false`\
Default: `false`\
\
Description: Exclude 3D models for components marked 'Do not populate'.

## `pcb_output_glb_no_unspecified`

Required: `false`\
Default: `false`\
\
Description: Exclude 3D models for components with an 'Unspecified' footprint
type.

## `pcb_output_glb_subst_models`

Required: `false`\
Default: `false`\
\
Description: Substitute STEP or IGS models for VRML models with the same name.

## `pcb_output_glb_fuse_shapes`

Required: `false`\
Default: `false`\
\
Description: Fuse overlapping geometry together. Slower, but removes coincident
surfaces.

## `pcb_output_glb_min_distance`

Required: `false`\
Default: `0.01mm`\
\
Description: Minimum distance between points to treat them as separate ones.

## `pcb_output_glb_origin`

Required: `false`\
Default: `board`\
\
Description: Origin of the exported model. Options: `board`, `grid`, `drill`,
or an explicit offset such as `25.4x25.4mm`.

The three named origins are matched exactly, in lower case; anything else is
treated as an explicit offset and must look like `<x>x<y>mm` or `<x>x<y>in`
(negatives allowed). A malformed value fails the export with an error naming
it, rather than being passed to `kicad-cli` — which rejects it with an exit
code the export deliberately treats as success, so it would otherwise surface
only as a missing output file.

## `pcb_output_glb_optimize`

Required: `false`\
Default: `true`\
\
Description: Post-process the GLB for real-time renderers such as PlayCanvas,
three.js or Babylon. Set to `false` to get exactly what `kicad-cli` produces.
See [What the post-processing does](#what-the-post-processing-does).

## `pcb_output_glb_center`

Required: `false`\
Default: `true`\
\
Description: Recentre the model on the board bounding box so it orbits around
itself instead of a point off to one side. Requires `pcb_output_glb_optimize`.

## `pcb_output_glb_scale`

Required: `false`\
Default: `1.0`\
\
Description: Uniform scale applied to the exported model. glTF units are
metres, so a 100 mm board is 0.1 units at the default scale. Use `1000` if you
want the model to arrive in millimetres.

Must be a positive number; `0` is rejected rather than written out as a scale
that would collapse the model to a point. Combines correctly with
`pcb_output_glb_center` — the recentre is applied in the scaled space, so the
board stays on the pivot at any scale.

## `pcb_output_glb_detect_metals`

Required: `false`\
Default: `true`\
\
Description: Treat unsaturated mid-grey component colours as bare metal, so
leads and shielding cans render metallic rather than as grey plastic. See
[Component materials](#component-materials) for what this can and cannot get
right.

## `pcb_output_glb_metal_colors`

Required: `false`\
\
Description: Comma-separated hex colours whose component materials are forced
metallic, for example `EDBE51,97A3DA`. Overrides the colour heuristic. Board
layers are never affected, so listing `FFFFFF` cannot make the silkscreen
metallic. See [Component materials](#component-materials).

## `pcb_output_glb_mask_opacity`

Required: `false`\
Default: `1.0`\
\
Description: Opacity of the soldermask, above 0 and up to 1. At `1.0` the mask
is opaque and hides every copper item under it, which is why only pads are
exported by default. Below 1 the mask keeps `alphaMode: BLEND` at this alpha
while the board body and silkscreen stay opaque. Copper under the mask then
shows through tinted, the way tracks and tented vias look on a real board.
`0.83` is what KiCad itself uses. Pair it with `pcb_output_glb_tracks` and
`pcb_output_glb_zones`, or there is nothing under the mask to see; the action
warns when that happens. A single blended layer above opaque geometry sorts
correctly in real-time renderers. It is the full stack of blended layers that
`pcb_output_glb_keep_transparency` restores which does not.

## `pcb_output_glb_optimize_mesh`

Required: `false`\
Default: `true`\
\
Description: Merge the exported primitives by material and quantize the
vertices with gltfpack. This is what takes a board from tens of thousands of
draw calls to a few hundred. Node and material names are preserved. See
[Mesh optimization](#mesh-optimization).

## `pcb_output_glb_compression`

Required: `false`\
Default: `meshopt`\
\
Description: Mesh compression applied after merging. Options: `meshopt`,
`meshopt-high`, `none`. `meshopt` writes `EXT_meshopt_compression`, which
PlayCanvas decodes natively and three.js decodes with `MeshoptDecoder`
registered. `meshopt-high` compresses further at no extra decode cost.
`none` leaves the geometry uncompressed for a loader that supports neither.
Requires `pcb_output_glb_optimize_mesh`.

## `pcb_output_glb_quantize`

Required: `false`\
Default: `true`\
\
Description: Quantize vertex attributes to integers with
`KHR_mesh_quantization`. This is most of the size reduction before compression
is applied. Turn it off only for a loader that supports no glTF extensions at
all. Requires `pcb_output_glb_optimize_mesh`.

## `pcb_output_glb_position_bits`

Required: `false`\
Default: `14`\
\
Description: Bits of precision per vertex position, from 1 to 16. The default
resolves about 0.01 mm across a 170 mm board. Requires
`pcb_output_glb_quantize`.

## `pcb_output_glb_simplify`

Required: `false`\
Default: `1.0`\
\
Description: Decimate the meshes to this fraction of their triangle count,
above 0 and up to 1. `1.0` means no simplification, which is the default:
merging and compression are lossless and this is not. See
[Simplification](#simplification).

## `pcb_output_glb_simplify_error`

Required: `false`\
Default: `0.01`\
\
Description: Deviation budget for `pcb_output_glb_simplify`, from 0 to 1,
where `0.01` allows 1% of the mesh size. Simplification stops at this error
even if the target ratio has not been reached.

## `pcb_output_glb_keep_transparency`

Required: `false`\
Default: `false`\
\
Description: Keep the board, soldermask and silkscreen as blended transparent
materials, the way KiCad exports them. These sort badly in real-time
renderers, so this is off by default.
