bl_info = {
    "name": "Multi-Cam BD",
    "author": "David",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Multi-Cam BD",
    "description": "Liste, previsualise et rend en batch les cameras de planches BD (res_x/res_y et frame par camera)",
    "category": "3D View",
}

import bpy
import os
import base64
import json
import posixpath
import threading
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import re

# ---------------------------------------------------------------------------
# Serveur local
# Sert gaufrier.html et le dossier du strip : le navigateur n'a besoin
# d'aucune permission disque, et n'importe lequel fait l'affaire.
# ---------------------------------------------------------------------------
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SERVER_PORT = 8778

_server = None
_server_thread = None
_pending_cameras = []           # rempli par le serveur, consomme par le timer
_pending_lock = threading.Lock()


class _GaufrierHandler(SimpleHTTPRequestHandler):
    root = ""                     # racine du strip

    def log_message(self, fmt, *args):
        pass

    def _under_root(self, relative):
        base = os.path.normpath(self.root)
        target = os.path.normpath(os.path.join(base, relative.replace("/", os.sep)))
        return target if target.startswith(base) else None

    def translate_path(self, path):
        clean = posixpath.normpath(urllib.parse.urlparse(path).path).lstrip("/")
        return os.path.join(WEB_DIR, clean.replace("/", os.sep))

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path.startswith("/files/"):
            target = self._under_root(posixpath.normpath(parsed.path[7:]).lstrip("/"))
            if target is None or not os.path.isfile(target):
                self.send_error(404)
                return

            with open(target, "rb") as f:
                payload = f.read()

            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(target))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path.startswith("/list/"):
            target = self._under_root(posixpath.normpath(parsed.path[6:]).lstrip("/"))
            if target is None:
                self.send_error(403)
                return
            if not os.path.isdir(target):
                self._json({"files": [], "dirs": []})
                return

            entries = sorted(os.listdir(target))
            self._json({
                "files": [f for f in entries
                          if os.path.isfile(os.path.join(target, f))],
                "dirs": [d for d in entries
                         if os.path.isdir(os.path.join(target, d))],
            })
            return

        SimpleHTTPRequestHandler.do_GET(self)

    def end_headers(self):
        # La page est en developpement : jamais de cache navigateur
        self.send_header("Cache-Control", "no-store, must-revalidate")
        SimpleHTTPRequestHandler.end_headers(self)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if route not in ("/save", "/cameras"):
            self.send_error(404)
            return

        if route == "/cameras":
            try:
                size = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return

            # Blender n'est pas thread-safe : le travail est differe
            with _pending_lock:
                _pending_cameras.append(payload)

            self._json({"ok": True, "count": len(payload.get("frames", []))})
            return

        try:
            size = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(size).decode("utf-8"))
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return

        target = self._under_root(data.get("path", ""))
        if target is None:
            self._json({"ok": False, "error": "chemin refuse"}, 403)
            return

        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if "b64" in data:
                with open(target, "wb") as f:
                    f.write(base64.b64decode(data["b64"].split(",")[-1]))
            else:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(data.get("text", ""))
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)
            return

        self._json({"ok": True, "path": target})


def start_gaufrier_server(root):
    global _server, _server_thread

    if _server is not None:
        _GaufrierHandler.root = root
        return True, "deja demarre"

    if not root or not os.path.isdir(root):
        return False, "racine du strip introuvable"

    _GaufrierHandler.root = root
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", SERVER_PORT), _GaufrierHandler)
    except Exception as e:
        _server = None
        return False, "port {} indisponible ({})".format(SERVER_PORT, e)

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    return True, "ok"


def stop_gaufrier_server():
    global _server, _server_thread

    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _server_thread = None


def _apply_cameras():
    """Cree ou met a jour les cameras demandees par le gaufrier."""
    with _pending_lock:
        jobs = list(_pending_cameras)
        _pending_cameras.clear()

    for job in jobs:
        created = 0
        for frame in job.get("frames", []):
            name = frame.get("name")
            if not name:
                continue

            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != 'CAMERA':
                data = bpy.data.cameras.new(name)
                obj = bpy.data.objects.new(name, data)
                bpy.context.collection.objects.link(obj)
                created += 1

            obj["res_x"] = int(frame.get("w", 1000))
            obj["res_y"] = int(frame.get("h", 1000))
            if "frame" not in obj:
                obj["frame"] = 0
            _multicam_fix_res_bounds(obj)

        try:
            bpy.ops.multicam.refresh()
        except Exception:
            pass

        print("[multicam] {} camera(s) creee(s), {} au total".format(
            created, len(job.get("frames", []))))

    return 1.0


class MULTICAM_OT_open_gaufrier(bpy.types.Operator):
    bl_idname = "multicam.open_gaufrier"
    bl_label = "Generateur de gaufrier"
    bl_description = ("Ouvre le generateur de planches dans le navigateur, "
                      "sur le dossier du strip courant")

    def execute(self, context):
        scene = context.scene
        root = bpy.path.abspath(scene.multicam_strip_root or "")

        ok, msg = start_gaufrier_server(root)
        if not ok:
            self.report({'ERROR'}, "Serveur local : {}".format(msg))
            return {'CANCELLED'}

        page = os.path.join(WEB_DIR, "gaufrier.html")
        if not os.path.isfile(page):
            self.report({'ERROR'}, "gaufrier.html absent de {}".format(WEB_DIR))
            return {'CANCELLED'}

        # Quel fichier est reellement servi, et est-il patche ?
        try:
            with open(page, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            content = ""

        print("[multicam] page servie :", page)
        print("[multicam] taille :", len(content),
              "- walkServer:", "walkServer" in content,
              "- SERVED:", "SERVED" in content)

        if "walkServer" not in content:
            self.report({'WARNING'},
                        "Le gaufrier servi n'est pas la version patchee : " + page)

        pages = _multicam_pages(scene)
        params = {"variant": RENDER_DIRS[_multicam_variant(scene)]}
        if pages:
            params["page"] = pages[0]

        webbrowser.open("http://127.0.0.1:{}/gaufrier.html?{}".format(
            SERVER_PORT, urllib.parse.urlencode(params)))
        return {'FINISHED'}

# ---------------------------------------------------------------------------
# PropertyGroup : un element de la liste = une camera
# ---------------------------------------------------------------------------
class MULTICAM_CameraItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Nom camera")
    enabled: bpy.props.BoolProperty(name="Rendre", default=True)


# ---------------------------------------------------------------------------
# UIList : affichage de chaque camera (checkbox / nom / resolution / preview)
# ---------------------------------------------------------------------------
class MULTICAM_UL_cameras(bpy.types.UIList):
    # Proportions partagees avec l'en-tete du panneau (voir MULTICAM_PT_panel)
    NAME_FACTOR = 0.42

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")

        cam = bpy.data.objects.get(item.name)

        if cam is None:
            row.label(text=item.name + "  (introuvable)", icon='ERROR')
            return

        split = row.split(factor=self.NAME_FACTOR, align=True)
        split.label(text=item.name)

        fields = split.row(align=True)

        # Largeur / hauteur
        if "res_x" in cam and "res_y" in cam:
            fields.prop(cam, '["res_x"]', text="")
            fields.prop(cam, '["res_y"]', text="")
        else:
            op = fields.operator("multicam.set_resolution", text="res ?")
            op.camera_name = item.name

        # Frame de la timeline utilisee pour le rendu de cette camera
        if "frame" in cam:
            fields.prop(cam, '["frame"]', text="")
        else:
            op = fields.operator("multicam.set_frame", text="f ?")
            op.camera_name = item.name

        op = fields.operator("multicam.preview", text="", icon='HIDE_OFF')
        op.camera_name = item.name


# ---------------------------------------------------------------------------
# Operateur : ajouter res_x / res_y sur une camera qui n'en a pas
# ---------------------------------------------------------------------------
class MULTICAM_OT_set_resolution(bpy.types.Operator):
    bl_idname = "multicam.set_resolution"
    bl_label = "Definir resolution"
    bl_description = "Ajoute les proprietes res_x / res_y (1000 x 1000 par defaut) sur cette camera"

    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera_name)
        if cam is None:
            return {'CANCELLED'}

        cam["res_x"] = 1000
        cam["res_y"] = 1000

        _multicam_fix_res_bounds(cam)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operateur : definir la frame d'une camera
# ---------------------------------------------------------------------------
class MULTICAM_OT_set_frame(bpy.types.Operator):
    bl_idname = "multicam.set_frame"
    bl_label = "Definir la frame"
    bl_description = ("Ajoute la propriete 'frame' sur cette camera : la timeline sera "
                      "placee sur cette frame au moment du rendu (animation de vehicules, "
                      "destruction de batiment...)")

    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera_name)
        if cam is None:
            return {'CANCELLED'}

        cam["frame"] = context.scene.frame_current
        _multicam_fix_res_bounds(cam)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operateur : repartir automatiquement les frames (0, 10, 20... par defaut)
# ---------------------------------------------------------------------------
class MULTICAM_OT_auto_frames(bpy.types.Operator):
    bl_idname = "multicam.auto_frames"
    bl_label = "Repartir les frames"
    bl_description = ("Attribue une frame a chaque camera de la liste, par pas regulier. "
                      "Les valeurs restent modifiables ensuite camera par camera")
    bl_options = {'REGISTER', 'UNDO'}

    start: bpy.props.IntProperty(name="Premiere frame", default=0, min=0)
    step: bpy.props.IntProperty(name="Pas", default=10, min=1)
    only_enabled: bpy.props.BoolProperty(
        name="Uniquement les cameras cochees", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        frame = self.start
        count = 0

        for item in scene.multicam_items:
            if self.only_enabled and not item.enabled:
                continue
            cam = bpy.data.objects.get(item.name)
            if cam is None or cam.type != 'CAMERA':
                continue

            cam["frame"] = frame
            _multicam_fix_res_bounds(cam)
            frame += self.step
            count += 1

        self.report({'INFO'}, "{} camera(s) : frames {} a {}".format(
            count, self.start, max(self.start, frame - self.step)))
        return {'FINISHED'}


def _multicam_fix_res_bounds(cam):
    """S'assure que res_x / res_y / frame peuvent etre edites librement
    (corrige aussi les anciennes proprietes bloquees a Max=1)."""
    for key in ("res_x", "res_y"):
        if key in cam:
            try:
                cam.id_properties_ui(key).update(min=1, max=20000)
            except Exception:
                pass
    if "frame" in cam:
        try:
            cam.id_properties_ui("frame").update(min=0, max=1048574)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Operateur : rafraichir la liste depuis la scene
# ---------------------------------------------------------------------------
class MULTICAM_OT_refresh(bpy.types.Operator):
    bl_idname = "multicam.refresh"
    bl_label = "Rafraichir la liste"
    bl_description = "Recharge la liste des cameras possedant res_x / res_y"

    def execute(self, context):
        scene = context.scene

        # On garde en memoire l'etat des cases deja cochees / decochees
        previous_state = {item.name: item.enabled for item in scene.multicam_items}

        scene.multicam_items.clear()

        cams = [obj for obj in bpy.data.objects if obj.type == 'CAMERA']
        cams.sort(key=lambda o: o.name)

        for cam in cams:
            item = scene.multicam_items.add()
            item.name = cam.name
            # Si la camera existait deja dans la liste, on garde son etat
            item.enabled = previous_state.get(cam.name, True)

            # Corrige les bornes UI des anciennes res_x/res_y (ex: Max=1)
            _multicam_fix_res_bounds(cam)

        self.report({'INFO'}, "{} camera(s) trouvee(s)".format(len(cams)))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Regroupement par planche : "Camera.01.A" -> planche "01"
# ---------------------------------------------------------------------------
_PAGE_RE = re.compile(r"\.(\d+)\.")

# Convention de dossiers : .../planches/<NN>/rendus-XXX
_PATH_PAGE_RE = re.compile(r"(planches[\\/])(\d+)([\\/])", re.IGNORECASE)


def _multicam_retarget_path(path, page):
    """Remplace le numero de planche dans un chemin de type planches/<NN>/...
    Renvoie (nouveau_chemin, True) si la substitution a eu lieu."""
    if not path:
        return path, False

    new_path, count = _PATH_PAGE_RE.subn(
        lambda m: m.group(1) + page + m.group(3), path, count=1)
    return new_path, bool(count)


def _multicam_page_of(name):
    """Numero de planche extrait du nom de camera, ou None."""
    m = _PAGE_RE.search(name)
    return m.group(1) if m else None


def _multicam_pages(scene):
    """Liste triee des planches presentes dans la liste des cameras."""
    pages = {_multicam_page_of(item.name) for item in scene.multicam_items}
    pages.discard(None)
    return sorted(pages, key=lambda p: (len(p), p))


class MULTICAM_OT_select_page(bpy.types.Operator):
    bl_idname = "multicam.select_page"
    bl_label = "Cocher une planche"
    bl_description = "Coche toutes les cameras de cette planche et decoche toutes les autres"

    page: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene

        count = 0
        for item in scene.multicam_items:
            match = (_multicam_page_of(item.name) == self.page)
            item.enabled = match
            if match:
                count += 1

        msg = "Planche {} : {} camera(s)".format(self.page, count)

        # Adapte les dossiers de sortie a la planche selectionnee
        if scene.multicam_follow_page:
            retargeted = False
            for prop in ("multicam_output_eevee", "multicam_output_cycles"):
                new_path, changed = _multicam_retarget_path(getattr(scene, prop), self.page)
                if changed:
                    setattr(scene, prop, new_path)
                    retargeted = True

            if retargeted:
                msg += " - dossiers de sortie adaptes"
            elif scene.multicam_output_eevee or scene.multicam_output_cycles:
                msg += " - chemins inchanges (motif 'planches/<NN>/' absent)"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operateur : tout cocher / tout decocher
# ---------------------------------------------------------------------------
class MULTICAM_OT_select_all(bpy.types.Operator):
    bl_idname = "multicam.select_all"
    bl_label = "Selectionner / Deselectionner tout"
    bl_description = "Coche ou decoche toutes les cameras de la liste"

    state: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        for item in context.scene.multicam_items:
            item.enabled = self.state
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operateur : preview d'une camera (active + resolution + vue camera)
# ---------------------------------------------------------------------------
class MULTICAM_OT_preview(bpy.types.Operator):
    bl_idname = "multicam.preview"
    bl_label = "Preview"
    bl_description = "Active cette camera, applique sa resolution et bascule en vue camera (passe-partout ajuste)"

    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera_name)

        if cam is None or cam.type != 'CAMERA':
            self.report({'ERROR'}, "Camera '{}' introuvable".format(self.camera_name))
            return {'CANCELLED'}

        scene = context.scene
        scene.camera = cam

        # Se placer sur la frame de cette camera (si definie)
        if "frame" in cam:
            scene.frame_set(int(cam["frame"]))

        if "res_x" in cam and "res_y" in cam:
            scene.render.resolution_x = cam["res_x"]
            scene.render.resolution_y = cam["res_y"]
        else:
            self.report({'WARNING'}, "Cette camera n'a pas de res_x / res_y")

        # Basculer le viewport 3D en vue camera (Numpad 0)
        # -> il faut un override sur la region 'WINDOW' du viewport 3D
        # -> on ne bascule QUE si on n'est pas deja en vue camera, sinon
        #    view3d.view_camera() est un toggle et nous ferait sortir de la vue camera
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    space = area.spaces.active
                    already_camera_view = (
                        space.region_3d is not None
                        and space.region_3d.view_perspective == 'CAMERA'
                    )
                    if not already_camera_view:
                        for region in area.regions:
                            if region.type == 'WINDOW':
                                with context.temp_override(window=window, area=area, region=region):
                                    bpy.ops.view3d.view_camera()
                                break
                    break

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Rendu batch via bpy.app.timers (plus fiable qu'un operateur modal + timer)
# ---------------------------------------------------------------------------
_batch_state = {
    "cameras": [],
    "index": 0,
    "active": False,
    "current_filepath": None,
}

_FORMAT_EXTENSIONS = {
    'PNG': '.png',
    'JPEG': '.jpg',
    'OPEN_EXR': '.exr',
    'OPEN_EXR_MULTILAYER': '.exr',
    'TIFF': '.tif',
    'BMP': '.bmp',
    'TARGA': '.tga',
    'TARGA_RAW': '.tga',
    'WEBP': '.webp',
}


def _multicam_redraw_areas():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# Variante de rendu selon le moteur, et sous-dossiers d'une planche
RENDER_DIRS = {'CYCLES': "rendus-Cycles", 'EEVEE': "rendus-EVEE"}
EXTRA_DIRS = ["rendus-Kuwahara"]


def _multicam_variant(scene):
    return 'CYCLES' if scene.render.engine == 'CYCLES' else 'EEVEE'


def _multicam_page_dir(scene, page, variant=None):
    """<racine>/planches/<NN>/rendus-<variante>"""
    root = bpy.path.abspath(scene.multicam_strip_root or "")
    if not root or not page:
        return ""

    return os.path.join(root, "planches", page,
                        RENDER_DIRS[variant or _multicam_variant(scene)])


def _multicam_dir_for(scene, cam):
    """Dossier de sortie d'une camera : derive de son nom, ou saisi a la main."""
    if scene.multicam_auto_paths:
        page = _multicam_page_of(cam.name if cam is not None else "")
        derived = _multicam_page_dir(scene, page)
        if derived:
            return derived

    return _multicam_output_dir_raw(scene)


def _multicam_output_dir_raw(scene):
    """Chemin de sortie brut correspondant au moteur de rendu actif."""
    if scene.render.engine == 'CYCLES':
        return scene.multicam_output_cycles
    return scene.multicam_output_eevee


def _multicam_check_cameras(scene, cams):
    """Verifie le dossier de chaque camera cochee. Le dossier de rendu peut
    manquer, il sera cree ; son parent doit exister."""
    missing = []

    for cam in cams:
        raw = _multicam_dir_for(scene, cam)
        if not raw:
            missing.append(cam.name + " : pas de dossier")
            continue

        path = os.path.normpath(bpy.path.abspath(raw))
        if os.path.isdir(path) or os.path.isdir(os.path.dirname(path)):
            continue
        missing.append(path)

    return missing


def _multicam_check_output_dir(scene):
    """Verifie le dossier de sortie du moteur actif.
    Renvoie None si tout va bien, sinon un message d'erreur.

    Le dossier de rendu lui-meme peut ne pas exister (il sera cree), mais son
    parent doit exister : cela evite de creer silencieusement une arborescence
    entiere a cause d'une faute de frappe (ex: strip-02 pas encore cree)."""
    raw = _multicam_output_dir_raw(scene)

    if not raw:
        return "Dossier de sortie non defini pour le moteur '{}'".format(scene.render.engine)

    path = os.path.normpath(bpy.path.abspath(raw))

    if os.path.isdir(path):
        return None

    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        return "Chemin introuvable : {} (le dossier parent n'existe pas)".format(path)

    return None


VL_DECOR, VL_PERSOS = "DECOR", "PERSOS"
BG_NODE = "MC_Fond"
MERGE_NODE = "MC_Merge"


def _robot_colls():
    return [c for c in bpy.data.collections if c.name.startswith("ROBOT_")]


def _only_lights(coll):
    """Collection ne contenant que de l'eclairage : presente sur tous les calques."""
    if not coll.objects and not coll.children:
        return False
    if any(o.type not in {'LIGHT', 'EMPTY'} for o in coll.objects):
        return False
    return all(_only_lights(c) for c in coll.children)


def _has_robot(coll):
    if coll.name.startswith("ROBOT_"):
        return True
    return any(_has_robot(c) for c in coll.children)


def _apply_exclusions(view_layer, mode, cycles=False):
    """mode : 'DECOR' tout sauf les persos, 'PERSOS' eux seuls.
    Ce qui n'est pas dessine reste present pour les ombres portees."""
    def walk(lc):
        is_robot = lc.collection.name.startswith("ROBOT_")

        if mode == 'DECOR':
            # Tout est rendu, persos compris : c'est ainsi que leurs ombres
            # apparaissent. Leur image sera recouverte par le calque PERSOS.
            lc.exclude = False
            lc.indirect_only = False
            lc.holdout = False
            for child in lc.children:
                walk(child)
            return

        # PERSOS : on garde la branche qui mene aux robots, on coupe le reste
        if is_robot:
            lc.exclude = False
            lc.indirect_only = False
            lc.holdout = False
            return

        # L'eclairage doit rester present sur les deux calques
        if _only_lights(lc.collection):
            lc.exclude = False
            return

        if _has_robot(lc.collection):
            lc.exclude = False
            lc.indirect_only = False
            for child in lc.children:
                walk(child)
        else:
            # Le decor n'est pas dessine mais masque les persos derriere lui :
            # un poteau au premier plan les cache correctement
            lc.exclude = False
            lc.indirect_only = False
            lc.holdout = True

    for child in view_layer.layer_collection.children:
        walk(child)


def _freestyle_setup(scene, view_layer):
    """Contour sur le calque des personnages, avec tremble d'epaisseur."""
    scene.render.use_freestyle = True
    view_layer.use_freestyle = True

    fs = view_layer.freestyle_settings
    fs.mode = 'EDITOR'
    fs.crease_angle = 2.443           # 140 degres

    lineset = fs.linesets.get("MC_Lines")
    if lineset is None:
        lineset = fs.linesets.new("MC_Lines")

    lineset.select_silhouette = True
    lineset.select_border = True
    lineset.select_crease = True

    style = lineset.linestyle
    style.color = scene.multicam_line_color[:3]
    style.thickness = scene.multicam_line_thick

    # Les modificateurs sont reconstruits : plus simple que de les retrouver
    while style.thickness_modifiers:
        style.thickness_modifiers.remove(style.thickness_modifiers[0])

    if scene.multicam_line_calli:
        calli = style.thickness_modifiers.new("calli", 'CALLIGRAPHY')
        calli.orientation = 0.785     # 45 degres
        calli.thickness_min = scene.multicam_line_thick * 0.35
        calli.thickness_max = scene.multicam_line_thick * 1.6

    if scene.multicam_line_noise > 0.0:
        noise = style.thickness_modifiers.new("grain", 'NOISE')
        noise.amplitude = scene.multicam_line_noise
        noise.period = scene.multicam_line_period
        noise.seed = 1


def _tidy_master(scene):
    """Les objets poses a la racine ne peuvent pas etre exclus d'un calque :
    on les range dans des collections dediees."""
    master = scene.collection
    loose = [o for o in master.objects]
    if not loose:
        return 0

    def bucket(name):
        coll = bpy.data.collections.get(name)
        if coll is None:
            coll = bpy.data.collections.new(name)
        if coll.name not in {c.name for c in master.children}:
            master.children.link(coll)
        return coll

    moved = 0
    for obj in loose:
        target = bucket("MC_Lights" if obj.type == 'LIGHT' else "MC_Decor")
        master.objects.unlink(obj)
        target.objects.link(obj)
        moved += 1

    return moved


def _setup_view_layers(scene):
    """Cree et configure DECOR et PERSOS."""
    scene.render.film_transparent = True
    _tidy_master(scene)

    layers = {}
    for name, mode in ((VL_DECOR, 'DECOR'), (VL_PERSOS, 'PERSOS')):
        vl = scene.view_layers.get(name)
        if vl is None:
            vl = scene.view_layers.new(name)
        layers[name] = vl
        _apply_exclusions(vl, mode, cycles=(scene.render.engine == 'CYCLES'))

    layers[VL_DECOR].use_freestyle = False
    _freestyle_setup(scene, layers[VL_PERSOS])

    # Un calque par defaut sans role reste inutile mais couteux
    for vl in scene.view_layers:
        if vl.name not in layers:
            vl.use = False

    return layers


def _build_compositor(scene):
    """FOND < DECOR < PERSOS, puis Composite. Renvoie la sortie fusionnee."""
    scene.use_nodes = True
    tree = scene.node_tree

    sources = {}
    for i, name in enumerate((VL_DECOR, VL_PERSOS)):
        node = tree.nodes.get("MC_RL_" + name)
        if node is None:
            node = tree.nodes.new('CompositorNodeRLayers')
            node.name = "MC_RL_" + name
        node.label = name
        node.scene = scene
        node.layer = name
        node.location = (-600, 200 - i * 260)
        sources[name] = node

    # Le monde n'est pas rendu quand le film est transparent : on le remplace
    # par sa couleur, posee derriere le decor
    bg = _node_get(tree, BG_NODE, 'CompositorNodeRGB', (-600, -320))
    color = (0.05, 0.05, 0.05, 1.0)
    if scene.world is not None and scene.world.use_nodes:
        node = scene.world.node_tree.nodes.get("Background")
        if node is not None:
            color = tuple(node.inputs[0].default_value)
    bg.outputs[0].default_value = color

    over1 = _node_get(tree, "MC_Over1", 'CompositorNodeAlphaOver', (-260, 80))
    over2 = _node_get(tree, MERGE_NODE, 'CompositorNodeAlphaOver', (140, 0))

    tree.links.new(bg.outputs[0], over1.inputs[1])
    tree.links.new(sources[VL_DECOR].outputs['Image'], over1.inputs[2])
    tree.links.new(over1.outputs['Image'], over2.inputs[1])
    tree.links.new(sources[VL_PERSOS].outputs['Image'], over2.inputs[2])

    comp = next((n for n in tree.nodes if n.type == 'COMPOSITE'), None)
    if comp is None:
        comp = tree.nodes.new('CompositorNodeComposite')
        comp.location = (320, 120)
    tree.links.new(over2.outputs['Image'], comp.inputs['Image'])

    return over2.outputs['Image']


def _merged_source(scene):
    """Sortie a filtrer : le decor seul quand les calques sont separes, pour
    que les personnages gardent leur trait net."""
    if scene.use_nodes:
        over1 = scene.node_tree.nodes.get("MC_Over1")
        if over1 is not None:
            return over1.outputs['Image']

    rl = next((n for n in scene.node_tree.nodes if n.type == 'R_LAYERS'), None) \
        if scene.use_nodes else None
    return rl.outputs['Image'] if rl else None


KUWA_NODE = "MC_Kuwahara"
KUWA_OUT = "MC_KuwaharaOut"


def _node_get(tree, name, node_type, location):
    node = tree.nodes.get(name)
    if node is not None:
        return node

    node = tree.nodes.new(node_type)
    node.name = name
    node.label = name
    node.location = location
    return node


def _kuwahara_setup(scene):
    """Branche Kuwahara en parallele du rendu : le compositeur ecrit la
    version stylisee pendant que l'addon enregistre l'image brute."""
    scene.use_nodes = True
    tree = scene.node_tree

    rl = next((n for n in tree.nodes if n.type == 'R_LAYERS'), None)
    if rl is None:
        rl = tree.nodes.new('CompositorNodeRLayers')
        rl.location = (-400, 0)

    comp = next((n for n in tree.nodes if n.type == 'COMPOSITE'), None)
    if comp is None:
        comp = tree.nodes.new('CompositorNodeComposite')
        comp.location = (300, 120)
    if not comp.inputs['Image'].is_linked:
        tree.links.new(rl.outputs['Image'], comp.inputs['Image'])

    kuwa = _node_get(tree, KUWA_NODE, 'CompositorNodeKuwahara', (120, -320))
    out = _node_get(tree, KUWA_OUT, 'CompositorNodeOutputFile', (400, -320))

    out.format.file_format = 'PNG'
    out.format.color_mode = 'RGBA'
    if not out.file_slots:
        out.file_slots.new("image")

    source = _merged_source(scene) or rl.outputs['Image']
    tree.links.new(source, kuwa.inputs[0])

    # Les personnages sont reposes par-dessus le decor filtre
    persos = tree.nodes.get("MC_RL_" + VL_PERSOS)
    if persos is not None:
        final = _node_get(tree, "MC_KuwaOver", 'CompositorNodeAlphaOver', (280, -320))
        tree.links.new(kuwa.outputs[0], final.inputs[1])
        tree.links.new(persos.outputs['Image'], final.inputs[2])
        tree.links.new(final.outputs['Image'], out.inputs[0])
    else:
        tree.links.new(kuwa.outputs[0], out.inputs[0])

    return kuwa, out


def _kuwahara_apply(scene, cam):
    """Regle le filtre et dirige la sortie vers le dossier de la planche."""
    if not scene.multicam_kuwahara:
        node = scene.node_tree.nodes.get(KUWA_OUT) if scene.use_nodes else None
        if node is not None:
            node.mute = True
        return

    kuwa, out = _kuwahara_setup(scene)
    out.mute = False

    # Le filtre amplifie le grain : un rendu bruite donnerait des taches
    if scene.multicam_kuwa_denoise and scene.render.engine == 'CYCLES':
        try:
            scene.cycles.use_denoising = True
            scene.cycles.denoiser = 'OPENIMAGEDENOISE'
            scene.cycles.denoising_use_gpu = True
            for vl in scene.view_layers:
                vl.cycles.use_denoising = True
        except Exception:
            pass

    kuwa.variation = scene.multicam_kuwa_mode
    try:
        kuwa.inputs['Size'].default_value = scene.multicam_kuwa_size
    except Exception:
        kuwa.size = int(scene.multicam_kuwa_size)

    if scene.multicam_kuwa_mode == 'ANISOTROPIC':
        for attr, value in (("uniformity", scene.multicam_kuwa_uniform),
                            ("sharpness", scene.multicam_kuwa_sharp),
                            ("eccentricity", scene.multicam_kuwa_ecc)):
            try:
                setattr(kuwa, attr, value)
            except Exception:
                pass

    page = _multicam_page_of(cam.name)
    folder = _multicam_page_dir(scene, page, variant=None)
    if folder:
        # Un dossier par moteur : sinon EEVEE et Cycles s'ecrasent
        suffix = "-Cycles" if _multicam_variant(scene) == 'CYCLES' else ""
        folder = os.path.join(os.path.dirname(folder), "rendus-Kuwahara" + suffix)
        os.makedirs(folder, exist_ok=True)
        out.base_path = folder
        out.file_slots[0].path = cam.name + "_"


def _kuwahara_rename(scene, cam):
    """Le noeud File Output suffixe le numero de frame : on remet le nom voulu."""
    if not scene.multicam_kuwahara or not scene.use_nodes:
        return

    out = scene.node_tree.nodes.get(KUWA_OUT)
    if out is None or not out.base_path:
        return

    folder = bpy.path.abspath(out.base_path)
    if not os.path.isdir(folder):
        return

    # Le suffixe de frame est ajoute par Blender, et l'extension peut manquer
    # puisque use_file_extension est desactive pour le rendu principal
    target = os.path.join(folder, cam.name + ".png")
    prefix = cam.name + "_"

    for fname in sorted(os.listdir(folder)):
        if not fname.startswith(prefix) or fname == cam.name + ".png":
            continue

        try:
            if os.path.exists(target):
                os.remove(target)
            os.rename(os.path.join(folder, fname), target)
        except Exception:
            pass
        break


def _multicam_setup_camera(scene, cam):
    out_dir = _multicam_dir_for(scene, cam)
    if not out_dir:
        raise RuntimeError("Dossier de sortie introuvable pour '{}'".format(cam.name))

    out_dir_abs = bpy.path.abspath(out_dir)
    os.makedirs(out_dir_abs, exist_ok=True)

    scene.camera = cam

    # Frame de la timeline propre a cette camera (animation en cours)
    if "frame" in cam:
        scene.frame_set(int(cam["frame"]))

    scene.render.resolution_x = cam["res_x"]
    scene.render.resolution_y = cam["res_y"]

    ext = _FORMAT_EXTENSIONS.get(scene.render.image_settings.file_format, '.png')
    filepath = os.path.join(out_dir_abs, cam.name + ext)

    # On gere la sauvegarde nous-memes (voir _multicam_on_render_complete),
    # donc on desactive l'auto-save de l'operateur de rendu.
    scene.render.use_file_extension = False
    scene.render.filepath = filepath

    _kuwahara_apply(scene, cam)

    return filepath


def _multicam_finish(scene):
    scene.multicam_running = False
    scene.multicam_current_name = ""
    _batch_state["current_filepath"] = None
    _multicam_redraw_areas()
    _multicam_remove_handlers()


def _multicam_start_current():
    scene = bpy.context.scene
    state = _batch_state

    # Annulation ou fin de la liste
    if scene.multicam_cancel or state["index"] >= len(state["cameras"]):
        _multicam_finish(scene)
        return

    cam = state["cameras"][state["index"]]

    try:
        filepath = _multicam_setup_camera(scene, cam)
    except Exception as e:
        scene.multicam_last_error = "Erreur sur '{}' : {}".format(cam.name, str(e))
        _multicam_finish(scene)
        return

    state["current_filepath"] = filepath
    scene.multicam_current_name = cam.name
    scene.multicam_last_error = ""
    _multicam_redraw_areas()

    # Rendu silencieux (sans fenetre) : plus fiable en batch.
    # write_still=False : on sauvegarde nous-memes dans render_complete.
    bpy.ops.render.render(write_still=False)


def _multicam_on_render_complete(scene, depsgraph=None):
    state = _batch_state
    if not state["active"]:
        return

    # Sauvegarde manuelle du resultat AVANT de toucher quoi que ce soit
    # pour la camera suivante (evite le decalage de timing observe avec write_still).
    filepath = state.get("current_filepath")
    if filepath:
        try:
            result = bpy.data.images.get("Render Result")
            if result is not None:
                result.save_render(filepath=filepath, scene=scene)
            else:
                scene.multicam_last_error = "Render Result introuvable pour la sauvegarde"
        except Exception as e:
            scene.multicam_last_error = "Erreur sauvegarde '{}' : {}".format(filepath, str(e))

    cam = state["cameras"][state["index"]] if state["index"] < len(state["cameras"]) else None
    if cam is not None:
        _kuwahara_rename(scene, cam)

    state["index"] += 1
    scene.multicam_progress_current = state["index"]
    _multicam_redraw_areas()

    bpy.app.timers.register(_multicam_start_current, first_interval=0.3)


def _multicam_on_render_cancel(scene, depsgraph=None):
    state = _batch_state
    if not state["active"]:
        return

    scene.multicam_last_error = "Rendu annule"
    _multicam_finish(scene)


def _multicam_add_handlers():
    if _multicam_on_render_complete not in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.append(_multicam_on_render_complete)
    if _multicam_on_render_cancel not in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.append(_multicam_on_render_cancel)


def _multicam_remove_handlers():
    _batch_state["active"] = False
    if _multicam_on_render_complete in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.remove(_multicam_on_render_complete)
    if _multicam_on_render_cancel in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.remove(_multicam_on_render_cancel)


# ---------------------------------------------------------------------------
# Operateur : annuler le rendu batch en cours
# ---------------------------------------------------------------------------
class MULTICAM_OT_cancel_render(bpy.types.Operator):
    bl_idname = "multicam.cancel_render"
    bl_label = "Annuler"
    bl_description = "Arrete le rendu batch apres la camera en cours"

    def execute(self, context):
        scene = context.scene
        scene.multicam_cancel = True
        # Force l'arret immediat de l'UI au cas ou les handlers seraient bloques
        scene.multicam_running = False
        scene.multicam_current_name = ""
        _multicam_remove_handlers()
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operateur : declenche le rendu batch des cameras cochees
# ---------------------------------------------------------------------------
class MULTICAM_OT_make_tree(bpy.types.Operator):
    bl_idname = "multicam.make_tree"
    bl_label = "Creer l'arborescence"
    bl_description = ("Cree planches/<NN>/rendus-EVEE, rendus-Cycles et "
                      "rendus-Kuwahara pour chaque planche detectee")

    def execute(self, context):
        scene = context.scene
        root = bpy.path.abspath(scene.multicam_strip_root or "")

        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, "Racine du strip introuvable")
            return {'CANCELLED'}

        # Les planches viennent des cameras si elles existent, sinon de la
        # saisie : l'arborescence se cree avant de generer quoi que ce soit
        pages = _multicam_pages(scene)
        if not pages:
            pages = [p.strip() for p in scene.multicam_pages_hint.split(",")
                     if p.strip()]
        if not pages:
            self.report({'ERROR'}, "Indiquer les numeros de planches a creer")
            return {'CANCELLED'}

        created = 0
        for page in pages:
            for sub in list(RENDER_DIRS.values()) + EXTRA_DIRS:
                path = os.path.join(root, "planches", page, sub)
                if os.path.isdir(path):
                    continue
                try:
                    os.makedirs(path, exist_ok=True)
                    created += 1
                except Exception as e:
                    self.report({'ERROR'}, "Creation impossible : {}".format(e))
                    return {'CANCELLED'}

        self.report({'INFO'}, "{} planche(s) - {} dossier(s) cree(s)".format(
            len(pages), created))
        return {'FINISHED'}


class MULTICAM_OT_clean_colls(bpy.types.Operator):
    bl_idname = "multicam.clean_colls"
    bl_label = "Nettoyer les collections vides"
    bl_description = "Supprime les collections ROBOT_ ne contenant plus rien"

    def execute(self, context):
        removed = 0
        for coll in list(bpy.data.collections):
            if not coll.name.startswith("ROBOT_"):
                continue
            if coll.objects or coll.children:
                continue
            bpy.data.collections.remove(coll)
            removed += 1

        self.report({'INFO'}, "{} collection(s) supprimee(s)".format(removed))
        return {'FINISHED'}


class MULTICAM_OT_setup_layers(bpy.types.Operator):
    bl_idname = "multicam.setup_layers"
    bl_label = "Configurer les calques"
    bl_description = ("Cree FOND, DECOR et PERSOS, y repartit les collections et "
                      "monte leur superposition dans le compositeur")

    def execute(self, context):
        scene = context.scene

        robots = _robot_colls()
        if not robots:
            self.report({'WARNING'}, "Aucune collection ROBOT_ : le calque PERSOS "
                                     "sera vide")

        moved = _tidy_master(scene)
        _setup_view_layers(scene)
        _build_compositor(scene)

        if scene.multicam_kuwahara:
            _kuwahara_setup(scene)

        # Un maillage non marque dans une collection ROBOT_ est du decor egare
        strays = []
        for coll in robots:
            for obj in coll.objects:
                if obj.type == 'MESH' and not obj.get("robot"):
                    strays.append(obj.name)

        if strays:
            self.report({'WARNING'},
                        "Dans une collection ROBOT_ mais pas marque perso : "
                        + ", ".join(strays[:4])
                        + (" ..." if len(strays) > 4 else ""))

        msg = "2 calques - {} collection(s) perso".format(len(robots))
        if moved:
            msg += " - {} objet(s) ranges dans MC_Decor / MC_Lights".format(moved)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class MULTICAM_OT_render_selected(bpy.types.Operator):
    bl_idname = "multicam.render_selected"
    bl_label = "Render Selected"
    bl_description = "Rend toutes les cameras cochees dans la liste (dossier selon le moteur de rendu actif)"

    def execute(self, context):
        scene = context.scene

        # Verification des dossiers AVANT tout rendu
        cams_check = [bpy.data.objects[i.name] for i in scene.multicam_items
                      if i.enabled and i.name in bpy.data.objects]
        missing = _multicam_check_cameras(scene, cams_check)
        if missing:
            msg = "Chemin introuvable : " + " | ".join(missing[:3])
            scene.multicam_last_error = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        enabled_items = [item for item in scene.multicam_items if item.enabled]

        if not enabled_items:
            self.report({'WARNING'}, "Aucune camera selectionnee")
            return {'CANCELLED'}

        # Bloque si une camera cochee n'a pas (encore) de res_x/res_y
        missing = []
        for item in enabled_items:
            cam = bpy.data.objects.get(item.name)
            if cam is None or "res_x" not in cam or "res_y" not in cam:
                missing.append(item.name)

        if missing:
            msg = "Resolution manquante (res_x/res_y) pour : {}".format(", ".join(missing))
            scene.multicam_last_error = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        cameras = [bpy.data.objects[item.name] for item in enabled_items]

        _batch_state["cameras"] = cameras
        _batch_state["index"] = 0
        _batch_state["active"] = True

        scene.multicam_progress_total = len(cameras)
        scene.multicam_progress_current = 0
        scene.multicam_current_name = ""
        scene.multicam_last_error = ""
        scene.multicam_cancel = False
        scene.multicam_running = True

        _multicam_add_handlers()
        _multicam_start_current()

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Panel : Sidebar 3D View (N) > Multi-Cam BD
# ---------------------------------------------------------------------------
class MULTICAM_PT_panel(bpy.types.Panel):
    bl_label = "Multi-Cam BD"
    bl_idname = "MULTICAM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Multi-Cam BD"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # --- Dossiers de sortie ---
        # --- Dossiers de sortie ---
        box = layout.box()
        box.prop(scene, "multicam_auto_paths")

        if scene.multicam_auto_paths:
            box.prop(scene, "multicam_strip_root", text="")
            if not _multicam_pages(scene):
                box.prop(scene, "multicam_pages_hint")

            row = box.row(align=True)
            row.operator("multicam.make_tree", icon='NEWFOLDER')
            row.operator("multicam.open_gaufrier", text="", icon='URL')

            pages = _multicam_pages(scene)
            if pages:
                sub = box.column(align=True)
                sub.scale_y = 0.7
                sub.label(text="{} planche(s) : {}".format(
                    len(pages), ", ".join(pages[:8])))
                sub.label(text=_multicam_page_dir(scene, pages[0]) or "?")
        else:
            box.prop(scene, "multicam_output_eevee", text="EEVEE")
            box.prop(scene, "multicam_output_cycles", text="Cycles")
            box.prop(scene, "multicam_follow_page")

        # Etat du dossier du moteur actif (verifie avant chaque rendu)
        path_error = _multicam_check_output_dir(scene)
        state = box.row()
        if path_error:
            state.alert = True
            state.label(text=path_error, icon='ERROR')
        else:
            state.label(text="Dossier {} OK".format(scene.render.engine), icon='CHECKMARK')

        layout.separator()

        # --- Erreur du dernier rendu ---
        if scene.multicam_last_error:
            err_box = layout.box()
            err_box.alert = True
            err_box.label(text="Erreur :", icon='ERROR')
            err_box.label(text=scene.multicam_last_error)
            layout.separator()

        # --- Liste des cameras ---
        row = layout.row()
        row.operator("multicam.refresh", icon='FILE_REFRESH')

        row = layout.row(align=True)
        op = row.operator("multicam.select_all", text="Tout cocher")
        op.state = True
        op = row.operator("multicam.select_all", text="Tout decocher")
        op.state = False

        # Selection par planche : un bouton par numero detecte dans les noms
        pages = _multicam_pages(scene)
        if pages:
            grid = layout.grid_flow(row_major=True, columns=6, align=True)
            for page in pages:
                op = grid.operator("multicam.select_page", text=page)
                op.page = page

        layout.operator("multicam.auto_frames", icon='TIME')

        # En-tete de colonnes, aligne sur les proportions de la UIList
        header = layout.row(align=True)
        header.label(text="", icon='BLANK1')          # colonne de la case a cocher
        hsplit = header.split(factor=MULTICAM_UL_cameras.NAME_FACTOR, align=True)
        hsplit.label(text="Camera")
        hcols = hsplit.row(align=True)
        hcols.label(text="Largeur")
        hcols.label(text="Hauteur")
        hcols.label(text="Frame")
        hcols.label(text="", icon='BLANK1')           # colonne du bouton preview

        layout.template_list(
            "MULTICAM_UL_cameras", "",
            scene, "multicam_items",
            scene, "multicam_active_index",
            rows=6,
        )

        layout.separator()

        # --- Rendu batch ---
        # --- Sortie Kuwahara ---
        # --- Calques et contour ---
        box = layout.box()
        box.prop(scene, "multicam_split_layers")

        if scene.multicam_split_layers:
            row = box.row(align=True)
            row.operator("multicam.setup_layers", icon='RENDERLAYERS')
            row.operator("multicam.clean_colls", text="", icon='TRASH')

            col = box.column(align=True)
            col.label(text="Contour des personnages :")
            col.prop(scene, "multicam_line_color", text="")
            col.prop(scene, "multicam_line_thick")
            col.prop(scene, "multicam_line_calli")
            r = col.row(align=True)
            r.prop(scene, "multicam_line_noise")
            r.prop(scene, "multicam_line_period")

            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Relancer la configuration apres un changement",
                      icon='INFO')

        # --- Sortie Kuwahara ---
        box = layout.box()
        box.prop(scene, "multicam_kuwahara")

        if scene.multicam_kuwahara:
            col = box.column(align=True)

            row = col.row()
            row.enabled = (scene.render.engine == 'CYCLES')
            row.prop(scene, "multicam_kuwa_denoise")

            col.prop(scene, "multicam_kuwa_mode", expand=True)
            col.prop(scene, "multicam_kuwa_size")

            if scene.multicam_kuwa_mode == 'ANISOTROPIC':
                r = col.row(align=True)
                r.prop(scene, "multicam_kuwa_uniform")
                r.prop(scene, "multicam_kuwa_sharp")
                col.prop(scene, "multicam_kuwa_ecc")

            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="Ecrit dans rendus-Kuwahara au meme rendu", icon='INFO')

        if scene.multicam_running:
            box = layout.box()
            box.label(text="En cours : {}".format(scene.multicam_current_name), icon='RENDER_STILL')

            total = max(scene.multicam_progress_total, 1)
            factor = scene.multicam_progress_current / total
            text = "{} / {}".format(scene.multicam_progress_current, scene.multicam_progress_total)

            row = box.row()
            try:
                row.progress(factor=factor, type='BAR', text=text)
            except AttributeError:
                # Fallback si UILayout.progress() n'existe pas (Blender < 4.0)
                row.label(text=text)

            box.operator("multicam.cancel_render", icon='CANCEL')
        else:
            layout.operator("multicam.render_selected", icon='RENDER_STILL')


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------
classes = (
    MULTICAM_CameraItem,
    MULTICAM_UL_cameras,
    MULTICAM_OT_set_resolution,
    MULTICAM_OT_set_frame,
    MULTICAM_OT_auto_frames,
    MULTICAM_OT_refresh,
    MULTICAM_OT_select_all,
    MULTICAM_OT_select_page,
    MULTICAM_OT_preview,
    MULTICAM_OT_cancel_render,
    MULTICAM_OT_render_selected,
    MULTICAM_PT_panel,
    MULTICAM_OT_make_tree,
    MULTICAM_OT_open_gaufrier,
    MULTICAM_OT_setup_layers,
    MULTICAM_OT_clean_colls,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    if not bpy.app.timers.is_registered(_apply_cameras):
        bpy.app.timers.register(_apply_cameras, first_interval=1.0, persistent=True)

    bpy.types.Scene.multicam_items = bpy.props.CollectionProperty(type=MULTICAM_CameraItem)
    bpy.types.Scene.multicam_active_index = bpy.props.IntProperty(default=0)

    bpy.types.Scene.multicam_output_eevee = bpy.props.StringProperty(
        name="Dossier sortie EEVEE", subtype='DIR_PATH', default=""
    )
    bpy.types.Scene.multicam_output_cycles = bpy.props.StringProperty(
        name="Dossier sortie Cycles", subtype='DIR_PATH', default=""
    )
    bpy.types.Scene.multicam_strip_root = bpy.props.StringProperty(
        name="Racine du strip", subtype='DIR_PATH', default="",
        description="Dossier contenant planches/ (ex: .../Fury Rex/strip-01)")
    bpy.types.Scene.multicam_split_layers = bpy.props.BoolProperty(
        name="Personnages sur un calque separe", default=False,
        description=("Rend decor et personnages en deux calques composes "
                     "ensemble : permet un traitement propre a chacun"))
    bpy.types.Scene.multicam_line_color = bpy.props.FloatVectorProperty(
        name="Couleur du trait", subtype='COLOR', size=4,
        default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)
    bpy.types.Scene.multicam_line_thick = bpy.props.FloatProperty(
        name="Epaisseur", default=3.0, min=0.1, max=40.0)
    bpy.types.Scene.multicam_line_calli = bpy.props.BoolProperty(
        name="Plume", default=True,
        description="Epaisseur variable selon l'orientation, comme une plume")
    bpy.types.Scene.multicam_line_noise = bpy.props.FloatProperty(
        name="Tremble", default=1.5, min=0.0, max=20.0,
        description="Amplitude de la variation d'epaisseur")
    bpy.types.Scene.multicam_line_period = bpy.props.FloatProperty(
        name="Grain", default=25.0, min=1.0, max=400.0,
        description="Longueur d'onde du tremble : petit = nerveux")
    bpy.types.Scene.multicam_kuwahara = bpy.props.BoolProperty(
        name="Sortie Kuwahara", default=False,
        description=("Ecrit en parallele une version picturale dans "
                     "rendus-Kuwahara, sans rendu supplementaire"))
    bpy.types.Scene.multicam_kuwa_denoise = bpy.props.BoolProperty(
        name="Debruiter (OpenImageDenoise)", default=True,
        description=("Indispensable avec Kuwahara : le filtre amplifie le grain. "
                     "Cycles uniquement"))
    bpy.types.Scene.multicam_kuwa_mode = bpy.props.EnumProperty(
        name="Mode", default='ANISOTROPIC',
        items=[('CLASSIC', "Classique", "Plus rapide, aspect plus bloc"),
               ('ANISOTROPIC', "Anisotrope", "Coups de pinceau orientes")])
    bpy.types.Scene.multicam_kuwa_size = bpy.props.FloatProperty(
        name="Taille", default=6.0, min=1.0, max=64.0,
        description="Environ largeur de l'image divisee par 300")
    bpy.types.Scene.multicam_kuwa_uniform = bpy.props.FloatProperty(
        name="Uniformite", default=4.0, min=0.0, max=50.0)
    bpy.types.Scene.multicam_kuwa_sharp = bpy.props.FloatProperty(
        name="Nettete", default=0.5, min=0.0, max=1.0)
    bpy.types.Scene.multicam_kuwa_ecc = bpy.props.FloatProperty(
        name="Elongation", default=2.0, min=0.0, max=4.0)
    bpy.types.Scene.multicam_pages_hint = bpy.props.StringProperty(
        name="Planches", default="01",
        description="Numeros a creer quand aucune camera n'existe encore")
    bpy.types.Scene.multicam_auto_paths = bpy.props.BoolProperty(
        name="Chemins automatiques", default=True,
        description=("Chaque camera ecrit dans planches/<NN>/rendus-<variante>, "
                     "deduit de son nom"))
    bpy.types.Scene.multicam_follow_page = bpy.props.BoolProperty(
        name="Adapter les chemins a la planche",
        description=("En cliquant sur un numero de planche, remplace le numero de dossier "
                     "dans les chemins de sortie (convention .../planches/<NN>/...)"),
        default=True,
    )

    bpy.types.Scene.multicam_progress_current = bpy.props.IntProperty(default=0)
    bpy.types.Scene.multicam_progress_total = bpy.props.IntProperty(default=0)
    bpy.types.Scene.multicam_current_name = bpy.props.StringProperty(default="")
    bpy.types.Scene.multicam_last_error = bpy.props.StringProperty(default="")
    bpy.types.Scene.multicam_running = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.multicam_cancel = bpy.props.BoolProperty(default=False)


def unregister():
    if bpy.app.timers.is_registered(_apply_cameras):
        bpy.app.timers.unregister(_apply_cameras)
        
    stop_gaufrier_server()
    _multicam_remove_handlers()

    del bpy.types.Scene.multicam_cancel
    del bpy.types.Scene.multicam_running
    del bpy.types.Scene.multicam_last_error
    del bpy.types.Scene.multicam_current_name
    del bpy.types.Scene.multicam_progress_total
    del bpy.types.Scene.multicam_progress_current
    del bpy.types.Scene.multicam_follow_page
    del bpy.types.Scene.multicam_output_cycles
    del bpy.types.Scene.multicam_output_eevee
    del bpy.types.Scene.multicam_active_index
    del bpy.types.Scene.multicam_items
    del bpy.types.Scene.multicam_strip_root
    del bpy.types.Scene.multicam_auto_paths
    del bpy.types.Scene.multicam_pages_hint
    del bpy.types.Scene.multicam_kuwahara
    del bpy.types.Scene.multicam_kuwa_mode
    del bpy.types.Scene.multicam_kuwa_size
    del bpy.types.Scene.multicam_kuwa_uniform
    del bpy.types.Scene.multicam_kuwa_sharp
    del bpy.types.Scene.multicam_kuwa_ecc
    del bpy.types.Scene.multicam_split_layers
    del bpy.types.Scene.multicam_line_color
    del bpy.types.Scene.multicam_line_thick
    del bpy.types.Scene.multicam_line_calli
    del bpy.types.Scene.multicam_line_noise
    del bpy.types.Scene.multicam_line_period
    del bpy.types.Scene.multicam_kuwa_denoise

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
