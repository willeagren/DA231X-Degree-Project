"""
Signal SimCLR sampler implementation, inherits from the base
PretextSampler, and depends on the ContrastiveViewGenerator
object to perform data augmentations. 

Signal augmentations are implemented in the neurocode.datautil
module, in transforms.py file, and currently only has implementations
for Crop&Resize and Permutation transforms. TODO is to implement 
more and see if they result in better features, but literature shows
results that indicated these two existing transforms are 'optimal'.

Recap: the SimCLR pipeline constitutes of four main modules:
    1: data augmentation T
    2: encoder model f()
    3: projection head g()
    4: contrastive loss function NTXentLoss

Authors: Wilhelm Ågren <wagren@kth.se>
Last edited: 14-02-2022
"""
import torch
import numpy as np

from .base import PretextSampler
from ..datautil import CropResizeTransform, PermutationTransform, AmplitudeScaleTransform, ZeroMaskingTransform


class ContrastiveViewGenerator(object):
    """callable object that generated n_views
    of the given input data x. 

    Attributes
    ----------
    T: tuple | list
        A collection holding the transforms, either torchvision.transform or
        BaseTransform from neurocode.datautil, number of transforms should
        be the same as n_views. No stochastic choice on transform is made
        in this module, but could be implemented.
    n_views: int
        The number dictating the amount of augmentations/transformations 
        to apply to input x, and decides the length of the resulting list
        after invoking __call__ on the object.

    """
    def __init__(self, T, n_views):
        self.transforms = T
        self.n_views = n_views
    
    def __call__(self, x):
        return [torch.Tensor(self.transforms[t](x)) for t in range(self.n_views)]


class SignalSampler(PretextSampler):
    """pretext task sampler for the SimCLR pipeline,
    applying data augmentations T to signals S(t), of the SLEMEG
    data. Currently only applies to transformations,
    CropResizeTransformation and PermutationTransformation. 
    Literature has showed their strength together, but could be
    insightful to explore other transformations as well.

    Attributes
    ----------
    n_channels: int
        The amount of signal channels that is included in the input data.
        Decides the resulting dimensionality of the samples tensors.
    n_views: int
        Number of transformations to apply to the original signal S(t).
    crop_partitions: int
        The number of partitions to create when performing CropResize
        transformation. TODO investigate how this effects learning.
    permutation_partitions: int
        The number of partitions to create when performing Permutation
        transformation. TODO investigate how this effects learning.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _parameters(self, n_channels, n_views=2, crop_partitions=5, 
            permutation_partitions=10, **kwargs):
        self.n_channels = n_channels
        self.n_views = n_views

        self._transforms = [
                CropResizeTransform(n_partitions=crop_partitions),
                PermutationTransform(n_partitions=permutation_partitions)
                #ZeroMaskingTransform(samples=.30),
                #AmplitudeScaleTransform()
                ]

        self.transformer = ContrastiveViewGenerator(
                self._transforms, n_views)

    def _sample_pair(self):
        """sample two augmented views (t1, t2) of the same original
        signal S(t), as per the SimCLR framework. Randomly picks a 
        recording and corresponding window to transform. The transformation
        is performed directly on the signal, and supports both single- and
        multi-channel data. To see the specific parameters for the transforms,
        see the neurocode.datautil.transforms module. 

        Returns
        -------
        ANCHORS: torch.tensor
            The signals which has had the first transform applied to them.
            Resulting shape should be (N, 1, C, T) where N is the batch size,
            C is the amount of signal channels included, and T is the size of
            the time window. 
        SAMPLES: torch.tensor
            Same as above, but second transformation was applied to the original
            signal S(t). See neurocode.datautil.transforms for documentation.
        """
        batch_anchors = list()
        batch_samples = list()
        for _ in range(self.batch_size):
            reco_idx = self._sample_recording()
            wind_idx = self._sample_window(recording_idx=reco_idx)

            x = self.data[reco_idx][wind_idx][0]
            T1, T2 = self.transformer(x)

            """
            import matplotlib.pyplot as plt
            plt.style.use('seaborn')
            plt.rcParams['figure.dpi'] = 300
            plt.rcParams['savefig.dpi'] = 300
            fig, axs = plt.subplots()

            #for channel in range(3):
            #axs[channel, 0].plot(x[channel, :])
            #axs[channel, 1].plot(T1.numpy()[channel, :])
            axs.plot(x[0, :])
            plt.show()
            """

            batch_anchors.append(T1.unsqueeze(0))
            batch_samples.append(T2.unsqueeze(0))

        ANCHORS = torch.cat(batch_anchors).unsqueeze(1)
        SAMPLES = torch.cat(batch_samples).unsqueeze(1)

        return (ANCHORS, SAMPLES)

    def _extract_features(self, model, device):
        """heuristically sample windows from each
        recording and use f() to extract features. 
        Labels are pairwise sampled to the corresponding
        features, otherwise the tSNE plots are useless.

        Parameters
        ----------
        model: torch.nn.Module
            The neural network model, encoder f(), which is yet to be 
            or has already been trained and is used to perform the 
            feature extraction. Make sure to set the model in evaluation
            mode so that batch normalization layers and dropout layers
            are inactivated; otherwise you get inaccurate features.
            Furthermore, enable returning of features, otherwise the 
            features are fed to the projection head g() and we are not
            interested in the features in the mapped space.
        device: torch.device | str
            The device on which to perform feature extraction, either
            CPU, CUDA or some GPU:0...N, should be the same as that 
            of the provided model.

        Returns
        -------
        X: np.array
            The extracted features, casted to numpy arrays and forced to 
            move to the CPU if they were on another device. The amount
            of features to extract is a bit arbitrary, and depends
            on the window_size_s of the pipeline and the amount of
            recordings provided to the sampler instance.
        Y: np.array
            The corresponding labels for the extracted features. Used
            such that the tSNE plots can be labeled accordingly, and 
            has to be the same length as X.

        """
        X, Y = [], []
        model.eval()
        model._return_features = True
        with torch.no_grad():
            for recording in range(len(self.data)):
                for window in range(len(self.data[recording])):
                    if window % 1 == 0:
                        window = torch.Tensor(self.data[recording][window][0][None]).float().to(device)
                        feature = model(window.unsqueeze(0))
                        X.append(feature[0, :][None])
                        Y.append(*self.labels[recording])
        X = np.concatenate([x.cpu().detach().numpy() for x in X], axis=0)
        model._return_features = False

        return (X, Y)
