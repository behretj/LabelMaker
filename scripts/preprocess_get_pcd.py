### PS: I don't know where should I put this code to, so I put it here
# this code is a step of preprocessing, given color, depth, intr and pose, it runs tsdf fusion and get an RGB point cloud
import os
from os.path import exists, join

import cv2
import numpy as np
import open3d as o3d
from PIL import Image
from tqdm import tqdm


def get_pcd(scan_dir: str):

  color_dir = join(scan_dir, 'color')
  depth_dir = join(scan_dir, 'depth')
  pose_dir = join(scan_dir, 'pose')
  intr_dir = join(scan_dir, 'intr')

  assert exists(color_dir)
  assert exists(depth_dir)
  assert exists(pose_dir)
  assert exists(intr_dir)

  color_list = os.listdir(color_dir)
  color_list.sort(key=lambda e: int(e[:-4]))

  depth_list = os.listdir(depth_dir)
  depth_list.sort(key=lambda e: int(e[:-4]))

  pose_list = os.listdir(pose_dir)
  pose_list.sort(key=lambda e: int(e[:-4]))

  intr_list = os.listdir(intr_dir)
  intr_list.sort(key=lambda e: int(e[:-4]))

  # see if all files exists
  assert all(
      (a[:-4] == b[:-4]) and (a[:-4] == c[:-4]) and (a[:-4] == d[:-4])
      for a, b, c, d in zip(color_list, depth_list, pose_list, intr_list))

  tsdf = o3d.pipelines.integration.ScalableTSDFVolume(
      sdf_trunc=0.06,
      voxel_length=0.02,
      color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)

  for color_f, depth_f, pose_f, intr_f in tqdm(
      zip(color_list, depth_list, pose_list, intr_list)):

    intr = np.loadtxt(join(intr_dir, intr_f))
    pose = np.loadtxt(join(pose_dir, pose_f))
    color = np.asanyarray(Image.open(join(color_dir, color_f))).astype(np.uint8)
    depth = np.asarray(Image.open(join(depth_dir, depth_f))).astype(np.uint16)

    h, w, _ = color.shape

    color = o3d.geometry.Image(color)
    depth = o3d.geometry.Image(depth)

    rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
        color=color,
        depth=depth,
        depth_scale=1000.0,
        depth_trunc=3.0,
        convert_rgb_to_intensity=False)

    tsdf.integrate(
        image=rgbd,
        intrinsic=o3d.camera.PinholeCameraIntrinsic(
            height=h,
            width=w,
            intrinsic_matrix=intr,
        ),
        extrinsic=pose,
    )

  pcd = tsdf.extract_point_cloud()
  o3d.io.write_point_cloud(join(scan_dir, 'pointcloud.ply'), pcd)


get_pcd('/home/quanta/Experiments/LabelMaker/ARKitScene_test')
