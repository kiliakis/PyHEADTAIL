'''
Created on 06.01.2014

@author: Kevin Li
'''


import numpy as np


import copy, h5py, sys
from scipy.constants import c, e, epsilon_0, m_e, m_p, pi

from beams.slices import *
from beams.matching import match_transverse, match_longitudinal, unmatched_inbucket
from solvers.poissonfft import *


# re = 1 / (4 * pi * epsilon_0) * e ** 2 / c ** 2 / m_e
# rp = 1 / (4 * pi * epsilon_0) * e ** 2 / c ** 2 / m_p


class Beam(object):

    def __init__(self, n_macroparticles, charge, gamma, intensity, mass,
                 alpha_x, beta_x, epsn_x, alpha_y, beta_y, epsn_y, sigma_z, sigma_dp,
                 distribution='gauss'):

        if distribution == 'empty':
            _create_empty(n_macroparticles)
        elif distribution == 'gauss':
            _creat_gauss(n_macroparticles)
        elif distribution == "uniform":
            _create_uniform(n_macroparticles)

        self.id = np.arange(1, n_macroparticles + 1, dtype=int)

        _set_beam_quality(charge, gamma, intensity, mass)
        _set_beam_geometry(alpha_x, beta_x, epsn_x, alpha_y, beta_y, epsn_y, sigma_z, sigma_dp)

        self.x0 = self.x.copy()
        self.xp0 = self.xp.copy()
        self.y0 = self.y.copy()
        self.yp0 = self.yp.copy()
        self.z0 = self.z.copy()
        self.dp0 = self.dp.copy()

    def _create_empty(self, n_macroparticles):

        self.x = np.zeros(n_macroparticles)
        self.xp = np.zeros(n_macroparticles)
        self.y = np.zeros(n_macroparticles)
        self.yp = np.zeros(n_macroparticles)
        self.z = np.zeros(n_macroparticles)
        self.dp = np.zeros(n_macroparticles)

    def _create_gauss(self, n_macroparticles):

        self.x = np.random.randn(n_macroparticles)
        self.xp = np.random.randn(n_macroparticles)
        self.y = np.random.randn(n_macroparticles)
        self.yp = np.random.randn(n_macroparticles)
        self.z = np.random.randn(n_macroparticles)
        self.dp = np.random.randn(n_macroparticles)

    def _create_uniform(self, n_macroparticles):

        self.x = 2 * np.random.rand(n_macroparticles) - 1
        self.xp = 2 * np.random.rand(n_macroparticles) - 1
        self.y = 2 * np.random.rand(n_macroparticles) - 1
        self.yp = 2 * np.random.rand(n_macroparticles) - 1
        self.z = 2 * np.random.rand(n_macroparticles) - 1
        self.dp = 2 * np.random.rand(n_macroparticles) - 1

    def _set_beam_quality(self, charge, gamma, intensity, mass):

        self.charge = charge
        self.gamma = gamma
        self.intensity = intensity
        self.mass = mass

    def _set_beam_geometry(self, alpha_x, beta_x, epsn_x, alpha_y, beta_y, epsn_y, sigma_z, sigma_dp,
                           distribution='gauss'): pass

    @property
    def n_macroparticles(self):

        return len(self.x)

    @property
    def beta(self):

        return np.sqrt(1 - 1 / self.gamma ** 2)

    @property
    def p0(self):

        return self.mass * self.gamma * self.beta * c

    def reinit():

        np.copyto(self.x, self.x0)
        np.copyto(self.xp, self.xp0)
        np.copyto(self.y, self.y0)
        np.copyto(self.yp, self.yp0)
        np.copyto(self.z, self.z0)
        np.copyto(self.dp, self.dp0)

    #~ @profile
    def sort_particles(self):
        # update the number of lost particles
        self.n_macroparticles_lost = (self.n_macroparticles - np.count_nonzero(self.id))

        # sort particles according to dz (this is needed for correct functioning of bunch.compute_statistics)
        if self.n_macroparticles_lost:
            dz_argsorted = np.lexsort((self.dz, -np.sign(self.id))) # place lost particles at the end of the array
        else:
            dz_argsorted = np.argsort(self.dz)

        self.x = self.x.take(dz_argsorted)
        self.xp = self.xp.take(dz_argsorted)
        self.y = self.y.take(dz_argsorted)
        self.yp = self.yp.take(dz_argsorted)
        self.dz = self.dz.take(dz_argsorted)
        self.dp = self.dp.take(dz_argsorted)
        self.id = self.id.take(dz_argsorted)


from random import sample
import cobra_functions.stats as cp


class Slices(object):
    '''
    classdocs
    '''

    def __init__(self, n_slices, nsigmaz=None, mode='const_space', z_cuts=None):
        '''
        Constructor
        '''
        self.nsigmaz = nsigmaz
        self.mode = mode

        self.mean_x = np.zeros(n_slices)
        self.mean_xp = np.zeros(n_slices)
        self.mean_y = np.zeros(n_slices)
        self.mean_yp = np.zeros(n_slices)
        self.mean_dz = np.zeros(n_slices)
        self.mean_dp = np.zeros(n_slices)
        self.sigma_x = np.zeros(n_slices)
        self.sigma_y = np.zeros(n_slices)
        self.sigma_dz = np.zeros(n_slices)
        self.sigma_dp = np.zeros(n_slices)
        self.epsn_x = np.zeros(n_slices)
        self.epsn_y = np.zeros(n_slices)
        self.epsn_z = np.zeros(n_slices)

        self.n_macroparticles = np.zeros(n_slices, dtype=int)
        self.z_bins = np.zeros(n_slices + 1)
        self.static_slices = False

        if z_cuts:
            self.z_cut_tail, self.z_cut_head = z_cuts
            self.z_bins = np.linspace(self.z_cut_tail, self.z_cut_head, self.n_slices + 1)
            self.z_centers = self.z_bins[:-1] + (self.z_bins[1:] - self.z_bins[:-1]) / 2.
            self.static_slices = True

    @property
    def n_slices(self):

        return len(self.mean_x)

    def _set_longitudinal_cuts(self, bunch):

        if self.nsigmaz == None:
            z_cut_tail = bunch.dz[0]
            z_cut_head = bunch.dz[-1 - bunch.n_macroparticles_lost]
        else:
            mean_z = cp.mean(bunch.dz[:bunch.n_macroparticles - bunch.n_macroparticles_lost])
            sigma_z = cp.std(bunch.dz[:bunch.n_macroparticles - bunch.n_macroparticles_lost])
            z_cut_tail = mean_z - self.nsigmaz * sigma_z
            z_cut_head = mean_z + self.nsigmaz * sigma_z

        return z_cut_tail, z_cut_head

    # @profile
    def _slice_constant_space(self, bunch):

        # sort particles according to dz (this is needed for correct functioning of bunch.compute_statistics)
        bunch.sort_particles()

        # 1. z-bins
        if not self.static_slices:
            self.z_cut_tail, self.z_cut_head = self._set_longitudinal_cuts(bunch)
            self.z_bins = np.linspace(self.z_cut_tail, self.z_cut_head, self.n_slices + 1) # more robust than arange, to reach z_cut_head exactly
            self.z_centers = self.z_bins[:-1] + (self.z_bins[1:] - self.z_bins[:-1]) / 2.

        n_macroparticles_alive = bunch.n_macroparticles - bunch.n_macroparticles_lost
        self.n_cut_tail = +np.searchsorted(bunch.dz[:n_macroparticles_alive], self.z_cut_tail)
        self.n_cut_head = -np.searchsorted(bunch.dz[:n_macroparticles_alive], self.z_cut_head) + n_macroparticles_alive

        # 2. n_macroparticles
        z_bins_all = np.hstack((bunch.dz[0], self.z_bins, bunch.dz[n_macroparticles_alive - 1]))
        first_index_in_bin = np.searchsorted(bunch.dz[:n_macroparticles_alive], z_bins_all)
        if (self.z_bins[-1] in bunch.dz[:n_macroparticles_alive]): first_index_in_bin[-1] += 1
        self.z_index = first_index_in_bin[1:-1]

        # first_index_in_bin = np.searchsorted(bunch.dz[:n_macroparticles_alive], self.z_bins)
        # self.z_index = first_index_in_bin

        # self.n_macroparticles = np.diff(first_index_in_bin)
        # print self.n_macroparticles

        self.n_macroparticles = np.diff(first_index_in_bin)[1:-1]

        # .in_slice indicates in which slice the particle is (needed for wakefields)
        # bunch.set_in_slice(index_after_bin_edges)

    def _slice_constant_charge(self, bunch):

        # sort particles according to dz (this is needed for correct functioning of bunch.compute_statistics)
        bunch.sort_particles()

        if not self.static_slices:
            self.z_cut_tail, self.z_cut_head = self._set_longitudinal_cuts(bunch)

        n_macroparticles_alive = bunch.n_macroparticles - bunch.n_macroparticles_lost
        self.n_cut_tail = +np.searchsorted(bunch.dz[:n_macroparticles_alive], self.z_cut_tail)
        self.n_cut_head = -np.searchsorted(bunch.dz[:n_macroparticles_alive], self.z_cut_head) + n_macroparticles_alive

        # 1. n_macroparticles - distribute macroparticles uniformly along slices
        q0 = n_macroparticles_alive - self.n_cut_tail - self.n_cut_head
        ix = sample(range(self.n_slices), q0 % self.n_slices)
        self.n_macroparticles[:] = q0 // self.n_slices
        self.n_macroparticles[ix] += 1

        # 2. z-bins
        # Get indices of the particles defining the bin edges
        n_macroparticles_all = np.hstack((self.n_cut_tail, self.n_macroparticles, self.n_cut_head))
        first_index_in_bin = np.cumsum(n_macroparticles_all)
        self.z_index = first_index_in_bin[:-1]

        self.z_bins = (bunch.dz[self.z_index - 1] + bunch.dz[self.z_index]) / 2.
        self.z_bins[0], self.z_bins[-1] = self.z_cut_tail, self.z_cut_head
        self.z_centers = (self.z_bins[:-1] + self.z_bins[1:]) / 2.
        # # self.z_centers = map((lambda i: cp.mean(bunch.dz[first_index_in_bin[i]:first_index_in_bin[i+1]])), np.arange(self.n_slices)

    def update_slices(self, bunch):

        if self.mode == 'const_charge':
            self._slice_constant_charge(bunch)
        elif self.mode == 'const_space':
            self._slice_constant_space(bunch)

    # @profile
    def compute_statistics(self, bunch):

        index = self.n_cut_tail + np.cumsum(np.append(0, self.n_macroparticles))

        # # determine the start and end indices of each slices
        # i1 = np.append(np.cumsum(self.slices.n_macroparticles[:-2]), np.cumsum(self.slices.n_macroparticles[-2:]))
        # i0 = np.zeros(len(i1), dtype=np.int)
        # i0[1:] = i1[:-1]
        # i0[-2] = 0

        for i in xrange(self.n_slices):
            x = bunch.x[index[i]:index[i + 1]]
            xp = bunch.xp[index[i]:index[i + 1]]
            y = bunch.y[index[i]:index[i + 1]]
            yp = bunch.yp[index[i]:index[i + 1]]
            z = bunch.z[index[i]:index[i + 1]]
            dp = bunch.dp[index[i]:index[i + 1]]

            self.mean_x[i] = cp.mean(x)
            self.mean_xp[i] = cp.mean(xp)
            self.mean_y[i] = cp.mean(y)
            self.mean_yp[i] = cp.mean(yp)
            self.mean_dz[i] = cp.mean(z)
            self.mean_dp[i] = cp.mean(dp)

            self.sigma_x[i] = cp.std(x)
            self.sigma_y[i] = cp.std(y)
            self.sigma_dz[i] = cp.std(z)
            self.sigma_dp[i] = cp.std(dp)

            self.epsn_x[i] = cp.emittance(x, xp) * bunch.gamma * bunch.beta * 1e6
            self.epsn_y[i] = cp.emittance(y, yp) * bunch.gamma * bunch.beta * 1e6
            self.epsn_z[i] = 4 * np.pi * self.sigma_dz[i] * self.sigma_dp[i] * bunch.p0 / e

    # def sort_particles(self, bunch):

    #     # update the number of lost particles
    #     bunch.n_macroparticles_lost = (bunch.n_macroparticles - np.count_nonzero(bunch.id))

    #     # sort particles according to dz (this is needed for correct functioning of bunch.compute_statistics)
    #     if bunch.n_macroparticles_lost:
    #         dz_argsorted = np.lexsort((bunch.dz, -np.sign(bunch.id))) # place lost particles at the end of the array
    #     else:
    #         dz_argsorted = np.argsort(bunch.dz)

    #     bunch.x = bunch.x.take(dz_argsorted)
    #     bunch.xp = bunch.xp.take(dz_argsorted)
    #     bunch.y = bunch.y.take(dz_argsorted)
    #     bunch.yp = bunch.yp.take(dz_argsorted)
    #     bunch.dz = bunch.dz.take(dz_argsorted)
    #     bunch.dp = bunch.dp.take(dz_argsorted)
    #     bunch.id = bunch.id.take(dz_argsorted)

    # def set_in_slice(self, index_after_bin_edges):

    #     self.in_slice = (self.slices.n_slices + 3) * np.ones(self.n_macroparticles, dtype=np.int)

    #     for i in xrange(self.slices.n_slices + 2):
    #         self.in_slice[index_after_bin_edges[i]:index_after_bin_edges[i+1]] = i
