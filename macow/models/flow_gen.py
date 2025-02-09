__author__ = 'max'

import os
import json
import math
from typing import Dict, Tuple
import torch
import torch.nn as nn
from overrides import overrides

from macow.flows.flow import Flow
from macow.flows.parallel import DataParallelFlow
from macow.flows.dequant import DeQuantFlow


class FlowGenModel(nn.Module):
    """
    Flow-based Generative model
    """
    def __init__(self, flow: Flow, ngpu=1, gpu_id=0):
        super(FlowGenModel, self).__init__()
        assert flow.inverse, 'flow based generative should have inverse mode'
        self.flow = flow
        assert ngpu > 0, 'the number of GPUs should be positive.'
        self.ngpu = ngpu
        self.device = None
        if ngpu > 1:
            device_ids = list(range(ngpu))
            device_ids[gpu_id] = 0
            device_ids[0] = gpu_id
            self.device = torch.device('cuda:{}'.format(gpu_id))
            self.flow = DataParallelFlow(self.flow, device_ids=device_ids, output_device=0)

    def sync(self):
        flow = self.flow.flow if isinstance(self.flow, DataParallelFlow) else self.flow
        flow.sync()

    def to_device(self, device):
        if self.device is None:
            return self.to(device)
        else:
            return self.to(self.device)

    def dequantize(self, x, nsamples=1) -> Tuple[torch.Tensor, torch.Tensor]:
        # [batch, nsamples, channels, H, W]
        return x.new_empty(x.size(0), nsamples, *x.size()[1:]).uniform_(), x.new_zeros(x.size(0), nsamples)

    def encode(self, x) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            x: Tensor
                The dequantized input data with shape =[batch, x_shape]

        Returns: z: Tensor, logdet: Tensor, eps: List[Tensor]
            z, the latent variable
            logdet, the log determinant of :math:`\partial z / \partial x`
            Then the density :math:`\log(p(x)) = \log(p(z)) + logdet`
            eps: eps for multi-scale architecture.
        """
        z, logdet = self.flow.bwdpass(x)
        return z, logdet

    def decode(self, z) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            z: Tensor
                The latent code with shape =[batch, *]

        Returns: x: Tensor, logdet: Tensor
            x, the decoded variable
            logdet, the log determinant of :math:`\partial z / \partial x`
            Then the density :math:`\log(p(x)) = \log(p(z)) + logdet`
        """
        x, logdet = self.flow.fwdpass(z)
        return x, logdet

    def init(self, data, init_scale=1.0) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.flow.bwdpass(data, init=True, init_scale=init_scale)

    def log_probability(self, x) -> torch.Tensor:
        """

        Args:
            x: Tensor
                The input data with shape =[batch, x_shape]

        Returns:
            Tensor
            The tensor of the posterior probabilities of x shape = [batch]
        """
        # [batch, x_shape]
        z, logdet = self.encode(x)
        # [batch, x_shape] --> [batch, numels]
        z = z.view(z.size(0), -1)
        # [batch]
        log_probs = z.mul(z).sum(dim=1) + math.log(math.pi * 2.) * z.size(1)
        return log_probs.mul(-0.5) + logdet

    @classmethod
    def from_params(cls, params: Dict) -> "FlowGenModel":
        flow_params = params.pop('flow')
        flow = Flow.by_name(flow_params.pop('type')).from_params(flow_params)
        return FlowGenModel(flow, **params)

    @classmethod
    def load(cls, model_path, device) -> "FlowGenModel":
        params = json.load(open(os.path.join(model_path, 'config.json'), 'r'))
        model_name = os.path.join(model_path, 'model.pt')
        fgen = FlowGenModel.from_params(params)
        fgen.load_state_dict(torch.load(model_name, map_location=device))
        return fgen.to(device)


class VDeQuantFlowGenModel(FlowGenModel):
    def __init__(self, flow: Flow, dequant_flow: Flow, ngpu=1, gpu_ids=(0, 0)):
        flow_gpu_id, dequant_gpu_id = gpu_ids
        super(VDeQuantFlowGenModel, self).__init__(flow, ngpu, flow_gpu_id)
        assert not dequant_flow.inverse, 'dequantization flow should NOT have inverse mode'
        self.dequant_flow = dequant_flow
        self.dequant_device = None
        if ngpu > 1:
            device_ids = list(range(ngpu))
            device_ids[dequant_gpu_id] = 0
            device_ids[0] = dequant_gpu_id
            self.dequant_device = torch.device('cuda:{}'.format(dequant_gpu_id))
            self.dequant_flow = DataParallelFlow(self.dequant_flow, device_ids=device_ids, output_device=0)

    # @overrides
    def to_device(self, device):
        if self.device is None:
            assert self.dequant_device is None
            return self.to(device)
        else:
            self.flow = self.flow.to(self.device)
            self.dequant_flow = self.dequant_flow.to(self.dequant_device)
            return self

    # @overrides
    def dequantize(self, x, nsamples=1) -> Tuple[torch.Tensor, torch.Tensor]:
        batch = x.size(0)
        # [batch * nsamples, channels, H, W]
        epsilon = torch.randn(batch * nsamples, *x.size()[1:], device=x.device)
        if nsamples > 1:
            x = x.unsqueeze(1) + x.new_zeros(batch, nsamples, *x.size()[1:])
            x = x.view(epsilon.size())
        u, logdet = self.dequant_flow.fwdpass(epsilon, x)
        # [batch * nsamples, channels, H, W]
        epsilon = epsilon.view(epsilon.size(0), -1)
        # [batch * nsamples]
        log_posteriors = epsilon.mul(epsilon).sum(dim=1) + math.log(math.pi * 2.) * epsilon.size(1)
        log_posteriors = log_posteriors.mul(-0.5) - logdet
        return u.view(batch, nsamples, *x.size()[1:]), log_posteriors.view(batch, nsamples)

    # @overrides
    def init(self, data, init_scale=1.0) -> Tuple[torch.Tensor, torch.Tensor]:
        # [batch, channels, H, W]
        epsilon = torch.randn(data.size(), device=data.device)
        self.dequant_flow.fwdpass(epsilon, data, init=True, init_scale=init_scale)
        return self.flow.bwdpass(data, init=True, init_scale=init_scale)

    @classmethod
    def from_params(cls, params: Dict) -> "VDeQuantFlowGenModel":
        flow_params = params.pop('flow')
        flow = Flow.by_name(flow_params.pop('type')).from_params(flow_params)
        dequant_params = params.pop('dequant')
        dequant_flow = DeQuantFlow.from_params(dequant_params)
        return VDeQuantFlowGenModel(flow, dequant_flow, **params)

    @classmethod
    def load(cls, model_path, device) -> "VDeQuantFlowGenModel":
        params = json.load(open(os.path.join(model_path, 'config.json'), 'r'))
        model_name = os.path.join(model_path, 'model.pt')
        fgen = VDeQuantFlowGenModel.from_params(params)
        fgen.load_state_dict(torch.load(model_name, map_location=device))
        return fgen.to(device)
