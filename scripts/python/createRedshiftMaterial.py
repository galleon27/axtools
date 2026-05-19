import hou
import os
import re
import json

class RedshiftMaterialBuilder:
    def __init__(self):
        # 1. Global / State Variables
        self.node = hou.pwd()
        self.mat_node = hou.node(f"{self.node.path()}/material1")
        
        try:
            self.directory = self.node.parm('directory').unexpandedString()
        except hou.OperationFailed:
            self.directory = self.node.parm('directory').evalAsString()
            
        self.directory_path = self.node.parm('directory').eval()
        self.iteration = 0
        
        # 2. Setup the Material Network subnet
        self.matnet = self._get_or_create_matnet()
        self.preview_keywords = ["Preview", "preview"]

        # 3. Cache directory files and set configuration
        self.cached_files = self._cache_directory_files()
        self._setup_config()

    def _setup_config(self):
        """Loads the texture parameters and maps them to Redshift properties."""
        basecolor_suffix = self.node.parm('basecolor_suffix').eval().split()
        ambientocclusion_suffix = self.node.parm('ambientocclusion_suffix').eval().split()
        specular_suffix = self.node.parm('specular_suffix').eval().split()
        roughness_suffix = self.node.parm('roughness_suffix').eval().split()
        metallic_suffix = self.node.parm('metallic_suffix').eval().split()
        opacity_suffix = self.node.parm('opacity_suffix').eval().split()
        normal_suffix = self.node.parm('normal_suffix').eval().split()
        displacement_suffix = self.node.parm('displacement_suffix').eval().split()
        emission_suffix = self.node.parm('emission_suffix').eval().split()

        self.basecolor_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Auto"} for item in basecolor_suffix }
        self.ao_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Auto"} for item in ambientocclusion_suffix }
        self.specular_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Raw"} for item in specular_suffix }
        self.roughness_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Raw"} for item in roughness_suffix }
        self.metallic_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Raw"} for item in metallic_suffix }
        self.opacity_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Auto"} for item in opacity_suffix }
        self.normal_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Raw"} for item in normal_suffix }
        self.displacement_dict = { item: {"type": "redshift::TextureSampler", "color_space": "Raw"} for item in displacement_suffix }
        self.emission_dict = { item: {"type": "redshift::TextureSampler", "color_space": "NAMED_COLOR_SPACE_SRGB"} for item in emission_suffix }

    def _cache_directory_files(self):
        """Pre-scans the directory to avoid repeated OS calls."""
        files = []
        if os.path.exists(self.directory_path):
            for filename in os.listdir(self.directory_path):
                if not any(p in filename for p in self.preview_keywords):
                    if filename.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                        files.append(filename)
        return files

    def _get_megascans_displacement_scale(self):
        """Scans for a JSON file and attempts to extract Megascans height scale."""
        if not os.path.exists(self.directory_path):
            return None
            
        for filename in os.listdir(self.directory_path):
            if filename.lower().endswith('.json'):
                filepath = os.path.join(self.directory_path, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        
                        def find_height(obj):
                            if isinstance(obj, dict):
                                if obj.get("key") == "height" and "value" in obj:
                                    return obj.get("value")
                                for k, v in obj.items():
                                    result = find_height(v)
                                    if result is not None: 
                                        return result
                            elif isinstance(obj, list):
                                for item in obj:
                                    result = find_height(item)
                                    if result is not None: 
                                        return result
                            return None
                            
                        val_str = find_height(data)
                        if val_str:
                            match = re.search(r"([0-9]*\.?[0-9]+)", str(val_str))
                            if match:
                                return float(match.group(1))
                except Exception:
                    pass
        return None

    def _get_or_create_matnet(self):
        """Creates or retrieves the AX_MATNET subnet."""
        matnet = hou.node(f"{self.node.path()}/AX_MATNET")
        if matnet is None:
            matnet = self.node.createNode('matnet', 'AX_MATNET')
        return matnet

    def get_material_names(self):
        """Scans the directory and returns a list of unique material base names."""
        temp = set()
        for filename in self.cached_files:
            name_part = os.path.splitext(filename)[0]
            name_part = re.sub(r'[^a-zA-Z0-9]+', '_', name_part).rstrip('_')
            name = "_".join(name_part.split('_')[:-1])
            temp.add(name)
        return list(temp)

    def set_groups(self, total_materials, name):
        """Sets the group and material path parameters on the HDA and internal nodes."""
        if not self.mat_node:
            return

        self.mat_node.parm('num_materials').set(total_materials)
        self.mat_node.parm(f'shop_materialpath{self.iteration}').set(f'../AX_MATNET/{name}')
        
        self.node.parm('groupnum').set(total_materials)
        
        groupnum_parm = self.node.parm(f'groupnum{self.iteration}')
        if groupnum_parm:
            self.mat_node.parm(f'group{self.iteration}').set(groupnum_parm)
            
        self.mat_node.parm(f'group{self.iteration}').set(f'@shop_materialpath={name}')
        
        texsets_parm = self.node.parm('texSets')
        if texsets_parm:
            texsets_parm.set(total_materials)

    def get_or_create_material(self, name):
        """Retrieves existing Redshift VOPNET/components, or creates them if missing."""
        is_new = False
        material = self.matnet.node(name)
        
        if not material:
            is_new = True
            material = self.matnet.createNode('redshift_vopnet', name)
            
        material_node = None
        output_node = None
        
        for child in material.children():
            if child.type().name() == 'redshift::StandardMaterial':
                material_node = child
            elif child.type().name() == 'redshift_material':
                output_node = child
                
        if not material_node:
            material_node = material.createNode('redshift::StandardMaterial')
        if not output_node:
            output_node = material.createNode('redshift_material')
            
        # Ensure they are connected properly
        output_node.setNamedInput('Surface', material_node, 0)
        
        return material, material_node, output_node, is_new

    def _get_or_update_image_node(self, texture_set, material, name, texdir):
        """Internal helper to locate, create, or update a single image node."""
        target_file = None
        matched_channel = None

        for filename in self.cached_files:
            if name.lower() in filename.lower():
                for channel in texture_set.keys():
                    if channel.lower() in filename.lower():
                        target_file = filename
                        matched_channel = channel
                        break
            if target_file:
                break
        
        if not target_file:
            return None, None, False

        node_name = f"{name}_{matched_channel}"
        properties = texture_set[matched_channel]
        
        file_path = os.path.join(self.directory, target_file)
        eval_file_path = os.path.join(self.directory_path, target_file)
        
        existing_image = material.node(node_name)
        texdir_parm = self.node.parm(f"{texdir}{self.iteration}")
        
        made_change = False

        if existing_image:
            image = existing_image
            current_eval_path = existing_image.parm('tex0').evalAsString()
            ui_eval_path = texdir_parm.evalAsString() if texdir_parm else eval_file_path
            
            if current_eval_path != eval_file_path or ui_eval_path != eval_file_path:
                existing_image.parm('tex0').set(file_path)
                if texdir_parm:
                    texdir_parm.set(existing_image.parm('tex0'))
                made_change = True
        else:
            image = material.createNode(properties["type"])
            image.setName(node_name, unique_name=True)
            image.parm('tex0').set(file_path)
            
            if "color_space" in properties:
                image.parm('tex0_colorSpace').set(properties["color_space"])
                
            if texdir_parm:
                texdir_parm.set(image.parm('tex0'))
                
            made_change = True

        return image, properties, made_change

    def setup_albedo_ao(self, material, material_node, name):
        """Handles combining Albedo and AO maps using a Multiply node."""
        albedo_img, albedo_props, albedo_changed = self._get_or_update_image_node(self.basecolor_dict, material, name, 'basecolordir')
        ao_img, ao_props, ao_changed = self._get_or_update_image_node(self.ao_dict, material, name, 'aodir')
        
        made_change = albedo_changed or ao_changed
        
        if not albedo_img:
            return made_change
            
        if ao_img:
            mult_node_name = f"{name}_albedo_ao_mult"
            mult_node = material.node(mult_node_name)
            
            if not mult_node:
                mult_node = material.createNode('redshift::RSMathMulVector')
                mult_node.setName(mult_node_name, unique_name=True)
                mult_node.setNamedInput('input1', albedo_img, 0)
                mult_node.setNamedInput('input2', ao_img, 0)
                made_change = True
                
            # Connect the multiply node to the material base color
            material_node.setNamedInput('base_color', mult_node, 0)
        else:
            # Connect albedo directly if no AO exists
            material_node.setNamedInput('base_color', albedo_img, 0)
            
        return made_change

    def create_texture_node(self, texture_set, material, target_node, name, ch_input, texdir, secondary_node_type=None, secondary_input='texture', defaults=None):
        """Finds the texture file and wires standard nodes/secondary utilities."""
        image, properties, made_change = self._get_or_update_image_node(texture_set, material, name, texdir)
        
        if not image:
            return False

        if secondary_node_type:
            sec_node_name = f"{image.name()}_sec"
            secondary_node = material.node(sec_node_name)
            
            if not secondary_node:
                secondary_node = material.createNode(secondary_node_type)
                secondary_node.setName(sec_node_name, unique_name=True)
                secondary_node.setNamedInput(secondary_input, image, 0)
                target_node.setNamedInput(ch_input, secondary_node, 0)
                made_change = True
                
            if defaults:
                for param, value in defaults.items():
                    parm_obj = secondary_node.parm(param)
                    if parm_obj:
                        current_val = parm_obj.eval()
                        if isinstance(current_val, float) and isinstance(value, (int, float)):
                            if abs(current_val - value) > 0.00001:
                                parm_obj.set(value)
                                made_change = True
                        elif current_val != value:
                            parm_obj.set(value)
                            made_change = True
        else:
            target_node.setNamedInput(ch_input, image, 0)
            
        return made_change

    def build(self):
        """The main execution method that orchestrates the material creation and updates."""
        material_names = sorted(self.get_material_names())
        total_materials = len(material_names)

        if total_materials == 0:
            hou.ui.displayMessage("No valid textures found in the selected directory.", severity=hou.severityType.Warning, title="No Textures Found")
            return

        # Fetch JSON custom displacement 
        custom_disp_scale = self._get_megascans_displacement_scale()
        rs_disp_amount = custom_disp_scale if custom_disp_scale is not None else 0.01

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for name in material_names:
            self.iteration += 1
            
            material, material_node, output_node, is_new = self.get_or_create_material(name)
            self.set_groups(total_materials, name)

            changes = []
            
            # --- Albedo & AO ---
            changes.append(self.setup_albedo_ao(material, material_node, name))
            
            # --- Standard Nodes ---
            changes.append(self.create_texture_node(self.roughness_dict, material, material_node, name, 'refl_roughness', 'roughnessdir'))
            changes.append(self.create_texture_node(self.metallic_dict, material, material_node, name, 'metalness', 'metallicdir'))
            changes.append(self.create_texture_node(self.opacity_dict, material, material_node, name, 'opacity', 'opacitydir'))
            changes.append(self.create_texture_node(self.emission_dict, material, material_node, name, 'emission_color', 'emissivedir'))

            # Normal Map
            changes.append(self.create_texture_node(
                self.normal_dict, material, material_node, name, 'bump_input', 'normaldir', 
                secondary_node_type='redshift::BumpMap', 
                secondary_input='input',
                defaults={'inputType': '1'}
            ))

            # Displacement
            changes.append(self.create_texture_node(
                self.displacement_dict, material, output_node, name, 'Displacement', 'displacementdir', 
                secondary_node_type='redshift::Displacement', 
                secondary_input='texMap', 
                defaults={'scale': rs_disp_amount}
            ))

            made_changes = any(changes)

            if is_new:
                created_count += 1
                material.layoutChildren()
            elif made_changes:
                updated_count += 1
                material.layoutChildren()
            else:
                skipped_count += 1
            
        self.matnet.layoutChildren() 

        msg_lines = [f"Processed {total_materials} Redshift material(s)."]
        if custom_disp_scale is not None:
            msg_lines.append(f"- JSON Custom Scale Applied: {custom_disp_scale}")
        
        if created_count > 0: msg_lines.append(f"- Created: {created_count}")
        if updated_count > 0: msg_lines.append(f"- Updated: {updated_count}")
        if skipped_count > 0: msg_lines.append(f"- Skipped (unchanged): {skipped_count}")
            
        hou.ui.displayMessage("\n".join(msg_lines), title="Material Builder Completed")

# --- Execution ---
def execute():
    builder = RedshiftMaterialBuilder()
    builder.build()