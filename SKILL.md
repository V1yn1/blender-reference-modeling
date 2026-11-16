---
name: blender-reference-modeling
description: Reference-exact 3D modeling in Blender (via the Blender MCP or headless blender.exe). Use whenever the user asks to model, build, sculpt, recreate or fix a 3D model/asset/prop/weapon/character/vehicle in Blender, or to turn an image/concept into a Blender model. It requires a full 360 reference (8 turnaround views + top + bottom), generated with an image generator such as Codex, Gemini or ForgeGUI, or supplied by the user. If no generator is available and references are missing, it stops and asks. It compares every part against the reference and keeps fixing until the model matches exactly, restarts from zero when fixing stalls, and runs a full verification (textures, weapon grip, z-fighting, clipping, floating or missing parts, perspective and proportion errors) before delivery.
---

# Blender reference-exact modeling

The goal is a model that matches the reference down to the small details from every angle, and that
passes a full technical verification. "Close enough" does not count as done. You keep working until
the model matches and verifies, or until the user tells you to stop.

`SKILL_DIR` = the folder this SKILL.md is in (normally `~/.claude/skills/blender-reference-modeling`). Scripts are in
`SKILL_DIR/scripts`. Expand it to an absolute path before passing it to Blender.
Blender exe: find it once. Windows: `C:/Program Files/Blender Foundation/Blender <version>/blender.exe`; macOS:
`/Applications/Blender.app/Contents/MacOS/Blender`; Linux: `which blender`.

## Rules you never break

1. **No reference, no modeling.** You never model from imagination. Each of the 10 views must exist as an image
   before the first vertex is placed (see "Phase 1" below).
2. **No generator and missing views means STOP and ASK.** If you can't produce the missing views with a real image
   generator, stop and ask the user for them. List exactly which views are missing. Proceed with partial coverage
   only if the user says so in chat, and mark those views "UNVERIFIED" in the final report.
3. **Compare every piece.** After every part you build or change, render it and compare it with the reference
   views where it is visible. A part is done only when you can't find any difference.
4. **Never stop while the model differs from the reference.** Fix it. Never call a mismatch "stylistic" or
   "minor" so you can move on.
5. **Restart when fixing stalls** (see "Phase 4"). Patching a wrong base forever is worse than rebuilding it.
6. **Deliver only after Phase 5 passes**: every view matches, `verify_model` says PASS, and every WARN has been
   fixed or justified.
7. Never claim a check passed unless you ran it and saw the output. Never invent reference images.

## Workspace layout (create once per model)

```
<work>/                       e.g. a folder next to the user's project, or one they choose
  refs/        ref_front.png ... ref_bottom.png   (the 10 views, exact names below)
  spec.json    parts list + dimensions + weapon info (template: SKILL_DIR/references/spec_template.json)
  log.md       iteration log (attempt #, part, differences found, fixes, compare scores)
  renders/iter_NN/   compare/iter_NN/   verify/   versions/model_vNN.blend
```

### The 10 views (file names `ref_<view>.png`; same names for renders)
| view | camera | what it shows |
|---|---|---|
| front | at -Y looking +Y | the object's front |
| front_right | 45 deg between front and right | 3/4 front |
| right | at +X | the side on the RIGHT edge of the front view |
| back_right | 135 deg | 3/4 back |
| back | at +Y | the back |
| back_left | 225 deg | 3/4 back, other side |
| left | at -X | the side on the LEFT edge of the front view |
| front_left | 315 deg | 3/4 front, other side |
| top | at +Z looking down | front edge at the BOTTOM of the image |
| bottom | at -Z looking up | front edge at the TOP of the image |

Orientation in Blender: the object's front faces **-Y**, up is **+Z**. Weapons: the muzzle or blade tip points **-Y**
(so the side profile is the `right`/`left` view) unless the user's engine needs something else. Write the
choice into `spec.json`.

---

## Phase 0: Connect to Blender

1. Call `get_addon_status`, then `get_scene_info`. If they respond, use `execute_blender_code` to build,
   `look` (or `get_viewport_screenshot` on older add-on versions) for quick looks, and the scripts below for
   real checks. Don't swap modeling for `generate_3d` or `import_asset`: those don't match a reference exactly.
   Use them only if the user asks.
2. If the Blender MCP is missing or failed to connect, tell the user: *open Blender, press N in the 3D viewport,
   open the BlenderMCP tab, click "Connect to Claude", then restart or reconnect the `blender` MCP server.*
   While you wait, or if the user prefers, you can work **headless**: write the build as a Python script and run
   `"<blender.exe>" -b <work>/model.blend --python build_step.py`. Save the .blend after every step.
3. Run every helper script inside Blender like this (MCP `execute_blender_code`):
   ```python
   ns = {}; exec(open(r"SKILL_DIR/scripts/render_views.py", encoding="utf-8").read(), ns)
   ns["render_views"](r"<work>/renders/iter_01", resolution=1024)
   ```
   Or headless: `blender -b model.blend --python SKILL_DIR/scripts/render_views.py -- --out <dir>`.

## Phase 1: Reference gate (blocking)

**A. The user gave images.** Copy them into `refs/` and decide which of the 10 views each one is. Read every
image. If all 10 are covered, go to C. If views are missing, go to B to generate **only the missing views**,
conditioned on the user's images (pass them as input images) so the design stays identical.

**B. Find a generator.** Look in this order and use the first one that works:
1. **Codex CLI** (`where codex`). Image generation is a stable Codex feature. Run, for example:
   `codex exec --skip-git-repo-check -s workspace-write -C "<work>/refs" -i <input refs...> "<prompt> Save the final image as ref_<view>.png in the current directory."`
   If the file doesn't appear, take the newest PNG under `~/.codex/generated_images/` and copy it in.
2. **MCP image tools.** Use ToolSearch for "generate image" and "image generation". Example: ForgeGUI
   `generation_image`, or any Gemini, Imagen, OpenAI or Flux tool. Use it only if it actually returns an image file or URL you can save.
3. **Gemini CLI** (`where gemini`). This counts only if an image extension such as nanobanana appears in
   `gemini extensions list`. Plain Gemini CLI can't create images.

If nothing works: **STOP.** Tell the user no image generator is connected and ask for reference images
of the 10 views (list them by name with the descriptions above). Don't continue until they arrive.

Generation rules (full prompts in `references/generation_prompts.md`):
- First make one **master** image (front_right 3/4, the most informative view). Then generate every other view
  **with the master and every already-approved view as input images**, so it shows the same object.
- Use orthographic views, a plain flat light-grey background, even lighting, no cast shadows, no props, no text,
  the whole object in frame, and the same scale across views. 3/4 views are at eye level.
- If a generator returns a turnaround sheet, split it with
  `python SKILL_DIR/scripts/split_sheet.py --sheet s.png --rows R --cols C --order ... --out refs --trim`.

**C. Validate the reference set.** This step is required for user images too.
1. Run `python SKILL_DIR/scripts/compare_views.py --ref <work>/refs --check-ref-only --out <work>/compare/refcheck`.
   It lists missing views and runs the **cross-view consistency check**. If the front, side and top views disagree
   on proportions, that's a **perspective error in the reference**: regenerate that view (or ask the user). Don't
   model from references that contradict each other.
2. Read all 10 images yourself. Every feature, such as each screw, vent, strap, engraving, color band or bevel, must
   appear consistently in every view that should show it. Regenerate any view that contradicts the others.
3. Show the user the reference set (a quick contact sheet or the files) and keep going. They can interrupt you
   to change the design.

## Phase 2: Spec sheet (from the references, before modeling)

Write `spec.json` (template in `references/spec_template.json`):
- `dimensions` [X width, Y depth, Z height] in meters. Measure pixel ratios from the orthographic refs and choose a
  real-world scale (ask the user if the real size matters and isn't obvious, such as the length of a specific gun model).
- `parts`: **every** visible piece, with name, count, color hex, the views it appears in, and shape notes. Count
  repeated details (5 screws means `count: 5`). This list is what the missing-parts check uses.
- `allowed_intersections` (intentional hidden embeds, each with a reason) and `intentionally_separate`.
- `weapon` block for anything held in a hand: type, grip_part, grip_point (an Empty), forward_axis, grip_axis, hands.
Also add the refs to Blender as reference images (Empty > Image) aligned to the front, side and top views at the
measured scale, in a collection excluded from render, so you can model directly on top of them.

## Phase 3: Build part by part, comparing every piece

Order: big masses and silhouette first, then secondary forms, then small details, then materials and textures.
For **each part**:
1. Build it on top of the reference image planes, with names that match `spec.json`.
2. Render the views where it's visible, or a close-up framed on it:
   `render_views(out, views=[...], frame_objects=["PartName"])`. For textures, use `color="texture"`.
3. Compare: run `compare_views.py --ref refs --render <renders> --out <compare>` and **Read** the `*_compare.png`
   images. Red means it's in the reference but missing from the model. Blue means the model has extra.
   Then compare by eye: shape, position, size relative to its neighbors, bevels, thickness, hole and screw
   count, color, and texture placement.
4. Write every difference into `log.md`, fix each one, then render and compare again. The part is done only when
   you find **zero** differences.

After all parts are built, run the **full 10-view compare** of the whole model. Pass thresholds (minimums only):
IoU ≥ 0.95 for the orthographic views and ≥ 0.92 for the 3/4 views, aspect error ≤ 2%, no grid cell under 0.85, and
model consistency OK. Then compare every view **by eye** at full resolution. Passing numbers with a visible
difference still means it isn't done.

## Phase 4: Iterate, and restart from zero when it stalls

Keep a counter in `log.md`. **Rebuild a part from scratch** (delete it, rebuild it clean using what you learned)
when any of these is true:
- 4 fix attempts on the same part haven't removed its differences, or the scores got worse;
- its topology became messy from patching (stacked booleans, shading artifacts, non-manifold edges).

**Restart the whole model from zero** (save `versions/model_vNN.blend` first, then start a new empty file) when any of these is true:
- after 3 full-model compare rounds, the average IoU improved by less than 0.01 per round, or any orthographic view is still under 0.85;
- the base proportions or blockout are wrong, so that fixing them would mean moving most parts;
- more than 40% of the parts have already been rebuilt, or 12 full rounds passed without converging.
Before you restart, write in `log.md` what went wrong and how the new attempt will avoid it, such as better
measurements, a different modeling approach, or a better blockout. The restart begins at Phase 3 with the same refs and spec.

## Phase 5: Final verification (all of it must pass)

1. Run `verify_model` (in Blender: `ns["verify_model"](spec_path=..., out_path=<work>/verify/report.json)`).
   It checks:
   - **z-fighting**: coplanar overlapping faces;
   - **clipping**: object-to-object penetration and self-intersection;
   - **floating parts**: islands that don't touch the rest of the model;
   - **missing parts**: compared with `spec.parts`, including counts;
   - **dimensions and proportions**: perspective errors;
   - **materials, textures and UVs**: missing materials or images, no UVs, stretching, colors compared with the spec;
   - **weapon grip**: grip point exists and sits on the grip, grip length and thickness fit a hand, hand clearance, forward axis;
   - **topology and normals**: loose geometry, duplicate vertices, faces shared by more than 2 edges, flipped normals;
   - **hygiene**: unapplied or negative scale, hidden leftover objects.
   Fix every FAIL. For every WARN, render a close-up (`frame_objects=[...]`) and fix it, or write down why it's fine.
2. Do visual checks the script can't fully judge:
   - **textures**: use `color="texture"` or `engine="eevee"` (Eevee needs a World or lights). Check seams, scale and direction against the refs.
   - **weapon grip**: grip close-ups from all 10 views. A hand must fit naturally. The trigger must be reachable from the grip. The guard and pommel must not block the hand. The grip angle must match the ref.
   - **perspective**: the model's own consistency check in `compare_views` must be OK, and the 3/4 views must match.
   - **clipping and z-fighting hotspots**: close-ups at every `at` location the report gives.
3. Run a final full 10-view compare. Every view must pass both by the numbers and by eye.
4. Re-run 1 to 3 after **any** fix. A fix can break something else.

## Delivery

Only when Phase 5 is fully green:
- Save `<work>/model_final.blend`, and export the format the user needs (FBX, GLB or OBJ) with transforms applied.
- Give the user: a final contact sheet (reference vs. model, all 10 views), the `verify_model` verdict with its numbers,
  the list of WARNs with their justification, any views marked UNVERIFIED, and the number of iterations or restarts it took.

## Reference files
- `references/generation_prompts.md`: prompt templates for the master and each view, plus consistency tips.
- `references/spec_template.json`: spec format with comments.
- `references/troubleshooting.md`: common failure patterns and how to fix them in Blender.
