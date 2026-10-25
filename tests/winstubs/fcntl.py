"""Stand-in for the Unix-only fcntl module, so HA's test harness imports on Windows.

Only used for running the tests on Windows (PYTHONPATH=tests/winstubs); HA itself
runs on Linux.
"""
LOCK_EX = LOCK_NB = LOCK_SH = LOCK_UN = 0


def flock(fd, operation):
    return None


def fcntl(fd, cmd, arg=0):
    return 0


def ioctl(fd, request, arg=0, mutate_flag=True):
    return 0
