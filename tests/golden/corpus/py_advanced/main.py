"""main — ambiguous refs + homonymous import picks."""
from pkg.dup_a import process
from pkg.core import Engine


def main():
    return process() + Engine().run()
