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

                #create redshift material
                RSmaterial = matnet.createNode('rs_usd_material_builder', "RS_"+name)
                RSmaterial.moveToGoodPosition()
                RSmaterialnode = RSmaterial.createNode('StandardMaterial')
                RSoutput = RSmaterial.path() + "/redshift_usd_material1"
                RSo = hou.node(RSoutput)
                RSo.setNamedInput('Surface', RSmaterialnode, 'outColor')
                collect.setInput(1, RSmaterial, 0)


                #create karma material
                INHERIT_PARM_EXPRESSION = '''n = hou.pwd()
                n_hasFlag = n.isMaterialFlagSet()
                i = n.evalParm('inherit_ctrl')
                r = 'none'
                if i == 1 or (n_hasFlag and i == 2):
                    r = 'inherit'
                return r'''

                KMAmaterial = matnet.createNode('subnet', "KMA_"+ name)
                KMAmaterial.moveToGoodPosition()

                parameters = KMAmaterial.parmTemplateGroup()

                newParm_hidingFolder = hou.FolderParmTemplate("mtlxBuilder","MaterialX Builder",folder_type=hou.folderType.Collapsible)
                control_parm_pt = hou.IntParmTemplate('inherit_ctrl','Inherit from Class', 
                                    num_components=1, default_value=(2,), 
                                    menu_items=(['0','1','2']),
                                    menu_labels=(['Never','Always','Material Flag']))


                newParam_tabMenu = hou.StringParmTemplate("tabmenumask", "Tab Menu Mask", 1, default_value=["MaterialX parameter constant collect null genericshader subnet subnetconnector suboutput subinput"])
                class_path_pt = hou.properties.parmTemplate('vopui', 'shader_referencetype')
                class_path_pt.setLabel('Class Arc')
                class_path_pt.setDefaultExpressionLanguage((hou.scriptLanguage.Python,))
                class_path_pt.setDefaultExpression((INHERIT_PARM_EXPRESSION,))   

                ref_type_pt = hou.properties.parmTemplate('vopui', 'shader_baseprimpath')
                ref_type_pt.setDefaultValue(['/__class_mtl__/`$OS`'])
                ref_type_pt.setLabel('Class Prim Path')               

                newParm_hidingFolder.addParmTemplate(newParam_tabMenu)
                newParm_hidingFolder.addParmTemplate(control_parm_pt)  
                newParm_hidingFolder.addParmTemplate(class_path_pt)    
                newParm_hidingFolder.addParmTemplate(ref_type_pt)             

                parameters.append(newParm_hidingFolder)
                KMAmaterial.setParmTemplateGroup(parameters)
                children = KMAmaterial.allSubChildren()

                for c in children:
                    c.destroy()
                    
                    
                subnet_output_surface = KMAmaterial.createNode("subnetconnector","surface_output")
                subnet_output_surface.parm("connectorkind").set("output")
                subnet_output_surface.parm("parmname").set("surface")
                subnet_output_surface.parm("parmlabel").set("Surface")
                subnet_output_surface.parm("parmtype").set("surface")

                subnet_output_disp = KMAmaterial.createNode("subnetconnector","displacement_output")
                subnet_output_disp.parm("connectorkind").set("output")
                subnet_output_disp.parm("parmname").set("displacement")
                subnet_output_disp.parm("parmlabel").set("Displacement")
                subnet_output_disp.parm("parmtype").set("displacement")        


                KMAmaterialnode = KMAmaterial.createNode("mtlxstandard_surface")
                KMAo = subnet_output_surface.setNamedInput("suboutput", KMAmaterialnode, "out")
                collect.setInput(2, KMAmaterial, 0)




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







        
                #build RS shader

                imageset.sort(key=lambda x: not x.lower().endswith('.exr'))
                for imagefile in imageset:
                    if imagefile.lower().endswith(('.exr', '.png', '.tga', '.tif', '.jpg')):
                        #print(imagefile)
                        if name + node.parm('ch_baseColor').eval() in imagefile:
                            baseColor = RSmaterial.createNode('redshift::TextureSampler', 'color')
                            baseColor.parm('tex0').set(os.path.join(directory, imagefile))
                            RSmaterialnode.setNamedInput('base_color', baseColor, 'outColor')
                            #node.parm('basecolordir' + str(iteration)).set(baseColor.parm('tex0'))

                        if name + node.parm('ch_emissive').eval() in imagefile:
                            emissive = RSmaterial.createNode('redshift::TextureSampler', 'emissive')
                            emissive.parm('tex0').set(os.path.join(directory, imagefile))
                            RSmaterialnode.setNamedInput('emission_color', emissive, 'outColor')
                            #node.parm('emissivedir' + str(iteration)).set(emissive.parm('tex0'))
                        
                        if name + node.parm('ch_metallic').eval() in imagefile:
                            metalness = RSmaterial.createNode('redshift::TextureSampler', 'metallic')
                            metalness.parm('tex0').set(os.path.join(directory, imagefile))
                            #metalness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            RSmaterialnode.setNamedInput('metalness', metalness, 'outColor')
                            #node.parm('metalnessdir' + str(iteration)).set(metalness.parm('tex0'))
                            

                        if name + node.parm('ch_normal').eval() in imagefile:
                            normal = RSmaterial.createNode('redshift::TextureSampler', 'normal')
                            normal.parm('tex0').set(os.path.join(directory, imagefile))
                            normal_map = RSmaterial.createNode("redshift::BumpMap", "normal_map")
                            normal_map.setNamedInput('input', normal, 'outColor')
                            normal_map.parm('inputType').set('1')
                            #normal.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            RSmaterialnode.setNamedInput('bump_input', normal_map, 'out')
                            #node.parm('normaldir' + str(iteration)).set(normal.parm('tex0'))
                            
                        if name + node.parm('ch_roughness').eval() in imagefile:
                            roughness = RSmaterial.createNode('redshift::TextureSampler', 'roughness')
                            roughness.parm('tex0').set(os.path.join(directory, imagefile))
                            #roughness.parm('gamma').set(1)
                            RSmaterialnode.setNamedInput('refl_roughness', roughness, 'outColor')
                            #node.parm('roughnessdir' + str(iteration)).set(roughness.parm('tex0'))

                        if name + node.parm('ch_displacement').eval() in imagefile:
                            displacement = RSmaterial.createNode('redshift::TextureSampler', 'displacement')
                            displacement.parm('tex0').set(os.path.join(directory, imagefile))
                            displacementNode = RSmaterial.createNode('redshift::Displacement')
                            displacementNode.setNamedInput('texMap', displacement, 'outColor')
                            #displacementNode.parm('black_level').set(0.5)
                            #displacement.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            RSo.setNamedInput('Displacement', displacementNode, 'out')
                            #node.parm('displacementdir' + str(iteration)).set(displacement.parm('tex0'))






                #build KARMA shader

                imageset.sort(key=lambda x: not x.lower().endswith('.exr'))
                for imagefile in imageset:
                    if imagefile.lower().endswith(('.exr', '.png', '.tga', '.tif', '.jpg')):
                        #print(imagefile)
                        if name + node.parm('ch_baseColor').eval() in imagefile:
                            baseColor = KMAmaterial.createNode('mtlximage', 'color')
                            baseColor.parm('file').set(os.path.join(directory, imagefile))
                            KMAmaterialnode.setNamedInput('base_color', baseColor, 'out')
                            #node.parm('basecolordir' + str(iteration)).set(baseColor.parm('file'))

                        if name + node.parm('ch_emissive').eval() in imagefile:
                            emissive = KMAmaterial.createNode('mtlximage', 'emissive')
                            emissive.parm('file').set(os.path.join(directory, imagefile))
                            KMAmaterialnode.setNamedInput('emission_color', emissive, 'out')
                            node.parm('emissivedir' + str(iteration)).set(emissive.parm('file'))
                        
                        if name + node.parm('ch_metallic').eval() in imagefile:
                            metalness = KMAmaterial.createNode('mtlximage', 'metallic')
                            metalness.parm('file').set(os.path.join(directory, imagefile))
                            metalness.parm('signature').set("default")
                            #metalness.parm('colorSpace').set("NAMED_COLOR_SPACE_OTHER")
                            KMAmaterialnode.setNamedInput('metalness', metalness, 'out')
                            #node.parm('metalnessdir' + str(iteration)).set(metalness.parm('file'))
                            

                        if name + node.parm('ch_normal').eval() in imagefile:
                            normal = KMAmaterial.createNode('mtlximage', 'normal')
                            normal.parm('file').set(os.path.join(directory, imagefile))
                            normal.parm('signature').set("vector3")
                            normal_map = KMAmaterial.createNode("mtlxnormalmap", "normal_map")
                            normal_map.setNamedInput('in', normal, 'out')
                            KMAmaterialnode.setNamedInput('normal', normal_map, 'out')
                            #node.parm('normaldir' + str(iteration)).set(normal.parm('file'))
                            
                        if name + node.parm('ch_roughness').eval() in imagefile:
                            roughness = KMAmaterial.createNode('mtlximage', 'roughness')
                            roughness.parm('file').set(os.path.join(directory, imagefile))
                            roughness.parm('signature').set("default")
                            KMAmaterialnode.setNamedInput('specular_roughness', roughness, 'out')
                            #node.parm('roughnessdir' + str(iteration)).set(roughness.parm('file'))

                        if name + node.parm('ch_displacement').eval() in imagefile:
                            displacement = KMAmaterial.createNode('mtlximage', 'displacement')
                            displacement.parm('file').set(os.path.join(directory, imagefile))
                            displacement.parm('signature').set("default")
                            displacementNode = KMAmaterial.createNode('mtlxdisplacement')
                            displacementNode.setNamedInput('displacement', displacement, 'out')
                            subnet_output_disp.setNamedInput('suboutput', displacementNode, 'out')
                            #node.parm('displacementdir' + str(iteration)).set(displacement.parm('file'))