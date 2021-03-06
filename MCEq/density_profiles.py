# -*- coding: utf-8 -*-
"""
:mod:`MCEq.density_profiles` - models of the Earth's atmosphere
===============================================================

This module includes classes and functions modeling the Earth's atmosphere.
Currently, two different types models are supported:

- Linsley-type/CORSIKA-style parameterization
- Numerical atmosphere via external routine (NRLMSISE-00)

Both implementations have to inherit from the abstract class 
:class:`CascadeAtmosphere`, which provides the functions for other parts of
the program. In particular the function :func:`CascadeAtmosphere.get_density` 

Typical interaction::

      $ atm_object = CorsikaAtmosphere("BK_USStd")
      $ atm_object.set_theta(90)
      $ print 'density at X=100', atm_object.X2rho(100.)

The class :class:`MCEqRun` will only the following routines::
    - :func:`CascadeAtmosphere.set_theta`,
    - :func:`CascadeAtmosphere.r_X2rho`.
    
If you are extending this module make sure to provide these
functions without breaking compatibility.

Example:
  An example can be run by executing the module::

      $ python MCEq/atmospheres.py
"""

import numpy as np
import geometry as geom
from numba import jit, double  # @UnresolvedImport
from os.path import join
from abc import ABCMeta, abstractmethod
from mceq_config import dbg, config

def _load_cache():
    """Loads atmosphere cache from file.

    If file does not exist, function returns
    a new empty dictionary.

    Returns:
        dict: Dictionary containing splines.

    """
    import cPickle as pickle
    if dbg > 0:
        print "atmospheres::_load_cache(): loading cache."
    fname = join(config['data_dir'],
                 config['atm_cache_file'])
    
    try:
        return pickle.load(open(fname, 'r'))
    except IOError:
        print "density_profiles::_load_cache(): creating new cache.."
        return {}

def _dump_cache(cache):
    """Stores atmosphere cache to file.

    Args:
        (dict) current cache
    Raises:
        IOError:
    """
    import cPickle as pickle
    
    if dbg > 0:
        print "density_profiles::_dump_cache() dumping cache."
    fname = join(config['data_dir'],
                 config['atm_cache_file'])
    print fname
    try:
        pickle.dump(cache, open(fname, 'w'), protocol=-1)
    except IOError:
        raise IOError("density_profiles::_dump_cache(): " + 
                'could not (re-)create cache. Wrong working directory?')

class CascadeAtmosphere():
    """Abstract class containing common methods on atmosphere.
    You have to inherit from this class and implement the virtual method 
    :func:`get_density`.

    Note:
      Do not instantiate this class directly.
       
    Attributes:
      thrad (float): current zenith angle :math:`\\theta` in radiants
      theta_deg (float): current zenith angle :math:`\\theta` in degrees
      X_surf (float): Slant depth at the surface according to the geometry
                      defined in the :mod:`MCEq.geometry`
    
    """

    __metaclass__ = ABCMeta
    thrad = None
    theta_deg = None
    X_surf = None

    @abstractmethod
    def get_density(self, h_cm):
        """Abstract method which implementation  should return the density in g/cm**3.

        Args:
           h_cm (float):  height in cm

        Returns:
           float: density in g/cm**3

        Raises:
            NotImplementedError:
        """
        raise NotImplementedError("CascadeAtmosphere::get_density(): " + 
                                  "Base class called.")

    def calculate_density_spline(self, n_steps=1000):
        """Calculates and stores a spline of :math:`\\rho(X)`.
        
        Args:
          n_steps (int, optional): number of :math:`X` values
                                   to use for interpolation

        Raises:
            Exception: if :func:`set_theta` was not called before.
        """
        from scipy.integrate import quad
        from time import time
        from scipy.interpolate import UnivariateSpline
        
        if self.theta_deg == None:
            raise Exception('{0}::calculate_density_spline(): ' + 
                            'zenith angle not set'.format(
                             self.__class__.__name__))
        else:
            print ('{0}::calculate_density_spline(): ' + 
                   'Calculating spline of rho(X) for zenith ' + 
                   '{1} degrees.').format(self.__class__.__name__,
                                         self.theta_deg)

        thrad = self.thrad
        path_length = geom.l(thrad)
        vec_rho_l = np.vectorize(
            lambda delta_l: self.get_density(geom.h(delta_l, thrad)))
        dl_vec = np.linspace(0, path_length, n_steps)
        
        now = time()
        
        # Calculate integral for each depth point 
        # functionality could be more efficient :)
        X_int = np.zeros_like(dl_vec, dtype='float64')
        for i, dl in enumerate(dl_vec):
            X_int[i] = quad(vec_rho_l, 0, dl, epsrel=0.01)[0]

        print '.. took {0:1.2f}s'.format(time() - now)

        # Save depth value at h_obs
        self.X_surf = X_int[-1]
        
        # Interpolate with bi-splines without smoothing
        self.s_X2rho = UnivariateSpline(X_int, vec_rho_l(dl_vec),
                                        k=2, s=0.0)
        
        print 'Average spline error:', np.std(vec_rho_l(dl_vec) / 
                                              self.s_X2rho(X_int))

    def set_theta(self, theta_deg):
        """Configures geometry and initiates spline calculation for
        :math:`\\rho(X)`.
        
        If the option 'use_atm_cache' is enabled in the config, the
        function will check, if a corresponding spline is available
        in the cache and use it. Otherwise it will call 
        :func:`calculate_density_spline`,  make the function 
        :func:`r_X2rho` available to the core code and store the spline 
        in the cache.
         
        Args:
          theta_deg (float): zenith angle :math:`\\theta` at detector
        """
        def calculate_and_store(key, cache):
            self.thrad = geom._theta_rad(theta_deg)
            self.theta_deg = theta_deg
            self.calculate_density_spline()
            cache[key][theta_deg] = (self.X_surf, self.s_X2rho)
            _dump_cache(cache)

        if self.theta_deg == theta_deg:
            print self.__class__.__name__ + '::set_theta(): Using previous' + \
                'density spline.'
            return
        elif config['use_atm_cache']:
            from MCEq.misc import _get_closest
            cache = _load_cache()
            key = (self.__class__.__name__, self.location, self.season)
            if cache and key in cache.keys():
                try:
                    closest = _get_closest(theta_deg, cache[key].keys())[1]
                    if abs(closest - theta_deg) < 1.:
                        self.thrad = geom._theta_rad(closest)
                        self.theta_deg = closest
                        self.X_surf, self.s_X2rho = cache[key][closest]
                    else:
                        calculate_and_store(key, cache)
                except:
                    cache[key] = {}
                    calculate_and_store(key, cache)

            else:
                cache[key] = {}
                calculate_and_store(key, cache)

        else:
            self.thrad = geom._theta_rad(theta_deg)
            self.theta_deg = theta_deg
            self.calculate_density_spline()

    def r_X2rho(self, X):
        """Returns the inverse density :math:`\\frac{1}{\\rho}(X)`. 

        The spline `s_X2rho` is used, which was calculated or retrieved
        from cache during the :func:`set_theta` call.

        Args:
           X (float):  slant depth in g/cm**2

        Returns:
           float: :math:`1/\\rho` in cm**3/g

        """
        return 1 / self.s_X2rho(X)
    
    def X2rho(self, X):
        """Returns the density :math:`\\rho(X)`. 

        The spline `s_X2rho` is used, which was calculated or retrieved
        from cache during the :func:`set_theta` call.

        Args:
           X (float):  slant depth in g/cm**2

        Returns:
           float: :math:`\\rho` in cm**3/g

        """
        return self.s_X2rho(X)

    def moliere_air(self, h_cm):
        """Returns the Moliere unit of air for US standard atmosphere. """

        return 9.3 / (self.get_density(h_cm) * 100.)

    def nref_rel_air(self, h_cm):
        """Returns the refractive index - 1 in air (density parametrization
        as in CORSIKA).
        """

        return 0.000283 * self.get_density(h_cm) / self.get_density(0)

    def gamma_cherenkov_air(self, h_cm):
        """Returns the Lorentz factor gamma of Cherenkov threshold in air (MeV).
        """

        nrel = self.nref_rel_air(h_cm)
        return (1. + nrel) / np.sqrt(2. * nrel + nrel ** 2)

    def theta_cherenkov_air(self, h_cm):
        """Returns the Cherenkov angle in air (degrees).
        """

        return np.arccos(1. / (1. + self.nref_rel_air(h_cm))) * 180. / np.pi


#=========================================================================
# CorsikaAtmosphere
#=========================================================================
class CorsikaAtmosphere(CascadeAtmosphere):
    """Class, holding the parameters of a Linsley type parameterization
    similar to the Air-Shower Monte Carlo 
    `CORSIKA <https://web.ikp.kit.edu/corsika/>`_.
    
    The parameters pre-defined parameters are taken from the CORSIKA
    manual. If new sets of parameters are added to :func:`init_parameters`, 
    the array _thickl can be calculated using :func:`calc_thickl` .
    
    Attributes:
      _atm_param (numpy.array): (5x5) Stores 5 atmospheric parameters 
                                _aatm, _batm, _catm, _thickl, _hlay 
                                for each of the 5 layers
    Args:
      location (str): see :func:`init_parameters`
      season (str,optional): see :func:`init_parameters`
    """
    _atm_param = None
    
    def __init__(self, location, season=None):
        self.init_parameters(location, season)
        CascadeAtmosphere.__init__(self)

    def init_parameters(self, location, season=None):
        """Initializes :attr:`_atm_param`.
        
        +--------------+-------------------+------------------------------+
        | location     | CORSIKA Table     | Description/season           |
        +==============+===================+==============================+
        | "USStd"      |         1         |  US Standard atmosphere      |
        +--------------+-------------------+------------------------------+
        | "BK_USStd"   |         31        |  Bianca Keilhauer's USStd    |
        +--------------+-------------------+------------------------------+
        | "Karlsruhe"  |         18        |  AT115 / Karlsruhe           |
        +--------------+-------------------+------------------------------+
        | "SouthPole"  |      26 and 28    |  MSIS-90-E for Dec and June  |
        +--------------+-------------------+------------------------------+
        |"PL_SouthPole"|      29 and 30    |  P. Lipari's  Jan and Aug    |
        +--------------+-------------------+------------------------------+
        
        
        Args:
          location (str): see table
          season (str, optional): choice of season for supported locations
                          
        Raises:
          Exception: if parameter set not available
        """
        _aatm, _batm, _catm, _thickl, _hlay = None, None, None, None, None
        self.X_surf = None
        if location == "USStd":
            _aatm = np.array([-186.5562, -94.919, 0.61289, 0.0, 0.01128292])
            _batm = np.array([1222.6562, 1144.9069, 1305.5948, 540.1778, 1.0])
            _catm = np.array([994186.38, 878153.55, 636143.04, 772170., 1.0e9])
            _thickl = np.array(
                [1036.102549, 631.100309, 271.700230, 3.039494, 0.001280])
            _hlay = np.array([0, 4.0e5, 1.0e6, 4.0e6, 1.0e7])
        elif location == "BK_USStd":
            _aatm = np.array(
                [-149.801663, -57.932486, 0.63631894, 4.3545369e-4, 0.01128292])
            _batm = np.array([1183.6071, 1143.0425, 1322.9748, 655.69307, 1.0])
            _catm = np.array(
                [954248.34, 800005.34, 629568.93, 737521.77, 1.0e9])
            _thickl = np.array(
                [1033.804941, 418.557770, 216.981635, 4.344861, 0.001280])
            _hlay = np.array([0.0, 7.0e5, 1.14e6, 3.7e6, 1.0e7])
        elif location == "Karlsruhe":
                _aatm = np.array(
                    [-118.1277, -154.258, 0.4191499, 5.4094056e-4, 0.01128292])
                _batm = np.array(
                    [1173.9861, 1205.7625, 1386.7807, 555.8935, 1.0])
                _catm = np.array(
                    [919546., 963267.92, 614315., 739059.6, 1.0e9])
                _thickl = np.array(
                    [1055.858707, 641.755364, 272.720974, 2.480633, 0.001280])
                _hlay = np.array([0.0, 4.0e5, 1.0e6, 4.0e6, 1.0e7])
        elif location == 'SouthPole':
            if season == 'December':
                _aatm = np.array(
                    [-128.601, -39.5548, 1.13088, -0.00264960, 0.00192534])
                _batm = np.array([1139.99, 1073.82, 1052.96, 492.503, 1.0])
                _catm = np.array(
                    [861913., 744955., 675928., 829627., 5.8587010e9])
                _thickl = np.array(
                    [1011.398804, 588.128367, 240.955360, 3.964546, 0.000218])
                _hlay = np.array([0.0, 4.0e5, 1.0e6, 4.0e6, 1.0e7])
            elif season == "June":
                _aatm = np.array(
                    [-163.331, -65.3713, 0.402903, -0.000479198, 0.00188667])
                _batm = np.array([1183.70, 1108.06, 1424.02, 207.595, 1.0])
                _catm = np.array(
                    [875221., 753213., 545846., 793043., 5.9787908e9])
                _thickl = np.array(
                    [1020.370363, 586.143464, 228.374393, 1.338258, 0.000214])
                _hlay = np.array([0.0, 4.0e5, 1.0e6, 4.0e6, 1.0e7])
            else:
                raise Exception('CorsikaAtmosphere(): Season "' + season + 
                                '" not parameterized for location SouthPole.')
        elif location == 'PL_SouthPole':
            if season == 'January':
                _aatm = np.array(
                    [-113.139, -7930635, -54.3888, -0.0, 0.00421033])
                _batm = np.array([1133.10, 1101.20, 1085.00, 1098.00, 1.0])
                _catm = np.array(
                    [861730., 826340., 790950., 682800., 2.6798156e9])
                _thickl = np.array(
                    [1019.966898, 718.071682, 498.659703, 340.222344, 0.000478])
                _hlay = np.array([0.0, 2.67e5, 5.33e5, 8.0e5, 1.0e7])
            elif season == "August":
                _aatm = np.array(
                    [-59.0293, -21.5794, -7.14839, 0.0, 0.000190175])
                _batm = np.array([1079.0, 1071.90, 1182.0, 1647.1, 1.0])
                _catm = np.array(
                    [764170., 699910., 635650., 551010., 59.329575e9])
                _thickl = np.array(
                    [1019.946057, 391.739652, 138.023515, 43.687992, 0.000022])
                _hlay = np.array([0.0, 6.67e5, 13.33e5, 2.0e6, 1.0e7])
            else:
                raise Exception('CorsikaAtmosphere(): Season "' + season + 
                                '" not parameterized for location SouthPole.')
        else:
            raise Exception("CorsikaAtmosphere:init_parameters(): Location " + 
                            str(location) + " not parameterized.")

        self._atm_param = np.array([_aatm, _batm, _catm, _thickl, _hlay])
        
        self.location, self.season = location, season
        # Clear cached theta value to force spline recalculation
        self.theta_deg = None

    def depth2height(self, x_v):
        """Converts column/vertical depth to height.
        
        Args:
          x_v (float): column depth :math:`X_v` in g/cm**2
          
        Returns:
          float: height in cm
        """
        _aatm, _batm, _catm, _thickl, _hlay = self._atm_param

        if x_v >= _thickl[1]:
            height = _catm[0] * np.log(_batm[0] / (x_v - _aatm[0]))
        elif x_v >= _thickl[2]:
            height = _catm[1] * np.log(_batm[1] / (x_v - _aatm[1]))
        elif x_v >= _thickl[3]:
            height = _catm[2] * np.log(_batm[2] / (x_v - _aatm[2]))
        elif x_v >= _thickl[4]:
            height = _catm[3] * np.log(_batm[3] / (x_v - _aatm[3]))
        else:
            height = (_aatm[4] - x_v) * _catm[4]

        return height

    def height2depth(self, h_cm):
        """Converts height to column/vertical depth.
        
        Args:
          h_cm (float): height in cm
          
        Returns:
          float: column depth :math:`X_v` in g/cm**2
        """

        _aatm, _batm, _catm, _thickl, _hlay = self._atm_param

        height = h_cm

        if height <= _hlay[1]:
            x_v = _aatm[0] + _batm[0] * np.exp(-height / _catm[0])
        elif height <= _hlay[2]:
            x_v = _aatm[1] + _batm[1] * np.exp(-height / _catm[1])
        elif height <= _hlay[3]:
            x_v = _aatm[2] + _batm[2] * np.exp(-height / _catm[2])
        elif height <= _hlay[4]:
            x_v = _aatm[3] + _batm[3] * np.exp(-height / _catm[3])
        else:
            x_v = _aatm[4] - height / _catm[4]

        return x_v

    def get_density(self, h_cm):
        """ Returns the density of air in g/cm**3.
        
        Uses the optimized module function :func:`corsika_get_density_jit`.
        
        Args:
          h_cm (float): height in cm
        
        Returns:
          float: column depth :math:`\\rho(h_{cm})` in g/cm**3
        """
        return corsika_get_density_jit(h_cm, self._atm_param)

    def rho_inv(self, X, cos_theta):
        """Returns reciprocal density in cm**3/g using planar approximation.
        
        This function uses the optimized function :func:`planar_rho_inv_jit`
         
        Args:
          h_cm (float): height in cm
        
        Returns:
          float: :math:`\\frac{1}{\\rho}(X,\\cos{\\theta})` cm**3/g
        """
        return planar_rho_inv_jit(X, cos_theta, self._atm_param)

    def calc_thickl(self):
        """Calculates thickness layers for :func:`depth2height` 
        
        The analytical inversion of the CORSIKA parameterization 
        relies on the knowledge about the depth :math:`X`, where
        trasitions between layers/exponentials occur.
        
        Example:
          Create a new set of parameters in :func:`init_parameters`
          inserting arbitrary values in the _thikl array::

          $ cor_atm = CorsikaAtmosphere(new_location, new_season)
          $ cor_atm.calc_thickl()
          
          Replace _thickl values with printout. 
        
        """
        from scipy.integrate import quad
        thickl = []
        for h in self._atm_param[4]:
            thickl.append('{0:4.6f}'.format(quad(self.get_density, h,
                                                 112.8e5, epsrel=1e-4)[0]))
        print '_thickl = np.array([' + ', '.join(thickl) + '])'


@jit(double(double, double, double[:, :]), target='cpu')
def planar_rho_inv_jit(X, cos_theta, param):
    """Optimized calculation of :math:`1/\\rho(X,\\theta)` in
    planar approximation. 
    
    This function can be used for calculations where 
    :math:`\\theta < 70^\\circ`.  
    
    Args:
      X (float): slant depth in g/cm**2
      cos_theta (float): :math:`\\cos(\\theta)`
    
    Returns:
      float: :math:`1/\\rho(X,\\theta)` in cm**3/g
    """
    a = param[0]
    b = param[1]
    c = param[2]
    t = param[3]
    res = 0.0
    x_v = X * cos_theta
    layer = 0
    for i in xrange(t.size):
        if not (x_v >= t[i]):
            layer = i
    if layer == 4:
        res = c[4] / b[4]
    else:
        l = layer
        res = c[l] / (x_v - a[l])
    return res


@jit(double(double, double[:, :]), target='cpu')
def corsika_get_density_jit(h_cm, param):
    """Optimized calculation of :math:`\\rho(h)` in
    according to CORSIKA type parameterization.
    
    Args:
      h_cm (float): height above surface in cm
      param (numpy.array): 5x5 parameter array from 
                        :class:`CorsikaAtmosphere`
    
    Returns:
      float: :math:`\\rho(h)` in g/cm**3
    """
    b = param[1]
    c = param[2]
    hl = param[4]
    res = 0.0
    layer = 0
    for i in xrange(hl.size):
        if not (h_cm <= hl[i]):
            layer = i
    if layer == 4:
        res = b[4] / c[4]
    else:
        l = layer
        res = b[l] / c[l] * np.exp(-h_cm / c[l])

    return res

class MSIS00Atmosphere(CascadeAtmosphere):
    """Wrapper class for a python interface to the NRLMSISE-00 model.
    
    `NRLMSISE-00 <http://ccmc.gsfc.nasa.gov/modelweb/atmos/nrlmsise00.html>`_
    is an empirical model of the Earth's atmosphere. It is available as
    a FORTRAN 77 code or as a verson traslated into 
    `C by Dominik Borodowski <http://www.brodo.de/english/pub/nrlmsise/>`_.  
    Here a PYTHON wrapper has been used. 
    
    Attributes:
      _msis : NRLMSISE-00 python wrapper object handler 
    
    Args:
      location (str): see :func:`init_parameters`
      season (str,optional): see :func:`init_parameters`
    """
    
    _msis = None
    
    def __init__(self, location, season):
        from msis_wrapper import cNRLMSISE00, pyNRLMSISE00
        if config['msis_python'] == 'ctypes':
            self.msis = cNRLMSISE00()
        else:
            self.msis = pyNRLMSISE00()

        self.init_parameters(location, season)
        CascadeAtmosphere.__init__(self)

    def init_parameters(self, location, season):
        """Sets location and season in :class:`NRLMSISE-00`.
        
        Translates location and season into day of year 
        and geo coordinates.   

        Args:
          location (str): Supported are "SouthPole" and "Karlsruhe"
          season (str): months of the year: January, February, etc.
        """
        self.msis.set_location(location)
        self.msis.set_season(season)
        
        self.location, self.season = location, season
        # Clear cached value to force spline recalculation
        self.theta_deg = None

    def get_density(self, h_cm):
        """ Returns the density of air in g/cm**3.
        
        Wraps around ctypes calls to the NRLMSISE-00 C library.
        
        Args:
          h_cm (float): height in cm
        
        Returns:
          float: column depth :math:`\\rho(h_{cm})` in g/cm**3
        """
        return self.msis.get_density(h_cm)

if __name__ == '__main__':
    import matplotlib.pyplot as plt

    plt.figure(figsize=(5, 4))
    atm_obj = CorsikaAtmosphere('PL_SouthPole', 'January')

    atm_obj.set_theta(0.0)
    x_vec = np.linspace(0, atm_obj.X_surf, 10000)
    plt.plot(x_vec, 1 / atm_obj.r_X2rho(x_vec), lw=1.5,
             label="PL_SouthPole/January")
    
    atm_obj.init_parameters('PL_SouthPole', 'August')
    atm_obj.set_theta(0.0)
    x_vec = np.linspace(0, atm_obj.X_surf, 10000)
    plt.plot(x_vec, 1 / atm_obj.r_X2rho(x_vec), lw=1.5,
             label="PL_SouthPole/August")
    plt.legend()
    plt.tight_layout()
    plt.show()
