import hou
import os
import re
import voptoolutils

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
        
        self.preview_keywords = ["Preview", "preview"]

        self.cached_files = self._cache_directory_files()
        self._setup_config()

    def _cache_directory_files(self):
        """Pre-scans the directory to avoid repeated OS calls."""
        files = []
        if os.path.exists(self.directory_path):
            for filename in os.listdir(self.directory_path):
                if not any(p in filename for p in self.preview_keywords):
                    if filename.lower().endswith(('.png', '.jpg', '.tga', '.tif', '.exr')):
                        files.append(filename)
        return files

    def _setup_config(self):
        """Loads the correct node types, parameter names, and ports based on the renderer."""
        if self.renderer == "octane":
            self.file_parm = 'A_FILENAME'
            
            self.basecolor_dict = {k: {"type": "NT_TEX_IMAGE", "port": "albedo", "color_space": "NAMED_COLOR_SPACE_SRGB"} for k in ["albedo", "basecolor", "base_color", "diffuse"]}
            self.roughness_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "roughness", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in ["roughness"]}
            self.metallic_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "metallic", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in ["metallic", "metalness"]}
            self.opacity_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "opacity", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in ["opacity"]}
            self.normal_dict = {k: {"type": "NT_TEX_IMAGE", "port": "normal", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in ["normal"]}
            self.displacement_dict = {k: {"type": "NT_TEX_FLOATIMAGE", "port": "displacement", "color_space": "NAMED_COLOR_SPACE_OTHER"} for k in ["displacement", "height"]}
            self.emission_dict = {k: {"type": "NT_TEX_IMAGE", "port": "emission", "color_space": "NAMED_COLOR_SPACE_SRGB"} for k in ["emission", "emissive"]}
            
        elif self.renderer == "karma":
            self.file_parm = 'file'
            
            self.basecolor_dict = {k: {"type": "mtlximage", "port": "base_color", "signature": "color3"} for k in ["albedo", "basecolor", "base_color", "diffuse"]}
            self.roughness_dict = {k: {"type": "mtlximage", "port": "specular_roughness", "signature": "float"} for k in ["roughness"]}
            self.metallic_dict = {k: {"type": "mtlximage", "port": "metalness", "signature": "float"} for k in ["metallic", "metalness"]}
            self.opacity_dict = {k: {"type": "mtlximage", "port": "transmission", "signature": "float"} for k in ["opacity"]}
            self.normal_dict = {k: {"type": "mtlximage", "port": "normal", "signature": "vector3"} for k in ["normal"]}
            self.displacement_dict = {k: {"type": "mtlximage", "port": "displacement", "signature": "float"} for k in ["displacement", "height"]}
            self.emission_dict = {k: {"type": "mtlximage", "port": "emission", "signature": "color3"} for k in ["emission", "emissive"]}

    def get_material_names(self):
        """Scans the directory and returns a list of unique material base names."""
        temp = set()
        for filename in self.cached_files:
            name_part = os.path.splitext(filename)[0]
            name_part = re.sub(r'[^a-zA-Z0-9]+', '_', name_part).rstrip('_')
            name = "_".join(name_part.split('_')[:-1])
            temp.add(name)
                
        return list(temp)

    def setGroups(self, total_materials, target_node):
        """Sets the group and material path parameters on the HDA and internal nodes."""
        if not self.mat_node:
            return

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
                material = self.mat_node.createNode('octane_solaris_material_builder', name)
            elif self.renderer == "karma":
                mask = voptoolutils.KARMAMTLX_TAB_MASK
                material = voptoolutils._setupMtlXBuilderSubnet(
                    destination_node=self.mat_node, 
                    name=name, 
                    mask=mask, 
                    folder_label='Karma Material Builder'
                )

        materialNode = None
        output = None
        dispNode = None

        if self.renderer == "octane":
            # Destroy the unwanted auto-generated standard surface node
            unwanted_node = material.node('Material_Standard_Surface1')
            if unwanted_node:
                unwanted_node.destroy()

            for child in material.children():
                if child.type().name() == 'NT_MAT_UNIVERSAL':
                    materialNode = child
                elif child.type().name() == 'octane_material':
                    output = child
            
            if not materialNode:
                materialNode = material.createNode('NT_MAT_UNIVERSAL')
            if not output:
                output = material.createNode('octane_material')
                
            # Always ensure the material is wired to the output, even if they already existed
            output.setNamedInput('material', materialNode, 'NT_MAT_UNIVERSAL')
                
        elif self.renderer == "karma":
            for child in material.children():
                if child.type().name() == 'mtlxstandard_surface':
                    materialNode = child
                elif child.type().name() == 'suboutput':  
                    output = child
                elif child.type().name() == 'mtlxdisplacement': 
                    dispNode = child
            
            if not materialNode:
                materialNode = material.createNode('mtlxstandard_surface')
            if not output:
                output = material.createNode('suboutput')
            if not dispNode:
                dispNode = material.createNode('mtlxdisplacement')
                
            # Always ensure the surface and displacement are wired to the output
            output.setNamedInput('surface', materialNode, 0)
            output.setNamedInput('displacement', dispNode, 0)
                
        return material, materialNode, output, dispNode, is_new

    def create_texture_node(self, texture_set, material, target_node, name, texdir, secondary_node_type=None, secondary_input='in', defaults=None):
        """Finds the texture file. Returns True if a change was made, False if untouched."""
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
            return False

        node_name = f"{name}_{matched_channel}"
        
        file_path = os.path.join(self.directory, target_file)
        eval_file_path = os.path.join(self.directory_path, target_file)
        
        existing_image = material.node(node_name)
        texdir_parm = self.node.parm(f"{texdir}{self.iteration}")
        
        if existing_image:
            # Safely evaluate paths to avoid keyframe/expression crashes
            current_eval_path = existing_image.parm(self.file_parm).evalAsString()
            ui_eval_path = texdir_parm.evalAsString() if texdir_parm else eval_file_path
            
            if current_eval_path == eval_file_path and ui_eval_path == eval_file_path:
                return False 
                
            existing_image.parm(self.file_parm).set(file_path)
            if texdir_parm:
                texdir_parm.set(existing_image.parm(self.file_parm))
            return True

        properties = texture_set[matched_channel]
        image = material.createNode(properties["type"])
        image.setName(node_name, unique_name=True)
        image.parm(self.file_parm).set(file_path)
        
        if self.renderer == "octane" and "color_space" in properties:
            image.parm('colorSpace').set(properties["color_space"])
            
        if self.renderer == "karma" and "signature" in properties:
            image.parm('signature').set(properties["signature"])

        target_port = properties["port"]

        if secondary_node_type:
            secondary_node = material.createNode(secondary_node_type)
            secondary_node.setNamedInput(secondary_input, image, 0)
            
            if defaults:
                for param, value in defaults.items():
                    secondary_node.parm(param).set(value)
                    
            target_node.setNamedInput(target_port, secondary_node, 0)
        else:
            target_node.setNamedInput(target_port, image, 0)

        if texdir_parm:
            texdir_parm.set(image.parm(self.file_parm))
            
        return True

    def build(self):
        """The main execution method that orchestrates the material creation and updates."""
        material_names = sorted(self.get_material_names())
        total_materials = len(material_names)

        if total_materials == 0:
            hou.ui.displayMessage("No valid textures found in the selected directory.", severity=hou.severityType.Warning, title="No Textures Found")
            return

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for name in material_names:
            
            # Check if a material already exists and belongs to a different renderer
            existing_material = self.mat_node.node(name)
            if existing_material:
                is_octane = (existing_material.type().name() == 'octane_solaris_material_builder')
                
                # If renderer types don't match, skip to avoid namespace collisions
                if (self.renderer == "octane" and not is_octane) or (self.renderer == "karma" and is_octane):
                    skipped_count += 1
                    continue

            self.iteration += 1
            
            try:
                self.node.setName(name, unique_name=True)
            except hou.PermissionError:
                pass 
            
            material, materialNode, outputNode, dispNode, is_new = self.get_or_create_material(name)
            self.setGroups(total_materials, material)

            # Collect booleans into a list to ensure every function runs and doesn't short-circuit
            changes = []
            changes.append(self.create_texture_node(self.basecolor_dict, material, materialNode, name, 'basecolordir'))
            changes.append(self.create_texture_node(self.roughness_dict, material, materialNode, name, 'roughnessdir'))
            changes.append(self.create_texture_node(self.metallic_dict, material, materialNode, name, 'metallicdir'))
            changes.append(self.create_texture_node(self.opacity_dict, material, materialNode, name, 'opacitydir'))

            if self.renderer == "karma":
                changes.append(self.create_texture_node(self.normal_dict, material, materialNode, name, 'normaldir', secondary_node_type='mtlxnormalmap', secondary_input='in'))
            else:
                changes.append(self.create_texture_node(self.normal_dict, material, materialNode, name, 'normaldir'))

            if self.renderer == "octane":
                changes.append(self.create_texture_node(
                    self.displacement_dict, material, materialNode, name, 'displacementdir', 
                    secondary_node_type='NT_VERTEX_DISPLACEMENT', secondary_input='texture', defaults={'black_level': 0.5, 'amount': 0.01}
                ))
                changes.append(self.create_texture_node(
                    self.emission_dict, material, materialNode, name, 'emissivedir',
                    secondary_node_type='NT_EMIS_TEXTURE', secondary_input='efficiency_or_texture'
                ))
            elif self.renderer == "karma":
                changes.append(self.create_texture_node(self.displacement_dict, material, dispNode, name, 'displacementdir'))
                changes.append(self.create_texture_node(self.emission_dict, material, materialNode, name, 'emissivedir'))

            # Check if any textures were actually changed or added
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
        
        if created_count > 0: msg_lines.append(f"- Created: {created_count}")
        if updated_count > 0: msg_lines.append(f"- Updated: {updated_count}")
        if skipped_count > 0: msg_lines.append(f"- Skipped (unchanged or mismatch): {skipped_count}")
            
        hou.ui.displayMessage("\n".join(msg_lines), title="Material Builder Completed")