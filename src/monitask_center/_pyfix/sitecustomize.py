import platform
_orig = platform.mac_ver
def mac_ver():
    v = _orig()
    if not v[0]:
        return ("26.0", ('', '', ''), platform.machine())
    return v
platform.mac_ver = mac_ver
