"""shadowed — helper local sombreia o import."""
from pkg.util import helper


def helper():
    return "local"


def use():
    return helper()
