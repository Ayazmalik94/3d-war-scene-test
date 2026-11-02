# ============================================================================
#  WAR SCENE — "DAWN OF TWO GENERALS"
#  Procedural cinematic war scene for Blender 4.x (EEVEE Next)
#
#  USAGE:
#    1. Open Blender 4.x -> Scripting workspace -> open this file -> Run Script.
#       (or from a terminal: blender --background --python war_scene_blender.py)
#    2. The script builds the whole scene, animates 6 camera setups over a
#       300-second / 7200-frame timeline, renders PNG frames to ./render/,
#       then calls ffmpeg to mux frames + audio into war_scene.mp4.
#    3. Set QUICK_PREVIEW = True for a fast test render (all six shots,
#       compressed onto a 300-frame timeline).
#
#  FINAL QUALITY (Cycles):
#    To switch the final render to Cycles, change the engine block in
#    setup_render() to:
#        scene.render.engine = 'CYCLES'
#        scene.cycles.samples = SAMPLES_FINAL        # e.g. 128
#        scene.cycles.use_denoising = True
#        scene.cycles.max_bounces = 8
#    and keep every other setting identical.
# ============================================================================

import bpy
import bmesh
import math
import random
import subprocess
import os
from mathutils import Vector, Matrix

# ---------------------------------------------------------------------------
#  MASTER CONFIGURATION
# ---------------------------------------------------------------------------

QUICK_PREVIEW   = True      # False -> full 300 s / 7200-frame production render
DO_RENDER       = True      # False -> build scene + save .blend, skip rendering
DO_FFMPEG       = True      # False -> render frames only, skip mp4 muxing

random.seed(7)              # deterministic layout, change for variations

# --- resolution / frame rate -------------------------------------------------
if QUICK_PREVIEW:
    RES_X, RES_Y   = 480, 270       # fast 16:9 test resolution
    FPS            = 8              # 8 fps test playback
    TOTAL_FRAMES   = 300            # compressed timeline (all 6 shots)
    SAMPLES        = 16             # low sample count for speed
else:
    RES_X, RES_Y   = 1920, 1080     # 16:9 Full HD (set 3840x2160 for 4K UHD)
    FPS            = 24             # cinematic 24 fps
    TOTAL_FRAMES   = 7200           # 300 seconds at 24 fps
    SAMPLES        = 32             # EEVEE preview samples (use 128 for final)

SAMPLES_FINAL    = 128              # documented target for the final render

# --- timeline mapping ---------------------------------------------------------
# The six camera setups are authored on the full 7200-frame timeline and then
# mapped onto whatever timeline is active (full or preview).
FRAME_SCALE = TOTAL_FRAMES / 7200.0

def F(full_frame):
    """Map a frame number on the full 7200-frame timeline to this timeline."""
    return max(1, int(round(full_frame * FRAME_SCALE)))

# Shot boundaries (full-timeline frames) matching the director's cut list.
SHOT_TABLE = [
    ("CAM1_DRONE_ESTABLISH",  F(1),    F(1500)),   # wide drone push-in
    ("CAM2_LOWANGLE_GENERAL_A", F(1500), F(2800)), # General A reveal
    ("CAM3_LOWANGLE_GENERAL_B", F(2800), F(4000)), # General B reveal
    ("CAM4_TRACKING_CHARGE",  F(4000), F(5200)),   # armies charge
    ("CAM5_TWOSHOT_CIRCLE",   F(5200), F(6500)),   # generals circle
    ("CAM6_ORBIT_DUEL",       F(6500), F(7200)),   # orbit + swords + fade
]
FRAME_END = F(7200)

# --- output paths -------------------------------------------------------------
BASE_DIR    = bpy.path.abspath("//") if bpy.data.filepath else os.getcwd()
RENDER_DIR  = os.path.join(BASE_DIR, "render")
FRAME_PATH  = os.path.join(RENDER_DIR, "frame_")      # -> frame_0001.png ...
AUDIO_PATH  = os.path.join(BASE_DIR, "audio.wav")
MP4_PATH    = os.path.join(BASE_DIR, "war_scene.mp4")
DURATION_S  = TOTAL_FRAMES / float(FPS)

# ---------------------------------------------------------------------------
#  GENERIC HELPERS
# ---------------------------------------------------------------------------

def clean_scene():
    """Remove every object, then orphan data, so re-running is idempotent."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights, bpy.data.particles):
        for block in list(datablocks):
            try:
                datablocks.remove(block)
            except Exception:
                pass
    for tex in list(bpy.data.textures):
        try:
            bpy.data.textures.remove(tex)
        except Exception:
            pass


def set_active(obj):
    """Make obj the single selected/active object (context-safe for ops)."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_scale(obj):
    """Apply object scale so bevel/displace widths stay uniform."""
    set_active(obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def smooth(obj):
    """Shade every polygon of a mesh smooth."""
    if obj.type == 'MESH':
        for poly in obj.data.polygons:
            poly.use_smooth = True


def add_cube(name, location, scale, material=None, rotation=(0.0, 0.0, 0.0),
             bevel=0.0, displace_tex=None, displace_strength=0.0):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location,
                                    rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    if bevel > 0.0 or displace_tex is not None:
        apply_scale(obj)
    if bevel > 0.0:
        mod = obj.modifiers.new(name="Bevel", type='BEVEL')
        mod.width = bevel
        mod.segments = 2
    if displace_tex is not None and displace_strength > 0.0:
        mod = obj.modifiers.new(name="StoneDisplace", type='DISPLACE')
        mod.texture = displace_tex
        mod.strength = displace_strength
        mod.mid_level = 0.5
        mod.texture_coords = 'GLOBAL'
    if material:
        obj.data.materials.append(material)
    return obj


def add_cylinder(name, location, radius, depth, material=None,
                 rotation=(0.0, 0.0, 0.0), vertices=16):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius,
                                        depth=depth, location=location,
                                        rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    if material:
        obj.data.materials.append(material)
    return obj


def add_uvsphere(name, location, radius, material=None, segments=16, rings=12):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings,
                                        radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    smooth(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def add_empty(name, location=(0.0, 0.0, 0.0), display='PLAIN_AXES', size=0.5):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    obj.empty_display_type = display
    obj.empty_display_size = size
    bpy.context.collection.objects.link(obj)
    return obj


def parent_keep(child, parent):
    """Parent child to parent while keeping the child's world transform."""
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = world


def keyframe(obj, data_path, frame, value=None):
    """Insert a keyframe; optionally set the value first."""
    if value is not None:
        setattr(obj, data_path, value)
    obj.keyframe_insert(data_path=data_path, frame=frame)


def linearize(obj):
    """Force LINEAR interpolation on every f-curve of the object."""
    if obj.animation_data and obj.animation_data.action:
        for fcurve in obj.animation_data.action.fcurves:
            for kp in fcurve.keyframe_points:
                kp.interpolation = 'LINEAR'


def add_handheld_noise(obj, loc_strength=0.05, rot_strength=0.01,
                       scale=30.0):
    """Add subtle procedural handheld noise to location/rotation f-curves."""
    if not (obj.animation_data and obj.animation_data.action):
        return
    for fcurve in obj.animation_data.action.fcurves:
        strength = loc_strength if fcurve.data_path == "location" else rot_strength
        if strength <= 0.0:
            continue
        mod = fcurve.modifiers.new(type='NOISE')
        mod.strength = strength
        mod.scale = scale
        mod.phase = random.uniform(0.0, 100.0)


def point_at(obj, target):
    """Rotate a camera/light so its -Z axis looks at a world-space point."""
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def face_xy(obj, tx, ty):
    """Rotate an object built facing local +X so it faces (tx, ty) in world XY."""
    obj.rotation_euler = (
        0.0, 0.0,
        math.atan2(ty - obj.location.y, tx - obj.location.x))


def new_clouds_texture(name, scale, detail=5.0):
    """Legacy texture datablock (used by modifiers and the compositor)."""
    tex = bpy.data.textures.new(name=name, type='CLOUDS')
    tex.noise_scale = scale
    tex.noise_depth = 2
    return tex

# ---------------------------------------------------------------------------
#  MATERIALS
# ---------------------------------------------------------------------------

def make_principled(name, base_color, metallic=0.0, roughness=0.5,
                    emission=None, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission is not None and emission_strength > 0.0:
        # Blender 4.x renamed the emission socket; support both spellings.
        sock = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        if sock is not None:
            sock.default_value = (*emission, 1.0)
        strength_sock = bsdf.inputs.get("Emission Strength")
        if strength_sock is not None:
            strength_sock.default_value = emission_strength
    return mat


def make_mud_material():
    """Dark brown PBR mud with noise-driven micro-bump."""
    mat = bpy.data.materials.new("M_Mud")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.045, 0.030, 0.020, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.95
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 4.0
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.8
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.45
    bump.inputs["Distance"].default_value = 0.35
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def make_stone_material():
    """Cold gray stone with procedural surface breakup."""
    mat = bpy.data.materials.new("M_Stone")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.14, 0.14, 0.15, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 4.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    bump.inputs["Distance"].default_value = 0.25
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def make_volume_material(name, density, color, anisotropy=0.3):
    """Principled Volume material for the battlefield fog cube."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = density
    vol.inputs["Color"].default_value = (*color, 1.0)
    vol.inputs["Anisotropy"].default_value = anisotropy
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def make_smoke_material():
    """Soft alpha-card smoke for distant background planes."""
    mat = bpy.data.materials.new("M_Smoke")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.06, 0.06, 0.065, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.6
    noise.inputs["Detail"].default_value = 3.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (0.55, 0.55, 0.55, 1.0)
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Alpha"])
    # Alpha handling: property name changed in Blender 4.2 (EEVEE Next).
    try:
        mat.surface_render_method = 'DITHERED'
    except Exception:
        try:
            mat.blend_method = 'BLEND'
        except Exception:
            pass
    return mat

# ---------------------------------------------------------------------------
#  RENDER / SCENE SETTINGS
# ---------------------------------------------------------------------------

def setup_render(scene):
    """Resolution, fps, engine, samples, motion blur, PNG output."""
    scene.render.resolution_x = RES_X
    scene.render.resolution_y = RES_Y
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = False
    scene.render.filepath = FRAME_PATH
    scene.frame_start = 1
    scene.frame_end = FRAME_END

    # --- engine: EEVEE Next (fast preview/final) ------------------------------
    # FINAL QUALITY: replace this block with Cycles settings (see header).
    try:
        scene.render.engine = 'BLENDER_EEVEE_NEXT'      # Blender 4.2+
    except Exception:
        scene.render.engine = 'BLENDER_EEVEE'           # Blender 4.0 / 4.1

    # --- samples (property location varies between 4.x releases) -------------
    def try_samples(owner, value):
        if owner is None:
            return False
        for attr in ("taa_render_samples", "taa_samples", "samples"):
            if hasattr(owner, attr):
                try:
                    setattr(owner, attr, value)
                    return True
                except Exception:
                    pass
        return False
    if not try_samples(getattr(scene, "eevee", None), SAMPLES):
        try_samples(scene.render, SAMPLES)

    # --- motion blur ----------------------------------------------------------
    try:
        scene.render.use_motion_blur = True
    except Exception:
        pass

    # --- color management: cinematic contrast ---------------------------------
    try:
        scene.view_settings.look = 'AgX - Medium High Contrast'
    except Exception:
        try:
            scene.view_settings.look = 'Medium High Contrast'
        except Exception:
            pass


def setup_world(scene):
    """Cold overcast gray-blue world background."""
    world = bpy.data.worlds.new("W_Overcast") if not bpy.data.worlds \
        else bpy.data.worlds[0]
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.055, 0.075, 0.105, 1.0)
    bg.inputs["Strength"].default_value = 0.35
    return world

# ---------------------------------------------------------------------------
#  ENVIRONMENT: GROUND, FOG, RUINS, SIEGE TOWERS, BANNERS, SMOKE
# ---------------------------------------------------------------------------

def build_ground(mud_mat):
    """Large subdivided plane with noise displacement -> churned mud."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=200, y_subdivisions=200,
                                    size=140.0, location=(0.0, 0.0, 0.0))
    ground = bpy.context.object
    ground.name = "ENV_Ground"
    smooth(ground)
    tex = new_clouds_texture("TEX_Mud", scale=0.9, detail=5.0)
    disp = ground.modifiers.new(name="MudDisplace", type='DISPLACE')
    disp.texture = tex
    disp.strength = 0.45
    disp.mid_level = 0.5
    disp.texture_coords = 'GLOBAL'
    ground.data.materials.append(mud_mat)
    return ground


def build_fog(vol_mat):
    """Bounded volume cube wrapping the battlefield -> ground fog + rays."""
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 5.0, 10.0))
    fog = bpy.context.object
    fog.name = "ENV_FogVolume"
    fog.scale = (70.0, 60.0, 10.0)
    apply_scale(fog)
    fog.data.materials.append(vol_mat)
    fog.display_type = 'WIRE'
    return fog


def build_ruins(stone_mat):
    """Broken fortress walls and rubble line in the far background (+Y)."""
    stone_tex = new_clouds_texture("TEX_Stone", scale=0.55, detail=4.0)
    # Long ruined curtain wall, broken into irregular segments.
    x = -34.0
    while x < 34.0:
        width = random.uniform(3.0, 7.0)
        height = random.uniform(2.0, 8.0)
        depth = random.uniform(1.6, 2.6)
        tilt = random.uniform(-0.06, 0.06)
        add_cube("ENV_RuinWall", (x, 30.0 + random.uniform(-2, 2), height / 2.0),
                 (width / 2.0, depth / 2.0, height / 2.0),
                 material=stone_mat, rotation=(0.0, tilt, 0.0),
                 bevel=0.18, displace_tex=stone_tex, displace_strength=0.25)
        x += width + random.uniform(0.4, 2.2)
    # Two taller ruined gate towers flanking the central breach.
    for gx in (-6.5, 6.5):
        add_cube("ENV_GateTower", (gx, 29.0, 6.0), (2.2, 2.2, 6.0),
                 material=stone_mat, rotation=(0.0, 0.0, 0.03 * gx),
                 bevel=0.25, displace_tex=stone_tex, displace_strength=0.3)
    # Rubble field scattered in front of the walls.
    for _ in range(26):
        rx = random.uniform(-30.0, 30.0)
        ry = random.uniform(20.0, 27.0)
        s = random.uniform(0.3, 1.3)
        add_cube("ENV_Rubble", (rx, ry, s * 0.4),
                 (s, s * random.uniform(0.6, 1.0), s * 0.5),
                 material=stone_mat,
                 rotation=(random.random(), random.random(), random.random()),
                 bevel=0.08, displace_tex=stone_tex, displace_strength=0.15)


def build_siege_towers(wood_mat):
    """Two broken siege towers: tilted frames of legs + platforms."""
    def tower(base_x, base_y, lean):
        root = add_empty("ENV_SiegeTower", (base_x, base_y, 0.0))
        parts = []
        for sx in (-1.2, 1.2):
            for sy in (-1.2, 1.2):
                parts.append(add_cube("ENV_TowerLeg",
                                      (base_x + sx, base_y + sy, 4.0),
                                      (0.22, 0.22, 4.0), material=wood_mat,
                                      bevel=0.05))
        for z in (2.6, 5.2, 7.8):
            parts.append(add_cube("ENV_TowerDeck",
                                  (base_x, base_y, z),
                                  (1.6, 1.6, 0.12), material=wood_mat,
                                  bevel=0.04))
        for part in parts:
            parent_keep(part, root)
        root.rotation_euler = (math.radians(lean), 0.0,
                               random.uniform(-0.2, 0.2))
        return root
    tower(-14.0, 16.0, 7.0)     # leaning, half-fallen
    tower(17.0, 18.0, -4.0)     # still standing but damaged


def build_banner(name, pole_x, pole_y, cloth_mat, accent_mat):
    """Pole + crossbar + subdivided cloth plane driven by a Wave modifier."""
    root = add_empty(name, (pole_x, pole_y, 0.0))
    pole = add_cylinder(name + "_Pole", (pole_x, pole_y, 3.0), 0.07, 6.0,
                        material=accent_mat)
    cross = add_cylinder(name + "_Crossbar", (pole_x, pole_y, 5.6), 0.05, 2.6,
                         material=accent_mat,
                         rotation=(math.radians(90.0), 0.0, 0.0))
    # Cloth: grid created in XY, rotated vertical, hung from the crossbar.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=16,
                                    size=2.0,
                                    location=(pole_x, pole_y, 5.6))
    cloth = bpy.context.object
    cloth.name = name + "_Cloth"
    cloth.scale = (0.65, 1.1, 1.0)
    apply_scale(cloth)
    cloth.rotation_euler = (math.radians(90.0), 0.0, 0.0)   # hang downward
    set_active(cloth)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    # After the X-rotation the grid hangs below the bar; drop it into place.
    cloth.location.z = 4.5
    sol = cloth.modifiers.new(name="ClothThickness", type='SOLIDIFY')
    sol.thickness = 0.015
    wave = cloth.modifiers.new(name="ClothWave", type='WAVE')
    wave.height = 0.22
    wave.width = 1.1
    wave.speed = 0.25
    wave.narrowness = 1.6
    wave.time_start = -250.0       # start mid-motion, never a flat first frame
    cloth.data.materials.append(cloth_mat)
    for obj in (pole, cross, cloth):
        parent_keep(obj, root)
    return root


def build_smoke(smoke_mat):
    """Distant alpha-card smoke columns drifting over the ruins."""
    for i in range(6):
        sx = random.uniform(-28.0, 28.0)
        sy = random.uniform(22.0, 34.0)
        size = random.uniform(6.0, 12.0)
        bpy.ops.mesh.primitive_plane_add(size=size,
                                         location=(sx, sy, size * 0.45))
        plane = bpy.context.object
        plane.name = "ENV_Smoke"
        plane.rotation_euler = (math.radians(90.0), 0.0,
                                random.uniform(-0.15, 0.15))
        plane.data.materials.append(smoke_mat)


def build_fires():
    """Small emissive fire cores + point lights among the distant ruins."""
    fire_mat = make_principled("M_Fire", (0.2, 0.05, 0.01), roughness=0.4,
                               emission=(1.0, 0.30, 0.04),
                               emission_strength=14.0)
    for fx, fy in ((-18, 24), (3, 26), (21, 23)):
        add_uvsphere("ENV_FireCore", (fx, fy, 0.5), 0.7, material=fire_mat,
                     segments=12, rings=8)
        data = bpy.data.lights.new(name="ENV_FireLight", type='POINT')
        data.color = (1.0, 0.42, 0.12)
        data.energy = 900.0
        data.shadow_soft_size = 2.0
        obj = bpy.data.objects.new("ENV_FireLight", data)
        obj.location = (fx, fy, 1.5)
        bpy.context.collection.objects.link(obj)

# ---------------------------------------------------------------------------
#  GENERALS (stylized humanoid proxies, built facing local +X)
# ---------------------------------------------------------------------------

def build_general(name, location, armor_mat, accent_mat, cape_mat,
                  weapon_kind):
    """Assemble a humanoid proxy from primitives, parented to a root empty.

    weapon_kind: 'GREATSWORD' (two-handed, right side) or 'TWINBLADES'.
    Returns (root_empty, parts_dict) so animation can target specific parts.
    """
    root = add_empty(name, location)
    parts = {}
    ox, oy, _ = location

    def L(x, y, z):
        """Local offset (forward=+X, up=+Z) converted to world position."""
        return (ox + x, oy + y, z)

    # --- body -----------------------------------------------------------------
    parts["torso"] = add_cube(name + "_Torso", L(0, 0, 1.15),
                              (0.26, 0.22, 0.34), material=armor_mat,
                              bevel=0.10)
    parts["trim"] = add_cube(name + "_ChestTrim", L(0.24, 0, 1.22),
                             (0.05, 0.20, 0.14), material=accent_mat,
                             bevel=0.03)
    parts["head"] = add_uvsphere(name + "_Head", L(0.02, 0, 1.74), 0.16,
                                 material=armor_mat)
    parts["crest"] = add_cube(name + "_HelmCrest", L(0.0, 0, 1.92),
                              (0.10, 0.02, 0.06), material=accent_mat,
                              bevel=0.02)
    for side, sy in (("L", 0.30), ("R", -0.30)):
        parts["shoulder" + side] = add_uvsphere(
            name + "_Shoulder" + side, L(0, sy, 1.50), 0.145,
            material=armor_mat)
        parts["arm" + side] = add_cylinder(
            name + "_Arm" + side, L(0.10, sy, 1.12), 0.07, 0.72,
            material=armor_mat,
            rotation=(0.0, math.radians(28.0), 0.0))
        parts["leg" + side] = add_cylinder(
            name + "_Leg" + side, L(0, sy * 0.4, 0.42), 0.095, 0.84,
            material=armor_mat)
        parts["boot" + side] = add_cube(
            name + "_Boot" + side, L(0.05, sy * 0.4, 0.06),
            (0.13, 0.09, 0.06), material=armor_mat, bevel=0.03)

    # --- cape: small waving plane hanging from the shoulders ------------------
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=14,
                                    size=1.0, location=L(-0.28, 0, 1.45))
    cape = bpy.context.object
    cape.name = name + "_Cape"
    cape.scale = (0.70, 1.15, 1.0)   # local X hangs down, local Y spans back
    apply_scale(cape)
    cape.rotation_euler = (0.0, math.radians(90.0), 0.0)   # hang down the back
    set_active(cape)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    cape.location.z = 1.15           # from the shoulders to above the boots
    cape_wave = cape.modifiers.new(name="CapeWave", type='WAVE')
    cape_wave.height = 0.12
    cape_wave.width = 0.7
    cape_wave.speed = 0.22
    cape_wave.narrowness = 1.4
    cape_wave.time_start = -200.0
    cape.data.materials.append(cape_mat)
    parts["cape"] = cape

    # --- weapons --------------------------------------------------------------
    def make_blade(blade_name, hand_loc, length, width):
        """A sword parented to a pivot empty placed at the hand."""
        pivot = add_empty(blade_name + "_Pivot", hand_loc)
        blade = add_cube(blade_name + "_Blade",
                         (hand_loc[0], hand_loc[1], hand_loc[2] + length * 0.55),
                         (width, 0.012, length * 0.5), material=accent_mat,
                         bevel=0.01)
        guard = add_cube(blade_name + "_Guard",
                         (hand_loc[0], hand_loc[1], hand_loc[2] + 0.12),
                         (width * 3.2, 0.03, 0.03), material=armor_mat,
                         bevel=0.01)
        grip = add_cylinder(blade_name + "_Grip",
                            (hand_loc[0], hand_loc[1], hand_loc[2] - 0.05),
                            0.028, 0.30, material=armor_mat)
        pommel = add_uvsphere(blade_name + "_Pommel",
                              (hand_loc[0], hand_loc[1], hand_loc[2] - 0.22),
                              0.04, material=accent_mat, segments=10, rings=8)
        for piece in (blade, guard, grip, pommel):
            parent_keep(piece, pivot)
        return pivot

    if weapon_kind == 'GREATSWORD':
        hand = L(0.34, -0.32, 1.05)
        parts["weaponR"] = make_blade(name + "_Greatsword", hand, 2.1, 0.055)
        parts["weaponR"].rotation_euler = (0.0, math.radians(24.0), 0.0)
    else:  # TWINBLADES
        hand_r = L(0.34, -0.32, 1.05)
        hand_l = L(0.34, 0.32, 1.05)
        parts["weaponR"] = make_blade(name + "_BladeR", hand_r, 1.25, 0.038)
        parts["weaponL"] = make_blade(name + "_BladeL", hand_l, 1.25, 0.038)
        parts["weaponR"].rotation_euler = (0.0, math.radians(20.0),
                                           math.radians(-8.0))
        parts["weaponL"].rotation_euler = (0.0, math.radians(20.0),
                                           math.radians(8.0))

    # --- parent everything to the root ----------------------------------------
    for key, obj in parts.items():
        parent_keep(obj, root)
        if key.startswith("weapon"):
            # weapon pivots carry their own swing animation; keep them under
            # the root so the whole general still moves as one unit.
            pass
    return root, parts

# ---------------------------------------------------------------------------
#  ARMIES (instanced low-poly soldier proxies in ranked rows)
# ---------------------------------------------------------------------------

def make_soldier_mesh(name, material):
    """One merged low-poly soldier: cone body + sphere head + spear."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8,
                          radius1=0.20, radius2=0.10, depth=0.95,
                          matrix=Matrix.Translation((0.0, 0.0, 0.48)))
    bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=6, radius=0.12,
                              matrix=Matrix.Translation((0.0, 0.0, 1.05)))
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=6,
                          radius1=0.02, radius2=0.02, depth=2.1,
                          matrix=Matrix.Translation((0.22, 0.0, 1.10)))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(material)
    return mesh


def build_army(name, mesh, origin_x, direction, rows=6, cols=12):
    """Ranked rows of linked-duplicate soldiers behind one general.

    direction: +1 faces +X (army stands at -X), -1 faces -X.
    Returns the root empty used for the charge animation.
    """
    root = add_empty(name, (0.0, 0.0, 0.0))
    for i in range(cols):
        for j in range(rows):
            obj = bpy.data.objects.new(name + "_Soldier", mesh)
            x = origin_x - direction * (6.0 + i * 1.9) \
                + random.uniform(-0.25, 0.25)
            y = (j - (rows - 1) / 2.0) * 2.3 + random.uniform(-0.3, 0.3)
            obj.location = (x, y, 0.0)
            obj.rotation_euler = (
                0.0, 0.0,
                (0.0 if direction > 0 else math.pi)
                + random.uniform(-0.08, 0.08))
            obj.parent = root
            bpy.context.collection.objects.link(obj)
    return root

# ---------------------------------------------------------------------------
#  EMBERS (particle system instancing an emissive icosphere)
# ---------------------------------------------------------------------------

def build_embers():
    """Rising ember particles across the duel zone and front line."""
    ember_mat = make_principled("M_Ember", (0.3, 0.06, 0.01), roughness=0.4,
                                emission=(1.0, 0.32, 0.05),
                                emission_strength=10.0)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.04,
                                          location=(0.0, 0.0, -60.0))
    ember_obj = bpy.context.object
    ember_obj.name = "FX_EmberInstance"
    ember_obj.data.materials.append(ember_mat)
    ember_obj.hide_render = True        # only particle instances render
    ember_obj.hide_viewport = True

    # Hidden emitter plane at ground level.
    bpy.ops.mesh.primitive_plane_add(size=28.0, location=(0.0, 2.0, 0.15))
    emitter = bpy.context.object
    emitter.name = "FX_EmberEmitter"
    emitter.hide_render = True
    emitter.hide_viewport = True

    set_active(emitter)
    bpy.ops.object.particle_system_add()
    psys = emitter.particle_systems[-1]
    pset = psys.settings
    pset.name = "FX_EmberSettings"
    pset.type = 'EMITTER'
    pset.count = 350
    pset.frame_start = 1.0
    pset.frame_end = float(FRAME_END)
    pset.lifetime = 140.0
    pset.lifetime_random = 0.6
    pset.physics_type = 'NEWTON'
    pset.normal_factor = 0.9            # upward drift
    pset.factor_random = 0.6
    pset.brownian_factor = 1.8
    pset.effector_weights.gravity = 0.04
    pset.render_type = 'OBJECT'
    pset.instance_object = ember_obj
    pset.particle_size = 1.0
    pset.size_random = 0.7

# ---------------------------------------------------------------------------
#  FINAL-SHOT PROPS: TWO SWORDS PLANTED IN THE MUD
# ---------------------------------------------------------------------------

def build_swords_in_mud(gold_mat, silver_mat, dark_mat):
    """A greatsword and a twin blade stuck in the ground at the duel center.
    Camera 6 ends on these; visible from frame 1 but only read in close-up."""
    # General A's greatsword, tilted, driven into the ground.
    gs_root = add_empty("PROP_SwordA", (0.55, 0.35, 0.0))
    blade = add_cube("PROP_SwordA_Blade", (0.55, 0.35, 0.85),
                     (0.055, 0.014, 0.85), material=silver_mat, bevel=0.01)
    guard = add_cube("PROP_SwordA_Guard", (0.55, 0.35, 1.55),
                     (0.20, 0.035, 0.035), material=gold_mat, bevel=0.01)
    grip = add_cylinder("PROP_SwordA_Grip", (0.55, 0.35, 1.78), 0.032, 0.40,
                        material=dark_mat)
    pommel = add_uvsphere("PROP_SwordA_Pommel", (0.55, 0.35, 2.0), 0.05,
                          material=gold_mat, segments=10, rings=8)
    for p in (blade, guard, grip, pommel):
        parent_keep(p, gs_root)
    gs_root.rotation_euler = (math.radians(8.0), math.radians(-12.0), 0.0)

    # General B's twin blade, crossed against the greatsword.
    tb_root = add_empty("PROP_SwordB", (-0.45, -0.25, 0.0))
    blade_b = add_cube("PROP_SwordB_Blade", (-0.45, -0.25, 0.60),
                       (0.038, 0.012, 0.60), material=silver_mat, bevel=0.01)
    guard_b = add_cube("PROP_SwordB_Guard", (-0.45, -0.25, 1.15),
                       (0.13, 0.03, 0.03), material=silver_mat, bevel=0.01)
    grip_b = add_cylinder("PROP_SwordB_Grip", (-0.45, -0.25, 1.32), 0.026,
                          0.30, material=dark_mat)
    for p in (blade_b, guard_b, grip_b):
        parent_keep(p, tb_root)
    tb_root.rotation_euler = (math.radians(-10.0), math.radians(14.0), 0.0)
    return gs_root, tb_root

# ---------------------------------------------------------------------------
#  LIGHTING: overcast key, warm rim spots, soft duel key, low sun
# ---------------------------------------------------------------------------

def build_lights():
    """Cold overcast ambience + warm god-ray rims + soft top key."""
    # Low, cold sun raking across the mud (direction of the god-rays).
    sun_data = bpy.data.lights.new(name="LGT_Sun", type='SUN')
    sun_data.energy = 1.1
    sun_data.color = (0.85, 0.88, 1.0)
    sun_data.angle = math.radians(6.0)
    sun = bpy.data.objects.new("LGT_Sun", sun_data)
    sun.rotation_euler = (math.radians(55.0), 0.0, math.radians(-35.0))
    bpy.context.collection.objects.link(sun)

    # Warm volumetric rim spotlights behind each general.
    def rim_spot(name, location, target, color, energy):
        data = bpy.data.lights.new(name=name, type='SPOT')
        data.color = color
        data.energy = energy
        data.spot_size = math.radians(45.0)
        data.spot_blend = 0.45
        data.shadow_soft_size = 0.3
        data.use_shadow = True
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        point_at(obj, target)
        bpy.context.collection.objects.link(obj)
        return obj
    rim_spot("LGT_RimA", (-9.0, 3.0, 6.5), (-3.5, 0.0, 1.2),
             (1.0, 0.52, 0.22), 2600.0)
    rim_spot("LGT_RimB", (9.0, 3.0, 6.5), (3.5, 0.0, 1.2),
             (1.0, 0.58, 0.28), 2600.0)

    # Soft cool key area light over the duel zone.
    key_data = bpy.data.lights.new(name="LGT_KeyDuel", type='AREA')
    key_data.energy = 1100.0
    key_data.color = (0.72, 0.80, 0.95)
    key_data.shape = 'DISK'
    key_data.size = 7.0
    key = bpy.data.objects.new("LGT_KeyDuel", key_data)
    key.location = (0.0, -1.0, 9.0)
    point_at(key, (0.0, 0.0, 0.0))
    bpy.context.collection.objects.link(key)

    # Very soft frontal fill so faces never crush to black.
    fill_data = bpy.data.lights.new(name="LGT_Fill", type='AREA')
    fill_data.energy = 420.0
    fill_data.color = (0.65, 0.72, 0.85)
    fill_data.size = 10.0
    fill = bpy.data.objects.new("LGT_Fill", fill_data)
    fill.location = (0.0, -10.0, 4.0)
    point_at(fill, (0.0, 0.0, 1.0))
    bpy.context.collection.objects.link(fill)

# ---------------------------------------------------------------------------
#  CAMERAS: 6 setups, timeline markers, animation, handheld noise, DOF
# ---------------------------------------------------------------------------

def make_camera(name, location, target, lens=50.0, dof_obj=None, fstop=2.8):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.sensor_width = 36.0
    cam = bpy.data.objects.new(name, data)
    cam.location = location
    point_at(cam, target)
    bpy.context.collection.objects.link(cam)
    if dof_obj is not None:
        data.dof.use_dof = True
        data.dof.focus_object = dof_obj
        data.dof.aperture_fstop = fstop
        data.dof.aperture_blades = 8
    return cam


def build_cameras_and_markers(scene, genA_root, genB_root):
    """Create the six camera setups, animate them, bind timeline markers."""
    s1, s2, s3, s4, s5, s6 = SHOT_TABLE
    cams = {}

    # Focus empties for depth of field.
    focus_duel = add_empty("CAM_FocusDuel", (0.0, 0.0, 1.2))
    focus_A = add_empty("CAM_FocusA", (-3.5, 0.0, 1.5))
    focus_B = add_empty("CAM_FocusB", (3.5, 0.0, 1.5))

    # --- CAM 1: high drone, slow push-in over the whole battlefield ----------
    cam1 = make_camera("CAM1", (0.0, -85.0, 42.0), (0.0, 10.0, 2.0),
                       lens=32.0, fstop=5.6)
    keyframe(cam1, "location", s1[0], (0.0, -85.0, 42.0))
    keyframe(cam1, "location", s1[1], (0.0, -46.0, 20.0))
    linearize(cam1)
    tgt1 = add_empty("CAM1_Target", (0.0, 10.0, 2.0))
    con = cam1.constraints.new(type='TRACK_TO')
    con.target = tgt1
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    add_handheld_noise(cam1, loc_strength=0.03, rot_strength=0.0, scale=45.0)
    cams["CAM1"] = cam1

    # --- CAM 2: low-angle hero shot, slow push-in on General A ---------------
    cam2 = make_camera("CAM2", (-1.8, -5.2, 0.75), (-3.5, 0.0, 1.6),
                       lens=58.0, dof_obj=focus_A, fstop=2.0)
    keyframe(cam2, "location", s2[0], (-1.8, -5.2, 0.75))
    keyframe(cam2, "location", s2[1], (-2.4, -3.6, 0.95))
    linearize(cam2)
    tgt2 = add_empty("CAM2_Target", (-3.5, 0.0, 1.6))
    con = cam2.constraints.new(type='TRACK_TO')
    con.target = tgt2
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    add_handheld_noise(cam2, loc_strength=0.04, rot_strength=0.0, scale=26.0)
    cams["CAM2"] = cam2

    # --- CAM 3: mirrored low-angle hero shot on General B --------------------
    cam3 = make_camera("CAM3", (1.8, -5.2, 0.75), (3.5, 0.0, 1.6),
                       lens=58.0, dof_obj=focus_B, fstop=2.0)
    keyframe(cam3, "location", s3[0], (1.8, -5.2, 0.75))
    keyframe(cam3, "location", s3[1], (2.4, -3.6, 0.95))
    linearize(cam3)
    tgt3 = add_empty("CAM3_Target", (3.5, 0.0, 1.6))
    con = cam3.constraints.new(type='TRACK_TO')
    con.target = tgt3
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    add_handheld_noise(cam3, loc_strength=0.04, rot_strength=0.0, scale=26.0)
    cams["CAM3"] = cam3

    # --- CAM 4: lateral tracking shot over the charging army ------------------
    cam4 = make_camera("CAM4", (-16.0, -7.0, 2.6), (0.0, 0.0, 1.0),
                       lens=35.0, fstop=4.0)
    keyframe(cam4, "location", s4[0], (-16.0, -7.0, 2.6))
    keyframe(cam4, "location", s4[1], (16.0, -7.0, 2.2))
    linearize(cam4)
    tgt4 = add_empty("CAM4_Target", (0.0, 2.0, 1.0))
    con = cam4.constraints.new(type='TRACK_TO')
    con.target = tgt4
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    add_handheld_noise(cam4, loc_strength=0.09, rot_strength=0.0, scale=18.0)
    cams["CAM4"] = cam4

    # --- CAM 5: close two-shot, gentle dolly while the generals circle --------
    cam5 = make_camera("CAM5", (0.0, -6.2, 1.7), (0.0, 0.0, 1.35),
                       lens=50.0, dof_obj=focus_duel, fstop=2.2)
    keyframe(cam5, "location", s5[0], (0.0, -6.2, 1.7))
    keyframe(cam5, "location", s5[1], (2.2, -5.2, 1.5))
    linearize(cam5)
    tgt5 = add_empty("CAM5_Target", (0.0, 0.0, 1.35))
    con = cam5.constraints.new(type='TRACK_TO')
    con.target = tgt5
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    add_handheld_noise(cam5, loc_strength=0.05, rot_strength=0.0, scale=24.0)
    cams["CAM5"] = cam5

    # --- CAM 6: slow orbit of the duel, descending to the swords -------------
    # No constraint: position AND rotation are keyframed so the final move
    # can leave the orbit and settle on the two swords planted in the mud.
    cam6 = make_camera("CAM6", (7.0, 0.0, 3.2), (0.0, 0.0, 1.2),
                       lens=40.0, dof_obj=focus_duel, fstop=2.4)
    seg_start, seg_end = s6[0], s6[1]
    descend_start = seg_start + int((seg_end - seg_start) * 0.62)
    orbit_frames = [seg_start,
                    seg_start + (descend_start - seg_start) // 3,
                    seg_start + 2 * (descend_start - seg_start) // 3,
                    descend_start]
    for i, f in enumerate(orbit_frames):
        angle = math.radians(20.0 + 140.0 * i / 3.0)
        pos = (7.0 * math.cos(angle), 7.0 * math.sin(angle), 3.0)
        cam6.location = pos
        point_at(cam6, (0.0, 0.0, 1.2))
        cam6.keyframe_insert(data_path="location", frame=f)
        cam6.keyframe_insert(data_path="rotation_euler", frame=f)
    # Final descent: settle low over the planted swords.
    cam6.location = (0.9, -1.1, 0.55)
    point_at(cam6, (0.05, 0.05, 0.9))
    cam6.keyframe_insert(data_path="location", frame=seg_end - 4)
    cam6.keyframe_insert(data_path="rotation_euler", frame=seg_end - 4)
    linearize(cam6)
    # Retarget DOF to the swords for the closing frames. Pointer properties
    # are not animatable on every build, so fall back to a static focus.
    focus_swords = add_empty("CAM_FocusSwords", (0.05, 0.05, 0.9))
    try:
        cam6.data.dof.focus_object = focus_duel
        cam6.data.dof.keyframe_insert(data_path="focus_object",
                                      frame=descend_start)
        cam6.data.dof.focus_object = focus_swords
        cam6.data.dof.keyframe_insert(data_path="focus_object",
                                      frame=descend_start + 1)
    except Exception:
        cam6.data.dof.focus_object = focus_swords
    add_handheld_noise(cam6, loc_strength=0.04, rot_strength=0.008, scale=28.0)
    cams["CAM6"] = cam6

    # --- timeline markers: one hard cut per camera setup ----------------------
    for (marker_name, f0, _f1), cam in zip(SHOT_TABLE, cams.values()):
        marker = scene.timeline_markers.new(marker_name, frame=f0)
        marker.camera = cam
    scene.camera = cam1
    return cams

# ---------------------------------------------------------------------------
#  CHARACTER / ARMY ANIMATION
# ---------------------------------------------------------------------------

def animate_intro_idle(gen_root, sway_phase):
    """Subtle idle breathing/sway so the generals are never frozen.
    Stops just before the duel animation takes over the same transforms."""
    root = gen_root
    base_loc = root.location.copy()
    base_rot = root.rotation_euler.copy()
    idle_end = F(5100)
    step = max(4, F(240))
    for f in range(1, idle_end + 1, step):
        t = f / float(FPS)
        root.location = (base_loc.x, base_loc.y,
                         base_loc.z + 0.02 * math.sin(t * 1.4 + sway_phase))
        root.rotation_euler = (
            base_rot.x, base_rot.y,
            base_rot.z + 0.015 * math.sin(t * 0.8 + sway_phase))
        root.keyframe_insert(data_path="location", frame=f)
        root.keyframe_insert(data_path="rotation_euler", frame=f)
    linearize(root)


def animate_charge(armyA_root, armyB_root):
    """Both armies surge forward during the tracking shot (4000-5200)."""
    f0, f1 = F(4000), F(5200)
    for root, direction in ((armyA_root, 1.0), (armyB_root, -1.0)):
        root.location = (0.0, 0.0, 0.0)
        root.keyframe_insert(data_path="location", frame=1)
        root.keyframe_insert(data_path="location", frame=f0)
        root.location = (direction * 7.0, 0.0, 0.0)
        root.keyframe_insert(data_path="location", frame=f1)
        root.keyframe_insert(data_path="location", frame=FRAME_END)
        linearize(root)


def animate_duel(genA_root, genB_root, partsA, partsB):
    """Generals circle each other through shots 5-6; weapon strikes keyed
    on the arm pivots; both end close to the planted swords at the center."""
    f_start, f_end = F(5200), F(6500)
    radius = 3.4
    steps = 6
    for i in range(steps + 1):
        f = f_start + int((f_end - f_start) * i / steps)
        ang = math.pi + math.radians(190.0 * i / steps)   # A sweeps ~190 deg
        posA = (radius * math.cos(ang), radius * math.sin(ang), 0.0)
        posB = (-posA[0], -posA[1], 0.0)
        for root, pos in ((genA_root, posA), (genB_root, posB)):
            root.location = pos
            root.keyframe_insert(data_path="location", frame=f)
        face_xy(genA_root, posB[0], posB[1])
        face_xy(genB_root, posA[0], posA[1])
        genA_root.keyframe_insert(data_path="rotation_euler", frame=f)
        genB_root.keyframe_insert(data_path="rotation_euler", frame=f)
    linearize(genA_root)
    linearize(genB_root)

    # Weapon strikes: wind up, clash, recover (mirrored between generals).
    for f_clash in (F(5600), F(5900), F(6200), F(6450)):
        windup = max(1, f_clash - max(2, F(90)))
        recover = min(FRAME_END, f_clash + max(2, F(110)))
        for pivot, sign in ((partsA["weaponR"], 1.0),
                            (partsB["weaponR"], -1.0)):
            base = pivot.rotation_euler.copy()
            pivot.rotation_euler = (base.x, base.y + sign * 0.55, base.z)
            pivot.keyframe_insert(data_path="rotation_euler", frame=windup)
            pivot.rotation_euler = (base.x, base.y - sign * 0.95, base.z)
            pivot.keyframe_insert(data_path="rotation_euler", frame=f_clash)
            pivot.rotation_euler = base
            pivot.keyframe_insert(data_path="rotation_euler", frame=recover)
            linearize(pivot)
        if "weaponL" in partsB:
            pivot = partsB["weaponL"]
            base = pivot.rotation_euler.copy()
            pivot.rotation_euler = (base.x, base.y - 0.4, base.z)
            pivot.keyframe_insert(data_path="rotation_euler", frame=windup)
            pivot.rotation_euler = (base.x, base.y + 0.8, base.z)
            pivot.keyframe_insert(data_path="rotation_euler", frame=f_clash)
            pivot.rotation_euler = base
            pivot.keyframe_insert(data_path="rotation_euler", frame=recover)
            linearize(pivot)

# ---------------------------------------------------------------------------
#  COMPOSITOR: glare bloom + lens distortion + film grain + vignette + fade
# ---------------------------------------------------------------------------

def build_compositor(scene):
    """Post chain:
    RenderLayers -> Glare(fog glow) -> LensDistortion -> grain overlay
    -> vignette multiply -> fade-to-black mix -> Composite / Viewer.
    """
    scene.use_nodes = True
    nt = scene.node_tree
    nt.nodes.clear()
    n = nt.nodes
    link = nt.links.new

    rl = n.new("CompositorNodeRLayers")
    rl.location = (0, 200)

    # --- bloom ----------------------------------------------------------------
    glare = n.new("CompositorNodeGlare")
    glare.glare_type = 'FOG_GLOW'
    glare.quality = 'HIGH'
    glare.threshold = 1.0
    glare.size = 7
    glare.location = (220, 200)
    link(rl.outputs["Image"], glare.inputs["Image"])

    # --- slight anamorphic-style lens distortion ------------------------------
    lens = n.new("CompositorNodeLensdist")
    lens.location = (420, 200)
    lens.inputs["Distortion"].default_value = 0.015
    lens.inputs["Dispersion"].default_value = 0.008
    link(glare.outputs["Image"], lens.inputs["Image"])

    # --- animated film grain (Clouds texture overlay) --------------------------
    grain_tex = bpy.data.textures.new("TEX_Grain", type='CLOUDS')
    grain_tex.noise_scale = 0.12
    tex_node = n.new("CompositorNodeTexture")
    tex_node.texture = grain_tex
    tex_node.location = (420, -40)
    # Animate the texture offset so the grain changes every few frames.
    offset_sock = tex_node.inputs["Offset"]
    step = max(2, int(FPS / 3))
    for f in range(1, FRAME_END + 1, step):
        offset_sock.default_value = (random.uniform(-5, 5),
                                     random.uniform(-5, 5), 0.0)
        offset_sock.keyframe_insert("default_value", frame=f, index=0)
        offset_sock.keyframe_insert("default_value", frame=f, index=1)
    grain = n.new("CompositorNodeMixRGB")
    grain.blend_type = 'OVERLAY'
    grain.inputs[0].default_value = 0.06
    grain.location = (640, 200)
    link(lens.outputs["Image"], grain.inputs[1])
    link(tex_node.outputs["Color"], grain.inputs[2])

    # --- vignette: blurred ellipse mask multiplied over the frame --------------
    mask = n.new("CompositorNodeEllipseMask")
    mask.location = (640, -180)
    mask.width = 0.62
    mask.height = 0.55
    blur = n.new("CompositorNodeBlur")
    blur.filter_type = 'GAUSS'
    blur.location = (820, -180)
    try:
        blur.size_x = int(RES_X * 0.30)
        blur.size_y = int(RES_Y * 0.30)
    except Exception:
        pass
    link(mask.outputs["Mask"], blur.inputs["Image"])
    vignette = n.new("CompositorNodeMixRGB")
    vignette.blend_type = 'MULTIPLY'
    vignette.inputs[0].default_value = 0.55
    vignette.location = (1040, 200)
    link(grain.outputs["Image"], vignette.inputs[1])
    link(blur.outputs["Image"], vignette.inputs[2])

    # --- fade to black over the final frames -----------------------------------
    fade_val = n.new("CompositorNodeValue")
    fade_val.location = (1040, -60)
    fade_val.label = "FADE_TO_BLACK"
    fade_start = max(1, FRAME_END - max(6, F(48)))
    fade_val.outputs[0].default_value = 0.0
    fade_val.outputs[0].keyframe_insert("default_value", frame=fade_start)
    fade_val.outputs[0].default_value = 1.0
    fade_val.outputs[0].keyframe_insert("default_value", frame=FRAME_END)
    fade = n.new("CompositorNodeMixRGB")
    fade.blend_type = 'MIX'
    fade.inputs[2].default_value = (0.0, 0.0, 0.0, 1.0)
    fade.location = (1240, 200)
    link(vignette.outputs["Image"], fade.inputs[1])
    link(fade_val.outputs[0], fade.inputs[0])

    comp = n.new("CompositorNodeComposite")
    comp.location = (1460, 200)
    viewer = n.new("CompositorNodeViewer")
    viewer.location = (1460, 60)
    link(fade.outputs["Image"], comp.inputs["Image"])
    link(fade.outputs["Image"], viewer.inputs["Image"])

# ---------------------------------------------------------------------------
#  EXPORT: placeholder audio + ffmpeg mux
# ---------------------------------------------------------------------------

def ensure_audio():
    """Write a placeholder silent audio.wav with ffmpeg if it is missing."""
    if os.path.exists(AUDIO_PATH):
        return True
    cmd = ["ffmpeg", "-y",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-t", "%.3f" % DURATION_S, AUDIO_PATH]
    try:
        subprocess.run(cmd, check=True)
        print("[war_scene] wrote placeholder silent audio:", AUDIO_PATH)
        return True
    except Exception as exc:
        print("[war_scene] could not create placeholder audio:", exc)
        return False


def mux_mp4():
    """Mux rendered PNG frames + audio into war_scene.mp4 via ffmpeg."""
    pattern = os.path.join(RENDER_DIR, "frame_%04d.png")
    cmd = ["ffmpeg", "-y",
           "-framerate", str(FPS),
           "-i", pattern,
           "-i", AUDIO_PATH,
           "-c:v", "libx264",
           "-pix_fmt", "yuv420p",
           "-crf", "18",
           "-c:a", "aac",
           "-shortest",
           MP4_PATH]
    print("[war_scene] running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        print("[war_scene] MP4 written:", MP4_PATH)
    except Exception as exc:
        print("[war_scene] ffmpeg mux failed:", exc)
        print("[war_scene] frames are still available in:", RENDER_DIR)

# ---------------------------------------------------------------------------
#  MAIN BUILD
# ---------------------------------------------------------------------------

def main():
    scene = bpy.context.scene
    clean_scene()
    os.makedirs(RENDER_DIR, exist_ok=True)

    setup_render(scene)
    setup_world(scene)

    # --- shared materials ------------------------------------------------------
    mud_mat = make_mud_material()
    stone_mat = make_stone_material()
    wood_mat = make_principled("M_Wood", (0.09, 0.055, 0.03), roughness=0.85)
    vol_mat = make_volume_material("M_Fog", 0.0045, (0.45, 0.50, 0.58), 0.35)
    smoke_mat = make_smoke_material()

    # General A: black-and-gold ornate plate, crimson cape.
    armorA = make_principled("M_ArmorA_BlackGold", (0.015, 0.012, 0.014),
                             metallic=0.9, roughness=0.35)
    goldA = make_principled("M_GoldA", (0.45, 0.28, 0.06),
                            metallic=1.0, roughness=0.28)
    capeA = make_principled("M_CapeA_Crimson", (0.22, 0.008, 0.012),
                            roughness=0.8)
    # General B: silver-blue engraved plate, white cape.
    armorB = make_principled("M_ArmorB_SilverBlue", (0.35, 0.42, 0.55),
                             metallic=0.95, roughness=0.25)
    trimB = make_principled("M_TrimB", (0.55, 0.65, 0.85),
                            metallic=1.0, roughness=0.2)
    capeB = make_principled("M_CapeB_White", (0.62, 0.64, 0.68),
                            roughness=0.85)
    # Army metals: dark host for A, bright host for B (heraldry consistency).
    hostA = make_principled("M_HostA_Dark", (0.05, 0.045, 0.05),
                            metallic=0.8, roughness=0.5)
    hostB = make_principled("M_HostB_Bright", (0.30, 0.34, 0.42),
                            metallic=0.85, roughness=0.4)
    crimson_banner = make_principled("M_BannerCrimson", (0.30, 0.01, 0.015),
                                     roughness=0.75)
    azure_banner = make_principled("M_BannerAzure", (0.04, 0.10, 0.30),
                                   roughness=0.75)

    # --- environment ------------------------------------------------------------
    build_ground(mud_mat)
    build_fog(vol_mat)
    build_ruins(stone_mat)
    build_siege_towers(wood_mat)
    build_banner("BANNER_A", -6.5, 4.0, crimson_banner, goldA)
    build_banner("BANNER_B", 6.5, 4.0, azure_banner, trimB)
    build_smoke(smoke_mat)
    build_fires()
    build_embers()

    # --- characters --------------------------------------------------------------
    genA_root, partsA = build_general("GEN_A", (-3.5, 0.0, 0.0),
                                      armorA, goldA, capeA, 'GREATSWORD')
    genB_root, partsB = build_general("GEN_B", (3.5, 0.0, 0.0),
                                      armorB, trimB, capeB, 'TWINBLADES')
    genA_root.rotation_euler = (0.0, 0.0, 0.0)          # faces +X toward B
    genB_root.rotation_euler = (0.0, 0.0, math.pi)      # faces -X toward A

    # --- armies --------------------------------------------------------------------
    meshA = make_soldier_mesh("MESH_SoldierA", hostA)
    meshB = make_soldier_mesh("MESH_SoldierB", hostB)
    armyA_root = build_army("ARMY_A", meshA, origin_x=-3.5, direction=1)
    armyB_root = build_army("ARMY_B", meshB, origin_x=3.5, direction=-1)

    # --- final-shot props ----------------------------------------------------------
    build_swords_in_mud(goldA, trimB, armorA)

    # --- lighting --------------------------------------------------------------------
    build_lights()

    # --- cameras -----------------------------------------------------------------------
    build_cameras_and_markers(scene, genA_root, genB_root)

    # --- animation -----------------------------------------------------------------------
    animate_intro_idle(genA_root, 0.0)
    animate_intro_idle(genB_root, 2.1)
    animate_charge(armyA_root, armyB_root)
    animate_duel(genA_root, genB_root, partsA, partsB)

    # --- compositor ----------------------------------------------------------------------
    build_compositor(scene)

    # --- save a .blend next to the script for inspection -----------------------------------
    blend_path = os.path.join(BASE_DIR, "war_scene.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=blend_path, check_existing=False)
        print("[war_scene] scene saved:", blend_path)
    except Exception as exc:
        print("[war_scene] could not save .blend (continuing):", exc)

    # --- render + export --------------------------------------------------------------------
    if DO_RENDER:
        print("[war_scene] rendering %d frames at %dx%d, %d fps ..."
              % (FRAME_END, RES_X, RES_Y, FPS))
        bpy.ops.render.render(animation=True)
        if DO_FFMPEG:
            if ensure_audio():
                mux_mp4()
    else:
        print("[war_scene] DO_RENDER=False; press F12 or run the render "
              "manually, then call ensure_audio() and mux_mp4().")

    print("[war_scene] done.")


if __name__ == "__main__":
    main()
