"""Small target-independent parametric blockout mesh recipes (metres, Y up)."""
def box_mesh():
    # Separate face corners keep hard normals and repeatable per-face UVs.
    faces=[([(-1,-1,-1),(-1,-1,1),(-1,1,1),(-1,1,-1)],[-1,0,0]),
           ([(1,-1,1),(1,-1,-1),(1,1,-1),(1,1,1)],[1,0,0]),
           ([(-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1)],[0,-1,0]),
           ([(-1,1,1),(1,1,1),(1,1,-1),(-1,1,-1)],[0,1,0]),
           ([(1,-1,-1),(-1,-1,-1),(-1,1,-1),(1,1,-1)],[0,0,-1]),
           ([(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)],[0,0,1])]
    mesh={"vertices":[],"indices":[],"normals":[],"uvs":[]}
    for points,normal in faces:
        base=len(mesh["vertices"])
        mesh["vertices"].extend([[v*.5 for v in point] for point in points])
        mesh["normals"].extend([normal]*4)
        mesh["uvs"].extend([[0,0],[1,0],[1,1],[0,1]])
        mesh["indices"].extend(base+i for i in (0,1,2,0,2,3))
    return mesh
