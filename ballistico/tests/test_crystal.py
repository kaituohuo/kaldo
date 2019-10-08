"""
Unit and regression test for the ballistico package.
"""

# Import package, test suite, and other packages as needed
from finitedifference.finitedifference import FiniteDifference
import numpy as np
from ballistico.phonons import Phonons
import ballistico.conductivity as bac

TMP_FOLDER = 'ballistico/tests/tmp-folder'

def create_phonons():
    # Create a finite difference object
    finite_difference = FiniteDifference.import_from_dlpoly_folder(folder='ballistico/tests/si-crystal',
                                                                   supercell=[3, 3, 3])

    # Create a phonon object
    phonons = Phonons(finite_difference=finite_difference,
                      kpts=[5, 5, 5],
                      is_classic=False,
                      temperature=300,
                      folder=TMP_FOLDER)
    return phonons


def test_sc_conductivity():
    phonons = create_phonons()
    cond = np.abs(np.mean(bac.conductivity(phonons, method='sc', max_n_iterations=71)[0].sum(axis=0).diagonal()))
    np.testing.assert_approx_equal(cond, 255, significant=3)


def test_qhgk_conductivity():
    phonons = create_phonons()
    cond = bac.conductivity(phonons, method='qhgk').sum(axis=0)
    cond = np.abs(np.mean(cond.diagonal()))
    np.testing.assert_approx_equal(cond, 230, significant=3)


def test_rta_conductivity():
    phonons = create_phonons()
    cond = np.abs(np.mean(bac.conductivity(phonons, method='rta').sum(axis=0).diagonal()))
    np.testing.assert_approx_equal(cond, 226, significant=3)


def test_inverse_conductivity():
    phonons = create_phonons()
    cond = np.abs(np.mean(bac.conductivity(phonons, method='inverse').sum(axis=0).diagonal()))
    np.testing.assert_approx_equal(cond, 256, significant=3)