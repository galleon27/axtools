import hou
import os
selected = hou.selectedNodes()

for s in selected:
    children = s.children()
    for c in children:
        if c.type().name() == 'file':
            filename = c.parm('file').eval()
            path = filename.split("/Downloaded")[1]
            path = ('$MEGASCANS' + "/Downloaded" + path)
            
            c.parm('file').set(path)
            

