"""rel — relative imports reais."""
from . import util
from .util import helper as h2


def use():
    return util.helper() + h2()
