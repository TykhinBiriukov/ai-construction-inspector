# Real-room review and reference verification

## Completed Inspector and Verifier review

A read-only check of the live Inspector API on 4 November recovered the completed review **9e859592-5027-5f16-94a0-7eae67034277**, last updated **2 November 2026, 18:53:56 UTC**. All **11 of 11 batches** completed. The [review report](completed-review.json) preserves case decisions, evidence identities, reviewer task IDs, review hashes and supplied measurements, without private chat history or credentials.

Source bindings were checked against the saved session: capture `2b6b4560-c2d5-482d-880b-21f34502b9f2`, Geometry job `54a47af7-ec32-4275-aee2-d07bd92550b1`, IFC SHA256 `4a618081be81cad4b7bba0eec22486038bc96d37e4778276d2f20a7d8143c51d`, and result-manifest SHA256 `e391ca94ecf25cd110fb2974cfce11e88cb4e212f42a493d3baa1eb4cc60b3d8`.

The completed review contains **8 needs-more-data decisions and 3 rejected discrepancy candidates**. No discrepancy was confirmed. Completion establishes that the review ran; it does not mean all objects passed an accuracy or construction-acceptance test. The earlier cancelled attempt belongs to a different chat and remains preserved in the historical receipt.

## Executed reference-dimension verification

The [verification script](verify_reference.py) uses only Python's standard library. It validates package file hashes, reads door/window dimensions from the actual IFC, recomputes retained 3D contour edge lengths, and verifies both reference bindings and signed-difference arithmetic. All four available matched-object dimension comparisons passed these numerical consistency checks.

| Candidate | Dimension | Laser-derived IFC | Retained contour | Scan − IFC |
|---|---|---:|---:|---:|
| Door `mesh_door_002` | Width | 919.00 mm | 895.96 mm | −23.04 mm |
| Door `mesh_door_002` | Height | 2173.00 mm | 2158.62 mm | −14.38 mm |
| Window `mesh_window_004` | Width | 3331.00 mm | 3228.92 mm | −102.08 mm |
| Window `mesh_window_004` | Height | 1513.00 mm | 1499.97 mm | −13.03 mm |

[Machine-readable verification result](reference-verification.json).

The team confirmed that the reference IFC was built from laser measurements. These checks independently recompute the retained numerical comparison; they do not reacquire geometry or establish that partially supported contour edges represent the exact same physical boundaries as the laser measurements. `reference_comparison_verified` is true for these four checks. Absolute-accuracy and whole-element verification remain unestablished where boundary evidence is incomplete. The four unmatched candidates have no corresponding complete reference measurement in this result.

Run against the extracted [real-room evidence archive](../2026-11-03/data-access.md):

```sh
python3 docs/evidence/2026-11-04/verify_reference.py /path/to/geometry-real-room-v1 --output /tmp/reference-verification.json
```

The verification was also checked against a deliberately changed IFC file and a deliberately incorrect reported delta; both were rejected. Original archived computation and review outputs were not rewritten.
