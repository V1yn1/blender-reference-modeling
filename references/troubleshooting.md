# Troubleshooting: what the checks mean and how to fix it

| finding | usual cause | fix in Blender |
|---|---|---|
| z-fighting (same direction) | decal or panel placed exactly on a surface; duplicated faces | move the decal out along its normal by at least the reported offset, or Solidify it; delete duplicate faces (Select > Select All by Trait > Interior Faces / Merge by Distance) |
| z-fighting (back-to-back, open mesh) | single-sided plane doubled, or a plane inside a closed surface | delete the hidden plane or give it thickness |
| clipping between objects | part pushed too deep, wrong scale, or a boolean leftover | move/scale it to sit ON the surface; if it is a deliberate hidden embed (screw shank), add it to `allowed_intersections` with a reason |
| self-intersection | a mesh folds through itself (bad extrude, mirror without clipping) | enable Mirror > Clipping, fix the extrude, Select > Select All by Trait > Intersect (Mesh > Clean Up) |
| floating part | part placed by eye with a small gap | snap it (Snap to Face, or Shift+S to cursor at the contact vertex) so its contact face meets the mount |
| missing part | the part isn't in the scene, or its name doesn't match the spec | model it, or rename it to the spec name |
| proportions / dimensions | measured from a perspective reference, or a wrong scale | re-measure from the orthographic refs and scale the whole blockout; if the reference itself failed the consistency check, regenerate the reference |
| inward or flipped normals | mirrored with negative scale, bad extrude | Apply scale, then Mesh > Normals > Recalculate Outside; check with Overlays > Face Orientation |
| duplicate vertices / zero-area faces | stacked extrudes, merged loops | Mesh > Clean Up > Merge by Distance, then Delete Loose / Degenerate Dissolve |
| no material / missing texture file | material not assigned, or the image path moved | assign the material; File > External Data > Pack Resources, or fix the path |
| UV stretching / zero UV area | not unwrapped after editing | mark seams and unwrap again (or Smart UV Project), then check with a checker texture |
| grip too thick or thin | proportions drifted | rescale the grip cross-section to the reference and the human range; confirm with a grip close-up |
| hand-clearance WARN | trigger guard, magazine or guard sits where the fingers go | confirm on the close-up render; move the part, or whitelist it in `weapon.hand_clearance_allowed` |

## Comparison problems
- **IoU low everywhere with the same aspect**: the reference segmentation failed (background too close to the object color).
  Pass `--bg R,G,B` explicitly, or regenerate the reference on a contrasting flat background.
- **Aspect error only in 3/4 views**: the reference 3/4 view was drawn in perspective or from above. Try
  `render_views(..., elevation=15)` or `persp_lens=50` to match it, but treat the orthographic views as the ground truth.
- **Left and right look swapped**: check the view convention. `right` is the camera at +X, which shows the side on
  the right edge of the front view. Rename the reference files if the generator mixed them up.
- **Eevee crashes headless** (AMD driver `atio6axx.dll`): use Workbench (`color="texture"`), or render inside the running Blender via MCP.
