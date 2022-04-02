def force_scipy_import():
    import numpy as np
    from scipy.spatial import Voronoi
    Voronoi(np.array([[0, 0], [0, 1], [1, 0], [1, 1]]))

def force_imports():
    force_scipy_import()