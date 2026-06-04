# A Multilevel Monte Carlo Asymptotic-Preserving Particle Method for Kinetic Equations in the Diffusion Limit

**Source**: arXiv paper · http://arxiv.org/abs/1902.04347v4

## Abstract

We propose a multilevel Monte Carlo method for a particle-based asymptotic-preserving scheme for kinetic equations. Kinetic equations model transport and collision of particles in a position-velocity phase-space. With a diffusive scaling, the kinetic equation converges to an advection-diffusion equation in the limit of zero mean free path. Classical particle-based techniques suffer from a strict time-step restriction to maintain stability in this limit. Asymptotic-preserving schemes provide a solution to this time step restriction, but introduce a first-order error in the time step size. We demonstrate how the multilevel Monte Carlo method can be used as a bias reduction technique to perform accurate simulations in the diffusive regime, while leveraging the reduced simulation cost given by the asymptotic-preserving scheme. We describe how to achieve the necessary correlation between simulation paths at different levels and demonstrate the potential of the approach via numerical experiments.
