# Open-source algorithms used by Geometry Engine

## Open3DIS: depth agreement, region relations and cross-view grouping

`geometry_engine/mask_projection.py` adapts the depth-consistency test in
`PointCloudToImageMapper.compute_mapping` to the nearest mesh triangle at each
pixel. It uses camera-Z depth, absolute plus relative tolerances and optional
sensor confidence. Triangle rasterization in `visibility.py` is an independent
NumPy implementation of a perspective-correct z-buffer; it is not copied from
Open3DIS. No Open3DIS weights, CUDA runtime or local SAM model are required.

`geometry_engine/mask_fusion.py` adapts the region intersection, union, IoU,
directional recall and overlap graph approach from Open3DIS. The implementation
uses CPU NumPy arrays and mesh face areas instead of CUDA tensors and point
counts. It adds per-frame vote caps, semantic conflict rejection and a constraint
that two observations from the same image cannot join one instance through a
transitive graph chain. This is a component adaptation, not the complete
Open3DIS method, and does not use its learned 3D features or proposal network.

Pinned upstream commit: `bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e`.

- [`compute_relation_matrix_self_mem` in clustering_utils.py](https://github.com/VinAIResearch/Open3DIS/blob/bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e/open3dis/src/clustering/clustering_utils.py)
- [`PointCloudToImageMapper` in mapper.py](https://github.com/VinAIResearch/Open3DIS/blob/bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e/open3dis/src/mapper.py)
- [`hierarchical_agglomerative_clustering_nospp` in clustering.py](https://github.com/VinAIResearch/Open3DIS/blob/bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e/open3dis/src/clustering/clustering.py)
- [Paper: Open3DIS: Open-Vocabulary 3D Instance Segmentation with 2D Mask Guidance, CVPR 2024](https://arxiv.org/abs/2312.10671)
- [Upstream license](https://github.com/VinAIResearch/Open3DIS/blob/bc2a3b3ee7161f5754ad7a7fe77202e9a66adc8e/LICENSE)

The upstream BSD 3-Clause license is reproduced below:

```text
Copyright (c) 2024, VinAI. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```
