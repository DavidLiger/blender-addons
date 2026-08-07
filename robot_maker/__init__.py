bl_info = {
    "name": "Robot Maker",
    "author": "David",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Robot Maker",
    "description": ("Assemblage de robots : points de connexion sur les pieces et tubes "
                    "de liaison qui suivent les pieces en temps reel"),
    "category": "3D View",
}

import bpy
import bpy.utils.previews
import hashlib
import math
import os
import re
import webbrowser
from mathutils import Matrix, Vector


# ---------------------------------------------------------------------------
# Conventions de nommage
# Robot-Manager s'appuiera dessus pour isoler les persos du rendu decor.
# ---------------------------------------------------------------------------
COLL_PREFIX = "ROBOT_"
SOCKET_PREFIX = "SKT_"
TUBE_PREFIX = "TUBE_"

# Cles posees sur les objets, plus fiables que le nom pour les retrouver
K_ROBOT = "robot"          # nom du robot auquel l'objet appartient
K_SOCKET = "robot_socket"  # marque un empty de connexion
K_TUBE = "robot_tube"      # marque un tube de liaison

# Categories de pieces (l'ordre est celui du menu)
PART_CATEGORIES = [
    ('BODY', "Corps", "Electromenager ou objet servant de torse"),
    ('HEAD', "Tete", "Cylindre, cube, ecran..."),
    ('HAND', "Main", "Gant facon cartoon"),
    ('FOOT', "Chaussure", "Chaussure ou pied"),
    ('HINGE', "Charniere", "Articulation : epaule, coude, hanche, genou, cheville"),
    ('OTHER', "Autre", "Accessoire"),
]

# Noms proposes pour les points de connexion
SOCKET_PRESETS = [
    "shoulder_L", "shoulder_R", "elbow_L", "elbow_R", "wrist_L", "wrist_R",
    "hip_L", "hip_R", "knee_L", "knee_R", "ankle_L", "ankle_R",
    "neck", "waist", "custom",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def robot_collections():
    return [c for c in bpy.data.collections if c.name.startswith(COLL_PREFIX)]


def active_robot_collection(context):
    """Collection du robot courant, ou None."""
    name = context.scene.rm_robot
    if not name:
        return None
    return bpy.data.collections.get(COLL_PREFIX + name)


def robot_enum(self, context):
    items = [(c.name[len(COLL_PREFIX):], c.name[len(COLL_PREFIX):], "")
             for c in robot_collections()]
    return items or [('', "(aucun robot)", "")]


def link_to_robot(obj, coll, robot_name):
    """Range l'objet dans la collection du robot et le marque."""
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)
    obj[K_ROBOT] = robot_name


def sockets_of(obj):
    if obj is None:
        return []
    return [c for c in obj.children if c.get(K_SOCKET)]


def all_sockets(coll):
    if coll is None:
        return []
    return [o for o in coll.objects if o.get(K_SOCKET)]


def selected_sockets(context):
    return [o for o in context.selected_objects if o.get(K_SOCKET)]


def socket_world(socket):
    return socket.matrix_world.translation.copy()

def deselect_all(context):
    """Deselectionne sans passer par bpy.ops.object.select_all, dont le poll
    echoue quand l'appel vient du panneau et qu'on n'est pas en mode Objet."""
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    for obj in context.view_layer.objects:
        try:
            obj.select_set(False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Arborescence de travail
#   <racine>/library/{body,head,hands,feet,joints,other}   pieces reutilisables
#   <racine>/creations/<robot>/expressions                 un dossier par robot
#   <racine>/expression-maker/expressions.html             generateur partage
# Robot-Manager s'appuiera sur cette organisation pour lister les robots et
# retrouver leurs expressions.
# ---------------------------------------------------------------------------
LIBRARY_DIRS = ["body", "head", "hands", "feet", "joints", "other"]
CREATIONS = "creations"
LIBRARY = "library"
EXPR_MAKER = "expression-maker"


def addon_prefs(context):
    try:
        return context.preferences.addons[__name__].preferences
    except Exception:
        return None


def root_path(context):
    prefs = addon_prefs(context)
    if prefs is None or not prefs.root:
        return ""
    return bpy.path.abspath(prefs.root)


def robot_dir(context, name, create=False):
    root = root_path(context)
    if not root:
        return ""
    path = os.path.join(root, CREATIONS, name)
    if create:
        os.makedirs(os.path.join(path, "expressions"), exist_ok=True)
    return path


class RM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    def _root_changed(self, context):
        try:
            scan_library(context)
        except Exception:
            pass

    root: bpy.props.StringProperty(
        name="Dossier ROBOTS", subtype='DIR_PATH', default="",
        update=_root_changed,
        description="Racine contenant library, creations et expression-maker")

    def draw(self, context):
        self.layout.prop(self, "root")


class RM_OT_init_folders(bpy.types.Operator):
    bl_idname = "rm.init_folders"
    bl_label = "Creer l'arborescence"
    bl_description = "Cree les dossiers library, creations et expression-maker"

    def execute(self, context):
        root = root_path(context)
        if not root:
            self.report({'ERROR'}, "Definir le dossier ROBOTS dans les preferences de l'addon")
            return {'CANCELLED'}

        try:
            for sub in LIBRARY_DIRS:
                os.makedirs(os.path.join(root, LIBRARY, sub), exist_ok=True)
            os.makedirs(os.path.join(root, CREATIONS), exist_ok=True)
            os.makedirs(os.path.join(root, EXPR_MAKER), exist_ok=True)
        except Exception as e:
            self.report({'ERROR'}, "Creation impossible : {}".format(e))
            return {'CANCELLED'}

        self.report({'INFO'}, "Arborescence prete dans {}".format(root))
        return {'FINISHED'}


class RM_OT_open_folder(bpy.types.Operator):
    bl_idname = "rm.open_folder"
    bl_label = "Ouvrir le dossier"
    bl_description = "Ouvre ce dossier dans l'explorateur de fichiers"

    which: bpy.props.StringProperty(default='ROBOT')

    def execute(self, context):
        root = root_path(context)
        if not root:
            self.report({'ERROR'}, "Dossier ROBOTS non defini (preferences de l'addon)")
            return {'CANCELLED'}

        if self.which == 'LIBRARY':
            path = os.path.join(root, LIBRARY)
        elif self.which == 'ROOT':
            path = root
        elif self.which in {'PROTOTYPE', 'RIGGED'}:
            base = robot_dir(context, context.scene.rm_robot)
            sub = "prototype" if self.which == 'PROTOTYPE' else "mixamo-rigged"
            path = os.path.join(base, sub) if base else ""
            if path:
                try:
                    os.makedirs(path, exist_ok=True)
                except Exception:
                    pass
        else:
            path = robot_dir(context, context.scene.rm_robot)

        if not path or not os.path.isdir(path):
            self.report({'ERROR'}, "Dossier introuvable : {}".format(path))
            return {'CANCELLED'}

        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class RM_OT_open_expression_maker(bpy.types.Operator):
    bl_idname = "rm.open_expression_maker"
    bl_label = "Generateur d'expressions"
    bl_description = ("Ouvre expressions.html dans le navigateur. Exporter le sprite sheet "
                      "dans le dossier expressions du robot")

    def execute(self, context):
        root = root_path(context)
        if not root:
            self.report({'ERROR'}, "Dossier ROBOTS non defini (preferences de l'addon)")
            return {'CANCELLED'}

        page = os.path.join(root, EXPR_MAKER, "expressions.html")
        if not os.path.isfile(page):
            self.report({'ERROR'}, "expressions.html absent de {}".format(
                os.path.join(root, EXPR_MAKER)))
            return {'CANCELLED'}

        webbrowser.open("file://" + page.replace("\\", "/"))

        target = os.path.join(robot_dir(context, context.scene.rm_robot), "expressions")
        self.report({'INFO'}, "Exporter vers : {}".format(target))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Bibliotheque d'assets
# Chaque piece est un .blend a un objet, accompagne d'une vignette .png.
# ---------------------------------------------------------------------------
CAT_DIR = {
    'BODY': "body", 'HEAD': "head", 'HAND': "hands",
    'FOOT': "feet", 'HINGE': "joints", 'OTHER': "other",
}

_asset_previews = None
_assets = {}            # categorie -> [(nom, chemin .blend)]


def library_dir(context, category, create=False):
    root = root_path(context)
    if not root:
        return ""
    path = os.path.join(root, LIBRARY, CAT_DIR.get(category, "other"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def scan_library(context):
    """Relit la bibliotheque et recharge les vignettes."""
    global _assets

    _assets = {}
    if _asset_previews is not None:
        _asset_previews.clear()

    root = root_path(context)
    if not root:
        return 0

    total = 0
    for cat in CAT_DIR:
        folder = library_dir(context, cat)
        items = []
        if os.path.isdir(folder):
            for fname in sorted(os.listdir(folder)):
                if not fname.lower().endswith(".blend"):
                    continue
                name = fname[:-6]
                path = os.path.join(folder, fname)
                items.append((name, path))

                thumb = os.path.join(folder, name + ".png")
                if _asset_previews is not None and os.path.isfile(thumb):
                    key = cat + "/" + name
                    if key not in _asset_previews:
                        _asset_previews.load(key, thumb, 'IMAGE')
        _assets[cat] = items
        total += len(items)

    return total


_asset_enum_cache = []


def asset_enum(self, context):
    """Assets de la categorie courante, avec vignette si disponible."""
    global _asset_enum_cache
    _asset_enum_cache = []

    scene = context.scene if context else None
    cat = scene.rm_category if scene else 'BODY'

    for i, (name, path) in enumerate(_assets.get(cat, [])):
        icon = 0
        if _asset_previews is not None:
            prev = _asset_previews.get(cat + "/" + name)
            if prev:
                icon = prev.icon_id
        _asset_enum_cache.append((name, name, path, icon, i))

    if not _asset_enum_cache:
        _asset_enum_cache = [('NONE', "(bibliotheque vide)", "", 0, 0)]

    return _asset_enum_cache


def _mesh_bounds_world(obj):
    """Coins du volume, calcules sur les vertices : ne depend pas d'une
    evaluation du depsgraph, contrairement a bound_box."""
    mw = obj.matrix_world
    verts = getattr(obj.data, "vertices", None)

    if verts and len(verts):
        pts = [mw @ v.co for v in verts]
    else:
        pts = [mw @ Vector(c) for c in obj.bound_box]

    lo = Vector((min(p[i] for p in pts) for i in range(3)))
    hi = Vector((max(p[i] for p in pts) for i in range(3)))
    return lo, hi


def _build_thumb_scene(obj, size):
    """Scene temporaire contenant la piece et une camera qui la cadre."""
    scn = bpy.data.scenes.new("_rm_thumb")
    scn.render.engine = 'BLENDER_WORKBENCH'
    scn.render.resolution_x = size
    scn.render.resolution_y = size
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = True
    scn.render.image_settings.file_format = 'PNG'
    scn.render.image_settings.color_mode = 'RGBA'

    scn.collection.objects.link(obj)

    # La piece doit etre visible dans cette scene, quels que soient ses
    # reglages d'origine
    obj.hide_viewport = False
    obj.hide_render = False
    obj.hide_set(False, view_layer=scn.view_layers[0])

    lo, hi = _mesh_bounds_world(obj)
    center = (lo + hi) / 2.0
    extent = max(hi[i] - lo[i] for i in range(3)) or 1.0

    cam_data = bpy.data.cameras.new("_rm_thumb_cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = extent * 1.6
    cam_data.clip_start = 0.001
    cam_data.clip_end = extent * 20.0

    cam = bpy.data.objects.new("_rm_thumb_cam", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam

    direction = Vector((1.0, -1.2, 0.7)).normalized()
    cam.matrix_world = (Matrix.Translation(center + direction * extent * 5.0)
                        @ direction.to_track_quat('Z', 'Y').to_matrix().to_4x4())

    return scn, cam


def _make_thumbnail(context, scn, path):
    """Rend la scene temporaire vers le PNG. Retourne (ok, message)."""
    scn.render.filepath = path
    scn.render.use_file_extension = False

    window = context.window
    previous = window.scene if window else None

    try:
        if window is not None:
            window.scene = scn
            bpy.ops.render.render(write_still=True)
        else:
            with context.temp_override(scene=scn):
                bpy.ops.render.render(write_still=True)
    except Exception as e:
        return False, str(e)
    finally:
        if window is not None and previous is not None:
            window.scene = previous

    if not os.path.isfile(path):
        return False, "fichier non ecrit"
    return True, ""


@bpy.app.handlers.persistent
def _on_load_scan(dummy=None):
    """Relit la bibliotheque a l'ouverture d'un fichier."""
    try:
        scan_library(bpy.context)
    except Exception:
        pass


def _deferred_scan():
    """Premiere lecture apres l'activation de l'addon : les preferences et le
    contexte ne sont pas encore disponibles pendant register()."""
    try:
        scan_library(bpy.context)
    except Exception:
        pass
    return None        # ne se replanifie pas


class RM_OT_scan_library(bpy.types.Operator):
    bl_idname = "rm.scan_library"
    bl_label = "Relire la bibliotheque"
    bl_description = "Relit les dossiers de la bibliotheque et recharge les vignettes"

    def execute(self, context):
        if not root_path(context):
            self.report({'ERROR'}, "Dossier ROBOTS non defini (preferences de l'addon)")
            return {'CANCELLED'}

        total = scan_library(context)
        self.report({'INFO'}, "{} asset(s) trouve(s)".format(total))
        return {'FINISHED'}


class RM_OT_add_to_library(bpy.types.Operator):
    bl_idname = "rm.add_to_library"
    bl_label = "Ajouter a la bibliotheque"
    bl_description = ("Enregistre l'objet selectionne comme asset reutilisable, "
                      "avec sa vignette. L'objet de la scene n'est pas modifie")

    def execute(self, context):
        scene = context.scene

        if not root_path(context):
            self.report({'ERROR'}, "Dossier ROBOTS non defini (preferences de l'addon)")
            return {'CANCELLED'}

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner la piece (mesh) a enregistrer")
            return {'CANCELLED'}

        name = re.sub(r"[^A-Za-z0-9_-]+", "_", scene.rm_asset_name.strip())
        if not name:
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", obj.name)

        # Une piece deja rattachee garde sa categorie d'origine : reenregistrer
        # une piece amelioree la remet au bon endroit sans reglage manuel
        folder = library_dir(context, scene.rm_category, create=True)
        path = os.path.join(folder, name + ".blend")

        if os.path.isfile(path) and not scene.rm_asset_overwrite:
            self.report({'ERROR'}, "'{}' existe deja (cocher Ecraser pour remplacer)".format(name))
            return {'CANCELLED'}

        # On travaille sur une copie : l'objet de la scene reste intact
        context.view_layer.update()
        world = obj.matrix_world.copy()

        tmp = obj.copy()
        tmp.data = obj.data.copy()
        tmp.name = name
        tmp.parent = None
        tmp.animation_data_clear()
        for key in (K_ROBOT, K_SOCKET, K_TUBE, "robot_part", "robot_slot"):
            if key in tmp:
                del tmp[key]

        if scene.rm_asset_freeze:
            # Rotation et echelle passees dans la geometrie : l'asset arrive
            # ensuite a l'echelle 1, sans surprise au reimport
            basis = world.copy()
            basis.translation = Vector((0.0, 0.0, 0.0))
            tmp.data.transform(basis)
            tmp.matrix_world = Matrix.Identity(4)
        else:
            tmp.matrix_world = world

        scn, cam = _build_thumb_scene(tmp, scene.rm_thumb_size)
        thumb_ok, thumb_err = _make_thumbnail(context, scn,
                                              os.path.join(folder, name + ".png"))

        # La camera ne doit pas partir dans l'asset
        scn.collection.objects.unlink(cam)

        error = ""
        try:
            # La scene est ecrite avec l'objet : le fichier reste lisible a
            # l'ouverture, au lieu de ne contenir que des donnees orphelines
            bpy.data.libraries.write(path, {scn, tmp}, fake_user=True)
        except Exception as e:
            error = str(e)

        # Nettoyage complet de la copie
        mesh = tmp.data
        bpy.data.scenes.remove(scn)
        cam_data = cam.data
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(cam_data)
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(mesh)

        if error:
            self.report({'ERROR'}, "Enregistrement impossible : {}".format(error))
            return {'CANCELLED'}

        scan_library(context)
        msg = "'{}' ajoute a {}".format(name, CAT_DIR.get(scene.rm_category))
        if not thumb_ok:
            msg += " (vignette : {})".format(thumb_err or "echec")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_place_asset(bpy.types.Operator):
    bl_idname = "rm.place_asset"
    bl_label = "Placer l'asset"
    bl_description = ("Importe l'asset et le pose sur le repere selectionne, "
                      "ou a defaut sur le repere choisi dans la liste")
    bl_options = {'REGISTER', 'UNDO'}

    asset: bpy.props.StringProperty(default="")

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        name = self.asset or scene.rm_asset
        if not name or name == 'NONE':
            self.report({'ERROR'}, "Aucun asset selectionne")
            return {'CANCELLED'}

        path = next((p for n, p in _assets.get(scene.rm_category, []) if n == name), None)
        if path is None or not os.path.isfile(path):
            self.report({'ERROR'}, "Fichier introuvable : relire la bibliotheque")
            return {'CANCELLED'}

        empty = resolve_target(context)
        if empty is None:
            self.report({'ERROR'}, "Aucun repere : creer le squelette")
            return {'CANCELLED'}

        # Memorise la cible : la selection va changer apres l'import
        scene.rm_target_socket = empty.name

        try:
            with bpy.data.libraries.load(path, link=False) as (src, dst):
                dst.objects = list(src.objects)
        except Exception as e:
            self.report({'ERROR'}, "Import impossible : {}".format(e))
            return {'CANCELLED'}

        imported = [o for o in dst.objects
                    if o is not None and o.type not in {'CAMERA', 'LIGHT'}]

        # Les objets ecartes sont supprimes pour ne pas encombrer le fichier
        for o in dst.objects:
            if o is not None and o.type in {'CAMERA', 'LIGHT'}:
                bpy.data.objects.remove(o)
        if not imported:
            self.report({'ERROR'}, "Le fichier ne contient aucun objet")
            return {'CANCELLED'}

        for obj in imported:
            coll.objects.link(obj)
            obj[K_ROBOT] = scene.rm_robot
            obj["robot_part"] = scene.rm_category
            obj["robot_slot"] = empty.get("robot_slot", empty.name)
            _attach_to_empty(context, obj, empty)
            _mirror_after_attach(context, obj, empty)

        deselect_all(context)
        for obj in imported:
            obj.select_set(True)
        context.view_layer.objects.active = imported[0]

        self.report({'INFO'}, "'{}' pose sur {}".format(
            name, empty.name[len(SOCKET_PREFIX):]))
        return {'FINISHED'}


def resolve_target(context):
    """Repere vise : celui selectionne dans la vue, sinon le dernier utilise,
    sinon celui choisi dans la liste des emplacements."""
    scene = context.scene
    coll = active_robot_collection(context)
    if coll is None:
        return None

    socks = selected_sockets(context)
    if socks:
        return socks[0]

    if scene.rm_target_socket:
        obj = bpy.data.objects.get(scene.rm_target_socket)
        if obj is not None and obj.get(K_SOCKET):
            return obj

    return slot_empty(coll, scene.rm_slot)


def filtered_assets(scene):
    """Assets de la categorie courante, filtres par la recherche."""
    items = _assets.get(scene.rm_category, [])
    query = scene.rm_asset_search.strip().lower()
    if query:
        items = [(n, p) for n, p in items if query in n.lower()]
    return items


def page_count(scene, total):
    per = max(1, scene.rm_asset_per_page)
    return max(1, (total + per - 1) // per)


class RM_OT_delete_asset(bpy.types.Operator):
    bl_idname = "rm.delete_asset"
    bl_label = "Supprimer l'asset"
    bl_description = ("Supprime definitivement cet asset de la bibliotheque "
                      "(fichier .blend et vignette). Les pieces deja posees "
                      "dans les scenes ne sont pas touchees")

    asset: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        name = self.asset or scene.rm_asset

        if not name or name == 'NONE':
            self.report({'ERROR'}, "Aucun asset selectionne")
            return {'CANCELLED'}

        path = next((p for n, p in _assets.get(scene.rm_category, []) if n == name), None)
        if path is None:
            self.report({'ERROR'}, "Asset introuvable : relire la bibliotheque")
            return {'CANCELLED'}

        removed = []
        for target in (path, os.path.splitext(path)[0] + ".png"):
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    removed.append(os.path.basename(target))
                except Exception as e:
                    self.report({'ERROR'}, "Suppression impossible : {}".format(e))
                    return {'CANCELLED'}

        scan_library(context)
        scene.rm_asset_page = 0

        self.report({'INFO'}, "'{}' supprime ({})".format(name, ", ".join(removed)))
        return {'FINISHED'}


class RM_OT_update_mirrors(bpy.types.Operator):
    bl_idname = "rm.update_mirrors"
    bl_label = "Mettre a jour les symetries"
    bl_description = ("Regenere les pieces symetriques dont l'originale a ete deplacee, "
                      "redimensionnee ou modifiee")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = active_robot_collection(context)
        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        stale = outdated_mirrors(coll)
        if not stale:
            self.report({'INFO'}, "Symetries deja a jour")
            return {'FINISHED'}

        rebuilt, orphans = 0, 0

        for child in stale:
            source = bpy.data.objects.get(child.get(K_MIRROR_OF, ""))
            slot = child.get("robot_slot", "")

            bpy.data.objects.remove(child)

            if source is None:
                orphans += 1
                continue

            dup, err = make_mirror(context, source, opposite_slot(slot) or slot)
            if dup is not None:
                rebuilt += 1

        msg = "{} symetrie(s) regeneree(s)".format(rebuilt)
        if orphans:
            msg += " - {} copie(s) sans source supprimee(s)".format(orphans)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_mirror_selected(bpy.types.Operator):
    bl_idname = "rm.mirror_selected"
    bl_label = "Dupliquer en symetrie"
    bl_description = "Cree la piece symetrique de la piece selectionnee"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = active_robot_collection(context)
        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        parts = [o for o in context.selected_objects
                 if o.type == 'MESH' and not o.get(K_TUBE) and not o.get(K_MIRROR_OF)]
        if not parts:
            self.report({'ERROR'}, "Selectionner une piece originale (pas une copie miroir)")
            return {'CANCELLED'}

        done, errors = 0, []
        for obj in parts:
            dup, err = make_mirror(context, obj, obj.get("robot_slot", ""))
            if dup is None:
                errors.append("{} : {}".format(obj.name, err))
            else:
                done += 1

        if errors:
            self.report({'WARNING'}, "; ".join(errors))
        else:
            self.report({'INFO'}, "{} piece(s) dupliquee(s)".format(done))
        return {'FINISHED'}


class RM_OT_asset_page(bpy.types.Operator):
    bl_idname = "rm.asset_page"
    bl_label = "Page"
    bl_description = "Page suivante ou precedente de la bibliotheque"

    delta: bpy.props.IntProperty(default=1)

    def execute(self, context):
        scene = context.scene
        total = len(filtered_assets(scene))
        last = page_count(scene, total) - 1
        scene.rm_asset_page = max(0, min(last, scene.rm_asset_page + self.delta))
        return {'FINISHED'}


def _reset_page(self, context):
    try:
        context.scene.rm_asset_page = 0
    except Exception:
        pass
# ---------------------------------------------------------------------------
# Suivi de la selection : les champs d'ajout a la bibliotheque se remplissent
# avec l'objet actif. msgbus reagit au changement d'objet actif, le timer sert
# de filet quand l'evenement n'est pas emis.
# ---------------------------------------------------------------------------
_sync_owner = object()
_last_active_name = None


def _fill_asset_fields():
    global _last_active_name

    try:
        scene = bpy.context.scene
        obj = bpy.context.view_layer.objects.active
    except Exception:
        return

    name = obj.name if obj else None
    if name == _last_active_name:
        return
    _last_active_name = name

    if obj is None or obj.type != 'MESH' or obj.get(K_TUBE) or obj.get(K_SOCKET):
        return

    scene.rm_asset_name = obj.name

    part = obj.get("robot_part")
    if part in {c[0] for c in PART_CATEGORIES}:
        scene.rm_category = part

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _poll_selection():
    try:
        _fill_asset_fields()
    except Exception:
        pass
    return 0.25


def _subscribe_selection():
    bpy.msgbus.clear_by_owner(_sync_owner)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=_sync_owner,
        args=(),
        notify=_fill_asset_fields,
        options={'PERSISTENT'},
    )


@bpy.app.handlers.persistent
def _on_load_selection(dummy=None):
    global _last_active_name
    _last_active_name = None
    _subscribe_selection()

def _asset_changed(self, context):
    """Placement immediat au clic sur une vignette."""
    scene = context.scene
    if not scene.rm_place_on_click or scene.rm_asset in ("", 'NONE'):
        return
    try:
        bpy.ops.rm.place_asset()
    except Exception:
        pass

class RM_OT_setup_scene(bpy.types.Operator):
    bl_idname = "rm.setup_scene"
    bl_label = "Preparer l'eclairage"
    bl_description = ("Ajoute un World gris et une lampe Sun : sans eux, l'apercu "
                      "rendu est noir dans un fichier vide")

    def execute(self, context):
        scene = context.scene
        steps = []

        if scene.world is None:
            world = bpy.data.worlds.new("World")
            world.use_nodes = True
            bg = world.node_tree.nodes.get("Background")
            if bg is not None:
                bg.inputs[0].default_value = (0.0513, 0.0513, 0.0545, 1.0)
                bg.inputs[1].default_value = 1.0
            scene.world = world
            steps.append("World ajoute")

        if not any(o.type == 'LIGHT' for o in scene.objects):
            data = bpy.data.lights.new("Sun", type='SUN')
            data.energy = 3.0
            sun = bpy.data.objects.new("Sun", data)
            scene.collection.objects.link(sun)
            sun.location = (4.0, -6.0, 8.0)
            sun.rotation_euler = (math.radians(50.0), 0.0, math.radians(35.0))
            steps.append("Sun ajoutee")

        self.report({'INFO'}, " - ".join(steps) or "Scene deja prete")
        return {'FINISHED'}

# ---------------------------------------------------------------------------
# Robot : creation / selection
# ---------------------------------------------------------------------------
class RM_OT_new_robot(bpy.types.Operator):
    bl_idname = "rm.new_robot"
    bl_label = "Creer le robot"
    bl_description = "Cree une collection ROBOT_<nom> qui contiendra toutes ses pieces"

    def execute(self, context):
        scene = context.scene
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", scene.rm_new_name.strip()) or "robot"

        full = COLL_PREFIX + name
        if full in bpy.data.collections:
            self.report({'WARNING'}, "Ce robot existe deja")
            scene.rm_robot = name
            return {'CANCELLED'}

        coll = bpy.data.collections.new(full)
        context.scene.collection.children.link(coll)

        # Racine : sert de poignee pour deplacer le robot entier
        root = bpy.data.objects.new(name + "_root", None)
        root.empty_display_type = 'PLAIN_AXES'
        root.empty_display_size = 0.5
        link_to_robot(root, coll, name)

        scene.rm_robot = name

        # Dossier de creation du robot, s'il y a une racine definie
        msg = "Robot '{}' cree".format(name)
        if root_path(context):
            try:
                path = robot_dir(context, name, create=True)
                coll["robot_dir"] = path
                msg += " - dossier : {}".format(path)
            except Exception as e:
                msg += " (dossier non cree : {})".format(e)

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_assign_part(bpy.types.Operator):
    bl_idname = "rm.assign_part"
    bl_label = "Rattacher au robot"
    bl_description = ("Range les objets selectionnes dans la collection du robot "
                      "et leur attribue une categorie")

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif : en creer un d'abord")
            return {'CANCELLED'}

        parts = [o for o in context.selected_objects if o.type == 'MESH']
        if not parts:
            self.report({'ERROR'}, "Selectionner au moins une piece (mesh)")
            return {'CANCELLED'}

        for obj in parts:
            link_to_robot(obj, coll, scene.rm_robot)
            obj["robot_part"] = scene.rm_category

        self.report({'INFO'}, "{} piece(s) rattachee(s)".format(len(parts)))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Points de connexion
# ---------------------------------------------------------------------------
class RM_OT_add_socket(bpy.types.Operator):
    bl_idname = "rm.add_socket"
    bl_label = "Ajouter un point"
    bl_description = ("Cree un point de connexion sur la piece active, a l'emplacement "
                      "du curseur 3D")

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        coll = active_robot_collection(context)

        # Hote : la piece active, sinon le repere visé du squelette
        if obj is None or obj.type != 'MESH' or obj.get(K_TUBE):
            obj = resolve_target(context)

        if obj is None:
            self.report({'ERROR'}, "Selectionner une piece, ou creer le squelette")
            return {'CANCELLED'}
        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        label = scene.rm_socket_name
        if label == 'custom':
            label = re.sub(r"[^A-Za-z0-9_-]+", "_", scene.rm_socket_custom.strip()) or "socket"

        empty = bpy.data.objects.new(SOCKET_PREFIX + label, None)
        empty.empty_display_type = 'SPHERE'
        empty.empty_display_size = scene.rm_socket_size
        empty[K_SOCKET] = True

        link_to_robot(empty, coll, scene.rm_robot)

        # Place au curseur 3D, puis parente sans deplacement
        empty.matrix_world = Matrix.Translation(scene.cursor.location)
        empty.parent = obj
        empty.matrix_parent_inverse = obj.matrix_world.inverted()

        deselect_all(context)
        empty.select_set(True)
        context.view_layer.objects.active = empty

        self.report({'INFO'}, "Point '{}' ajoute sur {}".format(label, obj.name))
        return {'FINISHED'}


class RM_OT_select_socket(bpy.types.Operator):
    bl_idname = "rm.select_socket"
    bl_label = "Selectionner"
    bl_description = "Selectionne ce point et le met en evidence dans la vue"

    name: bpy.props.StringProperty()
    extend: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {'CANCELLED'}

        if not self.extend:
            deselect_all(context)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # Le nom s'affiche dans la vue tant que le point est selectionne
        obj.show_name = True

        if obj.get(K_SOCKET):
            context.scene.rm_target_socket = obj.name

        if context.scene.rm_zoom_on_select and not self.extend:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type != 'VIEW_3D':
                        continue
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    if region is None:
                        continue
                    with context.temp_override(window=window, area=area, region=region):
                        bpy.ops.view3d.view_selected()
                    break

        return {'FINISHED'}


def _make_socket(context, host, label, world_pos, size):
    """Cree un empty de connexion parente a la piece."""
    scene = context.scene
    coll = active_robot_collection(context)

    empty = bpy.data.objects.new(SOCKET_PREFIX + label, None)
    empty.empty_display_type = 'SPHERE'
    empty.empty_display_size = size
    empty.show_name = scene.rm_show_names
    empty[K_SOCKET] = True

    link_to_robot(empty, coll, scene.rm_robot)

    empty.matrix_world = Matrix.Translation(world_pos)
    empty.parent = host
    empty.matrix_parent_inverse = host.matrix_world.inverted()
    return empty


class RM_OT_rename_socket(bpy.types.Operator):
    bl_idname = "rm.rename_socket"
    bl_label = "Renommer"
    bl_description = "Renomme le point de connexion actif avec le nom choisi au-dessus"

    def execute(self, context):
        scene = context.scene
        obj = context.active_object

        if obj is None or not obj.get(K_SOCKET):
            self.report({'ERROR'}, "Selectionner un point de connexion")
            return {'CANCELLED'}

        label = scene.rm_socket_name
        if label == 'custom':
            label = re.sub(r"[^A-Za-z0-9_-]+", "_", scene.rm_socket_custom.strip()) or "socket"

        obj.name = SOCKET_PREFIX + label
        self.report({'INFO'}, "Renomme en {}".format(obj.name))
        return {'FINISHED'}

class RM_OT_delete_socket(bpy.types.Operator):
    bl_idname = "rm.delete_socket"
    bl_label = "Supprimer le repere"
    bl_description = ("Supprime ce repere ainsi que les tubes qui s'y accrochent. "
                      "Les pieces qui en dependaient sont rattachees a son parent")
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        coll = active_robot_collection(context)
        sock = bpy.data.objects.get(self.name) or context.active_object

        if sock is None or not sock.get(K_SOCKET):
            self.report({'ERROR'}, "Selectionner un repere")
            return {'CANCELLED'}

        label = sock.name
        context.view_layer.update()

        # Les tubes accroches a ce repere n'ont plus de sens
        tubes = 0
        if coll is not None:
            for obj in list(coll.objects):
                if obj.get(K_TUBE) and label in (obj.get("socket_a"), obj.get("socket_b")):
                    bpy.data.objects.remove(obj)
                    tubes += 1

        # Les pieces posees dessus remontent au parent, sans bouger
        host = sock.parent
        moved = 0
        for child in list(sock.children):
            world = child.matrix_world.copy()
            child.parent = host
            if host is not None:
                child.matrix_parent_inverse = host.matrix_world.inverted()
            child.matrix_world = world
            moved += 1

        bpy.data.objects.remove(sock)
        context.view_layer.update()

        msg = "{} supprime".format(label[len(SOCKET_PREFIX):])
        if tubes:
            msg += " - {} tube(s) retire(s)".format(tubes)
        if moved:
            msg += " - {} piece(s) rattachee(s)".format(moved)
        self.report({'INFO'}, msg)
        return {'FINISHED'}

class RM_OT_reparent_socket(bpy.types.Operator):
    bl_idname = "rm.reparent_socket"
    bl_label = "Rattacher au repere"
    bl_description = ("Rattache le repere selectionne au repere du squelette choisi : "
                      "il suivra alors les proportions et les deplacements")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        sock = context.active_object
        if sock is None or not sock.get(K_SOCKET):
            self.report({'ERROR'}, "Selectionner le repere a rattacher")
            return {'CANCELLED'}

        host = slot_empty(coll, scene.rm_slot)
        if host is None:
            self.report({'ERROR'}, "Repere '{}' introuvable".format(scene.rm_slot))
            return {'CANCELLED'}
        if host == sock:
            self.report({'ERROR'}, "Un repere ne peut pas se rattacher a lui-meme")
            return {'CANCELLED'}

        context.view_layer.update()
        world = sock.matrix_world.copy()

        sock.parent = host
        sock.matrix_parent_inverse = host.matrix_world.inverted()
        sock.matrix_world = world
        context.view_layer.update()

        self.report({'INFO'}, "{} suit maintenant {}".format(
            sock.name[len(SOCKET_PREFIX):], scene.rm_slot))
        return {'FINISHED'}

class RM_OT_show_names(bpy.types.Operator):
    bl_idname = "rm.show_names"
    bl_label = "Afficher les noms"
    bl_description = "Affiche ou masque le nom des points de connexion dans la vue"

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)
        if coll is None:
            return {'CANCELLED'}

        scene.rm_show_names = not scene.rm_show_names
        for s in all_sockets(coll):
            s.show_name = scene.rm_show_names
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Squelette parametrique
# Les empties servent a la fois de reperes de montage et de poignees de
# proportions : les tubes y sont accroches, les pieces y sont parentees.
# ---------------------------------------------------------------------------
SLOTS = [
    'hips', 'chest', 'neck', 'head',
    'shoulder_L', 'elbow_L', 'wrist_L',
    'shoulder_R', 'elbow_R', 'wrist_R',
    'hip_L', 'knee_L', 'ankle_L',
    'hip_R', 'knee_R', 'ankle_R',
]

# Paires reliees par un tube
BONES = [
    ('hips', 'chest'), ('chest', 'neck'), ('neck', 'head'),
    ('chest', 'shoulder_L'), ('shoulder_L', 'elbow_L'), ('elbow_L', 'wrist_L'),
    ('chest', 'shoulder_R'), ('shoulder_R', 'elbow_R'), ('elbow_R', 'wrist_R'),
    ('hips', 'hip_L'), ('hip_L', 'knee_L'), ('knee_L', 'ankle_L'),
    ('hips', 'hip_R'), ('hip_R', 'knee_R'), ('knee_R', 'ankle_R'),
]

# Libelles courts pour le schema du panneau
SLOT_LABEL = {
    'head': "tete", 'neck': "cou", 'chest': "torse", 'hips': "bassin",
    'shoulder_L': "ep.L", 'elbow_L': "coude L", 'wrist_L': "poig.L",
    'shoulder_R': "ep.R", 'elbow_R': "coude R", 'wrist_R': "poig.R",
    'hip_L': "hanche L", 'knee_L': "genou L", 'ankle_L': "chev.L",
    'hip_R': "hanche R", 'knee_R': "genou R", 'ankle_R': "chev.R",
}

# Schema du squelette, vu de face : la gauche du robot est a droite de l'ecran
SCHEMA_ROWS = [
    ["", "", 'head', "", ""],
    ["", "", 'neck', "", ""],
    ['shoulder_R', "", 'chest', "", 'shoulder_L'],
    ['elbow_R', "", "", "", 'elbow_L'],
    ['wrist_R', "", 'hips', "", 'wrist_L'],
    ["", 'hip_R', "", 'hip_L', ""],
    ["", 'knee_R', "", 'knee_L', ""],
    ["", 'ankle_R', "", 'ankle_L', ""],
]

# Traits verticaux suggerant les tubes, intercales entre les rangees
SCHEMA_LINKS = {
    0: ["", "", "|", "", ""],
    1: ["", "", "|", "", ""],
    2: ["|", "", "|", "", "|"],
    3: ["|", "", "", "", "|"],
    4: ["", "\\", "", "/", ""],
    5: ["", "|", "", "|", ""],
    6: ["", "|", "", "|", ""],
}


# Ou poser chaque categorie de piece par defaut
CATEGORY_SLOT = {
    'BODY': 'chest',
    'HEAD': 'head',
    'HAND': 'wrist_L',
    'FOOT': 'ankle_L',
    'HINGE': 'elbow_L',
    'OTHER': 'chest',
}

# Angle auquel les pieces sont montees : les reperes de bras n'ont aucune
# rotation a cette valeur (90 = I-pose, bras le long du corps)
ARM_REF_ANGLE = 90.0

def skeleton_positions(scene):
    """Position de chaque repere, deduite des proportions."""
    hip_h = scene.rm_leg_thigh + scene.rm_leg_shin
    chest_h = hip_h + scene.rm_torso
    neck_h = chest_h + scene.rm_neck
    head_h = neck_h + scene.rm_head_gap

    sw = scene.rm_shoulder_w / 2.0
    hw = scene.rm_hip_w / 2.0

    pos = {
        'hips': Vector((0.0, 0.0, hip_h)),
        'chest': Vector((0.0, 0.0, chest_h)),
        'neck': Vector((0.0, 0.0, neck_h)),
        'head': Vector((0.0, 0.0, head_h)),
    }

    # Angle des bras : 0 = T-pose (bras horizontaux), 90 = bras le long du corps.
    # Mixamo exige une T-pose ou une A-pose pour reconnaitre les articulations.
    angle = math.radians(scene.rm_arm_angle)
    arm_dir = Vector((math.cos(angle), 0.0, -math.sin(angle)))

    for side, sign in (('L', 1.0), ('R', -1.0)):
        shoulder_z = chest_h - scene.rm_shoulder_drop
        shoulder = Vector((sign * sw, 0.0, shoulder_z))
        elbow = shoulder + Vector((sign * arm_dir.x, 0.0, arm_dir.z)) * scene.rm_arm_upper
        wrist = elbow + Vector((sign * arm_dir.x, 0.0, arm_dir.z)) * scene.rm_arm_fore

        pos['shoulder_' + side] = shoulder
        pos['elbow_' + side] = elbow
        pos['wrist_' + side] = wrist
        pos['hip_' + side] = Vector((sign * hw, 0.0, hip_h))
        pos['knee_' + side] = Vector((sign * hw, 0.0, scene.rm_leg_shin))
        pos['ankle_' + side] = Vector((sign * hw, 0.0, 0.0))

    return pos

def skeleton_matrices(scene):
    """Position et orientation de chaque repere.
    Les reperes de bras tournent avec l'angle : les pieces qui y sont
    parentees (charnieres, gants) suivent l'orientation du bras."""
    pos = skeleton_positions(scene)

    # Les pieces sont composees en I-pose : c'est donc l'angle de reference,
    # celui ou les reperes n'appliquent aucune rotation. La rotation ne joue
    # qu'a mesure qu'on s'en ecarte pour remonter les bras.
    angle = math.radians(scene.rm_arm_angle - ARM_REF_ANGLE)

    mats = {slot: Matrix.Translation(p) for slot, p in pos.items()}

    for side, sign in (('L', 1.0), ('R', -1.0)):
        rot = Matrix.Rotation(sign * angle, 4, 'Y')
        for part in ('shoulder_', 'elbow_', 'wrist_'):
            slot = part + side
            mats[slot] = Matrix.Translation(pos[slot]) @ rot

    return mats

def slot_empty(coll, slot):
    if coll is None:
        return None
    return next((o for o in coll.objects
                 if o.get(K_SOCKET) and o.get("robot_slot") == slot), None)


def _place_skeleton(scene):
    """Replace les reperes selon les proportions. Appele en direct pendant
    la manipulation des curseurs."""
    coll = None
    name = scene.rm_robot
    if name:
        coll = bpy.data.collections.get(COLL_PREFIX + name)
    if coll is None:
        return 0

    mats = skeleton_matrices(scene)
    count = 0

    for slot, m in mats.items():
        empty = slot_empty(coll, slot)
        if empty is None:
            continue
        parent = empty.parent
        if parent is not None:
            empty.matrix_world = parent.matrix_world @ m
        else:
            empty.matrix_world = m
        count += 1

    return count


def _proportion_update(self, context):
    try:
        _place_skeleton(self)
    except Exception:
        pass


class RM_OT_build_skeleton(bpy.types.Operator):
    bl_idname = "rm.build_skeleton"
    bl_label = "Creer le squelette"
    bl_description = ("Cree les reperes de montage et les tubes qui les relient. "
                      "Deplacer un repere redimensionne le robot et deplace la piece "
                      "qui y est accrochee")

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        if any(o.get("robot_slot") for o in coll.objects):
            self.report({'WARNING'}, "Ce robot a deja un squelette "
                                     "(utiliser 'Appliquer les proportions')")
            return {'CANCELLED'}

        root = next((o for o in coll.objects if o.type == 'EMPTY'
                     and o.name.endswith("_root")), None)

        mats = skeleton_matrices(scene)
        created = {}

        for slot in SLOTS:
            empty = bpy.data.objects.new(SOCKET_PREFIX + slot, None)
            empty.empty_display_type = 'SPHERE'
            empty.empty_display_size = scene.rm_socket_size
            empty.show_name = scene.rm_show_names
            empty[K_SOCKET] = True
            empty["robot_slot"] = slot

            link_to_robot(empty, coll, scene.rm_robot)
            empty.matrix_world = mats[slot]

            if root is not None:
                empty.parent = root
                empty.matrix_parent_inverse = root.matrix_world.inverted()

            created[slot] = empty

        for a, b in BONES:
            make_tube(context, created[a], created[b],
                      scene.rm_tube_radius, scene.rm_tube_res, scene.rm_tube_caps)

        self.report({'INFO'}, "Squelette cree : {} reperes, {} tubes".format(
            len(created), len(BONES)))
        return {'FINISHED'}


class RM_OT_apply_proportions(bpy.types.Operator):
    bl_idname = "rm.apply_proportions"
    bl_label = "Recaler sur les proportions"
    bl_description = ("Replace tous les reperes selon les proportions ci-dessus. "
                      "Utile apres avoir deplace des reperes a la main")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if active_robot_collection(context) is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        count = _place_skeleton(context.scene)
        self.report({'INFO'}, "{} repere(s) recale(s)".format(count))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Symetrie
# La piece miroir partage la geometrie de l'originale et n'en differe que par
# sa transformation : elle se regenere donc a l'identique en un clic.
# ---------------------------------------------------------------------------
K_MIRROR_OF = "mirror_of"        # nom de la piece source, porte par la copie
K_MIRROR_SIG = "mirror_sig"      # etat de la source au moment de la copie

MIRROR_AXIS = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}


def opposite_slot(slot):
    """wrist_L -> wrist_R, et inversement. None si le repere n'est pas lateral."""
    if slot.endswith("_L"):
        return slot[:-2] + "_R"
    if slot.endswith("_R"):
        return slot[:-2] + "_L"
    return None


def robot_root(coll):
    if coll is None:
        return None
    return next((o for o in coll.objects
                 if o.type == 'EMPTY' and o.name.endswith("_root")), None)


def mirror_matrix(context, coll):
    """Reflexion dans le plan median du robot, sur l'axe choisi."""
    axis = MIRROR_AXIS.get(context.scene.rm_mirror_axis, (1.0, 0.0, 0.0))
    flip = Matrix.Scale(-1.0, 4, Vector(axis))

    root = robot_root(coll)
    if root is None:
        return flip
    return root.matrix_world @ flip @ root.matrix_world.inverted()


def _source_signature(obj):
    """Empreinte de la piece source : transformation + geometrie."""
    parts = ["{:.6f}".format(v) for row in obj.matrix_world for v in row]

    mesh = obj.data
    verts = getattr(mesh, "vertices", None)
    if verts is not None and len(verts):
        buf = [0.0] * (len(verts) * 3)
        verts.foreach_get("co", buf)
        digest = hashlib.md5(
            ",".join("{:.5f}".format(x) for x in buf).encode("utf-8")).hexdigest()
        parts.append(digest)

    return "|".join(parts)


def mirror_children(coll):
    if coll is None:
        return []
    return [o for o in coll.objects if o.get(K_MIRROR_OF)]


def outdated_mirrors(coll):
    """Copies dont la source a change depuis la duplication."""
    result = []
    for child in mirror_children(coll):
        source = bpy.data.objects.get(child[K_MIRROR_OF])
        if source is None:
            result.append(child)
            continue
        if child.get(K_MIRROR_SIG, "") != _source_signature(source):
            result.append(child)
    return result


def make_mirror(context, obj, slot):
    """Cree la piece symetrique sur le repere oppose. Retourne (copie, message)."""
    scene = context.scene
    coll = active_robot_collection(context)

    other_slot = opposite_slot(slot or "")
    if other_slot is None:
        return None, "repere non lateral"

    target = slot_empty(coll, other_slot)
    if target is None:
        return None, "repere {} absent".format(other_slot)

    context.view_layer.update()

    # La geometrie est partagee : seule la transformation differe
    dup = obj.copy()
    dup.name = obj.name + "_mirror"
    coll.objects.link(dup)

    dup[K_ROBOT] = scene.rm_robot
    dup["robot_part"] = obj.get("robot_part", scene.rm_category)
    dup["robot_slot"] = other_slot
    dup[K_MIRROR_OF] = obj.name
    dup[K_MIRROR_SIG] = _source_signature(obj)

    world = mirror_matrix(context, coll) @ obj.matrix_world
    dup.parent = target
    dup.matrix_parent_inverse = target.matrix_world.inverted()
    dup.matrix_world = world
    context.view_layer.update()

    return dup, ""


def _mirror_after_attach(context, obj, empty):
    """Duplique la piece si l'option Mirror est active et le repere lateral."""
    if not context.scene.rm_mirror:
        return None
    slot = empty.get("robot_slot", "")
    dup, _ = make_mirror(context, obj, slot)
    return dup


def _bbox_center_world(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector((0.0, 0.0, 0.0))) / 8.0


def _attach_to_empty(context, obj, empty):
    """Pose la piece sur le repere et l'y parente. Rotation et echelle de la
    piece sont conservees telles quelles."""
    context.view_layer.update()

    target = empty.matrix_world.translation.copy()
    world = obj.matrix_world.copy()

    if context.scene.rm_snap_bbox:
        # Centre du volume sur le repere
        world.translation += target - _bbox_center_world(obj)
    else:
        # Origine de l'objet exactement sur le repere
        world.translation = target

    obj.parent = empty
    obj.matrix_parent_inverse = empty.matrix_world.inverted()
    obj.matrix_world = world
    context.view_layer.update()


class RM_OT_attach_part(bpy.types.Operator):
    bl_idname = "rm.attach_part"
    bl_label = "Placer la piece"
    bl_description = ("Deplace la piece selectionnee sur le repere choisi et l'y parente. "
                      "Elle reste librement deplacable, redimensionnable et orientable")
    bl_options = {'REGISTER', 'UNDO'}

    slot: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        if self.slot:
            empty = slot_empty(coll, self.slot)
            slot = self.slot
        else:
            empty = resolve_target(context)
            slot = empty.get("robot_slot", empty.name) if empty else scene.rm_slot

        if empty is None:
            self.report({'ERROR'}, "Repere '{}' introuvable : creer le squelette".format(slot))
            return {'CANCELLED'}

        scene.rm_target_socket = empty.name

        parts = [o for o in context.selected_objects
                 if o.type == 'MESH' and not o.get(K_TUBE)]
        if not parts:
            self.report({'ERROR'}, "Selectionner une piece (mesh)")
            return {'CANCELLED'}

        for obj in parts:
            link_to_robot(obj, coll, scene.rm_robot)
            obj["robot_part"] = scene.rm_category
            obj["robot_slot"] = slot

            _attach_to_empty(context, obj, empty)
            _mirror_after_attach(context, obj, empty)

        self.report({'INFO'}, "{} piece(s) posee(s) sur {}".format(len(parts), slot))
        return {'FINISHED'}


class RM_OT_attach_auto(bpy.types.Operator):
    bl_idname = "rm.attach_auto"
    bl_label = "Placer selon la categorie"
    bl_description = "Pose la piece sur le repere correspondant a sa categorie"

    def execute(self, context):
        slot = CATEGORY_SLOT.get(context.scene.rm_category, 'chest')
        return bpy.ops.rm.attach_part(slot=slot)


# ---------------------------------------------------------------------------
# Tubes de liaison
# ---------------------------------------------------------------------------
def make_tube(context, sock_a, sock_b, radius, resolution, caps):
    """Cree une courbe a deux points, accrochee aux deux points de connexion.
    Les Hook modifiers font suivre le tube quand les pieces bougent."""
    scene = context.scene
    coll = active_robot_collection(context)

    name = TUBE_PREFIX + sock_a.name[len(SOCKET_PREFIX):] + "_" + sock_b.name[len(SOCKET_PREFIX):]

    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = radius
    curve.bevel_resolution = resolution
    curve.use_fill_caps = caps
    
    if scene.rm_tube_material is not None:
        curve.materials.append(scene.rm_tube_material)

    spline = curve.splines.new('POLY')
    spline.points.add(1)

    a = socket_world(sock_a)
    b = socket_world(sock_b)
    spline.points[0].co = (a.x, a.y, a.z, 1.0)
    spline.points[1].co = (b.x, b.y, b.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    obj.matrix_world = Matrix.Identity(4)     # points exprimes en coordonnees monde
    obj[K_TUBE] = True
    obj["socket_a"] = sock_a.name
    obj["socket_b"] = sock_b.name
    link_to_robot(obj, coll, scene.rm_robot)

    for index, target in ((0, sock_a), (1, sock_b)):
        mod = obj.modifiers.new("Hook_{}".format(index), 'HOOK')
        mod.object = target
        mod.matrix_inverse = target.matrix_world.inverted()
        mod.vertex_indices_set([index])

    return obj


class RM_OT_make_tube(bpy.types.Operator):
    bl_idname = "rm.make_tube"
    bl_label = "Relier"
    bl_description = ("Cree un tube entre les deux points de connexion selectionnes. "
                      "Le tube suit ensuite les pieces automatiquement")

    def execute(self, context):
        scene = context.scene
        socks = selected_sockets(context)

        if active_robot_collection(context) is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}
        if len(socks) != 2:
            self.report({'ERROR'}, "Selectionner exactement 2 points de connexion "
                                   "({} selectionne(s))".format(len(socks)))
            return {'CANCELLED'}

        tube = make_tube(context, socks[0], socks[1],
                         scene.rm_tube_radius, scene.rm_tube_res, scene.rm_tube_caps)
        self.report({'INFO'}, "Tube {} cree".format(tube.name))
        return {'FINISHED'}


class RM_OT_auto_tubes(bpy.types.Operator):
    bl_idname = "rm.auto_tubes"
    bl_label = "Relier automatiquement"
    bl_description = ("Relie les points de meme nom portes par des pieces differentes "
                      "(SKT_elbow_L d'un bras avec SKT_elbow_L de l'avant-bras)")

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        # Regroupe par nom de point, en ignorant le suffixe .001 de Blender
        groups = {}
        for s in all_sockets(coll):
            label = re.sub(r"\.\d+$", "", s.name[len(SOCKET_PREFIX):])
            groups.setdefault(label, []).append(s)

        existing = {frozenset((t.get("socket_a"), t.get("socket_b")))
                    for t in coll.objects if t.get(K_TUBE)}

        created = 0
        skipped = []
        for label, socks in groups.items():
            if len(socks) < 2:
                continue
            if len(socks) > 2:
                skipped.append(label)
                continue
            if socks[0].parent is not None and socks[0].parent == socks[1].parent:
                continue        # deux points sur la meme piece : rien a relier
            if frozenset((socks[0].name, socks[1].name)) in existing:
                continue

            make_tube(context, socks[0], socks[1],
                      scene.rm_tube_radius, scene.rm_tube_res, scene.rm_tube_caps)
            created += 1

        msg = "{} tube(s) cree(s)".format(created)
        if skipped:
            msg += " - ambigus (plus de 2 points) : " + ", ".join(sorted(skipped))
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_update_tubes(bpy.types.Operator):
    bl_idname = "rm.update_tubes"
    bl_label = "Mettre a jour l'epaisseur"
    bl_description = "Applique le rayon et la resolution courants a tous les tubes du robot"

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)
        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        count = 0
        for obj in coll.objects:
            if obj.get(K_TUBE) and obj.type == 'CURVE':
                obj.data.bevel_depth = scene.rm_tube_radius
                obj.data.bevel_resolution = scene.rm_tube_res
                obj.data.use_fill_caps = scene.rm_tube_caps
                count += 1

        self.report({'INFO'}, "{} tube(s) mis a jour".format(count))
        return {'FINISHED'}

class RM_OT_tube_material(bpy.types.Operator):
    bl_idname = "rm.tube_material"
    bl_label = "Appliquer aux tubes"
    bl_description = ("Affecte le materiau a tous les tubes du robot. Sans materiau, "
                      "ils reviennent de Mixamo sans aspect defini")

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        mat = scene.rm_tube_material
        if mat is None:
            mat = bpy.data.materials.get("ROBOT_tube")
            if mat is None:
                mat = bpy.data.materials.new("ROBOT_tube")
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf is not None:
                    bsdf.inputs["Base Color"].default_value = (0.62, 0.64, 0.66, 1.0)
                    bsdf.inputs["Metallic"].default_value = 0.85
                    bsdf.inputs["Roughness"].default_value = 0.35
            scene.rm_tube_material = mat

        count = 0
        for obj in coll.objects:
            if obj.get(K_TUBE) and obj.data is not None:
                obj.data.materials.clear()
                obj.data.materials.append(mat)
                count += 1

        self.report({'INFO'}, "'{}' applique a {} tube(s)".format(mat.name, count))
        return {'FINISHED'}

class RM_OT_rebuild_tubes(bpy.types.Operator):
    bl_idname = "rm.rebuild_tubes"
    bl_label = "Regenerer les tubes"
    bl_description = ("Supprime les tubes existants et les recree en courbes vivantes. "
                      "A utiliser si des tubes ont ete convertis en mesh par erreur")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        for obj in [o for o in coll.objects if o.get(K_TUBE)]:
            bpy.data.objects.remove(obj)

        created = 0
        for a, b in BONES:
            ea, eb = slot_empty(coll, a), slot_empty(coll, b)
            if ea is None or eb is None:
                continue
            make_tube(context, ea, eb,
                      scene.rm_tube_radius, scene.rm_tube_res, scene.rm_tube_caps)
            created += 1

        self.report({'INFO'}, "{} tube(s) regeneres".format(created))
        return {'FINISHED'}

class RM_OT_convert_tubes(bpy.types.Operator):
    bl_idname = "rm.convert_tubes"
    bl_label = "Convertir les tubes en mesh"
    bl_description = ("Fige les tubes en maillage. A faire quand l'assemblage est valide : "
                      "les tubes ne suivront plus les pieces")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = active_robot_collection(context)
        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        tubes = [o for o in coll.objects if o.get(K_TUBE) and o.type == 'CURVE']
        if not tubes:
            self.report({'WARNING'}, "Aucun tube a convertir")
            return {'CANCELLED'}

        deselect_all(context)
        for t in tubes:
            t.select_set(True)
        context.view_layer.objects.active = tubes[0]
        bpy.ops.object.convert(target='MESH')

        self.report({'INFO'}, "{} tube(s) converti(s)".format(len(tubes)))
        return {'FINISHED'}

# ---------------------------------------------------------------------------
# Export vers Mixamo
# ---------------------------------------------------------------------------
MIXAMO_URL = "https://www.mixamo.com/#/"

MIXAMO_STEPS = [
    ("1. Preparer", [
        "Bras en T-pose (angle 0) et tubes convertis en mesh.",
        "Le bouton Preparer pour Mixamo fait les deux, puis exporte le FBX.",
    ]),
    ("2. Rigger sur Mixamo", [
        "Se connecter, onglet Animations, colonne de droite : Upload Character.",
        "Glisser le .fbx dans la boite de dialogue, puis Next.",
        "Auto Rigger : placer les reperes chin, wrists, elbows, knees, groin.",
        "Sur un robot, viser le centre du volume de la zone plutot qu'un detail.",
        "Next : verifier l'animation de controle, sinon revenir et corriger.",
        "Download Character > Pose : Original Pose (.fbx) > Download.",
    ]),
    ("3. Reimporter dans Blender", [
        "Ecarter le robot d'origine, File > Import > FBX, choisir le fichier telecharge.",
        "Verifier : selectionner l'armature, Pose Mode, R sur un bone.",
        "Supprimer ensuite les meshs du robot d'origine.",
    ]),
    ("4. Controleurs IK/FK", [
        "Addon Mixamo Control Rig (github.com/BlenderBoi/mixamo_blender).",
        "Touche N, onglet Mixamo, armature selectionnee : Create Control Rig.",
        "Bascule IK / FK dans Mixamo Rig Settings.",
    ]),
]


class RM_OT_mixamo_info(bpy.types.Operator):
    bl_idname = "rm.mixamo_info"
    bl_label = "Process de rigging Mixamo"
    bl_description = "Rappelle les etapes du rigging sur Mixamo"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        for title, lines in MIXAMO_STEPS:
            box = layout.box()
            box.label(text=title, icon='DOT')
            col = box.column(align=True)
            col.scale_y = 0.8
            for line in lines:
                col.label(text=line)

def missing_materials(coll):
    """Objets du robot sans materiau. Retourne (tubes, pieces)."""
    tubes, parts = [], []

    if coll is None:
        return tubes, parts

    for obj in coll.objects:
        if obj.type not in {'MESH', 'CURVE'}:
            continue

        data = obj.data
        slots = [m for m in getattr(data, "materials", []) if m is not None]
        if slots:
            continue

        if obj.get(K_TUBE):
            tubes.append(obj.name)
        else:
            parts.append(obj.name)

    return tubes, parts

class RM_OT_prepare_mixamo(bpy.types.Operator):
    bl_idname = "rm.prepare_mixamo"
    bl_label = "Preparer pour Mixamo"
    bl_description = ("Remet le robot en T-pose, convertit les tubes en mesh et "
                      "exporte un FBX pret pour l'auto-rigger")

    export_pose: bpy.props.EnumProperty(
        name="Pose", default='A',
        items=[('T', "T-pose (0)", "Bras horizontaux"),
               ('A', "A-pose (45)", "Bras a 45 degres : aide l'auto-rigger a separer "
                                    "le bras du torse et a trouver l'epaule"),
               ('KEEP', "Ne pas changer", "Exporter dans la pose actuelle")])
    do_export: bpy.props.BoolProperty(name="Exporter le FBX", default=True)
    
    ignore_materials: bpy.props.BoolProperty(
        name="Ignorer les materiaux manquants", default=False,
        description="Exporte meme si des pieces ou des tubes n'ont pas de materiau")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}

        steps = []

        if self.export_pose != 'KEEP':
            scene.rm_arm_angle = 0.0 if self.export_pose == 'T' else 45.0
            _place_skeleton(scene)
            context.view_layer.update()
            steps.append("T-pose" if self.export_pose == 'T' else "A-pose")

        if not self.do_export:
            self.report({'INFO'}, " - ".join(steps) or "Rien a faire")
            return {'FINISHED'}

        sources = [o for o in coll.objects if o.type in {'MESH', 'CURVE'}]
        if not sources:
            self.report({'ERROR'}, "Aucune geometrie a exporter")
            return {'CANCELLED'}
        no_tube_mat, no_part_mat = missing_materials(coll)
        if (no_tube_mat or no_part_mat) and not self.ignore_materials:
            names = (no_tube_mat + no_part_mat)[:4]
            total = len(no_tube_mat) + len(no_part_mat)
            self.report({'ERROR'},
                        "{} objet(s) sans materiau : {}{} - ils reviendront de Mixamo "
                        "sans aspect. Cocher 'Ignorer' pour exporter quand meme".format(
                            total, ", ".join(names), " ..." if total > 4 else ""))
            return {'CANCELLED'}

        # Le FBX destine a l'auto-rigger vit dans le sous-dossier prototype
        base = robot_dir(context, scene.rm_robot, create=True)
        if base:
            folder = os.path.join(base, "prototype")
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception:
                folder = base
        else:
            folder = bpy.path.abspath("//") or bpy.app.tempdir

        path = os.path.join(folder, scene.rm_robot + "_mixamo.fbx")

        # Les pieces sont parentees aux reperes : exportees telles quelles, elles
        # perdraient leur position (les empties ne partent pas dans le FBX).
        # On travaille donc sur des copies detachees, fusionnees en un seul
        # maillage - ce que l'auto-rigger attend.
        # Les originaux ne sont jamais touches : on evalue chaque objet
        # (modifiers et hooks appliques, courbes converties en maillage)
        # pour en tirer une copie figee, fusionnee ensuite en un seul mesh.
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()

        temp = []
        for obj in sources:
            try:
                evaluated = obj.evaluated_get(depsgraph)
                mesh = bpy.data.meshes.new_from_object(evaluated)
            except Exception:
                continue
            if mesh is None or len(mesh.vertices) == 0:
                if mesh is not None:
                    bpy.data.meshes.remove(mesh)
                continue

            dup = bpy.data.objects.new(obj.name + "_export", mesh)
            dup.matrix_world = obj.matrix_world.copy()
            context.scene.collection.objects.link(dup)
            temp.append(dup)

        if not temp:
            self.report({'ERROR'}, "Aucune geometrie exploitable")
            return {'CANCELLED'}

        deselect_all(context)
        for dup in temp:
            dup.select_set(True)
        context.view_layer.objects.active = temp[0]

        if len(temp) > 1:
            bpy.ops.object.join()

        merged = context.view_layer.objects.active
        merged.name = scene.rm_robot + "_mixamo"

        error = ""
        try:
            bpy.ops.export_scene.fbx(
                filepath=path,
                use_selection=True,
                object_types={'MESH'},
                apply_unit_scale=True,
                bake_space_transform=False,
                mesh_smooth_type='FACE',
                path_mode='COPY',
                embed_textures=True,
            )
        except Exception as e:
            error = str(e)

        merged_data = merged.data
        bpy.data.objects.remove(merged)
        bpy.data.meshes.remove(merged_data)

        if error:
            self.report({'ERROR'}, "Export impossible : {}".format(error))
            return {'CANCELLED'}

        steps.append("{} piece(s) fusionnees".format(len(temp)))
        self.report({'INFO'}, " - ".join(steps) + " -> " + path)
        return {'FINISHED'}

# ---------------------------------------------------------------------------

def draw_schema(layout, context, coll):
    """Schema du squelette : chaque repere est un bouton qui le selectionne
    dans la scene et le designe comme cible de placement."""
    scene = context.scene
    col = layout.column(align=True)

    for index, row_slots in enumerate(SCHEMA_ROWS):
        r = col.row(align=True)
        for slot in row_slots:
            if not slot:
                r.label(text="")
                continue

            empty = slot_empty(coll, slot)
            if empty is None:
                r.label(text="")
                continue

            selected = empty.select_get()
            r.operator("rm.select_socket",
                       text=SLOT_LABEL.get(slot, slot),
                       icon='RADIOBUT_ON' if selected else 'RADIOBUT_OFF',
                       depress=(scene.rm_target_socket == empty.name)).name = empty.name

        # Traits de liaison entre deux rangees
        links = SCHEMA_LINKS.get(index)
        if links:
            lr = col.row(align=True)
            lr.scale_y = 0.35
            lr.enabled = False
            for mark in links:
                lr.label(text=mark)


# ---------------------------------------------------------------------------
# Panneau
# ---------------------------------------------------------------------------
class RM_PT_panel(bpy.types.Panel):
    bl_label = "Robot Maker"
    bl_idname = "RM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Robot Maker"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        coll = active_robot_collection(context)

        # --- Dossiers ---
        root = root_path(context)
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Dossiers", icon='FILE_FOLDER')
        if root:
            op = row.operator("rm.open_folder", text="", icon='FILEBROWSER')
            op.which = 'ROOT'

        if context.scene.world is None:
            warn = box.row()
            warn.alert = True
            warn.operator("rm.setup_scene", icon='LIGHT_SUN')

        if not root:
            box.label(text="Racine non definie (preferences)", icon='ERROR')
        else:
            r = box.row(align=True)
            r.operator("rm.init_folders", icon='NEWFOLDER')
            op = r.operator("rm.open_folder", text="Library", icon='ASSET_MANAGER')
            op.which = 'LIBRARY'

        # --- Robot ---
        box = layout.box()
        box.label(text="Robot", icon='OUTLINER_COLLECTION')
        row = box.row(align=True)
        row.prop(scene, "rm_new_name", text="")
        row.operator("rm.new_robot", text="", icon='ADD')

        if robot_collections():
            box.prop(scene, "rm_robot", text="")

        if coll is None:
            box.label(text="Aucun robot actif", icon='ERROR')
            return

        n_parts = len([o for o in coll.objects if o.type == 'MESH' and not o.get(K_TUBE)])
        n_socks = len(all_sockets(coll))
        n_tubes = len([o for o in coll.objects if o.get(K_TUBE)])
        sub = box.row()
        sub.scale_y = 0.7
        sub.label(text="{} piece(s) - {} point(s) - {} tube(s)".format(n_parts, n_socks, n_tubes))

        if root:
            r = box.row(align=True)
            op = r.operator("rm.open_folder", text="Dossier du robot", icon='FILEBROWSER')
            op.which = 'ROBOT'
            r.operator("rm.open_expression_maker", text="", icon='URL')

        # --- Squelette ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Squelette", icon='ARMATURE_DATA')
        row.operator("rm.show_names", text="",
                     icon='HIDE_OFF' if scene.rm_show_names else 'HIDE_ON')

        has_skel = slot_empty(coll, 'chest') is not None

        if not has_skel:
            box.operator("rm.build_skeleton", icon='ADD')

        prop_col = box.column(align=True)
        r = prop_col.row(align=True)
        r.prop(scene, "rm_shoulder_w")
        r.prop(scene, "rm_hip_w")
        r = prop_col.row(align=True)
        r.prop(scene, "rm_torso")
        r.prop(scene, "rm_shoulder_drop")
        r = prop_col.row(align=True)
        r.prop(scene, "rm_neck")
        r.prop(scene, "rm_head_gap")
        r = prop_col.row(align=True)
        r.prop(scene, "rm_arm_upper")
        r.prop(scene, "rm_arm_fore")
        prop_col.prop(scene, "rm_arm_angle", slider=True)
        if scene.rm_arm_angle > 60.0:
            sub = prop_col.row()
            sub.scale_y = 0.7
            sub.label(text="I-pose : remettre a 0 avant l'export Mixamo", icon='INFO')
        r = prop_col.row(align=True)
        r.prop(scene, "rm_leg_thigh")
        r.prop(scene, "rm_leg_shin")

        if has_skel:
            box.operator("rm.apply_proportions", icon='FILE_REFRESH')
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Les reperes restent deplacables a la main")

        # --- Bibliotheque ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Bibliotheque", icon='ASSET_MANAGER')
        row.prop(scene, "rm_asset_edit", text="", icon='TRASH', toggle=True)
        row.operator("rm.scan_library", text="", icon='FILE_REFRESH')

        box.prop(scene, "rm_category", text="")

        if not root:
            box.label(text="Racine non definie (preferences)", icon='ERROR')
        else:
            assets = filtered_assets(scene)
            all_count = len(_assets.get(scene.rm_category, []))

            if all_count > scene.rm_asset_per_page or scene.rm_asset_search:
                box.prop(scene, "rm_asset_search", text="", icon='VIEWZOOM')

            if assets:
                # Vignettes si au moins une existe, sinon liste deroulante :
                # une grille d'icones vide serait inutilisable
                has_thumb = any(
                    _asset_previews is not None
                    and (scene.rm_category + "/" + n) in _asset_previews
                    for n, _ in assets)

                if has_thumb:
                    # Pagination : au-dela de quelques dizaines d'assets, tout
                    # afficher rendrait le panneau interminable
                    per = max(1, scene.rm_asset_per_page)
                    pages = page_count(scene, len(assets))
                    page = min(scene.rm_asset_page, pages - 1)
                    shown = assets[page * per:(page + 1) * per]

                    # Grille toujours depliee : un clic sur une vignette pose la piece
                    grid = box.grid_flow(row_major=True, columns=scene.rm_asset_columns,
                                         even_columns=True, align=False)
                    grid.enabled = has_skel

                    for aname, apath in shown:
                        cell = grid.box()
                        cell.scale_y = 0.9
                        icon = 0
                        if _asset_previews is not None:
                            prev = _asset_previews.get(scene.rm_category + "/" + aname)
                            if prev:
                                icon = prev.icon_id

                        if icon:
                            cell.template_icon(icon_value=icon, scale=scene.rm_asset_scale)

                        line = cell.row(align=True)
                        op = line.operator("rm.place_asset", text=aname,
                                           icon='IMPORT' if not icon else 'NONE')
                        op.asset = aname
                        if scene.rm_asset_edit:
                            op = line.operator("rm.delete_asset", text="", icon='TRASH')
                            op.asset = aname

                    if pages > 1:
                        nav = box.row(align=True)
                        op = nav.operator("rm.asset_page", text="", icon='TRIA_LEFT')
                        op.delta = -1
                        nav.label(text="{} / {}  ({} assets)".format(
                            page + 1, pages, len(assets)))
                        op = nav.operator("rm.asset_page", text="", icon='TRIA_RIGHT')
                        op.delta = 1

                    r = box.row(align=True)
                    r.prop(scene, "rm_asset_columns", text="Colonnes")
                    r.prop(scene, "rm_asset_scale", text="Taille")
                    if all_count > 12:
                        box.prop(scene, "rm_asset_per_page")
                else:
                    box.prop(scene, "rm_asset", text="")
                    box.prop(scene, "rm_place_on_click")
                    sub = box.row()
                    sub.scale_y = 0.7
                    sub.label(text="Aucune vignette pour cette categorie", icon='INFO')

                    r = box.row(align=True)
                    sub = r.row()
                    sub.enabled = has_skel
                    sub.operator("rm.place_asset", icon='IMPORT')
                    if scene.rm_asset_edit:
                        r.operator("rm.delete_asset", text="", icon='TRASH')
            elif scene.rm_asset_search:
                box.label(text="Aucun asset ne correspond a la recherche", icon='INFO')
            else:
                box.label(text="Categorie vide - relire la bibliotheque", icon='INFO')

        box.separator()
        col = box.column(align=True)
        col.label(text="Ajouter la piece selectionnee :")

        r = col.row(align=True)
        r.prop(scene, "rm_asset_name", text="")
        r.prop(scene, "rm_asset_overwrite")
        col.prop(scene, "rm_asset_freeze")
        col.prop(scene, "rm_thumb_size")
        col.operator("rm.add_to_library", icon='EXPORT')

        # --- Placement ---
        box = layout.box()
        box.label(text="Placement", icon='MESH_CUBE')
        box.prop(scene, "rm_snap_bbox")

        target = resolve_target(context)
        sub = box.row(align=True)
        if target is not None:
            live = bool(selected_sockets(context))
            sub.label(text="Cible : " + target.name[len(SOCKET_PREFIX):],
                      icon='RADIOBUT_ON' if live else 'PINNED')
            op = sub.operator("rm.select_socket", text="", icon='RESTRICT_SELECT_OFF')
            op.name = target.name
            op.extend = False
        else:
            sub.label(text="Cible : aucune", icon='RADIOBUT_OFF')

        col = box.column(align=True)
        col.enabled = has_skel
        col.operator("rm.attach_auto", icon='AUTOMERGE_ON')

        r = col.row(align=True)
        r.prop(scene, "rm_slot", text="")
        op = r.operator("rm.attach_part", text="Placer")
        op.slot = ""

        if not has_skel:
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Creer le squelette d'abord", icon='INFO')

        box.operator("rm.assign_part", text="Rattacher sans placer", icon='LINKED')

        # Symetrie
        box.separator()
        r = box.row(align=True)
        r.prop(scene, "rm_mirror", icon='MOD_MIRROR', toggle=True)
        sub = r.row(align=True)
        sub.enabled = scene.rm_mirror
        sub.prop(scene, "rm_mirror_axis", expand=True)

        mirrors = mirror_children(coll)
        if mirrors:
            stale = outdated_mirrors(coll)
            r = box.row()
            r.alert = bool(stale)
            r.enabled = bool(stale)
            r.operator("rm.update_mirrors",
                       text="Mettre a jour ({})".format(len(stale)) if stale
                       else "Symetries a jour",
                       icon='FILE_REFRESH')

        sel = [o for o in context.selected_objects
               if o.type == 'MESH' and not o.get(K_TUBE) and not o.get(K_MIRROR_OF)]
        if sel:
            box.operator("rm.mirror_selected", icon='MOD_MIRROR')

        # --- Reperes ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Reperes", icon='EMPTY_DATA')
        row.prop(scene, "rm_zoom_on_select", text="", icon='ZOOM_SELECTED')
        row.operator("rm.show_names", text="",
                     icon='HIDE_OFF' if scene.rm_show_names else 'HIDE_ON')

        if has_skel:
            target = resolve_target(context)
            sub = box.row()
            sub.scale_y = 0.8
            sub.label(text=("Cible : " + target.name[len(SOCKET_PREFIX):]) if target
                      else "Aucune cible",
                      icon='PINNED' if target else 'RADIOBUT_OFF')

            draw_schema(box, context, coll)
        else:
            box.label(text="Creer le squelette pour afficher le schema", icon='INFO')

        # Reperes hors squelette, ajoutes a la main
        extras = [s for s in all_sockets(coll) if not s.get("robot_slot")]
        if extras:
            box.separator()
            box.label(text="Autres reperes :")
            lst = box.column(align=True)
            for s in sorted(extras, key=lambda o: o.name):
                r = lst.row(align=True)
                icon = 'RADIOBUT_ON' if s.select_get() else 'RADIOBUT_OFF'
                op = r.operator("rm.select_socket",
                                text=s.name[len(SOCKET_PREFIX):], icon=icon)
                op.name = s.name
                op.extend = False
                op = r.operator("rm.select_socket", text="", icon='SELECT_EXTEND')
                op.name = s.name
                op.extend = True
                r.operator("rm.delete_socket", text="", icon='TRASH').name = s.name

        # Ajout d'un repere au curseur 3D
        box.separator()
        col = box.column(align=True)
        col.label(text="Ajouter un repere (curseur 3D) :")
        col.prop(scene, "rm_socket_name", text="")
        if scene.rm_socket_name == 'custom':
            col.prop(scene, "rm_socket_custom", text="")
        col.prop(scene, "rm_socket_size", text="Taille")
        col.operator("rm.add_socket", icon='ADD')

        obj = context.active_object
        if obj is not None and obj.get(K_SOCKET):
            col.separator()
            r = col.row(align=True)
            r.prop(obj, "name", text="")
            r.operator("rm.delete_socket", text="", icon='TRASH').name = obj.name

            col.operator("rm.rename_socket", icon='GREASEPENCIL')

            parent_name = obj.parent.name if obj.parent else "aucun"
            sub = col.row()
            sub.scale_y = 0.7
            sub.label(text="Suit : " + parent_name, icon='CON_CHILDOF')

            r = col.row(align=True)
            r.prop(scene, "rm_slot", text="")
            r.operator("rm.reparent_socket", text="Rattacher")

        # --- Tubes ---
        box = layout.box()
        box.label(text="Tubes de liaison", icon='CURVE_PATH')
        row = box.row(align=True)
        row.prop(scene, "rm_tube_radius", text="Rayon")
        row.prop(scene, "rm_tube_res", text="Lisse")
        box.prop(scene, "rm_tube_caps")
        row = box.row(align=True)
        row.template_ID(scene, "rm_tube_material", new="material.new")
        box.operator("rm.tube_material", icon='MATERIAL')

        n_sel = len(selected_sockets(context))
        col = box.column()
        col.enabled = (n_sel == 2)
        col.operator("rm.make_tube", icon='CURVE_PATH')
        if n_sel != 2:
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="{} point(s) selectionne(s) sur 2".format(n_sel))

        box.operator("rm.auto_tubes", icon='AUTOMERGE_ON')
        box.operator("rm.update_tubes", icon='FILE_REFRESH')

        # --- Finalisation ---
        box = layout.box()
        box.label(text="Finalisation", icon='CHECKMARK')
        no_tube_mat, no_part_mat = missing_materials(coll)
        if no_tube_mat or no_part_mat:
            warn = box.column(align=True)
            warn.alert = True
            warn.label(text="Sans materiau :", icon='ERROR')

            sub = warn.column(align=True)
            sub.scale_y = 0.7
            if no_tube_mat:
                sub.label(text="{} tube(s) : {}".format(
                    len(no_tube_mat), ", ".join(no_tube_mat[:3])
                    + (" ..." if len(no_tube_mat) > 3 else "")))
            for name in no_part_mat[:6]:
                sub.label(text=name)
            if len(no_part_mat) > 6:
                sub.label(text="... et {} autre(s)".format(len(no_part_mat) - 6))

            if no_tube_mat:
                warn.operator("rm.tube_material", icon='MATERIAL')

        box.operator("rm.rebuild_tubes", icon='FILE_REFRESH')
        sub = box.row()
        sub.scale_y = 0.7
        sub.label(text="L'export Mixamo n'a plus besoin de conversion", icon='INFO')
        box.operator("rm.convert_tubes", icon='MESH_DATA')

        # --- Mixamo ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Mixamo", icon='ARMATURE_DATA')
        row.operator("rm.mixamo_info", text="", icon='INFO')

        box.operator("rm.prepare_mixamo", icon='EXPORT')
        box.operator("wm.url_open", text="Ouvrir Mixamo", icon='URL').url = MIXAMO_URL

        r = box.row(align=True)
        op = r.operator("rm.open_folder", text="Prototype", icon='FILEBROWSER')
        op.which = 'PROTOTYPE'
        op = r.operator("rm.open_folder", text="Mixamo rigged", icon='FILEBROWSER')
        op.which = 'RIGGED'

        if scene.rm_arm_angle > 0.0:
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Bras a {:.0f} deg - la preparation les remettra a 0".format(
                scene.rm_arm_angle), icon='INFO')


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------
classes = (
    RM_Preferences,
    RM_OT_init_folders,
    RM_OT_open_folder,
    RM_OT_open_expression_maker,
    RM_OT_scan_library,
    RM_OT_add_to_library,
    RM_OT_place_asset,
    RM_OT_delete_asset,
    RM_OT_update_mirrors,
    RM_OT_mirror_selected,
    RM_OT_asset_page,
    RM_OT_new_robot,
    RM_OT_assign_part,
    RM_OT_add_socket,
    RM_OT_select_socket,
    RM_OT_build_skeleton,
    RM_OT_apply_proportions,
    RM_OT_attach_part,
    RM_OT_attach_auto,
    RM_OT_rename_socket,
    RM_OT_delete_socket,
    RM_OT_reparent_socket,
    RM_OT_show_names,
    RM_OT_make_tube,
    RM_OT_auto_tubes,
    RM_OT_update_tubes,
    RM_OT_rebuild_tubes,
    RM_OT_convert_tubes,
    RM_OT_tube_material,
    RM_OT_prepare_mixamo,
    RM_OT_mixamo_info,
    RM_PT_panel,
    RM_OT_setup_scene,
)


def register():
    global _asset_previews

    for cls in classes:
        bpy.utils.register_class(cls)

    _asset_previews = bpy.utils.previews.new()

    if _on_load_scan not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_scan)

    # Differe : pendant register(), les preferences de l'addon ne sont pas lues
    if not bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.register(_deferred_scan, first_interval=0.5)
    _subscribe_selection()
    if _on_load_selection not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_selection)
    if not bpy.app.timers.is_registered(_poll_selection):
        bpy.app.timers.register(_poll_selection, first_interval=1.0, persistent=True)

    S = bpy.types.Scene
    S.rm_new_name = bpy.props.StringProperty(name="Nom", default="robot_01")
    S.rm_robot = bpy.props.EnumProperty(name="Robot actif", items=robot_enum)
    S.rm_category = bpy.props.EnumProperty(name="Categorie", items=PART_CATEGORIES,
                                           default='BODY', update=_reset_page)
    S.rm_socket_name = bpy.props.EnumProperty(
        name="Point", default="shoulder_L",
        items=[(n, n.replace("_", " "), "") for n in SOCKET_PRESETS])
    S.rm_socket_custom = bpy.props.StringProperty(name="Nom du point", default="socket")
    S.rm_socket_size = bpy.props.FloatProperty(name="Taille", default=0.05, min=0.001, max=2.0)
    S.rm_asset = bpy.props.EnumProperty(name="Asset", items=asset_enum,
                                       update=_asset_changed)
    S.rm_asset_name = bpy.props.StringProperty(
        name="Nom", default="",
        description="Nom de l'asset dans la bibliotheque (vide = nom de l'objet)")
    S.rm_asset_overwrite = bpy.props.BoolProperty(name="Ecraser", default=False)
    S.rm_asset_freeze = bpy.props.BoolProperty(
        name="Figer la transformation", default=True,
        description=("Passe la rotation et l'echelle dans la geometrie : l'asset revient "
                     "exactement a la taille voulue au reimport"))
    S.rm_target_socket = bpy.props.StringProperty(
        name="Repere vise", default="",
        description="Dernier repere utilise, conserve entre deux placements")
    S.rm_place_on_click = bpy.props.BoolProperty(
        name="Placer au clic", default=True,
        description="Importe et pose l'asset des sa selection dans la grille")
    S.rm_asset_scale = bpy.props.FloatProperty(name="Taille vignettes", default=4.0,
                                               min=1.0, max=10.0)
    S.rm_asset_edit = bpy.props.BoolProperty(
        name="Mode gestion", default=False,
        description="Affiche un bouton de suppression sur chaque asset")
    S.rm_asset_search = bpy.props.StringProperty(
        name="Rechercher", default="", options={'TEXTEDIT_UPDATE'},
        update=_reset_page,
        description="Filtre les assets de la categorie par leur nom")
    S.rm_asset_page = bpy.props.IntProperty(name="Page", default=0, min=0)
    S.rm_asset_per_page = bpy.props.IntProperty(
        name="Par page", default=12, min=3, max=60,
        update=_reset_page,
        description="Nombre de vignettes affichees a la fois")
    S.rm_asset_columns = bpy.props.IntProperty(
        name="Colonnes", default=3, min=1, max=6,
        description="Nombre de vignettes par ligne")
    S.rm_thumb_size = bpy.props.IntProperty(
        name="Resolution vignette", default=256, min=64, max=512,
        description="Taille en pixels des vignettes generees")
    S.rm_slot = bpy.props.EnumProperty(
        name="Repere", default='chest',
        items=[(s, s.replace("_", " "), "") for s in SLOTS])
    S.rm_mirror = bpy.props.BoolProperty(
        name="Mirror", default=False,
        description=("Sur un repere lateral (_L ou _R), duplique la piece en symetrie "
                     "sur le repere oppose"))
    S.rm_mirror_axis = bpy.props.EnumProperty(
        name="Axe", default='X',
        items=[('X', "X", "Symetrie gauche/droite standard"),
               ('Y', "Y", ""), ('Z', "Z", "")],
        description="Axe du plan de symetrie, dans le repere de la racine du robot")
    S.rm_snap_bbox = bpy.props.BoolProperty(
        name="Centrer sur le volume", default=False,
        description=("Aligne le centre du volume de la piece sur le repere, plutot que "
                     "son origine (utile pour les modeles importes)"))

    # Proportions, en metres
    S.rm_torso = bpy.props.FloatProperty(update=_proportion_update, name="Torse", default=0.55, min=0.01)
    S.rm_neck = bpy.props.FloatProperty(update=_proportion_update, name="Cou", default=0.12, min=0.0)
    S.rm_head_gap = bpy.props.FloatProperty(update=_proportion_update, name="Tete", default=0.22, min=0.01)
    S.rm_shoulder_w = bpy.props.FloatProperty(update=_proportion_update, name="Largeur epaules", default=0.50, min=0.01)
    S.rm_shoulder_drop = bpy.props.FloatProperty(update=_proportion_update, name="Hauteur epaules", default=0.05)
    S.rm_hip_w = bpy.props.FloatProperty(update=_proportion_update, name="Largeur hanches", default=0.28, min=0.01)
    S.rm_arm_upper = bpy.props.FloatProperty(update=_proportion_update, name="Bras", default=0.32, min=0.01)
    S.rm_arm_fore = bpy.props.FloatProperty(update=_proportion_update, name="Avant-bras", default=0.30, min=0.01)
    S.rm_arm_angle = bpy.props.FloatProperty(
        update=_proportion_update, name="Angle des bras", default=90.0, min=0.0, max=90.0,
        description=("0 = T-pose, 45 = A-pose, 90 = bras le long du corps. "
                     "Mixamo demande 0 ou 45 pour le rig"))
    S.rm_leg_thigh = bpy.props.FloatProperty(update=_proportion_update, name="Cuisse", default=0.45, min=0.01)
    S.rm_leg_shin = bpy.props.FloatProperty(update=_proportion_update, name="Tibia", default=0.42, min=0.01)
    S.rm_zoom_on_select = bpy.props.BoolProperty(
        name="Cadrer sur le point", default=False,
        description="Recentre la vue sur le point selectionne")
    S.rm_show_names = bpy.props.BoolProperty(name="Noms visibles", default=True)
    S.rm_tube_radius = bpy.props.FloatProperty(name="Rayon", default=0.02, min=0.001, max=5.0)
    S.rm_tube_res = bpy.props.IntProperty(name="Lissage", default=4, min=0, max=32)
    S.rm_tube_caps = bpy.props.BoolProperty(name="Fermer les extremites", default=True)
    S.rm_tube_material = bpy.props.PointerProperty(
        name="Materiau", type=bpy.types.Material,
        description="Materiau applique aux tubes de liaison")


def unregister():
    global _asset_previews

    if _on_load_scan in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_scan)
    if bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.unregister(_deferred_scan)
    bpy.msgbus.clear_by_owner(_sync_owner)
    if _on_load_selection in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_selection)
    if bpy.app.timers.is_registered(_poll_selection):
        bpy.app.timers.unregister(_poll_selection)
    if _asset_previews is not None:
        bpy.utils.previews.remove(_asset_previews)
        _asset_previews = None

    S = bpy.types.Scene
    for prop in ("rm_mirror", "rm_mirror_axis", "rm_asset_edit", "rm_asset_search", "rm_asset_page", "rm_asset_per_page",
                 "rm_asset_columns", "rm_thumb_size", "rm_asset", "rm_asset_name", "rm_asset_overwrite",
                 "rm_asset_freeze", "rm_target_socket",
                 "rm_place_on_click", "rm_asset_scale", "rm_show_names", "rm_zoom_on_select", "rm_tube_caps",
                 "rm_slot", "rm_snap_bbox", "rm_torso", "rm_neck", "rm_head_gap",
                 "rm_shoulder_w", "rm_shoulder_drop", "rm_hip_w", "rm_arm_upper",
                 "rm_arm_fore", "rm_arm_angle", "rm_leg_thigh", "rm_leg_shin", "rm_tube_res", "rm_tube_radius", "rm_tube_material",
                 "rm_socket_size", "rm_socket_custom", "rm_socket_name", "rm_category", "rm_robot",
                 "rm_new_name"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
