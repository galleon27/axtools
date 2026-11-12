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


def _context_network(kwargs):
    """The OBJ network the tool was run in (Tab menu), or None (shelf click)."""
    pane = (kwargs or {}).get("pane")
    try:
        net = pane.pwd() if pane is not None else None
    except Exception:
        net = None
    if net is not None and net.childTypeCategory() == hou.objNodeTypeCategory():
        return net
    return None


def _pick_camera(net, interactive=True):
    """Choose a camera. With a network, only cameras directly inside it count.

    Order: selected camera, viewport camera, the only camera, then ask.
    """
    def ok(c):
        return _is_camera(c) and (net is None or c.parent() == net)

    sel = [n for n in hou.selectedNodes() if ok(n)]
    if sel:
        return sel[0]
    cam = _viewport_camera()
    if cam is not None and ok(cam):
        return cam
    cams = [c for c in net.children() if _is_camera(c)] if net is not None else _scene_cameras()
    if len(cams) == 1:
        return cams[0]
    if cams and interactive and hou.isUIAvailable():
        picked = hou.ui.selectFromList([c.path() for c in cams], exclusive=True,
                                       title="AX Camera Guides", message="Pick a camera")
        return cams[picked[0]] if picked else None
    return None


def on_created(node):
    node.setColor(hou.Color((0.8, 0.4, 0.2)))
    if node.evalParm("camera"):
        return
    cam = _pick_camera(node.parent(), interactive=False)
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
    """Shelf / Tab tool: add guides for a camera, or select the existing ones.

    From the Tab menu, the camera comes from the network you are in. From the
    shelf, any camera in the scene can be used.
    """
    net = _context_network(kwargs)
    cam = _pick_camera(net)
    if cam is None:
        where = " in %s" % net.path() if net is not None else ""
        hou.ui.displayMessage("No OBJ camera found%s." % where,
                              severity=hou.severityType.Warning)
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
