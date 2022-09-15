# copyright ################################# #
# This file is part of the Xfields Package.   #
# Copyright (c) CERN, 2021.                   #
# ########################################### #

import numpy as np
from scipy.special import wofz as wofz_scipy
import xobjects as xo
from xobjects.context import available
from xfields.general import _pkg_root


def test_cerrf_q1():
    ctx = xo.ContextCpu(omp_num_threads=0)

    xx = np.concatenate(([0], np.logspace(-8, +8, 51))).astype(np.float64)
    yy = np.concatenate(([0], np.logspace(-8, +8, 51))).astype(np.float64)

    n_re = len(xx)
    n_im = len(yy)
    n_z = len(yy) * len(xx)

    re_absc, im_absc = np.meshgrid(xx, yy)

    # Using scipy's wofz implemenation of the Faddeeva method. This is
    # (at the time of this writing in 2021) based on the MIT ab-initio
    # implementation using a combination of Algorithm 680 for large |z| and
    # Algorithm 916 for the remainder fo C. It claims a relative accuracy of
    # 1e-13 across the whole of C and is thus suitable to check the accuracy
    # of the cerrf_q1 implementation which has a target accuracy of 10^{-10}
    # in the *absolute* error.
    wz_cmp = wofz_scipy(re_absc + 1.0j * im_absc)

    src_code = """
    /*gpukern*/ void eval_cerrf_q1(
        const int n,
        /*gpuglmem*/ double const* /*restrict*/ re,
        /*gpuglmem*/ double const* /*restrict*/ im,
        /*gpuglmem*/ double* /*restrict*/ wz_re,
        /*gpuglmem*/ double* /*restrict*/ wz_im )
    {
        for(int tid = 0; ; tid < n ; ++tid ) { //vectorize_over tid n
            if( tid < n )
            {
                double const x = re[ tid ];
                double const y = im[ tid ];
                double wz_x, wz_y;

                cerrf_q1( x, y, &wz_x, &wz_y );

                wz_re[ tid ] = wz_x;
                wz_im[ tid ] = wz_y;
            }
        } //end_vectorize
    }
    """

    kernel_descriptions = {
        "eval_cerrf_q1": xo.Kernel(
            args=[
                xo.Arg(xo.Int32, name="n"),
                xo.Arg(xo.Float64, name="re", const=True, pointer=True),
                xo.Arg(xo.Float64, name="im", const=True, pointer=True),
                xo.Arg(xo.Float64, name="wz_re", pointer=True),
                xo.Arg(xo.Float64, name="wz_im", pointer=True),
            ],
            n_threads="n",
        ),
    }

    headers = [
        _pkg_root.joinpath("headers/constants.h"),
        _pkg_root.joinpath("headers/sincos.h"),
        _pkg_root.joinpath("headers/power_n.h"),
        _pkg_root.joinpath("fieldmaps/bigaussian_src/complex_error_function.h"),
    ]

    wz_re = np.arange(n_z, dtype=np.float64)
    wz_im = np.arange(n_z, dtype=np.float64)

    re_absc_dev = ctx.nparray_to_context_array(re_absc.reshape(n_z))
    im_absc_dev = ctx.nparray_to_context_array(im_absc.reshape(n_z))
    wz_re_dev = ctx.nparray_to_context_array(wz_re)
    wz_im_dev = ctx.nparray_to_context_array(wz_im)

    ctx.add_kernels(
        sources=[src_code], kernels=kernel_descriptions, extra_headers=headers
    )

    ctx.kernels.eval_cerrf_q1(
        n=n_z, re=re_absc_dev, im=im_absc_dev, wz_re=wz_re_dev, wz_im=wz_im_dev
    )

    wz_re = ctx.nparray_from_context_array(wz_re_dev).reshape(n_im, n_re)
    wz_im = ctx.nparray_from_context_array(wz_im_dev).reshape(n_im, n_re)

    d_abs_re = np.fabs(wz_re - wz_cmp.real)
    d_abs_im = np.fabs(wz_im - wz_cmp.imag)

    # NOTE: target accuracy of cerrf_q1 is 0.5e-10 but the algorithm does
    #       not converge to within target accuracy for all arguments in C,
    #       especially close to the real axis. We therfore require that
    #       d_abs_re.max(), d_abs_im.max() < 0.5e-9

    assert d_abs_re.max() < 0.5e-9
    assert d_abs_im.max() < 0.5e-9


def test_cerrf_all_quadrants():
    x0 = 5.33
    y0 = 4.29
    num_args = 10000

    assert xo.ContextCpu in available

    ctx = xo.ContextCpu(omp_num_threads=0)

    re_max = np.float64(np.sqrt(2.0) * x0)
    im_max = np.float64(np.sqrt(2.0) * y0)

    # Extending the sampled area symmetrically into Q3 and Q4 would
    # get the zeros of w(z) into the fold which are located close to the
    # first medians of these quadrants at Im(z) = \pm Re(z) for Re(z) > 1.99146
    #
    # This would lead to a degradation in the accuracy by at least an order
    # of magnitude due to cancellation effects and could distort the test ->
    # By excluding anything with an imaginary part < -1.95, this should be on
    # the safe side.

    np.random.seed(20210811)

    im_min = np.float64(-1.95)
    re_min = -re_max

    re_absc = np.random.uniform(re_min, re_max, num_args)
    im_absc = np.random.uniform(im_min, im_max, num_args)
    wz_cmp_re = np.arange(num_args, dtype=np.float64)
    wz_cmp_im = np.arange(num_args, dtype=np.float64)

    # Create comparison data for veryfing the correctness of cerrf().
    # Cf. the comments about scipy's wofz implementation in test_cerrf_q1()
    # for details!

    for ii, (x, y) in enumerate(zip(re_absc, im_absc)):
        wz = wofz_scipy(x + 1.0j * y)
        wz_cmp_re[ii] = wz.real
        wz_cmp_im[ii] = wz.imag

    src_code = """
    /*gpukern*/ void eval_cerrf_all_quadrants(
        const int n,
        /*gpuglmem*/ double const* /*restrict*/ re,
        /*gpuglmem*/ double const* /*restrict*/ im,
        /*gpuglmem*/ double* /*restrict*/ wz_re,
        /*gpuglmem*/ double* /*restrict*/ wz_im )
    {
        for(int tid = 0 ; tid < n ; ++tid ) { //vectorize_over tid n

            if( tid < n )
            {
                double const x = re[ tid ];
                double const y = im[ tid ];
                double wz_x, wz_y;

                cerrf( x, y, &wz_x, &wz_y );

                wz_re[ tid ] = wz_x;
                wz_im[ tid ] = wz_y;
            }
        }//end_vectorize
    }
    """

    kernel_descriptions = {
        "eval_cerrf_all_quadrants": xo.Kernel(
            args=[
                xo.Arg(xo.Int32, name="n"),
                xo.Arg(xo.Float64, name="re", const=True, pointer=True),
                xo.Arg(xo.Float64, name="im", const=True, pointer=True),
                xo.Arg(xo.Float64, name="wz_re", pointer=True),
                xo.Arg(xo.Float64, name="wz_im", pointer=True),
            ],
            n_threads="n",
        ),
    }

    headers = [
        _pkg_root.joinpath("headers/constants.h"),
        _pkg_root.joinpath("headers/sincos.h"),
        _pkg_root.joinpath("headers/power_n.h"),
        _pkg_root.joinpath("fieldmaps/bigaussian_src/complex_error_function.h"),
    ]

    wz_re = np.arange(num_args, dtype=np.float64)
    wz_im = np.arange(num_args, dtype=np.float64)

    re_absc_dev = ctx.nparray_to_context_array(re_absc)
    im_absc_dev = ctx.nparray_to_context_array(im_absc)
    wz_re_dev = ctx.nparray_to_context_array(wz_re)
    wz_im_dev = ctx.nparray_to_context_array(wz_im)

    ctx.add_kernels(
        sources=[src_code], kernels=kernel_descriptions, extra_headers=headers
    )

    ctx.kernels.eval_cerrf_all_quadrants(
        n=num_args,
        re=re_absc_dev,
        im=im_absc_dev,
        wz_re=wz_re_dev,
        wz_im=wz_im_dev,
    )

    wz_re = ctx.nparray_from_context_array(wz_re_dev)
    wz_im = ctx.nparray_from_context_array(wz_im_dev)

    d_abs_re = np.fabs(wz_re - wz_cmp_re)
    d_abs_im = np.fabs(wz_im - wz_cmp_im)

    assert d_abs_re.max() < 0.5e-9
    assert d_abs_im.max() < 0.5e-9


source = '''
    /*gpukern*/ void FaddeevaCalculator_compute(FaddeevaCalculatorData data) {
        int64_t len = FaddeevaCalculatorData_len_z_re(data);

        for (int64_t ii = 0; ii < len; ii++) {  //vectorize_over ii len
            double z_re = FaddeevaCalculatorData_get_z_re(data, ii);
            double z_im = FaddeevaCalculatorData_get_z_im(data, ii);
            double w_re, w_im;

            cerrf(z_re, z_im, &w_re, &w_im);

            FaddeevaCalculatorData_set_w_re(data, ii, w_re);
            FaddeevaCalculatorData_set_w_im(data, ii, w_im);
        } //end_vectorize
    }
'''

class FaddeevaCalculator(xo.HybridClass):
    _xofields = {
        'z_re': xo.Float64[:],
        'z_im': xo.Float64[:],
        'w_re': xo.Float64[:],
        'w_im': xo.Float64[:],
    }

    _extra_c_sources = [
        _pkg_root.joinpath("headers/constants.h"),
        _pkg_root.joinpath("headers/sincos.h"),
        _pkg_root.joinpath("headers/power_n.h"),
        _pkg_root.joinpath("fieldmaps/bigaussian_src/complex_error_function.h"),
        source,
    ]

    _kernels = {
        'FaddeevaCalculator_compute': xo.Kernel(
            args=[
                xo.Arg(xo.ThisClass, name='data'),
            ],
        )
    }

    def __init__(self, z, **kwargs):
        z = np.array(z)

        self.xoinitialize(
            z_re = z.real,
            z_im = z.imag,
            w_re = len(z),
            w_im = len(z),
            **kwargs,
        )

    def compute(self):
        self._xobject.compile_kernels(only_if_needed=True)
        import ipdb; ipdb.set_trace()
        self._context.kernels.FaddeevaCalculator_compute.description.n_threads = len(self.z_re)
        self._context.kernels.FaddeevaCalculator_compute(data=self)

context = xo.ContextCupy()
fc = FaddeevaCalculator(z=[1+1j, 2+2j, 3+3j] * 256, _context=context)
fc.compute()
