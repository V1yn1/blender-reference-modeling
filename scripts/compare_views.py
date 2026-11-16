"""
compare_views.py - compare reference images against Blender renders, view by view.

Runs with normal Python (needs Pillow + numpy):
    python compare_views.py --ref C:/work/refs --render C:/work/renders/iter_03 --out C:/work/compare/iter_03
    python compare_views.py --ref C:/work/refs --check-ref-only --out C:/work/compare/refcheck

Files: a view is found in a folder if its stem is "<view>" or "ref_<view>" (png/jpg/jpeg/webp).
Views: front front_right right back_right back back_left left front_left top bottom
(see render_views.py for the exact camera convention).

For every view present in BOTH folders it writes:
    <out>/<view>_compare.png   4 panels: reference | model | silhouette overlay | 50% blend
                               overlay colours: RED = only in reference (model is MISSING it),
                                                BLUE = only in model (model has EXTRA), GREY = match
    <out>/contact_sheet.png    every view stacked (ref | model | overlay)
    <out>/summary.json / summary.md   IoU, aspect error, worst grid cells, edge/detail recall, colour delta
It also runs a CROSS-VIEW CONSISTENCY check on each image set (detects perspective errors / views that
disagree about proportions, e.g. width in the front view != width in the top view).

The numbers are a NECESSARY condition, never a sufficient one: always Read the *_compare.png images and
compare every detail by eye too.
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

VIEWS = ["front", "front_right", "right", "back_right", "back", "back_left", "left", "front_left", "top", "bottom"]
ORTHO = {"front", "right", "back", "left", "top", "bottom"}
EXTS = (".png", ".jpg", ".jpeg", ".webp")
GRID = 4
CELL_NAMES = {0: "top", 1: "upper-middle", 2: "lower-middle", 3: "bottom"}
COL_NAMES = {0: "left", 1: "center-left", 2: "center-right", 3: "right"}

# default pass thresholds (strict). Silhouette metrics are computed on a normalised 512px crop.
IOU_ORTHO = 0.95
IOU_ANGLED = 0.92
ASPECT_TOL = 0.02
CELL_IOU_MIN = 0.85
CONSISTENCY_TOL = 0.04


def find_view(folder, view):
    if not folder or not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        stem, ext = os.path.splitext(name)
        if ext.lower() in EXTS and stem.lower() in (view, f"ref_{view}"):
            return os.path.join(folder, name)
    return None


def load_rgba(path):
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)


def subject_mask(rgba, bg=None, thresh=32.0):
    """Foreground mask. Uses alpha if present, else distance from the border/background colour."""
    alpha = rgba[..., 3]
    if alpha.min() < 250:
        return alpha > 127
    rgb = rgba[..., :3]
    if bg is None:
        border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb - np.asarray(bg, np.float32)) ** 2).sum(-1))
    m = dist > thresh
    # light clean-up of speckles (median filter keeps thin details better than opening)
    mi = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3))
    return np.asarray(mi) > 127


def bbox(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def normalise(rgba, mask, size):
    """Crop to subject bbox, pad to square (centered), resize to size. Returns (rgb, mask, aspect, bbox)."""
    bb = bbox(mask)
    if bb is None:
        return None
    x0, y0, x1, y1 = bb
    w, h = x1 - x0, y1 - y0
    side = max(w, h)
    rgb = rgba[y0:y1, x0:x1, :3]
    m = mask[y0:y1, x0:x1]
    canvas = np.full((side, side, 3), 255, np.float32)
    mcanvas = np.zeros((side, side), bool)
    ox, oy = (side - w) // 2, (side - h) // 2
    canvas[oy:oy + h, ox:ox + w] = np.where(m[..., None], rgb, 255)
    mcanvas[oy:oy + h, ox:ox + w] = m
    rgb_img = Image.fromarray(canvas.astype(np.uint8)).resize((size, size), Image.LANCZOS)
    m_img = Image.fromarray((mcanvas * 255).astype(np.uint8)).resize((size, size), Image.NEAREST)
    return np.asarray(rgb_img).astype(np.float32), np.asarray(m_img) > 127, w / h, bb


def edges(rgb, mask):
    g = Image.fromarray(rgb.mean(-1).astype(np.uint8)).filter(ImageFilter.FIND_EDGES)
    e = np.asarray(g) > 40
    return e & mask


def dilate(m, k=5):
    return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(k))) > 127


def compare_view(view, ref_path, ren_path, out_dir, size, bg):
    ref = load_rgba(ref_path)
    ren = load_rgba(ren_path)
    rn = normalise(ref, subject_mask(ref, bg), size)
    mn = normalise(ren, subject_mask(ren, bg), size)
    if rn is None or mn is None:
        return {"view": view, "error": "empty subject in " + ("reference" if rn is None else "render")}
    rrgb, rm, rasp, _ = rn
    mrgb, mm, masp, _ = mn
    inter, union = (rm & mm), (rm | mm)
    iou = inter.sum() / max(union.sum(), 1)
    aspect_err = abs(masp - rasp) / rasp
    missing = rm & ~mm   # in ref, not in model
    extra = mm & ~rm     # in model, not in ref

    cells = []
    cs = size // GRID
    for gy in range(GRID):
        for gx in range(GRID):
            sl = (slice(gy * cs, (gy + 1) * cs), slice(gx * cs, (gx + 1) * cs))
            u = union[sl].sum()
            if u < (cs * cs) * 0.02:
                continue
            ciou = inter[sl].sum() / u
            cdiff = float(np.abs(rrgb[sl][inter[sl]] - mrgb[sl][inter[sl]]).mean()) if inter[sl].sum() else None
            cells.append({"cell": f"{CELL_NAMES[gy]}-{COL_NAMES[gx]}", "iou": round(float(ciou), 3),
                          "missing_px": int(missing[sl].sum()), "extra_px": int(extra[sl].sum()),
                          "mean_rgb_diff": None if cdiff is None else round(cdiff, 1)})
    cells.sort(key=lambda c: c["iou"])

    re_, me_ = edges(rrgb, rm), edges(mrgb, mm)
    edge_recall = float((re_ & dilate(me_)).sum() / max(re_.sum(), 1))      # ref details present in model
    edge_precision = float((me_ & dilate(re_)).sum() / max(me_.sum(), 1))   # model details present in ref
    color_diff = float(np.abs(rrgb[inter] - mrgb[inter]).mean()) if inter.sum() else None

    iou_min = IOU_ORTHO if view in ORTHO else IOU_ANGLED
    problems = []
    if iou < iou_min:
        problems.append(f"silhouette IoU {iou:.3f} < {iou_min}")
    if aspect_err > ASPECT_TOL:
        problems.append(f"proportions (bbox aspect) off by {aspect_err * 100:.1f}% (ref {rasp:.3f} vs model {masp:.3f})")
    for c in cells:
        if c["iou"] < CELL_IOU_MIN:
            kind = "MISSING material (red)" if c["missing_px"] >= c["extra_px"] else "EXTRA material (blue)"
            problems.append(f"region {c['cell']}: IoU {c['iou']} - {kind}")

    # ---- visual panels ----
    def panel(arr):
        return Image.fromarray(arr.astype(np.uint8))
    ov = np.full((size, size, 3), 255, np.uint8)
    ov[inter] = (170, 170, 170)
    ov[missing] = (230, 30, 30)
    ov[extra] = (30, 90, 230)
    blend = (rrgb * 0.5 + mrgb * 0.5)
    pad, lab = 8, 22
    W = size * 4 + pad * 5
    H = size + lab + pad * 2
    sheet = Image.new("RGB", (W, H), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    titles = [f"REFERENCE  {view}", f"MODEL  IoU={iou:.3f}", "OVERLAY red=missing blue=extra", "50% BLEND"]
    for i, img in enumerate([panel(rrgb), panel(mrgb), Image.fromarray(ov), panel(blend)]):
        x = pad + i * (size + pad)
        sheet.paste(img, (x, lab + pad))
        d.text((x, 4), titles[i], fill=(255, 255, 255))
    gstep = size // GRID
    for i in (1, 2):
        x = pad + i * (size + pad)
        for k in range(1, GRID):
            d.line([(x + k * gstep, lab + pad), (x + k * gstep, lab + pad + size)], fill=(255, 200, 0), width=1)
            d.line([(x, lab + pad + k * gstep), (x + size, lab + pad + k * gstep)], fill=(255, 200, 0), width=1)
    cpath = os.path.join(out_dir, f"{view}_compare.png")
    sheet.save(cpath)

    return {"view": view, "pass": not problems, "iou": round(float(iou), 4), "iou_required": iou_min,
            "aspect_ref": round(rasp, 4), "aspect_model": round(masp, 4), "aspect_error": round(float(aspect_err), 4),
            "edge_recall_ref_details_in_model": round(edge_recall, 3),
            "edge_precision_model_details_in_ref": round(edge_precision, 3),
            "mean_rgb_diff": None if color_diff is None else round(color_diff, 1),
            "worst_cells": cells[:4], "problems": problems, "compare_image": cpath}


def consistency(folder, bg, label):
    """Cross-view proportion check on one image set (orthographic views only)."""
    dims = {}
    for v in ORTHO:
        p = find_view(folder, v)
        if p:
            bb = bbox(subject_mask(load_rgba(p), bg))
            if bb:
                dims[v] = (bb[2] - bb[0], bb[3] - bb[1])
    issues, checks = [], []

    def ratio(v):
        w, h = dims[v]
        return w / h

    def chk(name, a, b):
        err = abs(a - b) / max(abs(b), 1e-9)
        checks.append({"check": name, "a": round(a, 4), "b": round(b, 4), "error": round(err, 4)})
        if err > CONSISTENCY_TOL:
            issues.append(f"{label}: {name} disagree by {err * 100:.1f}% -> perspective/proportion error")

    if "front" in dims and "back" in dims:
        chk("front vs back aspect (W/H)", ratio("front"), ratio("back"))
    if "left" in dims and "right" in dims:
        chk("left vs right aspect (D/H)", ratio("left"), ratio("right"))
    if "top" in dims and "bottom" in dims:
        chk("top vs bottom aspect (W/D)", ratio("top"), ratio("bottom"))
    side = "right" if "right" in dims else ("left" if "left" in dims else None)
    if "front" in dims and side and "top" in dims:
        # front W/H divided by side D/H = W/D, must equal top W/D
        chk(f"front+{side} implied W/D vs top W/D", ratio("front") / ratio(side), ratio("top"))
    return {"set": label, "views_found": sorted(dims), "checks": checks, "issues": issues}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--render", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--bg", default=None, help="reference background colour as R,G,B if auto-detect fails")
    ap.add_argument("--check-ref-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    bg = [float(x) for x in a.bg.split(",")] if a.bg else None

    missing_refs = [v for v in VIEWS if not find_view(a.ref, v)]
    report = {"reference_dir": a.ref, "render_dir": a.render, "missing_reference_views": missing_refs,
              "consistency": [consistency(a.ref, bg, "reference")], "views": []}
    if not a.check_ref_only and a.render:
        report["consistency"].append(consistency(a.render, bg, "model"))
        for v in VIEWS:
            rp, mp = find_view(a.ref, v), find_view(a.render, v)
            if rp and mp:
                report["views"].append(compare_view(v, rp, mp, a.out, a.size, bg))
            elif rp and not mp:
                report["views"].append({"view": v, "pass": False, "problems": ["no model render for this view"]})
        # contact sheet
        imgs = [Image.open(r["compare_image"]) for r in report["views"] if r.get("compare_image")]
        if imgs:
            s = 0.5
            th = [im.resize((int(im.width * s), int(im.height * s))) for im in imgs]
            sheet = Image.new("RGB", (max(t.width for t in th), sum(t.height for t in th)), (20, 20, 20))
            y = 0
            for t in th:
                sheet.paste(t, (0, y))
                y += t.height
            sheet.save(os.path.join(a.out, "contact_sheet.png"))

    ref_issues = report["consistency"][0]["issues"]
    view_fail = [r for r in report["views"] if not r.get("pass")]
    model_issues = report["consistency"][1]["issues"] if len(report["consistency"]) > 1 else []
    report["all_pass"] = (not missing_refs and not ref_issues and not model_issues
                          and not view_fail and bool(report["views"]) and not a.check_ref_only)
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    lines = ["# View comparison", ""]
    if missing_refs:
        lines.append(f"**MISSING REFERENCE VIEWS:** {', '.join(missing_refs)}  -> 360 coverage incomplete")
    for c in report["consistency"]:
        lines.append(f"- consistency [{c['set']}]: " + ("OK" if not c["issues"] else "; ".join(c["issues"])))
    lines += ["", "| view | pass | IoU | aspect err | detail recall | problems |", "|---|---|---|---|---|---|"]
    for r in report["views"]:
        lines.append(f"| {r['view']} | {'PASS' if r.get('pass') else 'FAIL'} | {r.get('iou', '-')} | "
                     f"{r.get('aspect_error', '-')} | {r.get('edge_recall_ref_details_in_model', '-')} | "
                     f"{'; '.join(r.get('problems', [])) or '-'} |")
    lines += ["", f"**ALL NUMERIC CHECKS PASS: {report['all_pass']}**",
              "(Numbers passing is required but NOT sufficient - inspect every *_compare.png by eye.)"]
    md = "\n".join(lines)
    with open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
