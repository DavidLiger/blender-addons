# Données de l'addon → glbi_config (spec-extras-blender.md §4), écrit sur GLBI_ROOT.
import json

from . import core


def _text(settings, prefix):
    return {lang: getattr(settings, f"{prefix}_{lang}").strip() for lang in ("fr", "en", "es")}


def _name(id_block):
    return id_block.name if id_block is not None else ""


def build_config(scene):
    """Dictionnaire glbi_config, clés dans l'ordre du contrat ; champs facultatifs vides omis."""
    s = scene.glbg
    scene_part = {"id": s.scene_id.strip(), "name": _text(s, "name")}
    if s.loader_image.strip():
        scene_part["loaderImage"] = s.loader_image.strip()

    actions = []
    for item in s.actions:
        action = {
            "id": item.ident.strip(),
            "object": _name(item.obj),
            "type": "armature",
            "clip": _name(item.action),
            "trigger": "button",
            "label": _text(item, "label"),
        }
        if item.icon.strip():
            action["icon"] = item.icon.strip()
        if item.sound.strip():
            action["sound"] = item.sound.strip()
        action["loop"] = bool(item.loop)
        actions.append(action)

    return {
        "version": core.GLBI_VERSION,
        "scene": scene_part,
        "modules": {
            "animations": {
                "mode": "buttons",
                "idle": {"object": _name(s.idle.obj), "clip": _name(s.idle.action), "loop": True},
                "actions": actions,
            },
        },
    }


def to_json(config):
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def declared_clips(scene):
    """Noms des Actions déclarées (idle + actions), sans doublon."""
    s = scene.glbg
    names = [a.name for a in [s.idle.action, *(i.action for i in s.actions)] if a is not None]
    return list(dict.fromkeys(names))


def write_root(root, scene):
    """Écrit glbi_version (entier 1) et glbi_config (chaîne JSON) ; renvoie la chaîne écrite."""
    text = to_json(build_config(scene))
    root["glbi_version"] = core.GLBI_VERSION
    root["glbi_config"] = text
    # Piège de JSON.parse("1.0") : la relecture doit donner un objet.
    if not isinstance(json.loads(root["glbi_config"]), dict) or root["glbi_version"] != 1:
        raise RuntimeError("relecture de glbi_config incorrecte")
    return text
