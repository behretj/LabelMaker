import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

import cv2
import gin
import matplotlib.pyplot as plt
import mmcv
import numpy as np
import PIL
import torch
import torch.nn.functional as F
from joblib import Parallel, delayed
from PIL import Image
from torchvision import transforms
from tqdm import tqdm

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '3rdparty', 'omnidata',
                     'omnidata_tools', 'torch')))

from data.transforms import get_transform
from modules.midas.dpt_depth import DPTDepthModel
from modules.unet import UNet

logging.basicConfig(level="INFO")
log = logging.getLogger('Omnidata Depth')


def load_omnidepth():
  map_location = (lambda storage, loc: storage.cuda()
                 ) if torch.cuda.is_available() else torch.device('cpu')
  device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

  log.info('loading model')
  omnidata_path = Path(os.path.abspath(os.path.dirname(
      __file__))) / '..' / '3rdparty' / 'omnidata' / 'omnidata_tools' / 'torch'
  pretrained_weights_path = omnidata_path / 'pretrained_models' / 'omnidata_dpt_depth_v2.ckpt'
  model = DPTDepthModel(backbone='vitb_rn50_384')
  checkpoint = torch.load(pretrained_weights_path, map_location=map_location)
  if 'state_dict' in checkpoint:
    state_dict = {}
    for k, v in checkpoint['state_dict'].items():
      state_dict[k[6:]] = v
  else:
    state_dict = checkpoint
  model.load_state_dict(state_dict)
  model.to(device)
  return model


# @gin.configurable()
# def standardize_depth_map(img, mask_valid=None, trunc_value=0.1):
#     if mask_valid is not None:
#         img[~mask_valid] = torch.nan
#     sorted_img = torch.sort(torch.flatten(img))[0]
#     # Remove nan, nan at the end of sort
#     num_nan = sorted_img.isnan().sum()
#     if num_nan > 0:
#         sorted_img = sorted_img[:-num_nan]
#     # Remove outliers
#     trunc_img = sorted_img[int(trunc_value *
#                                len(sorted_img)):int((1 - trunc_value) *
#                                                     len(sorted_img))]
#     trunc_mean = trunc_img.mean()
#     trunc_var = trunc_img.var()
#     eps = 1e-6
#     # Replace nan by mean
#     img = torch.nan_to_num(img, nan=trunc_mean)
#     # Standardize
#     img = (img - trunc_mean) / torch.sqrt(trunc_var + eps)
#     return img

# @gin.configurable()
# def run_inference(img, depth_size=(480, 640)):
#     with torch.no_grad():
#         img_tensor = trans_totensor(img)[:3].unsqueeze(0).to(device)
#         if img_tensor.shape[1] == 1:
#             img_tensor = img_tensor.repeat_interleave(3, 1)
#         output = model(img_tensor).clamp(min=0, max=1)
#         output = F.interpolate(output.unsqueeze(0), depth_size,
#                                mode='bicubic').squeeze(0)
#         output = output.clamp(0, 1)
#         #output = 1 - output
#         #output = standardize_depth_map(output)
#         return output.detach().cpu().squeeze()


def omnidepth_completion(args, patch_size=32):

  scene_dir = Path(args.workspace)

  log.info('[omnidepth] running completion')

  assert (scene_dir / args.output).exists()

  def depth_completion(k):
    orig_depth = cv2.imread(str(scene_dir / 'depth' / f'{k}.png'),
                            cv2.IMREAD_UNCHANGED)
    omnidepth = cv2.imread(str(scene_dir / args.output / f'{k}.png'),
                           cv2.IMREAD_UNCHANGED)

    # now complete the original depth with omnidepth predictions, fitted to scale
    # within a patch around each missing pixel
    fused_depth = orig_depth.copy()
    coords_u, coords_v = np.where(fused_depth == 0)
    for i in range(len(coords_u)):
      u = coords_u[i]
      v = coords_v[i]
      window_u = max(0, u - patch_size), min(fused_depth.shape[0],
                                             u + patch_size)
      window_v = max(0, v - patch_size), min(fused_depth.shape[1],
                                             v + patch_size)
      target = orig_depth[window_u[0]:window_u[1], window_v[0]:window_v[1]]
      source = omnidepth[window_u[0]:window_u[1], window_v[0]:window_v[1]]
      source = source[target != 0]
      target = target[target != 0]
      a, b = np.linalg.lstsq(np.stack([source, np.ones_like(source)], axis=-1),
                             target,
                             rcond=None)[0]
      # for some areas this will completely break the geometry, we need to revert to omnidepth
      if a < 0.5 or a > 2:
        fused_depth[u, v] = omnidepth[u, v]
      else:
        fused_depth[u, v] = a * omnidepth[u, v] + b
    fused_depth[fused_depth == 0] = omnidepth[fused_depth == 0]
    cv2.imwrite(str(scene_dir / args.output / f'{k}.png'), fused_depth)

  keys = [p.stem for p in (Path(args.workspace) / 'depth').glob('*.png')]
  Parallel(n_jobs=8)(delayed(depth_completion)(k) for k in tqdm(keys))


def run(args, depth_size=(192, 256), completion=True):

  log.info('[omnidepth] loading model')
  device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
  model = load_omnidepth()
  trans_totensor = transforms.Compose([
      transforms.Resize((384, 384), interpolation=PIL.Image.BILINEAR),
      transforms.ToTensor(),
      transforms.Normalize(mean=0.5, std=0.5)
  ])

  log.info('[omnidepth] running inference')

  scene_dir = Path(args.workspace)
  output_dir = Path(args.workspace) / args.output

  shutil.rmtree(output_dir, ignore_errors=True)
  (output_dir).mkdir(exist_ok=False)

  keys = [p.stem for p in (Path(args.workspace) / args.input).glob('*.jpg')]

  for k in tqdm(keys):

    img = Image.open(str(scene_dir / args.input / f'{k}.jpg'))
    with torch.no_grad():
      img_tensor = trans_totensor(img)[:3].unsqueeze(0).to(device)
      if img_tensor.shape[1] == 1:
        img_tensor = img_tensor.repeat_interleave(3, 1)
      output = model(img_tensor).clamp(min=0, max=1)
      output = F.interpolate(output.unsqueeze(0), depth_size,
                             mode='bicubic').squeeze(0)
      output = output.clamp(0, 1)
      omnidepth = output.detach().cpu().squeeze().numpy()

    # find a linear scaling a * depth + b to fit to original depth
    orig_depth = cv2.imread(str(scene_dir / 'depth' / f'{k}.png'),
                            cv2.IMREAD_UNCHANGED)
    targets = orig_depth[orig_depth != 0]
    source = omnidepth[orig_depth != 0]
    a, b = np.linalg.lstsq(np.stack([source, np.ones_like(source)], axis=-1),
                           targets,
                           rcond=None)[0]
    omnidepth = (a * omnidepth + b).astype(orig_depth.dtype)
    cv2.imwrite(str(output_dir / f'{k}.png'), omnidepth)
  if completion:
    omnidepth_completion(args)


def main(args):
  run(args)


def arg_parser():
  parser = argparse.ArgumentParser(description='Omnidata Depth Estimation')
  parser.add_argument('--workspace',
                      type=str,
                      required=True,
                      help='Path to workspace directory')
  parser.add_argument('--input',
                      type=str,
                      default='color',
                      help='Name of input directory in the workspace directory')
  parser.add_argument(
      '--output',
      type=str,
      default='intermediate/depth_omnidata_1',
      help=
      'Name of output directory in the workspace directory intermediate. Has to follow the pattern $labelspace_$model_$version'
  )
  parser.add_argument('--config', help='Name of config file')
  return parser.parse_args()


if __name__ == "__main__":
  args = arg_parser()
  main(args)
