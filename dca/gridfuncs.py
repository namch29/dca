import functools
import math

import numpy as np

from eventgen import CEvent


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class GridFuncs(metaclass=Singleton):
    """Rhombus grid with axial coordinates. All methods are static in practice
    besides reading grid dims
    """

    def __init__(self, rows, cols, n_channels):
        self.rows, self.cols, self.n_channels = rows, cols, n_channels
        # Whether or not the part of the feature representation which
        # count the number of channels in use at neighbors with
        # a distance of 4 or less should include the cell itself in the count.
        # Nominal channels for each cell
        self.countself = False

        self.labels = np.zeros((self.rows, self.cols), dtype=int)
        self._partition_cells()
        self.nom_chs_mask = np.zeros((self.rows, self.cols, self.n_channels), dtype=bool)
        self.nom_chs = np.zeros((self.rows, self.cols, 10), dtype=int)
        self._assign_chs()

    def validate_reuse_constr(self, grid):
        """
        Verify that the channel reuse constraint of 3 is not violated,
        e.g. that a channel in use in a cell is not in use in its neighbors.
        Returns True if valid not violated, False otherwise
        """
        # TODO: It might be possible to do this more efficiently.
        # If no neighbors of a cell violate the channel reuse constraint,
        # then the cell itself does not either, so it should be possible
        # to skip checking some cells.
        for r in range(self.rows):
            for c in range(self.cols):
                neighs = self.neighbors(2, r, c, separate=True)
                # Channels in use at neighbors
                inuse = np.bitwise_or.reduce(grid[neighs])
                # Channels in use at a neigh and cell
                inuse_both = np.bitwise_and(grid[r, c], inuse)
                viols = np.where(inuse_both == 1)[0]
                if len(viols) > 0:
                    self.logger.error("Channel Reuse constraint violated"
                                      f" in Cell {(r, c) }"
                                      f" at channels {viols}")
                    return False
        return True

    def _get_eligible_chs_bitmap(self, grid, cell):
        """Find eligible chs by bitwise ORing the allocation maps of neighbors"""
        r, c = cell
        neighs = self.neighbors(2, r, c, separate=True, include_self=True)
        alloc_map = np.bitwise_or.reduce(grid[neighs])
        return alloc_map

    def get_eligible_chs(self, grid, cell):
        """
        Find the channels that are free in 'cell' and all of
        its neighbors with a distance of 2 or less.
        These are the eligible channels, i.e. those that can be assigned
        without violating the reuse constraint.
        """
        alloc_map = self._get_eligible_chs_bitmap(grid, cell)
        eligible = np.nonzero(np.invert(alloc_map))[0]
        return eligible

    def get_n_eligible_chs(self, grid, cell):
        """Return the number of eligible channels"""
        alloc_map = self._get_eligible_chs_bitmap(grid, cell)
        n_eligible = np.count_nonzero(np.invert(alloc_map))
        return n_eligible

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def neighbors1sparse(row, col):
        """
        Returns a list with the indecies of neighbors within a radius of 1,
        not including self. The indecies may not be within grid boundaries.
        """
        idxs = []
        for r in range(row - 1, row + 2):
            for c in range(col - 1, col + 2):
                if not ((r, c) == (row - 1, col - 1) or (r, c) == (row + 1, col + 1) or
                        (r, c) == (row, col)):
                    idxs.append((r, c))
        return idxs

    @functools.lru_cache(maxsize=None)
    def neighbors(self, dist, row, col, separate=False, include_self=False):
        """
        Returns a list with indices of neighbors with a distance of 'dist' or less
        from the cell at (row, col)

        If 'separate' is True, return ([r1, r2, ...], [c1, c2, ...]),
        else return [(r1, c1), (r2, c2), ...]
        """

        def _hex_distance(cell_a, cell_b):
            r1, c1 = cell_a
            r2, c2 = cell_b
            return (abs(r1 - r2) + abs(r1 + c1 - r2 - c2) + abs(c1 - c2)) / 2

        if separate:
            rs = []
            cs = []
        else:
            idxs = []
        for r2 in range(self.rows):
            for c2 in range(self.cols):
                if ((include_self or (row, col) != (r2, c2)) and _hex_distance(
                        (row, col), (r2, c2)) <= dist):  # YAPF: disable
                    if separate:
                        rs.append(r2)
                        cs.append(c2)
                    else:
                        idxs.append((r2, c2))
        if separate:
            return (rs, cs)
        return idxs

    def neighbors_all_oh(self, dist=2, include_self=True):
        """
        Returns an array where each and every cell has a onehot representation of
        their neighbors
        """
        idxs = np.zeros((self.rows, self.cols, self.rows, self.cols), dtype=np.bool)
        for r1 in range(self.rows):
            for c1 in range(self.cols):
                for r2 in range(self.rows):
                    for c2 in range(self.cols):
                        if (include_self or (r1, c1) != (r2, c2)) \
                           and self.hex_distance((r1, c1), (r2, c2)) <= dist:
                            idxs[r1, c1, r2, c2] = True
        return idxs

    def _partition_cells(self):
        """
        Partition cells into 7 lots such that the minimum distance
        between cells with the same label ([0..6]) is at least 2
        (which corresponds to a minimum reuse distance of 3).

        Create an n*m array with the label for each cell.
        """

        def label(l, y, x):
            # A center and some part of its subgrid may be out of bounds.
            if (x >= 0 and x < self.cols and y >= 0 and y < self.rows):
                self.labels[y, x] = l

        centers = [(0, 0), (1, 2), (2, 4), (3, 6), (4, 8), (3, -1), (4, 1), (5, 3),
                   (6, 5), (7, 7), (-1, 5), (7, 0), (0, 7)]
        for center in centers:
            label(0, *center)
            for i, neigh in enumerate(self.neighbors1sparse(*center)):
                label(i + 1, *neigh)

    def _assign_chs(self, n_nom_channels=0):
        """
        Partition the cells and channels up to and including 'n_nom_channels'
        into 7 lots, and assign
        the channels to cells such that they will not interfere with each
        other within a channel reuse constraint of 3.
        The channels assigned to a cell are its nominal channels.

        Create a (rows*cols*n_channels) array
        where a channel for a cell has value 1 if nominal, 0 otherwise.
        """
        if n_nom_channels == 0:
            n_nom_channels = self.n_channels
        channels_per_subgrid_cell = []
        channels_per_subgrid_cell_accu = [0]
        channels_per_cell = n_nom_channels / 7
        ceil = math.ceil(channels_per_cell)
        floor = math.floor(channels_per_cell)
        tot = 0
        for i in range(7):
            if tot + ceil + (6 - i) * floor > n_nom_channels:
                tot += ceil
                cell_channels = ceil
            else:
                tot += floor
                cell_channels = floor
            channels_per_subgrid_cell.append(cell_channels)
            channels_per_subgrid_cell_accu.append(tot)
        for r in range(self.rows):
            for c in range(self.cols):
                label = self.labels[r][c]
                lo = channels_per_subgrid_cell_accu[label]
                hi = channels_per_subgrid_cell_accu[label + 1]
                self.nom_chs_mask[r][c][lo:hi] = 1
                self.nom_chs[r][c] = np.arange(lo, hi)

    @staticmethod
    def afterstates(grid, cell, ce_type, chs):
        """Make an afterstate (resulting grid) for each action in 'chs'"""
        if ce_type == CEvent.END:
            targ_val = 0
        else:
            targ_val = 1
        grids = np.repeat(np.expand_dims(np.copy(grid), axis=0), len(chs), axis=0)
        for i, ch in enumerate(chs):
            # assert grids[i][cell][ch] != targ_val
            grids[i][cell][ch] = targ_val
        return grids

    def afterstate_freps(self, grid, cell, ce_type, chs):
        """ Get the feature representation for the current grid,
        and from it derive the f.rep for each possible afterstate.
        Current assumptions:
        n_used_neighs (frep[:-1]) does include self
        n_free_self (frep[-1]) counts ELIGIBLE chs
        """
        frep = self.feature_reps(grid)[0]
        r, c = cell
        neighs4 = self.neighbors(4, r, c, separate=True, include_self=self.countself)
        neighs2 = self.neighbors(2, r, c, include_self=True)
        freps = np.repeat(np.expand_dims(frep, axis=0), len(chs), axis=0)
        if ce_type == CEvent.END:
            # One less channel will be in use
            n_used_neighs_diff = -1
            # Any ch in 'chs' was not eligible for any neigh2 of 'cell',
            # since the ch was in use at 'cell', but it MIGHT become
            # eligible if, for a given neigh2, its neighs2 or itself
            # does not use it.
            # Temporarily modify grid and check if that's the case
            n_elig_self_diff = 1
            grid[cell][chs] = 0
        else:
            # One more ch will be in use
            n_used_neighs_diff = 1
            # The number of eligible channels for the neighs2 of 'cell'
            # MIGHT decrease by 1 if the channel is currently eligible
            # for a particular neighs2.
            n_elig_self_diff = -1
        eligible_chs = [self.get_eligible_chs(grid, neigh2) for neigh2 in neighs2]
        for i, ch in enumerate(chs):
            freps[i, neighs4[0], neighs4[1], ch] += n_used_neighs_diff
            for j, neigh2 in enumerate(neighs2):
                if ch in eligible_chs[j]:
                    freps[i, neigh2[0], neigh2[1], -1] += n_elig_self_diff
        if ce_type == CEvent.END:
            grid[cell][chs] = 1  # Revert changes
        return freps

    def afterstate_freps_naive(self, grid, cell, ce_type, chs):
        astates = GridFuncs.afterstates(grid, cell, ce_type, chs)
        freps = self.feature_reps(astates)
        return freps

    def feature_reps(self, grids):
        """
        Takes a grid or an array of grids and return the feature representations.

        For each cell-channel pair, the number of times that channel is
        used by neighbors with a distance of 4 or less.

        For each cell, the number of ELIGIBLE channels in that cell.
        """
        assert type(grids) == np.ndarray
        if grids.ndim == 3:
            grids = np.expand_dims(grids, axis=0)
        freps = np.zeros(
            (len(grids), self.rows, self.cols, self.n_channels + 1), dtype=np.int16)
        # freps[:, :, :, n_channels] = n_channels \
        #     - np.count_nonzero(grids, axis=3)
        for r in range(self.rows):
            for c in range(self.cols):
                # TODO all onehot
                neighs = self.neighbors(
                    4, r, c, separate=True, include_self=self.countself)
                n_used = np.count_nonzero(grids[:, neighs[0], neighs[1]], axis=1)
                freps[:, r, c, :-1] = n_used
                for i in range(len(grids)):
                    freps[i, r, c, -1] = self.get_n_eligible_chs(grids[i], (r, c))
        return freps

    @staticmethod
    def print_cell(grid, r, c):
        print(f"Cell ({r}, {c}): {np.where(grid[r, c] == 1)}")

    def print_neighs(self, row, col):
        print(f"Cell ({row}, {col})"
              f"\nNeighs1: {self.neighbors(1, row, col)}"
              f"\nNeighs2: {self.neighbors(2, row, col)}")

    def print_neighs2_inuse(self, grid, row, col):
        """
        Show all the channels for the given cell and its neighbors
        """
        for neigh in self.neighbors(2, row, col, include_self=True):
            print(f"\n{neigh}: {np.where(grid[neigh]==1)}")


GF = GridFuncs(7, 7, 70)
