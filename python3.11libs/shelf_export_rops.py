import hou
from PySide2 import QtWidgets, QtCore, QtGui

# --- Define the list of all ROP types we want to search for ---
ROP_TYPES_TO_FIND = ['rop_fbx', 'rop_geometry', 'rop_alembic']


# --- Core Logic Functions (find function is now modified) ---

def find_rop_nodes_in_selection(rop_type_names, recursive=True):
    """
    Finds all ROP nodes whose type matches any in the provided list.
    """
    selection = hou.selectedNodes()
    if not selection:
        hou.ui.displayMessage("No nodes selected in the network.", severity=hou.severityType.Warning)
        return []

    found_rops = []
    for node in selection:
        nodes_to_search = node.allSubChildren() if recursive else node.children()
        
        for child in nodes_to_search:
            # Check if the node's type name is IN the list
            if child.type().name() in rop_type_names:
                found_rops.append(child)
                
    if not found_rops:
        print("Search complete. No matching ROP nodes were found.")
        
    # Sort the list alphabetically by path for a consistent UI display
    found_rops.sort(key=lambda x: x.path())
    return found_rops


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


# --- PySide2 GUI Class (Upgraded to QTableWidget) ---

class RopExporterUI(QtWidgets.QWidget):
    
    ui_instance = None

    def __init__(self, parent=None):
        super(RopExporterUI, self).__init__(parent=hou.qt.mainWindow(), f=QtCore.Qt.Window)
        
        # --- Window Properties ---
        self.setWindowTitle("Selective ROP Exporter")
        self.setMinimumWidth(550)
        self.setMinimumHeight(350)

        # --- WIDGETS (Upgraded to QTableWidget) ---
        self.rop_table_widget = QtWidgets.QTableWidget()
        self.rop_table_widget.setColumnCount(2)
        self.rop_table_widget.setHorizontalHeaderLabels(["ROP Name", "Node Path"])
        self.rop_table_widget.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows) # Select whole rows
        self.rop_table_widget.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers) # Make cells not editable
        self.rop_table_widget.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        self.rop_table_widget.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        
        self.refresh_button = QtWidgets.QPushButton("Refresh List from Selection")
        self.export_button = QtWidgets.QPushButton("Export Selected ROPs")
        
        # --- Layout ---
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)
        
        main_layout.addWidget(QtWidgets.QLabel("Found ROPs in selected node(s):"))
        main_layout.addWidget(self.rop_table_widget) # Add the table widget
        
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.export_button)
        main_layout.addLayout(button_layout)

        # --- Connections ---
        self.refresh_button.clicked.connect(self.populate_table)
        self.export_button.clicked.connect(self.export_selected)

    def populate_table(self):
        """
        Clears the table and fills it with ROPs found in the current selection.
        """
        # Clear previous rows
        self.rop_table_widget.setRowCount(0)
        
        found_nodes = find_rop_nodes_in_selection(ROP_TYPES_TO_FIND, recursive=True)
        if not found_nodes:
            return

        # Populate the table row by row
        for node in found_nodes:
            row_position = self.rop_table_widget.rowCount()
            self.rop_table_widget.insertRow(row_position)
            
            # Create QTableWidgetItems for our cells
            name_item = QtWidgets.QTableWidgetItem(node.name())
            path_item = QtWidgets.QTableWidgetItem(node.parm('sopoutput').eval())
            
            # Store the actual hou.Node object in the first item's data role.
            name_item.setData(QtCore.Qt.UserRole, node)
            
            # Add the items to the table
            self.rop_table_widget.setItem(row_position, 0, name_item)
            self.rop_table_widget.setItem(row_position, 1, path_item)
            
    def export_selected(self):
        """
        Gets the selected rows from the table and runs the execute function.
        """
        # Get a list of unique selected row indices
        selected_rows = sorted(list(set(index.row() for index in self.rop_table_widget.selectedIndexes())))
        
        if not selected_rows:
            hou.ui.displayMessage("No ROPs selected in the table to export.", severity=hou.severityType.Warning)
            return
            
        nodes_to_export = []
        for row in selected_rows:
            # Get the item from the first column of the row
            item = self.rop_table_widget.item(row, 0)
            if item:
                # Retrieve the hou.Node object we stored in its data
                node = item.data(QtCore.Qt.UserRole)
                if isinstance(node, hou.Node):
                    nodes_to_export.append(node)
        
        if nodes_to_export:
            execute_rops(nodes_to_export)

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