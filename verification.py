# -*- coding: utf-8 -*-
"""
Created on Wed Dec 31 18:35:14 2014

@author: Richard
"""

import itertools
import sympy
import cPickle

from contradiction_exception import ContradictionException
from sympy_helper_fns import is_constant, max_value, min_value
from rsa_constants import RSA100, RSA100_F1, RSA100_F2
from cfg_sympy_solver import FACTOR_DICT_FILENAME
from carry_equations_generator import generate_carry_equations
from sympy_solver import EquationSolver
from solver_hybrid import SolverHybrid

SOLVER = SolverHybrid

EXTRA_KNOWN_FACTORISATIONS = {
        RSA100: (RSA100_F1, RSA100_F2),
        1267650600228508624673600186743: (1125899906842679, 1125899906842817),
        309485009821943203050291389: (17592186044423, 17592186044443),
        # exp 13        
        309485009822787627980424653: (17592186044443, 17592186044471),
        # exp 14
        309485009821943203050291389: (17592186044423, 17592186044443),
        # exp 15        
        1267650600228508624673600186743: (1125899906842679, 1125899906842817),
        # exp 16
        1267650600228402790082356974917: (1125899906842679, 1125899906842723),
}

for product, (f1, f2) in EXTRA_KNOWN_FACTORISATIONS.iteritems():
    assert product == f1 * f2
    assert f1 <= f2

# Now union with all the known factorisations on disk, for which the checks
# have been made at write time
try:
    factor_dict_file = open(FACTOR_DICT_FILENAME, 'r')
    KNOWN_FACTORISATIONS = cPickle.load(factor_dict_file)
    factor_dict_file.close()
    KNOWN_FACTORISATIONS.update(EXTRA_KNOWN_FACTORISATIONS)
except Exception as e:
    print 'Error loading factor dict:\n', e
    KNOWN_FACTORISATIONS = EXTRA_KNOWN_FACTORISATIONS

VERIFICATION_FAILURE_MESSAGE = '*** Assertions failed. Solution incorrect ***'
VERIFICATION_SUCCESS_MESSAGE = 'All assertions passed.'
FACTORISATION_NOT_FOUND_STEM = 'No factorisation found for {}'


BRUTE_FORCE_FACTORISATION_LIMIT = 10**16

def _extract_solutions(solutions, max_digits):
    ''' Solutions is a dict of solutions we want to look in. num_digits is
        the total number of digits in a factor.
        Returns the solution according to solutions.
        NOTE only works for factors with same number of digits
    '''
    p = [1]
    q = [1]
    for i in xrange(max_digits - 2, 0, -1):
        p.append(solutions.get(sympy.Symbol('p{}'.format(i))))
        q.append(solutions.get(sympy.Symbol('q{}'.format(i))))
    p.append(1)
    q.append(1)
    return p, q

def get_target_factors(product):
    ''' Return tuple of target factors, or None if the can't be found '''

    known_factors = KNOWN_FACTORISATIONS.get(product)
    if known_factors is not None:
        return known_factors

    # Work out our target ps and qs
    if product < BRUTE_FORCE_FACTORISATION_LIMIT:
        return factorise(product)

    return None

def get_target_digits(product):
    ''' Get the target solutions for pi, qi 
    
        >>> get_target_digits(143)
        [[1, 1, 0, 1], [1, 0, 1, 1]]
    '''

    # Get the target factors, turn them into binary and do some checks
    target_factors = get_target_factors(product)
    if target_factors is None:
        return
    target_factors = map(bin, target_factors)

    # Check we have 2 factors
    assert len(target_factors) == 2

    # Check the same length
    assert len(target_factors[0]) == len(target_factors[1])

    # Trim off the first '0b' and check we have a 1 at each end
    target_factors = [fact[2:] for fact in target_factors]
    target_factors = [map(int, fact) for fact in target_factors]
    for fact in target_factors:
        assert fact[0] == fact[-1] == 1
    
    return target_factors

def get_target_pq_dict(product, swap=False):
    ''' Return a dic=ctionary of expected p and q values 
    
        >>> get_target_pq_dict(143)
        {q1: 1, p1: 0, p2: 1, q2: 0}
    '''
    target_digits = get_target_digits(product)
    target_dict = {}
    pi, qi = target_digits
    
    # Allow p and q to permute
    if swap:
        pi, qi = qi, pi

    pi.reverse()
    qi.reverse()
    for i, pi_ in enumerate(pi):
        if (i == 0) or (i == len(pi) - 1):
            assert pi_ == 1
            continue
        target_dict[sympy.Symbol('p{}'.format(i))] = pi_
    for i, qi_ in enumerate(qi):
        if (i == 0) or (i == len(qi) - 1):
            assert qi_ == 1
            continue
        target_dict[sympy.Symbol('q{}'.format(i))] = qi_

    return target_dict

def get_num_digit_multiplicands(product):
    ''' Return the number of digits for each multiplicand 
        
        >>> get_num_digit_multiplicands(143)
        [4, 4]
        
        >>> get_num_digit_multiplicands(RSA100)
        [165, 165]
    '''
    factors = get_target_digits(product)
    return map(len, factors)

def get_target_solutions(product, equation_generator=generate_carry_equations):
    ''' Generate all of the correct solutions by plugging all of the correct
        pi and qi into a Solver and solving
        
        >>> get_target_solutions(143)
        {z56: 1, z12: 0, q1: 1, z24: 0, z35: 0, z45: 1, q2: 0, z67: 1, z46: 0, p2: 1, z34: 1, z23: 0, z57: 0, p1: 0}
    '''
    digitsInMultiplicand1, digitsInMultiplicand2 = get_num_digit_multiplicands(product)    
    
    eqns = equation_generator(digitsInMultiplicand1=digitsInMultiplicand1, 
                                    digitsInMultiplicand2=digitsInMultiplicand2,
                                    product=product)
    system = SOLVER(eqns)
    
    target_pq = get_target_pq_dict(product)
    
    for var, val in target_pq.iteritems():
        system.update_value(var, val)
    
    system.solve_equations()

#    assert len(system.unsolved_var) == 0
    return system.solutions

def check_substitutions(product, system, verbose=False):
    ''' Check that, when we substitute the correct p and q values in to a
        Solver we don't get any contradictions
        
        >>> eqns = generate_carry_equations(4, 4, 143)
        >>> system = SOLVER(eqns)
        >>> check_substitutions(143, system)
        True
        
        >>> eqns = generate_carry_equations(8, 8, 56153)
        >>> system = SOLVER(eqns)
        >>> system.solve_equations(max_iter=6)
        >>> check_substitutions(56153, system)
        True
        
    '''
    success = False
    
    for target_dict in [get_target_pq_dict(product), 
                        get_target_pq_dict(product, swap=True)]:
        try:
            for var, val in target_dict.iteritems():
                system.add_solution(var, val)
            system.solve_equations()
            success = True
            break
        except ContradictionException:
            continue

    if success:
        if verbose:
            num_remaining = len(system.unsolved_var)
            if num_remaining:
                print '{} qubits undetermined by answers'.format(num_remaining)
            print VERIFICATION_SUCCESS_MESSAGE
        return True
    else:
        if verbose:
            print VERIFICATION_FAILURE_MESSAGE
        return False


def check_solutions(product, solutions, verbose=False):
    ''' Check that solutions are consistent with the binary factorisation.
        NOTE Only works with prime numbers with the same number of digits in
        their factors

        >>> p1, p2, p3, p10, p160, q1, q3, q164 = sympy.var('p1 p2 p3 p10 p160 q1 q3 q164')
        >>> q1, q2, q3, q10, q160, p1, p3, p164 = sympy.var('q1 q2 q3 q10 q160 p1 p3 p164')

        >>> soln = {p1: 0, p2: 1, p3: 1, p10: 1, p160: 1, q1: 1, q3: 0, q164: 1}
        >>> check_solutions(RSA100, soln, verbose=True)
        All assertions passed.
        True

        >>> soln = {q1: 0, q2: 1, q3: 1, q10: 1, q160: 1, p1: 1, p3: 0, p164: 1}
        >>> check_solutions(RSA100, soln, verbose=True)
        All assertions passed.
        True
        
        >>> soln = {q1: 1, q2: 1, q3: 1, q10: 1, q160: 1, p1: 1, p3: 0, p164: 1}
        >>> check_solutions(RSA100, soln, verbose=True)
        *** Assertions failed. Solution incorrect ***
        False
        
        >>> soln = {q1: 1, q2: -1}
        >>> check_solutions(RSA100, soln, verbose=True)
        *** Assertions failed. Solution incorrect ***
        False
    '''

    target_factors = get_target_digits(product)
    if target_factors is None:
        if verbose:
            print FACTORISATION_NOT_FOUND_STEM.format(product)
        return
    for perm in itertools.permutations(target_factors):
        try:
            _check_solutions_for_targets(perm, solutions, verbose=verbose)
            return True
        except ContradictionException:
            continue
    
    if verbose:
        print VERIFICATION_FAILURE_MESSAGE
    
    return False


def _check_solutions_for_targets(targets, solutions, verbose=False):
    ''' Inner helper function to try different permutations of factors so that we don't have a nasty
        for loop
    '''
    assert len(targets) == 2
    target_p, target_q = targets

    # Now extract a similar list from the given solutions dict
    digits_in_multiplicand = len(target_p)
    soln_p, soln_q = _extract_solutions(solutions, digits_in_multiplicand)

    symbolic_pairs = []
    # Check fully determined ps and qs and extract symbolic matchings
    for sol, target in itertools.chain(itertools.izip(soln_p, target_p),
                                       itertools.izip(soln_q, target_q)):
        # No solutions found whatsoever
        if sol is None:
            continue
        # If constant, we can check that easily
        if is_constant(sol):
            if sol != target:
                raise ContradictionException()
        else:
            symbolic_pairs.append((sol, target))

    # For symbolic matchings, just check that no symbolic expression is mapped to
    # two different values
    #TODO Do something cleverer here, like plug into a new SOLVER.
    symbolic_map = {}
    for sol, tar in symbolic_pairs:
        prev_tar = symbolic_map.get(sol)
        if prev_tar is None:
            symbolic_map[sol] = tar
        elif tar != prev_tar:
            raise ContradictionException()

    # Now just check all of the symbolic stuff has the value in the range
    for sol, tar in symbolic_map.iteritems():
        if not (min_value(sol) <= tar <= max_value(sol)):
            raise ContradictionException('verification: {} != {}'.format(sol, tar))
    if verbose:
        print VERIFICATION_SUCCESS_MESSAGE

def evaluate_term_dict(product, term_dict):
    ''' Given a term dict, check that it evaluates to 0 
    
        >>> params = EXPERIMENTS[1][:3]
        
        >>> prod = params[-1]
        >>> eqns = generate_carry_equations(*params)
        >>> system = SOLVER(eqns)
        >>> system.solve_equations()
        >>> term_dict = equations_to_vanilla_term_dict(system.equations)
        >>> evaluate_term_dict(prod, term_dict)
        0
    
        >>> params = EXPERIMENTS[2][:3]
        
        >>> prod = params[-1]
        >>> eqns = generate_carry_equations(*params)
        >>> system = SOLVER(eqns)
        >>> system.solve_equations()
        >>> term_dict = equations_to_vanilla_term_dict(system.equations)
        >>> evaluate_term_dict(prod, term_dict)
        0

        >>> params = EXPERIMENTS[3][:3]
        
        >>> prod = params[-1]
        >>> eqns = generate_carry_equations(*params)
        >>> system = SOLVER(eqns)
        >>> system.solve_equations()
        >>> term_dict = equations_to_vanilla_term_dict(system.equations)
        >>> evaluate_term_dict(prod, term_dict)
        0
    '''
    target_solns = get_target_solutions(product)
    
    obj_func = 0
    
    for term, coef in term_dict.iteritems():
        obj_func += term.subs(target_solns) * coef
    
    return obj_func

def factorise(n):
    ''' Return list of factors 
    
        >>> for i in xrange(10): print factorise(i)
        None
        [1]
        [2]
        [3]
        [2, 2]
        [5]
        [3, 2]
        [7]
        [2, 2, 2]
        [3, 3]
    '''
    if n == 1:
        return [1]
    if n < 1:
        return None
    factors = []
    
    while not n % 2:
        factors.append(2)
        n /= 2

    i = 3
    while True:
        if n == 1:
            break
        while not n % i:
            factors.append(i)
            n /= i
        i += 2
    factors.reverse()
    return factors

def binary_factorisation(n):
    ''' Return the binary factorisation of a number '''
    factors = factorise(n)
    bin_fact = map(bin, factors)
    return bin_fact


def print_binary_factorisation(n):
    ''' Print the binary factorisation of a number

        >>> print_binary_factorisation(143)
        143
        =0b10001111
        Factors:
          1101
          1011
    '''
    bin_fact = binary_factorisation(n)
    max_len = len(max(bin_fact, key=len))
    fact_str = '\n'.join([b_f[2:].rjust(max_len) for b_f in bin_fact])
    print '{}\n={}\nFactors:\n{}'.format(n, bin(n), fact_str)


if __name__ == '__main__':
    from cfg_sympy_solver import EXPERIMENTS, QUBIT_REDUCTION_ID    
    from objective_function_helper import (equations_to_vanilla_term_dict,
                                           equations_to_recursive_schaller_term_dict)
    from rsa_constants import RSA100
    import doctest
    doctest.testmod()