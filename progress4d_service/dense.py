"""Experimental CPU stereo on estimated cameras, with left/right consistency.

Outputs only observed pairwise points in the SfM coordinate system. This is not
yet a validated multi-view fusion or a volume measurement implementation.
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
import pycolmap


def calibration(camera):
    if camera.model_name!='SIMPLE_RADIAL':
        raise ValueError('Only SIMPLE_RADIAL camera currently supported')
    return camera.calibration_matrix(),np.array([camera.params[3],0.,0.,0.,0.])


def run(model_path,images,output):
    reconstruction=pycolmap.Reconstruction(model_path)
    images,output=Path(images),Path(output)
    if output.exists(): raise ValueError('Output must be new')
    output.mkdir(parents=True)
    cv2.setNumThreads(2)
    views=sorted(reconstruction.images.values(),key=lambda i:i.name)
    clouds=[]; colors=[]; reports=[];depth_views=[]
    for left,right in zip(views,views[1:]):
        left_tracks={p.point3D_id for p in left.points2D if p.has_point3D()}
        right_tracks={p.point3D_id for p in right.points2D if p.has_point3D()}
        support=len(left_tracks & right_tracks)
        if support<30: continue
        first=cv2.imread(str(images/left.name));second=cv2.imread(str(images/right.name))
        if first is None or second is None or first.shape!=second.shape: continue
        height,width=first.shape[:2]
        k1,d1=calibration(reconstruction.cameras[left.camera_id])
        k2,d2=calibration(reconstruction.cameras[right.camera_id])
        m1=left.cam_from_world().matrix();m2=right.cam_from_world().matrix()
        rotation=m2[:,:3] @ m1[:,:3].T
        translation=m2[:,3]-rotation @ m1[:,3]
        if np.linalg.norm(translation)<1e-6:continue
        r1,r2,p1,p2,q,roi1,roi2=cv2.stereoRectify(k1,d1,k2,d2,(width,height),
            np.ascontiguousarray(rotation),np.ascontiguousarray(translation.reshape(3,1)),alpha=0)
        # OpenCV SGBM here supports horizontal rectification only.
        if abs(p2[1,3])>abs(p2[0,3]):continue
        mx1,my1=cv2.initUndistortRectifyMap(k1,d1,r1,p1,(width,height),cv2.CV_32FC1)
        mx2,my2=cv2.initUndistortRectifyMap(k2,d2,r2,p2,(width,height),cv2.CV_32FC1)
        a=cv2.remap(first,mx1,my1,cv2.INTER_LINEAR)
        b=cv2.remap(second,mx2,my2,cv2.INTER_LINEAR)
        ga=cv2.cvtColor(a,cv2.COLOR_BGR2GRAY);gb=cv2.cvtColor(b,cv2.COLOR_BGR2GRAY)
        # Near walls need much larger disparities than distant room ends.
        # Derive a bounded search interval from image-reconstructed tracks,
        # never from IFC/ground-truth depths.
        track_points=np.array([reconstruction.points3D[t].xyz for t in left_tracks & right_tracks])
        track_rectified=(track_points@m1[:,:3].T+m1[:,3])@r1.T
        depths=track_rectified[:,2];depths=depths[depths>1e-5]
        disparities=-p2[0,3]/depths+p1[0,2]-p2[0,2]
        disparities=disparities[np.isfinite(disparities)&(np.abs(disparities)<width-32)]
        if len(disparities)<20:continue
        lo,hi=np.quantile(disparities,[.02,.98])
        minimum=max(-width+16,min(width-80,int(np.floor((lo-16)/16)*16)))
        nd=int(np.ceil((hi+16-minimum)/16)*16)
        nd=max(64,min(nd,512,((width-16-minimum)//16)*16))
        params=dict(numDisparities=nd,blockSize=5,P1=8*25,P2=32*25,
                    uniquenessRatio=12,speckleWindowSize=100,speckleRange=2,disp12MaxDiff=1)
        dl=cv2.StereoSGBM_create(minDisparity=minimum,**params).compute(ga,gb).astype(np.float32)/16
        dr=cv2.StereoSGBM_create(minDisparity=-minimum-nd,**params).compute(gb,ga).astype(np.float32)/16
        yy,xx=np.indices(dl.shape)
        xr=np.rint(xx-dl).astype(int)
        valid=(xr>=0)&(xr<width)&(dl>minimum)&(np.abs(dl)>1)
        sampled=dr[yy,np.clip(xr,0,width-1)]
        valid &= np.abs(dl+sampled)<1.
        # Reject flat patches rather than manufacturing a smooth measured wall.
        mean=cv2.blur(ga.astype(np.float32),(7,7))
        variance=cv2.blur(ga.astype(np.float32)**2,(7,7))-mean**2
        valid &= variance>8
        for roi, horizontal in [(roi1,xx),(roi2,xr)]:
            x,y,w,h=roi
            valid &= (horizontal>=x)&(horizontal<x+w)&(yy>=y)&(yy<y+h)
        points=cv2.reprojectImageTo3D(dl,q)
        valid &= np.isfinite(points).all(axis=2)&(points[:,:,2]>0)
        depth=np.where(valid,points[:,:,2],0).astype(np.float32)
        depth_views.append(dict(depth=depth,world_to_camera=m1,rectification=r1,
                                projection=p1[:,:3],name=left.name))
        valid &= (xx%3==0)&(yy%3==0)
        xyz=points[valid] @ r1
        xyz=(xyz-m1[:,3]) @ m1[:,:3]
        clouds.append(xyz);colors.append(a[valid,::-1])
        reports.append(dict(left=left.name,right=right.name,shared_tracks=support,points=len(xyz)))
    xyz=np.concatenate(clouds) if clouds else np.empty((0,3))
    rgb=np.concatenate(colors) if colors else np.empty((0,3))
    raw_count=len(xyz)
    support=np.zeros(raw_count,dtype=np.uint16)
    for view in depth_views:
        camera=xyz@view['world_to_camera'][:,:3].T+view['world_to_camera'][:,3]
        rectified=camera@view['rectification'].T
        projection=rectified@view['projection'].T
        z=rectified[:,2]
        safe=np.where(z>1e-6,z,1)
        x=np.rint(projection[:,0]/safe).astype(int)
        y=np.rint(projection[:,1]/safe).astype(int)
        height,width=view['depth'].shape
        inside=(z>1e-6)&(x>=0)&(x<width)&(y>=0)&(y<height)
        measured=view['depth'][np.clip(y,0,height-1),np.clip(x,0,width-1)]
        support += (inside&(measured>0)&(np.abs(measured-z)<=.01*z)).astype(np.uint16)
    accepted=support>=3
    xyz=xyz[accepted];rgb=rgb[accepted]
    with (output/'observed-stereo.ply').open('w') as stream:
        stream.write('ply\nformat ascii 1.0\nelement vertex '+str(len(xyz))+'\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n')
        for p,c in zip(xyz,rgb):stream.write(' '.join(map(str,[*p,*c]))+'\n')
    report=dict(points=len(xyz),raw_pairwise_points=raw_count,pairs=reports,metric_scale_known=False,
                disparity_search='sparse_track_adaptive_max512',
                minimum_independent_depth_views=3,relative_depth_tolerance=.01,
                validated_for_quantities=False,kind='experimental_multiview_stereo',
                limitations=['No surface fusion or metric registration yet.',
                             'Textureless and occluded surfaces remain absent.'])
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('images');p.add_argument('output')
    a=p.parse_args();print(json.dumps(run(a.model,a.images,a.output),indent=2))
