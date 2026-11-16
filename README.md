# blender-reference-modeling

A [Claude Code](https://claude.com/claude-code) skill for **reference-exact 3D modeling in Blender**. Claude doesn't model from
imagination. It gets or generates a full 360° reference first, compares every part it builds against that reference,
keeps fixing until the model matches, and runs a technical verification before handing the model over.

## What it does

1. **Reference gate.** It needs 10 views: front, back, left, right, the four 3/4 views, top and bottom.
   - If you give images, it generates only the missing views from them.
   - Otherwise it uses an image generator: Codex CLI, any image-generation MCP (for example ForgeGUI), or Gemini CLI with an image extension.
   - **If no generator is available and views are missing, it stops and asks you for them.**
   - It also checks that the views agree with each other, so a reference with perspective errors is regenerated before modeling starts.
2. **Spec sheet.** Every visible part, with its count, color and size, the overall dimensions, and the weapon grip data.
3. **Part-by-part modeling.** After every part it renders the same views as the reference and builds comparison images
   (red = missing, blue = extra) with silhouette scores, then compares by eye as well. It keeps fixing until nothing differs.
4. **Restart rule.** It rebuilds a part, or the whole model, from scratch when fixing stops making progress.
5. **Final verification.**
   - z-fighting, clipping (object-object and self-intersection), floating parts, missing parts;
   - dimensions and proportions (perspective errors);
   - materials, textures and UVs;
   - weapon grip: attachment point, grip size for a hand, hand clearance;
   - normals and topology, plus scene hygiene.

   It delivers only when everything passes.

## Requirements

- Blender 4.x or 5.x (tested on **Blender 5.2**).
- Either a Blender MCP server connected to Claude Code (for example [blender-mcp](https://github.com/ahujasid/blender-mcp)),
  or Blender's headless mode (`blender -b`), which the scripts also support.
- Python 3 with `Pillow` and `numpy` (for `compare_views.py` and `split_sheet.py`).
- Optional, to generate references: [Codex CLI](https://github.com/openai/codex) with image generation, or any image-generation MCP.

## Install

```bash
git clone https://github.com/V1yn1/blender-reference-modeling ~/.claude/skills/blender-reference-modeling
```

Restart Claude Code. Then ask in normal words, for example *"model this sword in Blender"* or
*"make a sci-fi pistol in Blender, matte black slide, orange grip"*, or start a message with `/blender-reference-modeling`.

## Files

| file | purpose |
|---|---|
| `SKILL.md` | the workflow Claude follows |
| `scripts/render_views.py` | renders the 10 canonical views (orthographic, transparent PNG) inside Blender or headless |
| `scripts/compare_views.py` | reference vs. render: silhouette IoU, proportions, per-region diff, comparison sheets, cross-view consistency check |
| `scripts/verify_model.py` | technical verification (z-fighting, clipping, floating or missing parts, dimensions, materials/UVs, weapon grip, topology) |
| `scripts/split_sheet.py` | splits a generated turnaround sheet into one image per view |
| `references/` | generation prompts, spec template, troubleshooting |

## Status

The scripts were tested headless on Blender 5.2 with test models that had planted errors. Eevee in headless
mode can crash on some AMD GPU drivers, so the default renderer is Workbench.

## License

MIT
