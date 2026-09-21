import hou
import os
import re
import json
import voptoolutils
import octane_material_builder

class MaterialBuilder:
    def __init__(self, node, renderer="octane"):
        self.node = node
        if not self.node: 
            return
            
        self.mat_node = self.node.children()[0] if self.node.children() else self.node
        
        try:
            self.directory = self.node.parm('directory').unexpandedString()
        except hou.OperationFailed:
            self.directory = self.node.parm('directory').evalAsString()
            
        self.directory_path = self.node.parm('directory').eval()
        self.iteration = 0
        self.renderer = renderer.lower() 
        
        # Using sets for O(1) lookups
        self.preview_keywords = {"preview"}
        self.valid_exts = {'.png', '.jpg', '.tga', '.tif', '.exr'}

        self._setup_config()
        self.cached_files, self.file_lower_map = self._cache_directory_files()

    def _cache_directory_files(self):
        """Pre-scans the directory to avoid repeated OS calls and builds a lowercase mapping."""
        files = []
        file_lower_map = {}
        if os.path.exists(self.directory_path):
            try:
                # os.scandir is faster than os.listdir
                for entry in os.scandir(self.directory_path):
                    if entry.is_file():
                        lower_name = entry.name.lower()
                        if not any(p in lower_name for p in self.preview_keywords):
                            if os.path.splitext(lower_name)[1] in self.valid_exts:
                                files.append(entry.name)
                                file_lower_map[lower_name] = entry.name
            except OSError:
                pass
        return files, file_lower_map

    def _get_megascans_displacement_scale(self):
        """Scans for a JSON file and attempts to extract Megascans height scale."""
        if not os.path.exists(self.directory_path):
            return None
            
        try:
            for entry in os.scandir(self.directory_path):
                if entry.is_file() and entry.name.lower().endswith('.json'):
                    try:
                        with open(entry.path, 'r') as f:
                            data = json.load(f)
                            
                            def find_height(obj):
                                if isinstance(obj, dict):
                                    if obj.get("key") == "height" and "value" in obj:
                                        return obj.get("value")
                                    for v in obj.values():
                                        result = find_height(v)
                                        if result is not None: return result
                                elif isinstance(obj, list):
                                    for item in obj:
                                        result = find_height(item)
                                        if result is not None: return result
                                return None
                                
                            val_str = find_height(data)
                            if val_str is not None:
                                match = re.search(r"([0-9]*\.?[0-9]+)", str(val_str))
                                if match:
                                    return float(match.group(1))
                    except Exception:
                        continue
        except OSError:
            pass
        return None

    def _setup_config(self):
        """Loads the correct node types, parameter names, and ports based on the renderer."""
        # Fetching parameters once
        parms = {
            'albedo': self.node.parm('albedo_suffix').eval().split(),
            'ao': self.node.parm('ambientocclusion_suffix').eval().split(),
            'specular': self.node.parm('specular_suffix').eval().split(),
            'roughness': self.node.parm('roughness_suffix').eval().split(),
            'metallic': self.node.parm('metallic_suffix').eval().split(),
            'opacity': self.node.parm('opacity_suffix').eval().split(),
            'normal': self.node.parm('normal_suffix').eval().split(),
            'displacement': self.node.parm('displacement_suffix').eval().split(),
            'emission': self.node.parm('emission_suffix').eval().split()
        }
    
        if self.renderer == "octane":
            self.file_parm = 'A_FILENAME'
            self.basecolor_dict = {k: {"type": "NT_TEX_IMAGE", "port": "baseColor", "color_space": "NAMED_COLOR_SPACE_SRGB"} for k in parms['albedo']}
            self.ao_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "none", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['ao']}
            self.specular_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "specular", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['specular']}
            self.roughness_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "roughness", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['roughness']}
            self.metallic_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "metallic", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['metallic']}
            self.opacity_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "opacity", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['opacity']}
            self.normal_dict = {k: {"type": "NT_TEX_IMAGE", "port": "normal", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['normal']}
            self.displacement_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "displacement", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in parms['displacement']}
            self.emission_dict = {k: {"type": "NT_TEX_IMAGE", "port": "emission", "color_space": "NAMED_COLOR_SPACE_SRGB"} for k in parms['emission']}
            
        elif self.renderer == "karma":
            self.file_parm = 'file'
            self.basecolor_dict = {k: {"type": "mtlximage", "port": "base_color", "signature": "color3"} for k in parms['albedo']}
            self.ao_dict = {k: {"type": "mtlximage", "port": "none", "signature": "float"} for k in parms['ao']}
            self.specular_dict = {k: {"type": "mtlximage", "port": "specular", "signature": "float"} for k in parms['specular']}
            self.roughness_dict = {k: {"type": "mtlximage", "port": "specular_roughness", "signature": "float"} for k in parms['roughness']}
            self.metallic_dict = {k: {"type": "mtlximage", "port": "metalness", "signature": "float"} for k in parms['metallic']}
            self.opacity_dict = {k: {"type": "mtlximage", "port": "transmission", "signature": "float"} for k in parms['opacity']}
            self.normal_dict = {k: {"type": "mtlximage", "port": "normal", "signature": "vector3"} for k in parms['normal']}
            self.displacement_dict = {k: {"type": "mtlximage", "port": "displacement", "signature": "float"} for k in parms['displacement']}
            self.emission_dict = {k: {"type": "mtlximage", "port": "emission", "signature": "color3"} for k in parms['emission']}

        elif self.renderer == "redshift":
            self.file_parm = 'tex0'
            self.basecolor_dict = {k: {"type": "redshift::TextureSampler", "port": "base_color", "color_space": "sRGB"} for k in parms['albedo']}
            self.ao_dict = {k: {"type": "redshift::TextureSampler", "port": "none", "color_space": "Raw"} for k in parms['ao']}
            self.specular_dict = {k: {"type": "redshift::TextureSampler", "port": "specular_weight", "color_space": "Raw"} for k in parms['specular']}
            self.roughness_dict = {k: {"type": "redshift::TextureSampler", "port": "specular_roughness", "color_space": "Raw"} for k in parms['roughness']}
            self.metallic_dict = {k: {"type": "redshift::TextureSampler", "port": "metalness", "color_space": "Raw"} for k in parms['metallic']}
            self.opacity_dict = {k: {"type": "redshift::TextureSampler", "port": "geometry_opacity", "color_space": "Raw"} for k in parms['opacity']}
            self.normal_dict = {k: {"type": "redshift::TextureSampler", "port": "BumpMap", "color_space": "Raw"} for k in parms['normal']}
            self.displacement_dict = {k: {"type": "redshift::TextureSampler", "port": "Displacement", "color_space": "Raw"} for k in parms['displacement']}
            self.emission_dict = {k: {"type": "redshift::TextureSampler", "port": "emission_luminance", "color_space": "sRGB"} for k in parms['emission']}

    def get_material_names(self):
        """Scans the directory and returns a list of unique material base names."""
        temp = set()
        
        all_suffixes = []
        for d in [self.basecolor_dict, self.ao_dict, self.specular_dict, 
                self.roughness_dict, self.metallic_dict, self.opacity_dict, 
                self.normal_dict, self.displacement_dict, self.emission_dict]:
            all_suffixes.extend(list(d.keys()))
            
        # Compile a single regex to match any of the suffixes
        escaped_suffixes = [re.escape(s.lower()) for s in all_suffixes]
        pattern = re.compile(r'(.+?)(' + '|'.join(escaped_suffixes) + r')', re.IGNORECASE)

        for filename in self.cached_files:
            name_part = os.path.splitext(filename)[0]
            match = pattern.search(name_part)
            
            if match:
                base_name = match.group(1)
                # Strip trailing non-alphanumerics
                base_name = re.sub(r'[^a-zA-Z0-9]+$', '', base_name)
                if base_name:
                    temp.add(base_name)
                    
        return list(temp)

    def setGroups(self, total_materials, target_node):
        """Sets the group and material path parameters on the HDA and internal nodes."""
        if not self.mat_node: return

        if self.mat_node.parm('materials'):
            self.mat_node.parm('materials').set(total_materials)
            
        if self.mat_node.parm(f'matnode{self.iteration}'):
            self.mat_node.parm(f'matnode{self.iteration}').set(target_node.name())
            
        if self.mat_node.parm(f'matpath{self.iteration}'):
            self.mat_node.parm(f'matpath{self.iteration}').set(target_node.name())
            
        if self.node.parm('texSets'):
            self.node.parm('texSets').set(total_materials)

    def get_or_create_material(self, name):
        """Retrieves existing Builder node/components, or creates them if missing."""
        is_new = False
        material = self.mat_node.node(name)
        
        if not material:
            is_new = True
            if self.renderer == "octane":
                material = octane_material_builder.createMaskedOctaneSubnet(target_node=self.mat_node, name=name)
            elif self.renderer == "karma":
                mask = voptoolutils.KARMAMTLX_TAB_MASK
                material = voptoolutils._setupMtlXBuilderSubnet(
                    destination_node=self.mat_node, 
                    name=name, mask=mask, folder_label='Karma Material Builder'
                )
            elif self.renderer == "redshift":
                material = self.mat_node.createNode('rs_usd_material_builder', name)

        materialNode = None
        output = None
        dispNode = None

        if self.renderer == "octane":
            output = material.node('surface_output')
            if output and len(output.inputs()) > 0:
                materialNode = output.inputs()[0]
                
            if not materialNode:
                for child in material.children():
                    if 'STANDARD_SURFACE' in child.type().name().upper():
                        materialNode = child
                        if output: output.setInput(0, materialNode)
                        break
                        
            if not materialNode:
                materialNode = material.createNode('NT_MAT_STANDARD_SURFACE')
                if output: output.setInput(0, materialNode)
                
        elif self.renderer == "karma":
            for child in material.children():
                ctype = child.type().name()
                if ctype == 'mtlxstandard_surface': materialNode = child
                elif ctype == 'suboutput': output = child
                elif ctype == 'mtlxdisplacement': dispNode = child
            
            if not materialNode: materialNode = material.createNode('mtlxstandard_surface')
            if not output: output = material.createNode('suboutput')
            if not dispNode: dispNode = material.createNode('mtlxdisplacement')
                
            output.setNamedInput('surface', materialNode, 0)
            output.setNamedInput('displacement', dispNode, 0)
            
        elif self.renderer == "redshift":
            for child in material.children():
                ctype = child.type().name()
                if ctype == 'redshift_usd_material': output = child
                elif 'OpenPBRMaterial' in ctype: materialNode = child
            
            if not output: output = material.createNode('redshift_usd_material')
            if not materialNode: materialNode = material.createNode('redshift::OpenPBRMaterial')
                
            output.setNamedInput('Surface', materialNode, 0)
                
        return material, materialNode, output, dispNode, is_new

    def _get_or_update_image_node(self, texture_set, material, name, texdir):
        """Internal helper to locate, create, or update a single image node."""
        target_file = None
        matched_channel = None
        name_lower = name.lower()

        # Optimized search using the pre-built lowercase map
        for lower_file, original_file in self.file_lower_map.items():
            if name_lower in lower_file:
                for channel in texture_set.keys():
                    if channel.lower() in lower_file:
                        target_file = original_file
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
            current_eval_path = existing_image.parm(self.file_parm).evalAsString()
            ui_eval_path = texdir_parm.evalAsString() if texdir_parm else eval_file_path
            
            if current_eval_path != eval_file_path or ui_eval_path != eval_file_path:
                existing_image.parm(self.file_parm).set(file_path)
                if texdir_parm: texdir_parm.set(existing_image.parm(self.file_parm))
                made_change = True
        else:
            image = material.createNode(properties["type"])
            image.setName(node_name, unique_name=True)
            image.parm(self.file_parm).set(file_path)
            
            if self.renderer == "octane" and "color_space" in properties:
                image.parm('colorSpace').set(properties["color_space"])
            elif self.renderer == "karma" and "signature" in properties:
                image.parm('signature').set(properties["signature"])
            elif self.renderer == "redshift" and "color_space" in properties:
                image.parm('tex0_colorSpace').set(properties["color_space"])
                
            if texdir_parm:
                texdir_parm.set(image.parm(self.file_parm))
                
            made_change = True

        return image, properties, made_change

    def setup_albedo_ao(self, material, materialNode, name):
        """Handles combining Albedo and AO maps using a Multiply node."""
        albedo_img, albedo_props, albedo_changed = self._get_or_update_image_node(self.basecolor_dict, material, name, 'basecolordir')
        ao_img, ao_props, ao_changed = self._get_or_update_image_node(self.ao_dict, material, name, 'aodir')
        
        made_change = albedo_changed or ao_changed
        
        if not albedo_img: return made_change
            
        target_port = albedo_props["port"]
        
        if ao_img:
            mult_node_name = f"{name}_albedo_ao_mult"
            mult_node = material.node(mult_node_name)
            
            if not mult_node:
                if self.renderer == "octane":
                    mult_node = material.createNode('NT_TEX_MULTIPLY')
                    mult_node.setName(mult_node_name, unique_name=True)
                    mult_node.setNamedInput('texture1', albedo_img, 0)
                    mult_node.setNamedInput('texture2', ao_img, 0)
                elif self.renderer == "karma":
                    mult_node = material.createNode('mtlxmultiply')
                    mult_node.setName(mult_node_name, unique_name=True)
                    mult_node.parm('signature').set('color3')
                    mult_node.setNamedInput('in1', albedo_img, 0)
                    mult_node.setNamedInput('in2', ao_img, 0)
                elif self.renderer == "redshift":
                    mult_node = material.createNode('redshift::RSMathMul')
                    mult_node.setName(mult_node_name, unique_name=True)
                    mult_node.setInput(0, albedo_img)
                    mult_node.setInput(1, ao_img)
                made_change = True
                
            materialNode.setNamedInput(target_port, mult_node, 0)
        else:
            materialNode.setNamedInput(target_port, albedo_img, 0)
            
        return made_change

    def create_texture_node(self, texture_set, material, target_node, name, texdir, secondary_node_type=None, secondary_input='in', defaults=None):
        """Finds the texture file and wires standard nodes/secondary utilities."""
        image, properties, made_change = self._get_or_update_image_node(texture_set, material, name, texdir)
        
        if not image: return False

        target_port = properties["port"]

        if secondary_node_type:
            sec_node_name = f"{image.name()}_sec"
            secondary_node = material.node(sec_node_name)
            
            if not secondary_node:
                secondary_node = material.createNode(secondary_node_type)
                secondary_node.setName(sec_node_name, unique_name=True)
                secondary_node.setNamedInput(secondary_input, image, 0)
                target_node.setNamedInput(target_port, secondary_node, 0)
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
            target_node.setNamedInput(target_port, image, 0)
            
        return made_change

    def build(self):
        """The main execution method that orchestrates the material creation and updates."""
        material_names = sorted(self.get_material_names())
        total_materials = len(material_names)

        if total_materials == 0:
            hou.ui.displayMessage("No valid textures found in the selected directory.", severity=hou.severityType.Warning, title="No Textures Found")
            return

        custom_disp_scale = self._get_megascans_displacement_scale()
        octane_disp_amount = custom_disp_scale if custom_disp_scale is not None else 0.01
        karma_disp_amount = custom_disp_scale if custom_disp_scale is not None else 0.1
        redshift_disp_amount = custom_disp_scale if custom_disp_scale is not None else 0.1

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for name in material_names:
            
            existing_material = self.mat_node.node(name)
            if existing_material:
                is_octane = existing_material.node('surface_output') is not None
                is_redshift = existing_material.type().name() == 'rs_usd_material_builder'
                is_karma = not is_octane and not is_redshift
                
                if (self.renderer == "octane" and not is_octane) or \
                   (self.renderer == "karma" and not is_karma) or \
                   (self.renderer == "redshift" and not is_redshift):
                    skipped_count += 1
                    continue

            self.iteration += 1
            
            try:
                self.node.setName(name, unique_name=True)
            except hou.PermissionError:
                pass 
            
            material, materialNode, outputNode, dispNode, is_new = self.get_or_create_material(name)
            self.setGroups(total_materials, material)

            changes = []
            
            # --- Dedicated Albedo/AO Handler ---
            changes.append(self.setup_albedo_ao(material, materialNode, name))
            
            # --- Standard Nodes ---
            changes.append(self.create_texture_node(self.roughness_dict, material, materialNode, name, 'roughnessdir'))
            changes.append(self.create_texture_node(self.specular_dict, material, materialNode, name, 'speculardir'))
            changes.append(self.create_texture_node(self.metallic_dict, material, materialNode, name, 'metallicdir'))
            changes.append(self.create_texture_node(self.opacity_dict, material, materialNode, name, 'opacitydir'))

            if self.renderer == "karma":
                changes.append(self.create_texture_node(self.normal_dict, material, materialNode, name, 'normaldir', secondary_node_type='mtlxnormalmap', secondary_input='in'))
            elif self.renderer == "redshift":
                changes.append(self.create_texture_node(self.normal_dict, material, outputNode, name, 'normaldir', secondary_node_type='redshift::BumpMap', secondary_input='input', defaults={'inputType': '1'}))
            else:
                changes.append(self.create_texture_node(self.normal_dict, material, materialNode, name, 'normaldir'))

            if self.renderer == "octane":
                changes.append(self.create_texture_node(
                    self.displacement_dict, material, materialNode, name, 'displacementdir', 
                    secondary_node_type='NT_VERTEX_DISPLACEMENT', secondary_input='texture', defaults={'black_level': 0.5, 'amount': octane_disp_amount}
                ))
                changes.append(self.create_texture_node(
                    self.emission_dict, material, materialNode, name, 'emissivedir',
                    secondary_node_type='NT_EMIS_TEXTURE', secondary_input='efficiency_or_texture'
                ))
            elif self.renderer == "karma":
                changes.append(self.create_texture_node(self.displacement_dict, material, dispNode, name, 'displacementdir'))
                
                if dispNode:
                    current_scale = dispNode.parm('scale').eval()
                    if abs(current_scale - karma_disp_amount) > 0.00001:
                        dispNode.parm('scale').set(karma_disp_amount)
                        changes.append(True)
                        
                changes.append(self.create_texture_node(self.emission_dict, material, materialNode, name, 'emissivedir'))
            elif self.renderer == "redshift":
                changes.append(self.create_texture_node(
                    self.displacement_dict, material, outputNode, name, 'displacementdir', 
                    secondary_node_type='redshift::Displacement', secondary_input='texMap', defaults={'scale': redshift_disp_amount}
                ))
                changes.append(self.create_texture_node(self.emission_dict, material, materialNode, name, 'emissivedir'))

            made_changes = any(changes)

            if is_new:
                created_count += 1
                material.layoutChildren()
            elif made_changes:
                updated_count += 1
                material.layoutChildren()
            else:
                skipped_count += 1
            
        self.mat_node.layoutChildren()

        engine_name = self.renderer.capitalize()
        msg_lines = [f"Processed {total_materials} {engine_name} material(s)."]
        if custom_disp_scale is not None:
            msg_lines.append(f"- JSON Custom Scale Applied: {custom_disp_scale}")
        
        if created_count > 0: msg_lines.append(f"- Created: {created_count}")
        if updated_count > 0: msg_lines.append(f"- Updated: {updated_count}")
        if skipped_count > 0: msg_lines.append(f"- Skipped (unchanged or mismatch): {skipped_count}")
            
        hou.ui.displayMessage("\n".join(msg_lines), title="Material Builder Completed")