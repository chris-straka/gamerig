# SPDX-License-Identifier: GPL-2.0-or-later

"""Properties-panel UI for rigforge game export, diagnostics, and weights."""

import bpy


def _draw_cleanup_row(layout, wm, items):
    """Draw a row of cleanup weight operators with shared panel settings."""
    row = layout.row(align=True)
    for mode, text in items:
        op = row.operator("wm.rigforge_cleanup", text=text)
        op.mode = mode
        op.limit = wm.rigforge_cleanup_limit
        op.threshold = wm.rigforge_cleanup_threshold
        op.passes = wm.rigforge_cleanup_passes
        op.subset = wm.rigforge_cleanup_subset


# noinspection PyPep8Naming
class DATA_PT_rigforge_game_export(bpy.types.Panel):
    bl_label = "Rigforge Game Export"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "ARMATURE" and "rig_id" in obj.data)

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        row = layout.row(align=True)
        full = row.operator("wm.rigforge_game_export", text="Full GLB", icon="EXPORT")
        full.profile = "FULL"
        mobile = row.operator("wm.rigforge_game_export", text="Mobile GLB")
        mobile.profile = "MOBILE"
        mobile.mobile_limit_4 = wm.rigforge_mobile_limit_4
        layout.prop(wm, "rigforge_mobile_limit_4")


# noinspection PyPep8Naming
class DATA_PT_rigforge_diagnose(bpy.types.Panel):
    bl_label = "Rigforge Diagnose"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "ARMATURE" and "rig_id" not in obj.data)

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.rigforge_diagnose_metarig", text="Diagnose Metarig")


# noinspection PyPep8Naming
class DATA_PT_rigforge_voxel_weights(bpy.types.Panel):
    bl_label = "Rigforge Voxel Weights"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH")

    def draw(self, context):
        layout = self.layout
        layout.operator(
            "wm.rigforge_voxel_weights",
            text="Voxel Auto-Weights",
            icon="MOD_VERTEX_WEIGHT",
        )


# noinspection PyPep8Naming
class DATA_PT_rigforge_nudge(bpy.types.Panel):
    bl_label = "Rigforge Nudge"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH")

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.prop(wm, "rigforge_nudge_bone")
        layout.prop(wm, "rigforge_nudge_value", slider=True)
        row = layout.row(align=True)
        pin = row.operator("wm.rigforge_nudge", text="Pin")
        pin.mode = "PIN"
        pin.bone = wm.rigforge_nudge_bone
        pin.value = wm.rigforge_nudge_value
        exc = row.operator("wm.rigforge_nudge", text="Exclude")
        exc.mode = "EXCLUDE"
        exc.bone = wm.rigforge_nudge_bone
        clr = row.operator("wm.rigforge_nudge", text="Clear")
        clr.mode = "CLEAR"


# noinspection PyPep8Naming
class DATA_PT_rigforge_transfer(bpy.types.Panel):
    bl_label = "Rigforge Piece Transfer"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH")

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.prop_search(
            wm, "rigforge_transfer_source", bpy.data, "objects", text="Source"
        )
        layout.prop(wm, "rigforge_transfer_dist")
        op = layout.operator("wm.rigforge_piece_transfer", text="Transfer")
        op.source = wm.rigforge_transfer_source
        op.max_dist = wm.rigforge_transfer_dist


# noinspection PyPep8Naming
class DATA_PT_rigforge_cleanup(bpy.types.Panel):
    bl_label = "Rigforge Cleanup"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH")

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.prop(wm, "rigforge_cleanup_limit")
        layout.prop(wm, "rigforge_cleanup_threshold", slider=True)
        layout.prop(wm, "rigforge_cleanup_passes")
        layout.prop(wm, "rigforge_cleanup_subset")
        _draw_cleanup_row(
            layout,
            wm,
            (("LIMIT", "Limit"), ("NORMALIZE", "Norm"), ("PRUNE", "Prune")),
        )
        _draw_cleanup_row(
            layout,
            wm,
            (("MIRROR", "Mirror"), ("SMOOTH", "Smooth")),
        )


classes = (
    DATA_PT_rigforge_game_export,
    DATA_PT_rigforge_voxel_weights,
    DATA_PT_rigforge_nudge,
    DATA_PT_rigforge_transfer,
    DATA_PT_rigforge_cleanup,
    DATA_PT_rigforge_diagnose,
)


def register():
    from bpy.utils import register_class

    for cls in classes:
        register_class(cls)


def unregister():
    from bpy.utils import unregister_class

    for cls in classes:
        unregister_class(cls)
