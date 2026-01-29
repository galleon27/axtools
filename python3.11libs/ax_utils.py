import hou
import os
import platform

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