"""core — Engine + homonymous process()."""
from pkg.dup_a import process as proc_a


class Engine:
    def run(self):
        return proc_a()


def process():
    """homonymous: pkg.core.process vs pkg.dup_a.process vs pkg.dup_b.process"""
    return "core"
