import hou
import re

def fillMaterial():
    iter = 0
    node = hou.pwd()
    materialNode = hou.node(str(node.path() + '/material1'))
    attribName = node.parm('attribName').eval()
    rootGeo = node.geometry()
    list = rootGeo.findPrimAttrib(attribName)

    if list == None:
        hou.ui.displayMessage("No such attribute")

    else:
        listIter = list.strings()
        materialNode.parm('num_materials').set(len(listIter))

        for l in listIter:
            iter = iter + 1
            materialNode.parm('group' + str(iter)).set("@"+ attribName + "=" + '"' + l + '"')




def createMaterials():
    iter = 0
    root = hou.parent()
    node = hou.pwd()
    materialNode = hou.node(str(node.path() + '/material1'))
    attribName = node.parm('attribName').eval()
    rootGeo = node.geometry()
    list = rootGeo.findPrimAttrib(attribName)
    matnet = node.createNode('matnet')


    if list == None:
        
        hou.ui.displayMessage("No such attribute")

    else:
        listIter = list.strings()
        materialNode.parm('num_materials').set(len(listIter))

    for l in listIter:
        pattern = re.compile(r'[^a-zA-Z0-9_]')
        l = re.sub(pattern, '_', l)
        iter = iter + 1
        material = matnet.createNode('octane_vopnet', l)
        materialPath = material.path()
        material.moveToGoodPosition()
        path = material.path()
        materialNode.parm('shop_materialpath' + str(iter)).set('../matnet1/' + l)