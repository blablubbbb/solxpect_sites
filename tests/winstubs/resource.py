"""Stand-in for the Unix-only resource module (see fcntl.py)."""
RLIMIT_NOFILE = 7
RLIM_INFINITY = -1


def getrlimit(resource):
    return (8192, 8192)


def setrlimit(resource, limits):
    return None
