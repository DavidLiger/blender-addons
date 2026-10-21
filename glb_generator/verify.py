# Relecture du GLB écrit : vérifie ce que l'exportateur a réellement produit.
import json
import struct

from . import core


def read_glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("pas un fichier GLB")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ValueError("premier bloc du GLB non JSON")
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))


def _animated_objects(gltf, animation):
    """Noms des nœuds animés par le clip et de tous leurs ancêtres (un os animé compte pour son armature)."""
    nodes = gltf.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    names = set()
    for ch in animation.get("channels", []):
        i = ch.get("target", {}).get("node")
        while i is not None:
            names.add(nodes[i].get("name"))
            i = parent.get(i)
    return names


def verify_glb(path, clips, config_text, n_lights=0, clip_objects=None):
    """Liste des écarts entre le GLB et ce qui était attendu (vide si conforme).
    clip_objects (pistes NLA) : {clip: [armatures qui portent une piste de ce nom]}, chacune doit être animée par le clip
    commun (regroupement par nom de piste)."""
    try:
        gltf = read_glb_json(path)
    except (OSError, ValueError, struct.error) as exc:
        return [f"GLB illisible : {exc}"]
    problems = []
    roots = [n for n in gltf.get("nodes", []) if n.get("name") == core.ROOT_NAME]
    if len(roots) != 1:
        problems.append(f"{len(roots)} nœud(s) {core.ROOT_NAME} dans le GLB (1 attendu)")
    else:
        extras = roots[0].get("extras") or {}
        version = extras.get("glbi_version")
        if type(version) is not int or version != core.GLBI_VERSION:
            problems.append(f"glbi_version vaut {json.dumps(version)} (entier {core.GLBI_VERSION} attendu)")
        if extras.get("glbi_config") != config_text:
            problems.append("glbi_config du GLB différent de celui écrit sur GLBI_ROOT")
    names = [a.get("name") for a in gltf.get("animations", [])]
    missing = sorted(set(clips) - set(names))
    extra = sorted(set(names) - set(clips))
    dup = sorted({n for n in names if names.count(n) > 1})
    if missing:
        problems.append("clips absents du GLB : " + ", ".join(missing))
    if extra:
        problems.append("clips non déclarés dans le GLB : " + ", ".join(map(str, extra)))
    if dup:
        problems.append("clips en double dans le GLB : " + ", ".join(dup))
    for anim in gltf.get("animations", []):
        expected = (clip_objects or {}).get(anim.get("name"))
        if expected:
            missing_objs = sorted(set(expected) - _animated_objects(gltf, anim))
            if missing_objs:
                problems.append(f"clip « {anim.get('name')} » : non commun, sans " + ", ".join(missing_objs))
    lit = [n.get("name") for n in gltf.get("nodes", []) if "KHR_lights_punctual" in (n.get("extensions") or {})]
    if len(lit) != n_lights:
        problems.append(f"{len(lit)} lampe(s) dans le GLB (KHR_lights_punctual), {n_lights} attendue(s)")
    for scene in gltf.get("scenes", []):
        foreign = [k for k in (scene.get("extras") or {}) if not k.startswith("glbi_")]
        if foreign:
            problems.append("propriétés de scène hors glbi_ dans le GLB : " + ", ".join(foreign))
    return problems
