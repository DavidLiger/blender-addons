bl_info = {
    "name": "Robot Manager",
    "author": "David",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Robot Manager",
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

COLL_PREFIX = "ROBOT_"
K_ROBOT = "robot"

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
    global _robots, _postures, _anims

    _robots, _postures, _anims = [], {}, {}
    if _previews is not None:
        _previews.clear()

    root = root_path()
    creations = os.path.join(root, CREATIONS) if root else ""
    if not creations or not os.path.isdir(creations):
        return 0

    for name in sorted(os.listdir(creations)):
        folder = os.path.join(creations, name)
        if not os.path.isdir(folder):
            continue

        _robots.append((name, folder))

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
def render_thumbnail(context, objects, path, size=256, front=True):
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
    cam_data.ortho_scale = extent * 1.25
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
        if coll.name.startswith(COLL_PREFIX):
            return re.sub(r"_\d+$", "", coll.name[len(COLL_PREFIX):])

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


class RBM_OT_new_character(bpy.types.Operator):
    bl_idname = "rbm.new_character"
    bl_label = "Creer un personnage"
    bl_description = ("Cree l'arborescence du personnage et son fichier .blend, "
                      "ouvert dans une seconde instance de Blender")

    name: bpy.props.StringProperty(name="Nom", default="robot_01")
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
        meshes = rig_meshes(rig) if rig else [o for o in context.selected_objects
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

        ok, msg = save_posture(context, robot, rig,
                               scene.rbm_posture_name, scene.rbm_overwrite)
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

        rig = find_control_rig(context)
        if rig is None:
            self.report({'ERROR'}, "Aucun control rig trouve")
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


# ---------------------------------------------------------------------------
# Panneau
# ---------------------------------------------------------------------------
class RBM_PT_panel(bpy.types.Panel):
    bl_label = "Robot Manager"
    bl_idname = "RBM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Robot Manager"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        robot = scene.rbm_robot

        # --- Robots ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Robots", icon='OUTLINER_OB_ARMATURE')
        row.operator("rbm.new_character", text="", icon='ADD')
        row.prop(scene, "rbm_edit", text="", icon='TRASH', toggle=True)
        row.operator("rbm.scan", text="", icon='FILE_REFRESH')

        if not root_path():
            box.label(text="Racine non definie (preferences)", icon='ERROR')
            return

        if not _robots:
            box.label(text="Aucun robot - relire les dossiers", icon='INFO')
        else:
            grid = box.grid_flow(row_major=True, columns=scene.rbm_columns,
                                 even_columns=True)
            for name, folder in _robots:
                cell = grid.box()
                icon = icon_of("robot/" + name)
                if icon:
                    cell.template_icon(icon_value=icon, scale=scene.rbm_scale)
                line = cell.row(align=True)
                line.operator("rbm.select_robot", text=name,
                              depress=(name == robot)).name = name
                line.operator("rbm.edit_character", text="",
                              icon='GREASEPENCIL').name = name
                if scene.rbm_edit:
                    line.operator("rbm.delete_character", text="",
                                  icon='TRASH').name = name

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
        box.operator("rbm.robot_thumb", icon='RESTRICT_RENDER_OFF')

        # --- Control rig ---
        rig = find_control_rig(context)
        box = layout.box()
        row = box.row()
        if rig is None:
            row.alert = True
            row.label(text="Aucun control rig actif", icon='ERROR')
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Onglet Mixamo > Create Control Rig")
        else:
            row.label(text="Rig : " + rig.name, icon='ARMATURE_DATA')

        # --- Postures ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Postures", icon='POSE_HLT')
        row.prop(scene, "rbm_edit", text="", icon='TRASH', toggle=True)

        poses = _postures.get(robot, [])
        if poses:
            grid = box.grid_flow(row_major=True, columns=scene.rbm_columns,
                                 even_columns=True)
            grid.enabled = rig is not None
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
        col = box.column(align=True)
        col.enabled = rig is not None
        r = col.row(align=True)
        r.prop(scene, "rbm_posture_name", text="")
        r.prop(scene, "rbm_overwrite")
        col.prop(scene, "rbm_thumb_size")
        col.operator("rbm.save_posture", icon='ADD')

        # --- Animations ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Animations", icon='ANIM')
        row.operator("rbm.open_shared_anims", text="", icon='FILE_FOLDER')

        anims = _anims.get(robot, [])
        if anims:
            col = box.column(align=True)
            col.enabled = rig is not None
            col.prop(scene, "rbm_anim", text="")
            col.operator("rbm.load_animation", icon='IMPORT')

            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Choisir la frame, puis Enregistrer la posture")

            if getattr(scene, "mix_source_armature", None):
                box.operator("rbm.clear_source", icon='X')
        else:
            box.label(text="Aucune animation dans le dossier", icon='INFO')


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
    RBM_OT_load_animation,
    RBM_OT_clear_source,
    RBM_PT_panel,
    RBM_OT_new_character,
    RBM_OT_edit_character,
    RBM_OT_delete_character,
    RBM_OT_open_shared_anims,
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
    S.rbm_posture_name = bpy.props.StringProperty(name="Nom", default="posture_01")
    S.rbm_overwrite = bpy.props.BoolProperty(name="Ecraser", default=False)
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
    S.rbm_thumb_size = bpy.props.IntProperty(name="Resolution vignette", default=256,
                                             min=64, max=512)

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
    for prop in ("rbm_thumb_size", "rbm_scale", "rbm_columns", "rbm_edit", "rbm_follow",
                 "rbm_overwrite", "rbm_auto_rig", "rbm_auto_rest", "rbm_posture_name", "rbm_anim", "rbm_robot"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
