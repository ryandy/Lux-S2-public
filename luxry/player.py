import sys

from .util import log


class Player:
    def __init__(self, board, player_id, water, metal, factory_strains):
        self.board = board
        self.id = 0 if (player_id == 0 or player_id == 'player_0') else 1
        self.water = water
        self.metal = metal
        self.strains = set(factory_strains)
        self.lichen_disconnected_cells = None

    def factories(self):
        factories = [x for x in self.board.factories.values() if x.player_id == self.id]
        return sorted(factories, key=lambda x: x.id)

    def units(self):
        units = [x for x in self.board.units.values() if x.player_id == self.id]
        return sorted(units, key=lambda x: x.id)
