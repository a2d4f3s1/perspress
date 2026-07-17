"""PersPress N パネル UI."""
import bpy

from . import ops


class PERSPRESS_PT_main(bpy.types.Panel):
    bl_label = "PersPress"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PersPress"

    def draw(self, context):
        pass


class _SubPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PersPress"
    bl_parent_id = "PERSPRESS_PT_main"


class PERSPRESS_PT_setup(_SubPanel, bpy.types.Panel):
    bl_label = "Setup"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.perspress
        layout.prop(settings, "follow_target")
        if (settings.follow_target
                and settings.follow_target.type == "ARMATURE"):
            layout.prop_search(settings, "follow_bone",
                               settings.follow_target.data, "bones")
        layout.operator(ops.PERSPRESS_OT_setup.bl_idname, icon="ADD")


class PERSPRESS_UL_groups(bpy.types.UIList):
    """グループとメンバーを入れ子表示するリスト (行選択 = アクティブグループ)."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname):
        row = layout.row(align=True)
        if item.kind == "GROUP":
            anchor = bpy.data.objects.get(item.anchor_name)
            is_open = anchor.get("PP_ui_open", True) if anchor else True
            op = row.operator(ops.PERSPRESS_OT_toggle_expand.bl_idname,
                              text="", emboss=False,
                              icon="TRIA_DOWN" if is_open else "TRIA_RIGHT")
            op.anchor_name = item.anchor_name
            row.label(text=item.name, icon="EMPTY_AXIS")
            op = row.operator(ops.PERSPRESS_OT_remove.bl_idname,
                              text="", icon="TRASH", emboss=False)
            op.anchor_name = item.anchor_name
        else:
            row.separator(factor=2.0)
            row.label(text=item.name)
            op = row.operator(ops.PERSPRESS_OT_remove_member.bl_idname,
                              text="", icon="X", emboss=False)
            op.object_name = item.object_name

    def draw_filter(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "filter_name", text="", icon="VIEWZOOM")
        active = ops.active_anchor(context)
        if active is not None:
            col = layout.column(align=True)
            col.prop(active, "name", text="Name")
            row = col.row(align=True)
            row.prop(active, "empty_display_type", text="")
            row.prop(active, "empty_display_size", text="Size")


class PERSPRESS_PT_groups(_SubPanel, bpy.types.Panel):
    bl_label = "Groups"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.perspress

        row = layout.row()
        row.template_list("PERSPRESS_UL_groups", "", settings, "list_items",
                          settings, "list_index", rows=5)
        col = row.column(align=True)
        col.operator(ops.PERSPRESS_OT_add_members.bl_idname,
                     text="", icon="ADD")


class PERSPRESS_PT_control(_SubPanel, bpy.types.Panel):
    bl_label = "Control"

    def draw(self, context):
        layout = self.layout
        active = ops.active_anchor(context)
        if active is None:
            layout.label(text="(アクティブグループなし)", icon="INFO")
            return
        col = layout.column(align=True)
        col.prop(active, '["PP_Ratio"]', text="Ratio")
        col.prop(active, '["PP_Influence"]', text="Influence", slider=True)
        col.separator()
        col.prop(active, '["PP_FalloffStart"]', text="Falloff Start")
        col.prop(active, '["PP_FalloffEnd"]', text="Falloff End")

        shape = active.get("PP_FalloffShape", 0)
        row = layout.row(align=True)
        row.label(text="Shape:")
        for value, label, icon in ((0, "球", "SPHERE"), (1, "立方体", "CUBE")):
            op = row.operator(ops.PERSPRESS_OT_set_falloff_shape.bl_idname,
                              text=label, icon=icon, depress=(shape == value))
            op.shape = value

        row = layout.row(align=True)
        row.label(text="表示:")
        for giz in ops.gizmos_of(active):
            label = "Start" if "start" in giz.name else "End"
            row.prop(giz, "hide_viewport", text=label,
                     toggle=True, invert_checkbox=True)
        preview = ops.preview_of(active)
        if preview is not None:
            row.prop(preview, "hide_viewport", text="Preview",
                     toggle=True, invert_checkbox=True)


CLASSES = (
    PERSPRESS_UL_groups,
    PERSPRESS_PT_main,
    PERSPRESS_PT_setup,
    PERSPRESS_PT_groups,
    PERSPRESS_PT_control,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
