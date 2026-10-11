# Constantes et fonctions partagées de GLB Generator (sans interface).
import re

import bpy

ROOT_NAME = "GLBI_ROOT"
TAB = "BD"
GLBI_VERSION = 1

_ROOT_RE = re.compile(re.escape(ROOT_NAME) + r"(\.\d+)?")


def root_candidates():
    """Objets du .blend nommés GLBI_ROOT ou GLBI_ROOT.nnn (contrôle 1 : un seul par .blend)."""
    return [o for o in bpy.data.objects if _ROOT_RE.fullmatch(o.name)]


def scene_has_root(scene):
    return any(o.name == ROOT_NAME for o in scene.objects)


def scene_armatures(scene):
    return [o for o in scene.objects if o.type == 'ARMATURE']
