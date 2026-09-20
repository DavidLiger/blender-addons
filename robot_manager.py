bl_info = {
    "name": "Character Manager",
    "author": "David",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Character Manager",
    "description": ("Bibliotheque de robots rigges et de postures : instanciation en scene, "
                    "postures enregistrees a la main ou extraites d'une animation Mixamo"),
    "category": "3D View",
}

import bpy
import bpy.utils.previews
import json
import os
import re
import subprocess

from mathutils import Matrix, Vector


# ---------------------------------------------------------------------------
# Arborescence
#   <racine>/creations/<robot>/
#       preview.png       vignette du robot
#       mixamo-rigged/    retour de l'auto-rigger (fbx)
#       animations/       animations Mixamo (fbx)
#       postures/         <nom>.json + <nom>.png
# ---------------------------------------------------------------------------
CREATIONS = "creations"
D_RIGGED = "mixamo-rigged"
D_ANIM = "animations"
D_POSE = "postures"
READY_FILE = "ready.blend"
D_EXPR = "expressions-arkit"
PRESETS = "_presets"

COLL_PREFIX = "PERSO_"
COLL_PREFIXES = ("PERSO_", "ROBOT_")   # ROBOT_ : fichiers anterieurs


def coll_name(coll_or_name):
    """Nom du personnage, quel que soit le prefixe du fichier d'origine."""
    n = coll_or_name if isinstance(coll_or_name, str) else coll_or_name.name
    for p in COLL_PREFIXES:
        if n.startswith(p):
            return n[len(p):]
    return n


def is_char_coll(coll_or_name):
    n = coll_or_name if isinstance(coll_or_name, str) else coll_or_name.name
    return n.startswith(COLL_PREFIXES)

K_ROBOT = "robot"
K_ASSET = "rbm_asset"  # marque les materiaux/images importes par l'addon,
                       # pour ne jamais fusionner avec un datablock etranger
                       # qui porterait le meme nom generique par coincidence
                       # (ex: "Image_0" par defaut de Blender)

_previews = None
_robots = []        # [(nom, dossier)]
_postures = {}      # robot -> [(nom, chemin json)]
_anims = {}         # robot -> [(nom, chemin fbx)]


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_")


# ---------------------------------------------------------------------------
# Preferences : la racine est reprise de Robot Maker si l'addon est installe
# ---------------------------------------------------------------------------
class RBM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    root: bpy.props.StringProperty(
        name="Dossier ROBOTS", subtype='DIR_PATH', default="",
        description="Laisser vide pour reprendre celui de Robot Maker")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "root")
        shared = maker_root()
        if shared:
            row = layout.row()
            row.scale_y = 0.7
            row.label(text="Robot Maker : " + shared, icon='INFO')


def maker_root():
    """Racine definie dans Robot Maker, si l'addon est present."""
    try:
        addon = bpy.context.preferences.addons.get("robot_maker")
        if addon and addon.preferences.root:
            return bpy.path.abspath(addon.preferences.root)
    except Exception:
        pass
    return ""


def root_path(context=None):
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        if prefs.root:
            return bpy.path.abspath(prefs.root)
    except Exception:
        pass
    return maker_root()


def robot_dir(name):
    root = root_path()
    if not root or not name:
        return ""
    return os.path.join(root, CREATIONS, name)


def sub_dir(robot, sub, create=False):
    base = robot_dir(robot)
    if not base:
        return ""
    path = os.path.join(base, sub)
    if create:
        os.makedirs(path, exist_ok=True)
    return path

def scan_animations(robot, folder):
    """Animations du personnage, completees par la bibliotheque commune.
    Une animation propre au personnage masque celle de meme nom.
    Retourne [(identifiant, libelle, chemin)]."""
    found = {}
    root = root_path()

    sources = []
    if root:
        sources.append((os.path.join(root, D_ANIM), "  (commune)"))
    sources.append((os.path.join(folder, D_ANIM), ""))

    for base, tag in sources:
        if not os.path.isdir(base):
            continue
        for fname in sorted(os.listdir(base)):
            if not fname.lower().endswith(".fbx"):
                continue
            name = fname[:-4]
            found[name] = (name, name + tag, os.path.join(base, fname))

    return sorted(found.values(), key=lambda e: e[1])

# ---------------------------------------------------------------------------
# Lecture des dossiers
# ---------------------------------------------------------------------------
def scan_all(context=None):
    global _robots, _postures, _anims, _families

    _robots, _postures, _anims, _families = [], {}, {}, {}
    if _previews is not None:
        _previews.clear()

    root = root_path()
    creations = os.path.join(root, CREATIONS) if root else ""
    if not creations or not os.path.isdir(creations):
        return 0

    for name in sorted(os.listdir(creations)):
        folder = os.path.join(creations, name)
        # Les dossiers techniques commencent par _ : ce ne sont pas des persos
        if not os.path.isdir(folder) or name.startswith("_"):
            continue

        _robots.append((name, folder))
        
        try:
            with open(os.path.join(folder, CHARACTER_FILE), "r", encoding="utf-8") as f:
                _families[name] = json.load(f).get("family", 'ROBOT')
        except Exception:
            _families[name] = 'ROBOT'

        thumb = os.path.join(folder, "preview.png")
        if _previews is not None and os.path.isfile(thumb):
            _previews.load("robot/" + name, thumb, 'IMAGE')

        # Postures
        poses = []
        pdir = os.path.join(folder, D_POSE)
        if os.path.isdir(pdir):
            for fname in sorted(os.listdir(pdir)):
                if not fname.lower().endswith(".json"):
                    continue
                base = fname[:-5]
                poses.append((base, os.path.join(pdir, fname)))

                png = os.path.join(pdir, base + ".png")
                if _previews is not None and os.path.isfile(png):
                    _previews.load("pose/" + name + "/" + base, png, 'IMAGE')
        _postures[name] = poses

        _anims[name] = scan_animations(name, folder)

    return len(_robots)


def icon_of(key):
    if _previews is None:
        return 0
    prev = _previews.get(key)
    return prev.icon_id if prev else 0


def robot_enum(self, context):
    items = [(n, n, f) for n, f in _robots]
    return items or [('', "(aucun robot)", "")]


def anim_enum(self, context):
    scene = context.scene if context else None
    robot = scene.rbm_robot if scene else ""
    items = [(ident, label, path) for ident, label, path in _anims.get(robot, [])]
    return items or [('', "(aucune animation)", "")]


# ---------------------------------------------------------------------------
# Rendu de vignette : scene temporaire, moteur Workbench, fond transparent
# ---------------------------------------------------------------------------
def render_thumbnail(context, objects, path, size=256, front=True, frame_on=None, zoom=1.25):
    """Rend les objets donnes, dans leur etat evalue (pose comprise)."""
    if not objects:
        return False, "aucun objet"

    scn = bpy.data.scenes.new("_rbm_thumb")
    scn.render.engine = 'BLENDER_WORKBENCH'
    scn.render.resolution_x = size
    scn.render.resolution_y = size
    scn.render.film_transparent = True
    scn.render.image_settings.file_format = 'PNG'
    scn.render.image_settings.color_mode = 'RGBA'

    depsgraph = context.evaluated_depsgraph_get()
    temps = []
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))

    for obj in objects:
        if obj.type != 'MESH':
            continue
        try:
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
        except Exception:
            continue
        if mesh is None or not len(mesh.vertices):
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            continue

        dup = bpy.data.objects.new("_rbm_" + obj.name, mesh)
        dup.matrix_world = obj.matrix_world.copy()
        scn.collection.objects.link(dup)
        temps.append(dup)

        if frame_on is None or obj in frame_on:
            for v in mesh.vertices:
                p = dup.matrix_world @ v.co
                for i in range(3):
                    lo[i] = min(lo[i], p[i])
                    hi[i] = max(hi[i], p[i])

    if not temps:
        bpy.data.scenes.remove(scn)
        return False, "aucun maillage"

    center = (lo + hi) / 2.0
    extent = max(hi[i] - lo[i] for i in range(3)) or 1.0

    cam_data = bpy.data.cameras.new("_rbm_cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = extent * zoom
    cam_data.clip_start = 0.001
    cam_data.clip_end = extent * 20.0

    cam = bpy.data.objects.new("_rbm_cam", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam

    direction = Vector((0.0, -1.0, 0.15)) if front else Vector((1.0, -1.2, 0.7))
    direction.normalize()
    cam.matrix_world = (Matrix.Translation(center + direction * extent * 5.0)
                        @ direction.to_track_quat('Z', 'Y').to_matrix().to_4x4())

    scn.render.filepath = path
    scn.render.use_file_extension = False

    window = context.window
    previous = window.scene if window else None
    error = ""
    try:
        if window is not None:
            window.scene = scn
        bpy.ops.render.render(write_still=True)
    except Exception as e:
        error = str(e)
    finally:
        if window is not None and previous is not None:
            window.scene = previous

    for dup in temps:
        mesh = dup.data
        bpy.data.objects.remove(dup)
        bpy.data.meshes.remove(mesh)
    bpy.data.scenes.remove(scn)
    bpy.data.cameras.remove(cam_data)

    if error:
        return False, error
    return os.path.isfile(path), "" if os.path.isfile(path) else "fichier non ecrit"

# ---------------------------------------------------------------------------
# Suivi de la selection : cliquer un robot dans la scene le designe dans la liste
# ---------------------------------------------------------------------------
_last_active = None


def robot_of(obj):
    """Nom du personnage auquel appartient cet objet, par sa cle ou sa collection."""
    if obj is None:
        return ""

    name = obj.get(K_ROBOT)
    if name:
        return name

    # Repli : collection ROBOT_<nom>_NN posee a l'instanciation
    for coll in obj.users_collection:
        if is_char_coll(coll):
            return re.sub(r"_\d+$", "", coll_name(coll))

    parent = obj.parent
    return robot_of(parent) if parent is not None else ""


def _follow_selection():
    global _last_active

    try:
        scene = bpy.context.scene
        obj = bpy.context.view_layer.objects.active
    except Exception:
        return 0.3

    key = obj.name if obj else None
    if key == _last_active:
        return 0.3
    _last_active = key

    if scene is None or not scene.rbm_follow:
        return 0.3

    name = robot_of(obj)
    if name and name != scene.rbm_robot and any(n == name for n, _ in _robots):
        scene.rbm_robot = name
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    return 0.3

# ---------------------------------------------------------------------------
# Control rig
# ---------------------------------------------------------------------------
def is_control_rig(obj):
    return (obj is not None and obj.type == 'ARMATURE'
            and obj.data is not None and "mr_control_rig" in obj.data.keys())


def find_control_rig(context):
    """Control rig actif, sinon celui du robot selectionne dans la scene."""
    obj = context.active_object
    if is_control_rig(obj):
        return obj

    if obj is not None:
        for parent in (obj.parent, getattr(obj, "find_armature", lambda: None)()):
            if is_control_rig(parent):
                return parent

    rigs = [o for o in context.scene.objects if is_control_rig(o)]
    if len(rigs) == 1:
        return rigs[0]

    selected = [o for o in context.selected_objects if is_control_rig(o)]
    return selected[0] if selected else None


def rig_meshes(rig):
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and o.find_armature() == rig]


# ---------------------------------------------------------------------------
# Postures : lecture / ecriture
# ---------------------------------------------------------------------------
def pose_to_dict(rig):
    bones = {}
    for pb in rig.pose.bones:
        entry = {
            "mode": pb.rotation_mode,
            "loc": list(pb.location),
            "scale": list(pb.scale),
        }
        if pb.rotation_mode == 'QUATERNION':
            entry["rot"] = list(pb.rotation_quaternion)
        elif pb.rotation_mode == 'AXIS_ANGLE':
            entry["rot"] = list(pb.rotation_axis_angle)
        else:
            entry["rot"] = list(pb.rotation_euler)
        bones[pb.name] = entry
    return {"rig": rig.name, "bones": bones}


def dict_to_pose(rig, data):
    bones = data.get("bones", {})
    applied, missing = 0, 0

    for name, entry in bones.items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            missing += 1
            continue

        pb.location = entry.get("loc", (0.0, 0.0, 0.0))
        pb.scale = entry.get("scale", (1.0, 1.0, 1.0))

        mode = entry.get("mode", pb.rotation_mode)
        rot = entry.get("rot")
        if rot is None:
            applied += 1
            continue

        if mode == 'QUATERNION':
            pb.rotation_mode = 'QUATERNION'
            pb.rotation_quaternion = rot
        elif mode == 'AXIS_ANGLE':
            pb.rotation_mode = 'AXIS_ANGLE'
            pb.rotation_axis_angle = rot
        else:
            pb.rotation_mode = mode
            pb.rotation_euler = rot

        applied += 1

    return applied, missing


def keyframe_pose(rig, frame, bones=None):
    """Insere une keyframe (loc/rot/scale) pour chaque os pose, a la frame
    donnee - sans passer par le mode Pose ni par la touche I. Fonctionne
    quel que soit le mode courant (Objet, Pose...) puisque PoseBone.keyframe_insert
    resout lui-meme le chemin RNA complet vers l'action de l'armature.

    bones : sous-ensemble de noms d'os a garder (defaut : tous les os pose,
    c'est-a-dire la posture entiere, comme pose_to_dict)."""
    if rig is None:
        return 0

    if rig.animation_data is None:
        rig.animation_data_create()
    if rig.animation_data.action is None:
        rig.animation_data.action = bpy.data.actions.new(name=rig.name + "_Action")

    count = 0
    for pb in rig.pose.bones:
        if bones is not None and pb.name not in bones:
            continue

        pb.keyframe_insert(data_path="location", frame=frame, group=pb.name)

        if pb.rotation_mode == 'QUATERNION':
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame, group=pb.name)
        elif pb.rotation_mode == 'AXIS_ANGLE':
            pb.keyframe_insert(data_path="rotation_axis_angle", frame=frame, group=pb.name)
        else:
            pb.keyframe_insert(data_path="rotation_euler", frame=frame, group=pb.name)

        pb.keyframe_insert(data_path="scale", frame=frame, group=pb.name)
        count += 1

    return count

# Nom de la posture de repos, creee automatiquement a la premiere instanciation.
# Le prefixe numerique la place en tete de liste.
REST_POSTURE = "00_repos"


def save_posture(context, robot, rig, name, overwrite=False):
    """Ecrit une posture : JSON des os + vignette. Retourne (ok, message)."""
    if not robot or rig is None:
        return False, "robot ou control rig manquant"

    name = safe_name(name) or "posture"
    folder = sub_dir(robot, D_POSE, create=True)
    if not folder:
        return False, "dossier du robot introuvable"

    path = os.path.join(folder, name + ".json")
    if os.path.isfile(path) and not overwrite:
        return False, "'{}' existe deja (cocher Ecraser)".format(name)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pose_to_dict(rig), f, indent=1)
    except Exception as e:
        return False, "ecriture impossible : {}".format(e)

    ok, err = render_thumbnail(context, rig_meshes(rig),
                               os.path.join(folder, name + ".png"),
                               context.scene.rbm_thumb_size)
    scan_all(context)

    msg = "posture '{}' enregistree".format(name)
    if not ok:
        msg += " (vignette : {})".format(err)
    return True, msg


# ---------------------------------------------------------------------------
# Operateurs
# ---------------------------------------------------------------------------
class RBM_OT_scan(bpy.types.Operator):
    bl_idname = "rbm.scan"
    bl_label = "Relire les dossiers"
    bl_description = "Relit la liste des robots, des postures et des animations"

    def execute(self, context):
        if not root_path():
            self.report({'ERROR'}, "Racine non definie (preferences de l'addon)")
            return {'CANCELLED'}

        count = scan_all(context)
        self.report({'INFO'}, "{} robot(s)".format(count))
        return {'FINISHED'}

# Sous-dossiers crees pour chaque nouveau personnage
CHARACTER_DIRS = ["prototype", D_RIGGED, D_ANIM, D_POSE, "expressions"]
FAMILIES = [
    ('ROBOT', "Robot", "Corps mecanique, membres tubulaires"),
    ('HUMAN', "Humanoide", "Corps habille, pas de tubes de liaison"),
    ('ANIMAL', "Animal", "Quadrupede, creature, asset pre-anime externe"),
]
FAMILY_LABEL = {'ROBOT': "", 'HUMAN': "humanoide", 'ANIMAL': "animal"}
FAMILY_FILTER = [('ALL', "Tous", "")] + [(k, lbl, d) for k, lbl, d in FAMILIES]
PER_PAGE = 6
CHARACTER_FILE = "character.json"

class RBM_OT_new_character(bpy.types.Operator):
    bl_idname = "rbm.new_character"
    bl_label = "Creer un personnage"
    bl_description = ("Cree l'arborescence du personnage et son fichier .blend, "
                      "ouvert dans une seconde instance de Blender")

    name: bpy.props.StringProperty(name="Nom", default="robot_01")
    family: bpy.props.EnumProperty(name="Famille", items=FAMILIES, default='ROBOT')
    open_blender: bpy.props.BoolProperty(
        name="Ouvrir le fichier", default=True,
        description="Ouvre le nouveau .blend dans une seconde instance, "
                    "sans fermer le fichier courant")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        root = root_path()
        if not root:
            self.report({'ERROR'}, "Racine non definie (preferences de l'addon)")
            return {'CANCELLED'}

        name = safe_name(self.name)
        if not name:
            self.report({'ERROR'}, "Nom invalide")
            return {'CANCELLED'}

        folder = os.path.join(root, CREATIONS, name)
        blend_path = os.path.join(folder, name + ".blend")

        if os.path.isfile(blend_path):
            self.report({'ERROR'}, "{}.blend existe deja".format(name))
            return {'CANCELLED'}

        try:
            for sub in CHARACTER_DIRS:
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except Exception as e:
            self.report({'ERROR'}, "Creation impossible : {}".format(e))
            return {'CANCELLED'}
        
        # Fiche du personnage : relue par Robot Maker a l'ouverture du .blend
        try:
            with open(os.path.join(folder, CHARACTER_FILE), "w", encoding="utf-8") as f:
                json.dump({"name": name, "family": self.family}, f, indent=1)
        except Exception as e:
            self.report({'WARNING'}, "Fiche non ecrite : {}".format(e))

        # La seconde instance part d'un fichier vide, l'enregistre au bon
        # endroit et reste ouverte dessus : la session courante n'est pas touchee
        code = "\n".join([
            "import bpy",
            "bpy.ops.wm.read_homefile(use_empty=True)",
            # Un fichier vide n'a pas de World : le rendu serait noir
            "world = bpy.data.worlds.new('World')",
            "world.use_nodes = True",
            "bg = world.node_tree.nodes.get('Background')",
            "if bg is not None:",
            "    bg.inputs[0].default_value = (0.0513, 0.0513, 0.0545, 1.0)",
            "    bg.inputs[1].default_value = 1.0",
            "bpy.context.scene.world = world",
            # Lampe surface au-dessus de la zone ou nait le squelette
            # (robot d'environ 1,80 m, centre sur l'origine)
            "light = bpy.data.lights.new('Key', type='AREA')",
            "light.shape = 'RECTANGLE'",
            "light.size = 3.0",
            "light.size_y = 2.0",
            "light.energy = 400.0",
            "key = bpy.data.objects.new('Key', light)",
            "bpy.context.scene.collection.objects.link(key)",
            "key.location = (0.0, -1.5, 3.6)",
            "key.rotation_euler = (0.35, 0.0, 0.0)",
            "bpy.ops.wm.save_as_mainfile(filepath={})".format(repr(blend_path)),
            "try:",
            "    import addon_utils",
            "    addon_utils.enable('robot_maker', default_set=True)",
            "    bpy.context.scene.rm_family = {}".format(repr(self.family)),
            "except Exception:",
            "    pass",
            SIDEBAR_CODE,
        ])

        args = [bpy.app.binary_path]
        if not self.open_blender:
            args.append("--background")
        args += ["--python-expr", code]

        try:
            subprocess.Popen(args, cwd=folder)
        except Exception as e:
            self.report({'ERROR'}, "Lancement de Blender impossible : {}".format(e))
            return {'CANCELLED'}

        scan_all(context)
        context.scene.rbm_robot = name

        self.report({'INFO'}, "Personnage '{}' cree dans {}".format(name, folder))
        return {'FINISHED'}

SIDEBAR_CODE = "\n".join([
    "import bpy",
    "for area in bpy.context.screen.areas:",
    "    if area.type == 'VIEW_3D':",
    "        area.spaces.active.show_region_ui = True",
])


class RBM_OT_duplicate_character(bpy.types.Operator):
    bl_idname = "rbm.duplicate_character"
    bl_label = "Dupliquer le personnage"
    bl_description = ("Copie le dossier du personnage sous un nouveau nom et ouvre "
                      "son fichier dans une seconde instance de Blender")

    source: bpy.props.StringProperty()
    name: bpy.props.StringProperty(name="Nouveau nom", default="")
    with_rig: bpy.props.BoolProperty(
        name="Copier le rig", default=True,
        description="prototype, mixamo-rigged et ready.blend")
    with_expr: bpy.props.BoolProperty(
        name="Copier les expressions", default=True)
    with_poses: bpy.props.BoolProperty(
        name="Copier les postures", default=False)

    def invoke(self, context, event):
        if not self.name:
            self.name = self.source + "_2"
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        import shutil

        root = root_path()
        if not root:
            self.report({'ERROR'}, "Racine non definie (preferences de l'addon)")
            return {'CANCELLED'}

        src_name = self.source
        new_name = safe_name(self.name)
        if not new_name or new_name == src_name:
            self.report({'ERROR'}, "Nom invalide")
            return {'CANCELLED'}

        src = robot_dir(src_name)
        dst = os.path.join(root, CREATIONS, new_name)

        if not os.path.isdir(src):
            self.report({'ERROR'}, "Personnage source introuvable")
            return {'CANCELLED'}
        if os.path.exists(dst):
            self.report({'ERROR'}, "'{}' existe deja".format(new_name))
            return {'CANCELLED'}

        skip = set()
        if not self.with_rig:
            skip |= {"prototype", D_RIGGED}
        if not self.with_expr:
            skip |= {"expressions"}
        if not self.with_poses:
            skip |= {D_POSE}

        try:
            shutil.copytree(src, dst,
                            ignore=lambda d, names: [n for n in names if n in skip])
        except Exception as e:
            self.report({'ERROR'}, "Copie impossible : {}".format(e))
            return {'CANCELLED'}

        # Les fichiers portant l'ancien nom sont renommes
        for fname in os.listdir(dst):
            if fname.startswith(src_name + "."):
                try:
                    os.rename(os.path.join(dst, fname),
                              os.path.join(dst, new_name + fname[len(src_name):]))
                except Exception:
                    pass

        if not self.with_rig:
            for leftover in (READY_FILE,):
                path = os.path.join(dst, leftover)
                if os.path.isfile(path):
                    os.remove(path)

        # Fiche du personnage
        try:
            cfg = os.path.join(dst, CHARACTER_FILE)
            data = {}
            if os.path.isfile(cfg):
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["name"] = new_name
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except Exception:
            pass

        blend = os.path.join(dst, new_name + ".blend")
        scan_all(context)
        context.scene.rbm_robot = new_name

        if not os.path.isfile(blend):
            self.report({'WARNING'}, "Dossier copie, mais pas de .blend a ouvrir")
            return {'FINISHED'}

        # Les collections, objets et materiaux portent encore l'ancien nom :
        # la nouvelle instance les renomme puis enregistre
        code = "\n".join([
            "import bpy",
            "old, new = {}, {}".format(repr(src_name), repr(new_name)),
            "for group in (bpy.data.collections, bpy.data.objects,",
            "              bpy.data.materials, bpy.data.images):",
            "    for item in group:",
            "        if old in item.name and new not in item.name:",
            "            item.name = item.name.replace(old, new)",
            "for obj in bpy.data.objects:",
            "    if obj.get('robot') == old:",
            "        obj['robot'] = new",
            "bpy.ops.wm.save_mainfile()",
            SIDEBAR_CODE,
        ])

        try:
            subprocess.Popen([bpy.app.binary_path, blend, "--python-expr", code],
                             cwd=dst)
        except Exception as e:
            self.report({'ERROR'}, "Lancement impossible : {}".format(e))
            return {'CANCELLED'}

        self.report({'INFO'}, "'{}' duplique en '{}'".format(src_name, new_name))
        return {'FINISHED'}


class RBM_OT_edit_character(bpy.types.Operator):
    bl_idname = "rbm.edit_character"
    bl_label = "Modifier le personnage"
    bl_description = ("Ouvre le .blend du personnage dans une seconde instance de "
                      "Blender, sans fermer le fichier courant")

    name: bpy.props.StringProperty()

    def execute(self, context):
        folder = robot_dir(self.name)
        blend_path = os.path.join(folder, self.name + ".blend") if folder else ""

        if not blend_path or not os.path.isfile(blend_path):
            # Un dossier cree a la main peut ne pas avoir son fichier
            self.report({'ERROR'},
                        "{}.blend introuvable a la racine du dossier".format(self.name))
            return {'CANCELLED'}

        try:
            subprocess.Popen([bpy.app.binary_path, blend_path,
                              "--python-expr", SIDEBAR_CODE], cwd=folder)
        except Exception as e:
            self.report({'ERROR'}, "Lancement de Blender impossible : {}".format(e))
            return {'CANCELLED'}

        context.scene.rbm_robot = self.name
        self.report({'INFO'}, "Ouverture de {}.blend".format(self.name))
        return {'FINISHED'}

class RBM_OT_delete_character(bpy.types.Operator):
    bl_idname = "rbm.delete_character"
    bl_label = "Supprimer le personnage"
    bl_description = ("Supprime definitivement le dossier du personnage : .blend, "
                      "postures, animations, expressions, tout son contenu")

    name: bpy.props.StringProperty()
    confirm: bpy.props.BoolProperty(
        name="Je confirme la suppression definitive", default=False)

    def invoke(self, context, event):
        self.confirm = False
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        folder = robot_dir(self.name)

        col = layout.column(align=True)
        col.alert = True
        col.label(text="Suppression definitive de '{}'".format(self.name),
                  icon='ERROR')

        sub = layout.column(align=True)
        sub.scale_y = 0.8
        sub.label(text=folder)

        # Ce que le dossier contient reellement
        counts = []
        for sub_name in (D_POSE, D_ANIM, D_RIGGED, "expressions", "prototype"):
            path = os.path.join(folder, sub_name)
            if os.path.isdir(path):
                files = [f for f in os.listdir(path)
                         if os.path.isfile(os.path.join(path, f))]
                if files:
                    counts.append("{} : {} fichier(s)".format(sub_name, len(files)))

        for line in counts:
            sub.label(text=line)

        layout.separator()
        layout.prop(self, "confirm")

    def execute(self, context):
        import shutil

        if not self.confirm:
            self.report({'WARNING'}, "Suppression annulee : case non cochee")
            return {'CANCELLED'}

        folder = robot_dir(self.name)
        root = root_path()

        # Garde-fou : ne jamais sortir de creations/
        expected = os.path.normpath(os.path.join(root, CREATIONS))
        if not folder or not os.path.normpath(folder).startswith(expected):
            self.report({'ERROR'}, "Chemin inattendu, suppression refusee")
            return {'CANCELLED'}

        if not os.path.isdir(folder):
            self.report({'ERROR'}, "Dossier introuvable")
            return {'CANCELLED'}

        try:
            shutil.rmtree(folder)
        except Exception as e:
            self.report({'ERROR'}, "Suppression impossible : {} "
                                   "(le .blend est peut-etre ouvert)".format(e))
            return {'CANCELLED'}

        scan_all(context)
        if context.scene.rbm_robot == self.name:
            context.scene.rbm_robot = _robots[0][0] if _robots else ""

        self.report({'INFO'}, "'{}' supprime".format(self.name))
        return {'FINISHED'}

class RBM_OT_select_robot(bpy.types.Operator):
    bl_idname = "rbm.select_robot"
    bl_label = "Choisir ce robot"
    bl_description = "Selectionne ce robot dans le gestionnaire"

    name: bpy.props.StringProperty()

    def execute(self, context):
        context.scene.rbm_robot = self.name
        return {'FINISHED'}


def tag_asset_datablocks(coll):
    """Marque les materiaux et images utilises par les objets de coll comme
    provenant de l'addon, pour que merge_duplicates() ne les fusionne
    qu'avec d'autres datablocks du meme type (et jamais avec un datablock
    etranger au nom generique identique par coincidence, ex: "Image_0")."""
    for obj in coll.objects:
        for mat in (getattr(obj.data, "materials", None) or []):
            if mat is None:
                continue
            mat[K_ASSET] = True
            if not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image is not None:
                    node.image[K_ASSET] = True


def merge_duplicates():
    """Remappe les datablocks .001 sur leur original : un append recree
    systematiquement ceux qui existent deja dans le fichier.

    Restreint aux datablocks marques K_ASSET (poses par tag_asset_datablocks
    juste apres l'import) des deux cotes : on ne fusionne un doublon que
    s'il remplace bien un asset importe par l'addon lors d'une instanciation
    precedente, jamais un datablock etranger qui porterait le meme nom par
    coincidence (ex: le "Image_0" par defaut de Blender)."""
    merged = 0

    for data in (bpy.data.materials, bpy.data.images):
        for item in list(data):
            if not item.get(K_ASSET):
                continue

            match = re.match(r"^(.*)\.\d{3}$", item.name)
            if not match:
                continue

            base = data.get(match.group(1))
            if base is None or base is item or not base.get(K_ASSET):
                continue

            try:
                item.user_remap(base)
                data.remove(item)
                merged += 1
            except Exception:
                pass

    return merged


def rename_to_robot(coll, robot):
    """Aligne les noms internes sur le personnage courant : un ready.blend
    duplique porte encore ceux de sa source."""
    old = ""
    for obj in coll.objects:
        for mat in (getattr(obj.data, "materials", None) or []):
            if mat is not None and mat.name.startswith("FACE_"):
                parts = mat.name.split("_", 2)
                if len(parts) == 3 and parts[2] != robot:
                    old = parts[2]
                    break
        if old:
            break

    if not old:
        return 0

    # Chaque datablock une seule fois : un materiau partage serait renomme
    # autant de fois qu'il y a d'objets, et le nouveau nom contient l'ancien
    targets = [coll]
    seen = set()
    for obj in coll.objects:
        targets.append(obj)
        for mat in (getattr(obj.data, "materials", None) or []):
            if mat is not None:
                targets.append(mat)

    count = 0
    for item in targets:
        key = id(item)
        if key in seen:
            continue
        seen.add(key)

        if robot in item.name or old not in item.name:
            continue
        item.name = item.name.replace(old, robot)
        count += 1

    for obj in coll.objects:
        obj[K_ROBOT] = robot

    # Les textures peuvent pointer le dossier de la source
    folder = os.path.join(robot_dir(robot), "expressions")
    for obj in coll.objects:
        for mat in (getattr(obj.data, "materials", None) or []):
            if mat is None or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type != 'TEX_IMAGE' or node.image is None:
                    continue
                local = os.path.join(folder, os.path.basename(
                    bpy.path.abspath(node.image.filepath)))
                if os.path.isfile(local):
                    node.image = bpy.data.images.load(local, check_existing=True)
                    node.image.reload()

    return count


class RBM_OT_instantiate(bpy.types.Operator):
    bl_idname = "rbm.instantiate"
    bl_label = "Instancier dans la scene"
    bl_description = ("Importe le robot rigge (dossier mixamo-rigged) dans une nouvelle "
                      "collection de la scene")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot

        if not robot:
            self.report({'ERROR'}, "Aucun robot selectionne")
            return {'CANCELLED'}

        # Fichier prepare : visage, materiaux et control rig inclus
        ready = os.path.join(robot_dir(robot), READY_FILE)
        if os.path.isfile(ready):
            try:
                with bpy.data.libraries.load(ready, link=False) as (src, dst):
                    dst.collections = [c for c in src.collections if is_char_coll(c)]
            except Exception as e:
                self.report({'ERROR'}, "Import impossible : {}".format(e))
                return {'CANCELLED'}

            source = next((c for c in dst.collections if c is not None), None)
            if source is not None:
                index = 1
                while bpy.data.collections.get(
                        "{}{}_{:02d}".format(COLL_PREFIX, robot, index)):
                    index += 1
                source.name = "{}{}_{:02d}".format(COLL_PREFIX, robot, index)
                scene.collection.children.link(source)

                rig = next((o for o in source.objects if is_control_rig(o)), None)
                if rig is not None:
                    for o in list(context.selected_objects):
                        o.select_set(False)
                    rig.select_set(True)
                    context.view_layer.objects.active = rig

                rename_to_robot(source, robot)
                tag_asset_datablocks(source)
                merge_duplicates()
                self.report({'INFO'}, "{} instancie depuis {}".format(robot, READY_FILE))
                return {'FINISHED'}

        folder = sub_dir(robot, D_RIGGED)
        if not folder or not os.path.isdir(folder):
            self.report({'ERROR'}, "Dossier {} introuvable".format(D_RIGGED))
            return {'CANCELLED'}

        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".fbx"))
        if not files:
            self.report({'ERROR'}, "Aucun FBX dans {}".format(D_RIGGED))
            return {'CANCELLED'}

        before = set(bpy.data.objects)

        try:
            bpy.ops.import_scene.fbx(filepath=os.path.join(folder, files[0]),
                                     automatic_bone_orientation=False)
        except Exception as e:
            self.report({'ERROR'}, "Import impossible : {}".format(e))
            return {'CANCELLED'}

        imported = [o for o in bpy.data.objects if o not in before]
        if not imported:
            self.report({'ERROR'}, "Rien n'a ete importe")
            return {'CANCELLED'}

        # Collection dediee, numerotee pour permettre plusieurs occurrences
        index = 1
        while bpy.data.collections.get("{}{}_{:02d}".format(COLL_PREFIX, robot, index)):
            index += 1
        coll = bpy.data.collections.new("{}{}_{:02d}".format(COLL_PREFIX, robot, index))
        scene.collection.children.link(coll)

        for obj in imported:
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            coll.objects.link(obj)
            obj[K_ROBOT] = robot

        armature = next((o for o in imported if o.type == 'ARMATURE'), None)
        if armature is None:
            self.report({'WARNING'}, "{} importe, mais aucune armature trouvee".format(files[0]))
            return {'FINISHED'}

        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        context.view_layer.objects.active = armature

        tag_asset_datablocks(coll)
        merge_duplicates()
        msg = "{} importe dans {}".format(files[0], coll.name)

        if scene.rbm_auto_rig:
            # Sans source d'animation, make_rig ne tente pas de retarget :
            # c'est ce retarget a vide qui fait planter le plugin
            previous_source = getattr(scene, "mix_source_armature", None)
            scene.mix_source_armature = None

            # Les rigs deja en scene ne doivent pas bouger : make_rig agit
            # parfois au-dela de l'armature ciblee
            snapshots = [(o, pose_to_dict(o)) for o in context.scene.objects
                         if is_control_rig(o) and o is not armature]

            known = set(context.scene.objects)
            rig_error = ""

            try:
                bpy.ops.mr.make_rig()
            except Exception as e:
                # Le plugin peut lever une erreur apres avoir cree le rig :
                # on se fie a la presence du rig, pas au succes de l'operateur
                rig_error = str(e)

            if previous_source is not None:
                scene.mix_source_armature = previous_source

            for other, snapshot in snapshots:
                try:
                    dict_to_pose(other, snapshot)
                except Exception:
                    pass

            # Le plugin transforme l'armature importee en control rig au lieu
            # d'en creer une nouvelle : on la teste en premier
            if is_control_rig(armature):
                rig = armature
            else:
                rig = next((o for o in context.scene.objects
                            if is_control_rig(o) and o not in known), None)

            if rig is None:
                msg += " - control rig non cree"
                if rig_error:
                    msg += " ({})".format(rig_error)
            else:
                for c in list(rig.users_collection):
                    c.objects.unlink(rig)
                coll.objects.link(rig)
                rig[K_ROBOT] = robot
                msg += " - control rig cree"

                # Premiere instanciation : on fige la pose de repos, point de
                # retour entre deux essais de posture
                if scene.rbm_auto_rest and not _postures.get(robot):
                    try:
                        ok, info = save_posture(context, robot, rig, REST_POSTURE, False)
                    except Exception as e:
                        info = "posture de repos : {}".format(e)
                    msg += " - " + info

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RBM_OT_robot_thumb(bpy.types.Operator):
    bl_idname = "rbm.robot_thumb"
    bl_label = "Vignette du robot"
    bl_description = "Rend une vignette du robot a partir de la selection courante"

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot

        folder = robot_dir(robot)
        if not folder or not os.path.isdir(folder):
            self.report({'ERROR'}, "Dossier du robot introuvable")
            return {'CANCELLED'}

        rig = find_control_rig(context)
        if rig:
            meshes = rig_meshes(rig)
        else:
            arm = scene_armature(context, robot)
            meshes = ([o for o in arm.children_recursive if o.type == 'MESH']
                      if arm else [])
            meshes = meshes or [o for o in context.selected_objects
                                if o.type == 'MESH']
        if not meshes:
            self.report({'ERROR'}, "Selectionner le robot (ou son control rig)")
            return {'CANCELLED'}

        ok, err = render_thumbnail(context, meshes,
                                   os.path.join(folder, "preview.png"),
                                   scene.rbm_thumb_size)
        scan_all(context)

        if not ok:
            self.report({'ERROR'}, "Vignette : {}".format(err))
            return {'CANCELLED'}

        self.report({'INFO'}, "Vignette enregistree")
        return {'FINISHED'}


class RBM_OT_save_posture(bpy.types.Operator):
    bl_idname = "rbm.save_posture"
    bl_label = "Enregistrer la posture"
    bl_description = ("Enregistre la pose actuelle du control rig comme posture "
                      "reutilisable, avec sa vignette")
    clear_anim: bpy.props.BoolProperty(
        name="Supprimer l'animation", default=False,
        description="Retire l'action du control rig et l'armature source : la pose "
                    "reste figee sur celle qu'on vient d'enregistrer")

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot

        if not robot:
            self.report({'ERROR'}, "Aucun robot selectionne")
            return {'CANCELLED'}

        rig = find_control_rig(context)
        if rig is None:
            self.report({'ERROR'}, "Aucun control rig trouve "
                                   "(le creer avec l'addon Mixamo Control Rig)")
            return {'CANCELLED'}

        ok, msg = save_posture(context, scene.rbm_robot, rig,
                               scene.rbm_posture_name, scene.rbm_overwrite)

        if ok and self.clear_anim:
            # L'action retiree, la pose actuelle devient la pose statique du rig
            if rig.animation_data is not None:
                rig.animation_data.action = None
                rig.animation_data_clear()

            source = getattr(scene, "mix_source_armature", None)
            if source is not None:
                for child in list(source.children):
                    bpy.data.objects.remove(child)
                bpy.data.objects.remove(source)
                scene.mix_source_armature = None
                msg += " - animation retiree"
            else:
                msg += " - action retiree"

        self.report({'INFO'} if ok else {'ERROR'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


class RBM_OT_apply_posture(bpy.types.Operator):
    bl_idname = "rbm.apply_posture"
    bl_label = "Appliquer la posture"
    bl_description = "Applique cette posture au control rig"
    bl_options = {'REGISTER', 'UNDO'}

    posture: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot

        rig = find_control_rig(context) or scene_armature(context, robot)
        if rig is None:
            self.report({'ERROR'}, "Aucune armature trouvee")
            return {'CANCELLED'}

        path = next((p for n, p in _postures.get(robot, []) if n == self.posture), None)
        if path is None or not os.path.isfile(path):
            self.report({'ERROR'}, "Posture introuvable : relire les dossiers")
            return {'CANCELLED'}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, "Lecture impossible : {}".format(e))
            return {'CANCELLED'}

        if "bones" in data:
            applied, missing = 0, 0
            for bone_name, vals in data["bones"].items():
                pb = rig.pose.bones.get(bone_name)
                if pb is None:
                    missing += 1
                    continue
                pb.rotation_mode = vals.get("rotation_mode", pb.rotation_mode)
                pb.location = vals["location"]
                pb.rotation_quaternion = vals["rotation_quaternion"]
                pb.rotation_euler = vals["rotation_euler"]
                pb.scale = vals["scale"]
                applied += 1
        else:
            applied, missing = dict_to_pose(rig, data)

        msg = "'{}' appliquee ({} os)".format(self.posture, applied)
        if missing:
            msg += " - {} os absents de ce rig".format(missing)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RBM_OT_delete_posture(bpy.types.Operator):
    bl_idname = "rbm.delete_posture"
    bl_label = "Supprimer la posture"
    bl_description = "Supprime definitivement cette posture et sa vignette"

    posture: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        robot = context.scene.rbm_robot
        path = next((p for n, p in _postures.get(robot, []) if n == self.posture), None)

        if path is None:
            self.report({'ERROR'}, "Posture introuvable")
            return {'CANCELLED'}

        for target in (path, os.path.splitext(path)[0] + ".png"):
            if os.path.isfile(target):
                try:
                    os.remove(target)
                except Exception as e:
                    self.report({'ERROR'}, "Suppression impossible : {}".format(e))
                    return {'CANCELLED'}

        scan_all(context)
        self.report({'INFO'}, "'{}' supprimee".format(self.posture))
        return {'FINISHED'}


class RBM_OT_insert_keyframe(bpy.types.Operator):
    bl_idname = "rbm.insert_keyframe"
    bl_label = "Keyframer la posture"
    bl_description = ("Pose une keyframe (loc/rot/scale, tous les os) sur le control "
                      "rig a la frame courante - independant du systeme de postures, "
                      "sans passer par le mode Pose")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rig = find_control_rig(context) or scene_armature(context)
        if rig is None:
            self.report({'ERROR'}, "Aucune armature trouvee")
            return {'CANCELLED'}

        # Les animations livrees avec l'asset ne doivent pas recevoir les
        # keyframes de mise en scene : on bascule sur une action dediee
        switched = ""
        assets = set(a.name for a in embedded_actions(context))
        if assets:
            if rig.animation_data is None:
                rig.animation_data_create()

            current = rig.animation_data.action
            if current is None or current.name in assets:
                own_name = "POSE_" + rig.name
                own = bpy.data.actions.get(own_name)
                if own is None:
                    own = bpy.data.actions.new(own_name)
                    own.use_fake_user = True
                rig.animation_data.action = own
                if current is not None:
                    switched = " (animation '{}' mise de cote)".format(current.name)

        frame = context.scene.frame_current
        count = keyframe_pose(rig, frame)

        if not count:
            self.report({'WARNING'}, "Aucun os sur ce rig")
            return {'CANCELLED'}

        # La timeline / dope sheet n'affichent pas toujours la nouvelle
        # keyframe sans un rafraichissement explicite
        for area in context.screen.areas:
            if area.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'TIMELINE', 'VIEW_3D'}:
                area.tag_redraw()

        self.report({'INFO'}, "Keyframe posee sur {} os a la frame {}{}".format(
            count, frame, switched))
        return {'FINISHED'}

class RBM_Clip(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Nom", default="clip")
    start: bpy.props.IntProperty(name="Debut", default=0, min=0)
    end: bpy.props.IntProperty(name="Fin", default=0, min=0)


class RBM_OT_clip_add(bpy.types.Operator):
    bl_idname = "rbm.clip_add"
    bl_label = "Ajouter le clip"
    bl_description = ("Ajoute un clip aux bornes actuelles de la timeline "
                      "(Start / End de la scene)")

    def execute(self, context):
        scene = context.scene
        clip = scene.rbm_clips.add()
        clip.name = "clip_{:02d}".format(len(scene.rbm_clips))
        clip.start = scene.frame_start
        clip.end = scene.frame_end
        scene.rbm_clip_index = len(scene.rbm_clips) - 1
        return {'FINISHED'}


class RBM_OT_clip_remove(bpy.types.Operator):
    bl_idname = "rbm.clip_remove"
    bl_label = "Retirer le clip"

    index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if 0 <= self.index < len(scene.rbm_clips):
            scene.rbm_clips.remove(self.index)
            scene.rbm_clip_index = max(0, self.index - 1)
        return {'FINISHED'}


class RBM_OT_clip_preview(bpy.types.Operator):
    bl_idname = "rbm.clip_preview"
    bl_label = "Voir le clip"
    bl_description = "Cale la timeline sur les bornes de ce clip"

    index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if not (0 <= self.index < len(scene.rbm_clips)):
            return {'CANCELLED'}

        clip = scene.rbm_clips[self.index]
        scene.frame_start = clip.start
        scene.frame_end = clip.end
        scene.frame_current = clip.start
        return {'FINISHED'}


class RBM_OT_split_action(bpy.types.Operator):
    bl_idname = "rbm.split_action"
    bl_label = "Decouper en actions"
    bl_description = ("Cree une action par clip a partir de l'action concatenee. "
                      "Chaque clip repart a la frame 0")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        src = bpy.data.actions.get(scene.rbm_source_action)

        if src is None:
            self.report({'ERROR'}, "Action source introuvable")
            return {'CANCELLED'}
        if not scene.rbm_clips:
            self.report({'ERROR'}, "Aucun clip defini")
            return {'CANCELLED'}

        made = []
        for clip in scene.rbm_clips:
            name = safe_name(clip.name) or "clip"
            if clip.end <= clip.start:
                self.report({'WARNING'}, "'{}' ignore : bornes invalides".format(name))
                continue
            if name in bpy.data.actions:
                self.report({'WARNING'}, "'{}' existe deja, ignore".format(name))
                continue

            act = bpy.data.actions.new(name)
            act.use_fake_user = True

            for fc in src.fcurves:
                nfc = act.fcurves.new(
                    fc.data_path, index=fc.array_index,
                    action_group=fc.group.name if fc.group else "")
                pts = [kp for kp in fc.keyframe_points
                       if clip.start <= kp.co.x <= clip.end]
                nfc.keyframe_points.add(len(pts))
                for i, kp in enumerate(pts):
                    n = nfc.keyframe_points[i]
                    n.co = (kp.co.x - clip.start, kp.co.y)
                    n.interpolation = kp.interpolation
                nfc.update()

            made.append(name)

        if not made:
            self.report({'ERROR'}, "Aucune action creee")
            return {'CANCELLED'}

        self.report({'INFO'}, "{} action(s) creee(s) : {}".format(
            len(made), ", ".join(made)))
        return {'FINISHED'}


class RBM_OT_drop_source_action(bpy.types.Operator):
    bl_idname = "rbm.drop_source_action"
    bl_label = "Supprimer l'action source"
    bl_description = ("Supprime l'action concatenee et mute les pistes NLA, "
                      "une fois le decoupage verifie")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        arm = scene_armature(context)
        src = bpy.data.actions.get(scene.rbm_source_action)

        if arm is not None and arm.animation_data:
            for track in arm.animation_data.nla_tracks:
                track.mute = True
            if arm.animation_data.action is src:
                arm.animation_data.action = None

        if src is not None:
            bpy.data.actions.remove(src)

        scene.rbm_clips.clear()
        self.report({'INFO'}, "Action source supprimee")
        return {'FINISHED'}
    
class RBM_OT_load_animation(bpy.types.Operator):
    bl_idname = "rbm.load_animation"
    bl_label = "Charger l'animation"
    bl_description = ("Importe l'animation choisie et l'applique au control rig via "
                      "l'addon Mixamo Control Rig")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot

        rig = find_control_rig(context)
        if rig is None:
            self.report({'ERROR'}, "Aucun control rig trouve")
            return {'CANCELLED'}

        path = next((p for ident, label, p in _anims.get(robot, [])
                     if ident == scene.rbm_anim), None)
        if path is None or not os.path.isfile(path):
            self.report({'ERROR'}, "Animation introuvable")
            return {'CANCELLED'}

        before = set(bpy.data.objects)
        try:
            bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=False)
        except Exception as e:
            self.report({'ERROR'}, "Import impossible : {}".format(e))
            return {'CANCELLED'}

        imported = [o for o in bpy.data.objects if o not in before]
        source = next((o for o in imported if o.type == 'ARMATURE'), None)
        if source is None:
            self.report({'ERROR'}, "Aucune armature dans ce FBX")
            return {'CANCELLED'}

        source.name = "SRC_" + scene.rbm_anim

        if source == rig:
            self.report({'ERROR'}, "L'armature source est le control rig lui-meme")
            return {'CANCELLED'}

        # Sans action, le plugin importe une animation vide puis plante sur
        # action_suitable_slots[0]
        action = getattr(source.animation_data, "action", None)
        if action is None:
            for obj in imported:
                bpy.data.objects.remove(obj)
            self.report({'ERROR'},
                        "Ce FBX ne contient aucune animation : telecharger depuis Mixamo "
                        "une animation (pas le personnage en pose T)")
            return {'CANCELLED'}

        scene.mix_source_armature = source

        # Le control rig doit etre l'objet actif pour l'operateur du plugin
        bpy.ops.object.select_all(action='DESELECT')
        rig.select_set(True)
        context.view_layer.objects.active = rig

        error = ""
        try:
            bpy.ops.mr.import_anim_to_rig()
        except Exception as e:
            error = str(e)

        # Le plugin peut lever une erreur sur la gestion des slots (Blender 4.4+)
        # apres avoir tout de meme applique l'animation : on se fie au resultat
        applied = getattr(rig.animation_data, "action", None) is not None

        if applied:
            self.report({'INFO'}, "Animation '{}' appliquee".format(scene.rbm_anim))
        elif error:
            self.report({'WARNING'},
                        "Source chargee, application echouee : {} "
                        "(reessayer depuis le panneau Mixamo)".format(error))
        else:
            self.report({'WARNING'}, "Source chargee, aucune action sur le rig")
        return {'FINISHED'}

class RBM_OT_open_shared_anims(bpy.types.Operator):
    bl_idname = "rbm.open_shared_anims"
    bl_label = "Dossier des animations communes"
    bl_description = ("Ouvre la bibliotheque d'animations partagee par tous les robots. "
                      "Telecharger depuis Mixamo en 'Without Skin'")

    def execute(self, context):
        root = root_path()
        if not root:
            self.report({'ERROR'}, "Racine non definie")
            return {'CANCELLED'}

        path = os.path.join(root, D_ANIM)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            self.report({'ERROR'}, "Creation impossible : {}".format(e))
            return {'CANCELLED'}

        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}

class RBM_OT_preview_gif(bpy.types.Operator):
    bl_idname = "rbm.preview_gif"
    bl_label = "Voir l'animation"
    bl_description = ("Ouvre le GIF de l'animation dans la visionneuse du systeme. "
                      "Le fichier doit porter le meme nom que le FBX")

    def execute(self, context):
        scene = context.scene
        path = next((p for ident, label, p in _anims.get(scene.rbm_robot, [])
                     if ident == scene.rbm_anim), None)

        if path is None:
            self.report({'ERROR'}, "Aucune animation selectionnee")
            return {'CANCELLED'}

        gif = os.path.splitext(path)[0] + ".gif"
        if not os.path.isfile(gif):
            self.report({'WARNING'}, "Pas de GIF a cote de {}".format(
                os.path.basename(path)))
            return {'CANCELLED'}

        bpy.ops.wm.path_open(filepath=gif)
        return {'FINISHED'}

class RBM_OT_clear_source(bpy.types.Operator):
    bl_idname = "rbm.clear_source"
    bl_label = "Retirer l'armature source"
    bl_description = "Supprime l'armature d'animation importee, une fois la posture prise"

    def execute(self, context):
        scene = context.scene
        source = getattr(scene, "mix_source_armature", None)

        if source is None:
            self.report({'WARNING'}, "Aucune armature source")
            return {'CANCELLED'}

        name = source.name
        for child in list(source.children):
            bpy.data.objects.remove(child)
        bpy.data.objects.remove(source)
        scene.mix_source_armature = None

        self.report({'INFO'}, "{} supprimee".format(name))
        return {'FINISHED'}

class RBM_OT_page(bpy.types.Operator):
    bl_idname = "rbm.page"
    bl_label = "Page"
    bl_description = "Page suivante ou precedente de la grille"

    delta: bpy.props.IntProperty(default=1)

    def execute(self, context):
        context.scene.rbm_page = max(0, context.scene.rbm_page + self.delta)
        return {'FINISHED'}
    
def scene_armature(context, robot=None):
    """Armature du personnage instancie, hors control rig."""
    obj = context.view_layer.objects.active
    robot = robot or robot_of(obj) or context.scene.rbm_robot
    if not robot:
        return None

    colls = [c for c in bpy.data.collections
             if is_char_coll(c)
             and re.sub(r"_\d+$", "", coll_name(c)) == robot]
    for coll in colls:
        for o in coll.all_objects:
            if o.type == 'ARMATURE' and not o.name.startswith("SRC_"):
                return o
    return None


def embedded_actions(context, robot=None):
    """Actions presentes dans le fichier et compatibles avec l'armature."""
    arm = scene_armature(context, robot)
    if arm is None:
        return []

    bones = set(b.name for b in arm.pose.bones)
    out = []
    for act in bpy.data.actions:
        if act.name.startswith("POSE_"):
            continue
        used = set()
        for fc in act.fcurves:
            if fc.data_path.startswith('pose.bones["'):
                used.add(fc.data_path.split('"')[1])
        if used and used & bones:
            out.append(act)
    return sorted(out, key=lambda a: a.name.lower())


def action_enum(self, context):
    items = [(a.name, a.name, "") for a in embedded_actions(context)]
    return items or [('NONE', "(aucune)", "")]


class RBM_OT_load_embedded_anim(bpy.types.Operator):
    bl_idname = "rbm.load_embedded_anim"
    bl_label = "Charger l'animation"
    bl_description = ("Assigne l'action a l'armature et cale les bornes de la "
                      "scene sur sa duree")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        arm = scene_armature(context)
        act = bpy.data.actions.get(scene.rbm_embedded_anim)

        if arm is None or act is None:
            self.report({'ERROR'}, "Armature ou action introuvable")
            return {'CANCELLED'}

        if arm.animation_data is None:
            arm.animation_data_create()

        # Les pistes NLA importees se cumulent a l'action active
        for track in arm.animation_data.nla_tracks:
            track.mute = True

        arm.animation_data.action = act

        # Les frames issues du glTF sont souvent non entieres
        start, end = act.frame_range
        scene.frame_start = int(round(start))
        scene.frame_end = int(round(end))
        scene.frame_current = scene.frame_start

        self.report({'INFO'}, "'{}' chargee ({} a {})".format(
            act.name, scene.frame_start, scene.frame_end))
        return {'FINISHED'}


class RBM_OT_save_pose_bones(bpy.types.Operator):
    bl_idname = "rbm.save_pose_bones"
    bl_label = "Enregistrer la posture"
    bl_description = ("Capture la pose de tous les os de l'armature, sans "
                      "passer par un control rig")

    def execute(self, context):
        scene = context.scene
        robot = scene.rbm_robot
        arm = scene_armature(context, robot)
        name = safe_name(scene.rbm_posture_name) or "posture"

        if arm is None:
            self.report({'ERROR'}, "Aucune armature pour ce personnage")
            return {'CANCELLED'}

        data = {}
        for pb in arm.pose.bones:
            data[pb.name] = {
                "location": list(pb.location),
                "rotation_mode": pb.rotation_mode,
                "rotation_quaternion": list(pb.rotation_quaternion),
                "rotation_euler": list(pb.rotation_euler),
                "scale": list(pb.scale),
            }

        folder = sub_dir(robot, D_POSE, create=True)
        if not folder:
            self.report({'ERROR'}, "Dossier des postures introuvable")
            return {'CANCELLED'}

        path = os.path.join(folder, name + ".json")
        if os.path.isfile(path) and not scene.rbm_overwrite:
            self.report({'ERROR'}, "'{}' existe deja (cocher Ecraser)".format(name))
            return {'CANCELLED'}

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": name, "bones": data}, f, indent=1)
        except Exception as e:
            self.report({'ERROR'}, "Ecriture impossible : {}".format(e))
            return {'CANCELLED'}

        meshes = [o for o in arm.children_recursive if o.type == 'MESH']
        ok, err = render_thumbnail(context, meshes or [arm],
                                   os.path.join(folder, name + ".png"),
                                   scene.rbm_thumb_size)

        if scene.rbm_clear_anim and arm.animation_data:
            arm.animation_data.action = None

        scan_all(context)
        msg = "posture '{}' enregistree".format(name)
        if not ok:
            msg += " (vignette : {})".format(err)
        self.report({'INFO'}, msg)
        return {'FINISHED'}
    
class RBM_OT_set_family(bpy.types.Operator):
    bl_idname = "rbm.set_family"
    bl_label = "Changer la famille"
    bl_description = "Corrige la famille du personnage dans character.json"

    name: bpy.props.StringProperty()
    family: bpy.props.EnumProperty(name="Famille", items=FAMILIES, default='ROBOT')

    def invoke(self, context, event):
        self.family = _families.get(self.name, 'ROBOT')
        return context.window_manager.invoke_props_dialog(self, width=280)

    def execute(self, context):
        folder = robot_dir(self.name)
        cfg = os.path.join(folder, CHARACTER_FILE) if folder else ""

        if not cfg:
            self.report({'ERROR'}, "Dossier introuvable")
            return {'CANCELLED'}

        data = {}
        if os.path.isfile(cfg):
            try:
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        data["name"] = data.get("name", self.name)
        data["family"] = self.family

        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except Exception as e:
            self.report({'ERROR'}, "Ecriture impossible : {}".format(e))
            return {'CANCELLED'}

        scan_all(context)
        self.report({'INFO'}, "'{}' classe en {}".format(self.name, self.family))
        return {'FINISHED'}
    
# ---------------------------------------------------------------------------
# Panneau
# ---------------------------------------------------------------------------
class RBM_PT_panel(bpy.types.Panel):
    bl_label = "Character Manager"
    bl_idname = "RBM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BD"
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        robot = scene.rbm_robot

        # --- Robots ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Robots", icon='OUTLINER_OB_ARMATURE')
        row.operator("rbm.new_character", text="", icon='ADD')
        row.prop(scene, "rbm_edit", text="", icon='GREASEPENCIL', toggle=True)
        row.operator("rbm.robot_thumb", text="", icon='RESTRICT_RENDER_OFF')
        row.operator("rbm.scan", text="", icon='FILE_REFRESH')

        if scene.rbm_edit:
            sub = box.row()
            sub.scale_y = 0.7
            sub.alert = True
            sub.label(text="Mode gestion : famille et suppression", icon='INFO')

        if not root_path():
            box.label(text="Racine non definie (preferences)", icon='ERROR')
            return

        if not _robots:
            box.label(text="Aucun robot - relire les dossiers", icon='INFO')
        else:
            filt = box.row(align=True)
            filt.prop(scene, "rbm_family_filter", text="")
            filt.prop(scene, "rbm_search", text="", icon='VIEWZOOM')

            fam = scene.rbm_family_filter
            needle = scene.rbm_search.lower()
            shown = [(n, f) for n, f in _robots
                     if (fam == 'ALL' or _families.get(n, 'ROBOT') == fam)
                     and (not needle or needle in n.lower())]

            pages = max(1, (len(shown) + PER_PAGE - 1) // PER_PAGE)
            page = min(scene.rbm_page, pages - 1)
            shown = shown[page * PER_PAGE:(page + 1) * PER_PAGE]

            grid = box.grid_flow(row_major=True, columns=scene.rbm_columns,
                                 even_columns=True)
            for name, folder in shown:
                cell = grid.box()
                icon = icon_of("robot/" + name)
                if icon:
                    cell.template_icon(icon_value=icon, scale=scene.rbm_scale)
                line = cell.row(align=True)
                line.operator("rbm.select_robot", text=name,
                              depress=(name == robot)).name = name
                line.operator("rbm.edit_character", text="",
                              icon='GREASEPENCIL').name = name
                line.operator("rbm.duplicate_character", text="",
                              icon='DUPLICATE').source = name
                if scene.rbm_edit:
                    line.operator("rbm.set_family", text="",
                                  icon='OUTLINER_OB_GROUP_INSTANCE').name = name
                    line.operator("rbm.delete_character", text="",
                                  icon='TRASH').name = name
                label = FAMILY_LABEL.get(_families.get(name, 'ROBOT'), "")
                if label:
                    tag = cell.row()
                    tag.scale_y = 0.6
                    tag.label(text=label)

            if pages > 1:
                nav = box.row(align=True)
                prev = nav.row(align=True)
                prev.enabled = page > 0
                prev.operator("rbm.page", text="", icon='TRIA_LEFT').delta = -1
                nav.label(text="{} / {}".format(page + 1, pages))
                nxt = nav.row(align=True)
                nxt.enabled = page < pages - 1
                nxt.operator("rbm.page", text="", icon='TRIA_RIGHT').delta = 1

            r = box.row(align=True)
            r.prop(scene, "rbm_columns", text="Colonnes")
            r.prop(scene, "rbm_scale", text="Taille")
            box.prop(scene, "rbm_follow")

        if not robot:
            return

        box.prop(scene, "rbm_auto_rig")
        row = box.row()
        row.enabled = scene.rbm_auto_rig
        row.prop(scene, "rbm_auto_rest")
        box.operator("rbm.instantiate", icon='IMPORT')
        if os.path.isfile(os.path.join(robot_dir(robot), READY_FILE)):
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Instanciation depuis ready.blend", icon='CHECKMARK')

        # --- Control rig ---
        rig = find_control_rig(context)
        box = layout.box()
        row = box.row()
        arm = scene_armature(context, robot)
        if rig is not None:
            row.label(text="Rig : " + rig.name, icon='ARMATURE_DATA')
        elif _families.get(robot) == 'ANIMAL' and arm is not None:
            row.label(text="Armature : " + arm.name, icon='ARMATURE_DATA')
        else:
            row.alert = True
            row.label(text="Aucun control rig actif", icon='ERROR')
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Onglet Mixamo > Create Control Rig")

        # --- Postures ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Postures", icon='POSE_HLT')
        row.prop(scene, "rbm_edit", text="", icon='TRASH', toggle=True)

        poses = _postures.get(robot, [])
        if poses:
            grid = box.grid_flow(row_major=True, columns=scene.rbm_columns,
                                 even_columns=True)
            grid.enabled = rig is not None or arm is not None
            for name, path in poses:
                cell = grid.box()
                icon = icon_of("pose/" + robot + "/" + name)
                if icon:
                    cell.template_icon(icon_value=icon, scale=scene.rbm_scale)
                line = cell.row(align=True)
                line.operator("rbm.apply_posture", text=name).posture = name
                if scene.rbm_edit:
                    line.operator("rbm.delete_posture", text="",
                                  icon='TRASH').posture = name
        else:
            box.label(text="Aucune posture enregistree", icon='INFO')

        box.separator()
        arm = scene_armature(context, robot)
        col = box.column(align=True)
        col.enabled = rig is not None or arm is not None
        r = col.row(align=True)
        r.prop(scene, "rbm_posture_name", text="")
        r.prop(scene, "rbm_overwrite")
        col.prop(scene, "rbm_clear_anim")
        col.prop(scene, "rbm_thumb_size")
        if rig is None and arm is not None:
            col.operator("rbm.save_pose_bones", icon='ADD')
        else:
            col.operator("rbm.save_posture", icon='ADD').clear_anim = scene.rbm_clear_anim

        # --- Animations ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Animations", icon='ANIM')
        row.operator("rbm.open_shared_anims", text="", icon='FILE_FOLDER')

        embedded = embedded_actions(context, robot)
        if embedded:
            col = box.column(align=True)
            col.prop(scene, "rbm_embedded_anim", text="")
            col.operator("rbm.load_embedded_anim", icon='IMPORT')

            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="{} animation(s) dans l'asset".format(len(embedded)))

        anims = _anims.get(robot, [])
        if anims:
            col = box.column(align=True)
            col.enabled = rig is not None
            r = col.row(align=True)
            r.prop(scene, "rbm_anim", text="")
            r.operator("rbm.preview_gif", text="", icon='HIDE_OFF')
            col.operator("rbm.load_animation", icon='IMPORT')

            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Choisir la frame, puis Enregistrer la posture")

            if getattr(scene, "mix_source_armature", None):
                box.operator("rbm.clear_source", icon='X')
        else:
            box.label(text="Aucune animation dans le dossier", icon='INFO')

        # --- Decoupage d'une animation concatenee ---
        if embedded:
            box = layout.box()
            box.prop(scene, "rbm_show_split",
                     text="Decouper une animation concatenee",
                     icon='TRIA_DOWN' if scene.rbm_show_split else 'TRIA_RIGHT',
                     emboss=False)

            if scene.rbm_show_split:
                box.prop(scene, "rbm_source_action", text="Source")

                sub = box.row()
                sub.scale_y = 0.7
                sub.label(text="Regler Start/End sur la timeline, puis Ajouter")

                for i, clip in enumerate(scene.rbm_clips):
                    line = box.row(align=True)
                    line.prop(clip, "name", text="")
                    line.prop(clip, "start", text="")
                    line.prop(clip, "end", text="")
                    line.operator("rbm.clip_preview", text="",
                                  icon='PLAY').index = i
                    line.operator("rbm.clip_remove", text="",
                                  icon='X').index = i

                box.operator("rbm.clip_add", icon='ADD')

                if scene.rbm_clips:
                    box.operator("rbm.split_action", icon='MOD_BUILD')
                    box.operator("rbm.drop_source_action", icon='TRASH')

        # --- Keyframe manuelle ---
        box = layout.box()
        box.enabled = rig is not None or arm is not None
        row = box.row(align=True)
        row.label(text="Keyframe", icon='KEY_HLT')
        sub = box.row()
        sub.scale_y = 0.7
        sub.label(text="Pose de l'armature entiere, sans passer par le mode Pose")
        box.operator("rbm.insert_keyframe", icon='KEY_HLT',
                    text="Keyframer la posture (frame {})".format(scene.frame_current))

# ---------------------------------------------------------------------------
# Expressions ARKit
#   creations/_presets/expressions-arkit/<nom>.json   presets communs a la serie
#   creations/<perso>/expressions-arkit/<nom>.json    override propre au perso
#   Un preset de meme nom dans le dossier du perso masque le commun.
# ---------------------------------------------------------------------------
ARKIT_PROBE = "jawOpen"   # presence = personnage bake par Faceit


def expr_dirs(robot):
    """(dossier commun, dossier du perso). L'un ou l'autre peut etre vide."""
    root = root_path()
    shared = os.path.join(root, CREATIONS, PRESETS, D_EXPR) if root else ""
    own = sub_dir(robot, D_EXPR) if robot else ""
    return shared, own


def scan_expressions(robot):
    """[(nom, chemin, propre_au_perso)] : le perso ecrase le commun."""
    found = {}
    shared, own = expr_dirs(robot)

    for folder, is_own in ((shared, False), (own, True)):
        if not folder or not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith(".json"):
                continue
            base = fname[:-5]
            found[base] = (base, os.path.join(folder, fname), is_own)

            key = "expr/" + robot + "/" + base
            png = os.path.join(folder, base + ".png")
            if _previews is not None and key not in _previews and os.path.isfile(png):
                _previews.load(key, png, 'IMAGE')

    return [found[k] for k in sorted(found)]


def face_meshes(context, robot=None):
    """Meshes du personnage courant qui portent des shape keys ARKit.

    Les shapes sont reparties sur plusieurs objets (visage, yeux, dents,
    langue) : il faut tous les traiter, pas seulement l'objet actif.
    """
    obj = context.view_layer.objects.active
    robot = robot or robot_of(obj)
    if not robot:
        return []

    colls = [c for c in bpy.data.collections
             if is_char_coll(c)
             and re.sub(r"_\d+$", "", coll_name(c)) == robot]

    # Plusieurs instances du meme perso : on prend celle de l'objet actif
    if len(colls) > 1 and obj is not None:
        mine = [c for c in colls if obj.name in c.all_objects]
        colls = mine or colls[:1]

    meshes = []
    for coll in colls[:1]:
        for o in coll.all_objects:
            if o.type != 'MESH':
                continue
            keys = getattr(o.data, "shape_keys", None)
            if keys and len(keys.key_blocks) > 1:
                meshes.append(o)
    return meshes


def has_arkit(context, robot=None):
    for obj in face_meshes(context, robot):
        if ARKIT_PROBE in obj.data.shape_keys.key_blocks:
            return True
    return False


def expr_to_dict(meshes):
    """Valeurs non nulles des shapes, tous objets confondus."""
    data = {}
    for obj in meshes:
        for kb in obj.data.shape_keys.key_blocks:
            if kb.name == "Basis" or kb.value <= 0.0005:
                continue
            data[kb.name] = round(kb.value, 4)
    return data


def apply_expr(meshes, data, keyframe=False, frame=None):
    """Applique un preset. Toute shape absente du preset repasse a 0, sinon
    l'expression precedente se cumule avec la nouvelle."""
    frame = frame if frame is not None else bpy.context.scene.frame_current
    touched = 0

    for obj in meshes:
        keys = obj.data.shape_keys
        for kb in keys.key_blocks:
            if kb.name == "Basis":
                continue
            kb.value = float(data.get(kb.name, 0.0))
            touched += 1
            if keyframe:
                kb.keyframe_insert("value", frame=frame)

        # Interpolation Constant : pas d'etat intermediaire entre deux cases
        if keyframe and keys.animation_data and keys.animation_data.action:
            for fc in keys.animation_data.action.fcurves:
                for kp in fc.keyframe_points:
                    if abs(kp.co.x - frame) < 0.001:
                        kp.interpolation = 'CONSTANT'

    return touched

def shape_actions(meshes):
    """Meshes dont les shape keys sont pilotees par une action."""
    out = []
    for obj in meshes:
        ad = getattr(obj.data.shape_keys, "animation_data", None)
        if ad is not None and ad.action is not None:
            out.append(obj)
    return out


class RBM_OT_expr_clear_anim(bpy.types.Operator):
    bl_idname = "rbm.expr_clear_anim"
    bl_label = "Delier l'animation des shapes"
    bl_description = ("Retire l'action qui pilote les shape keys sur tous les "
                      "meshes du visage et remet les valeurs a zero. L'action "
                      "est conservee dans le fichier (fake user)")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        meshes = face_meshes(context)
        if not meshes:
            self.report({'ERROR'}, "Aucun mesh a shape keys pour ce personnage")
            return {'CANCELLED'}

        cleared = 0
        for obj in meshes:
            keys = obj.data.shape_keys
            ad = getattr(keys, "animation_data", None)
            if ad is not None:
                if ad.action is not None:
                    ad.action.use_fake_user = True
                    cleared += 1
                keys.animation_data_clear()
            for kb in keys.key_blocks:
                if kb.name != "Basis":
                    kb.value = 0.0

        self.report({'INFO'},
                    "{} action(s) deliee(s) sur {} mesh(es), shapes a zero".format(
                        cleared, len(meshes)))
        return {'FINISHED'}
    
class RBM_OT_expr_apply(bpy.types.Operator):
    bl_idname = "rbm.expr_apply"
    bl_label = "Appliquer l'expression"
    bl_description = "Regle les shape keys ARKit du personnage sur ce preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        robot = robot_of(context.view_layer.objects.active) or scene.rbm_robot
        entry = next((e for e in scan_expressions(robot) if e[0] == self.name), None)
        if entry is None:
            self.report({'ERROR'}, "Preset '{}' introuvable".format(self.name))
            return {'CANCELLED'}

        try:
            with open(entry[1], "r", encoding="utf-8") as f:
                data = json.load(f).get("arkit", {})
        except Exception as e:
            self.report({'ERROR'}, "Lecture impossible : {}".format(e))
            return {'CANCELLED'}

        meshes = face_meshes(context, robot)
        if not meshes:
            self.report({'ERROR'}, "Aucun mesh a shape keys pour ce personnage")
            return {'CANCELLED'}

        apply_expr(meshes, data, scene.rbm_expr_keyframe)
        scene.rbm_expr_name = self.name
        scene.rbm_overwrite = False
        msg = "'{}' appliquee".format(self.name)
        if scene.rbm_expr_keyframe:
            msg += " et keyframee a la frame {}".format(scene.frame_current)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RBM_OT_expr_save(bpy.types.Operator):
    bl_idname = "rbm.expr_save"
    bl_label = "Enregistrer l'expression"
    bl_description = ("Enregistre l'etat courant des shape keys comme preset, "
                      "avec sa vignette")

    def execute(self, context):
        scene = context.scene
        robot = robot_of(context.view_layer.objects.active) or scene.rbm_robot
        name = safe_name(scene.rbm_expr_name) or "expression"

        meshes = face_meshes(context, robot)
        if not meshes:
            self.report({'ERROR'}, "Aucun mesh a shape keys pour ce personnage")
            return {'CANCELLED'}

        data = expr_to_dict(meshes)
        if not data:
            self.report({'ERROR'}, "Toutes les shapes sont a zero")
            return {'CANCELLED'}

        if scene.rbm_expr_own:
            folder = sub_dir(robot, D_EXPR, create=True)
        else:
            root = root_path()
            folder = os.path.join(root, CREATIONS, PRESETS, D_EXPR)
            os.makedirs(folder, exist_ok=True)

        if not folder:
            self.report({'ERROR'}, "Dossier de destination introuvable")
            return {'CANCELLED'}

        path = os.path.join(folder, name + ".json")
        if os.path.isfile(path) and not scene.rbm_overwrite:
            self.report({'ERROR'}, "'{}' existe deja (cocher Ecraser)".format(name))
            return {'CANCELLED'}

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": name, "arkit": data}, f, indent=1)
        except Exception as e:
            self.report({'ERROR'}, "Ecriture impossible : {}".format(e))
            return {'CANCELLED'}

        # Vignette cadree sur les seuls meshes du visage
        # Cadrage sur les petits meshes (yeux, dents, langue) : l'objet du
        # visage porte le corps entier, il ferait dezoomer la vignette
        small = sorted(meshes, key=lambda o: len(o.data.vertices))[:max(1, len(meshes) - 1)]
        ok, err = render_thumbnail(context, meshes,
                                   os.path.join(folder, name + ".png"),
                                   scene.rbm_thumb_size,
                                   frame_on=small, zoom=2.2)
        scan_all(context)

        msg = "expression '{}' enregistree".format(name)
        if not ok:
            msg += " (vignette : {})".format(err)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RBM_OT_expr_reset(bpy.types.Operator):
    bl_idname = "rbm.expr_reset"
    bl_label = "Remettre a zero"
    bl_description = "Remet toutes les shape keys du personnage a 0"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        meshes = face_meshes(context)
        if not meshes:
            self.report({'ERROR'}, "Aucun mesh a shape keys pour ce personnage")
            return {'CANCELLED'}

        apply_expr(meshes, {}, context.scene.rbm_expr_keyframe)
        self.report({'INFO'}, "shapes remises a zero")
        return {'FINISHED'}


class RBM_OT_expr_delete(bpy.types.Operator):
    bl_idname = "rbm.expr_delete"
    bl_label = "Supprimer l'expression"
    bl_description = "Supprime ce preset et sa vignette"

    name: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        robot = robot_of(context.view_layer.objects.active) or context.scene.rbm_robot
        entry = next((e for e in scan_expressions(robot) if e[0] == self.name), None)
        if entry is None:
            self.report({'ERROR'}, "Preset '{}' introuvable".format(self.name))
            return {'CANCELLED'}

        base = entry[1][:-5]
        for path in (base + ".json", base + ".png"):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                self.report({'ERROR'}, "Suppression impossible : {}".format(e))
                return {'CANCELLED'}

        scan_all(context)
        self.report({'INFO'}, "expression '{}' supprimee".format(self.name))
        return {'FINISHED'}


class RBM_PT_expressions(bpy.types.Panel):
    bl_label = "Expressions ARKit"
    bl_idname = "RBM_PT_expressions"
    bl_parent_id = "RBM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BD"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return has_arkit(context)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        robot = robot_of(context.view_layer.objects.active) or scene.rbm_robot

        presets = scan_expressions(robot)
        if presets:
            grid = layout.grid_flow(row_major=True, columns=scene.rbm_columns,
                                    even_columns=True, align=True)
            for name, path, is_own in presets:
                cell = grid.box()
                icon = icon_of("expr/" + robot + "/" + name)
                if icon:
                    cell.template_icon(icon_value=icon, scale=scene.rbm_scale)

                line = cell.row(align=True)
                line.operator("rbm.expr_apply", text=name,
                              icon='USER' if is_own else 'WORLD').name = name
                line.operator("rbm.expr_delete", text="", icon='X').name = name
        else:
            layout.label(text="(aucune expression enregistree)", icon='INFO')

        animated = shape_actions(face_meshes(context, robot))
        if animated:
            warn = layout.box()
            warn.label(text="{} mesh(es) animes : valeurs pilotees".format(len(animated)),
                       icon='ERROR')
            warn.operator("rbm.expr_clear_anim", icon='UNLINKED')

        layout.prop(scene, "rbm_expr_keyframe")
        layout.operator("rbm.expr_reset", icon='LOOP_BACK')

        box = layout.box()
        box.label(text="Enregistrer l'etat courant :", icon='FILE_TICK')
        box.prop(scene, "rbm_expr_name", text="")
        row = box.row(align=True)
        row.prop(scene, "rbm_expr_own")
        row.prop(scene, "rbm_overwrite")
        box.operator("rbm.expr_save", icon='ADD')
        
# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------
classes = (
    RBM_Preferences,
    RBM_OT_scan,
    RBM_OT_select_robot,
    RBM_OT_instantiate,
    RBM_OT_robot_thumb,
    RBM_OT_save_posture,
    RBM_OT_apply_posture,
    RBM_OT_delete_posture,
    RBM_OT_insert_keyframe,
    RBM_OT_load_animation,
    RBM_OT_clear_source,
    RBM_OT_load_embedded_anim,
    RBM_OT_save_pose_bones,    
    RBM_OT_set_family,
    RBM_Clip,
    RBM_OT_clip_add,
    RBM_OT_clip_remove,
    RBM_OT_clip_preview,
    RBM_OT_split_action,
    RBM_OT_drop_source_action,
    RBM_OT_page,
    RBM_PT_panel,
    RBM_OT_new_character,
    RBM_OT_edit_character,
    RBM_OT_delete_character,
    RBM_OT_open_shared_anims,
    RBM_OT_preview_gif,
    RBM_OT_duplicate_character,
    RBM_OT_expr_apply,
    RBM_OT_expr_clear_anim,
    RBM_OT_expr_save,
    RBM_OT_expr_reset,
    RBM_OT_expr_delete,
    RBM_PT_expressions,
)


@bpy.app.handlers.persistent
def _on_load(dummy=None):
    try:
        scan_all(bpy.context)
    except Exception:
        pass


def _deferred_scan():
    try:
        scan_all(bpy.context)
    except Exception:
        pass
    return None


def register():
    global _previews

    for cls in classes:
        bpy.utils.register_class(cls)

    _previews = bpy.utils.previews.new()

    S = bpy.types.Scene
    S.rbm_robot = bpy.props.EnumProperty(name="Robot", items=robot_enum)
    S.rbm_anim = bpy.props.EnumProperty(name="Animation", items=anim_enum)
    S.rbm_embedded_anim = bpy.props.EnumProperty(
        name="Animation de l'asset", items=action_enum)
    S.rbm_posture_name = bpy.props.StringProperty(name="Nom", default="posture_01")
    S.rbm_overwrite = bpy.props.BoolProperty(name="Ecraser", default=False)
    S.rbm_clear_anim = bpy.props.BoolProperty(
        name="Supprimer l'animation apres enregistrement", default=False,
        description="Fige le rig sur la posture et retire l'armature source importee")
    S.rbm_edit = bpy.props.BoolProperty(name="Mode gestion", default=False)
    S.rbm_follow = bpy.props.BoolProperty(
        name="Suivre la selection", default=True,
        description="Cliquer un robot ou son control rig dans la scene le designe "
                    "dans la liste")
    S.rbm_auto_rig = bpy.props.BoolProperty(
        name="Control rig automatique", default=True,
        description="Cree le control rig IK/FK juste apres l'import du robot")
    S.rbm_auto_rest = bpy.props.BoolProperty(
        name="Posture de repos automatique", default=True,
        description=("A la premiere instanciation, enregistre la pose initiale du robot "
                     "comme posture de reference"))
    S.rbm_columns = bpy.props.IntProperty(name="Colonnes", default=3, min=1, max=6)
    S.rbm_scale = bpy.props.FloatProperty(name="Taille", default=4.0, min=1.0, max=10.0)
    S.rbm_family_filter = bpy.props.EnumProperty(
        name="Famille", items=FAMILY_FILTER, default='ALL')
    S.rbm_search = bpy.props.StringProperty(
        name="Rechercher", default="", options={'TEXTEDIT_UPDATE'})
    S.rbm_page = bpy.props.IntProperty(name="Page", default=0, min=0)
    S.rbm_thumb_size = bpy.props.IntProperty(name="Resolution vignette", default=256,
                                             min=64, max=512)
    S.rbm_expr_name = bpy.props.StringProperty(name="Nom", default="expression_01")
    S.rbm_expr_keyframe = bpy.props.BoolProperty(
        name="Poser un keyframe", default=True,
        description="Keyframe Constant sur toutes les shapes a la frame courante, "
                    "pour que chaque camera garde son expression")
    S.rbm_expr_own = bpy.props.BoolProperty(
        name="Propre au perso", default=False,
        description="Enregistre dans le dossier du personnage au lieu du dossier "
                    "commun a la serie")
    S.rbm_clips = bpy.props.CollectionProperty(type=RBM_Clip)
    S.rbm_clip_index = bpy.props.IntProperty(default=0)
    S.rbm_show_split = bpy.props.BoolProperty(default=False)
    S.rbm_source_action = bpy.props.EnumProperty(
        name="Action source", items=action_enum)

    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.register(_deferred_scan, first_interval=0.5)
    if not bpy.app.timers.is_registered(_follow_selection):
        bpy.app.timers.register(_follow_selection, first_interval=1.0, persistent=True)


def unregister():
    global _previews

    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
        
    if bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.unregister(_deferred_scan)
        
    if bpy.app.timers.is_registered(_follow_selection):
        bpy.app.timers.unregister(_follow_selection)

    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None

    S = bpy.types.Scene
    for prop in ("rbm_source_action", "rbm_show_split", "rbm_clip_index",
                 "rbm_clips", "rbm_clear_anim", "rbm_embedded_anim", "rbm_page", "rbm_search", "rbm_family_filter", "rbm_expr_own", "rbm_expr_keyframe", "rbm_expr_name", 
                 "rbm_thumb_size", "rbm_scale", "rbm_columns", "rbm_edit", "rbm_follow",
                 "rbm_overwrite", "rbm_auto_rig", "rbm_auto_rest", "rbm_posture_name", "rbm_anim", "rbm_robot"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()