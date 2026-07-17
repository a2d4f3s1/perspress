"""PersPress のシーン設定プロパティ."""
import bpy


class PersPressListItem(bpy.types.PropertyGroup):
    """Groups UIList の 1 行 (グループ行 or メンバー行)."""

    kind: bpy.props.EnumProperty(items=[
        ("GROUP", "Group", ""),
        ("MEMBER", "Member", ""),
    ])
    anchor_name: bpy.props.StringProperty()
    object_name: bpy.props.StringProperty()


def _on_index_update(self, context):
    """行選択 -> アクティブグループ同期 (メンバー行はその親グループ)."""
    if not (0 <= self.list_index < len(self.list_items)):
        return
    item = self.list_items[self.list_index]
    anchor = bpy.data.objects.get(item.anchor_name)
    if (anchor is not None and anchor.type == "EMPTY"
            and "PP_Ratio" in anchor.keys()):
        if self.active_anchor is not anchor:
            self.active_anchor = anchor


class PersPressSettings(bpy.types.PropertyGroup):
    follow_target: bpy.props.PointerProperty(
        name="Follow Target",
        description="アンカーを Child Of で追従させるオブジェクト (任意)",
        type=bpy.types.Object,
    )
    follow_bone: bpy.props.StringProperty(
        name="Bone",
        description="Follow Target がアーマチュアの場合の追従ボーン (任意)",
    )
    active_anchor: bpy.props.PointerProperty(
        name="Active Group",
        description="パネルで操作対象にしているグループのアンカー",
        type=bpy.types.Object,
    )
    list_items: bpy.props.CollectionProperty(type=PersPressListItem)
    list_index: bpy.props.IntProperty(update=_on_index_update)


CLASSES = (PersPressListItem, PersPressSettings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.perspress = bpy.props.PointerProperty(type=PersPressSettings)


def unregister():
    del bpy.types.Scene.perspress
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
