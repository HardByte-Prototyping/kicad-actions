#!/bin/bash
# Sourced by glb/export.sh, after the GLB exists and postprocess.py has named
# it. Expects the INPUT_PCB_OUTPUT_GLB_* variables and inherits 'set -e' from
# the caller.
#
# kicad-cli emits one glTF primitive per OpenCASCADE face, so a board arrives
# as tens of thousands of draw calls -- 62,553 on a real 170 mm carrier board,
# 16,005 of them the copper layer alone -- for under a million triangles. That
# is roughly sixteen triangles per draw call, and it is what makes the export
# unusable in a real-time renderer rather than the triangle count. The
# accessor table needed to describe them all is the other half of the problem:
# 138,849 accessors put 22 MB of JSON in a 49 MB file.
#
# gltfpack merges primitives that share a material, welds and quantizes the
# vertices, and optionally applies meshopt compression. On that same board:
# 49.4 MB / 62,553 draw calls -> 3.8 MB / 174.

if [[ $INPUT_PCB_OUTPUT_GLB_OPTIMIZE_MESH == "true" ]]; then

  if ! command -v gltfpack &> /dev/null; then
    echo "::error::pcb_output_glb_optimize_mesh is enabled but gltfpack is not installed in the action image."
    exit 1
  fi

  case "$INPUT_PCB_OUTPUT_GLB_COMPRESSION" in
    meshopt|meshopt-high|none) ;;
    *)
      echo "::error::Invalid GLB compression '$INPUT_PCB_OUTPUT_GLB_COMPRESSION'. Supported values are 'meshopt', 'meshopt-high' and 'none'."
      exit 1
      ;;
  esac

  # Above 1 is not a ratio, and 0 would target no triangles at all.
  if [[ -n $INPUT_PCB_OUTPUT_GLB_SIMPLIFY ]] &&
     { ! [[ $INPUT_PCB_OUTPUT_GLB_SIMPLIFY =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]] ||
       [[ $INPUT_PCB_OUTPUT_GLB_SIMPLIFY =~ ^0+(\.0+)?$ ]]; }; then
    echo "::error::Invalid GLB simplify ratio '$INPUT_PCB_OUTPUT_GLB_SIMPLIFY'. Use a number above 0 and up to 1, for example 0.5."
    exit 1
  fi

  if [[ -n $INPUT_PCB_OUTPUT_GLB_SIMPLIFY_ERROR ]] &&
     ! [[ $INPUT_PCB_OUTPUT_GLB_SIMPLIFY_ERROR =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]]; then
    echo "::error::Invalid GLB simplify error '$INPUT_PCB_OUTPUT_GLB_SIMPLIFY_ERROR'. Use a number from 0 to 1, for example 0.01 for 1% deviation."
    exit 1
  fi

  if [[ -n $INPUT_PCB_OUTPUT_GLB_POSITION_BITS ]] &&
     { ! [[ $INPUT_PCB_OUTPUT_GLB_POSITION_BITS =~ ^[0-9]+$ ]] ||
       (( INPUT_PCB_OUTPUT_GLB_POSITION_BITS < 1 || INPUT_PCB_OUTPUT_GLB_POSITION_BITS > 16 )); }; then
    echo "::error::Invalid GLB position bits '$INPUT_PCB_OUTPUT_GLB_POSITION_BITS'. Use a whole number from 1 to 16."
    exit 1
  fi

  # -kn and -km are not optional and are deliberately not exposed as inputs.
  # Without them gltfpack merges across nodes and renames what it merges, which
  # would undo the naming the post-processing exists to produce and silently
  # break every material override and engine script written against those names.
  # They cost merging opportunities; the names are the more valuable half.
  gp=(gltfpack -kn -km -i "$INPUT_PCB_OUTPUT_GLB_FILE_NAME")

  case "$INPUT_PCB_OUTPUT_GLB_COMPRESSION" in
    meshopt) gp+=(-c) ;;
    meshopt-high) gp+=(-cc) ;;
  esac

  # Quantization is what KHR_mesh_quantization buys and it is most of the win
  # before compression. Turning it off leaves a plain glTF with no extensions at
  # all, for a loader that supports neither.
  if [[ $INPUT_PCB_OUTPUT_GLB_QUANTIZE == "true" ]]; then
    [[ -n $INPUT_PCB_OUTPUT_GLB_POSITION_BITS ]] && gp+=(-vp "$INPUT_PCB_OUTPUT_GLB_POSITION_BITS")
  else
    gp+=(-noq)
  fi

  if [[ -n $INPUT_PCB_OUTPUT_GLB_SIMPLIFY && $INPUT_PCB_OUTPUT_GLB_SIMPLIFY != 1 &&
        $INPUT_PCB_OUTPUT_GLB_SIMPLIFY != 1.0 ]]; then
    gp+=(-si "$INPUT_PCB_OUTPUT_GLB_SIMPLIFY")
    [[ -n $INPUT_PCB_OUTPUT_GLB_SIMPLIFY_ERROR ]] && gp+=(-se "$INPUT_PCB_OUTPUT_GLB_SIMPLIFY_ERROR")
    # A board is many separate solids that meet at shared edges. Collapsing a
    # vertex on such an edge opens a gap between the two parts that used to
    # share it, which reads as a hole in the board rather than as a lower
    # triangle count.
    gp+=(-slb)
  fi

  # gltfpack cannot write its output over its input, and a partial write must
  # not be able to replace a good GLB. Stage outside the workspace so a failure
  # leaves the export as it was and no stray file is left for a later
  # upload-artifact step to glob up.
  glb_opt_dir=$(mktemp -d)
  glb_opt_tmp="$glb_opt_dir/optimized.glb"

  glb_size_before=$(stat -c%s "$INPUT_PCB_OUTPUT_GLB_FILE_NAME")

  set +e
  "${gp[@]}" -o "$glb_opt_tmp"
  glb_opt_failure=$?
  set -e

  if [[ $glb_opt_failure -ne 0 ]]; then
    rm -rf "$glb_opt_dir"
    echo "::error::Failed to optimize the GLB mesh. gltfpack exit code: $glb_opt_failure"
    exit 1
  fi

  # gltfpack reports success for an input it could not make sense of, so check
  # that what came out is a glTF container before it replaces the one that is
  # known good.
  if [[ ! -s $glb_opt_tmp || $(head -c 4 "$glb_opt_tmp") != "glTF" ]]; then
    rm -rf "$glb_opt_dir"
    echo "::error::GLB mesh optimization produced no usable glTF file."
    exit 1
  fi

  mv -f "$glb_opt_tmp" "$INPUT_PCB_OUTPUT_GLB_FILE_NAME"
  rm -rf "$glb_opt_dir"

  glb_size_after=$(stat -c%s "$INPUT_PCB_OUTPUT_GLB_FILE_NAME")
  echo "::notice::GLB mesh optimized: $INPUT_PCB_OUTPUT_GLB_FILE_NAME ($((glb_size_before / 1024)) KiB -> $((glb_size_after / 1024)) KiB)"
fi
