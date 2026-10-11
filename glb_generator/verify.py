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


def verify_glb(path, clips, config_text):
    """Liste des écarts entre le GLB et ce qui était attendu (vide si conforme)."""
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
            problems.append(f"glbi_version vaut {json.dumps(version)} (entier 1 attendu)")
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
    for scene in gltf.get("scenes", []):
        foreign = [k for k in (scene.get("extras") or {}) if not k.startswith("glbi_")]
        if foreign:
            problems.append("propriétés de scène hors glbi_ dans le GLB : " + ", ".join(foreign))
    return problems
