# Mini-jeu (v0.4, spec-mini-jeu.md §5, §9, §16) : couche 3D « jeu » d'une entité ou de l'arène, mesure de la capsule,
# nœuds de l'arène reconnus par leur nom, triangles des collisionneurs, taille des textures.
import math

import bpy
from mathutils import Vector

from . import core, prefs

# Budgets du gabarit « poursuite » mesurables dans Blender (POURSUITE_BUDGETS de book-check.ts) : avertissements.
BUDGET_CAPSULE_RADIUS = 1.2
BUDGET_COLLIDER_TRIANGLES = 5000
BUDGET_TEXTURE_PX = 1024


def kind(scene):
    return scene.glbg.kind


def entity_clips(scene):
    """{rôle: Action} des rôles de clip renseignés, dans l'ordre du contrat."""
    g = scene.glbg.game
    out = {}
    for role in core.GAME_CLIP_ROLES:
        act = getattr(g, "clip_" + role)
        if act is not None:
            out[role] = act
    return out


def entity_actions(scene):
    """Actions des rôles de clip, sans doublon (une Action peut servir deux rôles)."""
    return list(dict.fromkeys(entity_clips(scene).values()))


def collider_object(scene):
    """Empty COLLIDER de la scène (nom exact), ou None."""
    obj = scene.objects.get(core.GAME_COLLIDER_NAME)
    return obj if obj is not None and obj.type == 'EMPTY' else None


# ---------------------------------------------------------------------------
# Capsule (choix A de David, 29/10) : maillages de l'entité à la première image d'idle, en coordonnées monde.
# Rayon = moitié du côté étroit de l'emprise au sol ; hauteur = sommet du personnage (pieds à l'origine), ou hauteur
# totale des maillages avec un empty COLLIDER (le moteur centre alors la capsule sur l'empty) ; hauteur portée au
# diamètre au besoin (règle du contrat). Valeurs arrondies au millimètre supérieur.
# ---------------------------------------------------------------------------
_cache = {}


def begin():
    """Nouvelle vérification : la mesure suivante est refaite (cache par scène pour un même passage de contrôles)."""
    _cache.clear()


def last_measure(scene):
    """Dernière mesure faite (panneau : dessiner ne doit pas changer l'image courante)."""
    return _cache.get(scene.name)


def _ceil_mm(x):
    return math.ceil(round(x * 1000, 2)) / 1000


def _mesh_bounds(scene):
    lo = hi = None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        ob = obj.evaluated_get(depsgraph)
        mesh = ob.to_mesh()
        try:
            mw = ob.matrix_world
            for v in mesh.vertices:
                p = mw @ v.co
                lo = p.copy() if lo is None else Vector(map(min, lo, p))
                hi = p.copy() if hi is None else Vector(map(max, hi, p))
        finally:
            ob.to_mesh_clear()
    return lo, hi


def _measure(scene):
    g = scene.glbg.game
    arm, idle = g.obj, g.clip_idle
    snapshot = core.snapshot_poses(scene)
    saved = None
    pose = "pose affichée"
    try:
        if arm is not None and arm.type == 'ARMATURE' and core.is_armature_action(idle, arm):
            ad = arm.animation_data or arm.animation_data_create()
            saved = (ad, ad.action, ad.action_slot, ad.use_nla)
            ad.action = idle
            if ad.action_slot is None:
                slots = [s for s in idle.slots if s.target_id_type == 'OBJECT']
                if slots:
                    ad.action_slot = slots[0]
            ad.use_nla = False
            frame = int(idle.frame_range[0])
            scene.frame_set(frame)
            pose = f"idle, image {frame}"
        lo, hi = _mesh_bounds(scene)
    finally:
        if saved is not None:
            ad, action, slot, use_nla = saved
            try:
                ad.action = action
                if action is not None and slot is not None:
                    ad.action_slot = slot
                ad.use_nla = use_nla
            except ReferenceError:
                pass
        core.restore_poses(scene, snapshot)
    if lo is None:
        return None
    col = collider_object(scene)
    radius = _ceil_mm(min(hi.x - lo.x, hi.y - lo.y) / 2)
    top = hi.z - lo.z if col is not None else hi.z
    return {
        "lo": tuple(lo), "hi": tuple(hi), "pose": pose,
        "radius": radius,
        "height": _ceil_mm(max(top, 2 * radius)) if top > 0 else 0.0,
        "collider": col.matrix_world.translation.copy() if col is not None else None,
    }


def measure(scene):
    """Mesure des maillages de l'entité (dict) ou None s'il n'y a aucun maillage."""
    if scene.name not in _cache:
        _cache[scene.name] = _measure(scene)
    return _cache[scene.name]


def capsule(scene):
    """(rayon, hauteur) écrits dans la couche 3D : saisis, ou mesurés ; (0, 0) si la mesure est impossible."""
    g = scene.glbg.game
    if g.capsule_manual:
        return round(g.radius, 3), round(g.height, 3)
    m = measure(scene)
    if m is None:
        return 0.0, 0.0
    return m["radius"], m["height"]


# ---------------------------------------------------------------------------
# Couche 3D du jeu (contrat de MJ1, game3dEntitySchema et game3dArenaSchema), clés dans l'ordre du contrat.
# ---------------------------------------------------------------------------
def build_config(scene):
    s = scene.glbg
    if s.kind == core.KIND_ARENA:
        return {"version": core.GAME3D_VERSION, "kind": "arena", "id": core.GAME_ARENA_ID}
    g = s.game
    radius, height = capsule(scene)
    config = {
        "version": core.GAME3D_VERSION,
        "kind": "entity",
        "id": prefs.game_id(s),
        "role": g.role,
        "object": g.obj.name if g.obj is not None else "",
        "clips": {role: act.name for role, act in entity_clips(scene).items()},
        "capsule": {"radius": radius, "height": height},
    }
    if collider_object(scene) is not None:
        config["collider"] = core.GAME_COLLIDER_NAME
    return config


# ---------------------------------------------------------------------------
# Arène : nœuds reconnus par leur nom (objets de la scène, nom = nom du nœud glTF).
# ---------------------------------------------------------------------------
def arena_nodes(scene):
    objs = sorted(scene.objects, key=lambda o: o.name)
    return {
        "spawn_player": [o for o in objs if o.name == core.SPAWN_PLAYER],
        "spawn_prey": [o for o in objs if o.name.startswith(core.SPAWN_PREY_PREFIX)],
        "colliders": [o for o in objs if o.name.startswith(core.COL_PREFIX)],
        "fade": [o for o in objs if o.name.startswith(core.FADE_PREFIX)],
    }


def has_mesh(obj):
    """Objet qui sort comme nœud à maillage : objet Mesh avec au moins une face (un maillage vide n'est pas exporté)."""
    return obj.type == 'MESH' and obj.data is not None and len(obj.data.polygons) > 0


def triangles(obj):
    """Triangles du maillage tel qu'exporté (modificateurs non appliqués, comme l'exportateur par défaut)."""
    if not has_mesh(obj):
        return 0
    return sum(p.loop_total - 2 for p in obj.data.polygons)


def big_textures(scene):
    """[(image, largeur, hauteur)] des images des matériaux des maillages de la scène au-delà du budget."""
    seen, out = set(), []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            for node in mat.node_tree.nodes:
                img = getattr(node, "image", None) if node.type == 'TEX_IMAGE' else None
                if img is None or img.name in seen:
                    continue
                seen.add(img.name)
                w, h = img.size
                if max(w, h) > BUDGET_TEXTURE_PX:
                    out.append((img, w, h))
    return out
