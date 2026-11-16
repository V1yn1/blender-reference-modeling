"""
split_sheet.py - cut a turnaround / multi-view reference sheet into one image per view.

    python split_sheet.py --sheet sheet.png --rows 2 --cols 5 \
        --order front,front_right,right,back_right,back,back_left,left,front_left,top,bottom \
        --out C:/work/refs [--trim] [--pad 0.06]

Cells are read left-to-right, top-to-bottom. Use "skip" in --order for cells to ignore (labels etc.).
--trim crops each cell to its subject (background = median border colour) and re-adds uniform padding,
so every view is centred the same way. Always Read the produced files to confirm the right view landed
in the right file - generators often place views in a different order than asked.
"""
import argparse
import os

import numpy as np
from PIL import Image


def trim(img, pad):
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    m = np.sqrt(((a - bg) ** 2).sum(-1)) > 32
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return img
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    crop = img.crop((x0, y0, x1, y1))
    p = int(max(crop.size) * pad)
    out = Image.new("RGB", (crop.width + 2 * p, crop.height + 2 * p), tuple(int(c) for c in bg))
    out.paste(crop.convert("RGB"), (p, p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--order", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trim", action="store_true")
    ap.add_argument("--pad", type=float, default=0.06)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    img = Image.open(a.sheet).convert("RGB")
    names = [n.strip() for n in a.order.split(",")]
    cw, ch = img.width / a.cols, img.height / a.rows
    k = 0
    for r in range(a.rows):
        for c in range(a.cols):
            if k >= len(names):
                break
            name = names[k]
            k += 1
            if name == "skip":
                continue
            cell = img.crop((int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch)))
            if a.trim:
                cell = trim(cell, a.pad)
            fp = os.path.join(a.out, f"ref_{name}.png")
            cell.save(fp)
            print("wrote", fp)


if __name__ == "__main__":
    main()
