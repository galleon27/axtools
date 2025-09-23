import hou
import os
import re


def createOctaneMaterial():
    node = hou.pwd()
    iteration = 0
    #excluded = ['Preview', 'preview']
    filename = []
    imageset = []
    filtered = []
    channels = ['albedo', 'basecolor',
                'roughness', 'metallic',
                'normal', 'emissive',
                'displacement']
                
    directory = node.parm('directory').unexpandedString()
    directory_path = node.parm('directory').eval()
    matNode = hou.node(str(node.path() + '/material1'))
    
    #create material network
    matnet = hou.node(node.path() + '/AX_MATNET')  
    if (matnet == None):
        matnet = node.createNode('matnet', 'AX_MATNET')

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

        # create material        
        if matnet.children() == ():
            for name in filename:
                iteration = iteration + 1
                material = matnet.createNode('octane_vopnet', name)
                material.moveToGoodPosition()
                materialnode = material.createNode('NT_MAT_UNIVERSAL')
                output = material.path() + "/octane_material1"
                o = hou.node(output)
                o.setNamedInput('material', materialnode, 'NT_MAT_UNIVERSAL')
                # material node
                matNode.parm('num_materials').set(len(filename))
                matNode.parm('shop_materialpath' + str(iteration)).set('../AX_MATNET/' + name)
                node.parm('groupnum').set(len(filename))
                matNode.parm('group'+ str(iteration)).set(node.parm('groupnum' + str(iteration)))
                matNode.parm('group'+ str(iteration)).set('@shop_materialpath=' + name)
                node.parm('texSets').set(len(filename))


        
                # build shader
                for imagefile in imageset:
                    if imagefile.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                        #print(imagefile)

                        if name + node.parm('ch_baseColor').eval() in imagefile:
                            baseColor = material.createNode('NT_TEX_IMAGE', 'COLOR')
                            baseColor.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            materialnode.setNamedInput('albedo', baseColor, 'NT_TEX_IMAGE')
                            node.parm('basecolordir' + str(iteration)).set(baseColor.parm('A_FILENAME'))


                        if name + node.parm('ch_emissive').eval() in imagefile:
                            emissive = material.createNode('NT_TEX_IMAGE', 'emissive')
                            emissive.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            texEmission = material.createNode('NT_EMIS_TEXTURE')
                            texEmission.setNamedInput('efficiency_or_texture', emissive, 'NT_TEX_IMAGE')
                            materialnode.setNamedInput('emission', texEmission, 'NT_EMIS_TEXTURE')
                            node.parm('emissivedir' + str(iteration)).set(emissive.parm('A_FILENAME'))
                        
                        if name + node.parm('ch_metallic').eval() in imagefile:
                            metalness = material.createNode('NT_TEX_FLOATIMAGE', 'metallic')
                            metalness.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #metalness.parm('gamma').set(1)
                            metalness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('metallic', metalness, 'NT_TEX_FLOATIMAGE')
                            node.parm('metalnessdir' + str(iteration)).set(metalness.parm('A_FILENAME'))
                            
                        if name + node.parm('ch_normal').eval() in imagefile:
                            normal = material.createNode('NT_TEX_IMAGE', 'normal')
                            normal.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            #normal.parm('gamma').set(1)
                            normal.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('normal', normal, 'NT_TEX_IMAGE')
                            node.parm('normaldir' + str(iteration)).set(normal.parm('A_FILENAME'))

                        if name + node.parm('ch_opacity').eval() in imagefile:
                            opacity = material.createNode('NT_TEX_IMAGE', 'COLOR')
                            opacity.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            materialnode.setNamedInput('albedo', opacity, 'NT_TEX_IMAGE')
                            node.parm('opacitydir' + str(iteration)).set(opacity.parm('A_FILENAME'))

                            
                        if name + node.parm('ch_roughness').eval() in imagefile:
                            roughness = material.createNode('NT_TEX_FLOATIMAGE', 'roughness')
                            roughness.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            roughness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('roughness', roughness, 'NT_TEX_FLOATIMAGE')
                            node.parm('roughnessdir' + str(iteration)).set(roughness.parm('A_FILENAME'))

                        if name + node.parm('ch_displacement').eval() in imagefile:
                            displacement = material.createNode('NT_TEX_FLOATIMAGE', 'displacement')
                            displacement.parm('A_FILENAME').set(os.path.join(directory, imagefile))
                            displacementNode = material.createNode('NT_VERTEX_DISPLACEMENT')
                            displacementNode.setNamedInput('texture', displacement, 'NT_TEX_FLOATIMAGE')
                            displacementNode.parm('black_level').set(0.5)
                            #displacement.parm('gamma').set(1)
                            displacement.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('displacement', displacementNode, 'NT_VERTEX_DISPLACEMENT')
                            node.parm('displacementdir' + str(iteration)).set(displacement.parm('A_FILENAME'))
                            






def createRedshiftMaterial():
    node = hou.pwd()
    iteration = 0
    #excluded = ['Preview', 'preview']
    filename = []
    imageset = []
    channels = ['albedo', 'basecolor',
                'roughness', 'metallic',
                'normal', 'emissive',
                'displacement']
                
    directory = node.parm('directory').unexpandedString()
    directory_path = node.parm('directory').eval()
    matNode = hou.node(str(node.path() + '/material1'))
    
    #create material network
    matnet = hou.node(node.path() + '/AX_MATNET')  
    if (matnet == None):
        matnet = node.createNode('matnet', 'AX_MATNET')

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

        # create material        
        if matnet.children() == ():
            for name in filename:
                iteration = iteration + 1
                material = matnet.createNode('redshift_vopnet', name)
                material.moveToGoodPosition()
                materialnode = material.path() + "/StandardMaterial1"
                materialnode = hou.node(materialnode)

                materialoutnode = material.path() + "/redshift_material1"
                materialoutnode = hou.node(materialoutnode)

                #output = material.path() + "/redshift_material1"
                #o = hou.node(output)
                #o.setNamedInput('material', materialnode, 'NT_MAT_UNIVERSAL')
                # material node
                matNode.parm('num_materials').set(len(filename))
                matNode.parm('shop_materialpath' + str(iteration)).set('../AX_MATNET/' + name)
                node.parm('groupnum').set(len(filename))
                matNode.parm('group'+ str(iteration)).set(node.parm('groupnum' + str(iteration)))
                matNode.parm('group'+ str(iteration)).set('@shop_materialpath=' + name)
                node.parm('texSets').set(len(filename))


        
                #build shader

                imageset.sort(key=lambda x: not x.lower().endswith('.exr'))
                for imagefile in imageset:
                    if imagefile.lower().endswith(('.exr', '.png', '.tga', '.tif', '.jpg')):
                        #print(imagefile)
                        if name + node.parm('ch_baseColor').eval() in imagefile:
                            baseColor = material.createNode('redshift::TextureSampler', 'color')
                            baseColor.parm('tex0').set(os.path.join(directory, imagefile))
                            materialnode.setNamedInput('base_color', baseColor, 'outColor')
                            node.parm('basecolordir' + str(iteration)).set(baseColor.parm('tex0'))

                        if name + node.parm('ch_emissive').eval() in imagefile:
                            emissive = material.createNode('redshift::TextureSampler', 'emissive')
                            emissive.parm('tex0').set(os.path.join(directory, imagefile))
                            materialnode.setNamedInput('emission_color', texEmission, 'outColor')
                            node.parm('emissivedir' + str(iteration)).set(emissive.parm('tex0'))
                        
                        if name + node.parm('ch_metallic').eval() in imagefile:
                            metalness = material.createNode('redshift::TextureSampler', 'metallic')
                            metalness.parm('tex0').set(os.path.join(directory, imagefile))
                            #metalness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('metalness', metalness, 'outColor')
                            node.parm('metalnessdir' + str(iteration)).set(metalness.parm('tex0'))
                            

                        if name + node.parm('ch_normal').eval() in imagefile:
                            normal = material.createNode('redshift::TextureSampler', 'normal')
                            normal.parm('tex0').set(os.path.join(directory, imagefile))
                            normal_map = material.createNode("redshift::BumpMap", "normal_map")
                            normal_map.setNamedInput('input', normal, 'outColor')
                            normal_map.parm('inputType').set('1')
                            #normal.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialnode.setNamedInput('bump_input', normal_map, 'out')
                            node.parm('normaldir' + str(iteration)).set(normal.parm('tex0'))
                            
                        if name + node.parm('ch_roughness').eval() in imagefile:
                            roughness = material.createNode('redshift::TextureSampler', 'roughness')
                            roughness.parm('tex0').set(os.path.join(directory, imagefile))
                            #roughness.parm('gamma').set(1)
                            materialnode.setNamedInput('refl_roughness', roughness, 'outColor')
                            node.parm('roughnessdir' + str(iteration)).set(roughness.parm('tex0'))

                        if name + node.parm('ch_displacement').eval() in imagefile:
                            displacement = material.createNode('redshift::TextureSampler', 'displacement')
                            displacement.parm('tex0').set(os.path.join(directory, imagefile))
                            displacementNode = material.createNode('redshift::Displacement')
                            displacementNode.setNamedInput('texMap', displacement, 'outColor')
                            #displacementNode.parm('black_level').set(0.5)
                            #displacement.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            materialoutnode.setNamedInput('Displacement', displacementNode, 'out')
                            node.parm('displacementdir' + str(iteration)).set(displacement.parm('tex0'))

                            