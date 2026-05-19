import hou
import os
import re

class RedshiftMaterialBuilder:
    def __init__(self):
        # 1. Global / State Variables
        self.node = hou.pwd()
        self.mat_node = hou.node(f"{self.node.path()}/material1")
        self.directory = self.node.parm('directory').unexpandedString()
        self.directory_path = self.node.parm('directory').eval()
        self.iteration = 0
        
        # 2. Setup the Material Network subnet
        self.matnet = self._get_or_create_matnet()
        # define suffix
        basecolor_suffix = self.node.parm('basecolor_suffix').eval().split()
        ambientocclusion_suffix = self.node.parm('ambientocclusion_suffix').eval().split()
        specular_suffix = self.node.parm('specular_suffix').eval().split()
        roughness_suffix = self.node.parm('roughness_suffix').eval().split()
        metallic_suffix = self.node.parm('metallic_suffix').eval().split()
        opacity_suffix = self.node.parm('opacity_suffix').eval().split()
        normal_suffix = self.node.parm('normal_suffix').eval().split()
        displacement_suffix = self.node.parm('displacement_suffix').eval().split()
        emission_suffix = self.node.parm('emission_suffix').eval().split()

        # 3. Define Texture Dictionaries
        self.basecolor_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Auto"} 
            for item in basecolor_suffix
        }
        self.ao_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Auto"} 
            for item in ambientocclusion_suffix
        }
        self.specular_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Raw"} 
            for item in specular_suffix
        }
        self.roughness_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Raw"} 
            for item in roughness_suffix
        }
        self.metallic_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Raw"} 
            for item in metallic_suffix
        }
        self.opacity_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Auto"} 
            for item in opacity_suffix
        }
        self.normal_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Raw"} 
            for item in normal_suffix
        }
        self.displacement_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "Raw"} 
            for item in displacement_suffix
        
        }
        self.emission_dict = {
            item: {"type": "redshift::TextureSampler", "color_space": "NAMED_COLOR_SPACE_SRGB"} 
            for item in emission_suffix
        }

        self.preview_keywords = ["Preview", "preview"]

    def _get_or_create_matnet(self):
        """Creates or retrieves the AX_MATNET subnet."""
        matnet = hou.node(f"{self.node.path()}/AX_MATNET")
        if matnet is None:
            matnet = self.node.createNode('matnet', 'AX_MATNET')
        return matnet

    def get_material_names(self):
        """Scans the directory and returns a list of unique material base names."""
        temp = []
        if not os.path.exists(self.directory_path):
            return temp

        for filename in os.listdir(self.directory_path):
            if not any(p in filename for p in self.preview_keywords):
                if filename.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                    # Remove extension
                    name_part = os.path.splitext(filename)[0]
                    # Replace non-alphanumeric separators with underscores
                    name_part = re.sub(r'[^a-zA-Z0-9]+', '_', name_part).rstrip('_')
                    # Remove channel keyword from end
                    name = "_".join(name_part.split('_')[:-1])
                    temp.append(name)
                    
        return list(set(temp))

    def set_groups(self, total_materials, name):
        """Sets the group and material path parameters on the HDA and internal nodes."""
        if not self.mat_node:
            return

        self.mat_node.parm('num_materials').set(total_materials)
        self.mat_node.parm(f'shop_materialpath{self.iteration}').set(f'../AX_MATNET/{name}')
        
        self.node.parm('groupnum').set(total_materials)
        
        # Note: In your original script, the group parameter is set twice. 
        # I preserved your original logic here.
        groupnum_parm = self.node.parm(f'groupnum{self.iteration}')
        if groupnum_parm:
            self.mat_node.parm(f'group{self.iteration}').set(groupnum_parm)
            
        self.mat_node.parm(f'group{self.iteration}').set(f'@shop_materialpath={name}')
        
        texsets_parm = self.node.parm('texSets')
        if texsets_parm:
            texsets_parm.set(total_materials)

    def create_material_network(self, name):
        """Creates the Octane VOPNET and the base Universal Material nodes."""
        material = self.matnet.createNode('redshift_vopnet', name)
        
        # Delete existing default nodes
        material.deleteItems(material.children())
        
        # Recreate Octane Material network
        material_node = material.createNode('redshift::StandardMaterial')
        output_node = material.createNode('redshift_material')
        output_node.setNamedInput('Surface', material_node, 0)
        
        return material, material_node

    def create_texture_node(self, texture_set, material, material_node, name, ch_input, texdir, secondary_node_type=None, secondary_input='texture', defaults=None):
        """Finds the correct texture file and wires it into the material node."""
        file_dict = {}
        for filename in os.listdir(self.directory_path):
            base, ext = os.path.splitext(filename)
            if ext.lower() in ['.png', '.jpg', '.tga', '.tif', '.exr']:
                base = re.sub(r'[^a-zA-Z0-9]+', '_', base).rstrip('_')
                if base not in file_dict or ext.lower() == '.exr':
                    file_dict[base] = filename

        for base, filename in file_dict.items():
            if material.name() in filename:
                for channel, properties in texture_set.items():
                    if channel.lower() in filename.lower():
                        # Create primary image node, set name, filename, and color space
                        image = material.createNode(properties["type"])
                        image.setName(name, unique_name=True)
                        image.parm('tex0').set(os.path.join(self.directory, filename))
                        image.parm('tex0_colorSpace').set(properties["color_space"])

                        final_node = image
                        final_type = 'outColor'

                        # Handle secondary node (e.g., displacement or emission)
                        if secondary_node_type:
                            secondary_node = material.createNode(secondary_node_type)
                            secondary_node.setNamedInput(secondary_input, image, 'outColor')

                            if defaults:
                                for param, value in defaults.items():
                                    secondary_node.parm(param).set(value)

                            final_node = secondary_node
                            final_type = secondary_node_type
                        
                        # Only wire directly if ch_input is provided
                        if ch_input:
                            material_node.setNamedInput(ch_input, final_node, final_type)

                        # Update UI parameter on the main node
                        if texdir:
                            texdir_parm = self.node.parm(f"{texdir}{self.iteration}")
                            if texdir_parm:
                                texdir_parm.set(image.parm('tex0'))
                                
                        return final_node, final_type # Return nodes for advanced wiring

        return None, None # Return empty if no match found

    def build(self):
        """The main execution method that orchestrates the material creation."""
        material_names = self.get_material_names()
        total_materials = len(material_names)

        for name in material_names:
            self.iteration += 1
            material, material_node = self.create_material_network(name)
            self.set_groups(total_materials, name)

            # --- Albedo and AO Logic ---
            # Fetch nodes without wiring them directly to the material node yet (ch_input=None)
            albedo_node, albedo_type = self.create_texture_node(self.basecolor_dict, material, material_node, 'basecolor', None, 'basecolordir')
            ao_node, ao_type = self.create_texture_node(self.ao_dict, material, material_node, 'ao', None, 'aodir')

            if albedo_node and ao_node:
                # If both exist, create a multiply node
                mult_node = material.createNode('redshift::StandardMaterial', 'albedo_ao_mult')
                mult_node.setNamedInput('input1', albedo_node, 'outColor')
                mult_node.setNamedInput('input2', ao_node, 'outColor')
                
                # Plug the multiply node into the material's albedo channel
                material_node.setNamedInput('base_color', mult_node, 'out')
            elif albedo_node:
                # If only albedo exists, plug it directly
                material_node.setNamedInput('base_color', albedo_node, 'outColor')

            # --- Wire remaining texture channels ---
            self.create_texture_node(self.roughness_dict, material, material_node, 'roughness', 'refl_roughness', 'roughnessdir')
            # self.create_texture_node(self.specular_dict, material, material_node, 'specular', 'specular', 'speculardir')
            self.create_texture_node(self.metallic_dict, material, material_node, 'metallic', 'metalness', 'metallicdir')
            # self.create_texture_node(self.normal_dict, material, material_node, 'normal', 'normal', 'normaldir')
            self.create_texture_node(self.opacity_dict, material, material_node, 'opacity', 'opacity', 'opacitydir')
            self.create_texture_node(self.opacity_dict, material, material_node, 'emissive', 'emissive_color', 'emissivedir')

            self.create_texture_node(
                self.normal_dict, material, material_node, 'normal', 'bump_input', 'normaldir', 
                secondary_node_type='redshift::BumpMap', 
                secondary_input='input' 
                # defaults={'inputType': '1', 'amount': 0.01}
            )

            # self.create_texture_node(
            #     self.displacement_dict, material, material_node, 'displacement', 'Displacement', 'displacementdir', 
            #     secondary_node_type='redshift::Displacement', 
            #     secondary_input='texMap', 
            #     # defaults={'scale': 0.01}
            # )

            # Rearrange nodes inside the material
            material.layoutChildren() 

        # Rearrange materials inside the matnet
        self.matnet.layoutChildren() 


# --- Execution ---
# To run this script (e.g., inside a button callback or Python module script), 
# you just need to initialize the class and call `.build()`.

def execute():
    builder = RedshiftMaterialBuilder()
    builder.build()