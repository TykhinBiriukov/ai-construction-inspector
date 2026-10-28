"""Render a static diagnostic overlay from the actual fitted planes and mesh."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .visibility import render_visibility
from .wall_plane_viewer import plane_color


def save_wall_plane_preview(path, vertices, faces, face_plane_id, planes, *, up_axis="z"):
    axis = "xyz".index(up_axis)
    up = np.eye(3)[axis]
    wall_planes = [plane for plane in planes if plane["status"] == "wall_candidate"]
    focus = wall_planes or planes
    bounds = np.concatenate([np.asarray(p["corners_world"]) for p in focus]) if focus else vertices
    lower, upper = bounds.min(axis=0), bounds.max(axis=0)
    center = (lower + upper)*.5
    radius = max(float(np.linalg.norm(upper-lower))*.5, .5)
    horizontal = np.eye(3)[[i for i in range(3) if i != axis]]
    direction = .65*horizontal[0] + .55*horizontal[1] + .52*up
    direction /= np.linalg.norm(direction)
    eye = center + direction*radius*2.9
    forward = center-eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack((right, down, forward))
    transform[:3, 3] = eye
    width, height = 640, 560
    focal = height*.5/np.tan(np.pi/8)
    K = np.array([[focal, 0, (width-1)*.5], [0, focal, (height-1)*.5], [0, 0, 1.]])
    # This cut is only for viewing the interior; saved source geometry is untouched.
    cut_height = lower[axis]+.88*(upper[axis]-lower[axis])
    retained = np.flatnonzero(vertices[faces][:, :, axis].mean(axis=1) <= cut_height)
    depth, visible_ids = render_visibility(vertices, faces[retained], K, transform, width, height)
    visible = visible_ids >= 0
    ids = np.full(visible_ids.shape, -1, dtype=np.int32)
    ids[visible] = retained[visible_ids[visible]]
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    light = .5+.5*np.abs(normals @ direction)
    base = np.full((height, width, 3), [18, 25, 37], dtype=np.uint8)
    base[visible] = (np.array([161, 169, 182])*light[ids[visible], None]).astype(np.uint8)
    overlay = base.astype(float)
    for plane in planes:
        selected = visible & (face_plane_id[np.maximum(ids, 0)] == plane["plane_id"])
        color = np.asarray(plane_color(plane["plane_id"]))
        overlay[selected] = .18*overlay[selected]+.82*color*light[ids[selected], None]
    # Blend plane polygons from back to front with the mesh as an occluder.
    ordered = sorted(planes, key=lambda p: float((np.asarray(p["center_world"])-eye) @ forward), reverse=True)
    for plane in ordered:
        corners = np.asarray(plane["corners_world"], dtype=float)
        plane_depth, plane_ids = render_visibility(corners, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32),
                                                  K, transform, width, height)
        # Evaluate world height at each ray hit, to apply the same top cut to planes.
        yy, xx = np.indices((height, width))
        ray = ((xx-K[0, 2])[..., None]*right/focal +
               (yy-K[1, 2])[..., None]*down/focal + forward)
        finite_depth = np.where(plane_ids >= 0, plane_depth, 0)
        world_height = eye[axis]+finite_depth*ray[..., axis]
        visible_plane = ((plane_ids >= 0) & (plane_depth <= depth+.03) & (world_height <= cut_height))
        overlay[visible_plane] = .72*overlay[visible_plane]+.28*np.asarray(plane_color(plane["plane_id"]))
    rows = (len(planes)+3)//4
    canvas = Image.new("RGB", (width*2, height+92+rows*25), (18, 25, 37))
    canvas.paste(Image.fromarray(base), (0, 50))
    canvas.paste(Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8)), (width, 50))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 17)
        small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    draw.text((18, 15), "Prepared mesh", font=font, fill="white")
    draw.text((width+18, 15), "RANSAC support + translucent planes", font=font, fill="white")
    draw.text((18, height+60), "Top cut for visibility. Plane rectangles show support extents, not measured wall boundaries or filled openings.",
              font=small, fill=(190, 202, 220))
    for index, plane in enumerate(planes):
        x, y = 18+(index % 4)*315, height+91+(index//4)*25
        draw.rectangle((x, y+3, x+12, y+15), fill=plane_color(plane["plane_id"]))
        text = f"P{plane['plane_id']+1}: {plane['support_area_m2']:.1f} m2 | RMSE {plane['rmse_m']*1000:.0f} mm"
        draw.text((x+20, y), text, font=small, fill="white")
    canvas.save(path)
