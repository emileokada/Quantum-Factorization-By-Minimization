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

from contradiction_exception import ContradictionException
from contradictions import apply_contradictions
from judgement_mixin import JudgementMixin
from solver_base import SolverBase, unique_array_stable
from sympy_helper_fns import (max_value, min_value, is_equation,
                              remove_binary_squares_eqn, balance_terms,
                              cancel_constant_factor, is_constant,
                              num_add_terms, parity, is_monic, is_one_or_zero,
                              remove_binary_squares, expressions_to_variables,
                              gather_monic_terms, square_equations,
                              str_eqns_to_sympy_eqns, standardise_equation,
                              is_simple_binary, dict_as_eqns)
from objective_function_helper import (equations_to_vanilla_coef_str, 
                                       equations_to_vanilla_objective_function,
                                       equations_to_auxillary_coef_str)

from sympy_paralleliser import paralellised_subs, get_pool, DEFAULT_NUM_PROCESSES


__author__ = "Richard Tanburn"
__credits__ = ["Richard Tanburn", "Nathaniel Bryans", "Nikesh Dattani"]
__version__ = "0.0.1"
__status__ = "Prototype"

# Maximum number of equations before quadratic equality checking kicks in
EQUATION_QUADRATIC_LIMIT = 350


class EquationSolver(SolverBase, JudgementMixin):
    ''' Solver of equations '''

    def __init__(self, equations=None, variables=None, log_deductions=False,
                 output_filename=None, invariant_interactions_on_substitution=True,
                 parallelise=False):

        super(EquationSolver, self).__init__(equations=equations, 
                variables=variables, output_filename=output_filename,
                parallelise=False)

        # Set of deductions we have made
        self.deductions = {}

        # And keep a nested dictionary of who made them, if we want
        self.log_deductions = log_deductions
        self.deduction_record = defaultdict(lambda : defaultdict(list))
        
        # if invariant_interactions_on_substitution is True, then only
        # substitute x = (1-y + z1) type deductions, where each term on the
        # RHS has 1 atom
        self.invariant_interactions_on_substitution = invariant_interactions_on_substitution
        
    def copy(self):
        ''' Return a new instance of itself '''
        copy = EquationSolver(deepcopy(self.equations), 
                              deepcopy(self.variables),
                              log_deductions=self.log_deductions, 
                              output_filename=self.output_filename,
                              parallelise=self.parallelise)
                              
        # Now use deepcopy to copy everything else
        copy.num_qubits_start = self.num_qubits_start
        copy.deductions = deepcopy(self.deductions)
        copy.solutions = deepcopy(self.solutions)
        copy.deduction_record = deepcopy(self.deduction_record)
        copy.invariant_interactions_on_substitution = self.invariant_interactions_on_substitution
        return copy

    # Pickling
    # This isn't mature/finished, but manages to write equations, deductions and
    # solutions to disk
    def __getstate__(self):
        return (self.equations, self.deductions, self.solutions, 
                self.invariant_interactions_on_substitution, self.log_deductions,
                # We can't pickle defaultdicts apparently
                dict(self.deduction_record), self.variables, self.num_qubits_start,
                self.output_filename, self._file, self.parallelise)
        
    def __setstate__(self, state):
        (self.equations, self.deductions, self.solutions, 
         self.invariant_interactions_on_substitution, self.log_deductions,
         deduction_record, self.variables, self.num_qubits_start,
         self.output_filename, self._file, self.parallelise) = state
         
        # Re cast to a defaultdict
        self.deduction_record = defaultdict(lambda : defaultdict(list))
        for k, v in deduction_record.iteritems():
            self.deduction_record[k] = v
    
    @property
    def deductions_as_equations(self):
        ''' Return deductions as a list of equations '''
        new_equations = dict_as_eqns(self.deductions)
        new_equations = filter(is_equation, new_equations)        
        new_equations = [eqn.expand() for eqn in new_equations]
        new_equations = map(remove_binary_squares_eqn, new_equations)
        new_equations = map(balance_terms, new_equations)
        new_equations = map(cancel_constant_factor, new_equations)
        new_equations = filter(is_equation, new_equations)
        return new_equations
    
    def print_deduction_log(self):
        ''' Print the judgements and the deductions they have made '''
        to_skip = ['clean_deductions', 'clean_solutions']
        for judgement in sorted(self.deduction_record.keys()):
            if judgement in to_skip:
                continue
            ded_info = self.deduction_record[judgement]
            self.print_('\n' + judgement)
            for eqn, deds in ded_info.iteritems():
                eqn_str = str(eqn).ljust(25)
                ded_str = map(lambda (x, y) : '{}={}'.format(x, y), deds)
                ded_str = ', '.join(ded_str)
                self.print_('{}\t=>\t{}'.format(eqn_str, ded_str))
        self.print_('\n')

    @property    
    def _length_tuple(self):
        ''' Return a tuple of the lengths of equations, deductions, solutions 
        '''
        return len(self.equations), len(self.deductions), len(self.solutions)

    def solve_equations(self, max_iter=250, verbose=False):
        ''' Solve a system of equations
        '''
        state_summary = self._length_tuple
        # The number of iterations in which we've made no new deductions
        num_constant_iter = 0

        # Keep track of the last few number of solutions, so that if we get
        # too many repeats we can break the cycle
        prev_num_solns = []

        if verbose:        
            self.print_('Num variables: {}'.format(len(self.variables)))
            self.print_('Iter\tNum Eqn\tNum Ded\tNum Sol')
        for i in xrange(max_iter):
            # Check we're not going around in circles
            prev_num_solns.append(self._length_tuple[2])
            if len(prev_num_solns) > 10:
                comp = prev_num_solns.pop(0)
                if all([comp-1 <= v <= comp+1 for v in prev_num_solns]):
                    break            

            # Clear the cache so that we don't blow up memory when working with
            # large numbers
            clear_cache()

            if verbose:
                self.print_('\t'.join(['{}'] * 4).format(i, *state_summary))

            self.equations = self.clean_equations(self.equations)
            
            # Extract all the equations from the system
            all_equations = self.final_equations

            all_equations.extend(self.non_trivial_solns)

            self.apply_contradictions(all_equations)
            self.apply_judgements(all_equations)

            # Slightly mysterious clean that fixes judgement blow up.
            # Something to do with the way clean_solutions cleans cycling imports,
            # cleaning re-updates values in the deductions and such.
            self.clean_deductions()

            if self._length_tuple == state_summary:
                num_constant_iter += 1

                # Here lets apply some slower, complex judgements to try and
                # unstick ourselves
                self.apply_judgements_complex(all_equations, num_constant_iter,
                                              verbose=verbose)

                if num_constant_iter > 4 or (self._length_tuple[:2] == (0, 0)):
                    break
                
                self.clean_deductions()

            if self._length_tuple != state_summary:
                num_constant_iter = 0
                state_summary = self._length_tuple

        # Final clean again, for good luck
        self.equations = self.clean_equations(self.equations)
        # and clear the cache for future generations
        clear_cache()
        
        # Close the pool
        self.close_pool()

    @property
    def final_equations(self):
        ''' final_equations are the final filtered equations that also
            include deductions
        '''
        final_equations = self.equations + self.deductions_as_equations
        final_equations = sorted(set(final_equations), key=str)
        return final_equations

    @property
    def non_trivial_solns(self):
        ''' A list of everything from self.solutions that might hold 
            information
            
            >>> a, b, c, u, v, x, y, z = sympy.symbols('a b c u v x y z')
            >>> system = EquationSolver()
            >>> solutions = {a: 1, b: c, u: 1 - v, x: y*z, y: x - 2*z}
            >>> system.solutions = solutions
            >>> system.non_trivial_solns
            [y + 2*z == x, x == y*z]
        '''
        # Now fetch non-trivial solutions
        non_trivial_soln = []
        for variable, soln in self.solutions.iteritems():
            # If we've found the solution, don't bother trying to apply
            # judgements to it                
            if is_simple_binary(soln):
                continue
            non_trivial_soln.append(sympy.Eq(variable, soln))
        non_trivial_soln = map(remove_binary_squares_eqn, non_trivial_soln)
        non_trivial_soln = map(balance_terms, non_trivial_soln)
        non_trivial_soln = map(cancel_constant_factor, non_trivial_soln)
        non_trivial_soln = filter(is_equation, non_trivial_soln)
        return non_trivial_soln

    def print_summary(self):
        ''' Print a summary of the information held in the object '''
        # Print the p and q solution
#        pqs = {}
#        zs = {}
#        self.print_('p, q Solutions')
#        for var, sol in self.solutions.iteritems():
#            svar = str(var)
#            if svar.startswith('p') or svar.startswith('q'):
#                pqs[var] = sol
#            else:
#                zs[var] = sol
#        for k in sorted(pqs.keys(), key=str):
#            self.print_('{} = {}'.format(k, pqs[k].subs(zs)))

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

        super(EquationSolver, self).print_summary()        
        


#        self.print_('Final coefficients')
#        self.print_(equations_to_coef_string(self.final_equations), close=True)

    def clean_equations(self, eqns):
        ''' Remove True equations and simplify '''
        # First clean up the deductions so we can use them
        self.clean_deductions()

        cleaned = filter(is_equation, eqns[:])

        # Extract only the atoms we would like to try and find
        if len(cleaned):
            cleaned_atoms = expressions_to_variables(cleaned)
            cleaned_sol = ((var, self.solutions.get(var)) for var in cleaned_atoms)
            cleaned_sol = filter(lambda x: x[1] is not None, cleaned_sol)
            cleaned_sol = {x[0]: x[1] for x in cleaned_sol}
        else:
            cleaned_sol = {}

        # Combine all combinations into one dict, giving priority to cleaned_sol        
        combined_subs = self.deductions.copy()
        combined_subs.update(cleaned_sol)

        cleaned = self.batch_substitutions(cleaned, combined_subs)

        cleaned = filter(is_equation, cleaned)
        cleaned = [eqn.expand() for eqn in cleaned]
        cleaned = map(remove_binary_squares_eqn, cleaned)
        cleaned = map(balance_terms, cleaned)
        cleaned = map(cancel_constant_factor, cleaned)
        cleaned = filter(is_equation, cleaned)

        if len(cleaned) < EQUATION_QUADRATIC_LIMIT:
            to_add = []
            # Now add any equations where LHS = RHS1, LHS = RHS2 and permutations
            def _helper(eqn1, eqn2, to_add):
                if ((eqn1.lhs == eqn2.lhs) and
                    (eqn1.rhs != eqn2.rhs) and
                    (not is_constant(eqn1.lhs))):
                    new_eq = sympy.Eq(eqn1.rhs, eqn2.rhs)
                    new_eq = balance_terms(new_eq)
                    
                    # Try only adding stuff with more than one additive term
                    if num_add_terms(new_eq.lhs) == num_add_terms(new_eq.rhs) == 1:
                        return
                    
                    to_add.append(new_eq)
#                    self.print_('Equation added! {}, {}\t=>\t{}'.format(eqn1, eqn2, new_eq))
    
            all_equations = itertools.chain(cleaned, self.deductions_as_equations)
            for eqn1, eqn2 in itertools.combinations(all_equations, 2):
                _helper(eqn1, eqn2, to_add)
                _helper(sympy.Eq(eqn1.rhs, eqn1.lhs), eqn2, to_add)
                _helper(eqn1, sympy.Eq(eqn2.rhs, eqn2.lhs), to_add)
                _helper(sympy.Eq(eqn1.rhs, eqn1.lhs), sympy.Eq(eqn2.rhs, eqn2.lhs),
                        to_add)
            to_add = filter(is_equation, to_add)
            cleaned.extend(to_add)

        return unique_array_stable(cleaned)

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
        unsolved_var = self.unsolved_var

        # Clean the solutions so we don't spend so long in subs
        # Extract only the atoms we would like to try and find
        ded_as_eqn = self.deductions_as_equations
        if len(ded_as_eqn):
            cleaned_atoms = expressions_to_variables(ded_as_eqn)
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
            val = remove_binary_squares(val)
            latoms = expr.atoms()
            ratoms = val.atoms()
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
                    if expr != val:
                        self.print_('Dropping deduction {} = {}'.format(expr, val))

    def clean_solutions(self, _prev_changed=None, _depth=0):
        ''' Remove cycles and chains in the solutions. Make sure every value is
            set to equal an expression involving constants and unsolved variables
            
            _prev_changed allows the recursive calling to keep track of cycles

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

            Non-trival cyclic solutions. Little bit rough around the edges.
            Keep an eye on it
            >>> system = EquationSolver()
            >>> x, y, z = sympy.symbols('x y z')
            >>> soln = {x: 1 - y, y: 1 - x}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.equations
            [x + y == 1]
            >>> system.deductions
            {}
            >>> system.solutions
            {y: -x + 1}

            >>> system = EquationSolver()
            >>> soln = {z: - x + 2, x: - y + 1, y: z - 1}
            >>> system.solutions = soln
            >>> system.clean_solutions()
            >>> system.equations
            [x + y == 1]
            >>> system.deductions
            {}
            >>> system.solutions
            {z: -x + 2, y: -x + 1}

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
        #TODO Make the solver handle cycles properly!!!

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
        
        # Now go through and do some standard checks and cleaning
        for variable, value in self.solutions.copy().iteritems():
            # Remove binary squares
            self.solutions[variable] = remove_binary_squares(value)

            # Now pop anything with a crazy number of terms
            #TODO Put in a config file
            #TODO Add equation method??
            #TODO Pop anything that's not simple_binary??
            if num_add_terms(value) > 10:
                self.solutions.pop(variable)
                new_eq = standardise_equation(sympy.Eq(variable, value))
                self.equations.append(new_eq)
            
            # Now make sure every value in the dict can be binary, throwing if
            # not
            if (max_value(value) < 0) or (min_value(value) > 1):
                err_str = 'clean_solutions: {} != {}'.format(variable, value)
                raise ContradictionException(err_str)
            # Now add an equation if it is written in terms of more than 3
            # other variables
#            if len(value.atoms(sympy.Symbol)) >= 3:
#                print 'Adding {} = {}'.format(variable, value)
#                self.update_value(variable, value)
#                self.solutions.pop(variable)

        changed = set()
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
                    if value == variable:
                        value = old_value
                        changed.add(variable)
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
                changed.add(variable)

            new_solutions[variable] = value

        self.solutions = new_solutions

        if len(changed):
            #TODO Find a smarter way of choosing which one to pop?
            #TODO Add equation method??
            if changed == _prev_changed:
                var = changed.pop()
                self.equations.append(standardise_equation(sympy.Eq(var, self.solutions[var])))
                self.solutions.pop(var)
            if _depth > 50:
                raise RuntimeError('Crazy depths in clean_solutions!')
            self.clean_solutions(_prev_changed=changed, _depth=_depth+1)

    def _update_log(self, expr, value):
        ''' Log an update under a judgement '''
        if not self.log_deductions:
            return

        # Update the deductions process dictionary
        judgement, eqn = _get_judgement()
        self.deduction_record[judgement][eqn].append((expr, value))

    def add_solution(self, variable, value):
        ''' Override the base implementation to use update_value '''
        self.update_value(variable, value)

    def update_value(self, expr, value):
        ''' Update the global dictionary and check for contradictions.
            Make sure expr is always 'positive'.
            NOTE Only accepts single terms for expr

            >>> system = EquationSolver()
            >>> x = sympy.symbols('x')
            >>> system.update_value(x, 0)
            >>> system.deductions
            {x: 0}
            >>> system.update_value(x, 1)
            Traceback (most recent call last):
                ...
            ContradictionException: x is already set to 0 != 1

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
        # First do some preprocessing to make sure the deduction is in a
        # reasonably nice form
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
        
        # Remember, we live in binary land
        expr = remove_binary_squares(expr)
        value = remove_binary_squares(value)

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
            if is_constant(value):
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
            if is_constant(value):
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

    def apply_judgements_square(self, equations, verbose=False):
        ''' Pick out equations that we can square in a reasonable amount of
            time and apply the judgements to them
        '''
        pre = self._length_tuple
        eqn_sq1 = square_equations(equations, term_limit=20, method=1)
        self.apply_judgements(eqn_sq1)

        eqn_sq2 = square_equations(equations, term_limit=20, method=2)
        self.apply_judgements(eqn_sq2)
        post = self._length_tuple

        # If we didn't find anything, try the first round of complex judgements        
        if pre == post:
            self.apply_judgements_complex(eqn_sq1, num_constant_iter=1)
            self.apply_judgements_complex(eqn_sq2, num_constant_iter=1)

            post = self._length_tuple
            if verbose:
                num_ded = post[1] - pre[1]
                print '{} deductions made from squaring and complex judgements'.format(num_ded)

        elif verbose:
            num_ded = post[1] - pre[1]
            print '{} deductions made from squaring'.format(num_ded)

    def apply_judgements_complex(self, equations, num_constant_iter, 
                                 verbose=False):
        ''' Apply more complex or slow judgements if we get stuck.
            num_constant_iter is the number of iterations that we have been
            stuck for.
        '''
        if num_constant_iter == 0:
            return
        state_summary = self._length_tuple
        
        if num_constant_iter > 0:
            # Use mini-assumptions
            # Do for every stuck iteration, but with more variables
            for eqn in equations:
                # Limit the substitutions at 2^6=64
                num_var = min(3*num_constant_iter + 2, 6)
                # Rank by number of times each occurs
                self.judgement_mini_assumption(eqn, num_var=num_var, 
                                               coef_transform=lambda x: pow(x, 0.01))
                # Rank by sum of coefficients
                self.judgement_mini_assumption(eqn, num_var=num_var,
                                               coef_transform=lambda x: pow(x, 1.01))

        if num_constant_iter == 1:
            # Use the multi-equation mini-assumption to find simple
            # correspondences between simple equations
            num_var = 5
            max_num_eqn = 3
#            filter_func = lambda eq: eq.atoms(sympy.Symbol) < 5
            filter_func = lambda eq: (num_add_terms(eq.lhs) + num_add_terms(eq.rhs)) < 5
            short_equations = filter(filter_func, equations)
            # Make sure we do something with the short equations
            if len(short_equations) < max_num_eqn:
                eqn_comb = [short_equations]
            else:
                eqn_comb = itertools.combinations(short_equations, max_num_eqn)

            for comb in eqn_comb:
                self.judgement_mini_assumption_multi_eqn(comb, num_var=num_var,
                                                         coef_transform=lambda x: pow(x, 0.01),
                                                         cutoff=0.01)
                self.judgement_mini_assumption_multi_eqn(comb, num_var=num_var,
                                                         coef_transform=lambda x: pow(x, 1.01),
                                                         cutoff=0.01)
            
            for eqn in equations:
                # Apply the slow judgement 8 and 2
                self.judgement_2_slow(eqn)
                self.judgement_8_slow(eqn)

                # Apply the judgements that may add complexity
                self.judgement_5(eqn, increase_complexity=True)
                self.judgement_6(eqn, increase_complexity=True)
                self.judgement_9(eqn, increase_complexity=True)

        # Now apply judgements to the squares of the equations
        # Since applying judgements to the square of the equations 
        # doesn't change behaviour as we get stuck, just apply it once
        if num_constant_iter == 2:
            self.apply_judgements_square(equations, verbose=verbose)

        if num_constant_iter > 2:
            # Unleash the multiple equation mini-assumption
            #TODO put in a config or improve filtering
            num_var = min(3 * num_constant_iter + 2, 6)
            num_eqn = max(2, int(num_constant_iter / 2.0))
            eqn_comb = itertools.combinations(equations, num_eqn)
            for comb in eqn_comb:
                self.judgement_mini_assumption_multi_eqn(comb, num_var=num_var,
                                                         coef_transform=lambda x: pow(x, 0.01))
                self.judgement_mini_assumption_multi_eqn(comb, num_var=num_var,
                                                         coef_transform=lambda x: pow(x, 1.01))

            # Only do 1 at a time, so if we have a new deduction
            # go round again
#            if self._length_tuple != state_summary:
#                return

            for eqn in equations:
                self.judgement_n_term(eqn, num_constant_iter + 2)
        
        if num_constant_iter > 3:
            # If we don't need the final sledgehammer arguments, then carry on
#            if self._length_tuple != state_summary:
#                return

#            if (not self.invariant_interactions_on_substitution):
            for eqn in equations:


                self.judgement_5(eqn, increase_complexity=True, 
                                 invariant_interactions_on_substitution=False)
                self.judgement_6(eqn, increase_complexity=True, 
                                 invariant_interactions_on_substitution=False)
                self.judgement_9(eqn, increase_complexity=True, 
                                 invariant_interactions_on_substitution=False)


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
            self.judgement_5(eqn, increase_complexity=False)
            self.judgement_6(eqn, increase_complexity=False)
            self.judgement_7(eqn)
            self.judgement_8(eqn)
            self.judgement_9(eqn)
            
    def apply_contradictions(self, equations):
        ''' Now look for contradictions in the equations 
        
            >>> x, y, z = sympy.symbols('x y z')
    
            >>> eqn = sympy.Eq(x*y*z, 2)
            >>> system = EquationSolver([eqn])
            >>> system.solve_equations()
            Traceback (most recent call last):
                ...
            ContradictionException: contradiction_1: x*y*z == 2
    
            >>> eqn = sympy.Eq(x*y*z)
            >>> system = EquationSolver(equations=[eqn])
            >>> system.solve_equations()
            >>> system.solutions
            {}

            >>> eqn = sympy.Eq(2*x*y + 4*z, 1)
            >>> system = EquationSolver([eqn])
            >>> system.solve_equations()
            Traceback (most recent call last):
                ...
            ContradictionException: contradiction_2: 2*x*y + 4*z == 1
        '''
        apply_contradictions(equations)



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

    if caller_name == 'judgement_mini_assumption_multi_eqn':
        eqn = frame[0].f_locals.get('eqns')
    else:
        eqn = frame[0].f_locals.get('eqn')
    return caller_name, eqn


if __name__ == "__main__":
    import doctest
    doctest.testmod()
