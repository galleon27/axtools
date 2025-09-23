import hou

def get_selected_node():
    """Returns the node from the 'sopnode' parameter if valid, else None."""
    if  not hou.pwd().inputs() or hou.pwd().inputs()[0] is None:
        sopnode_path = hou.pwd().parm('sopnode').eval() 
    else:
        sopnode_path = hou.pwd().input(0).path()

    if not sopnode_path:
        hou.ui.displayMessage("Nothing is selected! Please select a node.")
        return None

    node = hou.node(sopnode_path)
    return node

def get_display_node(node):
    """Returns the child node with display flag set, or None if none found."""
    for child in node.children():
        if child.isDisplayFlagSet():
            return child
    return None

def init():
    selected = get_selected_node()
    if not selected:
        return  # Stop if nothing is selected
    
    root = hou.pwd()
    
    # Clean up existing nulls
    for child in root.children():
        if child.type().name() == "null":
            child.destroy()
            
    display_node = get_display_node(selected)

    if not display_node:
        hou.ui.displayMessage("No child node with display flag set found!")
        return
    
    geo = display_node.geometry()

    # Check if orient attribute exists
    has_orient = geo.findPointAttrib("orient") is not None
            
    for point in geo.points():  # Using points() instead of iterPoints() for better indexing
        ptnum = point.number()
        null = root.createNode('null', f"point_{ptnum}")  # Combined create and name
        null.moveToGoodPosition()

        # Set position expressions
        for axis, idx in zip(('x', 'y', 'z'), range(3)):
            null.parm(f"t{axis}").setExpression(
                f'point("{display_node.path()}", {ptnum}, "P", {idx})'
            )
        
        # Set rotation if orient exists - using Python expressions
        if has_orient:
            for axis, idx in zip(('rx', 'ry', 'rz'), range(3)):
                expr = f"""
try:
    geo = hou.node('{display_node.path()}').geometry()
    pt = geo.iterPoints()[{ptnum}]
    orient = pt.attribValue('orient')
    if orient and len(orient) == 4:
        q = hou.Quaternion(orient)
        e = q.extractEulerRotates('XYZ')
        return e[{idx}]
    return 0.0
except:
    return 0.0
"""
                null.parm(axis).setExpression(
                    expr,
                    language=hou.exprLanguage.Python
                )