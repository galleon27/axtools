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
        nodes_to_process.extend(sel_node.allSubChildren())
        nodes_to_process.append(sel_node)

    unique_nodes = set(nodes_to_process)

    for node in unique_nodes:
        if node.type().name() in rop_type_names:
            parent_path = node.parent().path()
            if parent_path not in found_rops_by_parent:
                found_rops_by_parent[parent_path] = []
            
            if node not in found_rops_by_parent[parent_path]:
                 found_rops_by_parent[parent_path].append(node)
            
    return found_rops_by_parent

def find_rop_nodes_in_all_obj(rop_type_names):
    """
    Finds all ROP nodes whose type matches any in the provided list,
    searching all nodes under /obj.
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
        
        self.setWindowTitle("ROP Exporter")
        self.setMinimumWidth(650)
        self.setMinimumHeight(450)

        self.rop_model = QtGui.QStandardItemModel()
        self.rop_model.setHorizontalHeaderLabels(["ROP Name", "Node", "Output Path"])

        self.rop_tree_view = QtWidgets.QTreeView()
        self.rop_tree_view.setModel(self.rop_model)
        self.rop_tree_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # --- CHANGE 1: Set the trigger for editing ---
        self.rop_tree_view.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        self.rop_tree_view.setSortingEnabled(False)
        self.rop_tree_view.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        self.rop_tree_view.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        self.rop_tree_view.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.rop_tree_view.header().resizeSection(0, 150)
        self.rop_tree_view.header().resizeSection(1, 150)
        self.rop_tree_view.header().setStyleSheet("QHeaderView::section { padding-left: 20px; padding-right: 40px; }")
        
        self.main_label = QtWidgets.QLabel("Found ROPs:")
        self.refresh_button = QtWidgets.QPushButton("Refresh List")
        self.export_button = QtWidgets.QPushButton("Export Selected ROPs")
        
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
        # --- CHANGE 2: Connect the itemChanged signal to our handler function ---
        self.rop_model.itemChanged.connect(self.on_item_changed)

        self.populate_tree()

    def on_item_changed(self, item):
        """
        This function is called whenever an item in the tree view is edited.
        """
        # Ensure the change happened in the "Output Path" column (index 2)
        if item.column() != 2:
            return

        # Get the new path from the item's text
        new_path = item.text()
        
        # Get the corresponding "ROP Name" item from the same row to retrieve the node
        parent_item = item.parent()
        if not parent_item:
            return # Should not happen for a ROP item
            
        name_item = parent_item.child(item.row(), 0)
        if not name_item:
            return
            
        rop_node = name_item.data(QtCore.Qt.UserRole)
        
        # Check if we have a valid Houdini node
        if not isinstance(rop_node, hou.Node):
            return
        
        # Update the parameter on the actual Houdini node
        try:
            output_parm = rop_node.parm("sopoutput")
            if output_parm:
                output_parm.set(new_path)
                print(f"Updated {rop_node.path()} output to: {new_path}")
            else:
                message = f"Node {rop_node.path()} has no 'sopoutput' parameter."
                print(message)
                hou.ui.displayMessage(message, severity=hou.severityType.Warning)
        except hou.Error as e:
            message = f"Error setting parameter for {rop_node.path()}:\n{e}"
            print(message)
            hou.ui.displayMessage(message, severity=hou.severityType.Error)
            # Revert the UI text to the actual parameter value to avoid confusion
            item.setText(output_parm.eval())

    def populate_tree(self):
        """
        Clears the tree and fills it with ROPs.
        """
        # Block signals while populating to prevent on_item_changed from firing
        self.rop_model.blockSignals(True)
        
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
            self.rop_model.blockSignals(False) # Re-enable signals
            return

        sorted_parent_paths = sorted(found_rops_by_parent.keys())

        for parent_path in sorted_parent_paths:
            parent_rop_nodes = found_rops_by_parent[parent_path]
            
            parent_node_name = hou.node(parent_path).name()
            parent_item = QtGui.QStandardItem(f"{parent_node_name}")
            parent_item.setEditable(False)
            parent_item.setSelectable(False)

            parent_rop_nodes.sort(key=lambda x: x.name())

            for rop_node in parent_rop_nodes:
                rop_name_item = QtGui.QStandardItem(rop_node.name())
                rop_name_item.setEditable(False) # Ensure name is not editable
                
                node_path_item = QtGui.QStandardItem(parent_path)
                node_path_item.setEditable(False) # Ensure node path is not editable
                
                output_path = ""
                if rop_node.parm('sopoutput'):
                    output_path = rop_node.parm('sopoutput').eval()
                
                rop_output_item = QtGui.QStandardItem(output_path)
                # --- CHANGE 3: Make the output path item editable ---
                rop_output_item.setEditable(True)
                
                rop_name_item.setData(rop_node, QtCore.Qt.UserRole)
                
                parent_item.appendRow([rop_name_item, node_path_item, rop_output_item])
            
            self.rop_model.appendRow(parent_item)
            self.rop_tree_view.expand(parent_item.index())
            
        self.rop_tree_view.resizeColumnToContents(0)
        self.rop_tree_view.resizeColumnToContents(1)
        
        # Re-enable signals after population is complete
        self.rop_model.blockSignals(False)

    def export_selected(self):
        """
        Gets the selected ROP nodes from the tree view and runs the execute function.
        """
        selection_model = self.rop_tree_view.selectionModel()
        selected_indexes = selection_model.selectedRows(0)
        
        if not selected_indexes:
            hou.ui.displayMessage("No ROPs selected in the list to export.", severity=hou.severityType.Warning)
            return
            
        nodes_to_export = []
        for index in selected_indexes:
            if index.parent().isValid():
                item = self.rop_model.itemFromIndex(index)
                if item:
                    node = item.data(QtCore.Qt.UserRole)
                    if isinstance(node, hou.Node):
                        nodes_to_export.append(node)
        
        if nodes_to_export:
            unique_nodes_to_export = list(set(nodes_to_export))
            execute_rops(unique_nodes_to_export)
        else:
            hou.ui.displayMessage("No actual ROP nodes were selected. Please select the child ROP items, not the parent categories.", severity=hou.severityType.Warning)


# --- Function to launch the UI ---

def show_ui():
    """
    Creates and shows the UI. Ensures that only one instance of the UI exists.
    """
    if RopExporterUI.ui_instance:
        RopExporterUI.ui_instance.close()
        
    RopExporterUI.ui_instance = RopExporterUI()
    RopExporterUI.ui_instance.show()


# --- Start the Application ---
show_ui()