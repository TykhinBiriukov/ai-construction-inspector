# Open-source algorithms used by Geometry Engine

## Open3D: RGB-D surface reconstruction

`geometry_engine/reconstruction.py` calls the Open3D 0.20.0 CPU
`VoxelBlockGrid` TSDF integration and Marching Cubes extraction APIs. The
application adds ARKit input adaptation, confidence filtering, conservative
cross-view diagnostics, stage reports and exports. Open3D is an MIT-licensed
dependency; its license is included in the installed distribution.

- [Upstream TSDF integration example](https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/integration.html)
- [VoxelBlockGrid API](https://www.open3d.org/docs/latest/python_api/open3d.t.geometry.VoxelBlockGrid.html)
- [Open3D source and license](https://github.com/isl-org/Open3D)

## Open3D: RANSAC plane detection

`geometry_engine/wall_plane_detection.py` adapts the three-point RANSAC
hypotheses, distance-based support/RMSE ranking, adaptive iteration bound and
least-squares refinement sequence from Open3D's `PointCloud::SegmentPlane`.
This is a NumPy adaptation, not an invocation of the Open3D package. It adds
area-uniform mesh sampling, triangle-normal filtering, area-weighted refinement
and correspondence to the original mesh faces. Only SAM wall-labeled faces
participate in plane sampling, fitting, refinement and final support.

Pinned upstream version: Open3D 0.19.0, commit
`1e7b17438687a0b0c1e5a7187321ac7044afe275`.

- [PointCloudSegmentation.cpp](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/cpp/open3d/geometry/PointCloudSegmentation.cpp)
- [Upstream MIT license](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/LICENSE)

The source file identifies Copyright (c) 2018-2024 www.open3d.org. The
repository license is reproduced below:

```text
The MIT License (MIT)

Open3D: www.open3d.org
Copyright (c) 2018-2023 www.open3d.org
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

SPDX-License-Identifier: MIT
```

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
