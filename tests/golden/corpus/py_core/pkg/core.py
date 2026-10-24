from pkg.util import helper as hlp
from pkg.util import Base


class Engine(Base):
    @staticmethod
    def make():
        return Engine()

    def run(self):
        return hlp() + missing_fn()


def outer():
    def inner():
        return hlp()

    return inner
