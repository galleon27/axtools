import hou
import os
import re


def octaneSolarisQuickMaterial():
    iteration = 0
    filename = []
    imageset = []
    filtered = []
    
    node = hou.pwd()
    matnet = node.children()[0]
    directory = node.parm('directory').unexpandedString()
    directory_path = node.parm('directory').eval()

    #print(directory)
    if directory == "":
        hou.ui.displayMessage('No directory found')
    else:
        # get image list
        imagesetdir = os.listdir(directory_path)
        # replace space in image name with underscore
        for i in imagesetdir:
            i = i.replace(" ", "_")
            imageset.append(i)
        # create material names from imagesets
        imageset = [s for s in imageset if "Preview" not in s and "preview" not in s]

        #prioritize exr
        unique_base_names = set()
        for file in imageset:
            base_name, extension = os.path.splitext(file)
            if extension == '.exr':
                unique_base_names.add(base_name)
                filtered.append(file)

        for file in imageset:
            base_name, extension = os.path.splitext(file)
            if extension != '.exr' and base_name not in unique_base_names:
                filtered.append(file)
        imageset = filtered

        for imagefile in imageset:
            if imagefile.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                # create filename list
                name = imagefile.split('_')[:len(imagefile.split('_'))-1]
                textureType = imagefile.split('_')[-1]
                #print(os.path.splitext(textureType)[0])
                name = "_".join(name).replace(" ", "_")
                filename.append(name)
                # remove duplicates from list
                filename = list(dict.fromkeys(filename))
                #print(filename)
                
        if matnet.children() == ():
            for name in filename:
                iteration = iteration + 1
                #create collection
                collect = matnet.createNode('collect', name+"_collect")
                #create octane material
                ORmaterial = matnet.createNode('octane_solaris_material_builder', "OR_"+name)
                ORmaterial.moveToGoodPosition()
                ORmaterialnode = ORmaterial.createNode('NT_MAT_UNIVERSAL')
                ORoutput = ORmaterial.path() + "/octane_material"
                ORo = hou.node(ORoutput)
                ORo.setNamedInput('material', ORmaterialnode, 'NT_MAT_UNIVERSAL')
                collect.setInput(0, ORmaterial, 0)

                # material node
                matnet.parm('materials').set(len(filename))
                matnet.parm('matnode' + str(iteration)).set(collect.name())
                matnet.parm('matpath' + str(iteration)).set(collect.name())
#                node.parm('groupnum').set(len(filename))
#                matNode.parm('group'+ str(iteration)).set(node.parm('groupnum' + str(iteration)))
#                matNode.parm('group'+ str(iteration)).set('@shop_materialpath=' + name)
                node.parm('texSets').set(len(filename))
 

                #build shader
                for imagefile in imageset:
                    if imagefile.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                        #print(imagefile)
                        if name + node.parm('ch_baseColor').eval() in imagefile:
                            baseColor = ORmaterial.createNode('NT_TEX_IMAGE', 'COLOR')
                            baseColor.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            ORmaterialnode.setNamedInput('albedo', baseColor, 'NT_TEX_IMAGE')
                            node.parm('basecolordir' + str(iteration)).set(baseColor.parm('A_FILENAME'))

                        if name + node.parm('ch_emissive').eval() in imagefile:
                            emissive = ORmaterial.createNode('NT_TEX_IMAGE', 'emissive')
                            emissive.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            texEmission = ORmaterial.createNode('NT_EMIS_TEXTURE')
                            texEmission.setNamedInput('efficiency_or_texture', emissive, 'NT_TEX_IMAGE')
                            ORmaterialnode.setNamedInput('emission', texEmission, 'NT_EMIS_TEXTURE')
                            node.parm('emissivedir' + str(iteration)).set(emissive.parm('A_FILENAME'))
                        
                        if name + node.parm('ch_metallic').eval() in imagefile:
                            metalness = ORmaterial.createNode('NT_TEX_FLOATIMAGE', 'metallic')
                            metalness.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #metalness.parm('gamma').set(1)
                            metalness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            ORmaterialnode.setNamedInput('metallic', metalness, 'NT_TEX_FLOATIMAGE')
                            node.parm('metalnessdir' + str(iteration)).set(metalness.parm('A_FILENAME'))
                            

                        if name + node.parm('ch_normal').eval() in imagefile:
                            normal = ORmaterial.createNode('NT_TEX_IMAGE', 'normal')
                            normal.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #normal.parm('gamma').set(1)
                            normal.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            ORmaterialnode.setNamedInput('normal', normal, 'NT_TEX_IMAGE')
                            node.parm('normaldir' + str(iteration)).set(normal.parm('A_FILENAME'))
                            
                        if name + node.parm('ch_roughness').eval() in imagefile:
                            roughness = ORmaterial.createNode('NT_TEX_FLOATIMAGE', 'roughness')
                            roughness.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #roughness.parm('gamma').set(1)
                            roughness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            ORmaterialnode.setNamedInput('roughness', roughness, 'NT_TEX_FLOATIMAGE')
                            node.parm('roughnessdir' + str(iteration)).set(roughness.parm('A_FILENAME'))

                        if name + node.parm('ch_opacity').eval() in imagefile:
                            opacity = ORmaterial.createNode('NT_TEX_FLOATIMAGE', 'opacity')
                            opacity.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #opacity.parm('gamma').set(1)
                            opacity.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            ORmaterialnode.setNamedInput('opacity', opacity, 'NT_TEX_FLOATIMAGE')
                            node.parm('opacitydir' + str(iteration)).set(opacity.parm('A_FILENAME'))

                        if name + node.parm('ch_displacement').eval() in imagefile:
                            displacement = ORmaterial.createNode('NT_TEX_FLOATIMAGE', 'displacement')
                            displacement.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            displacementNode = ORmaterial.createNode('NT_VERTEX_DISPLACEMENT')
                            displacementNode.setNamedInput('texture', displacement, 'NT_TEX_FLOATIMAGE')
                            displacementNode.parm('black_level').set(0.5)
                            #displacement.parm('gamma').set(1)
                            displacement.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            ORmaterialnode.setNamedInput('displacement', displacementNode, 'NT_VERTEX_DISPLACEMENT')
                            node.parm('displacementdir' + str(iteration)).set(displacement.parm('A_FILENAME'))