import hou


def nodeColor():
	node = kwargs["node"]
	node.setColor(hou.Color((0.145, 0.667, 0.557)))
