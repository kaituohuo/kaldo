import os
import numpy as np
import scipy
import scipy.special
import spglib as spg
import ballistico.atoms_helper as ath
import ballistico.constants as constants
import tensorflow as tf
from ballistico.logger import Logger
import sys
# from memory_profiler import profile
# tf.enable_eager_execution ()
EIGENVALUES_FILE = 'eigenvalues.npy'
EIGENVECTORS_FILE = 'eigenvectors.npy'
FREQUENCY_K_FILE = 'frequencies.npy'
VELOCITY_K_FILE = 'velocities.npy'
GAMMA_FILE = 'gammas.npy'

DELTA_THRESHOLD = 2
# DELTA_CORRECTION = scipy.special.erf (DELTA_THRESHOLD / np.sqrt (2))
DELTA_CORRECTION = 1

class Ballistico (object):
    def __init__(self, atoms, supercell=(1, 1, 1), kpts=(1, 1, 1), second_order=None, third_order=None, is_classic=False, temperature=300):
        # TODO: Keep only the relevant default initializations
        self.configuration = atoms
        self.replicas = np.array(supercell)
        self.k_size = np.array(kpts)
        self.is_classic = is_classic
        self.folder = 'ballistico_phonons/'
        [self.replicated_configuration, self.list_of_replicas] = \
            ath.replicate_configuration (self.configuration, self.replicas)
        self._frequencies = None
        self._velocities = None
        self._eigenvalues = None
        self._eigenvectors = None
        self._occupations = None
        self._gamma = None
        self._cell_inv = None
        self.second_order = second_order
        self.third_order = third_order
        self.temperature = temperature
        directory = os.path.dirname (self.folder)
        if not os.path.exists (directory):
            os.makedirs (directory)

    @property
    def frequencies(self):
        return self._frequencies

    @frequencies.getter
    def frequencies(self):
        if self._frequencies is None:
            # try:
            #     self._frequencies = np.load (self.folder + FREQUENCY_K_FILE)
            # except FileNotFoundError as e:
            #     print(e)
            self.calculate_second_all_grid ()
                # np.save (self.folder + FREQUENCY_K_FILE, self._frequencies)
                # np.save (self.folder + VELOCITY_K_FILE, self._velocities)
        return self._frequencies

    @frequencies.setter
    def frequencies(self, new_frequencies):
        self._frequencies = new_frequencies

    @property
    def velocities(self):
        return self._velocities

    @velocities.getter
    def velocities(self):
        if self._velocities is None:
            # try:
            #     self._velocities = np.load (self.folder + VELOCITY_K_FILE)
            # except IOError as e:
            self.calculate_second_all_grid ()
                # np.save (self.folder + VELOCITY_K_FILE, self._velocities)
                # np.save (self.folder + FREQUENCY_K_FILE, self._frequencies)
        return self._velocities

    @velocities.setter
    def velocities(self, new_velocities):
        self._velocities = new_velocities

    @property
    def eigenvalues(self):
        return self._eigenvalues

    @eigenvalues.getter
    def eigenvalues(self):
        if self._eigenvalues is None:
            # self.calculate_second_all_grid ()
            # try:
            #     self._eigenvalues = np.load (self.folder + EIGENVALUES_FILE)
            # except IOError as e:
            self.calculate_second_all_grid ()
                # np.save (self.folder + EIGENVALUES_FILE, self._eigenvalues)
        return self._eigenvalues

    @eigenvalues.setter
    def eigenvalues(self, new_eigenvalues):
        self._eigenvalues = new_eigenvalues

    @property
    def eigenvectors(self):
        return self._eigenvectors

    @eigenvectors.setter
    def eigenvectors(self, new_eigenvectors):
        self._eigenvectors = new_eigenvectors

    @eigenvectors.getter
    def eigenvectors(self):
        if self._eigenvectors is None:
            # try:
            #     self._eigenvectors = np.load (self.folder + EIGENVECTORS_FILE)
            # except IOError as e:
            self.calculate_second_all_grid ()
                # np.save (self.folder + EIGENVECTORS_FILE, self._eigenvectors)
        return self._eigenvectors

    @property
    def occupations(self):
        return self._occupations

    @occupations.getter
    def occupations(self):
        if self._occupations is None:
            self._occupations = self.calculate_occupations ().squeeze()
        return self._occupations

    @occupations.setter
    def occupations(self, new_occupations):
        self._occupations = new_occupations

    @property
    def gamma(self):
        return self._gamma

    @gamma.getter
    def gamma(self):
        if self._gamma is None:
            # try:
            #     self._gamma = np.load (self.folder + GAMMA_FILE)
            # except IOError as e:
            self._gamma = self.calculate_gamma ()
            #     np.save (self.folder + GAMMA_FILE, self._gamma)
        return np.sum(self._gamma, axis=0)

    @gamma.setter
    def gamma(self, new_gamma):
        self._gamma = new_gamma

    @property
    def cell_inv(self):
        return self._cell_inv

    @cell_inv.getter
    def cell_inv(self):
        if self._cell_inv is None:
            self._cell_inv = np.linalg.inv(self.configuration.cell)
        return self._cell_inv

    @cell_inv.setter
    def cell_inv(self, new_cell_inv):
        self._cell_inv = new_cell_inv

    def unravel_index(self, index):
        multi_index = np.unravel_index (index, self.k_size, order='F')
        return multi_index

    def ravel_multi_index(self, multi_index):
        single_index = np.ravel_multi_index (multi_index, self.k_size, order='F',  mode='wrap')
        return single_index

    def diagonalize_second_order_k(self, klist):
        frequencies = []
        eigenvals = []
        velocities = []
        eigenvects = []
        for qvec in klist:
            # TODO: The logic is a bit messy here and we can only support this for the path and not the grid
            freq, evalue, evect, vels = self.diagonalize_second_order_single_k(qvec)
            frequencies.append(freq)
            velocities.append(vels)
            eigenvects.append(evect)
            eigenvals.append(evalue)
        return np.array(frequencies), np.array(eigenvals), np.array(eigenvects), np.array(velocities)

    def diagonalize_second_order_single_k(self, qvec):
        list_of_replicas = self.list_of_replicas
        geometry = self.configuration.positions
        cell_inv = self.cell_inv
        kpoint = 2 * np.pi * (cell_inv).dot (qvec)
        n_particles = geometry.shape[0]
        n_replicas = list_of_replicas.shape[0]
        ddyn_s = np.zeros ((3, n_particles, 3, n_particles, 3)).astype (complex)
        if (qvec[0] == 0 and qvec[1] == 0 and qvec[2] == 0):
            # calculate_eigenvec = scipy.linalg.lapack.zheev
            calculate_eigenvec = np.linalg.eigh
        else:
            # calculate_eigenvec = scipy.linalg.lapack.zheev
            calculate_eigenvec = np.linalg.eigh
        second_order = self.second_order[0]
        chi_k = np.zeros (n_replicas).astype (complex)
        for id_replica in range (n_replicas):
            chi_k[id_replica] = np.exp (1j * list_of_replicas[id_replica].dot (kpoint))
        dyn_s = np.einsum('ialjb,l->iajb', second_order, chi_k)
        for id_replica in range (n_replicas):
            for alpha in range(3):
                for i_at in range (n_particles):
                    for j_at in range (n_particles):
                        for i_pol in range (3):
                            for j_pol in range (3):
                                # dxij = ath.apply_boundary(self.replicated_configuration, atoms[i_at] - \
                                # (atoms[j_at] + list_of_replicas[id_replica]))
                                dxij = list_of_replicas[id_replica]
                                prefactor = 1j * (dxij[alpha] * chi_k[id_replica])
                                ddyn_s[alpha, i_at, i_pol, j_at, j_pol] += prefactor * \
                                                                           (second_order[
                                                                               i_at, i_pol, id_replica, j_at, j_pol])
        mass = np.sqrt(self.configuration.get_masses ())
        massfactor = 2 * constants.electron_mass * constants.avogadro * 1e3
        dyn_s /= mass[:, np.newaxis, np.newaxis, np.newaxis]
        dyn_s /= mass[np.newaxis, np.newaxis, :, np.newaxis]
        dyn_s *= massfactor
        ddyn_s /= mass[np.newaxis, :, np.newaxis, np.newaxis, np.newaxis]
        ddyn_s /= mass[np.newaxis, np.newaxis, np.newaxis, :, np.newaxis]
        ddyn_s *= massfactor
        prefactor = 1 / (constants.charge_of_electron * constants.avogadro / 10) / constants.rydbergoverev * (constants.bohroverangstrom ** 2)
        dyn = prefactor * dyn_s.reshape(n_particles * 3, n_particles * 3)
        ddyn = prefactor * ddyn_s.reshape(3, n_particles * 3, n_particles * 3) / constants.bohroverangstrom
        out = calculate_eigenvec(dyn.reshape(n_particles * 3, n_particles * 3))
        eigenvals, eigenvects = out[0], out[1]
        # idx = eigenvals.argsort ()
        # eigenvals = eigenvals[idx]
        # eigenvects = eigenvects[:, idx]
        frequencies = np.abs (eigenvals) ** .5 * np.sign(eigenvals) / (np.pi * 2.)
        velocities = np.zeros ((frequencies.shape[0], 3), dtype=np.complex)
        for alpha in range (3):
            for i in range(3 * n_particles):
                vel = (eigenvects[:, i].conj ()).dot (np.matmul (ddyn[alpha, :, :], eigenvects[:, i]))
                if frequencies[i] != 0:
                    velocities[i, alpha] = vel / (2 * (2 * np.pi) * frequencies[i])
        return frequencies * constants.toTHz, eigenvals, eigenvects, velocities*constants.toTHz*constants.bohr2nm

    def density_of_states(self, delta=1):
        frequencies = self.frequencies
        k_mesh = self.k_size
        n_modes = frequencies.shape[-1]
        frequencies = frequencies.reshape ((k_mesh[0], k_mesh[1], k_mesh[2], n_modes))
        n_k_points = np.prod (self.k_size)
        # increase_factor = 3
        omega_kl = np.zeros(( n_k_points, n_modes))
        for mode in range(n_modes):
            omega_kl[:, mode] = frequencies[...,mode].flatten()
        # Energy axis and dos
        omega_e = np.linspace (0., np.amax (omega_kl) + 5e-3, num=100)
        dos_e = np.zeros_like (omega_e)
        # Sum up contribution from all q-points and branches
        for omega_l in omega_kl:
            diff_el = (omega_e[:, np.newaxis] - omega_l[np.newaxis, :]) ** 2
            dos_el = 1. / (diff_el + (0.5 * delta) ** 2)
            dos_e += dos_el.sum (axis=1)
        dos_e *= 1. / (n_k_points * np.pi) * 0.5 * delta
        return omega_e, dos_e
    
    def calculate_second_all_grid(self):
        n_k_points = np.prod(self.k_size)
        n_unit_cell = self.second_order.shape[1]
        frequencies = np.zeros((n_k_points, n_unit_cell * 3))
        eigenvalues = np.zeros((n_k_points, n_unit_cell * 3))
        eigenvectors = np.zeros((n_k_points, n_unit_cell * 3, n_unit_cell * 3)).astype(np.complex)
        velocities = np.zeros((n_k_points, n_unit_cell * 3, 3))
        for index_k in range(np.prod(self.k_size)):
            k_point = self.unravel_index(index_k)
            freq, eval, evect, vels = self.diagonalize_second_order_single_k (k_point / self.k_size)
            frequencies[index_k, :] = freq
            eigenvalues[index_k, :] = eval
            eigenvectors[index_k, :, :] = evect
            velocities[index_k, :, :] = vels.real
        self._frequencies = frequencies
        self._eigenvalues = eigenvalues
        # self._velocities = np.flip(velocities, axis=2)
        self._velocities = velocities
        self._eigenvectors = eigenvectors
    
    def calculate_occupations(self):
        temp = self.temperature
        omega = 2 * np.pi * self.frequencies
        eigenvalues = omega * constants.hbar / constants.k_b
        density = np.zeros_like(eigenvalues)
        if self.is_classic == False:
            density[omega != 0] = 1. / (
                    np.exp (constants.hbar * omega[omega != 0] / constants.k_b / self.temperature) - 1.)
        else:
            density[omega != 0] = temp / omega[omega != 0] / constants.hbar * constants.k_b
        return density

    def gaussian_delta(self, params):
        # alpha is a factor that tells whats the ration between the width of the gaussian and the width of allowed phase space
        delta_energy = params[0]
        # allowing processes with width sigma and creating a gaussian with width sigma/2 we include 95% (erf(2/sqrt(2)) of the probability of scattering. The erf makes the total area 1
        sigma = params[1]
        # correction = scipy.special.erf(DELTA_THRESHOLD / np.sqrt(2))
        correction = 1
        return 1 / np.sqrt (2 * np.pi * sigma ** 2) * np.exp (- delta_energy ** 2 / (2 * sigma ** 2)) / correction
    
    
    def triangular_delta(self, params):
        deltaa = np.abs (params[0])
        domega = params[1]
        return 1. / domega * (1 - deltaa / domega)

    # @profile
    def calculate_gamma(self, sigma_in=None):
        prefactor = 1e-3 / (
                4. * np.pi) ** 3 * constants.avogadro ** 3 * constants.charge_of_electron ** 2 * constants.hbar
        coeff = 1000 * constants.hbar / constants.charge_of_electron

        # TODO: remove this when done debugging
        nup = tf.placeholder ('int64', (None), name='nup')
        nupp = tf.placeholder ('int64', (None), name='nupp')
        index_kp = tf.placeholder ('int64', (None), name='index_kp')
        index_kpp = tf.placeholder ('int64', (None), name='index_kpp')
        second_eigenv = tf.placeholder ('complex64', (None, None), name='second_eigenv')
        third_eigenv = tf.placeholder ('complex64', (None, None), name='third_eigenv')
        potential = tf.placeholder ('complex64', (None, None, None, None), name='potential')
        second_chi = tf.placeholder ('complex64', (None, None), name='second_chi')
        third_chi = tf.placeholder ('complex64', (None, None), name='third_chi')
        sigma = tf.placeholder ('float64', (None, None), name='sigma')
        density_fact = tf.placeholder ('float64', (None, None), name='density')
        freq_product = tf.placeholder ('float64', (None, None), name='freq_product')
        freq_diff = tf.placeholder ('float64', (None, None), name='freq_diff')
        coords = tf.stack ((nup, nupp), axis=-1)
        sparsify = lambda operator: tf.cast (tf.gather_nd (operator, coords), tf.float64)
        dirac_delta = sparsify (density_fact) / sparsify (freq_product)
        if sigma_in == None:
            sigma_to_plug = sparsify (sigma)
        else:
            sigma_to_plug = sigma_in
        dirac_delta *= tf.exp (- sparsify (freq_diff) ** 2 / (2 * sigma_to_plug ** 2)) \
                       / (sigma_to_plug * np.sqrt (2 * np.pi)) / DELTA_CORRECTION
        second = tf.gather (second_eigenv, nup, axis=0)
        third = tf.gather (third_eigenv, nupp, axis=0)
        third_chi_tf = tf.gather (third_chi, index_kpp, axis=0)
        second_chi_tf = tf.gather (second_chi, index_kp, axis=0)
        second_chi_tf.set_shape ((None, None))
        third_chi_tf.set_shape ((None, None))
        second.set_shape ((None, None))
        third.set_shape ((None, None))
        # potential_proj_tf = tf.einsum \
        #     ('litj,al,at,aj,ai->a', potential, second_chi_tf, third_chi_tf, third, second)
        potential_proj_tf = tf.tensordot(potential, second_chi_tf, (0, 1))
        potential_full_proj_tf = tf.einsum('itja,at,aj,ai->a', potential_proj_tf, third_chi_tf, third, second)
        
        phase_space_tf = tf.reduce_sum (dirac_delta)
        gamma_tf = tf.reduce_sum (tf.cast (tf.abs (potential_full_proj_tf) ** 2, \
                                           tf.float64) * dirac_delta)
        Logger().info ('Lifetime calculation')
        nptk = np.prod (self.k_size)
        n_particles = self.configuration.positions.shape[0]
        n_modes = n_particles * 3
        ps = np.zeros ((2, np.prod (self.k_size), n_modes))
        # TODO: remove acoustic sum rule
        self.frequencies[0, :3] = 0
        self.velocities[0, :3, :] = 0
        cellinv = self.cell_inv
        masses = self.configuration.get_masses ()
        list_of_replicas = self.list_of_replicas
        n_modes = n_particles * 3
        k_size = self.k_size
        n_replicas = list_of_replicas.shape[0]
        rlattvec = cellinv * 2 * np.pi
        chi = np.zeros ((nptk, n_replicas), dtype=np.complex)
        for index_k in range (np.prod (k_size)):
            i_k = np.array (self.unravel_index (index_k))
            k_point = i_k / k_size
            realq = np.matmul (rlattvec, k_point)
            for l in range (n_replicas):
                chi[index_k, l] = np.exp (1j * list_of_replicas[l].dot (realq))
        scaled_potential = self.third_order[0] / np.sqrt \
            (masses[:, np.newaxis, np.newaxis, np.newaxis, np.newaxis, \
             np.newaxis, np.newaxis, np.newaxis])
        scaled_potential /= np.sqrt (masses[np.newaxis, np.newaxis, \
                                     np.newaxis, :, np.newaxis, np.newaxis, np.newaxis, np.newaxis])
        scaled_potential /= np.sqrt (masses[np.newaxis, np.newaxis, \
                                     np.newaxis, np.newaxis, np.newaxis, np.newaxis, :, np.newaxis])
        scaled_potential = scaled_potential.reshape (n_modes, n_replicas, n_modes, n_replicas, n_modes)
        Logger().info ('Projection started')
        gamma = np.zeros ((2, nptk, n_modes))
        n_particles = self.configuration.positions.shape[0]
        n_modes = n_particles * 3
        k_size = self.k_size
        nptk = np.prod (k_size)
        density = self.calculate_occupations()
        freq_product_np = (self.frequencies[:, :, np.newaxis, np.newaxis] * \
                          self.frequencies[np.newaxis, np.newaxis, :, :])
        freq_product_tf = freq_product_np.reshape (nptk * n_modes, nptk * n_modes)
        if sigma_in is None:
            sigma_tensor_np = self.calculate_broadening ( \
                self.velocities[:, :, np.newaxis, np.newaxis, :] - \
                self.velocities[np.newaxis, np.newaxis, :, :, :])
            sigma_tensor = sigma_tensor_np
            sigma_tf = sigma_tensor_np.reshape(nptk * n_modes,
                                            nptk * n_modes)
        mapping, grid = spg.get_ir_reciprocal_mesh (self.k_size,
                                                    self.configuration,
                                                    is_shift=[0, 0, 0])
        unique_points, degeneracy = np.unique (mapping, return_counts=True)
        list_of_k = unique_points
        Logger().info ('n_irreducible_q_points = ' + str(int(len(unique_points))) + ' : ' + str(unique_points))
        third_eigenv_np = self.eigenvectors.conj ()
        third_chi_tf = chi.conj ()
        third_eigenv_tf = third_eigenv_np.swapaxes (1, 2).reshape ( \
            third_eigenv_np.shape[0] * third_eigenv_np.shape[1], third_eigenv_np.shape[2])
        for is_plus in (1, 0):
            if is_plus:
                Logger().info ('\nCreation processes')
                density_fact_np = density[:, :, np.newaxis, np.newaxis] - density[np.newaxis, np.newaxis, :, :]
                second_eigenv_np = self.eigenvectors
                second_chi_tf = chi
            else:
                Logger().info ('\nAnnihilation processes')
                density_fact_np = .5 * (1 + density[:, :, np.newaxis, np.newaxis] + density[np.newaxis, np.newaxis, :, :])
                second_eigenv_np = self.eigenvectors.conj ()
                second_chi_tf = chi.conj ()
            density_fact_tf = density_fact_np.reshape (nptk * n_modes, nptk * n_modes)

            second_eigenv_tf = second_eigenv_np.swapaxes (1, 2).reshape (
                second_eigenv_np.shape[0] * second_eigenv_np.shape[1], second_eigenv_np.shape[2])
            for index_k in (list_of_k):
                i_k = np.array (self.unravel_index (index_k))
                for mu in range (n_modes):
                    # TODO: add a threshold instead of 0
                    if self.frequencies[index_k, mu] != 0:
                        first = self.eigenvectors[index_k, :, mu]
                        # TODO: replace this with a dot
                        projected_potential = np.einsum ('wlitj,w->litj', scaled_potential, first, optimize='greedy')
                        if is_plus:
                            freq_diff_np = np.abs (
                                self.frequencies[index_k, mu] + self.frequencies[:, :, np.newaxis, np.newaxis] - self.frequencies[np.newaxis, np.newaxis,
                                                                                                                 :, :])
                        else:
                            freq_diff_np = np.abs (
                                self.frequencies[index_k, mu] - self.frequencies[:, :, np.newaxis, np.newaxis] - self.frequencies[np.newaxis, np.newaxis,
                                                                                                                 :, :])
                        freq_diff_tf = freq_diff_np.reshape (nptk * n_modes, nptk * n_modes)
                        index_kp_vec = np.arange (np.prod (self.k_size))
                        i_kp_vec = np.array (self.unravel_index (index_kp_vec))
                        i_kpp_vec = i_k[:, np.newaxis] + (int (is_plus) * 2 - 1) * i_kp_vec[:, :]
                        index_kpp_vec = self.ravel_multi_index (i_kpp_vec)
                        delta_freq = freq_diff_np[index_kp_vec, :, index_kpp_vec, :]
                        if sigma_in is None:
                            sigma_small = sigma_tensor_np[index_kp_vec, :, index_kpp_vec, :]
                        else:
                            sigma_small = sigma_in
                        condition = (delta_freq < DELTA_THRESHOLD * sigma_small) & (
                                self.frequencies[index_kp_vec, :, np.newaxis] != 0) & (self.frequencies[index_kpp_vec, np.newaxis, :] != 0)
                        interactions = np.array (np.where (condition)).T
                        # interactions = np.array(np.unravel_index (np.flatnonzero (condition), condition.shape)).T
                        if interactions.size != 0:
                            print ('interactions: ', index_k, interactions.size)
                            index_kp_vec = interactions[:, 0]
                            index_kpp_vec = index_kpp_vec[index_kp_vec]
                            mup_vec = interactions[:, 1]
                            mupp_vec = interactions[:, 2]

                            dirac_delta = density_fact_np[index_kp_vec, mup_vec, index_kpp_vec, mupp_vec]

                            dirac_delta /= freq_product_np[index_kp_vec, mup_vec, index_kpp_vec, mupp_vec]
                            if sigma_in is None:
                                gaussian = self.gaussian_delta ([freq_diff_np[index_kp_vec, mup_vec, index_kpp_vec, mupp_vec], sigma_tensor[index_kp_vec, mup_vec, index_kpp_vec, mupp_vec]])

                            else:
                                gaussian = self.gaussian_delta ([freq_diff_np[index_kp_vec, mup_vec, index_kpp_vec, mupp_vec], sigma])

                            dirac_delta *= gaussian

                            ps[is_plus, index_k, mu] += np.sum (dirac_delta)

                            third = third_eigenv_np[index_kpp_vec, :, mupp_vec]
                            second = second_eigenv_np[index_kp_vec, :, mup_vec]

                            projected_potential = np.einsum ('litj,al,at,aj,ai->a', projected_potential,
                                                             second_chi_tf[index_kp_vec], third_chi_tf[index_kpp_vec], third,
                                                             second, optimize='greedy')

                            gamma[is_plus, index_k, mu] += np.sum (np.abs (projected_potential) ** 2 * dirac_delta)
                        #     index_kp_vec = interactions[:, 0]
                        #     index_kpp_vec = index_kpp_vec[index_kp_vec]
                        #     mup_vec = interactions[:, 1]
                        #     mupp_vec = interactions[:, 2]
                        #     nup_vec = np.ravel_multi_index (np.array ([index_kp_vec, mup_vec]),
                        #                                     np.array ([np.prod (self.k_size), n_modes]), order='C')
                        #     nupp_vec = np.ravel_multi_index (np.array ([index_kpp_vec, mupp_vec]),
                        #                                      np.array ([np.prod (self.k_size), n_modes]), order='C')
                        #     with tf.Session () as sess:
                        #         tf.summary.FileWriter (
                        #             "tensorboard/",
                        #             sess.graph)
                        #         feed_dict = {
                        #             nup: nup_vec,
                        #             nupp: nupp_vec,
                        #             index_kp: index_kp_vec,
                        #             index_kpp: index_kpp_vec,
                        #             second_eigenv: second_eigenv_tf,
                        #             third_eigenv: third_eigenv_tf,
                        #             potential: projected_potential,
                        #             second_chi: second_chi_tf,
                        #             third_chi: third_chi_tf,
                        #             density_fact: density_fact_tf,
                        #             freq_product: freq_product_tf,
                        #             freq_diff: freq_diff_tf}
                        #         if sigma_in == None:
                        #             feed_dict[sigma] = sigma_tf
                        #         gamma_value = sess.run (gamma_tf, feed_dict=feed_dict)
                        #     gamma[is_plus, index_k, mu] = gamma_value
                            
                        gamma[is_plus, index_k, mu] /= self.frequencies[index_k, mu]
                        ps[is_plus, index_k, mu] /= self.frequencies[index_k, mu]
                        Logger().info ('q-point   = ' + str(index_k))
                        Logger().info ('mu-branch = ' + str(mu))


        for index_k, (associated_index, gp) in enumerate (zip (mapping, grid)):
            ps[:, index_k, :] = ps[:, associated_index, :]
            gamma[:, index_k, :] = gamma[:, associated_index, :]

        gamma = gamma * prefactor / nptk
        ps = ps / nptk / (2 * np.pi) ** 3
        return gamma
    
    # @profile
    def calculate_broadening(self, velocity):
        cellinv = self.cell_inv
        rlattvec = cellinv * 2 * np.pi

        # we want the last index of velocity (the coordinate index to dot from the right to rlattice vec
        # 10 = armstrong to nanometers
        base_sigma = ((np.tensordot (velocity * 10., rlattvec / self.k_size, [-1, 1])) ** 2).sum (axis=-1)
        base_sigma = np.sqrt (base_sigma / 6.)
        return base_sigma / (2 * np.pi)