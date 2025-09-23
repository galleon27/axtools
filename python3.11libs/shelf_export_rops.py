import hou
from PySide2 import QtWidgets, QtCore, QtGui

# --- Define the list of all ROP types we want to search for ---
ROP_TYPES_TO_FIND = ['rop_fbx', 'rop_geometry', 'rop_alembic']


# --- Core Logic Functions ---

def find_rop_nodes_in_selection(rop_type_names):
    """
    Finds ROP nodes within the user's current selection and their children.
    Returns a dictionary grouped by the ROP's immediate parent node path.
    """
    selection = hou.selectedNodes()
    found_rops_by_parent = {}

    nodes_to_process = []
    for sel_node in selection:
        # Add all children of the selected node
        nodes_to_process.extend(sel_node.allSubChildren())
        # Also check the selected node itself, in case it's a ROP
        nodes_to_process.append(sel_node)

    # Use a set to get a unique list of nodes to check
    unique_nodes = set(nodes_to_process)

    for node in unique_nodes:
        if node.type().name() in rop_type_names:
            parent_path = node.parent().path()
            if parent_path not in found_rops_by_parent:
                found_rops_by_parent[parent_path] = []
            
            # Since we're iterating over a unique set, no duplicate check needed here
            found_rops_by_parent[parent_path].append(node)
            
    return found_rops_by_parent

def find_rop_nodes_in_all_obj(rop_type_names):
    """
    Finds all ROP nodes whose type matches any in the provided list,
    searching all nodes under /obj.
    Returns a dictionary grouped by parent node.
    """
    obj_node = hou.node("/obj")
    if not obj_node:
        hou.ui.displayMessage("'/obj' node not found.", severity=hou.severityType.Error)
        return {}

    found_rops_by_parent = {}

    all_obj_children = obj_node.allSubChildren()

    for child_node in all_obj_children:
        if child_node.type().name() in rop_type_names:
            parent_path = child_node.parent().path()
            if parent_path not in found_rops_by_parent:
                found_rops_by_parent[parent_path] = []
            found_rops_by_parent[parent_path].append(child_node)

    if not any(found_rops_by_parent.values()):
        print("Search complete. No matching ROP nodes were found in /obj.")
        
    return found_rops_by_parent


def execute_rops(rop_nodes):
    """
    Executes 'Save to Disk' on a list of ROP nodes.
    """
    if not rop_nodes:
        print("No ROP nodes were provided for execution.")
        return

    print(f"\nExecuting {len(rop_nodes)} ROP node(s)...")
    
    for rop in rop_nodes:
        try:
            print(f"-> Executing {rop.path()}")
            rop.render()
        except hou.Error as e:
            message = f"Error executing {rop.path()}:\n{e}"
            print(message)
            hou.ui.displayMessage(message, severity=hou.severityType.Error)
            
    print("\nExecution complete.")


# --- PySide2 GUI Class ---

class RopExporterUI(QtWidgets.QWidget):
    
    ui_instance = None

    def __init__(self, parent=None):
        super(RopExporterUI, self).__init__(parent=hou.qt.mainWindow(), f=QtCore.Qt.Window)
        
        # --- Window Properties ---
        self.setWindowTitle("ROP Exporter")
        self.setMinimumWidth(650)
        self.setMinimumHeight(450)

        # --- WIDGETS ---
        self.rop_model = QtGui.QStandardItemModel()
        self.rop_model.setHorizontalHeaderLabels(["ROP Name", "Node", "Output Path"])

        self.rop_tree_view = QtWidgets.QTreeView()
        self.rop_tree_view.setModel(self.rop_model)
        self.rop_tree_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        
        # --- IMPROVEMENT: Changed edit trigger for better responsiveness ---
        self.rop_tree_view.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        
        self.rop_tree_view.setSortingEnabled(False) # Disable sorting to maintain groups
        self.rop_tree_view.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        self.rop_tree_view.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        self.rop_tree_view.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.rop_tree_view.header().resizeSection(0, 150)
        self.rop_tree_view.header().resizeSection(1, 150)
        
        self.rop_tree_view.header().setStyleSheet("QHeaderView::section { padding-left: 20px; padding-right: 40px; }")
        
        self.main_label = QtWidgets.QLabel("Found ROPs:")
        self.refresh_button = QtWidgets.QPushButton("Refresh List")
        self.export_button = QtWidgets.QPushButton("Export Selected ROPs")
        
        # --- Layout ---
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)
        
        main_layout.addWidget(self.main_label)
        main_layout.addWidget(self.rop_tree_view)
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.export_button)
        main_layout.addLayout(button_layout)

        # --- Connections ---
        self.refresh_button.clicked.connect(self.populate_tree)
        self.export_button.clicked.connect(self.export_selected)
        self.rop_model.itemChanged.connect(self.on_item_changed)

        # Initial population
        self.populate_tree()

    def populate_tree(self):
        """
        Clears the tree and fills it with ROPs.
        Searches selection first, then falls back to all of /obj.
        """
        # Temporarily disconnect the signal to prevent it firing during population
        self.rop_model.itemChanged.disconnect(self.on_item_changed)
        
        self.rop_model.clear()
        self.rop_model.setHorizontalHeaderLabels(["ROP Name", "Node", "Output Path"])
        
        selection = hou.selectedNodes()
        found_rops_by_parent = {}
        
        if selection:
            self.main_label.setText("Found ROPs in selected node(s):")
            found_rops_by_parent = find_rop_nodes_in_selection(ROP_TYPES_TO_FIND)
        else:
            self.main_label.setText("Found ROPs in /obj (grouped by parent node):")
            found_rops_by_parent = find_rop_nodes_in_all_obj(ROP_TYPES_TO_FIND)
        
        if not any(found_rops_by_parent.values()):
            no_rops_item = QtGui.QStandardItem("No matching ROPs found.")
            no_rops_item.setSelectable(False)
            self.rop_model.appendRow(no_rops_item)
        else:
            sorted_parent_paths = sorted(found_rops_by_parent.keys())

            for parent_path in sorted_parent_paths:
                parent_rop_nodes = sorted(found_rops_by_parent[parent_path], key=lambda x: x.name())
                
                parent_node_name = hou.node(parent_path).name()
                parent_item = QtGui.QStandardItem(f"{parent_node_name}")
                parent_item.setEditable(False)
                parent_item.setSelectable(False)

                for rop_node in parent_rop_nodes:
                    rop_name_item = QtGui.QStandardItem(rop_node.name())
                    rop_name_item.setEditable(False)
                    
                    node_path_item = QtGui.QStandardItem(parent_path)
                    node_path_item.setEditable(False)
                    
                    output_path = rop_node.parm('sopoutput').eval() if rop_node.parm('sopoutput') else ""
                    rop_output_item = QtGui.QStandardItem(output_path)
                    
                    # Store the hou.Node object on the items that need it
                    rop_name_item.setData(rop_node, QtCore.Qt.UserRole)
                    rop_output_item.setData(rop_node, QtCore.Qt.UserRole) # Added for direct access
                    
                    parent_item.appendRow([rop_name_item, node_path_item, rop_output_item])
                
                self.rop_model.appendRow(parent_item)
                self.rop_tree_view.expand(parent_item.index())
            
        # Reconnect the signal
        self.rop_model.itemChanged.connect(self.on_item_changed)
            
        self.rop_tree_view.resizeColumnToContents(0)
        self.rop_tree_view.resizeColumnToContents(1)

    def on_item_changed(self, item):
        """
        Handles the editing of the output path. This is triggered after an item is changed.
        """
        # We only care about edits in the "Output Path" column (column 2)
        if item.column() != 2:
            return

        new_path = item.text()
        rop_node = item.data(QtCore.Qt.UserRole) # Directly get the node from the item
        
        if isinstance(rop_node, hou.Node) and rop_node.parm('sopoutput'):
            try:
                # Set the parameter on the actual Houdini node
                rop_node.parm('sopoutput').set(new_path)
                print(f"Updated {rop_node.path()} output path to: {new_path}")
            except hou.Error as e:
                message = f"Error setting output path for {rop_node.path()}:\n{e}"
                print(message)
                hou.ui.displayMessage(message, severity=hou.severityType.Error)
                
                # If setting the param fails, revert the UI text to the node's actual value
                self.rop_model.itemChanged.disconnect(self.on_item_changed)
                item.setText(rop_node.parm('sopoutput').eval())
                self.rop_model.itemChanged.connect(self.on_item_changed)

    def export_selected(self):
        """
        Gets the selected ROP nodes from the tree view and runs the execute function.
        """
        selection_model = self.rop_tree_view.selectionModel()
        selected_indexes = selection_model.selectedRows(0) # Get selected rows from the first column
        
        if not selected_indexes:
            hou.ui.displayMessage("No ROPs selected in the list to export.", severity=hou.severityType.Warning)
            return
            
        nodes_to_export = []
        for index in selected_indexes:
            # We only care about child items (the actual ROPs), not the parent groups
            if index.parent().isValid():
                item = self.rop_model.itemFromIndex(index)
                if item:
                    node = item.data(QtCore.Qt.UserRole)
                    if isinstance(node, hou.Node):
                        nodes_to_export.append(node)
        
        if nodes_to_export:
            # Use a set to ensure each node is exported only once, even if selected multiple times
            unique_nodes_to_export = list(set(nodes_to_export))
            execute_rops(unique_nodes_to_export)
        else:
            hou.ui.displayMessage("No actual ROP nodes were selected. Please select the child ROP items, not the parent categories.", severity=hou.severityType.Warning)


# --- Function to launch the UI ---

def show_ui():
    """
    Creates and shows the UI. Ensures that only one instance of the UI exists.
    """
    # This simple singleton pattern is good for Houdini tools
    if RopExporterUI.ui_instance:
        try:
            RopExporterUI.ui_instance.close()
            RopExporterUI.ui_instance.deleteLater()
        except Exception:
            pass # Ignore errors if the window was already closed
        
    RopExporterUI.ui_instance = RopExporterUI()
    RopExporterUI.ui_instance.show()


# --- Start the Application ---
show_ui()