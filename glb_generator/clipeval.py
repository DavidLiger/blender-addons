# Évaluation des clips dans Blender (contrôle 24) : position de chaque personnage à la première et à la dernière image
# d'un clip, comparée à sa position à la première image d'idle (celle d'où le lecteur part et où il revient).
#
# Le clip est joué comme l'exportateur l'échantillonne : source Actions, l'Action seule (NLA coupé) ; source pistes
# NLA, la piste du clip en solo sur chaque armature (Action active retirée). Tout est rétabli à la sortie, y compris
# en cas d'erreur : Action et slot actifs, NLA, solos, pose, image courante.
import bpy
from mathutils import Vector

from . import core, serialize


def _center(arm, depsgraph):
    """Position du personnage : centre des têtes d'os, en coordonnées monde."""
    ob = arm.evaluated_get(depsgraph)
    heads = [ob.matrix_world @ pb.head for pb in ob.pose.bones]
    if not heads:
        return ob.matrix_world.translation.copy()
    return sum(heads, Vector()) / len(heads)


def scene_radius(scene, depsgraph):
    """Rayon de la sphère englobante des maillages de la scène (demi-diagonale de la boîte, comme le lecteur)."""
    lo = hi = None
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        ob = obj.evaluated_get(depsgraph)
        for corner in ob.bound_box:
            p = ob.matrix_world @ Vector(corner)
            lo = p.copy() if lo is None else Vector(map(min, lo, p))
            hi = p.copy() if hi is None else Vector(map(max, hi, p))
    return (hi - lo).length / 2 if lo is not None else None


class ClipPlayer:
    """with ClipPlayer(scene) as player: player.positions(clip) → {armature: (position début, position fin)}."""

    def __init__(self, scene):
        self.scene = scene
        self.nla = serialize.is_nla(scene)

    def __enter__(self):
        scene = self.scene
        self.snapshot = core.snapshot_poses(scene)
        self.saved = []
        for arm in core.scene_armatures(scene):
            ad = arm.animation_data
            if ad is None:
                continue
            self.saved.append((arm, ad.action, ad.action_slot, ad.use_nla,
                               [(t, t.is_solo, t.mute) for t in ad.nla_tracks]))
        return self

    def __exit__(self, *exc):
        for arm, action, slot, use_nla, tracks in self.saved:
            try:
                ad = arm.animation_data
                ad.action = action
                if action is not None and slot is not None:
                    ad.action_slot = slot
                ad.use_nla = use_nla
                for t, _solo, mute in tracks:
                    t.is_solo = False
                    t.mute = mute
                for t, solo, _mute in tracks:
                    if solo:
                        t.is_solo = True
            except ReferenceError:
                pass
        core.restore_poses(self.scene, self.snapshot)
        return False

    def _ranges(self, clip, arms):
        """{armature: (première, dernière image)} des armatures que le clip anime, préparées pour le jouer."""
        out = {}
        for arm in arms:
            ad = arm.animation_data
            if ad is None:
                continue
            if self.nla:
                tracks = core.tracks_named(arm, clip)
                rng = core.track_range(tracks[0]) if tracks else None
                if rng is None:
                    continue
                ad.action = None
                ad.use_nla = True
                tracks[0].is_solo = True
            else:
                act = bpy.data.actions.get(clip)
                if act is None or not core.is_armature_action(act, arm):
                    continue
                ad.action = act
                if ad.action_slot is None:
                    slots = [s for s in act.slots if s.target_id_type == 'OBJECT']
                    if slots:
                        ad.action_slot = slots[0]
                ad.use_nla = False
                rng = (int(act.frame_range[0]), int(act.frame_range[1]))
            out[arm] = rng
        return out

    def positions(self, clip, arms):
        """{armature: (position à la première image, position à la dernière image)}."""
        ranges = self._ranges(clip, arms)
        frames = sorted({f for r in ranges.values() for f in r})
        at = {}
        for frame in frames:
            self.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            for arm, rng in ranges.items():
                if frame in rng:
                    at[(arm, frame)] = _center(arm, depsgraph)
        return {arm: (at[(arm, r[0])], at[(arm, r[1])]) for arm, r in ranges.items()}

    def radius_at_idle_start(self, clip, arms):
        """Rayon de la scène à la première image d'idle (le lecteur le mesure là)."""
        ranges = self._ranges(clip, arms)
        if not ranges:
            return None
        self.scene.frame_set(min(r[0] for r in ranges.values()))
        return scene_radius(self.scene, bpy.context.evaluated_depsgraph_get())
