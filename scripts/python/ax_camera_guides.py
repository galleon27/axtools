"""
AX Camera Guides - composition guides drawn in the viewport for an OBJ camera.

Called from the AX::AX_camera_guides HDA and the AX shelf:

    OnCreated:                  ax_camera_guides.on_created(kwargs["node"])
    Camera parm callback:       ax_camera_guides.camera_changed(kwargs["node"])
    Use Viewport Camera button: ax_camera_guides.use_viewport_camera(kwargs["node"])
    Look Through button:        ax_camera_guides.look_through(kwargs["node"])
    Shelf tool:                 ax_camera_guides.create_for_viewport(kwargs)

The HDA is parented to the camera, so the guides follow it without expressions.
The guide geometry sits on the display flag and the render flag is on an empty
node, so renderers never see it.
"""

import hou

HDA_TYPE = "AX::AX_camera_guides::1.0"


def _scene_viewer():
    try:
        return hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    except Exception:
        return None


def _viewport_camera():
    """The OBJ camera the current Scene Viewer is looking through, or None."""
    viewer = _scene_viewer()
    if viewer is None:
        return None
    cam = viewer.curViewport().camera()
    if cam is not None and cam.type().category() == hou.objNodeTypeCategory():
        return cam
    return None


def _is_camera(node):
    return node is not None and node.type().name().split("::")[0] in ("cam", "camera")


def _scene_cameras():
    return [n for n in hou.node("/obj").allSubChildren() if _is_camera(n)]


def _set_camera(node, cam):
    node.parm("camera").set(cam.path() if cam else "")
    camera_changed(node)


def camera_changed(node):
    """Parent the guides to the chosen camera (or unparent if it isn't one)."""
    path = node.evalParm("camera")
    cam = hou.node(path) if path else None
    if path and not _is_camera(cam):
        if hou.isUIAvailable():
            hou.ui.setStatusMessage("AX Camera Guides: %s is not a camera" % path,
                                    hou.severityType.Warning)
        cam = None
    if cam is not None and cam.parent() != node.parent():
        # Parenting only works between siblings; fall back to unparented.
        cam = None
    node.setFirstInput(cam)
    if cam is not None:
        pos = cam.position()
        node.setPosition(hou.Vector2(pos[0], pos[1] - 1.2))


def on_created(node):
    node.setColor(hou.Color((0.8, 0.4, 0.2)))
    if node.evalParm("camera"):
        return
    cam = _viewport_camera()
    if cam is None:
        cams = _scene_cameras()
        cam = cams[0] if len(cams) == 1 else None
    if cam is not None:
        _set_camera(node, cam)


def use_viewport_camera(node):
    cam = _viewport_camera()
    if cam is None:
        hou.ui.displayMessage("The viewport is not looking through an OBJ camera.",
                              severity=hou.severityType.Warning)
        return
    _set_camera(node, cam)


def look_through(node):
    path = node.evalParm("camera")
    cam = hou.node(path) if path else None
    viewer = _scene_viewer()
    if cam is None or viewer is None:
        return
    viewer.curViewport().setCamera(cam)


def create_for_viewport(kwargs=None):
    """Shelf tool: add guides for the viewport camera (or a picked one)."""
    cam = _viewport_camera()
    if cam is None:
        sel = [n for n in hou.selectedNodes() if _is_camera(n)]
        cam = sel[0] if sel else None
    if cam is None:
        cams = _scene_cameras()
        if len(cams) == 1:
            cam = cams[0]
        elif cams:
            picked = hou.ui.selectFromList([c.path() for c in cams], exclusive=True,
                                           title="AX Camera Guides",
                                           message="Pick a camera")
            if not picked:
                return None
            cam = cams[picked[0]]
    if cam is None:
        hou.ui.displayMessage("No OBJ camera found.", severity=hou.severityType.Warning)
        return None

    existing = [n for n in cam.outputs() if n.type().name() == HDA_TYPE]
    if existing:
        node = existing[0]
    else:
        node = cam.parent().createNode(HDA_TYPE, cam.name() + "_guides")
        _set_camera(node, cam)
    node.setCurrent(True, clear_all_selected=True)

    viewer = _scene_viewer()
    if viewer is not None:
        viewer.curViewport().setCamera(cam)
    return node
