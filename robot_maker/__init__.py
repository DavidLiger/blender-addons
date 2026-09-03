bl_info = {
    "name": "Character Maker",
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
import json
import math
import os
import re
import subprocess
import tempfile
import webbrowser
from mathutils import Matrix, Vector

try:
    import asset_library as al
except Exception:      # l'addon n'est pas installe ou pas active
    al = None

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

# Credits : posee sur l'objet, l'origine voyage avec lui dans le .blend
K_SRC_NAME = "src_name"          # titre du modele sur sa page d'origine
K_SRC_AUTHOR = "src_author"
K_SRC_LICENSE = "src_license"
K_SRC_URL = "src_url"
K_SRC_ORIGINAL = "src_original"  # creation maison : aucun credit a rendre

FACE_SLOTS = [('eyes', "Yeux"), ('mouth', "Bouche")]
READY_FILE = "ready.blend"
D_RIGGED = "mixamo-rigged"

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
            if al is not None:
                al.scan(context)
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
            os.makedirs(os.path.join(root, LIBRARY), exist_ok=True)
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


class RM_CreditFolder(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(
        name="Dossier", subtype='DIR_PATH', default="",
        description="Parcouru recursivement a la recherche de .blend")


class RM_UL_credit_folders(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        layout.prop(item, "path", text="")


class RM_OT_credit_folder_add(bpy.types.Operator):
    bl_idname = "rm.credit_folder_add"
    bl_label = "Ajouter un dossier"
    bl_description = "Ajoute une ligne a la liste des dossiers a parcourir"

    def execute(self, context):
        scene = context.scene
        scene.rm_credit_folders.add()
        scene.rm_credit_folder_index = len(scene.rm_credit_folders) - 1
        return {'FINISHED'}


class RM_OT_credit_folder_remove(bpy.types.Operator):
    bl_idname = "rm.credit_folder_remove"
    bl_label = "Retirer le dossier"
    bl_description = "Retire le dossier selectionne de la liste"

    def execute(self, context):
        scene = context.scene
        i = scene.rm_credit_folder_index
        if 0 <= i < len(scene.rm_credit_folders):
            scene.rm_credit_folders.remove(i)
            scene.rm_credit_folder_index = max(0, i - 1)
        return {'FINISHED'}


def _credit_index(root):
    """Fiches JSON de la bibliotheque, indexees par nom d'asset. C'est le
    rattrapage des objets importes avant l'ajout des cles de credit."""
    index = {}
    if not root:
        return index

    for folder, _dirs, files in os.walk(os.path.join(root, LIBRARY)):
        for f in files:
            if not f.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, f), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            key = str(data.get("asset") or os.path.splitext(f)[0])
            index[key.lower()] = data
    return index


def _credit_of(obj, index):
    """Cles posees sur l'objet ; a defaut, rattrapage par le nom de l'asset."""
    if K_SRC_AUTHOR in obj:
        if obj.get(K_SRC_ORIGINAL):
            return None
        data = {k: str(obj.get(v, "")) for k, v in
                (("src_name", K_SRC_NAME), ("author", K_SRC_AUTHOR),
                 ("license", K_SRC_LICENSE), ("url", K_SRC_URL))}
        return data

    # Blender suffixe les doublons : Chaise.003 -> chaise
    data = index.get(re.sub(r"\.\d+$", "", obj.name).lower())
    if data is None or data.get("original"):
        return None
    return {k: str(data.get(k, "")) for k in
            ("src_name", "author", "license", "url")}


def _scan_blend_credits(path, index, found, unknown):
    """Lie les objets du .blend le temps de lire leurs cles, puis detache."""
    before = set(bpy.data.libraries)
    try:
        with bpy.data.libraries.load(path, link=True) as (src, dst):
            dst.objects = list(src.objects)
    except Exception as e:
        unknown.append("{} : illisible ({})".format(os.path.basename(path), e))
        return

    libs = [l for l in bpy.data.libraries if l not in before]

    for obj in bpy.data.objects:
        if obj.library not in libs or obj.type != 'MESH':
            continue
        credit = _credit_of(obj, index)
        if credit is None:
            if K_SRC_AUTHOR not in obj:
                unknown.append("{} : {}".format(os.path.basename(path), obj.name))
            continue
        found[(credit["author"], credit["src_name"],
               credit["license"], credit["url"])] = credit

    for lib in libs:
        try:
            bpy.data.libraries.remove(lib)
        except Exception:
            pass


def _build_credits(list_path, out_path, root):
    """Coeur du generateur. Aucune dependance a l'interface : c'est ce qui
    permet de l'executer dans un Blender en arriere-plan."""
    folders = []
    with open(list_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                folders.append(line if os.path.isabs(line)
                               else os.path.join(root, line))

    index = _credit_index(root)
    found, unknown, blends = {}, [], 0

    for folder in folders:
        if not os.path.isdir(folder):
            unknown.append("dossier absent : " + folder)
            continue
        for cur, _dirs, files in os.walk(folder):
            for f in sorted(files):
                if not f.lower().endswith(".blend"):
                    continue
                path = os.path.join(cur, f)
                print("[credits] scan", path, flush=True)
                _scan_blend_credits(path, index, found, unknown)
                blends += 1

    lines = ["# Credits", ""]
    for c in sorted(found.values(), key=lambda d: (d["author"].lower(),
                                                   d["src_name"].lower())):
        line = "- {} par {}".format(c["src_name"] or "(sans titre)",
                                    c["author"] or "(auteur inconnu)")
        if c["license"]:
            line += " - " + c["license"]
        if c["url"]:
            line += " - " + c["url"]
        lines.append(line)

    if unknown:
        lines += ["", "# A verifier (aucune origine trouvee)", ""]
        lines += ["- " + u for u in unknown[:300]]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    return len(found), blends, len(unknown)


class RM_OT_build_credits(bpy.types.Operator):
    bl_idname = "rm.build_credits"
    bl_label = "Generer les credits"
    bl_description = ("Parcourt les .blend des dossiers listes et ecrit le "
                      "fichier de credits des assets utilises.\n\n"
                      "ATTENTION : lie temporairement chaque .blend dans le "
                      "fichier courant. Travailler dans une scene vide et ne "
                      "pas sauvegarder juste apres. Blender se fige pendant "
                      "toute la duree du scan")

    def invoke(self, context, event):
        wm = context.window_manager
        # Le message personnalise n'existe que depuis Blender 4.1 ; sur 4.0
        # on retombe sur la confirmation simple, qui affiche le bl_label
        try:
            return wm.invoke_confirm(
                self, event,
                title="Generer les credits",
                message=("Le scan lie chaque .blend dans le fichier courant. "
                         "Scene vide conseillee, ne pas sauvegarder ensuite. "
                         "Blender se fige pendant toute la duree."),
                confirm_text="Lancer le scan")
        except TypeError:
            return wm.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        out_path = bpy.path.abspath(scene.rm_credits_out)

        folders = [bpy.path.abspath(f.path).strip()
                   for f in scene.rm_credit_folders if f.path.strip()]
        if not folders:
            self.report({'ERROR'}, "Aucun dossier dans la liste")
            return {'CANCELLED'}
        if not out_path:
            self.report({'ERROR'}, "Fichier de sortie non defini")
            return {'CANCELLED'}

        # Le sous-processus lit une liste sur disque : _build_credits reste
        # inchange, et la commande ne depend pas du nombre de dossiers
        fd, list_path = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(folders))

        root = root_path(context) or ""

        # Blender separe : le scan tourne dans un autre processus, ton fichier
        # courant n'est jamais touche et rien ne peut y etre sauvegarde
        try:
            proc = subprocess.run(
                [bpy.app.binary_path, "--background", "--factory-startup",
                 "--python", os.path.abspath(__file__), "--",
                 list_path, out_path, root],
                capture_output=True, text=True, timeout=1800)
        except Exception as e:
            self.report({'ERROR'}, "Lancement impossible : {}".format(e))
            return {'CANCELLED'}

        try:
            os.remove(list_path)
        except Exception:
            pass

        result = ""
        for line in (proc.stdout or "").splitlines():
            if line.startswith("[credits] RESULT"):
                result = line.split(None, 2)[2]

        if not result:
            print(proc.stdout)
            print(proc.stderr)
            self.report({'ERROR'},
                        "Scan echoue : details dans la console systeme")
            return {'CANCELLED'}

        n, b, u = result.split()
        self.report({'INFO'},
                    "{} credit(s), {} fichier(s), {} a verifier".format(n, b, u))
        return {'FINISHED'}


class _RM_dead_code:
    def _ancien_execute(self, context):
        folders = []
        try:
            with open(list_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        folders.append(line if os.path.isabs(line)
                                       else os.path.join(root, line))
        except Exception as e:
            self.report({'ERROR'}, "Liste illisible : {}".format(e))
            return {'CANCELLED'}

        index = _credit_index(context)
        found, unknown, blends = {}, [], 0

        for folder in folders:
            if not os.path.isdir(folder):
                unknown.append("dossier absent : " + folder)
                continue
            for cur, _dirs, files in os.walk(folder):
                for f in sorted(files):
                    if f.lower().endswith(".blend"):
                        _scan_blend_credits(os.path.join(cur, f), index,
                                            found, unknown)
                        blends += 1

        lines = ["# Credits", ""]
        for c in sorted(found.values(), key=lambda d: (d["author"].lower(),
                                                       d["src_name"].lower())):
            line = "- {} par {}".format(c["src_name"] or "(sans titre)",
                                        c["author"] or "(auteur inconnu)")
            if c["license"]:
                line += " - " + c["license"]
            if c["url"]:
                line += " - " + c["url"]
            lines.append(line)

        if unknown:
            lines += ["", "# A verifier (aucune origine trouvee)", ""]
            lines += ["- " + u for u in unknown[:300]]

        try:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception as e:
            self.report({'ERROR'}, "Ecriture impossible : {}".format(e))
            return {'CANCELLED'}

        self.report({'INFO'}, "{} credit(s), {} fichier(s), {} a verifier"
                    .format(len(found), blends, len(unknown)))
        return {'FINISHED'}


def import_asset(context, coll, path, empty, category, mirror=True):
    """Importe un .blend d'asset et le pose sur le repere. Retourne la liste
    des objets crees, marquage compris."""
    scene = context.scene

    imported = al.import_blend(path) if al else []
    if not imported:
        return []

    created = []
    for obj in imported:
        coll.objects.link(obj)
        obj[K_ROBOT] = scene.rm_robot
        obj["robot_part"] = category
        obj["robot_slot"] = empty.get("robot_slot", empty.name)
        _attach_to_empty(context, obj, empty)
        created.append(obj)

        if mirror:
            dup, _err = make_mirror(context, obj, empty.get("robot_slot", ""))
            if dup is not None:
                created.append(dup)

    return created


class RM_OT_default_rules(bpy.types.Operator):
    bl_idname = "rm.default_rules"
    bl_label = "Regles par defaut"
    bl_description = "Remplit la table avec les correspondances usuelles de la famille"

    def execute(self, context):
        scene = context.scene
        scene.rm_rules.clear()

        for slot, cat, mirror in DEFAULT_RULES.get(scene.rm_default_rules, []):
            rule = scene.rm_rules.add()
            rule.slot = slot
            rule.category = cat
            rule.mirror = mirror
            rule.use = True

        self.report({'INFO'}, "{} regle(s)".format(len(scene.rm_rules)))
        return {'FINISHED'}


class RM_OT_rule_add(bpy.types.Operator):
    bl_idname = "rm.rule_add"
    bl_label = "Ajouter une regle"
    bl_description = "Ajoute une correspondance repere / categorie"

    def execute(self, context):
        rule = context.scene.rm_rules.add()
        rule.slot = 'chest'
        rule.use = True
        rule.mirror = False
        return {'FINISHED'}


class RM_OT_rule_remove(bpy.types.Operator):
    bl_idname = "rm.rule_remove"
    bl_label = "Retirer"
    bl_description = "Retire cette correspondance"

    index: bpy.props.IntProperty()

    def execute(self, context):
        rules = context.scene.rm_rules
        if 0 <= self.index < len(rules):
            rules.remove(self.index)
        return {'FINISHED'}


class RM_OT_random_fill(bpy.types.Operator):
    bl_idname = "rm.random_fill"
    bl_label = "Generer au hasard"
    bl_description = ("Pose une piece tiree au sort sur chaque repere de la table. "
                      "Un nouveau clic remplace le tirage precedent")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import random

        scene = context.scene
        coll = active_robot_collection(context)

        if coll is None:
            self.report({'ERROR'}, "Aucun robot actif")
            return {'CANCELLED'}
        if not scene.rm_rules:
            self.report({'ERROR'}, "Table vide : cliquer sur Regles par defaut")
            return {'CANCELLED'}

        # Le tirage precedent laisse la place au nouveau
        removed = 0
        for obj in [o for o in coll.objects if o.get(K_RANDOM)]:
            bpy.data.objects.remove(obj)
            removed += 1

        placed, empty_cats = 0, []

        for rule in scene.rm_rules:
            if not rule.use:
                continue

            empty = slot_empty(coll, rule.slot)
            if empty is None:
                continue

            pool = al.assets(rule.category) if al else []
            if not pool:
                empty_cats.append(rule.category)
                continue

            name, path = random.choice(pool)
            if not os.path.isfile(path):
                continue

            for obj in import_asset(context, coll, path, empty,
                                    rule.category, rule.mirror):
                obj[K_RANDOM] = True
                placed += 1

        deselect_all(context)
        msg = "{} piece(s) posee(s)".format(placed)
        if removed:
            msg += " - {} remplacee(s)".format(removed)
        if empty_cats:
            msg += " - categorie(s) vide(s) : " + ", ".join(sorted(set(empty_cats)))

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_place_asset(bpy.types.Operator):
    bl_idname = "rm.place_asset"
    bl_label = "Placer l'asset"
    bl_description = ("Importe l'asset et le pose sur le repere selectionne, "
                      "ou a defaut sur le repere choisi dans la liste")
    bl_options = {'REGISTER', 'UNDO'}

    asset: bpy.props.StringProperty(default="")
    category: bpy.props.StringProperty(default="")

    def execute(self, context):
        scene = context.scene

        if al is None:
            self.report({'ERROR'}, "Addon Asset Library non active")
            return {'CANCELLED'}

        coll = active_robot_collection(context)
        if coll is None:
            self.report({'ERROR'}, "Aucun personnage actif")
            return {'CANCELLED'}

        key = self.category or al.key_of(scene, "rm_cat", "rm_sub")
        path = al.path_of(key, self.asset)
        if path is None:
            self.report({'ERROR'}, "Fichier introuvable : relire la bibliotheque")
            return {'CANCELLED'}

        empty = resolve_target(context)
        if empty is None:
            self.report({'ERROR'}, "Aucun repere : creer le squelette")
            return {'CANCELLED'}

        # La selection va changer apres l'import : on retient la cible
        scene.rm_target_socket = empty.name

        imported = al.import_blend(path)
        if not imported:
            self.report({'ERROR'}, "Le fichier ne contient aucun objet")
            return {'CANCELLED'}

        for obj in imported:
            coll.objects.link(obj)
            obj[K_ROBOT] = scene.rm_robot
            obj["robot_part"] = key
            obj["robot_slot"] = empty.get("robot_slot", empty.name)
            _attach_to_empty(context, obj, empty)
            _mirror_after_attach(context, obj, empty)

        deselect_all(context)
        for obj in imported:
            obj.select_set(True)
        context.view_layer.objects.active = imported[0]

        self.report({'INFO'}, "'{}' pose sur {}".format(
            self.asset, empty.name[len(SOCKET_PREFIX):]))
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

    if al is not None and hasattr(scene, "al_asset_name"):
        scene.al_asset_name = obj.name

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
                with open(os.path.join(path, "character.json"), "w",
                          encoding="utf-8") as f:
                    json.dump({"name": name, "family": scene.rm_default_rules},
                              f, indent=1)
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
            obj["robot_part"] = al.key_of(scene, "rm_cat", "rm_sub") if al else ""
            
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


K_RANDOM = "robot_random"      # marque une piece posee par le tirage

# Repere -> categorie piochee, et mise en miroir automatique
DEFAULT_RULES = {
    'ROBOT': [
        ('chest', 'robot/body', False), ('head', 'robot/head', False),
        ('shoulder_L', 'robot/joints', True), ('elbow_L', 'robot/joints', True),
        ('hip_L', 'robot/joints', True), ('knee_L', 'robot/joints', True),
        ('wrist_L', 'robot/hands', True), ('ankle_L', 'robot/feet', True),
    ],
    'HUMAN': [
        ('chest', 'human/torso', False), ('head', 'human/head', False),
        ('hips', 'human/legs', False), ('neck', 'human/hair', False),
        ('elbow_L', 'human/arms', True), ('wrist_L', 'human/hands', True),
        ('ankle_L', 'human/feet', True),
    ],
}

_slot_cache = {}


def slot_items(self, context):
    """Reperes du squelette, completes par ceux ajoutes a la main."""
    extras = []
    try:
        coll = active_robot_collection(context)
        extras = sorted(s.name[len(SOCKET_PREFIX):] for s in all_sockets(coll)
                        if not s.get("robot_slot"))
    except Exception:
        extras = []

    key = tuple(extras)
    if key not in _slot_cache:
        _slot_cache[key] = ([(s, SLOT_LABEL.get(s, s), "") for s in SLOTS]
                            + [(e, e, "Repere ajoute a la main") for e in extras])

    return _slot_cache[key]

def _rule_keys(edit_text=""):
    """Cles de bibliotheque proposees dans la table de tirage."""
    if al is None:
        return []

    out = []
    for cat in al.categories():
        for sub in cat.get("children", []):
            out.append(cat["key"] + "/" + sub["key"])

    return sorted(k for k in out if edit_text.lower() in k.lower())

class RM_Rule(bpy.types.PropertyGroup):
    slot: bpy.props.EnumProperty(name="Repere", items=slot_items)
    category: bpy.props.StringProperty(
        name="Categorie", default="",
        search=lambda s, c, t: al.style_search and _rule_keys(t) or [])
    mirror: bpy.props.BoolProperty(
        name="Miroir", default=True,
        description="Duplique la piece sur le repere oppose")
    use: bpy.props.BoolProperty(name="Active", default=True)


# Clef = sous-categorie de bibliotheque, en majuscules
CATEGORY_SLOT = {
    'BODY': 'chest', 'TORSO': 'chest', 'ACCESSORIES': 'chest', 'OTHER': 'chest',
    'HEAD': 'head', 'HAIR': 'head', 'HATS': 'head',
    'ARMS': 'elbow_L', 'JOINTS': 'elbow_L', 'HANDS': 'wrist_L',
    'LEGS': 'knee_L', 'FEET': 'ankle_L',
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

    found = next((o for o in coll.objects
                  if o.get(K_SOCKET) and o.get("robot_slot") == slot), None)
    if found is not None:
        return found

    # Repere ajoute a la main : il n'a pas de cle robot_slot, seulement son nom
    return next((o for o in coll.objects
                 if o.get(K_SOCKET) and o.name[len(SOCKET_PREFIX):] == slot), None)


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

        tubes = 0
        if scene.rm_default_rules == 'ROBOT':
            for a, b in BONES:
                make_tube(context, created[a], created[b],
                          scene.rm_tube_radius, scene.rm_tube_res, scene.rm_tube_caps)
            tubes = len(BONES)

        self.report({'INFO'}, "Squelette cree : {} reperes, {} tube(s)".format(
            len(created), tubes))
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
    dup["robot_part"] = obj.get("robot_part", "")
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
            obj["robot_part"] = al.key_of(scene, "rm_cat", "rm_sub") if al else ""
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
        sub = getattr(context.scene, "rm_sub", "")
        slot = CATEGORY_SLOT.get(sub.upper(), 'chest')
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

def _slot_color(obj, fallback=(0.6, 0.6, 0.62, 1.0)):
    """Base Color du materiau porte par les faces selectionnees."""
    mats = obj.data.materials
    poly = next((p for p in obj.data.polygons if p.select), None)

    if poly is not None and poly.material_index < len(mats):
        mat = mats[poly.material_index]
        if mat is not None and mat.use_nodes:
            node = next((n for n in mat.node_tree.nodes
                         if n.type == 'BSDF_PRINCIPLED'), None)
            if node is not None:
                return tuple(node.inputs["Base Color"].default_value)

    return tuple(fallback)


def _face_material(name, uv_layer, sprite, color):
    """Sprite sheet sur une couche UV dediee, emission sur le trait,
    couleur unie ailleurs. Le noeud EXPR_Mapping est pilote par Expressions."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()

    out = tree.nodes.new('ShaderNodeOutputMaterial')
    out.location = (400, 0)

    base = tree.nodes.new('ShaderNodeBsdfPrincipled')
    base.location = (100, -200)
    base.inputs["Base Color"].default_value = color

    emit = tree.nodes.new('ShaderNodeEmission')
    emit.location = (100, 160)

    mix = tree.nodes.new('ShaderNodeMixShader')
    mix.location = (260, 0)

    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.location = (-160, 120)
    tex.extension = 'CLIP'
    if sprite is not None:
        tex.image = sprite

    cell = tree.nodes.new('ShaderNodeMapping')
    cell.name = "EXPR_Mapping"
    cell.label = "Cellule"
    cell.location = (-380, 120)

    uv = tree.nodes.new('ShaderNodeUVMap')
    uv.location = (-580, 120)
    uv.uv_map = uv_layer

    tree.links.new(uv.outputs['UV'], cell.inputs['Vector'])
    tree.links.new(cell.outputs['Vector'], tex.inputs['Vector'])
    tree.links.new(tex.outputs['Color'], emit.inputs['Color'])
    tree.links.new(tex.outputs['Alpha'], mix.inputs['Fac'])
    tree.links.new(base.outputs['BSDF'], mix.inputs[1])
    tree.links.new(emit.outputs['Emission'], mix.inputs[2])
    tree.links.new(mix.outputs['Shader'], out.inputs['Surface'])

    return mat


class RM_OT_prepare_face(bpy.types.Operator):
    bl_idname = "rm.prepare_face"
    bl_label = "Preparer"
    bl_description = ("Cree le materiau et la couche UV de cette zone, et l'assigne "
                      "aux faces selectionnees. Se placer en vue de face avant")
    bl_options = {'REGISTER', 'UNDO'}

    slot: bpy.props.StringProperty(default='eyes')

    def execute(self, context):
        scene = context.scene
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner le maillage")
            return {'CANCELLED'}
        if not any(p.select for p in obj.data.polygons):
            self.report({'ERROR'}, "Aucune face selectionnee : les choisir en Edit Mode")
            return {'CANCELLED'}

        robot = scene.rm_robot or obj.name
        mat_name = "FACE_{}_{}".format(self.slot, robot)
        uv_name = "UV_" + self.slot

        # Sprite sheet du personnage : fichier contenant le nom de la zone
        sprite, sheet_json = None, ""
        folder = os.path.join(robot_dir(context, robot), "expressions")
        if os.path.isdir(folder):
            for fname in sorted(os.listdir(folder)):
                low = fname.lower()
                if self.slot not in low:
                    continue
                path = os.path.join(folder, fname)
                if low.endswith(".json"):
                    sheet_json = path
                elif low.endswith(".png"):
                    try:
                        sprite = bpy.data.images.load(path, check_existing=True)
                    except Exception:
                        pass

        # En Edit Mode la selection n'est pas encore repercutee sur le maillage
        was_edit = (obj.mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data
        faces = [p for p in mesh.polygons if p.select]
        if not faces:
            self.report({'ERROR'}, "Aucune face selectionnee")
            return {'CANCELLED'}

        # Couleur du materiau que portent ces faces
        color = (0.6, 0.6, 0.62, 1.0)
        first = mesh.materials[faces[0].material_index] \
            if faces[0].material_index < len(mesh.materials) else None
        if first is not None and first.use_nodes:
            node = next((n for n in first.node_tree.nodes
                         if n.type == 'BSDF_PRINCIPLED'), None)
            if node is not None:
                color = tuple(node.inputs["Base Color"].default_value)

        mat = _face_material(mat_name, uv_name, sprite, color)

        index = mesh.materials.find(mat_name)
        if index < 0:
            mesh.materials.append(mat)
            index = len(mesh.materials) - 1
        obj.active_material_index = index

        for poly in faces:
            poly.material_index = index

        # Depliage frontal calcule directement : projection sur X/Z, normalisee
        if uv_name not in mesh.uv_layers:
            mesh.uv_layers.new(name=uv_name)
        layer = mesh.uv_layers[uv_name]
        mesh.uv_layers.active = layer

        mw = obj.matrix_world
        pts = [mw @ mesh.vertices[mesh.loops[li].vertex_index].co
               for poly in faces for li in poly.loop_indices]

        min_x, max_x = min(p.x for p in pts), max(p.x for p in pts)
        min_z, max_z = min(p.z for p in pts), max(p.z for p in pts)
        # Meme echelle sur les deux axes : pas de deformation du visage
        span = max(max_x - min_x, max_z - min_z) or 1.0
        span_x = span_z = span
        min_x -= ((span - (max_x - min_x)) / 2.0)
        min_z -= ((span - (max_z - min_z)) / 2.0)

        for poly in faces:
            for li in poly.loop_indices:
                co = mw @ mesh.vertices[mesh.loops[li].vertex_index].co
                layer.data[li].uv = ((co.x - min_x) / span_x,
                                     (co.z - min_z) / span_z)

        mesh.update()
        if was_edit:
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass
        # Passe le relais a l'addon Expressions
        if sheet_json and hasattr(scene, "expr_json"):
            scene.expr_json = sheet_json
            scene.expr_target = obj
            try:
                bpy.ops.expr.load_json()
                bpy.ops.expr.setup()
            except Exception:
                pass

        msg = "{} : materiau et UV prets".format(self.slot)
        if sprite is None:
            msg += " - aucun sprite sheet '{}' dans expressions/".format(self.slot)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RM_OT_face_info(bpy.types.Operator):
    bl_idname = "rm.face_info"
    bl_label = "Mise en place du visage"
    bl_description = "Rappelle les etapes"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=540)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        steps = [
            ("1. Sprite sheets", [
                "Exporter depuis expressions.html en mode Yeux seuls puis Bouches seules,",
                "vers creations/<perso>/expressions/. Le nom doit contenir eyes ou mouth.",
            ]),
            ("2. Selection", [
                "Selectionner le maillage, Edit Mode (Tab), mode Face (3),",
                "choisir les faces de la zone. Se placer en vue de face (numpad 1).",
            ]),
            ("3. Preparer", [
                "Cliquer Yeux ou Bouche : materiau, couche UV dediee et depliage",
                "frontal sont crees, le sprite sheet est charge.",
            ]),
            ("4. Ajuster", [
                "Ouvrir un UV Editor : l'ilot apparait sur le sprite sheet.",
                "Le deplacer et le redimensionner pour caler la zone au pixel pres.",
                "Chaque zone a sa propre couche UV, donc reglage independant.",
            ]),
            ("5. Expressions", [
                "Onglet Expressions : mettre le slot materiau voulu en actif,",
                "puis poser les keyframes.",
            ]),
        ]
        for title, lines in steps:
            box = self.layout.box()
            box.label(text=title, icon='DOT')
            col = box.column(align=True)
            col.scale_y = 0.8
            for line in lines:
                col.label(text=line)
                
JAW_BONE = "jaw"


class RM_OT_add_jaw(bpy.types.Operator):
    bl_idname = "rm.add_jaw"
    bl_label = "Machoire"
    bl_description = ("Cree l'os de machoire sous mixamorig:Head et y assigne "
                      "les sommets selectionnes. A faire apres le retour de "
                      "Mixamo, avant le control rig")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner le maillage de la tete")
            return {'CANCELLED'}

        rig = obj.find_armature()
        if rig is None:
            self.report({'ERROR'}, "Le maillage n'est pas rigge")
            return {'CANCELLED'}

        head = None
        for name in ("mixamorig:Head", "mixamorig1:Head", "Head"):
            if name in rig.data.bones:
                head = name
                break

        if head is None:
            self.report({'ERROR'}, "Os de tete introuvable dans l'armature")
            return {'CANCELLED'}

        # En Edit Mode la selection n'est pas encore repercutee sur le maillage
        was_edit = (obj.mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data
        verts = [v for v in mesh.vertices if v.select]
        if not verts:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
            self.report({'ERROR'}, "Aucun sommet selectionne")
            return {'CANCELLED'}

        mw = obj.matrix_world
        pts = [mw @ v.co for v in verts]

        lo = Vector((min(p[i] for p in pts) for i in range(3)))
        hi = Vector((max(p[i] for p in pts) for i in range(3)))
        center = (lo + hi) / 2.0

        # La charniere est a l'arriere de la selection, l'os pointe vers l'avant
        pivot = Vector((center.x, hi.y, center.z))
        tip = Vector((center.x, lo.y, center.z))
        if (tip - pivot).length < 1e-4:
            tip = pivot + Vector((0.0, -0.1, 0.0))

        # --- Creation de l'os ---
        previous = context.view_layer.objects.active
        deselect_all(context)
        rig.select_set(True)
        context.view_layer.objects.active = rig

        inv = rig.matrix_world.inverted()
        bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = rig.data.edit_bones
        bone = edit_bones.get(JAW_BONE)
        if bone is None:
            bone = edit_bones.new(JAW_BONE)

        bone.head = inv @ pivot
        bone.tail = inv @ tip
        bone.parent = edit_bones[head]
        bone.use_connect = False        # sinon la tete du parent serait deplacee

        bpy.ops.object.mode_set(mode='OBJECT')

        deselect_all(context)
        obj.select_set(True)
        context.view_layer.objects.active = previous or obj

        # --- Groupe de poids ---
        group = obj.vertex_groups.get(JAW_BONE) or obj.vertex_groups.new(name=JAW_BONE)
        group.add([v.index for v in verts], 1.0, 'REPLACE')

        # Les memes sommets ne doivent plus suivre la tete
        head_group = obj.vertex_groups.get(head)
        if head_group is not None:
            head_group.remove([v.index for v in verts])

        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, "Os '{}' cree sous {} - {} sommet(s)".format(
            JAW_BONE, head, len(verts)))
        return {'FINISHED'}
                
class RM_OT_import_rigged(bpy.types.Operator):
    bl_idname = "rm.import_rigged"
    bl_label = "Instancier le perso rigge"
    bl_description = ("Importe le FBX revenu de Mixamo pour y preparer le visage, "
                      "puis enregistrer le personnage pret")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        robot = scene.rm_robot or getattr(scene, "rbm_robot", "")

        if not robot:
            self.report({'ERROR'}, "Aucun personnage actif")
            return {'CANCELLED'}

        folder = os.path.join(robot_dir(context, robot), D_RIGGED)
        if not os.path.isdir(folder):
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

        index = 1
        while bpy.data.collections.get("{}{}_{:02d}".format(COLL_PREFIX, robot, index)):
            index += 1
        coll = bpy.data.collections.new("{}{}_{:02d}".format(COLL_PREFIX, robot, index))
        context.scene.collection.children.link(coll)

        for obj in imported:
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            coll.objects.link(obj)
            obj[K_ROBOT] = robot

        armature = next((o for o in imported if o.type == 'ARMATURE'), None)
        mesh = next((o for o in imported if o.type == 'MESH'), None)

        deselect_all(context)
        target = mesh or armature
        if target is not None:
            target.select_set(True)
            context.view_layer.objects.active = target

        msg = "{} importe dans {}".format(files[0], coll.name)

        if scene.rm_make_rig and armature is not None:
            source = getattr(scene, "mix_source_armature", None)
            if source is not None:
                scene.mix_source_armature = None

            deselect_all(context)
            armature.select_set(True)
            context.view_layer.objects.active = armature
            try:
                bpy.ops.mr.make_rig()
            except Exception:
                pass

            if source is not None:
                scene.mix_source_armature = source

            if "mr_control_rig" in armature.data.keys():
                msg += " - control rig cree"
            else:
                msg += " - control rig non cree"

            if target is not None:
                deselect_all(context)
                target.select_set(True)
                context.view_layer.objects.active = target

        self.report({'INFO'}, msg)
        return {'FINISHED'}
                
class RM_OT_save_ready(bpy.types.Operator):
    bl_idname = "rm.save_ready"
    bl_label = "Enregistrer le personnage pret"
    bl_description = ("Enregistre le personnage rigge avec son visage prepare. "
                      "Robot Manager instanciera ce fichier au lieu du FBX brut")

    def execute(self, context):
        scene = context.scene
        obj = context.active_object

        if obj is None:
            self.report({'ERROR'}, "Selectionner le personnage")
            return {'CANCELLED'}

        coll = next((c for c in obj.users_collection
                     if c.name.startswith(COLL_PREFIX)), None)
        if coll is None:
            self.report({'ERROR'}, "Le personnage n'est pas dans une collection ROBOT_")
            return {'CANCELLED'}

        robot = (scene.rm_robot or getattr(scene, "rbm_robot", "")
                 or re.sub(r"_\d+$", "", coll.name[len(COLL_PREFIX):]))

        folder = robot_dir(context, robot)
        if not folder or not os.path.isdir(folder):
            self.report({'ERROR'}, "Dossier de '{}' introuvable".format(robot))
            return {'CANCELLED'}

        try:
            bpy.data.libraries.write(os.path.join(folder, READY_FILE),
                                     {coll}, fake_user=True)
        except Exception as e:
            self.report({'ERROR'}, "Ecriture impossible : {}".format(e))
            return {'CANCELLED'}

        self.report({'INFO'}, "'{}' pret ({})".format(robot, coll.name))
        return {'FINISHED'}

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
    bl_label = "Character Maker"
    bl_idname = "RM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Character Maker"

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

        if al is None:
            box.label(text="Addon Asset Library non active", icon='ERROR')
        else:
            row.prop(scene, "al_edit", text="", icon='TRASH', toggle=True)
            row.operator("al.open_folder", text="", icon='FILEBROWSER')
            row.operator("al.scan", text="", icon='FILE_REFRESH')

            if al.draw_categories(box, context, manage=False,
                                  cat_prop="rm_cat", sub_prop="rm_sub"):
                al.draw_browser(box, context, "rm.place_asset", enabled=has_skel,
                                key=al.key_of(scene, "rm_cat", "rm_sub"))
                if not has_skel:
                    sub = box.row()
                    sub.scale_y = 0.7
                    sub.label(text="Creer le squelette pour poser", icon='INFO')
        box.separator()
        row = box.row(align=True)
        row.label(text="Remplissage automatique", icon='FILE_REFRESH')
        row.operator("rm.default_rules", text="", icon='LOOP_BACK')

        if scene.rm_rules:
            table = box.column(align=True)

            for i, rule in enumerate(scene.rm_rules):
                r = table.row(align=True)
                r.prop(rule, "use", text="")
                r.prop(rule, "slot", text="")
                r.prop(rule, "category", text="")
                r.prop(rule, "mirror", text="", icon='MOD_MIRROR')
                r.operator("rm.rule_remove", text="", icon='X').index = i

            box.operator("rm.rule_add", icon='ADD')
            box.operator("rm.random_fill", icon='FILE_REFRESH')
        else:
            r = box.row(align=True)
            r.operator("rm.rule_add", icon='ADD')
            r.label(text="ou fleche pour les regles usuelles")

        if al is not None:
            box.separator()
            al.draw_add_panel(box, context)

        # --- Placement ---
        # --- Credits ---
        box = layout.box()
        box.label(text="Credits", icon='TEXT')
        box.label(text="Dossiers a parcourir :")
        r = box.row()
        r.template_list("RM_UL_credit_folders", "", scene, "rm_credit_folders",
                        scene, "rm_credit_folder_index", rows=3)
        c = r.column(align=True)
        c.operator("rm.credit_folder_add", text="", icon='ADD')
        c.operator("rm.credit_folder_remove", text="", icon='REMOVE')

        box.prop(scene, "rm_credits_out")

        # alert = True : Blender dessine la colonne en rouge
        note = box.row()
        note.scale_y = 0.7
        note.label(text="Scan en arriere-plan, ~1 s par .blend", icon='INFO')

        box.operator("rm.build_credits", icon='FILE_TEXT')

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
        box.enabled = (scene.rm_default_rules == 'ROBOT')
        box.label(text="Tubes de liaison" if scene.rm_default_rules == 'ROBOT'
                  else "Tubes (robots uniquement)", icon='CURVE_PATH')
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
        
        # --- Visage ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Visage", icon='USER')
        row.operator("rm.face_info", text="", icon='INFO')

        box.prop(scene, "rm_make_rig")
        box.operator("rm.import_rigged", icon='IMPORT')
        box.separator()

        r = box.row(align=True)
        for slot, label in FACE_SLOTS:
            r.operator("rm.prepare_face", text=label).slot = slot
        r.operator("rm.add_jaw", text="Machoire")

        sub = box.column(align=True)
        sub.scale_y = 0.7
        sub.label(text="Faces selectionnees + vue de face", icon='INFO')
        sub.label(text="Ajuster ensuite dans l'UV Editor")
        box.operator("rm.save_ready", icon='FILE_TICK')

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
    RM_CreditFolder,
    RM_UL_credit_folders,
    RM_OT_credit_folder_add,
    RM_OT_credit_folder_remove,
    RM_Preferences,
    RM_OT_init_folders,
    RM_OT_open_folder,
    RM_OT_open_expression_maker,
    RM_OT_build_credits,
    RM_OT_place_asset,
    RM_OT_update_mirrors,
    RM_OT_mirror_selected,
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
    RM_OT_prepare_face,
    RM_OT_add_jaw,
    RM_OT_face_info,
    RM_OT_save_ready,
    RM_OT_import_rigged,
    RM_Rule,
    RM_OT_default_rules,
    RM_OT_random_fill,
    RM_OT_rule_add,
    RM_OT_rule_remove,
)

@bpy.app.handlers.persistent
def _on_load_character(dummy=None):
    """Le .blend vit dans creations/<perso>/ : la fiche voisine donne la famille."""
    try:
        blend = bpy.data.filepath
        if not blend:
            return

        folder = os.path.dirname(blend)
        cfg = os.path.join(folder, "character.json")
        if not os.path.isfile(cfg):
            return

        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)

        family = data.get("family")
        if family in {'ROBOT', 'HUMAN'}:
            bpy.context.scene.rm_default_rules = family

        if al is not None:
            al.scan(bpy.context)
    except Exception:
        pass

def register():

    for cls in classes:
        bpy.utils.register_class(cls)
        
    if _on_load_character not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_character)

    _subscribe_selection()
    if _on_load_selection not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_selection)
    if not bpy.app.timers.is_registered(_poll_selection):
        bpy.app.timers.register(_poll_selection, first_interval=1.0, persistent=True)

    S = bpy.types.Scene
    S.rm_new_name = bpy.props.StringProperty(name="Nom", default="robot_01")
    S.rm_robot = bpy.props.EnumProperty(name="Robot actif", items=robot_enum)
    if al is not None:
        S.rm_cat = bpy.props.EnumProperty(
            name="Categorie", items=al.scoped_cat_items(("robot", "human")))
        S.rm_sub = bpy.props.EnumProperty(
            name="Sous-categorie", items=al.scoped_sub_items("rm_cat"))
    S.rm_socket_name = bpy.props.EnumProperty(
        name="Point", default="shoulder_L",
        items=[(n, n.replace("_", " "), "") for n in SOCKET_PRESETS])
    S.rm_socket_custom = bpy.props.StringProperty(name="Nom du point", default="socket")
    S.rm_socket_size = bpy.props.FloatProperty(name="Taille", default=0.05, min=0.001, max=2.0)

    S.rm_credit_folders = bpy.props.CollectionProperty(type=RM_CreditFolder)
    S.rm_credit_folder_index = bpy.props.IntProperty(default=0)
    S.rm_credits_out = bpy.props.StringProperty(
        name="Fichier de credits", subtype='FILE_PATH', default="//credits.md")

    S.rm_target_socket = bpy.props.StringProperty(
        name="Repere vise", default="",
        description="Dernier repere utilise, conserve entre deux placements")
    S.rm_default_rules = bpy.props.EnumProperty(
        name="Regles usuelles", default='ROBOT',
        items=[('ROBOT', "Robot", ""), ('HUMAN', "Humanoide", "")])
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
    S.rm_make_rig = bpy.props.BoolProperty(
        name="Creer le control rig", default=True,
        description="Ajoute les controleurs IK/FK via l'addon Mixamo Control Rig")
    S.rm_rules = bpy.props.CollectionProperty(type=RM_Rule)


def unregister():

    if _on_load_character in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_character)
    bpy.msgbus.clear_by_owner(_sync_owner)
    if _on_load_selection in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_selection)
    if bpy.app.timers.is_registered(_poll_selection):
        bpy.app.timers.unregister(_poll_selection)

    S = bpy.types.Scene
    for prop in ("rm_mirror", "rm_mirror_axis", "rm_asset_edit", "rm_asset_search", "rm_asset_page", "rm_asset_per_page",
                 "rm_asset_columns", "rm_thumb_size", "rm_asset", "rm_asset_name", "rm_asset_overwrite",
                 "rm_asset_freeze", "rm_era", "rm_style",
                 "rm_filter_era", "rm_filter_style", "rm_credit_folders",
                 "rm_credit_folder_index", "rm_credits_out",
                 "rm_src_name", "rm_src_author",
                 "rm_src_license", "rm_src_url", "rm_src_original",
                 "rm_target_socket",
                 "rm_place_on_click", "rm_asset_scale", "rm_show_names", "rm_zoom_on_select", "rm_tube_caps",
                 "rm_slot", "rm_snap_bbox", "rm_torso", "rm_neck", "rm_head_gap",
                 "rm_shoulder_w", "rm_shoulder_drop", "rm_hip_w", "rm_arm_upper",
                 "rm_arm_fore", "rm_arm_angle", "rm_leg_thigh", "rm_leg_shin", "rm_tube_res", "rm_tube_radius", "rm_tube_material",
                 "rm_socket_size", "rm_socket_custom", "rm_socket_name", "rm_category", "rm_robot",
                 "rm_new_name", "rm_family", "rm_make_rig", "rm_rules", "rm_cat", "rm_sub", "rm_default_rules", "rm_cat", "rm_sub"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    import sys
    import traceback

    # Lance par RM_OT_build_credits :
    #   blender --background --factory-startup --python <ce fichier> --
    #           <liste> <sortie> <racine>
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    if len(argv) == 3:
        # Mode scan : pas d'addon a enregistrer, seules les fonctions servent
        try:
            n, b, u = _build_credits(argv[0], argv[1], argv[2])
            print("[credits] RESULT {} {} {}".format(n, b, u), flush=True)
        except Exception as e:
            print("[credits] ERREUR", e, flush=True)
            traceback.print_exc()
    else:
        # Fichier lance depuis l'editeur de texte de Blender
        register()