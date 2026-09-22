#!/usr/bin/env python3
"""Re-point footprint 3D models from a missing .wrl to the .step beside it.

KiCad 9 removed the VRML models from the stock library, so `kicad-packages3D`
in the action image ships `BatteryHolder_Bulgin_BX0036_1xC.step` and no `.wrl`
at all. A board authored in KiCad 8 still points at
`${KICAD8_3DMODEL_DIR}/Battery.3dshapes/BatteryHolder_Bulgin_BX0036_1xC.wrl`,
which now resolves to nothing: the component is dropped from the GLB with only
a "Could not add 3D model" line to show for it.

`glb/setup.sh` fixes the *variable name* for the same class of board -- a
different half of the same problem, and one that cannot help here, because no
value of KICAD8_3DMODEL_DIR makes a .wrl appear.

`--subst-models` is the option that looks like it should cover this and does
not: measured byte-identical output on 2026-09-21. The substitution operates on
a model that resolved, and a missing file never gets that far.

So the reference itself is what changes, on a copy the caller restores after
the export. Only the extension moves: the variable reference is left exactly as
written, so KiCad resolves the path the same way it would have.

A .wrl that *does* exist is left alone -- it is the board's own model, and
preferring a sibling .step would silently change what gets exported.
"""

import argparse
import os
import re
import sys

# KiCad quotes any path containing '${', which every stock-library reference
# does, but a bare atom is still legal s-expression and costs nothing to accept.
MODEL_RE = re.compile(r'\(model\s+(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s()]+))')
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Probed in order. KiCad's own libraries are lowercase '.step'; the rest are
# what a hand-built library tends to carry.
STEP_EXTS = (".step", ".stp", ".STEP", ".STP")


def expand(path, project_dir):
    """Resolve ${VAR} against the environment, as kicad-cli would.

    Returns (expanded path, sorted list of names that were not set). An unset
    variable is reported rather than expanded to nothing: the difference
    between "the model is missing" and "the path was never resolvable" is the
    difference between a fixable board and a broken environment.
    """
    missing = set()

    def sub(m):
        name = m.group(1)
        if name == "KIPRJMOD":
            return project_dir
        value = os.environ.get(name)
        if value is None:
            missing.add(name)
            return m.group(0)
        return value

    out = path
    # Nested expansions are not a KiCad feature, but one pass past the fixed
    # point is cheap and stops a self-referential value from looping.
    for _ in range(8):
        new = VAR_RE.sub(sub, out)
        if new == out:
            break
        out = new
    return out, sorted(missing)


def resolve(path, project_dir):
    expanded, missing = expand(path, project_dir)
    if missing:
        return None, missing
    if not os.path.isabs(expanded):
        expanded = os.path.join(project_dir, expanded)
    return os.path.normpath(expanded), []


def step_beside(resolved):
    stem = resolved[: -len(".wrl")]
    for ext in STEP_EXTS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def rewrite(text, project_dir):
    """Return (new text, repointed, unresolved, kept).

    repointed  - (original reference, new reference, resolved .step)
    unresolved - (reference, reason) for a .wrl with no usable substitute
    kept       - references left alone because the .wrl is really there
    """
    repointed, unresolved, kept = [], [], []

    def replace(m):
        whole = m.group(0)
        ref = m.group("quoted") if m.group("quoted") is not None else m.group("bare")
        if not ref.lower().endswith(".wrl"):
            return whole

        resolved, missing = resolve(ref, project_dir)
        if missing:
            unresolved.append((ref, f"unset path variable(s): {', '.join(missing)}"))
            return whole
        if os.path.isfile(resolved):
            kept.append(ref)
            return whole

        step = step_beside(resolved)
        if step is None:
            unresolved.append((ref, "neither the .wrl nor a .step beside it exists"))
            return whole

        # Swap the extension on the reference as written, not on the resolved
        # path: the variable has to survive into the file KiCad reads.
        new_ref = ref[: -len(".wrl")] + os.path.splitext(step)[1]
        repointed.append((ref, new_ref, step))
        return whole.replace(ref, new_ref)

    return MODEL_RE.sub(replace, text), repointed, unresolved, kept


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pcb")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing the board")
    opts = ap.parse_args()

    project_dir = os.path.dirname(os.path.abspath(opts.pcb))
    try:
        with open(opts.pcb, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"::error::Could not read '{opts.pcb}' to check its 3D model references: {exc}")
        return 1

    new_text, repointed, unresolved, kept = rewrite(text, project_dir)

    for ref, reason in unresolved:
        print(f"::warning::3D model '{ref}' will be missing from the GLB: {reason}.")

    if not repointed:
        if kept:
            print(f"::debug::{len(kept)} .wrl model(s) resolved as written; nothing to re-point.")
        return 0

    for ref, new_ref, step in repointed:
        print(f"::debug::re-pointed {ref} -> {new_ref} ({step})")

    if opts.dry_run:
        print(f"::notice::{len(repointed)} .wrl 3D model reference(s) would be re-pointed to .step.")
        return 0

    if new_text == text:
        print("::error::3D model re-pointing produced no change to the board file.")
        return 1

    try:
        with open(opts.pcb, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except OSError as exc:
        print(f"::error::Could not rewrite '{opts.pcb}' 3D model references: {exc}")
        return 1

    print(f"::notice::Re-pointed {len(repointed)} missing .wrl 3D model reference(s) to the "
          ".step beside them for this export; the board file is restored afterwards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
