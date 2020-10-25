def isclose(a0, a1, tol=1.0e-4):
    """
    Check if two elements are almost equal with a tolerance

    :param a0: number to compare
    :param a1: number to compare
    :param tol: The absolute tolerance
    :return: Returns boolean value if the values are almost equal
    """
    return abs(a0 - a1) < tol
