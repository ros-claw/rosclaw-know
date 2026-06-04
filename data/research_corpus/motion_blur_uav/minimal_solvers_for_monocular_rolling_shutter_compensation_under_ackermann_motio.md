# Minimal Solvers for Monocular Rolling Shutter Compensation under Ackermann Motion

**Source**: arXiv paper · http://arxiv.org/abs/1712.03159v1

## Abstract

Modern automotive vehicles are often equipped with a budget commercial rolling shutter camera. These devices often produce distorted images due to the inter-row delay of the camera while capturing the image. Recent methods for monocular rolling shutter motion compensation utilize blur kernel and the straightness property of line segments. However, these methods are limited to handling rotational motion and also are not fast enough to operate in real time. In this paper, we propose a minimal solver for the rolling shutter motion compensation which assumes known vertical direction of the camera. Thanks to the Ackermann motion model of vehicles which consists of only two motion parameters, and two parameters for the simplified depth assumption that lead to a 4-line algorithm. The proposed minimal solver estimates the rolling shutter camera motion efficiently and accurately. The extensive experiments on real and simulated datasets demonstrate the benefits of our approach in terms of qualitative and quantitative results.
