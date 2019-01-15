__author__ = 'max'

from overrides import overrides
from typing import Tuple, Dict
import torch
import torch.nn as nn

from macow.flows.flow import Flow
from macow.nnet import Conv2dWeightNorm


class NICEBlock(nn.Module):
    def __init__(self, in_channels, out_channels, hidden_channels, s_channels, dilation, dropout=0.0):
        super(NICEBlock, self).__init__()
        self.conv1 = Conv2dWeightNorm(in_channels + s_channels, hidden_channels, kernel_size=3, dilation=dilation, padding=dilation, bias=True)
        self.conv2 = Conv2dWeightNorm(hidden_channels, hidden_channels, kernel_size=1, bias=True)
        self.conv3 = Conv2dWeightNorm(hidden_channels, out_channels, kernel_size=3, dilation=dilation, padding=dilation, bias=True)
        self.activation = nn.ELU(inplace=True)
        self.dropout = nn.Dropout(dropout)

    def init(self, x, s=None, init_scale=1.0):
        if s is not None:
            x = torch.cat([x, s], dim=1)

        out = self.activation(self.conv1.init(x, init_scale=init_scale))

        out = self.activation(self.conv2.init(out, init_scale=init_scale))

        out = self.conv3.init(self.dropout(out), init_scale=0.0)

        return out

    def forward(self, x, s=None):
        if s is not None:
            x = torch.cat([x, s], dim=1)

        out = self.activation(self.conv1(x))

        out = self.activation(self.conv2(out))

        out = self.conv3(self.dropout(out))
        return out


class NICE(Flow):
    def __init__(self, in_channels, hidden_channels=None, s_channels=None, scale=True, inverse=False, dilation=1, dropout=0.0, factor=2):
        super(NICE, self).__init__(inverse)
        self.in_channels = in_channels
        self.scale = scale
        if hidden_channels is None:
            hidden_channels = min(8 * in_channels, 512)
        out_channels = in_channels // factor
        in_channels = in_channels - out_channels

        # self.residual = Conv2dWeightNorm(in_channels, out_channels, kernel_size=1, bias=True)
        self.z1_channels = in_channels
        if scale:
            out_channels = out_channels * 2
        if s_channels is None:
            s_channels = 0
        self.net = NICEBlock(in_channels, out_channels, hidden_channels=hidden_channels, s_channels=s_channels, dilation=dilation, dropout=dropout)

    def calc_mu_and_scale(self, z1: torch.Tensor, s=None):
        mu = self.net(z1, s=s)
        # res = self.residual(z1)
        scale = None
        if self.scale:
            mu, log_scale = mu.chunk(2, dim=1)
            log_scale = log_scale
            scale = log_scale.add_(2.).sigmoid_()
        mu = mu
        return mu, scale

    def init_net(self, z1: torch.Tensor, s=None, init_scale=1.0):
        mu = self.net.init(z1, s=s, init_scale=init_scale)
        # res = self.residual.init(z1, init_scale=0.0)
        scale = None
        if self.scale:
            mu, log_scale = mu.chunk(2, dim=1)
            log_scale = log_scale
            scale = log_scale.add_(2.).sigmoid_()
        mu = mu
        return mu, scale

    @overrides
    def forward(self, input: torch.Tensor, s=None) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            input: Tensor
                input tensor [batch, in_channels, H, W]
            s: Tensor
                conditional input (default: None)

        Returns: out: Tensor , logdet: Tensor
            out: [batch, in_channels, H, W], the output of the flow
            logdet: [batch], the log determinant of :math:`\partial output / \partial input`

        """
        # [batch, in_channels, H, W]
        z1 = input[:, :self.z1_channels]
        z2 = input[:, self.z1_channels:]
        mu, scale = self.calc_mu_and_scale(z1, s)
        if self.scale:
            z2 = z2.mul(scale)
            logdet = scale.log().view(z1.size(0), -1).sum(dim=1)
        else:
            logdet = z1.new_zeros(z1.size(0))
        z2 = z2 + mu
        return torch.cat([z1, z2], dim=1), logdet

    @overrides
    def backward(self, input: torch.Tensor, s=None) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            input: Tensor
                input tensor [batch, in_channels, H, W]
            s: Tensor
                conditional input (default: None)

        Returns: out: Tensor , logdet: Tensor
            out: [batch, in_channels, H, W], the output of the flow
            logdet: [batch], the log determinant of :math:`\partial output / \partial input`

        """
        z1 = input[:, :self.z1_channels]
        z2 = input[:, self.z1_channels:]
        mu, scale = self.calc_mu_and_scale(z1, s)
        z2 = z2 - mu
        if self.scale:
            z2 = z2.div(scale + 1e-12)
            logdet = scale.log().view(z1.size(0), -1).sum(dim=1) * -1.0
        else:
            logdet = z1.new_zeros(z1.size(0))

        return torch.cat([z1, z2], dim=1), logdet

    @overrides
    def init(self, data: torch.Tensor, s=None, init_scale=1.0) -> Tuple[torch.Tensor, torch.Tensor]:
        # [batch, in_channels, H, W]
        z1 = data[:, :self.z1_channels]
        z2 = data[:, self.z1_channels:]
        mu, scale = self.init_net(z1, s=s, init_scale=init_scale)
        if self.scale:
            z2 = z2.mul(scale)
            logdet = scale.log().view(z1.size(0), -1).sum(dim=1)
        else:
            logdet = z1.new_zeros(z1.size(0))
        z2 = z2 + mu

        return torch.cat([z1, z2], dim=1), logdet

    @overrides
    def extra_repr(self):
        return 'inverse={}, in_channels={}, scale={}'.format(self.inverse, self.in_channels, self.scale)

    @classmethod
    def from_params(cls, params: Dict) -> "NICE":
        return NICE(**params)


NICE.register('nice')
