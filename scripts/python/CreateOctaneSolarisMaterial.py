import hou
import os
import re

def createOctaneMaterial():
    #global variables
    node = hou.pwd()
    matNode = node.children()[0]
    directory = node.parm('directory').unexpandedString()
    directory_path = node.parm('directory').eval()
    matnet = None
    iteration = 0

    basecolor = ["albedo", "basecolor", "base_color", "diffuse"]
    basecolor_dict = {item: {"type": "NT_TEX_IMAGE", "color_space": "NAMED_COLOR_SPACE_SRGB"} for item in basecolor}

    roughness = ["roughness"]
    roughness_dict = {item: {"type": "NT_TEX_FLOATIMAGE", "color_space": "NAMED_COLOR_SPACE_OTHER"} for item in roughness}

    metallic = ["metallic", "metalness"]
    metallic_dict = {item: {"type": "NT_TEX_FLOATIMAGE", "color_space": "NAMED_COLOR_SPACE_OTHER"} for item in metallic}

    opacity = ["opacity"]
    opacity_dict = {item: {"type": "NT_TEX_FLOATIMAGE", "color_space": "NAMED_COLOR_SPACE_OTHER"} for item in opacity}

    normal = ["normal"]
    normal_dict = {item: {"type": "NT_TEX_IMAGE", "color_space": "NAMED_COLOR_SPACE_OTHER"} for item in normal}

    displacement = ["displacement", "height"]
    displacement_dict = {item: {"type": "NT_TEX_FLOATIMAGE", "color_space": "NAMED_COLOR_SPACE_OTHER"} for item in displacement}

    emission = ["emission", "emissive"]
    emission_dict = {item: {"type": "NT_TEX_IMAGE", "color_space": "NAMED_COLOR_SPACE_SRGB"} for item in emission}

    preview = ["Preview", "preview"]

    def setGroups(filename, name, collect):
        matNode.parm('materials').set(len(filename))
        matNode.parm('matnode' + str(iteration)).set(collect.name())
        matNode.parm('matpath' + str(iteration)).set(collect.name())
        # node.parm('groupnum').set(len(filename))
        # matNode.parm('group' + str(iteration)).set(node.parm('groupnum' + str(iteration)))
        # matNode.parm('group' + str(iteration)).set('@shop_materialpath=' + name)
        node.parm('texSets').set(len(filename))

    def materialName():
        temp = []
        for filename in os.listdir(directory_path):
            if not any(p in filename for p in preview):
                if filename.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                    # Remove extension
                    name_part = os.path.splitext(filename)[0]
                    
                    # Replace any non-alphanumeric separator with underscores
                    name_part = re.sub(r'[^a-zA-Z0-9]+', '_', name_part)
                    
                    # Remove trailing underscores
                    name_part = name_part.rstrip('_')
                    
                    # Remove channel keyword from end
                    name = "_".join(name_part.split('_')[:-1])
                    
                    temp.append(name)
        temp = list(set(temp))
        return temp


    #create materials  
    def createMaterial(name, matnet):
        # Create Octane Material Builder and delete all existing nodes
        material = matNode.createNode('octane_solaris_material_builder', "OR_" + name)
        # material.deleteItems(material.children())
        # Recreate Octane Material network
        collect = matNode.createNode('collect', name+"_collect")
        materialNode = material.createNode('NT_MAT_UNIVERSAL')
        outputNode = material.path() + "/octane_material"
        output = hou.node(outputNode)
        output.setNamedInput('material', materialNode, 'NT_MAT_UNIVERSAL')
        collect.setInput(0, material, 0)
        return material, materialNode, collect

    def createMaterialNode(textureSet, material, materialNode, name, ch_input, texdir, secondary_node_type=None, secondary_input='texture', defaults=None):
        # Build a dict to track best files by base name
        file_dict = {}
        for filename in os.listdir(directory_path):
            base, ext = os.path.splitext(filename)
            if ext.lower() in ['.png', '.jpg', '.tga', '.tif', '.exr']:
                base = re.sub(r'[^a-zA-Z0-9]+', '_', base).rstrip('_')
                if base not in file_dict or ext.lower() == '.exr':
                    file_dict[base] = filename

        for base, filename in file_dict.items():
            if material.name() in filename:
                for channel, properties in textureSet.items():
                    if channel.lower() in filename.lower():
                        # Create primary image node, set name, filename, and color space
                        image = material.createNode(properties["type"])
                        image.setName(name, unique_name=True)
                        image.parm('A_FILENAME').set(os.path.join(directory, filename))
                        image.parm('colorSpace').set(properties["color_space"])

                        # If a secondary node is needed (e.g., for displacement or emission)
                        if secondary_node_type:
                            secondary_node = material.createNode(secondary_node_type)
                            secondary_node.setNamedInput(secondary_input, image, properties["type"])

                            # Apply default parameters to the secondary node if provided
                            if defaults:
                                for param, value in defaults.items():
                                    secondary_node.parm(param).set(value)

                            # Set material node input to the secondary node
                            materialNode.setNamedInput(ch_input, secondary_node, secondary_node_type)
                        else:
                            # Set material node input directly to the image node
                            materialNode.setNamedInput(ch_input, image, properties["type"])

                        # Set the directory parameter with iteration
                        node.parm(f"{texdir}{iteration}").set(image.parm('A_FILENAME'))
                        break  # Stop searching channels once a match is found



    # print(matnet)
    for material_name in materialName():
        iteration = iteration + 1
        matnet = matNode
        material, materialNode, collect = createMaterial(material_name, matnet)
        setGroups(materialName(), material_name, collect)

        # Usage examples for different node types:
        createMaterialNode(
            basecolor_dict, material, materialNode, 'basecolor', 'albedo', 'basecolordir'
        )

        createMaterialNode(
            roughness_dict, material, materialNode, 'roughness', 'roughness', 'roughnessdir'
        )

        createMaterialNode(
            metallic_dict, material, materialNode, 'metallic', 'metallic', 'metallicdir'
        )

        createMaterialNode(
            normal_dict, material, materialNode, 'normal', 'normal', 'normaldir'
        )

        createMaterialNode(
            opacity_dict, material, materialNode, 'opacity', 'opacity', 'opacitydir'
        )

        createMaterialNode(
            displacement_dict, material, materialNode, 'displacement', 'displacement', 'displacementdir', 
            secondary_node_type='NT_VERTEX_DISPLACEMENT', 
            secondary_input='texture', defaults={'black_level': 0.5, 'amount': 0.01}
        )

        createMaterialNode(
            emission_dict, material, materialNode, 'emission_dict', 'emission', 'emissivedir',
            secondary_node_type='NT_EMIS_TEXTURE', 
            secondary_input='efficiency_or_texture'
)
        # material.layoutChildren() #rearrange texture nodes
        # matnet.layoutChildren() #rearange material nodes


