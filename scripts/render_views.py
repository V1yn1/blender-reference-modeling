"""
render_views.py - render the current Blender model from the 10 canonical reference views
(8-step 360 turnaround + top + bottom) so every view can be compared 1:1 with the reference.

VIEW CONVENTION (same as Blender's numpad views; the object's FRONT faces -Y, UP is +Z):
    front        camera at -Y looking +Y            (numpad 1)
    front_right  camera between -Y and +X (45 deg)
    right        camera at +X looking -X            (numpad 3)  = side that is on the RIGHT edge of the front view
    back_right   camera between +Y and +X (135 deg)
    back         camera at +Y looking -Y            (ctrl+numpad 1)
    back_left    camera between +Y and -X (225 deg)
    left         camera at -X looking +X            (ctrl+numpad 3) = side that is on the LEFT edge of the front view
    front_left   camera between -Y and -X (315 deg)
    top          camera at +Z looking down, screen-up = +Y (front edge at the BOTTOM of the image)  (numpad 7)
    bottom       camera at -Z looking up,  screen-up = -Y (front edge at the TOP of the image)     (ctrl+numpad 7)

Usage inside Blender (e.g. Blender MCP `execute_blender_code`):
    ns = {}
    exec(open(r"C:/Users/<you>/.claude/skills/blender-reference-modeling/scripts/render_views.py", encoding="utf-8").read(), ns)
    ns["render_views"](r"C:/work/renders/iter_03", resolution=1024)
    # close-up on specific parts (everything still rendered, camera framed on these objects):
    ns["render_views"](r"C:/work/renders/grip", frame_objects=["Grip", "Trigger"])

Headless:
    blender -b model.blend --python render_views.py -- --out C:/work/renders --res 1024
            [--views front,back,top] [--engine workbench|eevee] [--color material|texture|object]
            [--collection Model] [--frame Grip,Trigger] [--elev 0] [--persp35 0]
"""
import bpy
import math
import os
import sys
from mathutils import Vector, Matrix

VIEW_DEFS = {
    # name: (azimuth_deg, elevation_deg). azimuth 0 = camera at -Y (front), increases toward +X.
    "front": (0, 0),
    "front_right": (45, 0),
    "right": (90, 0),
    "back_right": (135, 0),
    "back": (180, 0),
    "back_left": (225, 0),
    "left": (270, 0),
    "front_left": (315, 0),
    "top": (0, 90),
    "bottom": (0, -90),
}
ALL_VIEWS = list(VIEW_DEFS.keys())


def _model_objects(collection=None):
    if collection:
        col = bpy.data.collections.get(collection)
        if col is None:
            raise ValueError(f"collection '{collection}' not found")
        objs = col.all_objects
    else:
        objs = bpy.context.scene.objects
    out = []
    for o in objs:
        if o.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        try:
            if not o.visible_get() or o.hide_render:
                continue
        except RuntimeError:
            continue
        out.append(o)
    return out


def _world_points(objs):
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        eo = o.evaluated_get(deps)
        mw = eo.matrix_world
        try:
            me = eo.to_mesh()
        except RuntimeError:
            me = None
        if me is not None and len(me.vertices):
            step = max(1, len(me.vertices) // 20000)
            pts.extend(mw @ v.co for v in me.vertices[::step])
            eo.to_mesh_clear()
        else:
            pts.extend(mw @ Vector(c) for c in o.bound_box)
    return pts


def _view_basis(az_deg, el_deg):
    """Return (dir_from_target_to_camera, up_vector)."""
    if el_deg >= 89.999:
        return Vector((0, 0, 1)), Vector((0, 1, 0))
    if el_deg <= -89.999:
        return Vector((0, 0, -1)), Vector((0, -1, 0))
    az, el = math.radians(az_deg), math.radians(el_deg)
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    return d.normalized(), Vector((0, 0, 1))


def _cam_matrix(pos, forward, up):
    f = forward.normalized()
    r = f.cross(up).normalized()
    u = r.cross(f).normalized()
    m = Matrix((
        (r.x, u.x, -f.x, pos.x),
        (r.y, u.y, -f.y, pos.y),
        (r.z, u.z, -f.z, pos.z),
        (0, 0, 0, 1),
    ))
    return m, r, u


def _set_engine(scene, engine):
    if engine == "workbench":
        scene.render.engine = "BLENDER_WORKBENCH"
        return
    for eid in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = eid
            return
        except TypeError:
            continue
    scene.render.engine = "BLENDER_WORKBENCH"


def render_views(out_dir, views=None, resolution=1024, engine="workbench", color="material",
                 collection=None, frame_objects=None, elevation=0.0, persp_lens=0.0, margin=1.08,
                 prefix=""):
    """Render the requested views as transparent PNGs. Returns {view: filepath}.

    elevation  : extra elevation (deg) for the 8 horizontal views (use it if the reference turnaround
                 was drawn from slightly above, e.g. 15-20). Top/bottom are always exact.
    persp_lens : 0 = orthographic (default, use with orthographic references). >0 = perspective camera
                 with that focal length in mm (only to mimic a perspective reference image).
    """
    views = views or ALL_VIEWS
    out_dir = os.path.abspath(bpy.path.abspath(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    objs = _model_objects(collection)
    if not objs:
        raise RuntimeError("no visible renderable objects found")
    frame = objs
    if frame_objects:
        frame = [bpy.data.objects[n] for n in frame_objects if n in bpy.data.objects]
        if not frame:
            raise ValueError(f"none of frame_objects {frame_objects} exist")
    pts = _world_points(frame)
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center = (lo + hi) / 2
    diag = max((hi - lo).length, 1e-4)

    # --- save state ---
    r = scene.render
    saved = dict(engine=r.engine, rx=r.resolution_x, ry=r.resolution_y, pct=r.resolution_percentage,
                 transparent=r.film_transparent, fmt=r.image_settings.file_format,
                 mode=r.image_settings.color_mode, path=r.filepath, camera=scene.camera)
    shading = scene.display.shading
    saved_shading = dict(light=shading.light, color_type=shading.color_type,
                         cavity=shading.show_cavity, outline=shading.show_object_outline,
                         spec=shading.show_specular_highlight)

    # Workbench "MATERIAL" colour uses the viewport display colour, not the shader. Temporarily copy the
    # Principled Base Color into it so quick renders show the real colours (restored afterwards).
    saved_diffuse = {}
    for o in objs:
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if m is None or m.name in saved_diffuse or m.node_tree is None:
                continue
            for node in m.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED" and not node.inputs["Base Color"].is_linked:
                    saved_diffuse[m.name] = tuple(m.diffuse_color)
                    bc = node.inputs["Base Color"].default_value
                    m.diffuse_color = (bc[0], bc[1], bc[2], m.diffuse_color[3])
                    break

    cam_data = bpy.data.cameras.new("_RV_cam")
    cam_obj = bpy.data.objects.new("_RV_cam", cam_data)
    scene.collection.objects.link(cam_obj)
    results = {}
    try:
        _set_engine(scene, engine)
        r.resolution_x = r.resolution_y = int(resolution)
        r.resolution_percentage = 100
        r.film_transparent = True
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        if r.engine == "BLENDER_WORKBENCH":
            shading.light = "STUDIO"
            shading.color_type = {"material": "MATERIAL", "texture": "TEXTURE", "object": "OBJECT"}.get(color, "MATERIAL")
            shading.show_cavity = True
            shading.show_object_outline = False
            shading.show_specular_highlight = True
        if r.engine != "BLENDER_WORKBENCH" and scene.world is None:
            print("[render_views] WARNING: eevee with no World -> model renders black. Add a World/lights "
                  "(or use engine='workbench').")
        scene.camera = cam_obj
        cam_data.clip_start = diag * 0.001
        cam_data.clip_end = diag * 100

        for v in views:
            if v not in VIEW_DEFS:
                raise ValueError(f"unknown view '{v}'. Valid: {ALL_VIEWS}")
            az, el = VIEW_DEFS[v]
            if v not in ("top", "bottom"):
                el = elevation
            d, up = _view_basis(az, el)
            dist = diag * 3.0
            pos = center + d * dist
            m, rx, uy = _cam_matrix(pos, -d, up)
            # frame: project the bbox points onto the camera plane
            xs = [(p - center).dot(rx) for p in pts]
            ys = [(p - center).dot(uy) for p in pts]
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            extent = max(max(xs) - min(xs), max(ys) - min(ys), 1e-4) * margin
            pos = pos + rx * cx + uy * cy
            m, _, _ = _cam_matrix(pos, -d, up)
            cam_obj.matrix_world = m
            if persp_lens and persp_lens > 0:
                cam_data.type = "PERSP"
                cam_data.lens = persp_lens
                cam_data.sensor_fit = "AUTO"
                # distance so that `extent` fits the sensor
                fov = 2 * math.atan(cam_data.sensor_width / (2 * persp_lens))
                dist_p = (extent / 2) / math.tan(fov / 2) + diag / 2
                cam_obj.matrix_world = _cam_matrix(center + rx * cx + uy * cy + d * dist_p, -d, up)[0]
            else:
                cam_data.type = "ORTHO"
                cam_data.ortho_scale = extent
            fp = os.path.join(out_dir, f"{prefix}{v}.png")
            r.filepath = fp
            bpy.ops.render.render(write_still=True)
            results[v] = fp
    finally:
        for mname, col in saved_diffuse.items():
            if mname in bpy.data.materials:
                bpy.data.materials[mname].diffuse_color = col
        bpy.data.objects.remove(cam_obj, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        r.engine = saved["engine"]
        r.resolution_x, r.resolution_y = saved["rx"], saved["ry"]
        r.resolution_percentage = saved["pct"]
        r.film_transparent = saved["transparent"]
        r.image_settings.file_format = saved["fmt"]
        r.image_settings.color_mode = saved["mode"]
        r.filepath = saved["path"]
        scene.camera = saved["camera"]
        shading.light = saved_shading["light"]
        shading.color_type = saved_shading["color_type"]
        shading.show_cavity = saved_shading["cavity"]
        shading.show_object_outline = saved_shading["outline"]
        shading.show_specular_highlight = saved_shading["spec"]
    print(f"[render_views] rendered {len(results)} views -> {out_dir}")
    return results


def _cli():
    import argparse
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--res", type=int, default=1024)
    ap.add_argument("--views", default="")
    ap.add_argument("--engine", default="workbench", choices=["workbench", "eevee"])
    ap.add_argument("--color", default="material", choices=["material", "texture", "object"])
    ap.add_argument("--collection", default=None)
    ap.add_argument("--frame", default="")
    ap.add_argument("--elev", type=float, default=0.0)
    ap.add_argument("--persp35", type=float, default=0.0, help="perspective focal length in mm (0 = ortho)")
    a = ap.parse_args(argv)
    render_views(a.out, views=[v for v in a.views.split(",") if v] or None, resolution=a.res,
                 engine=a.engine, color=a.color, collection=a.collection,
                 frame_objects=[f for f in a.frame.split(",") if f] or None,
                 elevation=a.elev, persp_lens=a.persp35)


if __name__ == "__main__" and "--" in sys.argv:
    _cli()
