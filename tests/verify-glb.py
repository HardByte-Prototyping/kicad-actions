#!/usr/bin/env python3
"""Assert that a GLB exported by this action actually carries what it promises.

The magic bytes prove the file is a GLB, not that the passes that were asked
for ran. A recentre that composes wrongly with --scale, an OpenCASCADE label
left in a node name, a board layer still set to BLEND or a mesh optimization
that quietly did nothing all produce a perfectly valid GLB, so the outcomes are
asserted directly.

Everything here is measured in world space, by walking the scene graph and
composing node transforms. That is not incidental: gltfpack moves a named
node's mesh onto an unnamed child and pushes the quantization scale onto that
child, so reading one node's transform, or looking a mesh up by name, tests the
unoptimized export and silently skips the optimized one.
"""

import argparse
import json
import re
import struct
import sys

OCCT_LABEL_RE = re.compile(r"^=>\[?[\d:]+\]?$")
UNNAMED_MAT_RE = re.compile(r"^mat_\d+$")


def read_gltf(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        sys.exit(f"::error::{path}: not a binary glTF file (magic {data[:4]!r})")
    off = 12
    while off + 8 <= len(data):
        ln, kind = struct.unpack_from("<I4s", data, off)
        off += 8
        if kind == b"JSON":
            return json.loads(data[off:off + ln])
        off += ln
    sys.exit(f"::error::{path}: no JSON chunk")


# --------------------------------------------------------------------------
# Scene graph
# --------------------------------------------------------------------------


def identity():
    return [1.0 if i % 5 == 0 else 0.0 for i in range(16)]


def mat_mul(a, b):
    """Column-major 4x4 multiply, matching glTF's convention."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def node_matrix(node):
    if "matrix" in node:
        return list(node["matrix"])
    # glTF composes a node as M = T * R * S.
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
    tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
    x, y, z, w = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    # Rotation matrix from the quaternion. Footprint placement puts one on
    # most component nodes, so this is the common case, not a corner.
    r = [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y),
    ]
    scale = (sx, sy, sz)
    m = identity()
    for c in range(3):
        for row in range(3):
            m[c * 4 + row] = r[c * 3 + row] * scale[c]
    m[12], m[13], m[14] = tx, ty, tz
    return m


def transform_point(m, p):
    return [
        m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
        m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
        m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    ]


def walk(gltf):
    """Yield (node index, node, world matrix) for the whole default scene."""
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [{"nodes": []}])
    roots = scenes[gltf.get("scene", 0)].get("nodes", [])
    stack = [(r, identity()) for r in roots]
    seen = set()
    while stack:
        idx, parent = stack.pop()
        if idx in seen:
            continue
        seen.add(idx)
        world = mat_mul(parent, node_matrix(nodes[idx]))
        yield idx, nodes[idx], world
        for child in nodes[idx].get("children", []):
            stack.append((child, world))


def subtree(gltf, root_idx):
    """Node indices of the subtree rooted at root_idx."""
    nodes = gltf.get("nodes", [])
    out, stack = set(), [root_idx]
    while stack:
        i = stack.pop()
        if i in out:
            continue
        out.add(i)
        stack.extend(nodes[i].get("children", []))
    return out


def world_bounds(gltf, want):
    """World-space bounding box over the nodes in `want` that carry a mesh.

    Accessor min/max are in the mesh's own space, quantized or not, so putting
    them through the node's world matrix is what makes the two cases comparable.
    """
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found = False
    for idx, node, world in walk(gltf):
        if idx not in want or "mesh" not in node:
            continue
        for prim in meshes[node["mesh"]].get("primitives", []):
            acc_idx = prim.get("attributes", {}).get("POSITION")
            if acc_idx is None:
                continue
            acc = accessors[acc_idx]
            if "min" not in acc or "max" not in acc:
                continue
            found = True
            # Every corner, not just min and max: a node scale may be negative
            # or the axes permuted, either of which reorders the extremes.
            for cx in (acc["min"][0], acc["max"][0]):
                for cy in (acc["min"][1], acc["max"][1]):
                    for cz in (acc["min"][2], acc["max"][2]):
                        p = transform_point(world, [cx, cy, cz])
                        for a in range(3):
                            lo[a] = min(lo[a], p[a])
                            hi[a] = max(hi[a], p[a])
    return (lo, hi) if found else None


def find_board(gltf):
    """Nodes making up the board body, however the passes have restructured it.

    Unoptimized, the board is a mesh named '<board>_PCB' or 'Board' hanging off
    a node of the same name. gltfpack keeps the node name and drops the mesh
    name, so the node is the durable handle and its subtree is where the
    geometry ends up.
    """
    for idx, node, _ in walk(gltf):
        if node.get("name") == "Board":
            return subtree(gltf, idx)
    meshes = gltf.get("meshes", [])
    for idx, node, _ in walk(gltf):
        mi = node.get("mesh")
        if mi is None:
            continue
        name = meshes[mi].get("name") or ""
        if name == "Board" or name.endswith("_PCB"):
            return {idx}
    return None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_names(gltf, fails):
    stray = [n.get("name") for n in gltf.get("nodes", [])
             if OCCT_LABEL_RE.match(n.get("name") or "")]
    if stray:
        fails.append(f"OpenCASCADE label paths left in node names: {stray[:5]}")
    unnamed = [m.get("name") for m in gltf.get("materials", [])
               if UNNAMED_MAT_RE.match(m.get("name") or "")]
    if unnamed:
        fails.append(f"unnamed materials left behind: {unnamed[:5]}")

    # The scene graph names are a documented contract that engine script and
    # material overrides are written against, and the mesh optimization is the
    # pass most able to break them.
    names = {n.get("name") for n in gltf.get("nodes", [])}
    for required in ("PCB", "Board"):
        if required not in names:
            fails.append(f"node '{required}' is missing from the scene graph")


def check_materials(gltf, mask_opacity, fails):
    mats = gltf.get("materials", [])
    is_mask = lambda m: (m.get("name") or "").startswith("SolderMask")
    blend = [m.get("name") for m in mats
             if m.get("alphaMode") == "BLEND"
             and not (mask_opacity < 1.0 and is_mask(m))]
    if blend:
        fails.append(f"materials still alphaMode BLEND: {blend[:5]}")
    if mask_opacity < 1.0:
        masks = [m for m in mats if is_mask(m)]
        if not masks:
            fails.append("no SolderMask material found to keep translucent")
        for m in masks:
            alpha = m.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])[3]
            if m.get("alphaMode") != "BLEND" or abs(alpha - mask_opacity) > 1e-3:
                fails.append(f"{m.get('name')}: expected BLEND at alpha {mask_opacity}, "
                             f"got {m.get('alphaMode', 'OPAQUE')} at {alpha}")


def check_recentre(gltf, scale, fails):
    board = find_board(gltf)
    if board is None:
        fails.append("no board body found in the scene graph, so the recentre "
                     "could not be checked")
        return
    bounds = world_bounds(gltf, board)
    if bounds is None:
        fails.append("board body carries no positions with bounds")
        return
    lo, hi = bounds
    centre = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    # Quantization moves a vertex by up to half a step, and the bound is taken
    # from the board's own extent rather than assumed.
    span = max(hi[i] - lo[i] for i in range(3))
    tol = max(1e-4 * max(1.0, scale), span / 2 ** 11)
    if any(abs(v) > tol for v in centre):
        fails.append(f"board centre sits at {[round(v, 5) for v in centre]} in world "
                     f"space, not the origin (scale={scale}, tolerance={tol:.2e})")


def check_optimized(gltf, expect_compression, expect_quantization, fails):
    """The mesh optimization's own outcomes."""
    prims = sum(len(m.get("primitives", [])) for m in gltf.get("meshes", []))
    # kicad-cli emits one primitive per OpenCASCADE face, which is thousands
    # even for the small test board. Merging by material has to collapse that
    # to roughly the material count; a generous ceiling still catches the pass
    # silently not running.
    if prims > 512:
        fails.append(f"{prims} primitives left after merging by material; "
                     "the mesh optimization does not appear to have run")

    required = set(gltf.get("extensionsRequired", []))
    if expect_quantization and "KHR_mesh_quantization" not in required:
        fails.append(f"KHR_mesh_quantization missing from extensionsRequired: {sorted(required)}")
    if not expect_quantization and "KHR_mesh_quantization" in required:
        fails.append("KHR_mesh_quantization present although quantization was turned off")
    if expect_compression and "EXT_meshopt_compression" not in required:
        fails.append(f"EXT_meshopt_compression missing from extensionsRequired: {sorted(required)}")
    if not expect_compression and "EXT_meshopt_compression" in required:
        fails.append("EXT_meshopt_compression present although compression was set to 'none'")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--mask-opacity", type=float, default=1.0)
    ap.add_argument("--optimized-mesh", action="store_true",
                    help="assert the gltfpack pass ran and merged by material")
    ap.add_argument("--compressed", action="store_true",
                    help="with --optimized-mesh, require EXT_meshopt_compression")
    ap.add_argument("--unquantized", action="store_true",
                    help="with --optimized-mesh, quantization was turned off")
    opts = ap.parse_args()

    gltf = read_gltf(opts.path)
    fails = []
    check_names(gltf, fails)
    check_materials(gltf, opts.mask_opacity, fails)
    check_recentre(gltf, opts.scale, fails)
    if opts.optimized_mesh:
        check_optimized(gltf, opts.compressed, not opts.unquantized, fails)

    for f in fails:
        print(f"::error::{opts.path}: {f}")
    if not fails:
        prims = sum(len(m.get("primitives", [])) for m in gltf.get("meshes", []))
        print(f"{opts.path}: ok ({len(gltf.get('nodes', []))} nodes, {prims} primitives, "
              f"extensions {sorted(gltf.get('extensionsRequired', [])) or 'none'})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
