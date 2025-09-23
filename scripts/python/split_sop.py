import hou
import re

def mat():
    temp = '@shop_materialpath='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('shop_materialpath')

    if (list != None):
        attList = list.strings()
        for i in attList:
            blast = parent.createNode('blast')
            blast.setFirstInput(root, 0)
            blast.moveToGoodPosition()
            blast.parm('group').set(temp + "\"" + i + "\"")
            blast.parm('negate').set(1)

    else:
        print("There are no materials")


def name():

    temp = '@name='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('name')

    if (list != None):
        attList = list.strings()
        for i in attList:
            blast = parent.createNode('blast')
            blast.setFirstInput(root, 0)
            blast.moveToGoodPosition()
            blast.parm('group').set(temp + "\"" + i + "\"")
            blast.parm('negate').set(1)

    else:
        print("There is no name attribute")


def path():
    temp = '@path=='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('path')

    if (list != None):
        attList = list.strings()
        for i in attList:
            blast = parent.createNode('blast')
            blast.setFirstInput(root, 0)
            blast.moveToGoodPosition()
            blast.parm('group').set(temp + "\"" + i + "\"")
            blast.parm('negate').set(1)

    else:
        print("There is no path attribute")


def outName():
    context = hou.node('/obj')
    temp = '@name=='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('name')

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
            om.parm('group1').set(temp + originalName)


def outPath():
    context = hou.node('/obj')
    temp = '@path=='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('path')

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
            om.parm('group1').set(temp + originalName)

def outMat():
    context = hou.node('/obj')
    temp = '@shop_materialpath=='
    parent = hou.parent()
    root = hou.pwd()
    rootGeo = root.geometry()
    list = rootGeo.findPrimAttrib('shop_materialpath')

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
            om.parm('group1').set(temp + originalName)