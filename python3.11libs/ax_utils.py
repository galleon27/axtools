import hou
import os
import platform
from pxr import Usd

def open_node_path(node):
    # Parameter list
    target_parms = ['file', 'filename', 'map', 'tex', 'picture', 'sopoutput', 'vm_picture', 'copoutput']
    
    file_path = None
    
    # Check parameters
    for parm_name in target_parms:
        parm = node.parm(parm_name)
        if parm:
            raw_path = parm.eval()
            if raw_path:
                file_path = raw_path
                break
    
    if file_path:
        abs_path = os.path.abspath(file_path)
        
        # 1. Try to highlight the specific file
        if os.path.exists(abs_path):
            try:
                hou.ui.showInFileBrowser(abs_path)
            except hou.Error:
                pass
        # 2. If file is missing, open the parent directory
        else:
            folder = os.path.dirname(abs_path)
            if os.path.exists(folder):
                try:
                    hou.ui.showInFileBrowser(folder)
                except hou.Error:
                    pass
            else:
                hou.ui.setStatusMessage(f"AX: Directory not found: {folder}", severity=hou.severityType.Warning)
    else:
        hou.ui.setStatusMessage(f"AX: No file path found on {node.name()}", severity=hou.severityType.Message)





def populate_material_assignments(node=None):
    # 1. Get the node (use provided, or fallback to selection)
    if node is None:
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            print("Error: No node selected.")
            return
        node = selected_nodes[0]
    
    # 2. Validate node type
    valid_types = ["componentmaterial", "assignmaterial"]
    if node.type().name() not in valid_types:
        print(f"Error: Selected node '{node.name()}' is not a componentmaterial or assignmaterial.")
        return
        
    # 3. Ensure node has an input to read the USD stage
    if not node.inputs() or node.input(0) is None:
        print("Error: Node must be connected to an input to read the scene graph.")
        return
        
    stage = node.input(0).stage()
    if not stage:
        print("Error: Could not retrieve USD stage from input.")
        return

    prims_to_assign = []
    
    # 4. Iterate through root prims
    for root_prim in stage.GetPseudoRoot().GetChildren():
        root_path = root_prim.GetPath().pathString
        prim_locations = {}
        
        for folder_name in ["geo", "proxy"]:
            folder_prim = root_prim.GetChild(folder_name)
            
            if folder_prim and folder_prim.IsValid():
                for child in folder_prim.GetChildren():
                    prim_name = child.GetName()
                    if prim_name not in prim_locations:
                        prim_locations[prim_name] = []
                    prim_locations[prim_name].append(folder_name)
        
        # 5. Build the paths based on the gathered data
        for prim_name, folders in prim_locations.items():
            if "geo" in folders and "proxy" in folders:
                # Exists in both: use a wildcard
                wildcard_path = f"{root_path}/*/{prim_name}"
                prims_to_assign.append(wildcard_path)
            else:
                # Exists in only one: use the exact path
                folder = folders[0]
                exact_path = f"{root_path}/{folder}/{prim_name}"
                prims_to_assign.append(exact_path)
                    
    if not prims_to_assign:
        print("Warning: No primitives found in 'geo' or 'proxy' folders.")
        return

    # Sort paths alphabetically
    prims_to_assign.sort()

    # 6. Set the multiparm length ('nummaterials')
    num_prims = len(prims_to_assign)
    node.parm("nummaterials").set(num_prims)
    
    # 7. Fill the 'primpattern#' parameters
    for i, prim_path in enumerate(prims_to_assign):
        idx = i + 1 
        parm_name = f"primpattern{idx}"
        parm = node.parm(parm_name)
        
        if parm is not None:
            parm.set(prim_path)
            
    print(f"Success: Assigned {num_prims} primitive patterns to '{node.name()}'.")

