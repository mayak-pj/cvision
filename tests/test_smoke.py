def test_package_imports():
    import photo_renamer

    assert photo_renamer.__version__


def test_dependencies_available():
    import numpy
    import PIL
    from scipy.optimize import linear_sum_assignment

    assert numpy and PIL and linear_sum_assignment
