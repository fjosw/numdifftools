"""numerical differentiation functions:
Derivative, Gradient, Jacobian, and Hessian

Author : pbrod, josef-pkt
License : BSD
Notes
-----
These are simple forward differentiation, so that we have them available
without dependencies.
* Jacobian should be faster than numdifftools.core because it doesn't use loop
  over observations.
* numerical precision will vary and depend on the choice of stepsizes
"""

# TODO:
# * some cleanup
# * check numerical accuracy (and bugs) with numdifftools and analytical
#   derivatives
#   - linear least squares case: (hess - 2*X'X) is 1e-8 or so
#   - gradient and Hessian agree with numdifftools when evaluated away from
#     minimum
#   - forward gradient, Jacobian evaluated at minimum is inaccurate, centered
#     (+/- base_step) is ok
# * dot product of Jacobian is different from Hessian, either wrong example or
#   a bug (unlikely), or a real difference
#
#
# What are the conditions that Jacobian dotproduct and Hessian are the same?
#
# See also:
#
# BHHH: Greene p481 17.4.6,  MLE Jacobian = d loglike / d beta , where loglike
# is vector for each observation
#    see also example 17.4 when J'J is very different from Hessian
#    also does it hold only at the minimum, what's relationship to covariance
#    of Jacobian matrix
# http://projects.scipy.org/scipy/ticket/1157
# http://en.wikipedia.org/wiki/Levenberg%E2%80%93Marquardt_algorithm
#    objective: sum((y-f(beta,x)**2),   Jacobian = d f/d beta
#    and not d objective/d beta as in MLE Greene similar:
# http://crsouza.blogspot.com/2009/11/neural-network-learning-by-levenberg_18.html#hessian
#
# in example: if J = d x*beta / d beta then J'J == X'X
#    similar to
#    http://en.wikipedia.org/wiki/Levenberg%E2%80%93Marquardt_algorithm
from __future__ import print_function
import numpy as np
from numdifftools.core import dea3
from collections import namedtuple
from matplotlib import pyplot as plt
from numdifftools.multicomplex import bicomplex
from numdifftools.test_functions import get_test_function, function_names
from numpy import linalg
from scipy import misc
from scipy.ndimage.filters import convolve1d
import warnings
# NOTE: we only do double precision internally so far
EPS = np.MachAr().eps


def _make_exact(h):
    '''Make sure h is an exact representable number
    This is important when calculating numerical derivatives and is
    accomplished by adding 1 and then subtracting 1..
    '''
    return (h + 1.0) - 1.0


def default_scale(method='forward', n=1):
    is_odd = (n % 2) == 1
    return (dict(multicomplex=1.35, complex=1.35).get(method, 2.5) +
            int((n - 1)) * dict(multicomplex=0, complex=0.0).get(method, 1.3) +
            is_odd * dict(complex=2.65*int(n//2)).get(method, 0) +
            (n % 4 == 2) * dict(complex=3.65 + (n//4) * 8).get(method, 0) +
            (n % 4 == 0) * dict(complex=(n//4) * (10 + 1.5*int(n > 10))
                                ).get(method, 0))


def _default_base_step(x, scale, epsilon=None):
    if epsilon is None:
        h = (10 * EPS) ** (1. / scale) * np.maximum(np.log1p(np.abs(x)), 1)
    else:
        if np.isscalar(epsilon):
            h = np.ones(x.shape) * epsilon
        else:
            h = np.asarray(epsilon)
            if h.shape != x.shape:
                raise ValueError("If h is not a scalar it must have the same"
                                 " shape as x.")
    return h


_CENTRAL_WEIGHTS_AND_POINTS = {
    (1, 3): (np.array([-1, 0, 1]) / 2.0, np.arange(-1, 2)),
    (1, 5): (np.array([1, -8, 0, 8, -1]) / 12.0, np.arange(-2, 3)),
    (1, 7): (np.array([-1, 9, -45, 0, 45, -9, 1]) / 60.0, np.arange(-3, 4)),
    (1, 9): (np.array([3, -32, 168, -672, 0, 672, -168, 32, -3]) / 840.0,
             np.arange(-4, 5)),
    (2, 3): (np.array([1, -2.0, 1]), np.arange(-1, 2)),
    (2, 5): (np.array([-1, 16, -30, 16, -1]) / 12.0, np.arange(-2, 3)),
    (2, 7): (np.array([2, -27, 270, -490, 270, -27, 2]) / 180.0,
             np.arange(-3, 4)),
    (2, 9): (np.array([-9, 128, -1008, 8064, -14350,
                      8064, -1008, 128, -9]) / 5040.0,
             np.arange(-4, 5))}


def fornberg_weights_all(x, x0, M=1):
    '''
    Return finite difference weights_and_points for derivatives
    of all orders 0, 1, ..., m

    Parameters
    ----------
    x : vector, length n
        x-coordinates for grid points
    x0 : scalar
        location where approximations are to be accurate
    m : scalar integer
        highest derivative that we want to find weights_and_points for

    Returns
    -------
    C :  array, shape n x m+1
        contains coefficients for the j'th derivative in column j (0 <= j <= m)

    See also:
    ---------
    fornberg_weights

    References
    ----------
    B. Fornberg (1998)
    "Calculation of weights_and_points in finite difference formulas",
    SIAM Review 40, pp. 685-691.

    http://www.scholarpedia.org/article/Finite_difference_method
    '''
    N = len(x)
    if M >= N:
        raise ValueError('length(x) must be larger than m')

    c1, c4 = 1, x[0] - x0
    C = np.zeros((N, M + 1))
    C[0, 0] = 1
    for n in range(1, N):
        m = np.arange(0, min(n, M) + 1)
        c2, c5, c4 = 1, c4, x[n] - x0
        for v in range(n):
            c3 = x[n] - x[v]
            c2, c6, c7 = c2 * c3, m * C[v, m-1], C[v, m]
            C[v, m] = (c4 * c7 - c6) / c3
        else:
            C[n, m] = c1 * (c6 - c5 * c7) / c2
        c1 = c2
    return C


def fornberg_weights(x, x0, m=1):
    '''
    Return weights for finite difference approximation of the m'th derivative
    U^m(x0), evaluated at x0, based on n values of U at x[0], x[1],... x[n-1]:

        U^m(x0) = sum weights[i] * U(x[i])

    Parameters
    ----------
    x : vector
        abscissas used for the evaluation for the derivative at x0.
    x0 : scalar
        location where approximations are to be accurate
    m : integer
        order of derivative. Note for m=0 this can be used to evaluate the
        interpolating polynomial itself.

    Notes
    -----
    The x values can be arbitrarily spaced but must be distinct and len(x) > m.

    The Fornberg algorithm is much more stable numerically than regular
    vandermonde systems for large values of n.

    See also
    --------
    fornberg_weights_all
    '''
    return fornberg_weights_all(x, x0, m)[:, -1]


_cmn_doc = """
    Calculate %(derivative)s with finite difference approximation

    Parameters
    ----------
    f : function
       function of one array f(x, `*args`, `**kwargs`)
    step : float, array-like or StepGenerator object, optional
       Spacing used, if None, then the spacing is automatically chosen
       according to (10*EPS)**(1/scale)*max(log(1+|x|), 1) where scale is
       depending on method and derivative-order (see default_scale).
       A StepGenerator can be used to extrapolate the results. However,
       the generator must generate minimum 3 steps in order to extrapolate
       the values.
    method : string, optional
        defines method used in the approximation
        'central': central difference derivative
        'complex': complex-step derivative
        'backward': backward difference derivative
        'forward': forward difference derivative
        'multicomplex': multicomplex derivative
    %(extra_parameter)s
    full_output : bool, optional
        If `full_output` is False, only the derivative is returned.
        If `full_output` is True, then (der, r) is returned `der` is the
        derivative, and `r` is a Results object.

    Call Parameters
    ---------------
    x : array_like
       value at which function derivative is evaluated
    args : tuple
        Arguments for function `f`.
    kwds : dict
        Keyword arguments for function `f`.
    %(returns)s
    Notes
    -----
    The complex-step derivative has truncation error O(steps**2) and
    O(steps**4) for odd and even order derivatives respectively, so
    truncation error can be eliminated by choosing steps to be very small.
    Especially the first order complex-step derivative avoids the problem of
    round-off error with small steps because there is no subtraction. However,
    the function needs to be analytic. This method does not work if f(x) does
    not support complex numbers or involves non-analytic functions such as
    e.g.: abs, max, min.
    For this reason the 'central' method is the default method.
    This method is usually very accurate, but sometimes one can only allow
    evaluation in forward or backward direction.

    Higher order approximation methods will generally be more accurate, but may
    also suffer more from numerical problems. First order methods is usually
    not recommended.
    Be careful in decreasing the step size too much due to round-off errors.

    %(extra_note)s
    References
    ----------
    Ridout, M.S. (2009) Statistical applications of the complex-step method
        of numerical differentiation. The American Statistician, 63, 66-74

    K.-L. Lai, J.L. Crassidis, Y. Cheng, J. Kim (2005), New complex step
        derivative approximations with application to second-order
        kalman filtering, AIAA Guidance, Navigation and Control Conference,
        San Francisco, California, August 2005, AIAA-2005-5944.

    Lyness, J. M., Moler, C. B. (1966). Vandermonde Systems and Numerical
                     Differentiation. *Numerische Mathematik*.

    Lyness, J. M., Moler, C. B. (1969). Generalized Romberg Methods for
                     Integrals of Derivatives. *Numerische Mathematik*.
    %(example)s
    %(see_also)s
    """


class MinStepGenerator(object):
    '''
    Generates a sequence of steps

    where
        steps = base_step * step_ratio ** (np.arange(num_steps) + offset)

    Parameters
    ----------
    base_step : float, array-like, optional
       Defines the base step, if None, then base_step is set to
           (10*EPS)**(1/scale)*max(log(1+|x|), 1)
       where x is supplied at runtime through the __call__ method.
    step_ratio : real scalar, optional, default 2
        Ratio between sequential steps generated.
        Note: Ratio > 1
        If None then step_ratio is 2 for n=1 otherwise step_ratio is 1.6
    num_steps : scalar integer, optional, default  n + order - 1 + num_extrap
        defines number of steps generated. It should be larger than
        n + order - 1
    offset : real scalar, optional, default 0
        offset to the base step
    scale : real scalar, optional
        scale used in base step. If not None it will override the default
        computed with the default_scale function.
    '''

    def __init__(self, base_step=None, step_ratio=2, num_steps=None,
                 offset=0, scale=None, num_extrap=0, use_exact_steps=True,
                 check_num_steps=True):
        self.base_step = base_step
        self.num_steps = num_steps
        self.step_ratio = step_ratio
        self.offset = offset
        self.scale = scale
        self.check_num_steps = check_num_steps
        self.use_exact_steps = use_exact_steps
        self.num_extrap = num_extrap

    def __repr__(self):
        class_name = self.__class__.__name__
        kwds = ['%s=%s' % (name, str(getattr(self, name)))
                for name in self.__dict__.keys()]
        return """%s(%s)""" % (class_name, ','.join(kwds))

    def _default_base_step(self, xi, method, n):
        scale = self.scale
        if scale is None:
            scale = default_scale(method, n)

        delta = _default_base_step(xi, scale, self.base_step)
        return delta

    def _min_num_steps(self, method, n, order):
        num_steps = n + order - 1

        if method in ['central', 'central2', 'complex']:
            step = 2
            if method == 'complex':
                step = 4 if n % 2 == 0 else 2
            num_steps = (n + order-1) // step
        return int(num_steps)

    def _default_num_steps(self, method, n, order):
        min_num_steps = self._min_num_steps(method, n, order)
        if self.num_steps is not None:
            num_steps = int(self.num_steps)
            if self.check_num_steps:
                num_steps = max(num_steps, min_num_steps)
            return num_steps
        return min_num_steps + int(self.num_extrap)

    def _default_step_ratio(self, n):
        if self.step_ratio is None:
            return {1: 2.0}.get(n, 1.6)
        return float(self.step_ratio)

    def __call__(self, x, method='central', n=1, order=2):
        xi = np.asarray(x)
        base_step = self._default_base_step(xi, method, n)
        num_steps = self._default_num_steps(method, n, order)
        step_ratio = self._default_step_ratio(n)
        offset = self.offset
        if self.use_exact_steps:
            step_ratio = _make_exact(step_ratio)
            base_step = _make_exact(base_step)
        for i in range(num_steps-1, -1, -1):
            h = (base_step * step_ratio**(i + offset))
            if (np.abs(h) > 0).all():
                yield h


class MinMaxStepGenerator(object):
    '''
    Generates a sequence of steps

    where
        steps = logspace(log10(step_min), log10(step_max), num_steps)

    Parameters
    ----------
    step_min : float, array-like, optional
       Defines the minimim step. Default value is:
           (10*EPS)**(1/scale)*max(log(1+|x|), 0.1)
       where x and scale are supplied at runtime through the __call__ method.
    step_max : real scalar, optional
        maximum step generated. Default value is:
            exp(log(step_min) * scale / (scale + 1.5))
    num_steps : scalar integer, optional
        defines number of steps generated.
    scale : real scalar, optional
        scale used in base step. If set to a value it will override the scale
        supplied at runtime.
    '''

    def __init__(self, step_min=None, step_max=None, num_steps=10, scale=None,
                 num_extrap=0):
        self.step_min = step_min
        self.num_steps = num_steps
        self.step_max = step_max
        self.scale = scale
        self.num_extrap = num_extrap

    def __repr__(self):
        class_name = self.__class__.__name__
        kwds = ['%s=%s' % (name, str(getattr(self, name)))
                for name in self.__dict__.keys()]
        return """%s(%s)""" % (class_name, ','.join(kwds))

    def __call__(self, x, method='forward', n=1, order=None):
        if self.scale is not None:
            scale = self.scale
        xi = np.asarray(x)
        step_min, step_max = self.step_min, self.step_max
        delta = _default_base_step(xi, scale, step_min)
        if step_min is None:
            step_min = (10 * EPS)**(1. / scale)
        if step_max is None:
            step_max = np.exp(np.log(step_min) * scale / (scale + 1.5))
        steps = np.logspace(0, np.log10(step_max) - np.log10(step_min),
                            self.num_steps)[::-1]

        for step in steps:
            h = _make_exact(delta * step)
            if (np.abs(h) > 0).all():
                yield h

'''
    step_nom : vector   default maximum(log1p(abs(x0)), 0.1)
        Nominal step. (The steps: h_i = step_nom[i] * delta)
    step_max : real scalar  (Default 2.0)
        Maximum allowed excursion from step_nom as a multiple of it.
    step_ratio: real scalar  (Default 2.0)
        Ratio used between sequential steps in the estimation of the derivative
    step_num : integer  (Default 26)
        The minimum step_num for making richardson extrapolation work is
            7 + np.ceil(self.n/2.) + self.order + self.richardson_terms
    delta : vector default step_max*step_ratio**(-arange(step_num))
        Defines the steps sizes used in derivation: h_i = step_nom[i] * delta
'''


class MaxStepGenerator(MinStepGenerator):
    '''
    Generates a sequence of steps

    where
        steps = base_step * step_ratio ** (-np.arange(num_steps) + offset)
        base_step = step_max * step_nom

    Parameters
    ----------
    max_step : float, array-like, optional default 2
       Defines the maximum step
    step_ratio : real scalar, optional, default 2
        Ratio between sequential steps generated.
        Note: Ratio > 1
    num_steps : scalar integer, optional, default  n + order - 1 + num_extrap
        defines number of steps generated. It should be larger than
        n + order - 1
    step_nom :  default maximum(log1p(abs(x)), 0.1)
        Nominal step.
    offset : real scalar, optional, default 0
        offset to the base step: max_step * nom_step

    '''
    def __init__(self, step_max=2.0, step_ratio=2.0, num_steps=15,
                 step_nom=None, offset=0, num_extrap=0,
                 use_exact_steps=False, check_num_steps=True):
        self.step_max = step_max
        self.step_ratio = step_ratio
        self.num_steps = num_steps
        self.step_nom = step_nom
        self.offset = offset
        self.num_extrap = num_extrap
        self.check_num_steps = check_num_steps
        self.use_exact_steps = use_exact_steps

    def _default_step_nom(self, x):
        if self.step_nom is None:
            return np.maximum(np.log1p(np.abs(x)), 1)
        return self.step_nom

    def __call__(self, x, method='forward', n=1, order=None):
        xi = np.asarray(x)
        scale = default_scale(method, n)
        delta = self.step_max * self._default_step_nom(xi)
        step_min = _default_base_step(xi, scale, None) * 1e-2
        step_ratio = self._default_step_ratio(n)
        offset = self.offset
        if self.use_exact_steps:
            delta, step_ratio = _make_exact(delta), _make_exact(step_ratio)
        num_steps = self._default_num_steps(method, n, order)
        for i in range(num_steps):
            h = delta * step_ratio**(-i + offset)
            if (np.abs(h) > step_min).all():
                yield h


class Richardson(object):
    '''
    Extrapolates as sequence with Richardsons method

    Notes
    -----
    Suppose you have series expansion that goes like this

    L = f(h) + a0 * h^p_0 + a1 * h^p_1+ a2 * h^p_2 + ...

    where p_i = order + step * i  and f(h) -> L as h -> 0, but f(0) != L.

    If we evaluate the right hand side for different stepsizes h
    we can fit a polynomial to that sequence of approximations.
    This is exactly what this class does.

    Example
    -------
    >>> import numpy as np
    >>> import numdifftools.nd_cstep as nd
    >>> n = 3
    >>> Ei = np.zeros((n,1))
    >>> h = np.zeros((n,1))
    >>> linfun = lambda i : np.linspace(0, np.pi/2., 2**(i+5)+1)
    >>> for k in np.arange(n):
    ...    x = linfun(k)
    ...    h[k] = x[1]
    ...    Ei[k] = np.trapz(np.sin(x),x)
    >>> En, err, step = nd.Richardson(step=1, order=1)(Ei, h)
    >>> truErr = Ei-1.
    >>> (truErr, err, En)
    (array([[ -2.00805680e-04],
           [ -5.01999079e-05],
           [ -1.25498825e-05]]), array([[ 0.00111851]]), array([[ 1.]]))

    '''
    def __init__(self, step_ratio=2.0, step=1, order=1, num_terms=2):
        self.num_terms = num_terms
        self.order = order
        self.step = step
        self.step_ratio = step_ratio

    def _r_matrix(self, num_terms):
        step = self.step
        i, j = np.ogrid[0:num_terms+1, 0:num_terms]
        r_mat = np.ones((num_terms + 1, num_terms + 1))
        r_mat[:, 1:] = (1.0 / self.step_ratio) ** (i*(step*j + self.order))
        return r_mat

    def _get_richardson_rule(self, sequence_length=None):
        if sequence_length is None:
            sequence_length = self.num_terms + 1
        num_terms = min(self.num_terms, sequence_length - 1)
        if num_terms > 0:
            r_mat = self._r_matrix(num_terms)
            return linalg.pinv(r_mat)[0]
        return np.ones((1,))

    def _estimate_error(self, new_sequence, old_sequence, steps, rule):
        m, _n = new_sequence.shape

        if m < 2:
            return (np.abs(new_sequence) * EPS + steps) * 10.0
        cov1 = np.sum(rule**2)  # 1 spare dof
        fact = np.maximum(12.7062047361747 * np.sqrt(cov1), EPS * 10.)
        err = np.abs(np.diff(new_sequence, axis=0)) * fact
        tol = np.maximum(np.abs(new_sequence[1:]),
                         np.abs(new_sequence[:-1])) * EPS * fact
        converged = err <= tol
        abserr = err + np.where(converged, tol * 10,
                                abs(new_sequence[:-1]-old_sequence[1:])*fact)
        # abserr = err1 + err2 + np.where(converged, tol2 * 10, abs(result-E2))
        # abserr = s * fact + np.abs(new_sequence) * EPS * 10.0
        return abserr

    def extrapolate(self, sequence, steps):
        return self.__call__(sequence, steps)

    def __call__(self, sequence, steps):
        ne = sequence.shape[0]
        rule = self._get_richardson_rule(ne)
        nr = rule.size - 1
        m = ne - nr
        new_sequence = convolve1d(sequence, rule[::-1], axis=0, origin=(nr//2))
        abserr = self._estimate_error(new_sequence, sequence, steps, rule)
        return new_sequence[:m], abserr[:m], steps[:m]


class _Derivative(object):

    info = namedtuple('info', ['error_estimate', 'final_step', 'index'])

    def __init__(self, f, step=None, method='central',  order=2, n=1,
                 full_output=False):
        self.fun = f
        self.n = n
        self.order = order
        self.method = method
        self.full_output = full_output
        self.richardson_terms = 2
        self.step = self._make_callable(step)

    def _make_callable(self, step):
        if hasattr(step, '__call__'):
            return step
        if step is None and self.method not in ['complex', 'hybrid']:
            return MaxStepGenerator(step_ratio=None, num_extrap=7)
        return MinStepGenerator(base_step=step, step_ratio=None, num_extrap=0)

    def _get_arg_min(self, errors):
        shape = errors.shape
        try:
            arg_mins = np.nanargmin(errors, axis=0)
            min_errors = np.nanmin(errors, axis=0)
        except ValueError as msg:
            warnings.warn(str(msg))
            ix = np.arange(shape[1])
            return ix

        for i, min_error in enumerate(min_errors):
            idx = np.flatnonzero(errors[:, i] == min_error)
            arg_mins[i] = idx[idx.size // 2]
        ix = np.ravel_multi_index((arg_mins, np.arange(shape[1])), shape)
        return ix

    def _get_best_estimate(self, der, errors, steps, shape):
        ix = self._get_arg_min(errors)
        final_step = steps.flat[ix].reshape(shape)
        err = errors.flat[ix].reshape(shape)
        return der.flat[ix].reshape(shape), self.info(err, final_step, ix)

    @property
    def _method_order(self):
        step = self._richardson_step()
        # Make sure it is even and at least 2 or 4
        order = max((self.order // step) * step, step)
        return order

    def _richardson_step(self):
        complex_step = 4 if self.n % 2 == 0 else 2
        return dict(central=2, central2=2, complex=complex_step,
                    multicomplex=2).get(self.method, 1)

    def _set_richardson_rule(self, step_ratio, num_terms=2):
        order = self._method_order
        step = self._richardson_step()
        self._richardson_extrapolate = Richardson(step_ratio=step_ratio,
                                                  step=step, order=order,
                                                  num_terms=num_terms)

    def _wynn_extrapolate(self, der, steps):
        der, errors = dea3(der[0:-2], der[1:-1], der[2:], symmetric=False)
        return der, errors, steps[2:]

    def _extrapolate(self, results, steps, shape):
        der, errors, steps = self._richardson_extrapolate(results, steps)
        if len(der) > 2:
            # der, errors, steps = self._richardson_extrapolate(results, steps)
            der, errors, steps = self._wynn_extrapolate(der, steps)
        der, info = self._get_best_estimate(der, errors, steps, shape)
        return der, info

    def _get_function_name(self):
        name = '_%s' % self.method
        even_derivative_order = (self.n % 2) == 0
        if even_derivative_order and self.method in ('central', 'complex'):
            name = name + '_even'
            if self.method in ('complex'):
                is_multiplum_of_4 = (self.n % 4) == 0
                if is_multiplum_of_4:
                    name = name + '_higher'
        elif self.method == 'multicomplex' and self.n > 1:
            if self.n == 2:
                name = name + '2'
            else:
                raise ValueError('Multicomplex method only support first and'
                                 'second order derivatives.')
        return name

    def _get_functions(self):
        name = self._get_function_name()
        return getattr(self, name), self.fun

    def _get_steps(self, xi):
        method, n, order = self.method, self.n, self._method_order
        return [step for step in self.step(xi, method, n, order)]

    def _is_odd_derivative(self):
        return self.n % 2 == 1

    def _is_even_derivative(self):
        return self.n % 2 == 0

    def _is_fourth_derivative(self):
        return self.n % 4 == 0

    def _eval_first_condition(self):
        even_derivative = self._is_even_derivative()
        return ((even_derivative and self.method in ('central', 'central2')) or
                self.method in ['forward', 'backward'] or
                self.method == 'complex' and self._is_fourth_derivative())

    def _eval_first(self, f, x, *args, **kwds):
        if self._eval_first_condition():
            return f(x, *args, **kwds)
        return 0.0

    def _vstack(self, sequence, steps):
        # sequence = np.atleast_2d(sequence)
        original_shape = np.shape(sequence[0])
        f_del = np.vstack(list(np.ravel(r)) for r in sequence)
        h = np.vstack(list(np.ravel(np.ones(original_shape)*step))
                      for step in steps)
        if f_del.size != h.size:
            raise ValueError('fun did not return data of correct size ' +
                             '(it must be vectorized)')
        return f_del, h, original_shape

    def _compute_step_ratio(self, steps):
        if len(steps) < 2:
            return 1
        return np.unique(steps[0]/steps[1]).mean()

    def __call__(self, x, *args, **kwds):
        xi = np.asarray(x)
        results = self._derivative(xi, args, kwds)
        derivative, info = self._extrapolate(*results)
        if self.full_output:
            return derivative, info
        return derivative


class Derivative(_Derivative):
    __doc__ = _cmn_doc % dict(
        derivative='n-th derivative',
        extra_parameter="""order : int, optional
        defines the order of the error term in the Taylor approximation used.
        For 'central' and 'complex' methods, it must be an even number.
    n : int, optional
        Order of the derivative.""",
        extra_note='', returns="""
    Returns
    -------
    der : ndarray
       array of derivatives
    """, example="""
    Examples
    --------
    >>> import numpy as np
    >>> import numdifftools.nd_cstep as ndc

    # 1'st derivative of exp(x), at x == 1

    >>> fd = ndc.Derivative(np.exp)
    >>> np.allclose(fd(1), 2.71828183)
    True

    >>> d2 = fd([1, 2])
    >>> np.allclose(d2, [ 2.71828183,  7.3890561 ])
    True

    >>> def f(x):
    ...     return x**3 + x**2

    >>> df = ndc.Derivative(f)
    >>> np.allclose(df(1), 5)
    True
    >>> ddf = ndc.Derivative(f, n=2)
    >>> np.allclose(ddf(1), 8)
    True
    """, see_also="""
    See also
    --------
    Gradient,
    Hessian
    """)
    """
    Find the n-th derivative of a function at a point.

    Given a function, use a difference formula with spacing `dx` to
    compute the `n`-th derivative at `x0`.

    Parameters
    ----------
    f : function
        Input function.
    x0 : float
        The point at which `n`-th derivative is found.
    dx : float, optional
        Spacing.
    method : Method of estimation.  Valid options are:
        'central', 'forward' or 'backward'.          (Default 'central')
    n : int, optional (Default 1)
        Order of the derivative.
    order : int, optional       (Default 2)
        defining order of basic method used.
        For 'central' methods, it must be an even number eg. [2,4].

    Notes
    -----
    Decreasing the step size too small can result in round-off error.

    Note on order: higher order methods will generally be more accurate,
             but may also suffer more from numerical problems. First order
             methods would usually not be recommended.
    Note on method: Central difference methods are usually the most accurate,
            but sometimes one can only allow evaluation in forward or backward
            direction.


    """
    @staticmethod
    def _fd_matrix(step_ratio, parity, nterms):
        ''' Return matrix for finite difference and complex step derivation.

        Parameters
        ----------
        step_ratio : real scalar
            ratio between steps in unequally spaced difference rule.
        parity : scalar, integer
            0 (one sided, all terms included but zeroth order)
            1 (only odd terms included)
            2 (only even terms included)
            3 (only every 4'th order terms included starting from order 2)
            4 (only every 4'th order terms included starting from order 4)
        nterms : scalar, integer
            number of terms
        '''
        try:
            step = [1, 2, 2, 4, 4][parity]
        except Exception as msg:
            raise ValueError('%s. Parity must be 0, 1, 2, 3 or 4! ' +
                             '(%d)' % (str(msg), parity))
        inv_sr = 1.0 / step_ratio
        offset = [1, 1, 2, 2, 4][parity]
        c0 = [1.0, 1.0, 1.0, 2.0, 24.0][parity]
        c = c0/misc.factorial(np.arange(offset, step * nterms + offset, step))
        [i, j] = np.ogrid[0:nterms, 0:nterms]
        return np.atleast_2d(c[j] * inv_sr ** (i * (step * j + offset)))

    def _flip_fd_rule(self):
        n = self.n
        return ((self._is_even_derivative() and (self.method == 'backward')) or
                (self.method == 'complex' and ((n % 8 in [4, 6]) or
                                               (n % 4 == 3))))

    def _get_finite_difference_rule(self, step_ratio):
        '''
        Generate finite differencing rule in advance.

        The rule is for a nominal unit step size, and will
        be scaled later to reflect the local step size.

        Member methods used
        -------------------
        _fd_matrix

        Member variables used
        ---------------------
        n
        order
        method
        '''
        method = self.method
        if method in ('multicomplex', ):
            return np.ones((1,))

        order, method_order = self.n - 1, self._method_order
        parity = 0
        if (method.startswith('central') or
                method.startswith('complex') and self._is_odd_derivative()):
            parity = (order % 2) + 1
        elif self.method == 'complex':
            parity = 4 if self.n % 4 == 0 else 3

        step = self._richardson_step()
        num_terms, ix = (order + method_order) // step, order // step
        fd_mat = self._fd_matrix(step_ratio, parity, num_terms)
        fd_rule = linalg.pinv(fd_mat)[ix]

        if self._flip_fd_rule():
            fd_rule *= -1
        return fd_rule

    def _apply_fd_rule(self, fd_rule, sequence, steps):
        '''
        Return derivative estimates of f at x0 for a sequence of stepsizes h

        Member variables used
        ---------------------
        n
        '''
        f_del, h, original_shape = self._vstack(sequence, steps)

        ne = h.shape[0]
        if ne < fd_rule.size:
            raise ValueError('num_steps (%d) must  be larger than '
                             '(%d) n + order - 1 = %d + %d -1'
                             ' (%s)' % (ne, fd_rule.size, self.n, self.order,
                                        self.method)
                             )
        nr = (fd_rule.size-1)
        f_diff = convolve1d(f_del, fd_rule[::-1], axis=0, origin=nr//2)

        der_init = f_diff / (h ** self.n)
        ne = max(ne - nr, 1)
        return der_init[:ne], h[:ne], original_shape

    def _derivative(self, xi, args, kwds):
        diff, f = self._get_functions()
        steps = self._get_steps(xi)
        fxi = self._eval_first(f, xi, *args, **kwds)
        results = [diff(f, fxi, xi, h, *args, **kwds) for h in steps]
        step_ratio = self._compute_step_ratio(steps)

        self._set_richardson_rule(step_ratio, self.richardson_terms)
        fd_rule = self._get_finite_difference_rule(step_ratio)
        return self._apply_fd_rule(fd_rule, results, steps)

    @staticmethod
    def _central_even(fun, f_x0i, x0i, h, *args, **kwds):
        return (fun(x0i + h, *args, **kwds) +
                fun(x0i - h, *args, **kwds)) / 2.0 - f_x0i

    @staticmethod
    def _central(fun, f_x0i, x0i, h, *args, **kwds):
        return (fun(x0i + h, *args, **kwds) -
                fun(x0i - h, *args, **kwds)) / 2.0

    @staticmethod
    def _forward(fun, f_x0i, x0i, h, *args, **kwds):
        return (fun(x0i + h, *args, **kwds) - f_x0i)

    @staticmethod
    def _backward(fun, f_x0i, x0i, h, *args, **kwds):
        return (f_x0i - fun(x0i - h))

    @staticmethod
    def _complex(f, fx, x, h, *args, **kwds):
        return f(x + 1j * h, *args, **kwds).imag

    @staticmethod
    def _complex_even(f, fx, x, h, *args, **kwargs):
        ih = h * (1j + 1.0) / np.sqrt(2)
        return (f(x + ih, *args, **kwargs) +
                f(x - ih, *args, **kwargs)).imag

    @staticmethod
    def _complex_even_higher(f, fx, x, h, *args, **kwargs):
        ih = h * (1j + 1.0) / np.sqrt(2)
        return (f(x + ih, *args, **kwargs) +
                f(x - ih, *args, **kwargs) - 2 * fx).real * 12

    @staticmethod
    def _multicomplex(f, fx, x, h, *args, **kwds):
        z = bicomplex(x + 1j * h, 0)
        return f(z, *args, **kwds).imag

    @staticmethod
    def _multicomplex2(f, fx, x, h, *args, **kwds):
        z = bicomplex(x + 1j * h, h)
        return f(z, *args, **kwds).imag12


class Gradient(Derivative):
    def __init__(self, f, step=None, method='central', order=2,
                 full_output=False):
        super(Gradient, self).__init__(f, step=step, method=method, n=1,
                                       order=order, full_output=full_output)
    __doc__ = _cmn_doc % dict(
        derivative='Gradient',
        extra_parameter="""order : int, optional
        defines the order of the error term in the Taylor approximation used.
        For 'central' and 'complex' methods, it must be an even number.""",
        returns="""
    Returns
    -------
    grad : array
        gradient
    """, extra_note="", example="""
    Examples
    --------
    >>> import numpy as np
    >>> import numdifftools.nd_cstep as ndc
    >>> fun = lambda x: np.sum(x**2)
    >>> dfun = ndc.Gradient(fun)
    >>> dfun([1,2,3])
    array([ 2.,  4.,  6.])

    # At [x,y] = [1,1], compute the numerical gradient
    # of the function sin(x-y) + y*exp(x)

    >>> sin = np.sin; exp = np.exp
    >>> z = lambda xy: sin(xy[0]-xy[1]) + xy[1]*exp(xy[0])
    >>> dz = ndc.Gradient(z)
    >>> grad2 = dz([1, 1])
    >>> grad2
    array([ 3.71828183,  1.71828183])

    # At the global minimizer (1,1) of the Rosenbrock function,
    # compute the gradient. It should be essentially zero.

    >>> rosen = lambda x : (1-x[0])**2 + 105.*(x[1]-x[0]**2)**2
    >>> rd = ndc.Gradient(rosen)
    >>> grad3 = rd([1,1])
    >>> np.allclose(grad3,[0, 0])
    True""", see_also="""
    See also
    --------
    Derivative, Hessian, Jacobian
    """)

    @staticmethod
    def _central(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [(f(x + hi, *args, **kwds) - f(x - hi, *args, **kwds)) / 2.0
                    for hi in increments]
        return np.array(partials).T

    @staticmethod
    def _backward(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [(fx - f(x - hi, *args, **kwds)) for hi in increments]
        return np.array(partials).T

    @staticmethod
    def _forward(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [(f(x + hi, *args, **kwds) - fx) for hi in increments]
        return np.array(partials).T

    @staticmethod
    def _complex(f, fx, x, h, *args, **kwds):
        # From Guilherme P. de Freitas, numpy mailing list
        # http://mail.scipy.org/pipermail/numpy-discussion/2010-May/050250.html
        n = len(x)
        increments = np.identity(n) * 1j * h
        partials = [f(x + ih, *args, **kwds).imag for ih in increments]
        return np.array(partials).T

    @staticmethod
    def _multicomplex(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * 1j * h
        partials = [f(bicomplex(x + hi, 0), *args, **kwds).imag
                    for hi in increments]
        return np.array(partials).T


class Jacobian(Gradient):
    __doc__ = _cmn_doc % dict(
        derivative='Jacobian',
        extra_parameter="""order : int, optional
        defines the order of the error term in the Taylor approximation used.
        For 'central' and 'complex' methods, it must be an even number.""",
        returns="""
    Returns
    -------
    jacob : array
        Jacobian
    """, extra_note="""
    If f returns a 1d array, it returns a Jacobian. If a 2d array is returned
    by f (e.g., with a value for each observation), it returns a 3d array
    with the Jacobian of each observation with shape xk x nobs x xk. I.e.,
    the Jacobian of the first observation would be [:, 0, :]
    """, example='''
     Examples
    --------
    >>> import numdifftools.nd_cstep as ndc

    #(nonlinear least squares)

    >>> xdata = np.reshape(np.arange(0,1,0.1),(-1,1))
    >>> ydata = 1+2*np.exp(0.75*xdata)
    >>> fun = lambda c: (c[0]+c[1]*np.exp(c[2]*xdata) - ydata)**2

    >>> Jfun = ndc.Jacobian(fun)
    >>> val = Jfun([1,2,0.75])
    >>> np.allclose(val, np.zeros((10,3)))
    True

    >>> fun2 = lambda x : x[0]*x[1]*x[2] + np.exp(x[0])*x[1]
    >>> Jfun3 = ndc.Jacobian(fun2)
    >>> Jfun3([3.,5.,7.])
    array([ 135.42768462,   41.08553692,   15.        ])
    ''', see_also="""
    See also
    --------
    Derivative, Hessian, Gradient
    """)


class Hessdiag(Derivative):
    def __init__(self, f, step=None, method='central', order=2,
                 full_output=False):
        super(Hessdiag, self).__init__(f, step=step, method=method, n=2,
                                       order=order, full_output=full_output)
    __doc__ = _cmn_doc % dict(
        derivative='Hessian diagonal',
        extra_parameter="""    'central2' : central difference derivative
    order : int, optional
        defines the order of the error term in the Taylor approximation used.
        For 'central' and 'complex' methods, it must be an even number.""",
        returns="""
    Returns
    -------
    hessdiag : array
        hessian diagonal
    """, extra_note="", example="""
    Examples
    --------
    >>> import numpy as np
    >>> import numdifftools.nd_cstep as ndc
    >>> fun = lambda x : x[0] + x[1]**2 + x[2]**3
    >>> Hfun = ndc.Hessdiag(fun, full_output=True)
    >>> hd, info = Hfun([1,2,3])
    >>> np.allclose(hd, [  0.,   2.,  18.])
    True

    >>> info.error_estimate < 1e-11
    array([ True,  True,  True], dtype=bool)
    """, see_also="""
    See also
    --------
    Derivative, Hessian, Jacobian, Gradient
    """)

    @staticmethod
    def _central2(f, fx, x, h, *args, **kwds):
        '''Eq. 8'''
        n = len(x)
        increments = np.identity(n) * h
        partials = [(f(x + 2*hi, *args, **kwds) +
                    f(x - 2*hi, *args, **kwds) + 2*fx -
                    2*f(x + hi, *args, **kwds) -
                    2*f(x - hi, *args, **kwds)) / 4.0
                    for hi in increments]
        return np.array(partials)

    @staticmethod
    def _central_even(f, fx, x, h, *args, **kwds):
        '''Eq. 9'''
        n = len(x)
        increments = np.identity(n) * h
        partials = [(f(x + hi, *args, **kwds) +
                     f(x - hi, *args, **kwds)) / 2.0 - fx
                    for hi in increments]
        return np.array(partials)

    @staticmethod
    def _backward(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [(fx - f(x - hi, *args, **kwds)) for hi in increments]
        return np.array(partials)

    @staticmethod
    def _forward(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [(f(x + hi, *args, **kwds) - fx) for hi in increments]
        return np.array(partials)

    @staticmethod
    def _multicomplex2(f, fx, x, h, *args, **kwds):
        n = len(x)
        increments = np.identity(n) * h
        partials = [f(bicomplex(x + 1j * hi, hi), *args, **kwds).imag12
                    for hi in increments]
        return np.array(partials)

    @staticmethod
    def _complex_even(f, fx, x, h, *args, **kwargs):
        n = len(x)
        increments = np.identity(n) * h * (1j+1) / np.sqrt(2)
        partials = [(f(x + hi, *args, **kwargs) +
                     f(x - hi, *args, **kwargs)).imag
                    for hi in increments]
        return np.array(partials)


class Hessian(_Derivative):
    def __init__(self, f, step=None, method='central', full_output=False):
        order = dict(backward=1, forward=1, complex=4).get(method, 2)
        super(Hessian, self).__init__(f, n=2, step=step, method=method,
                                      order=order, full_output=full_output)

    __doc__ = _cmn_doc % dict(
        derivative='Hessian',
        extra_parameter="    'central2' : central difference derivative",
        returns="""
    Returns
    -------
    hess : ndarray
       array of partial second derivatives, Hessian
    """, extra_note="""Computes the Hessian according to method as:
    'forward', Eq. (7):
        1/(d_j*d_k) * ((f(x + d[j]*e[j] + d[k]*e[k]) - f(x + d[j]*e[j])))
    'central2', Eq. (8):
        1/(2*d_j*d_k) * ((f(x + d[j]*e[j] + d[k]*e[k]) - f(x + d[j]*e[j])) -
                         (f(x + d[k]*e[k]) - f(x)) +
                         (f(x - d[j]*e[j] - d[k]*e[k]) - f(x + d[j]*e[j])) -
                         (f(x - d[k]*e[k]) - f(x)))
    'central', Eq. (9):
        1/(4*d_j*d_k) * ((f(x + d[j]*e[j] + d[k]*e[k]) -
                          f(x + d[j]*e[j] - d[k]*e[k])) -
                         (f(x - d[j]*e[j] + d[k]*e[k]) -
                          f(x - d[j]*e[j] - d[k]*e[k]))
    'complex', Eq. (10):
        1/(2*d_j*d_k) * imag(f(x + i*d[j]*e[j] + d[k]*e[k]) -
                            f(x + i*d[j]*e[j] - d[k]*e[k]))
    where e[j] is a vector with element j == 1 and the rest are zero and
    d[i] is steps[i].
    """, example="""
    Examples
    --------
    >>> import numpy as np
    >>> import numdifftools.nd_cstep as ndc

    # Rosenbrock function, minimized at [1,1]

    >>> rosen = lambda x : (1.-x[0])**2 + 105*(x[1]-x[0]**2)**2
    >>> Hfun = ndc.Hessian(rosen)
    >>> h = Hfun([1, 1])
    >>> h
    array([[ 842., -420.],
           [-420.,  210.]])

    # cos(x-y), at (0,0)

    >>> cos = np.cos
    >>> fun = lambda xy : cos(xy[0]-xy[1])
    >>> Hfun2 = ndc.Hessian(fun)
    >>> h2 = Hfun2([0, 0])
    >>> h2
    array([[-1.,  1.],
           [ 1., -1.]])""", see_also="""
    See also
    --------
    Derivative, Hessian
    """)

    def _derivative(self, xi, args, kwds):
        diff, f = self._get_functions()
        steps = self._get_steps(xi)

        fxi = self._eval_first(f, xi, *args, **kwds)
        results = [diff(f, fxi, xi, h, *args, **kwds) for h in steps]
        step_ratio = self._compute_step_ratio(steps)
        self._set_richardson_rule(step_ratio, self.richardson_terms)
        return self._vstack(results, steps)

    @staticmethod
    def _complex_even(f, fx, x, h, *args, **kwargs):
        '''Calculate Hessian with complex-step derivative approximation
        The stepsize is the same for the complex and the finite difference part
        '''
        n = len(x)
        # h = _default_base_step(x, 3, base_step, n)
        ee = np.diag(h)
        hes = 2. * np.outer(h, h)

        for i in range(n):
            for j in range(i, n):
                hes[i, j] = (f(x + 1j * ee[i] + ee[j], *args, **kwargs) -
                             f(x + 1j * ee[i] - ee[j], *args, **kwargs)
                             ).imag / hes[j, i]
                hes[j, i] = hes[i, j]
        return hes

    @staticmethod
    def _multicomplex2(f, fx, x, h, *args, **kwargs):
        '''Calculate Hessian with bicomplex-step derivative approximation
        '''
        n = len(x)
        ee = np.diag(h)
        hess = np.outer(h, h)
        for i in range(n):
            for j in range(i, n):
                zph = bicomplex(x + 1j * ee[i, :], ee[j, :])
                hess[i, j] = (f(zph, *args, **kwargs)).imag12 / hess[j, i]
                hess[j, i] = hess[i, j]
        return hess

    @staticmethod
    def _central_even(f, fx, x, h, *args, **kwargs):
        '''Eq 9.'''
        n = len(x)
        # h = _default_base_step(x, 4, base_step, n)
        ee = np.diag(h)
        hess = np.outer(h, h)

        for i in range(n):
            hess[i, i] = (f(x + 2*ee[i, :], *args, **kwargs) - 2*fx +
                          f(x - 2*ee[i, :], *args, **kwargs)
                          ) / (4. * hess[i, i])
            for j in range(i+1, n):
                hess[i, j] = (f(x + ee[i, :] + ee[j, :], *args, **kwargs) -
                              f(x + ee[i, :] - ee[j, :], *args, **kwargs) -
                              f(x - ee[i, :] + ee[j, :], *args, **kwargs) +
                              f(x - ee[i, :] - ee[j, :], *args, **kwargs)
                              ) / (4. * hess[j, i])
                hess[j, i] = hess[i, j]
        return hess

    @staticmethod
    def _central2(f, fx, x, h, *args, **kwargs):
        '''Eq. 8'''
        n = len(x)
        # NOTE: ridout suggesting using eps**(1/4)*theta
        # h = _default_base_step(x, 3, base_step, n)
        ee = np.diag(h)
        dtype = np.result_type(fx)
        g = np.empty(n, dtype=dtype)
        gg = np.empty(n, dtype=dtype)
        for i in range(n):
            g[i] = f(x + ee[i], *args, **kwargs)
            gg[i] = f(x - ee[i], *args, **kwargs)

        hess = np.empty((n, n), dtype=dtype)
        np.outer(h, h, out=hess)
        for i in range(n):
            for j in range(i, n):
                hess[i, j] = (f(x + ee[i, :] + ee[j, :], *args, **kwargs) -
                              g[i] - g[j] + fx +
                              f(x - ee[i, :] - ee[j, :], *args, **kwargs) -
                              gg[i] - gg[j] + fx) / (2 * hess[j, i])
                hess[j, i] = hess[i, j]

        return hess

    @staticmethod
    def _forward(f, fx, x, h, *args, **kwargs):
        '''Eq. 7'''
        n = len(x)
        ee = np.diag(h)

        dtype = np.result_type(fx)
        g = np.empty(n, dtype=dtype)
        for i in range(n):
            g[i] = f(x + ee[i, :], *args, **kwargs)

        hess = np.empty((n, n), dtype=dtype)
        np.outer(h, h, out=hess)
        for i in range(n):
            for j in range(i, n):
                hess[i, j] = (f(x + ee[i, :] + ee[j, :], *args, **kwargs) -
                              g[i] - g[j] + fx) / hess[j, i]
                hess[j, i] = hess[i, j]
        return hess

    def _backward(self, f, fx, x, h, *args, **kwargs):
        return self._forward(f, fx, x, -h, *args, **kwargs)


def main():
    import statsmodels.api as sm

    data = sm.datasets.spector.load()
    data.exog = sm.add_constant(data.exog, prepend=False)
    mod = sm.Probit(data.endog, data.exog)
    _res = mod.fit(method="newton")
    _test_params = [1, 0.25, 1.4, -7]
    _llf = mod.loglike
    _score = mod.score
    _hess = mod.hessian

    def fun(beta, x):
        return np.dot(x, beta).sum(0)

    def fun1(beta, y, x):
        # print(beta.shape, x.shape)
        xb = np.dot(x, beta)
        return (y - xb) ** 2  # (xb-xb.mean(0))**2

    def fun2(beta, y, x):
        # print(beta.shape, x.shape)
        return fun1(beta, y, x).sum(0)

    nobs = 200
    x = np.random.randn(nobs, 3)

    # xk = np.array([1, 2, 3])
    xk = np.array([1., 1., 1.])
    # xk = np.zeros(3)
    beta = xk
    y = np.dot(x, beta) + 0.1 * np.random.randn(nobs)
    xk = np.dot(np.linalg.pinv(x), y)

    epsilon = 1e-6
    args = (y, x)
    from scipy import optimize
    _xfmin = optimize.fmin(fun2, (0, 0, 0), args)  # @UndefinedVariable
    # print(approx_fprime((1, 2, 3), fun, steps, x))
    jac = Gradient(fun1, epsilon, method='forward')(xk, *args)
    jacmin = Gradient(fun1, -epsilon, method='forward')(xk, *args)
    # print(jac)
    print(jac.sum(0))
    print('\nnp.dot(jac.T, jac)')
    print(np.dot(jac.T, jac))
    print('\n2*np.dot(x.T, x)')
    print(2 * np.dot(x.T, x))
    jac2 = (jac + jacmin) / 2.
    print(np.dot(jac2.T, jac2))

    # he = approx_hess(xk,fun2,steps,*args)
    print(Hessian(fun2, 1e-3, method='central2')(xk, *args))
    he = Hessian(fun2, method='central2')(xk, *args)
    print('hessfd')
    print(he)
    print('base_step =', None)
    print(he - 2 * np.dot(x.T, x))

    for eps in [1e-3, 1e-4, 1e-5, 1e-6]:
        print('eps =', eps)
        print(Hessian(fun2, eps, method='central2')(xk, *args) -
              2 * np.dot(x.T, x))

    hcs2 = Hessian(fun2, method='hybrid')(xk, *args)
    print('hcs2')
    print(hcs2 - 2 * np.dot(x.T, x))

    hfd3 = Hessian(fun2, method='central')(xk, *args)
    print('hfd3')
    print(hfd3 - 2 * np.dot(x.T, x))

    hfi = []
    epsi = np.array([1e-1, 1e-2, 1e-3, 1e-4, 1e-5, 1e-6]) * 10.
    for eps in epsi:
        h = eps * np.maximum(np.log1p(np.abs(xk)), 0.1)
        hfi.append(Hessian(fun2, h, method='hybrid')(xk, *args))
        print('hfi, eps =', eps)
        print(hfi[-1] - 2 * np.dot(x.T, x))

    import numdifftools as nd
    print('Dea3')
    err = 1000 * np.ones(hfi[0].shape)
    val = np.zeros(err.shape)
    errt = []
    for i in range(len(hfi) - 2):
        tval, terr = nd.dea3(hfi[i], hfi[i + 1], hfi[i + 2])
        errt.append(terr)
        k = np.flatnonzero(terr < err)
        if k.size > 0:
            np.put(val, k, tval.flat[k])
            np.put(err, k, terr.flat[k])
    print(val - 2 * np.dot(x.T, x))
    print(err)
    erri = [v.max() for v in errt]

    plt.loglog(epsi[1:-1], erri)
    plt.show('hold')
    hnd = nd.Hessian(lambda a: fun2(a, y, x))
    hessnd = hnd(xk)
    print('numdiff')
    print(hessnd - 2 * np.dot(x.T, x))
    # assert_almost_equal(hessnd, he[0])
    gnd = nd.Gradient(lambda a: fun2(a, y, x))
    _gradnd = gnd(xk)

    print(Derivative(np.cosh)(0))
    print(nd.Derivative(np.cosh)(0))


def _example3(x=0.0001, fun_name='cos', epsilon=None, method='central',
              scale=None, n=1, order=2):
    fun0, dfun = get_test_function(fun_name, n)
    if dfun is None:
        return dict(n=n, order=order, method=method, fun=fun_name,
                    error=np.nan, scale=np.nan)
    fd = Derivative(fun0, step=epsilon, method=method, n=n, order=order)
    t = []
    scales = np.arange(1.0, 45, 0.25)
    for scale in scales:
        fd.step.scale = scale
        try:
            val = fd(x)
        except Exception:
            val = np.nan
        t.append(val)
    t = np.array(t)
    tt = dfun(x)
    relativ_error = np.abs(t - tt) / (np.maximum(np.abs(tt), 1)) + 1e-17

    weights = np.ones((3,))/3
    relativ_error = convolve1d(relativ_error, weights)  # smooth curve

    if np.isnan(relativ_error).all():
        return dict(n=n, order=order, method=method, fun=fun_name,
                    error=np.nan, scale=np.nan)
    if True:  # False:  #
        plt.semilogy(scales, relativ_error)
        plt.vlines(default_scale(fd.method, n), np.nanmin(relativ_error), 1)
        plt.xlabel('scales')
        plt.ylabel('Relative error')
        txt = ['', "1'st", "2'nd", "3'rd", "4'th", "5'th", "6'th",
               "7th"] + ["%d'th" % i for i in range(8, 25)]

        plt.title("The %s derivative of %s using %s, order=%d" % (txt[n],
                                                                  fun_name,
                                                                  method,
                                                                  order))

        plt.axis([min(scales), max(scales), np.nanmin(relativ_error), 1])
        plt.figure()
        # plt.show('hold')
    i = np.nanargmin(relativ_error)
    return dict(n=n, order=order, method=method, fun=fun_name,
                error=relativ_error[i], scale=scales[i])


def _example2(x=0.0001, fun_name='inv', epsilon=None, method='central',
              scale=None, n=1):
    fun0, dfun = get_test_function(fun_name, n)

    fd = Derivative(fun0, step=epsilon, method=method, n=n)
    t = []
    orders = n + (n % 2) + np.arange(0, 12, 2)

    for order in orders:
        fd.order = order
        fd.step.num_steps = n + order - 1
        t.append(fd(x))
    t = np.array(t)
    tt = dfun(x)
    plt.semilogy(orders, np.abs(t - tt) / (np.abs(tt) + 1e-17) + 1e-17)

    plt.show('hold')


def _example(x=0.0001, fun_name='inv', epsilon=None, method='central',
             scale=None):
    '''
    '''
    fun0, dfun = get_test_function(fun_name)

    h = _default_base_step(x, scale=2, epsilon=None)  # 1e-4

    fd = Derivative(fun0, step=epsilon, method=method, full_output=True)

    t, res = fd(x)

    txt = (' (f(x+h)-f(x))/h = %g\n' %
           ((fun0(x + h) - fun0(x)) / h))
    deltas = np.array([h for h in epsilon(x, fd.scale)])

    print((txt +
           '      true df(x) = %20.15g\n' +
           ' estimated df(x) = %20.15g\n' +
           ' true err = %g\n err estimate = %g\n relative err = %g\n'
           ' delta = %g\n') % (dfun(x), t, dfun(x) - t,
                               res.error_estimate,
                               res.error_estimate / t,
                               deltas.flat[res.index]))
    # plt.show('hold')


def cartesian5(arrays, out=None):
    arrays = [np.asarray(x).ravel() for x in arrays]
    dtype = np.result_type(*arrays)

    n = np.prod([arr.size for arr in arrays])
    if out is None:
        out = np.empty((len(arrays), n), dtype=dtype)
    else:
        out = out.T

    for j, arr in enumerate(arrays):
        n /= arr.size
        out.shape = (len(arrays), -1, arr.size, n)
        out[j] = arr[np.newaxis, :, np.newaxis]
    out.shape = (len(arrays), -1)

    return out.T


def test_docstrings():
    import doctest
    doctest.testmod(optionflags=doctest.NORMALIZE_WHITESPACE)


def main2():
    import pandas as pd
    num_extrap = 0
    method = 'complex'
    data = []
    for name in ['exp', 'expm1', 'sin', 'cos']:  # function_names[:-3]:
        for order in range(2, 3, 1):
            #  order = 1
            for n in range(1, 16, 2):
                num_steps = n + order - 1 + num_extrap
                if method in ['central', 'complex']:
                    step = 2
                    if n % 2 == 0 and method == 'complex':
                        step = 4
                    num_steps = (n + order-1) // step + num_extrap

                step_ratio = 1.6  # 4**(1./n)
                epsilon = MinStepGenerator(num_steps=num_steps,
                                           step_ratio=step_ratio,
                                           offset=0, use_exact_steps=True)
                data.append(pd.DataFrame(_example3(x=0.7, fun_name=name,
                                                   epsilon=epsilon,
                                                   method=method,
                                                   scale=None, n=n,
                                                   order=order),
                                         index=np.arange(1)))
    df = pd.concat(data)
    # sprint(df)
    print(df.groupby(['n']).mean())
    print(np.diff(df.groupby(['n']).mean(), axis=0))
    plt.show('hold')

if __name__ == '__main__':  # pragma : no cover
    # test_docstrings()
    # main()
    main2()

# Method = 'central
#               error  order     scale
# n
# 1  1.597817e-11      2  2.387500
# 2  1.566730e-08      2  4.600000
# 3  8.454223e-07      2  5.625000
# 4  7.216031e-06      2  7.109375

# Method = forward
#           error  order     scale
# n
# 1  4.040869e-11      2  4.000000
# 2  4.720010e-08      2  4.765625
# 3  5.513484e-06      2  5.984375
# 4  1.107329e-04      2  7.233333

#     step = MinStepGenerator(num_steps=7)
#     d = Derivative(np.cos, method='central', step=step,
#                    full_output=True)
#     print(d([0, 1e5*np.pi*2]))
#     print(d(1e10*np.pi*2))


# For x = 0.5
#           error  fun   method  n  order  scale
# 0  1.529569e-08  cos  forward  1      1   1.75
# 0  3.643711e-06  cos  forward  2      1   2.50
# 0  1.850107e-04  cos  forward  3      1   3.75
# 0  4.208470e-04  cos  forward  4      1   5.50
# 0  9.130236e-03  cos  forward  5      1   7.75
# 0  5.515373e-03  cos  forward  6      1   9.50
# 0  1.913060e-11  cos  forward  1      2   2.75
# 0  1.097469e-08  cos  forward  2      2   3.50
# 0  2.534291e-07  cos  forward  3      2   5.00
# 0  3.189640e-05  cos  forward  4      2   6.75
# 0  7.366713e-05  cos  forward  5      2   9.50
# 0  1.723842e-04  cos  forward  6      2  12.50
# 0  5.930356e-13  cos  forward  1      3   2.75
# 0  3.544596e-10  cos  forward  2      3   4.75
# 0  7.313120e-08  cos  forward  3      3   6.25
# 0  2.982097e-08  cos  forward  4      3   8.00
# 0  2.900927e-05  cos  forward  5      3  11.25
# 0  1.705052e-05  cos  forward  6      3  13.50
# 0  8.981784e-16  cos  forward  1      4   4.00
# 0  1.792656e-11  cos  forward  2      4   5.50
# 0  4.038642e-09  cos  forward  3      4   7.00
# 0  2.925495e-08  cos  forward  4      4   9.75
# 0  3.326524e-06  cos  forward  5      4  13.25
# 0  2.158151e-05  cos  forward  6      4  15.25
