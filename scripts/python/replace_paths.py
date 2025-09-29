import hou

# --- Version-Agnostic PySide Import ---
# For compatibility with Houdini 20+ (PySide6) and older versions (PySide2).
try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtCore


# --- Node/Parameter Mapping ---
# Maps node types to the parameter name holding the file path.
NODE_PARAM_MAP = {
    # Karma Image Node
    'mtlximage': 'file',
    
    # Redshift Texture Sampler Node
    'redshift::TextureSampler': 'tex0',
    
    # SOP File Node
    'file': 'file',
    
    # Octane Image Texture Nodes
    'octane::NT_TEX_IMAGE': 'A_FILENAME',
    'octane::NT_TEX_FLOATIMAGE': 'A_FILENAME',
    'octane::NT_TEX_ALPHA': 'A_FILENAME'
}


class PathReplacerDialog(QtWidgets.QDialog):
    """A custom dialog window for user input."""
    def __init__(self, parent=hou.ui.mainQtWindow()):
        super(PathReplacerDialog, self).__init__(parent)

        self.setWindowTitle("Replace File Paths (Preserves Expressions)")
        self.setMinimumWidth(450)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        
        form_layout = QtWidgets.QFormLayout()
        self.original_path_input = QtWidgets.QLineEdit()
        self.new_path_input = QtWidgets.QLineEdit()
        form_layout.addRow("Original Path:", self.original_path_input)
        form_layout.addRow("New Path:", self.new_path_input)

        button_layout = QtWidgets.QHBoxLayout()
        self.ok_button = QtWidgets.QPushButton("Replace")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        button_layout.addStretch() 
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)

        self.main_layout.addLayout(form_layout)
        self.main_layout.addLayout(button_layout)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def getValues(self):
        """Returns the text from the input fields."""
        return (self.original_path_input.text(), self.new_path_input.text())


def run_multi_path_replacer():
    """Main function to find and replace paths on various node types."""
    selected_nodes = hou.selectedNodes()
    if not selected_nodes:
        hou.ui.displayMessage("Please select a parent node first.", title="Error")
        return

    start_node = selected_nodes[0]
    
    dialog = PathReplacerDialog()
    
    if dialog.exec_(): 
        original_path, new_path = dialog.getValues()

        if not original_path:
            hou.ui.displayMessage("The 'Original Path' field cannot be empty.", title="Error")
            return

        updated_nodes_count = 0
        
        with hou.undos.group("Replace File Paths in Multiple Nodes"):
            for node in start_node.allSubChildren():
                node_type_name = node.type().name()
                
                if node_type_name in NODE_PARAM_MAP:
                    param_name = NODE_PARAM_MAP[node_type_name]
                    file_parm = node.parm(param_name)
                    
                    if file_parm:
                        # --- KEY CHANGE: Use unexpandedString() ---
                        # This reads the literal text, including variables like $HIP,
                        # instead of the evaluated, absolute path.
                        current_raw_path = file_parm.unexpandedString()
                        
                        if original_path in current_raw_path:
                            # Replace the text within the raw string
                            new_raw_path = current_raw_path.replace(original_path, new_path)
                            
                            # Set the parameter with the new string, preserving the expression
                            file_parm.set(new_raw_path)
                            updated_nodes_count += 1
        
        hou.ui.displayMessage(
            f"Found and updated {updated_nodes_count} file path(s).",
            title="Process Complete"
        )

# --- Entry point for the shelf tool ---
run_multi_path_replacer()