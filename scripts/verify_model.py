"""
verify_model.py - final (and intermediate) technical verification of a Blender model.

Checks: scene hygiene & transforms, topology (non-manifold, loose geo, degenerate faces, duplicate verts),
normals (flipped / inconsistent), CLIPPING (object-object and self intersections), Z-FIGHTING (coplanar
overlapping faces), FLOATING PARTS (islands not touching the rest of the model), MISSING PARTS (vs spec),
overall DIMENSIONS (vs spec), MATERIALS / TEXTURES / UVs, and WEAPON GRIP (grip point, grip size,
hand clearance, forward axis).

Usage inside Blender (Blender MCP `execute_blender_code`):
    ns = {}
    exec(open(r"C:/Users/<you>/.claude/skills/blender-reference-modeling/scripts/verify_model.py", encoding="utf-8").read(), ns)
    rep = ns["verify_model"](spec_path=r"C:/work/spec.json", out_path=r"C:/work/verify/report.json")
    print(rep["summary_text"])

Headless:
    blender -b model.blend --python verify_model.py -- --spec C:/work/spec.json --out C:/work/verify/report.json

Result: rep["verdict"] is "PASS" only when there are zero FAIL items. WARN items must each be inspected
(render a close-up) and either fixed or explicitly justified in the delivery report.
"""
import bpy
import bmesh
import fnmatch
import json
import math
import os
import sys
from collections import defaultdict
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree
from mathutils import geometry as mgeo

try:
    import numpy as np
except ImportError:  # Blender always ships numpy, but be safe
    np = None

HAND_RANGES = {
    # type: (min grip length per hand [m], min cross-section [m], max cross-section [m], hands)
    "pistol": (0.075, 0.022, 0.050, 1), "smg": (0.075, 0.022, 0.050, 1), "rifle": (0.075, 0.022, 0.055, 1),
    "shotgun": (0.075, 0.022, 0.055, 1), "knife": (0.085, 0.015, 0.045, 1), "dagger": (0.085, 0.015, 0.045, 1),
    "sword": (0.090, 0.018, 0.045, 1), "greatsword": (0.170, 0.020, 0.050, 2), "katana": (0.200, 0.020, 0.045, 2),
    "axe": (0.100, 0.020, 0.050, 1), "greataxe": (0.250, 0.022, 0.055, 2), "hammer": (0.100, 0.020, 0.050, 1),
    "mace": (0.100, 0.020, 0.050, 1), "spear": (0.300, 0.020, 0.045, 2), "staff": (0.300, 0.020, 0.050, 2),
    "bow": (0.090, 0.020, 0.050, 1), "tool": (0.085, 0.015, 0.055, 1), "other": (0.085, 0.015, 0.060, 1),
}


# ----------------------------------------------------------------------------- helpers
class Report:
    def __init__(self):
        self.items = []

    def add(self, level, check, msg, **data):
        self.items.append({"level": level, "check": check, "msg": msg, **data})

    def fail(self, check, msg, **d):
        self.add("FAIL", check, msg, **d)

    def warn(self, check, msg, **d):
        self.add("WARN", check, msg, **d)

    def ok(self, check, msg, **d):
        self.add("OK", check, msg, **d)


def _match(name, patterns):
    return any(fnmatch.fnmatchcase(name, p) for p in (patterns or []))


def _pair_allowed(a, b, allowed):
    for rule in allowed or []:
        pa, pb = rule.get("a", "*"), rule.get("b", "*")
        if (fnmatch.fnmatchcase(a, pa) and fnmatch.fnmatchcase(b, pb)) or \
           (fnmatch.fnmatchcase(b, pa) and fnmatch.fnmatchcase(a, pb)):
            return rule.get("reason", "allowed by spec")
    return None


def _world_bm(obj, deps):
    eo = obj.evaluated_get(deps)
    me = eo.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.transform(eo.matrix_world)
    if eo.matrix_world.determinant() < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)  # match what the renderer shows
    eo.to_mesh_clear()
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.normal_update()
    return bm


def _islands(bm):
    """Return list of lists of face indices (connected by shared vertices)."""
    seen = set()
    islands = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, isl = [f], []
        seen.add(f.index)
        while stack:
            cur = stack.pop()
            isl.append(cur.index)
            for v in cur.verts:
                for nf in v.link_faces:
                    if nf.index not in seen:
                        seen.add(nf.index)
                        stack.append(nf)
        islands.append(isl)
    return islands


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def _bbox_gap(a, b):
    (alo, ahi), (blo, bhi) = a, b
    d = 0.0
    for i in range(3):
        g = max(blo[i] - ahi[i], alo[i] - bhi[i], 0.0)
        d += g * g
    return math.sqrt(d)


def _signed_volume(bm, faces):
    vol = 0.0
    for fi in faces:
        f = bm.faces[fi]
        vs = [v.co for v in f.verts]
        for i in range(1, len(vs) - 1):
            vol += vs[0].dot(vs[i].cross(vs[i + 1])) / 6.0
    return vol


def _point_inside(bvh, p, diag):
    """Ray parity test (+X direction) for closed meshes."""
    hits, origin, direction = 0, p.copy(), Vector((1.0, 0.0001, 0.00013)).normalized()
    for _ in range(200):
        loc, nrm, idx, dist = bvh.ray_cast(origin, direction, diag * 4)
        if loc is None:
            break
        hits += 1
        origin = loc + direction * max(diag * 1e-6, 1e-7)
    return hits % 2 == 1


# ----------------------------------------------------------------------------- main
def verify_model(spec_path=None, spec=None, out_path=None, collection=None, max_tris_zfight=400000):
    rep = Report()
    if spec is None and spec_path:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
    spec = spec or {}
    collection = collection or spec.get("collection")
    deps = bpy.context.evaluated_depsgraph_get()

    if collection:
        col = bpy.data.collections.get(collection)
        if col is None:
            rep.fail("scene", f"collection '{collection}' from spec does not exist")
            all_objs = []
        else:
            all_objs = list(col.all_objects)
    else:
        all_objs = list(bpy.context.scene.objects)

    meshes = []
    for o in all_objs:
        hidden = False
        try:
            hidden = (not o.visible_get()) or o.hide_render
        except RuntimeError:
            hidden = True
        if o.type == "MESH":
            if hidden:
                rep.warn("hygiene", f"'{o.name}' is a hidden mesh - delete it or unhide it (hidden leftovers ship with the file)")
            else:
                meshes.append(o)
        elif o.type in {"CURVE", "SURFACE", "META", "FONT"} and not hidden:
            rep.warn("hygiene", f"'{o.name}' is a {o.type} object - convert to mesh before delivery so it can be verified/exported")
    if not meshes:
        rep.fail("scene", "no visible mesh objects to verify")
        return _finish(rep, spec, out_path, {})

    # ---------------- per-object data
    data = {}
    total_tris = 0
    allowed = spec.get("allowed_intersections", [])
    separate = spec.get("intentionally_separate", [])
    for o in meshes:
        bm = _world_bm(o, deps)
        if len(bm.faces) == 0:
            rep.fail("geometry", f"'{o.name}' has no faces")
            bm.free()
            continue
        tris = bm.calc_loop_triangles()
        total_tris += len(tris)
        data[o.name] = {"obj": o, "bm": bm, "tris": tris}

    all_pts = [v.co for d in data.values() for v in d["bm"].verts]
    lo, hi = _bbox(all_pts)
    size = hi - lo
    diag = max(size.length, 1e-6)
    contact_tol = float(spec.get("contact_tolerance") or max(1e-4, diag * 0.002))
    plane_tol = float(spec.get("zfight_plane_tolerance") or max(1e-6, diag * 2e-4))
    stats = {"objects": len(data), "triangles": total_tris,
             "bbox_size_xyz": [round(size.x, 5), round(size.y, 5), round(size.z, 5)],
             "contact_tolerance": contact_tol, "zfight_plane_tolerance": plane_tol}

    # ---------------- transforms / hygiene
    for name, d in data.items():
        o = d["obj"]
        s = o.matrix_world.to_scale()
        if o.matrix_world.determinant() < 0:
            rep.fail("transforms", f"'{name}' has NEGATIVE scale (mirrored) - apply scale and recalc normals")
        if any(abs(abs(c) - 1.0) > 1e-4 for c in o.scale):
            rep.warn("transforms", f"'{name}' has unapplied scale {tuple(round(c, 4) for c in o.scale)} - apply (Ctrl+A) unless it is animated")
        for m in o.modifiers:
            if not m.show_render:
                rep.warn("hygiene", f"'{name}' modifier '{m.name}' is disabled for render")
        ngons = sum(1 for f in d["bm"].faces if len(f.verts) > 4)
        if ngons:
            rep.warn("topology", f"'{name}' has {ngons} n-gons (>4 sides) - check shading artifacts in close-up renders", count=ngons)

    # ---------------- topology & normals
    for name, d in data.items():
        bm = d["bm"]
        wire = [e for e in bm.edges if len(e.link_faces) == 0]
        loose_v = [v for v in bm.verts if len(v.link_edges) == 0]
        multi = [e for e in bm.edges if len(e.link_faces) > 2]
        boundary = [e for e in bm.edges if len(e.link_faces) == 1]
        degenerate = [f for f in bm.faces if f.calc_area() < (diag * 1e-6) ** 2]
        dmap = bmesh.ops.find_doubles(bm, verts=list(bm.verts), dist=max(1e-6, diag * 1e-6))["targetmap"]
        if loose_v:
            rep.fail("topology", f"'{name}': {len(loose_v)} loose vertices", count=len(loose_v))
        if wire:
            rep.fail("topology", f"'{name}': {len(wire)} loose edges (no faces)", count=len(wire))
        if multi:
            rep.fail("topology", f"'{name}': {len(multi)} non-manifold edges shared by >2 faces (internal faces?)", count=len(multi),
                     sample=[list(map(lambda x: round(x, 4), ((e.verts[0].co + e.verts[1].co) / 2))) for e in multi[:3]])
        if degenerate:
            rep.fail("topology", f"'{name}': {len(degenerate)} zero-area faces", count=len(degenerate))
        if dmap:
            rep.fail("topology", f"'{name}': {len(dmap)} duplicate (overlapping) vertices - merge by distance", count=len(dmap))
        if boundary:
            rep.warn("topology", f"'{name}': {len(boundary)} open boundary edges (mesh not closed) - fine for planes/cloth, otherwise a hole",
                     count=len(boundary))
        # normals consistency per island
        islands = _islands(bm)
        d["islands"] = islands
        bm2 = bm.copy()
        bm2.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm2, faces=bm2.faces)
        bm2.faces.ensure_lookup_table()
        flipped = [f.index for f in bm.faces if f.normal.dot(bm2.faces[f.index].normal) < 0]
        bm2.free()
        closed_isl_faces = set()
        for isl in islands:
            edges_closed = all(len(e.link_faces) == 2 for fi in isl for e in bm.faces[fi].edges)
            if edges_closed:
                closed_isl_faces.update(isl)
                if _signed_volume(bm, isl) < 0:
                    rep.fail("normals", f"'{name}': a closed part ({len(isl)} faces) has ALL normals pointing inward",
                             at=[round(c, 4) for c in bm.faces[isl[0]].calc_center_median()])
        bad = [fi for fi in flipped if fi in closed_isl_faces]
        if bad and len(bad) < len(closed_isl_faces):
            rep.fail("normals", f"'{name}': {len(bad)} faces have inconsistent (flipped) normals", count=len(bad),
                     at=[round(c, 4) for c in bm.faces[bad[0]].calc_center_median()])
        elif flipped and not bad:
            rep.warn("normals", f"'{name}': {len(flipped)} faces on open parts may be flipped - check with Face Orientation overlay")

    # ---------------- clipping: object-object intersections
    names = list(data.keys())
    for d in data.values():
        bm = d["bm"]
        d["bvh"] = BVHTree.FromBMesh(bm, epsilon=0.0)
        d["bbox"] = _bbox([v.co for v in bm.verts])
        d["open"] = any(len(e.link_faces) < 2 for e in bm.edges)
        # shrunk copy (every vertex pushed inward by contact_tol along its normal): parts that only TOUCH
        # flush separate after shrinking, parts that really PENETRATE each other still overlap.
        d["bvh_shrunk"] = BVHTree.FromPolygons([v.co - v.normal * contact_tol for v in bm.verts],
                                               [[v.index for v in f.verts] for f in bm.faces], epsilon=0.0)
    clip_pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = data[names[i]], data[names[j]]
            if _bbox_gap(a["bbox"], b["bbox"]) > 0:
                continue
            pairs = a["bvh_shrunk"].overlap(b["bvh_shrunk"])
            if not pairs:
                continue
            at = a["bm"].faces[pairs[0][0]].calc_center_median()
            reason = _pair_allowed(names[i], names[j], allowed)
            entry = dict(a=names[i], b=names[j], face_pairs=len(pairs), at=[round(c, 4) for c in at])
            clip_pairs.append(entry)
            if reason:
                rep.ok("clipping", f"'{names[i]}' x '{names[j]}' intersect ({len(pairs)} face pairs) - allowed: {reason}", **entry)
            else:
                rep.fail("clipping", f"'{names[i]}' and '{names[j]}' INTERSECT ({len(pairs)} face pairs). Render a close-up: "
                         "if a part visibly pokes through, fix it; if it is a hidden intentional embed, add it to "
                         "spec.allowed_intersections with a reason", **entry)
    # self intersections (different islands inside one object, or a mesh folding through itself)
    for name, d in data.items():
        if len(d["bm"].faces) > 60000:
            rep.warn("clipping", f"'{name}': self-intersection test skipped (too many faces)")
            continue
        bm = d["bm"]
        pairs = d["bvh"].overlap(d["bvh"])
        real = []
        for fa, fb in pairs:
            if fa >= fb:
                continue
            va = {v.index for v in bm.faces[fa].verts}
            if va & {v.index for v in bm.faces[fb].verts}:
                continue
            real.append((fa, fb))
        if real:
            rep.fail("clipping", f"'{name}': {len(real)} self-intersecting face pairs (parts of the same object pass through each other)",
                     count=len(real), at=[round(c, 4) for c in bm.faces[real[0][0]].calc_center_median()])

    # ---------------- z-fighting (coplanar overlapping faces)
    if total_tris <= max_tris_zfight:
        buckets = defaultdict(list)
        for oi, (name, d) in enumerate(data.items()):
            bm = d["bm"]
            for lt in d["tris"]:
                p = [l.vert.co for l in lt]
                n = (p[1] - p[0]).cross(p[2] - p[0])
                if n.length < 1e-14:
                    continue
                n.normalize()
                s = 1.0
                for c in n:
                    if abs(c) > 1e-6:
                        s = 1.0 if c > 0 else -1.0
                        break
                nc = n * s
                dd = nc.dot(p[0])
                key = (round(nc.x * 50), round(nc.y * 50), round(nc.z * 50), math.floor(dd / plane_tol))
                vids = frozenset(l.vert.index for l in lt)
                buckets[key].append((oi, name, vids, p, nc, dd, n.dot(nc) > 0, lt[0].face.index))
        zf = defaultdict(lambda: {"count": 0, "at": None, "facing": set()})
        keys = list(buckets.keys())
        for key in keys:
            group = list(buckets[key])
            nxt = (key[0], key[1], key[2], key[3] + 1)
            group_b = buckets.get(nxt, [])
            cand = group + group_b
            if len(cand) < 2:
                continue
            # 2D basis of the plane
            nc = cand[0][4]
            u = nc.orthogonal().normalized()
            v = nc.cross(u).normalized()
            proj = []
            for t in cand:
                pts2 = [Vector((q.dot(u), q.dot(v))) for q in t[3]]
                c = (pts2[0] + pts2[1] + pts2[2]) / 3
                pts2 = [c + (q - c) * 0.97 for q in pts2]  # shrink 3% so edge-touching neighbours don't count
                xs = [q.x for q in pts2]
                proj.append((min(xs), max(xs), min(q.y for q in pts2), max(q.y for q in pts2), pts2))
            order = sorted(range(len(cand)), key=lambda k: proj[k][0])
            active = []
            n_a = len(group)
            for k in order:
                x0 = proj[k][0]
                active = [a for a in active if proj[a][1] >= x0]
                for a in active:
                    if (a >= n_a) and (k >= n_a):
                        continue  # both from the neighbour bucket - handled when that bucket is processed
                    ta, tb = cand[a], cand[k]
                    if abs(ta[5] - tb[5]) > plane_tol:
                        continue
                    if ta[0] == tb[0] and (ta[2] & tb[2] or ta[7] == tb[7]):
                        continue
                    if proj[a][3] < proj[k][2] or proj[k][3] < proj[a][2]:
                        continue
                    if mgeo.intersect_tri_tri_2d(*proj[a][4], *proj[k][4]):
                        pk = tuple(sorted((ta[1], tb[1])))
                        e = zf[pk]
                        e["count"] += 1
                        e["facing"].add("same direction" if ta[6] == tb[6] else "back-to-back")
                        if e["at"] is None:
                            cc = (ta[3][0] + ta[3][1] + ta[3][2]) / 3
                            e["at"] = [round(c, 4) for c in cc]
                active.append(k)
        real_zf = 0
        for (a, b), e in zf.items():
            where = f"'{a}'" if a == b else f"'{a}' and '{b}'"
            if e["facing"] == {"back-to-back"} and not data[a]["open"] and not data[b]["open"]:
                # two closed parts pressed flush together: the touching faces are hidden inside the contact
                rep.ok("z-fighting", f"{where}: flush contact ({e['count']} hidden back-to-back face pairs) - not visible",
                       a=a, b=b, at=e["at"])
                continue
            real_zf += 1
            rep.fail("z-fighting", f"{where}: {e['count']} coplanar overlapping triangle pairs ({', '.join(sorted(e['facing']))}) "
                     f"-> Z-FIGHTING. Offset one surface (>= {plane_tol * 5:.5f}) or delete the hidden duplicate face",
                     a=a, b=b, count=e["count"], at=e["at"])
        if not real_zf:
            rep.ok("z-fighting", "no coplanar overlapping faces found")
    else:
        rep.warn("z-fighting", f"skipped: {total_tris} triangles > limit {max_tris_zfight}; decimate a copy or raise the limit")

    # ---------------- floating parts (connectivity graph over all islands of all objects)
    units = []  # (objname, island faces, bbox, bvh, sample pts)
    for name, d in data.items():
        bm = d["bm"]
        for isl in d["islands"]:
            vids = sorted({v.index for fi in isl for v in bm.faces[fi].verts})
            verts = [bm.verts[i].co.copy() for i in vids]
            remap = {vi: k for k, vi in enumerate(vids)}
            polys = [[remap[v.index] for v in bm.faces[fi].verts] for fi in isl]
            bvh = BVHTree.FromPolygons(verts, polys, epsilon=0.0)
            step = max(1, len(verts) // 600)
            units.append({"obj": name, "nfaces": len(isl), "bbox": _bbox(verts), "bvh": bvh,
                          "samples": verts[::step], "verts": verts, "polys": polys})
    parent = list(range(len(units)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    enclosed = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            A, B = units[i], units[j]
            if _bbox_gap(A["bbox"], B["bbox"]) > contact_tol:
                continue
            touching = bool(A["bvh"].overlap(B["bvh"]))
            if not touching:
                for P, Q in ((A, B), (B, A)):
                    for pt in P["samples"]:
                        hit = Q["bvh"].find_nearest(pt, contact_tol)
                        if hit[0] is not None:
                            touching = True
                            break
                    if touching:
                        break
            if not touching:
                # fully inside another closed island?
                for P, Q in ((A, B), (B, A)):
                    blo, bhi = Q["bbox"]
                    plo, phi = P["bbox"]
                    if all(blo[k] <= plo[k] and phi[k] <= bhi[k] for k in range(3)) and _point_inside(Q["bvh"], P["verts"][0], diag):
                        touching = True
                        enclosed.append((P["obj"], Q["obj"]))
                        break
            if touching:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comps = defaultdict(list)
    for k in range(len(units)):
        comps[find(k)].append(k)
    stats["connected_components"] = len(comps)
    if len(comps) > 1:
        main = max(comps.values(), key=lambda c: sum(units[k]["nfaces"] for k in c))
        for c in comps.values():
            if c is main:
                continue
            objs = sorted({units[k]["obj"] for k in c})
            lo_c, hi_c = _bbox([p for k in c for p in units[k]["verts"]])
            ctr = [round(x, 4) for x in (lo_c + hi_c) / 2]
            if all(_match(o, separate) for o in objs):
                rep.ok("floating", f"separate group {objs} is intentionally separate per spec", at=ctr)
            else:
                rep.fail("floating", f"FLOATING PART(S): {objs} ({sum(units[k]['nfaces'] for k in c)} faces) do not touch the main "
                         f"model (gap > {contact_tol:.5f}). Snap/extend them to their mount point, or list them in "
                         "spec.intentionally_separate if they must be separate (e.g. detachable magazine)", objects=objs, at=ctr)
    else:
        rep.ok("floating", "all parts are connected into one model")
    for inner, outer in sorted(set(enclosed)):
        rep.warn("hidden-geometry", f"part of '{inner}' is completely hidden inside '{outer}' - wasted polygons, or a missing opening?")

    # ---------------- missing parts / spec parts
    parts = spec.get("parts", [])
    if parts:
        claimed = set()
        for p in parts:
            pat = p.get("pattern") or p.get("name")
            found = [n for n in data if fnmatch.fnmatchcase(n, pat)]
            claimed.update(found)
            want = p.get("count")
            if p.get("count_islands"):
                got = sum(len(data[n]["islands"]) for n in found)
                unit = "islands"
            else:
                got = len(found)
                unit = "objects"
            if not found:
                rep.fail("missing-parts", f"MISSING PART '{p.get('name')}' (pattern '{pat}') - no object matches", part=p.get("name"))
            elif want is not None and got != want:
                rep.fail("missing-parts", f"part '{p.get('name')}': expected {want} {unit}, found {got}", part=p.get("name"),
                         expected=want, found=got)
            else:
                rep.ok("missing-parts", f"part '{p.get('name')}' present ({got} {unit})")
        extra = [n for n in data if n not in claimed]
        if extra:
            rep.warn("missing-parts", f"objects not listed in spec.parts: {extra} - extra geometry, or rename to the spec name", objects=extra)
    else:
        rep.warn("missing-parts", "spec has no 'parts' list - missing-part check could not run (write the parts list from the reference)")

    # ---------------- dimensions
    dims = spec.get("dimensions")
    if dims:
        tol = float(spec.get("dimension_tolerance") or 0.02)
        for axis, want, got in zip("XYZ", dims, size):
            if want and abs(got - want) / want > tol:
                rep.fail("dimensions", f"overall {axis} size {got:.4f} vs spec {want:.4f} ({(got - want) / want * 100:+.1f}%)", axis=axis)
        if all(not w or abs(g - w) / w <= tol for w, g in zip(dims, size)):
            rep.ok("dimensions", f"overall size {stats['bbox_size_xyz']} within {tol * 100:.0f}% of spec {dims}")
        # proportions independent of scale
        if all(dims) and all(c > 0 for c in size):
            for (i, j) in ((0, 2), (1, 2), (0, 1)):
                rw, rg = dims[i] / dims[j], size[i] / size[j]
                if abs(rg - rw) / rw > tol:
                    rep.fail("proportions", f"ratio {'XYZ'[i]}/{'XYZ'[j]} = {rg:.3f} vs reference {rw:.3f} - perspective/proportion error")
    else:
        rep.warn("dimensions", "spec has no 'dimensions' [x,y,z] - real-world size not verified")

    # ---------------- materials / textures / UVs
    palette = {}
    for name, d in data.items():
        o, bm = d["obj"], d["bm"]
        slots = o.material_slots
        if len(slots) == 0 or all(s.material is None for s in slots):
            rep.fail("materials", f"'{name}' has NO material")
            continue
        used = {f.material_index for f in bm.faces}
        for mi in used:
            if mi >= len(slots) or slots[mi].material is None:
                rep.fail("materials", f"'{name}': faces use empty/missing material slot {mi}")
        textured = False
        for mi in used:
            if mi >= len(slots) or slots[mi].material is None:
                continue
            mat = slots[mi].material
            col = None
            if mat.node_tree is not None:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE":
                        textured = True
                        img = node.image
                        if img is None:
                            rep.fail("textures", f"material '{mat.name}': image texture node '{node.name}' has no image")
                        elif img.source == "FILE" and not img.packed_file:
                            fp = bpy.path.abspath(img.filepath)
                            if not os.path.exists(fp):
                                rep.fail("textures", f"material '{mat.name}': texture file missing on disk: {fp}")
                        if img is not None and img.size[0] and max(img.size) < 256:
                            rep.warn("textures", f"texture '{img.name}' is only {tuple(img.size)} px")
                    if node.type == "BSDF_PRINCIPLED" and not node.inputs["Base Color"].is_linked:
                        col = node.inputs["Base Color"].default_value
            if col is None:
                col = mat.diffuse_color

            def to_srgb(c):
                c = max(0.0, min(1.0, c))
                return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
            palette.setdefault(name, []).append({"material": mat.name, "base_color_hex": "#%02x%02x%02x" % tuple(
                int(round(to_srgb(col[k]) * 255)) for k in range(3)), "textured": textured})
        if textured:
            uv = bm.loops.layers.uv.active
            if uv is None:
                rep.fail("uvs", f"'{name}' uses image textures but has NO UV map")
            else:
                ratios, zero_uv = [], 0
                for f in bm.faces:
                    a3 = f.calc_area()
                    if a3 < 1e-12:
                        continue
                    uvs = [l[uv].uv for l in f.loops]
                    a2 = 0.0
                    for k in range(1, len(uvs) - 1):
                        a2 += abs((uvs[k] - uvs[0]).cross(uvs[k + 1] - uvs[0])) / 2
                    if a2 < 1e-12:
                        zero_uv += 1
                    else:
                        ratios.append(a2 / a3)
                if zero_uv:
                    rep.fail("uvs", f"'{name}': {zero_uv} faces have zero UV area (texture smeared/stretched there)", count=zero_uv)
                if ratios:
                    ratios.sort()
                    med = ratios[len(ratios) // 2]
                    bad = sum(1 for r in ratios if r < med / 4 or r > med * 4)
                    frac = bad / len(ratios)
                    if frac > 0.05:
                        lvl = rep.fail if frac > 0.2 else rep.warn
                        lvl("uvs", f"'{name}': {frac * 100:.0f}% of faces have strongly uneven texel density (stretching)")
        # spec colour check
        for p in parts:
            pat = p.get("pattern") or p.get("name")
            want = p.get("color")
            if want and fnmatch.fnmatchcase(name, pat) and palette.get(name):
                wv = [int(want.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4)]
                best = None
                for entry in palette[name]:
                    hv = entry["base_color_hex"]
                    gv = [int(hv[k:k + 2], 16) for k in (1, 3, 5)]
                    dist = math.sqrt(sum((x - y) ** 2 for x, y in zip(wv, gv)))
                    best = dist if best is None else min(best, dist)
                if best is not None and best > 30 and not any(e["textured"] for e in palette[name]):
                    rep.fail("materials", f"'{name}' colour differs from reference {want} (RGB distance {best:.0f})")
    stats["palette"] = palette

    # ---------------- weapon grip
    wpn = spec.get("weapon")
    if wpn:
        wtype = (wpn.get("type") or "other").lower()
        min_len, min_w, max_w, hands = HAND_RANGES.get(wtype, HAND_RANGES["other"])
        hands = int(wpn.get("hands", hands))
        gname = wpn.get("grip_part")
        gp_name = wpn.get("grip_point", "GripPoint")
        realistic = spec.get("units", "meters") == "meters" and not spec.get("stylized_scale", False)
        gpart = data.get(gname) if gname else None
        if gpart is None:
            rep.fail("weapon-grip", f"grip part '{gname}' not found (set spec.weapon.grip_part to the handle object's name)")
        gpoint = bpy.data.objects.get(gp_name)
        if gpoint is None:
            rep.fail("weapon-grip", f"no grip attachment point '{gp_name}' (add an Empty at the centre of the hand position, "
                     "oriented with the weapon, so engines/rigs can attach it)")
        if gpart is not None:
            pts = [v.co for v in gpart["bm"].verts]
            if np is not None and len(pts) >= 4:
                arr = np.array([tuple(p) for p in pts])
                c = arr.mean(0)
                _, _, vt = np.linalg.svd(arr - c, full_matrices=False)
                axes = [Vector(vt[k]) for k in range(3)]
                if wpn.get("grip_axis"):
                    ga = Vector({"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[wpn["grip_axis"].upper().lstrip("+-")])
                    axes.sort(key=lambda ax: -abs(ax.dot(ga)))
                ext = []
                for ax in axes:
                    pr = arr @ np.array(tuple(ax))
                    ext.append(float(pr.max() - pr.min()))
                L, w1, w2 = ext[0], ext[1], ext[2]
                center = Vector(c)
                stats["grip"] = {"length": round(L, 4), "cross_section": [round(w1, 4), round(w2, 4)],
                                 "axis": [round(x, 3) for x in axes[0]]}
                if realistic:
                    need = min_len * hands
                    if L < need * 0.9:
                        rep.fail("weapon-grip", f"grip length {L * 100:.1f} cm too short for {hands} hand(s) of a {wtype} "
                                 f"(need >= {need * 100:.0f} cm)")
                    if max(w1, w2) > max_w * 1.1:
                        rep.fail("weapon-grip", f"grip is {max(w1, w2) * 100:.1f} cm thick - too thick to hold (max ~{max_w * 100:.1f} cm)")
                    if min(w1, w2) < min_w * 0.9:
                        rep.fail("weapon-grip", f"grip is only {min(w1, w2) * 100:.1f} cm thick - too thin (min ~{min_w * 100:.1f} cm)")
                    if not any(it["check"] == "weapon-grip" and it["level"] == "FAIL" for it in rep.items):
                        rep.ok("weapon-grip", f"grip {L * 100:.1f} cm long, {w1 * 100:.1f}x{w2 * 100:.1f} cm section - holdable by a hand")
                else:
                    rep.warn("weapon-grip", "stylized/non-metric scale: grip size only checked relative to the reference")
                # hand clearance: proxy cylinder around the grip axis
                r = max(w1, w2) / 2 + float(wpn.get("finger_thickness", 0.022))
                ax = axes[0]
                bmc = bmesh.new()
                bmesh.ops.create_cone(bmc, cap_ends=True, segments=24, radius1=r, radius2=r, depth=L * 0.8)
                rot = Vector((0, 0, 1)).rotation_difference(ax).to_matrix().to_4x4()
                bmc.transform(Matrix.Translation(center) @ rot)
                cyl = BVHTree.FromBMesh(bmc)
                bmc.free()
                ok_pat = [gname] + list(wpn.get("hand_clearance_allowed", ["*Trigger*", "*Guard*", "*Frame*", "*Receiver*", "*Pommel*"]))
                blockers = [n for n, d in data.items() if not _match(n, ok_pat) and cyl.overlap(d["bvh"])]
                if blockers:
                    rep.warn("weapon-grip", f"parts {blockers} occupy the space where the hand wraps around the grip - "
                             "render a grip close-up and confirm a hand fits (or add them to weapon.hand_clearance_allowed)",
                             objects=blockers)
                if gpoint is not None:
                    gp = gpoint.matrix_world.translation
                    dist_c = (gp - center).length
                    if dist_c > L * 0.6 + 0.01:
                        rep.fail("weapon-grip", f"grip point '{gp_name}' is {dist_c * 100:.1f} cm from the grip centre - "
                                 "it must sit where the hand holds the weapon")
                    else:
                        rep.ok("weapon-grip", f"grip point '{gp_name}' sits on the grip ({dist_c * 100:.1f} cm from centre)")
        fwd = wpn.get("forward_axis")
        if fwd:
            k = "XYZ".index(fwd.upper().lstrip("+-"))
            if wtype not in ("bow",) and size[k] < max(size) * 0.999:
                rep.warn("weapon-grip", f"weapon forward axis is {fwd} but the longest dimension is along "
                         f"{'XYZ'[list(size).index(max(size))]} - check orientation")

    # ---------------- cleanup
    for d in data.values():
        d["bm"].free()
    return _finish(rep, spec, out_path, stats)


def _finish(rep, spec, out_path, stats):
    fails = [i for i in rep.items if i["level"] == "FAIL"]
    warns = [i for i in rep.items if i["level"] == "WARN"]
    verdict = "PASS" if not fails else "FAIL"
    lines = [f"VERIFY VERDICT: {verdict}   ({len(fails)} FAIL, {len(warns)} WARN)"]
    for lvl in ("FAIL", "WARN"):
        for it in rep.items:
            if it["level"] == lvl:
                at = f"  @ {it['at']}" if it.get("at") else ""
                lines.append(f"[{lvl}] {it['check']}: {it['msg']}{at}")
    text = "\n".join(lines)
    result = {"verdict": verdict, "fail_count": len(fails), "warn_count": len(warns), "stats": stats,
              "items": rep.items, "summary_text": text}
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
    print(text)
    return result


def _cli():
    import argparse
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--collection", default=None)
    a = ap.parse_args(argv)
    verify_model(spec_path=a.spec, out_path=a.out, collection=a.collection)


if __name__ == "__main__" and "--" in sys.argv:
    _cli()
