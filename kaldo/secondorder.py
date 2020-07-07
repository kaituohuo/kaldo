from kaldo.forceconstant import ForceConstant
from opt_einsum import contract
from ase import Atoms
import os
import ase.io
import numpy as np
from kaldo.interface.eskm_io import import_from_files
import kaldo.interface.shengbte_io as shengbte_io
import ase.units as units
from kaldo.helpers.tools import timeit
from kaldo.grid import wrap_coordinates
from scipy.linalg.lapack import dsyev
from kaldo.forceconstant import chi
from kaldo.helpers.logger import get_logger
logging = get_logger()

SECOND_ORDER_FILE = 'second.npy'




def acoustic_sum_rule(dynmat):
    n_unit = dynmat[0].shape[0]
    sumrulecorr = 0.
    for i in range(n_unit):
        off_diag_sum = np.sum(dynmat[0, i, :, :, :, :], axis=(-2, -3))
        dynmat[0, i, :, 0, i, :] -= off_diag_sum
        sumrulecorr += np.sum(off_diag_sum)
    logging.info('error sum rule: ' + str(sumrulecorr))
    return dynmat


class SecondOrder(ForceConstant):
    def __init__(self, atoms, replicated_positions, supercell=None, force_constant=None, is_acoustic_sum=False):
        if is_acoustic_sum:
            force_constant = acoustic_sum_rule(force_constant)
        ForceConstant.__init__(self, atoms, replicated_positions, supercell, force_constant)
        self.n_modes = self.atoms.positions.shape[0] * 3
        self._list_of_replicas = None


    @classmethod
    def from_supercell(cls, atoms, grid_type, supercell=None, force_constant=None, is_acoustic_sum=False):
        if force_constant is not None and is_acoustic_sum is not None:
            force_constant = acoustic_sum_rule(force_constant)
        ifc = super(SecondOrder, cls).from_supercell(atoms, supercell, grid_type, force_constant)
        return ifc


    def dynmat(self, mass):
        dynmat = self.value
        dynmat = contract('mialjb,i,j->mialjb', dynmat, 1 / np.sqrt(mass), 1 / np.sqrt(mass))
        evtotenjovermol = units.mol / (10 * units.J)
        return dynmat * evtotenjovermol


    @classmethod
    def load(cls, folder, supercell=(1, 1, 1), format='eskm', is_acoustic_sum=False):
        if format == 'numpy':
            if folder[-1] != '/':
                folder = folder + '/'
            replicated_atoms_file = 'replicated_atoms.xyz'
            config_file = folder + replicated_atoms_file
            replicated_atoms = ase.io.read(config_file, format='extxyz')

            n_replicas = np.prod(supercell)
            n_total_atoms = replicated_atoms.positions.shape[0]
            n_unit_atoms = int(n_total_atoms / n_replicas)
            unit_symbols = []
            unit_positions = []
            for i in range(n_unit_atoms):
                unit_symbols.append(replicated_atoms.get_chemical_symbols()[i])
                unit_positions.append(replicated_atoms.positions[i])
            unit_cell = replicated_atoms.cell / supercell

            atoms = Atoms(unit_symbols,
                          positions=unit_positions,
                          cell=unit_cell,
                          pbc=[1, 1, 1])

            _second_order = np.load(folder + SECOND_ORDER_FILE)
            second_order = SecondOrder(atoms, replicated_atoms.positions, supercell, _second_order,
                                                         is_acoustic_sum=is_acoustic_sum)

        elif format == 'eskm':
            config_file = str(folder) + "/CONFIG"
            dynmat_file = str(folder) + "/Dyn.form"

            replicated_atoms = ase.io.read(config_file, format='dlp4')
            n_replicas = np.prod(supercell)
            n_total_atoms = replicated_atoms.positions.shape[0]
            n_unit_atoms = int(n_total_atoms / n_replicas)
            unit_symbols = []
            unit_positions = []
            for i in range(n_unit_atoms):
                unit_symbols.append(replicated_atoms.get_chemical_symbols()[i])
                unit_positions.append(replicated_atoms.positions[i])
            unit_cell = replicated_atoms.cell / supercell

            atoms = Atoms(unit_symbols,
                          positions=unit_positions,
                          cell=unit_cell,
                          pbc=[1, 1, 1])


            _second_order, _ = import_from_files(replicated_atoms=replicated_atoms,
                                                 dynmat_file=dynmat_file,
                                                 supercell=supercell)
            second_order = SecondOrder(atoms, replicated_atoms.positions, supercell, _second_order,
                                                         is_acoustic_sum=is_acoustic_sum)
        elif format == 'shengbte' or format == 'shengbte-qe':

            config_file = folder + '/' + 'CONTROL'
            try:
                atoms, supercell = shengbte_io.import_control_file(config_file)
            except FileNotFoundError as err:
                config_file = folder + '/' + 'POSCAR'
                logging.info('\nTrying to open POSCAR')
                atoms = ase.io.read(config_file)

            # Create a finite difference object
            # TODO: we need to read the grid type here
            is_qe_input = (format == 'shengbte-qe')
            n_replicas = np.prod(supercell)
            n_unit_atoms = atoms.positions.shape[0]
            if is_qe_input:
                filename = folder + '/espresso.ifc2'
                second_order, supercell = shengbte_io.read_second_order_qe_matrix(filename)
                second_order = second_order.reshape((n_unit_atoms, 3, n_replicas, n_unit_atoms, 3))
                second_order = second_order.transpose(3, 4, 2, 0, 1)
                grid_type = 'F'
            else:
                second_order = shengbte_io.read_second_order_matrix(folder, supercell)
                second_order = second_order.reshape((n_unit_atoms, 3, n_replicas, n_unit_atoms, 3))
                grid_type = 'C'
            second_order = SecondOrder.from_supercell(atoms,
                                                                        grid_type=grid_type,
                                                                        supercell=supercell,
                                                                        force_constant=second_order[np.newaxis, ...],
                                                                        is_acoustic_sum=True)



        elif format == 'hiphive':
            filename = 'atom_prim.xyz'
            # TODO: add replicated filename in example
            replicated_filename = 'replicated_atoms.xyz'
            try:
                import kaldo.interface.hiphive_io as hiphive_io
            except ImportError:
                logging.error('In order to use hiphive along with kaldo, hiphive is required. \
                      Please consider installing hihphive. More info can be found at: \
                      https://hiphive.materialsmodeling.org/')

            atom_prime_file = str(folder) + '/' + filename
            replicated_atom_prime_file = str(folder) + '/' + replicated_filename
            # TODO: Make this independent of replicated file
            atoms = ase.io.read(atom_prime_file)
            replicated_atoms = ase.io.read(replicated_atom_prime_file)

            # Create a finite difference object
            if 'model2.fcs' in os.listdir(str(folder)):
                _second_order = hiphive_io.import_second_from_hiphive(folder, np.prod(supercell),
                                                                      atoms.positions.shape[0])
                second_order = SecondOrder(atoms, replicated_atoms.positions,
                                                             supercell,
                                                             _second_order)


        else:
            raise ValueError
        return second_order


    @timeit
    def calculate_frequency(self, q_points, is_amorphous=False, distance_threshold=None):
        eigenvals = self.calculate_eigensystem(q_points, is_amorphous, distance_threshold, only_eigenvals=True)
        frequency = np.abs(eigenvals) ** .5 * np.sign(eigenvals) / (np.pi * 2.)
        return frequency.real


    def calculate_dynmat_derivatives(self, q_points, is_amorphous=False, distance_threshold=None):
        atoms = self.atoms
        list_of_replicas = self.list_of_replicas
        replicated_cell = self.replicated_atoms.cell
        replicated_cell_inv = np.linalg.inv(self.replicated_atoms.cell)

        dynmat = self.dynmat(atoms.get_masses())
        positions = self.atoms.positions
        n_unit_cell = atoms.positions.shape[0]
        n_modes = n_unit_cell * 3
        n_k_points = q_points.shape[0]
        n_replicas = np.prod(self.supercell)

        if distance_threshold is not None:
            logging.info('Using folded flux operators')
        cell_inv = np.linalg.inv(self.atoms.cell)

        ddyn = np.zeros((n_k_points, n_unit_cell * 3, n_unit_cell * 3, 3)).astype(np.complex)
        for index_k in range(n_k_points):
            qvec = q_points[index_k]
            if is_amorphous:
                distance = positions[:, np.newaxis, :] - positions[np.newaxis, :, :]
                distance = wrap_coordinates(distance, replicated_cell, replicated_cell_inv)
                dynmat_derivatives = contract('ija,ibjc->ibjca', distance, dynmat[0, :, :, 0, :, :])
            else:
                distance = positions[:, np.newaxis, np.newaxis, :] - (
                        positions[np.newaxis, np.newaxis, :, :] + list_of_replicas[np.newaxis, :, np.newaxis, :])

                distance_to_wrap = positions[:, np.newaxis, np.newaxis, :] - (
                    self.replicated_atoms.positions.reshape(n_replicas, n_unit_cell, 3)[
                    np.newaxis, :, :, :])

                list_of_replicas = self.list_of_replicas

                if distance_threshold is not None:
                    dynmat_derivatives = np.zeros((n_unit_cell, 3, n_unit_cell, 3, 3), dtype=np.complex)
                    for l in range(n_replicas):
                        wrapped_distance = wrap_coordinates(distance_to_wrap[:, l, :, :], replicated_cell,
                                                            replicated_cell_inv)
                        mask = (np.linalg.norm(wrapped_distance, axis=-1) < distance_threshold)
                        id_i, id_j = np.argwhere(mask).T
                        dynmat_derivatives[id_i, :, id_j, :, :] += np.einsum('fa,fbc->fbca', distance[id_i, l, id_j, :], \
                                                                             dynmat[0, id_i, :, 0, id_j, :] *
                                                                             chi(qvec, list_of_replicas, cell_inv)[l])
                else:

                    dynmat_derivatives = contract('ilja,ibljc,l->ibjca', distance, dynmat[0],
                                                  chi(qvec, list_of_replicas, cell_inv))
            ddyn[index_k] = dynmat_derivatives.reshape((n_modes, n_modes, 3))
        return ddyn


    def calculate_sij(self, q_points, is_amorphous=False, distance_threshold=None):
        dynmat_derivatives = self.calculate_dynmat_derivatives(q_points, is_amorphous, distance_threshold)
        eigenvects = self.calculate_eigensystem(q_points, is_amorphous, distance_threshold, only_eigenvals=False)[:, 1:, :]

        logging.info('Calculating the flux operators')
        if is_amorphous:
            sij = np.tensordot(eigenvects[0], dynmat_derivatives[0], (0, 1))
            sij = np.tensordot(eigenvects[0], sij, (0, 1))
            sij = sij.reshape((1, sij.shape[0], sij.shape[1], sij.shape[2]))
        else:
            sij = contract('kim,kija,kjn->kmna', eigenvects.conj(), dynmat_derivatives, eigenvects)
        return sij


    def calculate_velocity_af(self, q_points, is_amorphous=False, distance_threshold=None):
        n_modes = self.n_modes
        sij = self.calculate_sij(q_points, is_amorphous, distance_threshold)
        frequency = self.calculate_frequency(q_points, is_amorphous, distance_threshold)
        sij = sij.reshape((q_points.shape[0], n_modes, n_modes, 3))
        velocity_AF = contract('kmna,kmn->kmna', sij,
                               1 / (2 * np.pi * np.sqrt(frequency[:, :, np.newaxis]) * np.sqrt(
                                   frequency[:, np.newaxis, :]))) / 2
        return velocity_AF


    def calculate_velocity(self, q_points, is_amorphous=False, distance_threshold=None):
        velocity_AF = self.calculate_velocity_af(q_points, is_amorphous, distance_threshold)
        velocity = 1j * contract('kmma->kma', velocity_AF)
        return velocity.real


    def calculate_eigensystem(self, q_points, is_amorphous=False, distance_threshold=None, only_eigenvals=False):
        atoms = self.atoms
        n_unit_cell = atoms.positions.shape[0]
        n_k_points = q_points.shape[0]
        n_replicas = np.prod(self.supercell)
        if distance_threshold is not None:
            logging.info('Using folded dynamical matrix.')
        if is_amorphous:
            dtype = np.float
        else:
            dtype = np.complex
        if only_eigenvals:
            esystem = np.zeros((n_k_points, n_unit_cell * 3), dtype=dtype)
        else:
            esystem = np.zeros((n_k_points, n_unit_cell * 3 + 1, n_unit_cell * 3), dtype=dtype)
        cell_inv = np.linalg.inv(self.atoms.cell)
        replicated_cell_inv = np.linalg.inv(self.replicated_atoms.cell)

        for index_k in range(n_k_points):
            qvec = q_points[index_k]
            dynmat = self.dynmat(atoms.get_masses())
            is_at_gamma = (qvec == (0, 0, 0)).all()

            list_of_replicas = self.list_of_replicas
            if distance_threshold is not None:
                dyn_s = np.zeros((n_unit_cell, 3, n_unit_cell, 3), dtype=np.complex)
                replicated_cell = self.replicated_atoms.cell

                for l in range(n_replicas):
                    distance_to_wrap = atoms.positions[:, np.newaxis, :] - (
                        self.replicated_atoms.positions.reshape(n_replicas, n_unit_cell,
                                                                                               3)[np.newaxis, l, :, :])

                    distance_to_wrap = wrap_coordinates(distance_to_wrap, replicated_cell, replicated_cell_inv)

                    mask = np.linalg.norm(distance_to_wrap, axis=-1) < distance_threshold
                    id_i, id_j = np.argwhere(mask).T

                    dyn_s[id_i, :, id_j, :] += dynmat[0, id_i, :, 0, id_j, :] * chi(qvec, list_of_replicas, cell_inv)[l]
            else:
                if is_at_gamma:
                    dyn_s = contract('ialjb->iajb', dynmat[0])
                else:
                    dyn_s = contract('ialjb,l->iajb', dynmat[0], chi(qvec, list_of_replicas, cell_inv))
            dyn_s = dyn_s.reshape((self.n_modes, self.n_modes))


            if only_eigenvals:
                evals = np.linalg.eigvalsh(dyn_s)
                esystem[index_k] = evals
            else:
                if is_at_gamma:
                    evals, evects = dsyev(dyn_s)[:2]
                else:
                    evals, evects = np.linalg.eigh(dyn_s)
                    # evals, evects = zheev(dyn_s)[:2]
                esystem[index_k] = np.vstack((evals, evects))
        return esystem




    def __str__(self):
        return 'second'