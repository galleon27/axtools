import hou
import re

def split():
    parent = hou.parent()
    root = hou.pwd()
    attrib_name = root.parm('attrib_name').eval()
    rootGeo = root.geometry()
    attrib = rootGeo.findPrimAttrib(attrib_name)


    if attrib is not None:
        attrib_type = attrib.dataType()

        if attrib_type == hou.attribData.String:
            attributes = attrib.strings()
            for attribute_value in attributes:
                blast = parent.createNode('blast')
                blast.setFirstInput(root, 0)
                blast.moveToGoodPosition()
                blast.parm('group').set(f"@{attrib_name}=\"{attribute_value}\"")
                blast.parm('negate').set(1)

        elif attrib_type == hou.attribData.Int:
            if rootGeo.findPrimAttrib(attrib_name):
                raw_int_values = rootGeo.primIntAttribValues(attrib_name)
                unique_string_values = [str(value) for value in set(raw_int_values)]

                for string in unique_string_values:
                    blast = parent.createNode('blast')
                    blast.setFirstInput(root, 0)
                    blast.moveToGoodPosition()
                    blast.parm('group').set(f"@{attrib_name}={string}")
                    blast.parm('negate').set(1)
    else:
        print(f"There is no attribute named '{attrib_name}'")


def out():
    context = hou.node('/obj')
    parent = hou.parent()
    root = hou.pwd()
    attrib_name = root.parm('attrib_name').eval()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib(attrib_name)
    msg = hou.ui.displayMessage('Object Merge', buttons=('None', 'Into This Object', 'Into Specified Object'), severity=hou.severityType.Message, 
                default_choice=0, close_choice=2, help='Select transform type for object merge', title='Transform', 
                details=None, details_label=None, details_expanded=False)
    if (list != None):
        attList = list.strings()
        for i in attList:
            originalName = i;
            i = re.sub("[^0-9a-zA-Z\.]+", "_", i)
            node = context.createNode('geo')
            node.setName(str(hou.parent()) + "_" + i, unique_name=True)
            path = hou.pwd().path()
            om = node.createNode('object_merge')
            om.parm('objpath1').set(path)
            om.parm('group1').set("@" + attrib_name + "=" + "\"" +originalName + "\"")
            om.parm('xformtype').set(msg)