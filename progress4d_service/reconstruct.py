"""CPU image-only sparse reconstruction gate. Not a dense as-built mesh.

This module deliberately has no IFC or evaluator input, preventing ground-truth
camera poses or geometry from being used to manufacture a successful reconstruction.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import pycolmap


def reconstruct(images, output, threads=2):
    images,output=Path(images),Path(output)
    names=sorted(p.name for p in images.iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png'))
    if len(names)<8: raise ValueError('At least eight overlapping photos required for reconstruction attempt')
    if output.exists() and any(output.iterdir()): raise ValueError('Use a new output directory; existing reconstruction is immutable')
    output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    inventory=[dict(name=n,sha256=hashlib.sha256((images/n).read_bytes()).hexdigest()) for n in names]
    (output/'input-inventory.json').write_text(json.dumps(inventory,indent=2))
    extraction=pycolmap.FeatureExtractionOptions()
    extraction.num_threads=threads
    extraction.max_image_size=1000
    extraction.sift.max_num_features=6000
    extraction.sift.peak_threshold=.002
    extraction.sift.estimate_affine_shape=True
    extraction.sift.domain_size_pooling=True
    matching=pycolmap.FeatureMatchingOptions()
    matching.num_threads=threads
    matching.guided_matching=True
    reader=pycolmap.ImageReaderOptions()
    reader.camera_model='SIMPLE_RADIAL'
    database=output/'database.db'
    pycolmap.extract_features(database,images,image_names=names,
        camera_mode=pycolmap.CameraMode.SINGLE,reader_options=reader,
        extraction_options=extraction,device=pycolmap.Device.cpu)
    pairing=pycolmap.SequentialPairingOptions()
    pairing.overlap=10
    pairing.quadratic_overlap=True
    pycolmap.match_sequential(database,pairing_options=pairing,matching_options=matching,device=pycolmap.Device.cpu)
    options=pycolmap.IncrementalPipelineOptions()
    options.num_threads=threads
    options.random_seed=629
    options.max_runtime_seconds=900
    options.min_model_size=8
    models=pycolmap.incremental_mapping(database,images,output/'sparse',options=options)
    best=max(models.values(),key=lambda m:m.num_reg_images(),default=None)
    selected_id=str(next((k for k,v in models.items() if v is best),''))
    matching_kind='ordered_capture_sequential_overlap10'
    # A clean room can split an ordered orbit into disconnected halves. Complete
    # image pairing supplies loop closure without camera poses or design geometry.
    if best is None or best.num_reg_images()/len(names)<.8:
        pycolmap.match_exhaustive(database,matching_options=matching,device=pycolmap.Device.cpu)
        fallback=pycolmap.incremental_mapping(database,images,output/'exhaustive',options=options)
        candidate=max(fallback.values(),key=lambda m:m.num_reg_images(),default=None)
        if candidate is not None and (best is None or candidate.num_reg_images()>best.num_reg_images()):
            best=candidate
            selected_id='exhaustive-'+str(next(k for k,v in fallback.items() if v is best))
            destination=output/'sparse'/selected_id;destination.mkdir(parents=True,exist_ok=False)
            best.write(destination)
            matching_kind='exhaustive_fallback_after_incomplete_ordered_capture'
    report=dict(status='insufficient_reconstruction',photo_count=len(names),registered_images=0,
        points=0,metric_scale_known=False,kind='sparse_photogrammetry',matching=matching_kind,
        limitations=['Sparse points are not a dense surface or installed-volume estimate.',
                     'Reconstruction has arbitrary similarity scale until independently registered.'])
    if best is not None:
        best.export_PLY(str(output/'sparse.ply'))
        report['selected_model']=selected_id
        report.update(registered_images=best.num_reg_images(),points=best.num_points3D(),
                      mean_reprojection_error_px=best.compute_mean_reprojection_error())
        if best.num_reg_images()/len(names)>=.8 and best.num_points3D()>=500:
            report['status']='sparse_reconstructed'
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('images',type=Path); parser.add_argument('output',type=Path)
    parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args()
    print(json.dumps(reconstruct(args.images,args.output,args.threads),indent=2))
