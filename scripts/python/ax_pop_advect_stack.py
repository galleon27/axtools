"""
AX POP Advect Stack - several POP Advect by Volumes nodes in a row, with the
Velocity Scale of each one compensated for the Velocity Blend of the nodes after it.

Called from the AX_pop_advect_stack HDA (DOP):

    OnCreated / OnLoaded:       ax_pop_advect_stack.sync(kwargs["node"])
    Advects multiparm callback: ax_pop_advect_stack.sync(kwargs["node"])
    Rebuild button:             ax_pop_advect_stack.sync(kwargs["node"])

In Update Velocity mode each advect does  v = lerp(v, scale * V, blend)  once per
timestep. A later advect with blend b scales everything before it by (1 - b), so
the field of advect i ends up weighted by  b_i * s_i * prod_{j > i}(1 - b_j).
To make every advect act as if it were alone, its internal scale is divided by
that product. Only Update Velocity advects are compensated and only Update
Velocity advects count as attenuating: forces are integrated after the POP
chain and Update Position moves P directly.

The internal advects live in the editable subnet `stack` and are created,
wired and linked here, one per multiparm instance. Slot K of the multiparm
drives the node named by its label_K parm (default `advectK`).
"""

import re

import hou

STACK = "stack"
NODE_TYPE = "popadvectbyvolumes"
NODE_PREFIX = "advect"
OUT_NODE = "OUT"
SLOT_KEY = "ax_slot"
MIN_ATTEN = 1e-4

# Parms not linked one to one.
_SPECIAL = ("activate", "velscale", "soppath", "vex_cwdpath")
# Path parms: resolve against the HDA, not the internal node one level deeper.
_PATH_PARMS = ("soppath", "vex_cwdpath")
_SKIP_TYPES = (hou.parmTemplateType.Folder, hou.parmTemplateType.FolderSet,
               hou.parmTemplateType.Separator, hou.parmTemplateType.Label,
               hou.parmTemplateType.Button)


def _parm(node, name):
    """The parm, or None if this instance of the HDA doesn't have it."""
    return node.parm(name) if node is not None else None


def _eval(node, name, default=None):
    p = _parm(node, name)
    return p.eval() if p is not None else default


def _stack(node):
    stack = node.node(STACK)
    if stack is None:
        raise hou.NodeError("AX POP Advect Stack: internal subnet '%s' is missing" % STACK)
    return stack


def _count(node):
    return int(_eval(node, "numadvect", 0) or 0)


def _leaf_templates(templates):
    """All non-folder parm templates of a template list, recursively."""
    for t in templates:
        if t.type() == hou.parmTemplateType.Folder:
            for sub in _leaf_templates(t.parmTemplates()):
                yield sub
        elif t.type() not in _SKIP_TYPES:
            yield t


def _blend_expr(j):
    """hscript expression: effective blend of slot j (0 when it doesn't attenuate)."""
    return ('(ch("../../velblend{j}") * (ch("../../advecttype{j}") == 1)'
            ' * (ch("../../activate{j}") != 0) * ch("../../enable{j}"))').format(j=j)


def velscale_expr(k, count):
    """hscript expression for the internal velscale of slot k out of count slots."""
    base = 'ch("../../velscale%d")' % k
    later = range(k + 1, count + 1)
    if not later:
        return base
    denom = " * ".join("max(1 - %s, %g)" % (_blend_expr(j), MIN_ATTEN) for j in later)
    return ('%s * if(ch("../../compensate") && ch("../../advecttype%d") == 1, 1 / (%s), 1)'
            % (base, k, denom))


def _link(internal, node, k):
    for t in _leaf_templates(internal.parmTemplateGroup().entries()):
        name = t.name()
        if name in _SPECIAL:
            continue
        src = node.parmTuple(name + str(k))
        dst = internal.parmTuple(name)
        if src is None or dst is None:
            continue
        # follow_parm_reference=False: on a re-sync the internal parm already
        # references the HDA parm, and following it would make that parm
        # reference itself.
        if t.type() == hou.parmTemplateType.Ramp:
            dst[0].set(src[0], follow_parm_reference=False)
            continue
        for d, s in zip(dst, src):
            d.deleteAllKeyframes()
            d.set(s, follow_parm_reference=False)

    act = internal.parm("activate")
    act.deleteAllKeyframes()
    act.setExpression('ch("../../activate%d") * ch("../../enable%d")' % (k, k),
                      hou.exprLanguage.Hscript)
    for name in _PATH_PARMS:
        p = internal.parm(name)
        if p is not None and _parm(node, name + str(k)) is not None:
            p.deleteAllKeyframes()
            p.set('`chsop("../../%s%d")`' % (name, k), follow_parm_reference=False)


def _slot_name(node, k):
    """Valid node name for slot k: the label_K parm, or advectK when it is empty."""
    label = str(_eval(node, "label_%d" % k, "") or "").strip()
    name = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if not name:
        return NODE_PREFIX + str(k)
    if name[0].isdigit():
        name = "_" + name
    return name


def _rename(node, nodes):
    """Rename the internal advects to their slot names (two-phase, so swaps don't clash)."""
    wanted = [_slot_name(node, k) for k in range(1, len(nodes) + 1)]
    if [n.name() for n in nodes] == wanted:
        return
    for i, n in enumerate(nodes):
        n.setName("__ax_tmp%d" % i, unique_name=True)
    for n, name in zip(nodes, wanted):
        n.setName(name, unique_name=True)  # duplicate labels get a number added


def sync(node):
    """Make the internal advects match the multiparm: one node per slot, wired and linked."""
    stack = _stack(node)
    count = _count(node)

    existing = {}
    for child in stack.children():
        if child.type().name() != NODE_TYPE:
            continue
        slot = child.userData(SLOT_KEY)
        if slot is None and child.name().startswith(NODE_PREFIX):
            slot = child.name()[len(NODE_PREFIX):]
        if slot is not None and slot not in existing:
            existing[slot] = child

    nodes = []
    for k in range(1, count + 1):
        n = existing.get(str(k))
        if n is None:
            n = stack.createNode(NODE_TYPE)
        n.setUserData(SLOT_KEY, str(k))
        nodes.append(n)

    keep = set(nodes)
    for child in stack.children():
        if child.type().name() == NODE_TYPE and child not in keep:
            child.destroy()

    _rename(node, nodes)

    stack_in = stack.indirectInputs()[0]
    prev = stack_in
    for n in nodes:
        n.setInput(0, prev)
        prev = n

    out = stack.node(OUT_NODE)
    if out is None:
        out = stack.createNode("null", OUT_NODE)
    out.setInput(0, prev)
    out.setDisplayFlag(True)

    for k, n in enumerate(nodes, 1):
        _link(n, node, k)
        vs = n.parm("velscale")
        vs.deleteAllKeyframes()
        vs.setExpression(velscale_expr(k, count), hou.exprLanguage.Hscript)
        eff = _parm(node, "effscale%d" % k)
        if eff is not None:
            eff.deleteAllKeyframes()
            eff.setExpression('ch("%s/%s/velscale")' % (STACK, n.name()),
                              hou.exprLanguage.Hscript)

    stack.layoutChildren()
