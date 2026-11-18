import hou
import re


def _parm_str(node, name, default=None):
    parm = node.parm(name)
    return parm.evalAsString() if parm is not None else default


def _attrib_class(node):
    """Return 'point' or 'primitive' from the attrib_type menu."""
    cls = _parm_str(node, 'attrib_type', 'primitive')
    return 'point' if cls == 'point' else 'primitive'


def _find_attrib(geo, name, cls):
    if cls == 'point':
        return geo.findPointAttrib(name)
    return geo.findPrimAttrib(name)


def _unique_values(geo, attrib, cls):
    """Return (values, is_string) for a string or int attribute."""
    data_type = attrib.dataType()
    if data_type == hou.attribData.String:
        return list(attrib.strings()), True
    if data_type == hou.attribData.Int:
        if cls == 'point':
            raw = geo.pointIntAttribValues(attrib.name())
        else:
            raw = geo.primIntAttribValues(attrib.name())
        return sorted(set(raw)), False
    raise hou.NodeError(
        f"Attribute '{attrib.name()}' is not a string or int attribute."
    )


def _group_expr(name, value, is_string):
    if is_string:
        return f'@{name}="{value}"'
    return f'@{name}={value}'


def _group_type_token(cls):
    return 'points' if cls == 'point' else 'prims'


def _get_attrib(root):
    cls = _attrib_class(root)
    attrib_name = _parm_str(root, 'attrib_name', '')
    geo = root.geometry()
    attrib = _find_attrib(geo, attrib_name, cls)
    if attrib is None:
        raise hou.NodeError(f"No {cls} attribute named '{attrib_name}'.")
    values, is_string = _unique_values(geo, attrib, cls)
    return cls, attrib_name, values, is_string


def split():
    parent = hou.parent()
    root = hou.pwd()
    cls, attrib_name, values, is_string = _get_attrib(root)

    for value in values:
        blast = parent.createNode('blast')
        blast.setFirstInput(root, 0)
        blast.moveToGoodPosition()
        blast.parm('group').set(_group_expr(attrib_name, value, is_string))
        blast.parm('grouptype').set(_group_type_token(cls))
        blast.parm('negate').set(1)


def out():
    context = hou.node('/obj')
    root = hou.pwd()
    cls, attrib_name, values, is_string = _get_attrib(root)

    msg = hou.ui.displayMessage('Object Merge', buttons=('None', 'Into This Object', 'Into Specified Object'), severity=hou.severityType.Message,
                default_choice=0, close_choice=2, help='Select transform type for object merge', title='Transform',
                details=None, details_label=None, details_expanded=False)

    path = root.path()
    for value in values:
        safe_name = re.sub(r"[^0-9a-zA-Z\.]+", "_", str(value))
        node = context.createNode('geo')
        node.setName(str(hou.parent()) + "_" + safe_name, unique_name=True)
        om = node.createNode('object_merge')
        om.parm('objpath1').set(path)
        om.parm('xformtype').set(msg)
        expr = _group_expr(attrib_name, value, is_string)
        if cls == 'point':
            # Object Merge groups only select primitives, so filter points with a Blast.
            blast = node.createNode('blast')
            blast.setFirstInput(om, 0)
            blast.parm('group').set(expr)
            blast.parm('grouptype').set(_group_type_token(cls))
            blast.parm('negate').set(1)
            blast.setDisplayFlag(True)
            blast.setRenderFlag(True)
            blast.moveToGoodPosition()
        else:
            om.parm('group1').set(expr)
