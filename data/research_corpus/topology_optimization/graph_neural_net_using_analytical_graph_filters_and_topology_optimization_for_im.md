# Graph Neural Net using Analytical Graph Filters and Topology Optimization for Image Denoising

**Source**: arXiv paper · http://arxiv.org/abs/1911.03975v2

## Abstract

While convolutional neural nets (CNNs) have achieved remarkable performance for a wide range of inverse imaging applications, the filter coefficients are computed in a purely data-driven manner and are not explainable. Inspired by an analytically derived CNN by Hadji et al., in this paper we construct a new layered graph neural net (GNN) using GraphBio as our graph filter. Unlike convolutional filters in previous GNNs, our employed GraphBio is analytically defined and requires no training, and we optimize the end-to-end system only via learning of appropriate graph topology at each layer. In signal filtering terms, it means that our linear graph filter at each layer is always intrepretable as low-pass with known biorthogonal conditions, while the graph spectrum itself is optimized via data training. As an example application, we show that our analytical GNN achieves image denoising performance comparable to a state-of-the-art CNN-based scheme when the training and testing data share the same statistics, and when they differ, our analytical GNN outperforms it by more than 1dB in PSNR.
