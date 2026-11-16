# Reference generation prompts

Use these with whichever generator you found (Codex CLI, an MCP image tool, or Gemini with an image extension).
Replace `<OBJECT>` with a detailed description that covers shape, materials, colors, every distinct part, wear,
and style (realistic, stylized or low-poly). Reuse **exactly the same `<OBJECT>` text** in every prompt.

## Shared style block (append to every prompt)
```
Orthographic technical reference view (no perspective distortion, parallel projection), the whole object
fully in frame and centered with a small margin, plain flat light-grey (#C8C8C8) background, soft even studio
lighting, no cast shadows, no reflections of the environment, no text, no labels, no watermark, no other
objects, no hands, no stand. Ultra-detailed and crisp, every small detail visible (screws, seams, engravings,
vents, stitching, decals). Same object, same scale, same colors and materials as the provided reference images.
```

## 1. Master (front_right 3/4)
```
Product turnaround reference of <OBJECT>. View: three-quarter view from the front-right at eye level
(camera 45 degrees between the object's front and its right side, horizontal). <STYLE BLOCK>
```
Check the master carefully, because every other view inherits from it. Regenerate it until every part from the
user's description is present.

## 2. Each remaining view (attach the master and every approved view as input images)
```
This is the exact same object as in the attached reference images: <OBJECT>.
Draw it from this view only: <VIEW SENTENCE>. Keep every detail identical to the references: same parts,
same count of screws/holes/vents, same colors, same proportions. <STYLE BLOCK>
```
View sentences (object front faces the `front` camera):
- front: "straight-on orthographic FRONT view, looking directly at the object's front face"
- right: "straight-on orthographic side view of the object's RIGHT side (the side that appears on the right edge of the front view)"
- back: "straight-on orthographic BACK view"
- left: "straight-on orthographic side view of the object's LEFT side (the side that appears on the left edge of the front view)"
- front_left / back_right / back_left: "three-quarter view at eye level, camera 45 degrees between the <A> and the <B>"
- top: "straight-down orthographic TOP view (camera directly above), with the object's front edge at the BOTTOM of the image"
- bottom: "straight-up orthographic BOTTOM view (camera directly below), with the object's front edge at the TOP of the image"

For weapons, also describe the view in object terms so the generator can't get confused. For example, with the
muzzle pointing -Y: "front = looking straight down the barrel at the muzzle"; "right = full side profile with the
muzzle pointing LEFT"; "left = full side profile with the muzzle pointing RIGHT"; "top = looking down on the top of
the slide or receiver, muzzle at the bottom of the image".

## 3. Optional: one-shot turnaround sheet
Some generators keep a design more consistent inside one image:
```
Character/prop turnaround sheet of <OBJECT>: a 2 rows x 5 columns grid of equally sized cells, same scale in every cell.
Row 1: front, front-right 3/4, right side, back-right 3/4, back. Row 2: back-left 3/4, left side, front-left 3/4,
top view (front edge at bottom), bottom view (front edge at top). <STYLE BLOCK>
```
Split it with `split_sheet.py --rows 2 --cols 5 --order front,front_right,right,back_right,back,back_left,left,front_left,top,bottom --trim`.
Sheets have low resolution per view, so regenerate any view where small details are unreadable, using the sheet as an input image.

## Consistency review (required)
Make a feature checklist from the master, such as "3 vents on left side, 2 screws on grip panel, red stripe on
stock". Check every view against it. A view that contradicts the checklist or the cross-view proportion check
(`compare_views.py --check-ref-only`) gets regenerated. Never "average" contradictory views while modeling.
