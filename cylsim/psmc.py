import numpy as np

from cylsim import psestimation

from cosmoutils import nputil


def sim_skyvec(trans, n):
    """Simulate a set of alm(\nu)'s for a given m.

    Generated as if m=0. For greater m, just ignore entries for l < abs(m).

    Parameters
    ----------
    trans : np.ndarray
        Transfer matrix generated by `block_root` from a a particular C_l(z,z').

    Returns
    -------
    gaussvars : np.ndarray
       Vector of alms.
    """
    
    lside = trans.shape[0]
    nfreq = trans.shape[1]

    matshape = (lside, nfreq, n)

    gaussvars = (np.random.standard_normal(matshape)
                 + 1.0J * np.random.standard_normal(matshape)) / 2.0**0.5

    for i in range(lside):
        gaussvars[i] = np.dot(trans[i], gaussvars[i])

    return gaussvars   #.T.copy()
        

def block_root(clzz):
    """Blah.
    """

    trans = np.zeros_like(clzz)

    for i in range(trans.shape[0]):
        trans[i] = nputil.matrix_root_manynull(clzz[i], truncate=False)

    return trans
    

class PSMonteCarlo(psestimation.PSEstimation):
    """An extension of the PSEstimation class to support estimation of the
    Fisher matrix via Monte-Carlo simulations.

    This should be significantly faster when including large numbers of eigenmodes.

    Attributes
    ----------
    nswitch : integer
        The threshold number of eigenmodes above which we switch to Monte-Carlo
        estimation.
    nsamples : integer
        The number of samples to draw from each band.
    """
    
    nsamples = 200
    nswitch = 0 #200

    debias_sub = True

    __config_table_ =   {   'nsamples'  : [ int,    'nsamples'],
                            'nswitch'   : [ int,    'nswitch'],
                            'debias_sub': [ bool,   'debias_sub']
                        }


    def __init__(self, *args, **kwargs):

        super(PSMonteCarlo, self).__init__(*args, **kwargs)

        # Add configuration options                
        self.add_config(self.__config_table_)


    def genbands(self):
        """Override genbands to make it generate the transformation matrices for
        drawing random samples.
        """
        super(PSMonteCarlo, self).genbands()

        print "Generating transforms..."
        self.transarray = [block_root(clzz[0, 0]) for clzz in self.clarray]



    def get_vecs_old(self, mi, bi, scale=False):
        """Get a set of random samples from the specified band `bi` for a given
        `mi`.
        """
        evsims = np.zeros((self.nsamples, self.num_evals(mi)), dtype=np.complex128)

        for i in range(self.nsamples):
            skysim = sim_skyvec(self.transarray[bi])
            evsims[i] = self.kltrans.project_sky_vector_forward(mi, skysim, threshold=self.threshold)

        if scale:
            #evsims = (evsims - evsims.mean(axis=0)[np.newaxis, :]) / (1.0 + evals[np.newaxis, :])**0.5
            evals = self.kltrans.modes_m(mi, threshold=self.threshold)[0]
            evsims = evsims / (1.0 + evals[np.newaxis, :])**0.5

        return evsims


    def get_vecs(self, mi, bi, scale=False):
        """Get a set of random samples from the specified band `bi` for a given
        `mi`.
        """

        bt = self.kltrans.beamtransfer
        evals, evecs = self.kltrans.modes_m(mi, threshold=self.threshold)

        btsims = np.zeros((bt.nfreq, bt.ntel, self.nsamples), dtype=np.complex128)
        skysim = sim_skyvec(self.transarray[bi], self.nsamples)
        #beam = self.kltrans.beamtransfer.beam_m(mi).reshape((bt.nfreq, bt.ntel, bt.nsky))
        beam = self.kltrans.beamtransfer.beam_m(mi).reshape((bt.nfreq, bt.ntel, bt.nsky))[:, :, :(bt.nsky/self.telescope.num_pol_sky)]
        
        for fi in range(bt.nfreq):
            btsims[fi] = np.dot(beam[fi], skysim[:, fi, :])

        evsims = np.dot(evecs, btsims.reshape((bt.nfreq*bt.ntel, self.nsamples)))

        if scale:
            evsims = evsims / (1.0 + evals[:, np.newaxis])**0.5

        return evsims.T.copy()


    def gen_vecs(self, mi):
        """Generate a cache of sample vectors for each bandpower.
        """

        self.vec_cache = [ self.get_vecs(mi, bi, scale=True) for bi in range(len(self.clarray))]


    def makeproj_mc(self, mi, bi):
        """Estimate the band covariance from a set of samples.
        """
        evsims = self.get_vecs(mi, bi)
        #evsims = evsims - evsims.mean(axis=0)[np.newaxis, :]
        return np.dot(evsims.T.conj(), evsims) / (self.nsamples - 1.0)


    def fisher_m_mc(self, mi):
        """Calculate the Fisher Matrix by Monte-Carlo.
        """
            
        nbands = len(self.bands) - 1
        fab = np.zeros((nbands, nbands), dtype=np.complex128)

        if self.num_evals(mi) > 0:
            print "Making fisher (for m=%i)." % mi

            self.gen_vecs(mi)

            ns = self.nsamples

            for ia in range(nbands):
                # Estimate diagonal elements (including bias correction)
                va = self.vec_cache[ia]

                if self.debias_sub:
                    tmat = np.dot(va, va.T.conj())
                    fab[ia, ia] = (np.sum(np.abs(tmat)**2) / ns**2 - np.trace(tmat)**2 / ns**3) / (1.0 - 1.0 / ns**2)
                else:
                    h1 = self.nsamples / 2
                    va1 = va[:h1]
                    vb1 = va[h1:(2*h1)]
                    fab[ia, ia] = np.sum(np.abs(np.dot(va1, vb1.T.conj()))**2) / ns**2

                # Estimate diagonal elements
                for ib in range(ia):
                    vb = self.vec_cache[ib]
                    fab[ia, ib] = np.sum(np.abs(np.dot(va, vb.T.conj()))**2) / ns**2
                    fab[ib, ia] = np.conj(fab[ia, ib])
            
        else:
            print "No evals (for m=%i), skipping." % mi

        return fab


    def fisher_m(self, mi):
        """Calculate the Fisher Matrix for a given m.

        Decides whether to use direct evaluation or Monte-Carlo depending on the
        number of eigenvalues required.
        """
        if self.num_evals(mi) < self.nswitch:
            return super(PSMonteCarlo, self).fisher_m(mi)
        else:
            return self.fisher_m_mc(mi)
        
        

class PSMonteCarlo2(psestimation.PSEstimation):
    """An extension of the PSEstimation class to support estimation of the
    Fisher matrix via Monte-Carlo simulations.

    This uses a stochastic estimation of the trace which allows us to compute
    a reduced set of products between the four covariance matrices.

    Attributes
    ----------
    nswitch : integer
        The threshold number of eigenmodes above which we switch to Monte-Carlo
        estimation.
    nsamples : integer
        The number of samples to draw from each band.
    """
    
    nsamples = 200
    nswitch = 0 #200

    __config_table_ =   {   'nsamples'  : [ int,    'nsamples'],
                            'nswitch'   : [ int,    'nswitch'],
                        }


    def __init__(self, *args, **kwargs):

        super(PSMonteCarlo2, self).__init__(*args, **kwargs)

        # Add configuration options                
        self.add_config(self.__config_table_)




    def gen_vecs(self, mi):
        """Generate a cache of sample vectors for each bandpower.
        """

        # Delete cache
        self.vec_cache = []

        bt = self.kltrans.beamtransfer
        evals, evecs = self.kltrans.modes_m(mi)
        nbands = len(self.bands) - 1

        # Set of S/N weightings
        cf = (evals + 1.0)**-0.5

        # Generate random set of Z_2 vectors
        xv = 2*(np.random.rand(evals.size, self.nsamples) <= 0.5).astype(np.float) - 1.0

        # Multiply by C^-1 factorization
        xv1 = cf[:, np.newaxis] * xv

        # Project vector from eigenbasis into telescope basis
        xv2 = np.dot(evecs.T.conj(), xv1).reshape(bt.nfreq, bt.ntel, self.nsamples)

        # Get projection matrix from stokes I to telescope
        bp = bt.beam_m(mi)[:, :, :, 0, :].reshape(bt.nfreq, bt.ntel, -1)
        lside = bp.shape[-1]

        # Project with transpose B matrix
        xv3 = np.zeros((bt.nfreq, lside, self.nsamples), dtype=np.complex128)
        for fi in range(bt.nfreq):
            xv3[fi] = np.dot(bp[fi].T.conj(), xv2[fi])

        for bi in range(nbands):

            # Product with sky covariance C_l(z, z')
            xv4 = np.zeros_like(xv3)
            for li in range(lside):
                xv4[:, li, :] = np.dot(self.clarray[bi][0, 0, li], xv3[:, li, :]) # TT only.

            # Projection from sky vector into telescope
            xv5 = np.zeros_like(xv2r)
            for fi in range(bt.nfreq):
                xv5[fi] = np.dot(bp[fi], xv4[fi])

            # Projection into eigenbasis
            xv6 = np.dot(evecs, xv5.reshape(bt.nfreq * bt.ntel, self.nsamples))
            xv7 = cf[:, np.newaxis] * xv6

            # Push set of vectors into cache.
            self.vec_cache.append(xv7)



    def fisher_m_mc(self, mi):
        """Calculate the Fisher Matrix by Monte-Carlo.
        """
            
        nbands = len(self.bands) - 1
        fab = np.zeros((nbands, nbands), dtype=np.complex128)

        if self.num_evals(mi) > 0:
            print "Making fisher (for m=%i)." % mi

            self.gen_vecs(mi)

            ns = self.nsamples

            for ia in range(nbands):
                # Estimate diagonal elements (including bias correction)
                va = self.vec_cache[ia]

                fab[ia, ia] = np.sum(va * va.conj()) / ns

                # Estimate diagonal elements
                for ib in range(ia):
                    vb = self.vec_cache[ib]

                    fab[ia, ib] = np.sum(va * vb.conj()) / ns
                    fab[ib, ia] = np.conj(fab[ia, ib])
            
        else:
            print "No evals (for m=%i), skipping." % mi

        return fab


    def fisher_m(self, mi):
        """Calculate the Fisher Matrix for a given m.

        Decides whether to use direct evaluation or Monte-Carlo depending on the
        number of eigenvalues required.
        """
        if self.num_evals(mi) < self.nswitch:
            return super(PSMonteCarlo, self).fisher_m(mi)
        else:
            return self.fisher_m_mc(mi)
        
