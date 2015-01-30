# -*- coding: utf-8 -*-
"""
Created on Wed Jan 07 14:35:08 2015

@author: Richard
"""

from collections import defaultdict
import itertools

import sympy



## Groebner stuff
def _equations_to_groebner_exprs(eqns):
    ''' Take a bunch of equations, square them, add the equations that binarize
        the variables, and go

    '''
    eqns = map(lambda eqn: eqn.lhs - eqn.rhs, eqns)
    atoms = set.union(*[eqn.atoms(sympy.Symbol) for eqn in eqns])

    groebner_roots = [(atom*(1 - atom)).expand() for atom in atoms]
    #to_groebnerise = [(eqn**2).expand() for eqn in eqns] + groebner_roots
    to_groebnerise = eqns + groebner_roots
    #groebner_roots = atoms
    return to_groebnerise, atoms

def equations_to_groebner_eqns(eqns, binarise=True):
    ''' Take a set of equations, calculate the Groebner basis and return it.
        Thin wrapper around _equations_to_groebner_exprs as sometimes we don't
        want to pre-process for the purposes of testing

        >>> lhs = 'a*b + c*d'
        >>> rhs = '1'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> groeb = equations_to_groebner_eqns(eqns)
        >>> for expr in groeb: print expr        
        a*d - a - d + 1 == 0
        a*c - a - c + 1 == 0
        b*d - b - d + 1 == 0
        b*c - b - c + 1 == 0
        a*b + c*d - 1 == 0
    '''

    atoms = set.union(*[eqn.atoms(sympy.Symbol) for eqn in eqns])
    
    if binarise:
        eqns = map(lambda eqn: ((eqn.lhs - eqn.rhs)**2).expand(), eqns)
    
        # Force the equations to be binary
        binary_roots = [(atom*(atom - 1)).expand() for atom in atoms]
        
        # Now we force quadratic terms only
        cubic_terms = map(sympy.prod, itertools.combinations(atoms, 3))
        
        groeb_exprs = eqns + binary_roots# + cubic_terms

    # Don't do any preprocessing
    else:
        groeb_exprs = map(lambda eqn: eqn.lhs - eqn.rhs, eqns)

    groebner_expansion = sympy.polys.groebner(groeb_exprs, atoms, order='lex')
    
    if binarise:
        groebner_expansion = set(groebner_expansion).difference(set(binary_roots))
        groebner_expansion.difference_update(set(eqns))        
    
    return map(sympy.Eq, groebner_expansion)



def equations_to_groebner_coef_str(eqns):
    ''' Take equations and return a string representing the Groebner basis
        expansion of the objective function
        
        >>> lhs = 'a*b + c*d'
        >>> rhs = '1'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> print equations_to_groebner_coef_str(eqns)
        2 4 1
        1 2 -1
        1 2 3 4 2
        3 4 -1
        1 3 1
        3 -2
        4 -2
        5
        2 3 1
        1 -2
        2 -2
        1 4 1
        <BLANKLINE>
        {c: 3, b: 2, a: 1, d: 4}
    '''
    eqns = equations_to_groebner_eqns(eqns)
    term_dicts = map(eqn_to_vanilla_term_dict, eqns)
    term_dict = sum_term_dicts(term_dicts)
    return term_dict_to_coef_string(term_dict)

## Vanilla obj func
def eqn_to_vanilla_term_dict(eqn):
    ''' Take an equation and square it, throwing away any exponents 
        >>> eqn = sympy.Eq(sympy.sympify('x - 1'))
        >>> eqn_to_vanilla_term_dict(eqn)
        defaultdict(<type 'int'>, {1: 1, x: -1})
    '''
    def _combine_terms((term1, term2)):
        ''' Small helper to recombine things '''
        term1, coef1 = term1
        term2, coef2 = term2
    
        atoms = term1.atoms().union(term2.atoms())
        atoms = sympy.prod(atoms)
        
        return atoms, coef1 * coef2

    terms = itertools.chain(eqn.lhs.as_coefficients_dict().iteritems(),
                                (-eqn.rhs).as_coefficients_dict().iteritems())
    products = itertools.product(terms, repeat=2)
    term_to_coef = itertools.imap(_combine_terms, products)
    
    # Now add up and return the term dict    
    final_terms = defaultdict(int)
    for term, coef in term_to_coef:
        final_terms[term] += coef
    return final_terms
    
def equations_to_vanilla_objective_function(equations):
    ''' Take a list of sympy equations and return an objective function

        >>> lhs = 'x'
        >>> rhs = '1'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> equations_to_vanilla_objective_function(eqns)
        -x + 1

        >>> lhs = 'x + y'
        >>> rhs = 'x*y'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> equations_to_vanilla_objective_function(eqns)
        -x*y + x + y
    '''
    # Watch out for edge cases
    if len(equations) == 0:
        return sympy.sympify('0')

    tds = map(eqn_to_vanilla_term_dict, equations)
    term_dict = sum_term_dicts(tds)
    return sum([k*v for k, v in term_dict.iteritems()])

def equations_to_vanilla_coef_str(equations):
    ''' Return the coefficient string of the objective function of some equations
        Also include the dictionary of variable number to original variable

        >>> lhs = 'x'
        >>> rhs = '1'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> print equations_to_vanilla_coef_str(eqns)
        1
        1 -1
        <BLANKLINE>
        {x: 1}

        >>> lhs = 'x + y'
        >>> rhs = 'x*y'
        >>> eqns = [sympy.Eq(sympy.sympify(lhs), sympy.sympify(rhs))]
        >>> print equations_to_vanilla_coef_str(eqns)
        1 2 -1
        1 1
        2 1
        <BLANKLINE>
        {x: 1, y: 2}
    '''
    tds = map(eqn_to_vanilla_term_dict, equations)
    term_dict = sum_term_dicts(tds)
    return term_dict_to_coef_string(term_dict)

## General helpers
def sum_term_dicts(term_dicts):
    ''' Combine term dicts '''
    term_dict = defaultdict(int)
    for t_d in term_dicts:
        for term, coef in t_d.iteritems():
            term_dict[term] += coef
    return term_dict

def expressions_to_term_dict(exprs, process_eqn=None):
    ''' Given a list of Sympy expressions, calculate the term: coefficient dict
        of the objective function
    '''
    final_terms = defaultdict(int)
    for i, expr in enumerate(exprs):
#        print '{:.2f}% done'.format(100.0 * i / len(exprs))
        
        for t, c in expr.as_coefficients_dict().iteritems():
            final_terms[t] += c
    return final_terms

def _assign_atoms_to_index(atoms):
    ''' Take an iterable of atoms and return an {atom: integer id} map

        >>> atoms = sympy.sympify('x1*x2*y1*z*a').atoms()
        >>> _assign_atoms_to_index(atoms)
        {x2: 3, y1: 4, x1: 2, a: 1, z: 5}
    '''
    atoms = sorted(atoms, key=str)
    atom_map = {v : i + 1 for i, v in enumerate(atoms)}
    return atom_map

def term_dict_to_coef_string(term_dict):
    ''' Given a dictionary of terms to coefficient, return the coefficient
        string for input into the optimisation code
    '''
    if not len(term_dict):
        return ''

    atoms = set.union(*[term.atoms(sympy.Symbol) for term in term_dict.iterkeys()])
    atom_map = _assign_atoms_to_index(atoms)

    lines = []
    for term, coef in term_dict.iteritems():
        var_num = sorted([atom_map[atom] for atom in term.atoms(sympy.Symbol)])
        line = ' '.join(map(str, var_num))
        if line:
            line += ' ' + str(coef)
        else:
            if coef == 0:
                continue
            line = str(coef)
        lines.append(line)

    coef_str = '\n'.join(lines)
    atom_str = str(atom_map)
    return '\n\n'.join([coef_str, atom_str])

def expression_to_coef_string(expr):
    ''' Return the coefficient string for a sympy expression
        Also include the dictionary of variable number to original variable

        >>> inp = '40*s_1 + 30*s_1*s_2 + 100*s_1*s_2*s_3 - 15*s_2*s_3 - 20*s_3 + 4'
        >>> print expression_to_coef_string(inp)
        4
        1 2 30
        2 3 -15
        1 40
        3 -20
        1 2 3 100
        <BLANKLINE>
        {s_3: 3, s_2: 2, s_1: 1}
    '''
    if isinstance(expr, str):
        expr = sympy.sympify(expr)

    return term_dict_to_coef_string(expr.as_coefficients_dict())

def coef_str_to_file(coef_str, filename=None):
    ''' Write the objective function to a file, or printing if None.
        Also include the dictionary of variable number to original variable
    '''
    if filename is None:
        print coef_str
    else:
        f = open(filename, 'a')
        f.write(coef_str)
        f.close()

## Original Schaller implementation when the equations come transformed, we
## just need to add them up
def equations_to_sum_coef_str(eqns):
    ''' Take equations and sum them up
        >>> equations = ['a + b - c*d - 1', 'x1*x2 + 3*x2*x3 - 2']
        >>> equations = map(sympy.sympify, equations)
        >>> equations = map(sympy.Eq, equations)
        >>> print equations_to_sum_coef_str(equations)
        3 4 -1
        6 7 3
        -3
        1 1
        2 1
        5 6 1
        <BLANKLINE>
        {x3: 7, c: 3, x2: 6, d: 4, x1: 5, a: 1, b: 2}
    '''
    exprs = map(lambda x: x.lhs - x.rhs, eqns)
    term_dict = expressions_to_term_dict(exprs)
    return term_dict_to_coef_string(term_dict)

## Introduce auxilary variables
def schaller((ab, s)):
    ''' Change (ab-s)**2 to (ab - sa - sb + s), which has minimums at exactly
        the point ab = s
        
        >>> vars_ = sympy.symbols('a b s')
        >>> a, b, s = vars_
        >>> orig_expr = (a*b - s)**2
        >>> schaller_expr = schaller((a*b, s))
        
        >>> print schaller_expr
        a*b - 2*a*s - 2*b*s + 3*s
        
        >>> abss = itertools.product(range(2), repeat=3)
        >>> for abs in abss:
        ...     to_sub = dict(zip(vars_, abs))
        ...     if orig_expr.subs(to_sub) == 0:
        ...         assert schaller_expr.subs(to_sub) == 0
        ...     else:
        ...         assert schaller_expr.subs(to_sub) > 0
    '''
    a, b = ab.atoms(sympy.Symbol)
    return a*b - 2*a*s - 2*b*s + 3*s

def exprs_to_auxillary_term_dict(exprs):
    ''' Take equations and replace 2-qubit interactions with new variables 
        
        >>> a, b, c, d, e = sympy.symbols('a b c d e')
        >>> eqns = [sympy.Eq(a, b), sympy.Eq(c*d, e)]
        >>> exprs = map(lambda x: x.lhs - x.rhs, eqns)
        >>> exprs_to_auxillary_term_dict(exprs)
        defaultdict(<type 'int'>, {a*b: -2, c*c_d: -2, c_d: 4, 1: 0, a: 1, c_d*e: -2, e: 1, b: 1, c_d*d: -2, c*d: 1})
    '''
    # First replace all 2 qubit terms with new variables and sub them in
    aux_s = {}
    new_exprs = []
    for expr in exprs:
        new_expr = 0
        for term in expr.as_ordered_terms():
            num_qubits = len(term.atoms(sympy.Symbol))
            if num_qubits < 2:
                new_expr += term
            elif num_qubits == 2:
                coef, qubits = term.as_coeff_Mul()
                s = sympy.Symbol('{}_{}'.format(*sorted(qubits.atoms(), key=str)))
                aux_s[qubits] = s
                new_expr += coef * s
            else:
                new_expr += term
        new_exprs.append(new_expr)
    exprs = new_exprs
    #exprs = [expr.subs(aux_s) for expr in exprs]
    
    # Now take the squares ready for the final equation
    #exprs = [(expr**2).expand() for expr in exprs]
    exprs = map(sympy.Eq, exprs)
    tds = map(eqn_to_vanilla_term_dict, exprs)
    term_dict = sum_term_dicts(tds)
    
    # Now use the funky formula to provide equality for the auxillary variables
    aux_exprs = map(schaller, aux_s.iteritems())
    for term, coef in expressions_to_term_dict(aux_exprs).iteritems():     
        term_dict[term] += coef
    
    return term_dict
    
def equations_to_auxillary_coef_str(eqns):
    ''' Take equations and make the objective function nice 
    
        >>> a, b, c, d, e = sympy.symbols('a b c d e')
        >>> eqns = [sympy.Eq(a, b), sympy.Eq(c*d, e)]
        >>> print equations_to_auxillary_coef_str(eqns)
        1 2 -2
        3 4 -2
        4 4
        1 1
        4 6 -2
        6 1
        2 1
        4 5 -2
        3 5 1
        <BLANKLINE>
        {c: 3, d: 5, a: 1, e: 6, c_d: 4, b: 2}
    '''
    exprs = map(lambda x: x.lhs - x.rhs, eqns)
    term_dict = exprs_to_auxillary_term_dict(exprs)
    return term_dict_to_coef_string(term_dict)

if __name__ == "__main__":
    import doctest
    doctest.testmod()

    a, b, c, d, e, f = sympy.symbols('a b c d e f')
    syms = [a, b, c, d, e, f]
    exprs = [a + b,
             a*b + c*d,
             a*b + b*c,
             a*b + c*d + e,
             a*b + c*d - 1
             ]

    new = sum([_t*_c for _t, _c in exprs_to_auxillary_term_dict(exprs).iteritems()])
    exprs = sum(map(lambda x: (x**2).expand(), exprs))

    for abcde in itertools.product(xrange(2), repeat=5):
        to_sub = zip(syms, abcde)
        orig_val = exprs.subs(to_sub)
        new = sympy.sympify(str(new).replace('_', '*'))
        new_val = new.subs(to_sub)
        if orig_val == 0:
            assert new_val == 0
        else:
            assert new_val > 0