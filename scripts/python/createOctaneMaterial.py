import hou
import os
import re
import json
import octane_material_builder

class OctaneMaterialBuilder:
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
        
        # Pre-compile regex for performance
        self.clean_name_re = re.compile(r'[^a-zA-Z0-9]+$')
        self.json_height_re = re.compile(r"([0-9]*\.?[0-9]+)")
        
        # 2. Setup the Material Network subnet
        self.matnet = self._get_or_create_matnet()
        self.preview_keywords = {"Preview", "preview"} # Set for faster lookup

        # 3. Setup Config and Map Files
        self.channel_configs = self._setup_config()
        self.cached_files = self._cache_directory_files()
        self.material_inventory = self._scan_and_map_textures()

    def _setup_config(self):
        """Centralizes channel configurations, mapping suffixes to Octane properties."""
        return {
            'basecolor': {
                'suffixes': self.node.parm('basecolor_suffix').eval().split(),
                'type': 'NT_TEX_IMAGE', 'color_space': 'NAMED_COLOR_SPACE_SRGB', 'dir_parm': 'basecolordir'
            },
            'ao': {
                'suffixes': self.node.parm('ambientocclusion_suffix').eval().split(),
                'type': 'NT_TEX_IMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'aodir'
            },
            'specular': {
                'suffixes': self.node.parm('specular_suffix').eval().split(),
                'type': 'NT_TEX_FLOATIMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'speculardir'
            },
            'roughness': {
                'suffixes': self.node.parm('roughness_suffix').eval().split(),
                'type': 'NT_TEX_FLOATIMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'roughnessdir'
            },
            'metallic': {
                'suffixes': self.node.parm('metallic_suffix').eval().split(),
                'type': 'NT_TEX_FLOATIMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'metallicdir'
            },
            'opacity': {
                'suffixes': self.node.parm('opacity_suffix').eval().split(),
                'type': 'NT_TEX_FLOATIMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'opacitydir'
            },
            'normal': {
                'suffixes': self.node.parm('normal_suffix').eval().split(),
                'type': 'NT_TEX_IMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'normaldir'
            },
            'displacement': {
                'suffixes': self.node.parm('displacement_suffix').eval().split(),
                'type': 'NT_TEX_FLOATIMAGE', 'color_space': 'NAMED_COLOR_SPACE_OTHER', 'dir_parm': 'displacementdir'
            },
            'emission': {
                'suffixes': self.node.parm('emission_suffix').eval().split(),
                'type': 'NT_TEX_IMAGE', 'color_space': 'NAMED_COLOR_SPACE_SRGB', 'dir_parm': 'emissivedir'
            }
        }

    def _cache_directory_files(self):
        """Pre-scans the directory to avoid repeated OS calls."""
        files = []
        if os.path.exists(self.directory_path):
            valid_exts = ('.png', '.jpg', '.tga', '.tif', '.exr')
            for filename in os.listdir(self.directory_path):
                # Faster exclusion check using sets/generators
                if not any(p in filename for p in self.preview_keywords):
                    if filename.lower().endswith(valid_exts):
                        files.append(filename)
        return files

    def _scan_and_map_textures(self):
        """Builds a master dictionary mapping base names to their respective texture files. O(N) complexity."""
        inventory = {}
        
        # Flatten and sort all suffixes by length descending to match longest suffix first
        suffix_to_channel = {}
        for chan_name, config in self.channel_configs.items():
            for suffix in config['suffixes']:
                suffix_to_channel[suffix.lower()] = chan_name
                
        sorted_suffixes = sorted(suffix_to_channel.keys(), key=len, reverse=True)

        for filename in self.cached_files:
            name_part = os.path.splitext(filename)[0]
            name_lower = name_part.lower()
            
            for suffix in sorted_suffixes:
                idx = name_lower.rfind(suffix)
                if idx != -1:
                    base_name = name_part[:idx]
                    base_name = self.clean_name_re.sub('', base_name)
                    
                    if base_name:
                        if base_name not in inventory:
                            inventory[base_name] = {}
                        channel_type = suffix_to_channel[suffix]
                        inventory[base_name][channel_type] = filename
                    break 
                        
        return inventory

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
                                    if result is not None: return result
                            elif isinstance(obj, list):
                                for item in obj:
                                    result = find_height(item)
                                    if result is not None: return result
                            return None
                            
                        val_str = find_height(data)
                        if val_str:
                            match = self.json_height_re.search(str(val_str))
                            if match:
                                return float(match.group(1))
                except Exception:
                    continue # Continue to next json file if parsing fails
        return None

    def _get_or_create_matnet(self):
        """Creates or retrieves the AX_MATNET subnet."""
        matnet = hou.node(f"{self.node.path()}/AX_MATNET")
        if matnet is None:
            matnet = self.node.createNode('matnet', 'AX_MATNET')
        return matnet

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
        """Retrieves existing Octane Standard Surface material nodes, or creates them via the builder if missing."""
        is_new = False
        material = self.matnet.node(name)
        
        if not material:
            is_new = True
            material = octane_material_builder.createMaskedOctaneSubnet(target_node=self.matnet, name=name)
            
        material_node = None
        output_node = material.node('surface_output')
        
        if output_node and len(output_node.inputs()) > 0:
            material_node = output_node.inputs()[0]
            
        if not material_node:
            for child in material.children():
                if 'STANDARD_SURFACE' in child.type().name().upper():
                    material_node = child
                    if output_node: output_node.setInput(0, material_node)
                    break
                    
        if not material_node:
            material_node = material.createNode('NT_MAT_STANDARD_SURFACE')
            if output_node: output_node.setInput(0, material_node)
        
        return material, material_node, is_new

    def _get_or_update_image_node(self, channel_key, material, name):
        """Internal helper to locate, create, or update a single image node using pre-mapped data."""
        target_file = self.material_inventory.get(name, {}).get(channel_key)
        if not target_file:
            return None, None, False

        properties = self.channel_configs[channel_key]
        node_name = f"{name}_{channel_key}"
        
        file_path = os.path.join(self.directory, target_file)
        eval_file_path = os.path.join(self.directory_path, target_file)
        
        existing_image = material.node(node_name)
        texdir_parm = self.node.parm(f"{properties['dir_parm']}{self.iteration}")
        
        made_change = False

        if existing_image:
            image = existing_image
            current_eval_path = existing_image.parm('A_FILENAME').evalAsString()
            ui_eval_path = texdir_parm.evalAsString() if texdir_parm else eval_file_path
            
            if current_eval_path != eval_file_path or ui_eval_path != eval_file_path:
                existing_image.parm('A_FILENAME').set(file_path)
                if texdir_parm:
                    texdir_parm.set(existing_image.parm('A_FILENAME'))
                made_change = True
        else:
            image = material.createNode(properties["type"])
            image.setName(node_name, unique_name=True)
            image.parm('A_FILENAME').set(file_path)
            
            if "color_space" in properties:
                image.parm('colorSpace').set(properties["color_space"])
                
            if texdir_parm:
                texdir_parm.set(image.parm('A_FILENAME'))
                
            made_change = True

        return image, properties, made_change

    def setup_albedo_ao(self, material, material_node, name):
        """Handles combining Base Color and AO maps using a Multiply node."""
        albedo_img, _, albedo_changed = self._get_or_update_image_node('basecolor', material, name)
        ao_img, _, ao_changed = self._get_or_update_image_node('ao', material, name)
        
        made_change = albedo_changed or ao_changed
        
        if not albedo_img:
            return made_change
            
        if ao_img:
            mult_node_name = f"{name}_albedo_ao_mult"
            mult_node = material.node(mult_node_name)
            
            if not mult_node:
                mult_node = material.createNode('NT_TEX_MULTIPLY')
                mult_node.setName(mult_node_name, unique_name=True)
                mult_node.setNamedInput('texture1', albedo_img, 0)
                mult_node.setNamedInput('texture2', ao_img, 0)
                made_change = True
                
            material_node.setNamedInput('baseColor', mult_node, 0)
        else:
            material_node.setNamedInput('baseColor', albedo_img, 0)
            
        return made_change

    def create_texture_node(self, channel_key, material, target_node, name, ch_input, secondary_node_type=None, secondary_input='texture', defaults=None):
        """Wires standard nodes and optional secondary utilities using pre-mapped data."""
        image, _, made_change = self._get_or_update_image_node(channel_key, material, name)
        
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
        material_names = sorted(self.material_inventory.keys())
        total_materials = len(material_names)

        if total_materials == 0:
            hou.ui.displayMessage("No valid textures found in the selected directory.", severity=hou.severityType.Warning, title="No Textures Found")
            return

        custom_disp_scale = self._get_megascans_displacement_scale()
        oct_disp_amount = custom_disp_scale if custom_disp_scale is not None else 0.01

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for name in material_names:
            self.iteration += 1
            
            material, material_node, is_new = self.get_or_create_material(name)
            self.set_groups(total_materials, name)

            changes = [
                self.setup_albedo_ao(material, material_node, name),
                self.create_texture_node('roughness', material, material_node, name, 'roughness'),
                self.create_texture_node('specular', material, material_node, name, 'specular'),
                self.create_texture_node('metallic', material, material_node, name, 'metallic'),
                self.create_texture_node('normal', material, material_node, name, 'normal'),
                self.create_texture_node('opacity', material, material_node, name, 'opacity'),
                self.create_texture_node('displacement', material, material_node, name, 'displacement', 
                                         secondary_node_type='NT_VERTEX_DISPLACEMENT', defaults={'black_level': 0.5, 'amount': oct_disp_amount}),
                self.create_texture_node('emission', material, material_node, name, 'emission', 
                                         secondary_node_type='NT_EMIS_TEXTURE', secondary_input='efficiency_or_texture')
            ]

            if is_new:
                created_count += 1
                material.layoutChildren()
            elif any(changes):
                updated_count += 1
                material.layoutChildren()
            else:
                skipped_count += 1
            
        self.matnet.layoutChildren() 

        msg_lines = [f"Processed {total_materials} Octane material(s)."]
        if custom_disp_scale is not None: msg_lines.append(f"- JSON Custom Scale Applied: {custom_disp_scale}")
        if created_count > 0: msg_lines.append(f"- Created: {created_count}")
        if updated_count > 0: msg_lines.append(f"- Updated: {updated_count}")
        if skipped_count > 0: msg_lines.append(f"- Skipped (unchanged): {skipped_count}")
            
        hou.ui.displayMessage("\n".join(msg_lines), title="Material Builder Completed")

def execute():
    builder = OctaneMaterialBuilder()
    builder.build()