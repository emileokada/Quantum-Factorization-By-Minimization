"""
Created on Fri Dec 26 12:35:16 2014

Solve a system of equations with binary variables

@author: Richard Tanburn
"""
from copy import deepcopy
from collections import defaultdict
import inspect
import itertools
import sympy
from sympy.core.cache import clear_cache

import ReHandler
from contradiction_exception import ContradictionException
from sympy_helper_fns import (max_value, min_value, is_equation,
                              remove_binary_squares_eqn, balance_terms,
                              cancel_constant_factor, is_constant,
                              num_add_terms, parity, is_monic, is_one_or_zero,
                              remove_binary_squares, expressions_to_variables,
                              gather_monic_terms, square_equations)
from objective_function_helper import (equations_to_vanilla_coef_str, 
                                       equations_to_vanilla_objective_function,
                                       equations_to_auxillary_coef_str)

__author__ = "Richard Tanburn"
__credits__ = ["Richard Tanburn", "Nathaniel Bryans", "Nikesh Dattani"]
__version__ = "0.0.1"
__status__ = "Prototype"

EQUATION_EQUAL_CHECK_LIMIT = 350

# Parent equation

class EquationSolver(object):
    ''' Solver of equations '''

    def __init__(self, equations=None, variables=None, log_deductions=False,
                 output_filename=None):
        if variables is None:
            if equations is None:
                variables = {}
            else:
                variables = {str(v): v for v in expressions_to_variables(equations)}
        if equations is None:
            equations = []

        # Number of variables at the start
        self.num_qubits_start = len(variables)

        # Dict of string tag to sympy variable instance
        self.variables = variables
        # List of Equation instances to be solved
        self.equations = equations
        # Set of deductions we have made
        self.deductions = {}

        # Solutions. Subset of deductions, where the key is a single variable.
        self.solutions = {}

        # File to print to
        self.output_filename = output_filename
        self._file = None

        # And keep a nested dictionary of who made them, if we want
        self.log_deductions = log_deductions
        self.deduction_record = defaultdict(lambda : defaultdict(list))

    def print_(self, output, close=False):
        ''' Print either to screen or a file if given '''
        if self.output_filename is None:
            print output
        else:
            if (self._file is None) or self._file.closed:
                self._file = open(self.output_filename, 'a')
            output = str(output)
            self._file.write(output + '\n')
            if close:
                self._file.close()

    def copy(self):
        ''' Return a new instance of itself '''
        copy = EquationSolver(deepcopy(self.equations), 
                              deepcopy(self.variables),
                              log_deductions=self.log_deductions, 
                              output_filename=self.output_filename)
                              
        # Now use deepcopy to copy everything else
        copy.num_qubits_start = self.num_qubits_start
        copy.deductions = deepcopy(self.deductions)
        copy.solutions = deepcopy(self.solutions)
        copy.deduction_record = deepcopy(self.deduction_record)
        return copy

    @classmethod
    def from_params(cls, params, **kwargs):
        ''' Create an instance from the outpu of whatever came before
            params[0][i] contains all leftsides
            params[1][i] contains all rightsides
        '''
        equations, variables = parse_equations(params)
        return cls(equations, variables, **kwargs)

    # Pickling
    # This isn't mature/finished, but manages to write equations, deductions and
    # solutions to disk
    def __getstate__(self):
        return (self.equations, self.deductions, self.solutions)
        
    def __setstate__(self, state):
        self.equations, self.deductions, self.solutions = state

    def to_disk(self, filename):
        ''' Write a state to disk '''
        if filename is None:
            return
        import pickle
        pickle.dump(self, open(filename, 'w'))
    
    @staticmethod
    def from_disk(filename, **kwargs):
        ''' Load from disk '''
        if filename is None:
            raise ValueError('Cannot load from filename None')
        import pickle
        data = pickle.load(open(filename, 'r'))
        instance = EquationSolver(equations=data.equations, **kwargs)
        instance.deductions, instance.solutions = data.deductions, data.solutions
        return instance
        

    @staticmethod
    def _dict_as_equations(dict_):
        ''' Return deductions as a list of equations '''
        new_equations = []
        for lhs, rhs in dict_.iteritems():
            new_equations.append(sympy.Eq(lhs, rhs))
        
#        new_equations = filter(is_equation, new_equations)        
#        new_equations = [eqn.expand() for eqn in new_equations]
#        new_equations = map(remove_binary_squares_eqn, new_equations)
#        new_equations = map(balance_terms, new_equations)
#        new_equations = map(cancel_constant_factor, new_equations)
#        new_equations = filter(is_equation, new_equations)

        return sorted(new_equations, key=lambda x: str(x))
    
    @property
    def deductions_as_equations(self):
        ''' Return deductions as a list of equations '''
        new_equations = EquationSolver._dict_as_equations(self.deductions)
        new_equations = filter(is_equation, new_equations)        
        new_equations = [eqn.expand() for eqn in new_equations]
        new_equations = map(remove_binary_squares_eqn, new_equations)
        new_equations = map(balance_terms, new_equations)
        new_equations = map(cancel_constant_factor, new_equations)
        new_equations = filter(is_equation, new_equations)
        return new_equations
    
    @property
    def solutions_as_equations(self):
        ''' Return solutions as a list of equations '''
        return EquationSolver._dict_as_equations(self.solutions)

    def print_deduction_log(self):
        ''' Print the judgements and the deductions they have made '''
        for judgement, ded_info in self.deduction_record.iteritems():
            self.print_('\n' + judgement)
            for eqn, deds in ded_info.iteritems():
                eqn_str = str(eqn).ljust(25)
                ded_str = map(lambda (x, y) : '{}={}'.format(x, y), deds)
                ded_str = ', '.join(ded_str)
                self.print_('{}\t=>\t{}'.format(eqn_str, ded_str))

    @property    
    def _length_tuple(self):
        ''' Return a tuple of the lengths of equations, deductions, solutions 
        '''
        return len(self.equations), len(self.deductions), len(self.solutions)

    def solve_equations(self, max_iter=100, verbose=False):
        ''' Solve a system of equations
        '''
        state_summary = self._length_tuple
        # The number of iterations in which we've made no new deductions
        num_constant_iter = 0

        if verbose:        
            self.print_('Num variables: {}'.format(len(self.variables)))
            self.print_('Iter\tNum Eqn\tNum Ded\tNum Sol')
        for i in xrange(max_iter):
            # Clear the cache so that we don't blow up memory when working with
            # large numbers
            clear_cache()

            if verbose:
                self.print_('\t'.join(['{}'] * 4).format(i, *state_summary))

            self.equations = self.clean_equations(self.equations)
            self.apply_judgements(self.equations + 
                                  self.deductions_as_equations)
            
            # Now apply judgements to non-trivial solutions
            non_trivial_soln = []
            for variable, soln in self.solutions.iteritems():
                if len(soln.atoms()) > 2:
                    non_trivial_soln.append(sympy.Eq(variable, soln))
            non_trivial_soln = map(remove_binary_squares_eqn, non_trivial_soln)
            non_trivial_soln = map(balance_terms, non_trivial_soln)
            non_trivial_soln = map(cancel_constant_factor, non_trivial_soln)
            non_trivial_soln = filter(is_equation, non_trivial_soln)            
            self.apply_judgements(non_trivial_soln)

            if self._length_tuple == state_summary:
                num_constant_iter += 1

                # Here lets apply some slower, complex judgements to try and
                # unstick ourselves
                if num_constant_iter >= 2:
                    for eqn in self.equations:
                        self.judgement_n_term(eqn, 4)
                        
                        # Only do 1 at a time, so if we have a new deduction
                        # go round again
                        if self._length_tuple != state_summary:
                            num_constant_iter = 0
                            break
                    
                    # Now apply judgements to the squares of the equations
#                    self.apply_judgements_square(self.equations)

                if num_constant_iter > 4:
                    break
            else:
                num_constant_iter = 0
                state_summary = self._length_tuple

        # Final clean again, for good luck
        self.equations = self.clean_equations(self.equations)

    def print_summary(self):
        ''' Print a summary of the information held in the object '''
        final_equations = self.equations + self.deductions_as_equations
        final_equations = sorted(set(final_equations), key=str)
        unsolved_var = self.unsolved_var
#
#        self.final_variables = unsolved_var
#
#        self.final_equations = final_equations
#        self.final_solutions = self.solutions.copy()

        if self.log_deductions:
            self.print_deduction_log()

#        self.print_('Unsimplified equations')
#        for e in self.equations:
#            self.print_(e)
#        self.print_('Deductions')
#        for e in self.deductions_as_equations:
#            self.print_(e)

#        self.print_('Solns')
#        for k in sorted(self.solutions.keys(), key=str):
#            self.print_('{} = {}'.format(k, self.solutions[k]))

        self.print_('Final Variables')
        self.print_(unsolved_var)


        self.print_('Final Equations')
        for e in sorted(final_equations, key=str):
            self.print_(e)

        self.print_('Num Qubits Start: {}'.format(self.num_qubits_start))
        self.print_('Num Qubits End: {}'.format(len(unsolved_var)), close=True)

        # Print the p and q solution
        pqs = {}
        zs = {}
        self.print_('p, q Solutions')
        for var, sol in self.solutions.iteritems():
            svar = str(var)
            if svar.startswith('p') or svar.startswith('q'):
                pqs[var] = sol
            else:
                zs[var] = sol
        for k in sorted(pqs.keys(), key=str):
            self.print_('{} = {}'.format(k, pqs[k].subs(zs)))

#        self.print_('Final coefficients')
#        self.print_(equations_to_coef_string(self.final_equations), close=True)


    def reformulate_equations(self):
        ''' Reformulate the final equations from the deductions.
            Return the final equations and a dictionary of calculated values
        '''
        import warnings
        warnings.warn('reformulate_equations is deprecated!!')
        solved_var = {}

        # First trawl through the deductions for definite solutions
        for expr, val in self.deductions.iteritems():
#            if not is_one_or_zero(val):
#                continue
            latoms = expr.atoms()
            if len(latoms) == 1:
                solved_var[expr] = val

        # Now trawl through for equations which involve the unsolved variables
        final_equations = []
        unsolved_var = set(self.variables.values()).difference(solved_var.keys())

        for eqn in itertools.chain(self.equations,
                                   self.deductions_as_equations):
            expr, val = eqn.lhs, eqn.rhs
            latoms = expr.atoms()
            ratoms = set([val]) if isinstance(val, int) else val.atoms()
            if len(latoms.intersection(unsolved_var)) or len(ratoms.intersection(unsolved_var)):
                eqn = sympy.Eq(expr, val)
                # Substitute all of the solved variables
                eqn = eqn.subs(solved_var)

                if eqn == True:
                    continue

                eqn = remove_binary_squares_eqn(eqn)

                # If the final equation is x=constant, then we're also done!!
#                if len(eqn.atoms(sympy.Symbol)) == 1:
#                    assert eqn.rhs.is_constant()
#                    self.update_value(eqn.lhs, eqn.rhs)
#                    solved_var[eqn.lhs] = eqn.rhs
#                    unsolved_var.difference_update([eqn.lhs])
#                else:
                final_equations.append(eqn)

#        self.final_variables = set()
#        for eq in final_equations:
#            for atom in eq.atoms():
#                if not is_one_or_zero(atom):
#                    self.final_variables.add(atom)

        final_equations = sorted(set(final_equations), key=lambda x: str(x))

        self.final_variables = unsolved_var

        self.final_equations = final_equations
        self.final_solutions = solved_var

    @property
    def unsolved_var(self):
        ''' Return a set of variables we haven't managed to eliminate '''
        return set(self.variables.values()).difference(self.solutions.keys())

    @property
    def objective_function(self):
        ''' Return the final objective function, using self.final_equations '''
        return equations_to_vanilla_objective_function(self.equations)

    def objective_function_to_file(self, filename=None):
        ''' Write the objective function to a file, or printing if None.
            Also include the dictionary of variable number to original variable
        '''
        out = equations_to_vanilla_coef_str(self.equations)
        #out = equations_to_auxillary_coef_str(self.equations)

        if filename is None:
            self.print_(out)
        else:
            f = open(filename, 'a')
            f.write(out)
            f.close()

    def clean_equations(self, eqns):
        ''' Remove True equations and simplify '''
        cleaned = filter(is_equation, eqns[:])
        cleaned = [eqn.subs(self.deductions) for eqn in cleaned]
        cleaned = filter(is_equation, cleaned)

        # Extract only the atoms we would like to try and find
        if len(cleaned):
            cleaned_atoms = expressions_to_variables(cleaned)
            cleaned_sol = ((var, self.solutions.get(var)) for var in cleaned_atoms)
            cleaned_sol = filter(lambda x: x[1] is not None, cleaned_sol)
            cleaned_sol = {x[0]: x[1] for x in cleaned_sol}

        cleaned = [eqn.subs(cleaned_sol) for eqn in cleaned]
        cleaned = filter(is_equation, cleaned)
        cleaned = [eqn.expand() for eqn in cleaned]
        cleaned = map(remove_binary_squares_eqn, cleaned)
        cleaned = map(balance_terms, cleaned)
        cleaned = map(cancel_constant_factor, cleaned)
        cleaned = filter(is_equation, cleaned)

        if len(cleaned) < EQUATION_EQUAL_CHECK_LIMIT:
            to_add = []
            # Now add any equations where LHS = RHS1, LHS = RHS2 and permutations
            def _helper(eqn1, eqn2, to_add):
                if ((eqn1.lhs == eqn2.lhs) and
                    (eqn1.rhs != eqn2.rhs) and
                    (not is_constant(eqn1.lhs))):
                    new_eq = sympy.Eq(eqn1.rhs, eqn2.rhs)
                    new_eq = balance_terms(new_eq)
                    to_add.append(new_eq)
#                    self.print_('Equation added! {}, {}'.format(eqn1, eqn2))
    
            all_equations = itertools.chain(cleaned, self.deductions_as_equations)
            for eqn1, eqn2 in itertools.combinations(all_equations, 2):
                _helper(eqn1, eqn2, to_add)
                _helper(sympy.Eq(eqn1.rhs, eqn1.lhs), eqn2, to_add)
                _helper(eqn1, sympy.Eq(eqn2.rhs, eqn2.lhs), to_add)
                _helper(sympy.Eq(eqn1.rhs, eqn1.lhs), sympy.Eq(eqn2.rhs, eqn2.lhs),
                        to_add)
            to_add = filter(is_equation, to_add)
            cleaned.extend(to_add)

        # Finally clean up the deductions now we're finished with them
        self.clean_deductions()

        return list(set(cleaned))

    def clean_deductions(self):
        ''' Clean our deductions. Involves caching solved values and rearranging
            some equations, now we can have negative variables and substitutions

            >>> a, b, c, x, y, z = sympy.symbols('a b c x y z')
            >>> variables = [a, b, c, x, y, z]
            >>> system = EquationSolver([], {str(v) : v for v in variables})
            >>> ZERO, ONE = sympy.sympify(0), sympy.sympify(1)
            >>> deductions = {a: ONE, b: ZERO, ONE: c, x: 1 - y, z*x: ONE}
            >>> system.deductions = deductions
            >>> system.clean_deductions()
            >>> system.solutions
            {c: 1, x: -y + 1, b: 0, a: 1}

            >>> system.deductions
            {z: y*z + 1}

            >>> variables = [a, b, c, x, y, z]
            >>> system = EquationSolver([], {str(v) : v for v in variables})
            >>> deductions = {a: a*b, b: a*b, a: b}
            >>> system.deductions = deductions
            >>> system.clean_deductions()
            >>> system.solutions
            {a: b}
            
            Sort out the x = xy case
            >>> a, b, c, x, y, z = sympy.symbols('a b c x y z')
            >>> variables = [a, b, c, x, y, z]
            >>> system = EquationSolver([], {str(v) : v for v in variables})
            >>> ZERO, ONE = sympy.sympify(0), sympy.sympify(1)
            >>> deductions = {a: a*b, x:x*y + y*z}
            >>> system.deductions = deductions
            >>> system.clean_deductions()
            >>> system.solutions
            {}
            >>> system.deductions
            {a*b: a, x: x*y + y*z}
        '''
        # First trawl through the deductions for definite solutions
        for expr, val in self.deductions.copy().iteritems():
            latoms = expr.atoms(sympy.Symbol)
            ratoms = val.atoms(sympy.Symbol)

            # Hack around the dodgy edge case xy = y
            if len(ratoms.intersection(latoms)):
                if len(latoms) == 1:
                    possible_other_value = self.deductions.get(val)
                    if possible_other_value is not None:
                        self.update_value(expr, possible_other_value)
                continue

            if (len(latoms) == 1) and is_monic(expr):
                self.deductions.pop(expr)
                curr_sol = self.solutions.get(expr)

                if (curr_sol is not None) and (curr_sol != val):
                    # We have different things. Better be careful!!
                    if is_constant(curr_sol):
                        # Both are constant and unequal
                        if is_constant(val):
                            err_str = 'clean_deductions: {} = {} != {}'.format(expr, curr_sol, val)
                            raise ContradictionException(err_str)
                        else:
                            # We have a variable and constant
                            self.update_value(val, curr_sol)
                    else:
                        # Once again, we have a constant and a value
                        if is_constant(val):
                            self.update_value(curr_sol, val)
                        # Both are symbolic
                        else:
                            self.update_value(curr_sol, _simplest(curr_sol, val))
                else:
                    if is_monic(expr):
                        self.solutions[expr] = val

            # If the RHS of a deduction is monic, then go again!
            elif (len(ratoms) == 1) and is_monic(val):
                self.deductions.pop(expr)
                curr_sol = self.solutions.get(val)

                # We might have some disagreement
                if (curr_sol is not None) and (curr_sol != expr):
                    # But if they're both symbolic that is ok!
                    if is_constant(curr_sol) and is_constant(expr):
                        err_str = 'clean_deductions: {} = {} != {}'.format(expr, curr_sol, val)
                        raise ContradictionException(err_str)
                    
                    elif is_constant(curr_sol):
                        self.update_value(val, curr_sol)
                    else:
                        # The new val is constant
                        self.solutions[val] = _simplest(curr_sol, expr)
                        self.update_value(curr_sol, val)
                else:
                    self.solutions[val] = expr

        # Now clean up the solutions before we plug them in
        self.clean_solutions()

        # Now go over the remaining deductions and pick out equations which
        # include an unsolved variables
        unsolved_var = set(self.variables.values()).difference(self.solutions.keys())

        # Clean the solutions so we don't spend so long in subs
        # Extract only the atoms we would like to try and find
        ded_as_eqn = self.deductions_as_equations
        if len(ded_as_eqn):
            cleaned_atoms = set.union(*[eqn.atoms(sympy.Symbol) for eqn in ded_as_eqn])
            cleaned_sol = ((var, self.solutions.get(var)) for var in cleaned_atoms)
            cleaned_sol = filter(lambda x: x[1] is not None, cleaned_sol)
            cleaned_sol = {x[0]: x[1] for x in cleaned_sol}
        else:
            cleaned_sol = self.solutions.copy()

        old_deductions = self.deductions.copy()
        self.deductions = {}
        for expr, val in old_deductions.iteritems():
            
            # If we have something like x = xy + xz, we don't want to expand
            # up to something harder to deal with
            # Note here we want to break the convention of simplest, as in
            # the event of a tie, the first value is returned. In this case,
            # we want to err on the side of caution and not throw away
            # deductions
            # 
            # Actually, don't get rid of anything we don't *need* to, so that
            # we don't miss possible contradictions in the assumption stages.
#            if latoms.intersection(ratoms) and (_simplest(val, expr) == expr):
#                continue

            # Substitute all of the solved variables
            expr = expr.subs(cleaned_sol).expand()
            val = val.subs(cleaned_sol).expand()
            latoms = expr.atoms()
            ratoms = set([val]) if isinstance(val, int) else val.atoms()
            if (len(latoms.intersection(unsolved_var)) or
                len(ratoms.intersection(unsolved_var))):

                eqn = sympy.Eq(expr, val)

                if eqn == True:
                    continue
                eqn = eqn.expand()
                eqn = remove_binary_squares_eqn(eqn)
                eqn = balance_terms(eqn)
                if eqn == True:
                    continue

                self.update_value(eqn.lhs, eqn.rhs)

            # Else we want to check consistency
            else:
                eqn = sympy.Eq(expr, val)

                if is_equation(eqn) and (not eqn):
                    raise ContradictionException('Subbing solutions raised contradiction in deductions')

                if not is_constant(expr):
                    assert not is_constant(val)
                    self.print_('Dropping deduction {} = {}'.format(expr, val))

    def clean_solutions(self):
        ''' Remove cycles and chains in the solutions. Make sure every value is
            set to equal an expression involving constants and unsolved variables

            Simple solutions and cleaning
            >>> system = EquationSolver()
            >>> a, b, c, x, y, z = sympy.symbols('a b c x y z')
            >>> ZERO, ONE = sympy.sympify(0), sympy.sympify(1)
            >>> soln = {a: ONE, b: a, c: a + b - ONE, x: ONE, z: ONE - x + y}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {c: 1, x: 1, b: 1, a: 1, z: y}

            Dealing with cyclic solutions
            >>> system = EquationSolver()
            >>> a, b, c, x, y = sympy.symbols('a b c x y')
            >>> soln = {a: b, b: c, c: a, x: y, y: x}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {c: b, a: b, y: x}

            Cyclic solutions that have a tail
            >>> system = EquationSolver()
            >>> a, b, c, x, y = sympy.symbols('a b c x y')
            >>> soln = {a: b, b: c, c: x, x: y, y: x}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {c: y, x: y, b: y, a: y}

            Non-trival cyclic solutions. This doesn't work as it should yet
            >>> system = EquationSolver()
            >>> x, y = sympy.symbols('x y')
            >>> soln = {x: 1 - y, y: 1 - x}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {}

            Incorrect keys
            >>> system = EquationSolver()
            >>> x, y = sympy.symbols('x y')
            >>> soln = {1 - x: 0, y: x}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {x: 1, y: 1}
            >>> system = EquationSolver()
            >>> x, y = sympy.symbols('x y')
            >>> soln = {1 - x: y}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.solutions
            {x: -y + 1}
        '''
        # First make sure every key is a single value
        for expr, val in self.solutions.copy().iteritems():
            add_coef = expr.as_coeff_Add()[0]
            if add_coef:
                assert len(expr.atoms(sympy.Symbol)) == 1
                variable = expr - add_coef
                rhs = val - add_coef
                if variable.as_coeff_Mul()[0] < 0:
                    variable *= -1
                    rhs *= -1
                self.solutions.pop(expr)
                self.solutions[variable] = rhs
        
        # Now make sure every value in the dict can be binary, throwing if
        # not
        for variable, value in self.solutions.iteritems():
            if (max_value(value) < 0) or (min_value(value) > 1):
                err_str = 'clean_solutions: {} != {}'.format(variable, value)
                raise ContradictionException(err_str)

        changes = False
        new_solutions = {}
        to_skip = []  # Skip for the infinite loops
        for variable, value in self.solutions.iteritems():
            # If value is a constant, skip
            if is_constant(value):
                assert len(variable.atoms()) == 1
                if not is_one_or_zero(value):
                    err_str = '{} must be binary, not {}'.format(variable, value)
                    raise ContradictionException(err_str)
                new_solutions[variable] = value
                continue
            # If x == x, remove it as it as there is no new information in there
            if variable == value:
                continue
            # Break the infinite chain!
            if variable in to_skip:
                continue

            init_value = value
            # Keep a track of the variables we've visited
            seen_before = [variable, value]
            for i in xrange(1000):
                old_value = value
                value = self.solutions.get(value)

                # Watch out for the infinite loops!
                if value in seen_before:
                    value = old_value
                    to_skip.append(value)
                    break
                else:
                    seen_before.append(value)

                if value is None:
                    value = old_value.subs(self.solutions, simultaneous=True).expand()
                    break
                elif isinstance(value, int):
                    break
                else:
                    continue#value = value.subs(self.solutions, simultaneous=True)

            if i > 990:
                raise ValueError('Invalid solutions, check it out!')

            # If we have x = xy, then remove this from solutions and put it in
            # deductions
            if len(variable.atoms(sympy.Symbol).intersection(value.atoms(sympy.Symbol))):
                self.update_value(variable, value)
                continue

            if value != init_value:
                changes = True

            new_solutions[variable] = value

        self.solutions = new_solutions

        if changes:
            self.clean_solutions()

    def _update_log(self, expr, value):
        ''' Log an update under a judgement '''
        if not self.log_deductions:
            return

        # Update the deductions process dictionary
        judgement, eqn = _get_judgement()
        self.deduction_record[judgement][eqn].append((expr, value))

    def update_value(self, expr, value):
        ''' Update the global dictionary and check for contradictions.
            Make sure expr is always 'positive'.
            NOTE Only accepts single terms for expr

            >>> system = EquationSolver()
            >>> x = sympy.symbols('x')
            >>> system.update_value(x, 0)
            >>> system.deductions
            {x: 0}

            >>> system = EquationSolver()
            >>> x = sympy.symbols('x')
            >>> system.update_value(-x, 1)
            >>> system.deductions
            {x: -1}

            >>> system = EquationSolver()
            >>> x = sympy.symbols('x')
            >>> system.update_value(2*x, 0)
            >>> system.deductions
            {x: 0}

            x = x*y case
            >>> x, y, z = sympy.symbols('x y z')
            >>> system = EquationSolver()
            >>> system.update_value(x, x*y)
            >>> system.deductions
            {x*y: x}
            >>> system = EquationSolver()
            >>> system.update_value(y*z, x*y*z)
            >>> system.deductions
            {x*y*z: y*z}
        '''
        # If expr = 2*x and value == 0, then we can get rid of the 2
        if value == 0:
            expr = expr.as_coeff_Mul()[1]

        # Make sure the left is positive
        if expr.as_coeff_Mul()[0] < 0:
            expr = - expr
            value = - value

        # If value is an int, sympify it
        if isinstance(value, int):
            value = sympy.sympify(value)

        current_val = self.deductions.get(expr)

        # If value already maps to expr, avoid the cycle!
        if self.deductions.get(value) == expr:
            return

        # If we have the nasty case where x = x*y, then we want to flip the
        # values round
        # Actually we do this whenever the lhs is a factor of the rhs
        expr_atoms = expr.atoms(sympy.Symbol)
        lhs_simpler = all(expr_atoms.issubset(term.atoms(sympy.Symbol)) 
                      for term in value.as_coefficients_dict().iterkeys())
        if lhs_simpler and len(expr_atoms) < len(value.atoms(sympy.Symbol)):
            self.update_value(value, expr)
            return

        # No possible conflict
        if current_val is None:
            self.deductions[expr] = value
            self._update_log(expr, value)

        # If we already know a value of this family
        elif is_one_or_zero(current_val):
            # If we've found a numeric value
            if is_one_or_zero(value):
                if current_val != value:
                    raise ContradictionException('{} is already set to {} != {}'.format(expr,
                                                 current_val, value))
                else:
                    return
            # We know what we're trying to update to is not a numeric, so update it too
            else:
                self.update_value(value, current_val)
#                self.deductions[expr] = current_val
                self._update_log(expr, value)

        # Current_val is symbolic
        else:
            if is_one_or_zero(value):
                # Perform another error check
                cc_val = self.deductions.get(current_val)
                if is_one_or_zero(cc_val) and (cc_val != value):
                    raise ContradictionException(
                            '{} is already set to {} != {}'.format(
                            current_val,
                            cc_val,
                            value))
                self.deductions[current_val] = value
                self.deductions[expr] = value
                self._update_log(current_val, value)
                self._update_log(expr, value)
            # Both values are symbolic!
            else:
                #TODO Clean up the hack around this silly edge case
                # Right now, if the RHS is written in terms of the LHS, then
                # we'd prefer to use the new value
                if (expr.atoms(sympy.Symbol).issubset(current_val.atoms(sympy.Symbol)) and
                    not expr.atoms(sympy.Symbol).issubset(value.atoms(sympy.Symbol))):
                    self.deductions[expr] = value
                    self._update_log(expr, value)

                else:
                    simple = _simplest(current_val, value)
                    self.deductions[expr] = simple
                    self._update_log(expr, simple)
                    if value != current_val:
                        self.deductions[current_val] = value
                        self._update_log(current_val, value)

    def get_var(self, var):
        ''' Return symbolic variable

            >>> system = EquationSolver()
            >>> x = system.get_var('x')
            >>> x
            x

            >>> x1 = system.get_var('x')
            >>> x1
            x

            >>> x1 is x
            True
        '''
        res = self.variables.get(var)
        if res is None:
            res = sympy.symbols(var, integer=True)
            self.variables[var] = res
        return res

    def set_to_max(self, expr):
        ''' Given an expression, update all terms so that it achieves it's maximum

            >>> system = EquationSolver()
            >>> expr = sympy.sympify('-2*x*y - 3 + 3*z23')
            >>> system.set_to_max(expr)
            >>> system.deductions
            {x*y: 0, z23: 1}
        '''
        coef_dict = expr.as_coefficients_dict()
        for var, coef in coef_dict.iteritems():
            if var == 1:
                continue
            if coef > 0:
                self.update_value(var, 1)
            elif coef < 0:
                self.update_value(var, 0)

    def set_to_min(self, expr):
        ''' Given an expression, update all terms so that it achieves it's minumum

            >>> system = EquationSolver()
            >>> expr = sympy.sympify('-2*x*y - 3 + 3*z23')
            >>> system.set_to_min(expr)
            >>> system.deductions
            {x*y: 1, z23: 0}

        '''
        coef_dict = expr.as_coefficients_dict()
        for var, coef in coef_dict.iteritems():
            if var == 1:
                continue
            if coef < 0:
                self.update_value(var, 1)
            elif coef > 0:
                self.update_value(var, 0)

    def apply_judgements_square(self, equations):
        ''' Pick out equations that we can square in a reasonable amount of
            time and apply the judgements to them
        '''
        eqn_sq = square_equations(equations, term_limit=20, method=1)
        
        pre = self._length_tuple
        pre_ded = self.deductions.copy()
        self.apply_judgements(eqn_sq)
        post = self._length_tuple
        post_ded = self.deductions.copy()
        
        if pre != post:
            pre_ded = set(pre_ded.iteritems())
            post_ded = set(post_ded.iteritems())
            
            print '{} -> {} after square application'.format(pre, post)
            print post_ded.symmetric_difference(pre_ded)

    def apply_judgements(self, equations):
        ''' Apply judgements to a list of sympy equations and directly update
            self.deductions
        '''
        for eqn in equations:
            self.judgement_0(eqn)
            self.judgement_prod(eqn)
            self.judgement_min_max(eqn)
            self.judgement_1(eqn)
            self.judgement_2(eqn)
            self.judgement_3(eqn)
            self.judgement_4(eqn)
            self.judgement_5(eqn)
            self.judgement_6(eqn)
            self.judgement_7(eqn)
            
            # Now look for contradictions
            self.contradiction_1(eqn)
            self.contradiction_2(eqn)

    def judgement_0(self, eqn):
        ''' Add x=y to deductions. This shouldn't be needed, but it's nice to
            make sure we're not missing anything obvious        
        '''
        if len(eqn.lhs.atoms()) == len(eqn.rhs.atoms()) == 1:
            if eqn.lhs.is_constant():
                self.update_value(eqn.rhs, eqn.lhs)
            else:
                self.update_value(eqn.lhs, eqn.rhs)

    def judgement_prod(self, eqn):
        ''' If RHS is a non-zero constant and the LHS is a product of variables,
            then the variables must all be 1
            x*y*z=1 => x = y = 1

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x * y * z, 1)
            >>> system.judgement_prod(eqn)
            >>> system.deductions
            {x: 1, z: 1, y: 1}

            >>> eqn = sympy.Eq(2*x*y*z, 2)
            >>> system = EquationSolver([eqn])
            >>> system.solve_equations()
            >>> system.solutions
            {x: 1, z: 1, y: 1}
            
            >>> eqn = sympy.Eq(2*x*y*z, 1)
            >>> system = EquationSolver()
            >>> system.judgement_prod(eqn)
            Traceback (most recent call last):
                ...
            ContradictionException: judgement_sq: 2*x*y*z == 1
        '''
        if eqn.rhs == 1:
            if len(eqn.lhs.as_ordered_terms()) == 1:
                if eqn.lhs.as_coeff_mul()[0] != 1:
                    err_str = 'judgement_sq: {}'.format(str(eqn))
                    raise ContradictionException(err_str)
                for var in eqn.lhs.atoms(sympy.Symbol):
                    self.update_value(var, 1)

    def judgement_two_term(self, eqn):
        ''' If an expression has 2 variable terms, sub it in!
            This adds lots of complexity so the infrastructure, which is
            why we don't do it with 3 or 4 term sums.
            
            Note this isn't applied to the equations directly, but is called
            via the parity judgement
            
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y*z, 1)
            >>> system.judgement_two_term(eqn)
            >>> system.deductions
            {x: -y*z + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 1)
            >>> system.judgement_two_term(eqn)
            >>> system.deductions
            {x: -y + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(1 + y, 1)
            >>> system.judgement_two_term(eqn)
            >>> system.deductions
            {y: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x - y, 1)
            >>> system.judgement_two_term(eqn)
            >>> system.deductions
            {x: y + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(-x + y, 1)
            >>> system.judgement_two_term(eqn)
            >>> system.deductions
            {x: y - 1}
        '''
        lhs, rhs = eqn.lhs, eqn.rhs
        if (num_add_terms(lhs) == 2) and is_constant(rhs):

            term1, term2 = lhs.as_ordered_terms()

            if ((0 < len(term2.atoms(sympy.Symbol)) < len(term1.atoms(sympy.Symbol)))
                or is_constant(term1)):
                self.update_value(term2, rhs - term1)
#                self.update_value(term1 + term2, rhs)

            else:
                self.update_value(term1, rhs - term2)
#                self.update_value(term1 + term2, rhs)

    def judgement_n_term(self, eqn, max_num_terms=3):
        ''' If an expression has n or fewer variable terms, sub it in!
            Only substitute single atoms terms for the moment

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y*z, 1)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {x: -y*z + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, z)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {x: -y + z}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(1 + y, 1)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {y: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x - y, 1)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {x: y + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(-x + y, 1)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {y: x + 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + z, 1)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {x: -y - z + 1}

            >>> system = EquationSolver()
            >>> x, y, z, z2, u, v = sympy.symbols('x y z z2 u v')
            >>> eqn = sympy.Eq(2*x + y + z*z2, u + v)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {y: u + v - 2*x - z*z2}

            >>> system = EquationSolver()
            >>> lhs = sympy.sympify('q2 + q3')
            >>> rhs = sympy.sympify('2*q2*q3 + 2*z2021')
            >>> eqn = sympy.Eq(lhs, rhs)
            >>> system.judgement_n_term(eqn)
            >>> system.deductions
            {q2: 2*q2*q3 - q3 + 2*z2021}
        '''
        lhs, rhs = eqn.lhs, eqn.rhs
        if (num_add_terms(lhs) <= max_num_terms):

            term_to_sub = None
            for term in lhs.as_ordered_terms():
                # We want a monic term of 1 variable if we haven't found one yet
                if ((len(term.atoms(sympy.Symbol)) == 1) and
                    (term.as_coeff_mul()[0] == 1) and
                    (term_to_sub is None)):
                    term_to_sub = term

            if term_to_sub is not None:
                self.update_value(term_to_sub, rhs - lhs + term_to_sub)


    def judgement_min_max(self, eqn):
        ''' If min(rhs) == max(lhs), then we know what to do

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + z, 3)
            >>> system.judgement_min_max(eqn)
            >>> system.deductions
            {x: 1, z: 1, y: 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 2*y, 5 - 2*z)
            >>> system.judgement_min_max(eqn)
            >>> system.deductions
            {x: 1, z: 1, y: 1}
        '''

        def _helper(self, lhs, rhs):
            if min_value(lhs) == max_value(rhs):
                self.set_to_min(lhs)
                self.set_to_max(rhs)

        _helper(self, eqn.lhs, eqn.rhs)
        _helper(self, eqn.rhs, eqn.lhs)

    def judgement_1(self, eqn):
        ''' If x + y + z = 1 then xy = yz = zx = 0
            Generally true for any number of terms that = 1

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + z, 1)
            >>> system.judgement_1(eqn)
            >>> system.deductions
            {x*z: 0, x*y: 0, y*z: 0}

            >>> system = EquationSolver()
            >>> x, y, z, z2 = sympy.symbols('x y z z2')
            >>> eqn = sympy.Eq(x + y + z + z2, 1)
            >>> system.judgement_1(eqn)
            >>> system.deductions
            {x*z: 0, z*z2: 0, y*z2: 0, x*z2: 0, y*z: 0, x*y: 0}
            
            >>> system = EquationSolver()
            >>> x, y, z, z2 = sympy.symbols('x y z z2')
            >>> eqn = sympy.Eq(x + 2*y + z*z2, 1)
            >>> system.judgement_1(eqn)
            >>> system.deductions
            {x*y: 0, x*z*z2: 0, y*z*z2: 0}
        '''
        if eqn.rhs != 1:
            return

        terms = eqn.lhs.as_ordered_terms()
        # We need more than 2 terms
        if len(terms) < 2:
            return

        # We also need every term to be positive
        for term in terms:
            if min_value(term) < 0:
                return

        pairs = itertools.combinations(terms, 2)
        for t1, t2 in pairs:
            self.update_value(t1*t2, 0)


#        if len(terms) == 3:
#            variables, coef = zip(*terms)
#            if all([c == 1 for c in coef]):
#                for v in itertools.permutations(variables, 2):
#                    self.update_value(v[0] * v[1], 0)

    def judgement_2(self, eqn):
        ''' If a term being 1 would tip max(rhs) > max(lhs), then it must be 0

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 2 + z)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {z: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 2 + z)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {z: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 3 * y, 2 + z)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 5 * y, 2 + z)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {y: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 1 + z)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {}

            >>> system = EquationSolver()
            >>> lhs = sympy.sympify('q3 + q4')
            >>> rhs = sympy.sympify('2*q3*q4 + 2*z78 + 4*z79')
            >>> eqn = sympy.Eq(lhs, rhs)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {z79: 0}
            
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x+y, 2*z + 1)
            >>> system.judgement_2(eqn)
            >>> system.deductions
            {z: 0}
        '''
        def _helper(lhs, rhs):
            lhs_max = max_value(lhs)

            rhs_terms = rhs.as_ordered_terms()
            if is_constant(rhs_terms[-1]):
                rhs_const = rhs_terms.pop()
            else:
                rhs_const = 0

            for term in rhs_terms:
                if max_value(term) + rhs_const > lhs_max:
                    self.set_to_min(term)

        _helper(eqn.lhs, eqn.rhs)
        _helper(eqn.rhs, eqn.lhs)


    def _judgement_2_old(self, eqn):
        ''' If max(lhs) < max(rhs) and there is only 1 variable term
            in the RHS, then this term must be 1

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x+y, 2*z + 1)
            >>> system._judgement_2_old(eqn)
            >>> system.deductions
            {z: 0}
        '''
        lhs, rhs = eqn.lhs, eqn.rhs
        if (max_value(lhs) < max_value(rhs)):
            terms = rhs.as_ordered_terms()
            if (len(terms) == 2) and terms[-1].is_constant():
                self.set_to_min(rhs)

    def judgement_3(self, eqn):
        ''' If max(lhs) = max(rhs) and rhs is constant, then every term on the
            left is 1.
            Similarly for minimum

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 2)
            >>> system.judgement_3(eqn)
            >>> system.deductions
            {x: 1, y: 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + z, 0)
            >>> system.judgement_3(eqn)
            >>> system.deductions
            {x: 0, z: 0, y: 0}
        '''
        def _helper(lhs, rhs):
            if not is_constant(rhs):
                return
            elif (max_value(lhs) == max_value(rhs)):
                self.set_to_max(lhs)
            elif (min_value(lhs) == min_value(rhs)):
                self.set_to_min(lhs)

        _helper(eqn.lhs, eqn.rhs)
        _helper(eqn.rhs, eqn.lhs)


    def judgement_4(self, eqn):
        ''' If min(LHS) > min(RHS) and we only have one variable term on the
            RHS, this must be 1

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 1, 2*z)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {z: 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 1, z)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {z: 1}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x * y + 1, 2 * z)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {z: 1}

            >>> system = EquationSolver()
            >>> x, y, z, z2 = sympy.symbols('x y z z2')
            >>> eqn = sympy.Eq(x + 3 * y + 1, 2 * z + z2)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {}
            
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 3 * y + 4, 5 * z)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {z: 1}
            
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + 3 * y + 4, 5)
            >>> system.judgement_4(eqn)
            >>> system.deductions
            {}
        '''
        def _helper(lhs, rhs):
            if (min_value(lhs) > min_value(rhs)):
                if (num_add_terms(rhs) == 1) and (not is_constant(rhs)):
                        self.update_value(rhs.as_coeff_Mul()[1], 1)
        
        _helper(eqn.lhs, eqn.rhs)
        _helper(eqn.rhs, eqn.lhs)

    def judgement_5(self, eqn):
        ''' Parity argument. If the RHS is always even and the LHS has 2 odd
            terms, then the variable parts of these terms must be equal.
            x + 2y + z = 4z2 -> x = z
            Also works with any even RHS and any number of even variables on
            the LHS.

            >>> system = EquationSolver()
            >>> x, y, z, z2 = sympy.symbols('x y z z2')
            >>> eqn = sympy.Eq(x + 2*y + z, 2*z2)
            >>> system.judgement_5(eqn)
            >>> system.deductions
            {x: z}

            >>> system = EquationSolver()
            >>> x, y, z, z2 = sympy.symbols('x y z z2')
            >>> eqn = sympy.Eq(x + 2*y + 3*z, 2 * z2)
            >>> system.judgement_5(eqn)
            >>> system.deductions
            {x: z}
        '''

        def _helper(lhs, rhs):
            if parity(rhs) != 0:
                return

            odd_terms = []
            for term in lhs.as_ordered_terms():
                term_coef, term = term.as_coeff_Mul()
                if (term_coef % 2):
                    if len(odd_terms) > 2:
                        return
                    else:
                        odd_terms.append(term)

            if len(odd_terms) == 2:
                if is_constant(odd_terms[0]):
                    self.update_value(odd_terms[1], odd_terms[0])
                else:
                    self.update_value(*odd_terms)

        _helper(eqn.lhs, eqn.rhs)
        _helper(eqn.rhs, eqn.lhs)

    def judgement_6(self, eqn):
        ''' Parity argument. If RHS is always odd and the LHS has 2 monic terms
            then the sum must be 1.
            Also make sure we don't replicate judgement_two_term

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + 2*x*y, 2 * z + 1)
            >>> system.judgement_6(eqn)
            >>> system.deductions
            {x: -y + 1}

            >>> system = EquationSolver()
            >>> x, y = sympy.symbols('x y')
            >>> eqn = sympy.Eq(x + y, 1)
            >>> system.judgement_6(eqn)
            >>> system.deductions
            {x: -y + 1}
            
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + 2*z, 3)
            >>> system.judgement_6(eqn)
            >>> system.deductions
            {x: -y + 1}
        '''
        lhs, rhs = eqn.lhs, eqn.rhs
        if parity(rhs) == 1:
            odd_terms = []
            for term, term_coef in lhs.as_coefficients_dict().iteritems():
                if (term_coef % 2):
                    if term.is_constant():
                        return
                    odd_terms.append(term)

            if len(odd_terms) == 2:
                self.judgement_two_term(sympy.Eq(sum(odd_terms), 1))

    def judgement_7(self, eqn):
        ''' Special case of judgement_5
            x + y = 2z -> x = y = z

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y, 2*z)
            >>> system.judgement_7(eqn)
            >>> system.deductions
            {z: x, y: x}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x * y + x, 2 * z)
            >>> system.judgement_7(eqn)
            >>> system.deductions
            {x*y: x, z: x*y}
        '''
        lhs, rhs = eqn.lhs, eqn.rhs
        if len(lhs.as_ordered_terms()) == 2:
            t1, t2 = lhs.as_ordered_terms()
            if ((t1.as_coeff_mul()[0] == 1) and
                (not t1.is_constant()) and
                (t2.as_coeff_mul()[0] == 1) and
                (not t2.is_constant()) and
                (rhs.as_coeff_mul()[0] == 2)):
                self.update_value(t2, t1)
                self.update_value(rhs / 2, t1)



    def _judgement_10i(self, eqn):
        ''' x + y + z + 2a = 2b -> xy + xz = yx + yz = zx + zy = 0
            Also works with any even RHS and any number of even variables on
            the LHS.

            >>> system = EquationSolver()
            >>> x, y, z, z2, z3 = sympy.symbols('x y z z2, z3')
            >>> eqn = sympy.Eq(x + 2*y + z + z2, 2*z3)
            >>> system._judgement_10i(eqn)
            >>> system.deductions
            {x*z2 + z*z2: 0, x*z + x*z2: 0, x*z + z*z2: 0}

            >>> system = EquationSolver()
            >>> eqn = sympy.Eq(x + 2*y + 3*z + 7 * z2, 4*z3)
            >>> system._judgement_10i(eqn)
            >>> system.deductions
            {x*z2 + z*z2: 0, x*z + x*z2: 0, x*z + z*z2: 0}

            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> eqn = sympy.Eq(x + y + 1, 2 * z)
            >>> system._judgement_10i(eqn)
            >>> system.deductions
            {}
        '''

        def _helper(lhs, rhs):
            for term in rhs.as_ordered_terms():
                if term.as_coeff_mul()[0] % 2:
                    return

            odd_terms = []
            for term in lhs.as_ordered_terms():
                term_coef, term = term.as_coeff_Mul()
                if (term_coef % 2):
                    if len(odd_terms) > 3:
                        return
                    else:
                        if term.is_constant():
                            return
                        odd_terms.append(term)

            if len(odd_terms) == 3:
                pairs = [a * b for a, b in itertools.combinations(odd_terms, 2)]
                expressions = [ab + bc for ab, bc in itertools.combinations(pairs, 2)]
                for expr in expressions:
                    self.update_value(expr, 0)


        _helper(eqn.lhs, eqn.rhs)
        _helper(eqn.rhs, eqn.lhs)




    ## Look for contradictions
    def contradiction_1(self, eqn):
        ''' Check the values could be equal 
        
        >>> x, y, z = sympy.symbols('x y z')

        >>> eqn = sympy.Eq(x*y*z - 2)
        >>> system = EquationSolver(equations=[eqn])
        >>> system.solve_equations()
        Traceback (most recent call last):
            ...
        ContradictionException: contradiction_1: x*y*z == 2

        >>> eqn = sympy.Eq(x*y*z)
        >>> system = EquationSolver(equations=[eqn])
        >>> system.solve_equations()
        >>> system.solutions
        {}
        

        >>> eqn = sympy.Eq(x*y*z + 1)
        >>> system = EquationSolver()
        >>> system.contradiction_1(eqn)
        Traceback (most recent call last):
            ...
        ContradictionException: contradiction_1: x*y*z + 1 == 0
        '''
        def _helper(self, lhs, rhs):
            if min_value(lhs) > max_value(rhs):
                raise ContradictionException('contradiction_1: {}'.format(eqn))

        _helper(self, eqn.lhs, eqn.rhs)
        _helper(self, eqn.rhs, eqn.lhs)
    
    def contradiction_2(self, eqn):
        ''' Check the parity 
        
        >>> x, y, z = sympy.symbols('x y z')
        
        >>> eqn = sympy.Eq(2*x*y + 4*z + 1)
        >>> system = EquationSolver()
        >>> system.contradiction_2(eqn)
        Traceback (most recent call last):
            ...
        ContradictionException: contradiction_2: 2*x*y + 4*z + 1 == 0
        
        >>> eqn = sympy.Eq(2*x*y * 4*z - 2)
        >>> system = EquationSolver()
        >>> system.contradiction_2(eqn)

        >>> eqn = sympy.Eq(2*x*y + z - 1)
        >>> system = EquationSolver()
        >>> system.contradiction_2(eqn)
        '''
        l_parity = parity(eqn.lhs)
        if l_parity is not None:
            r_parity = parity(eqn.rhs)
            if (r_parity is not None) and (l_parity != r_parity):
                raise ContradictionException('contradiction_2: {}'.format(eqn))

## Parsing and set up

def parse_equations(params):
    '''
        params[0][i] contains all leftsides
        params[1][i] contains all rightsides
    '''
    eqns = []
    variables = {}
    for lhs, rhs in itertools.izip(*params):
        lhs = _parse_expression(lhs, variables)
        rhs = _parse_expression(rhs, variables)
        eqn = sympy.Eq(lhs, rhs)
        eqns.append(eqn)

    return eqns, variables

def _parse_expression(expr, variables):
    ''' Take a list of terms, clean them up and return a sympy expression in terms
        of the global variables '''
    out = 0
    for term in expr:
        if term == '':
            continue
        out += _parse_term(term, variables)
    return out

def _parse_term(term, variables):
    ''' Take a term and clean it, replacing variables '''
    coef = int(ReHandler.get_coefficient(term))
    var = ReHandler.get_variables(term)
    var_instances = []
    for v in var:
        instance = variables.get(v)
        if instance is None:
            instance = sympy.symbols(v)
            variables[v] = instance
        var_instances.append(instance)
    return coef * sympy.prod(var_instances)

## Conflict resolution
def _simplest(expr1, expr2):
    ''' Return the simplest of expr1 and expr2, giving precedence to expr1.
        Used by the solver when we have 2 different symbolic values and we
        want to determine which to assign.
        ASSUMES THE FIRST ARGUMENT IS THE OLD VALUE
    '''
    if len(expr2.atoms()) > len(expr1.atoms()):
        return expr2
    else:
        return expr1

# Inspection
def _get_judgement():
    ''' Find the judgement calling update_value. Horrible hackery, but at least
        it's in 1 place
    '''
    up_again = ['set_to_min', 'set_to_max', 'update_value', '_helper',
                'judgement_two_term']

    ind = 3
    frame = inspect.stack()[ind]
    caller_name = frame[-3]
    while caller_name in up_again:
        ind += 1
        frame = inspect.stack()[ind]
        caller_name = frame[-3]
        if ind > 100:
            break

    eqn = frame[0].f_locals.get('eqn')
    return caller_name, eqn


if __name__ == "__main__":
    import doctest
    doctest.testmod()
