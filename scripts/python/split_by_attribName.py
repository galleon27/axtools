import hou
import re

def split():
    parent = hou.parent()
    root = hou.pwd()
    attribName = root.parm('attrib_name').eval()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib(attribName)

    if (list != None):
        attList = list.strings()
        for i in attList:
            blast = parent.createNode('blast')
            blast.setFirstInput(root, 0)
            blast.moveToGoodPosition()
            blast.parm('group').set("@"+ attribName + "=" + "\"" + i + "\"")
            blast.parm('negate').set(1)


    else:
        print("There is no attibute by that name")







def out():
    context = hou.node('/obj')
    parent = hou.parent()
    root = hou.pwd()
    attribName = root.parm('attrib_name').eval()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib(attribName)
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
            om.parm('group1').set("@" + attribName + "=" + "\"" +originalName + "\"")
            om.parm('xformtype').set(msg)