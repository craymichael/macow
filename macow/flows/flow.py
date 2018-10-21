__author__ = 'max'

from typing import Dict, Tuple
import torch
import torch.nn as nn


class Flow(nn.Module):
    """
    Normalizing Flow base class
    """
    _registry = dict()

    def __init__(self, inverse):
        super(Flow, self).__init__()
        self.inverse = inverse

    def forward(self, *input) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            *input: input [batch, *input_size]

        Returns: out: Tensor [batch, *input_size], logdet: Tensor [batch]
            out, the output of the flow
            logdet, the log determinant of :math:`\partial output / \partial input`
        """
        raise NotImplementedError

    def backward(self, *input) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            *input: input [batch, *input_size]

        Returns: out: Tensor [batch, *input_size], logdet: Tensor [batch]
            out, the output of the flow
            logdet, the log determinant of :math:`\partial output / \partial input`
        """
        raise NotImplementedError

    def init(self, *input, **kwargs) -> Tuple[torch.Tensor, torch.Tensor]:
        raise NotImplementedError

    def fwdpass(self, x: torch.Tensor, *h, init=False, init_scale=1.0) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            x: Tensor
                The random variable before flow
            h: list of object
                other conditional inputs
            init: bool
                perform initialization or not (default: False)
            init_scale: float
                initial scale (default: 1.0)

        Returns: y: Tensor, logdet: Tensor
            y, the random variable after flow
            logdet, the log determinant of :math:`\partial x / \partial y`
            Then the density :math:`\log(p(y)) = \log(p(x)) + logdet`

        """
        if self.inverse:
            if init:
                raise RuntimeError('inverse flow shold be initialized with backward pass')
            else:
                y, logdet = self.backward(x, h)
        else:
            if init:
                y, logdet = self.init(x, h, init_scale=init_scale)
            else:
                y, logdet = self.forward(x, h)
        return y, logdet.mul(-1.0)

    def bwdpass(self, y: torch.Tensor, *h, init=False, init_scale=1.0) -> Tuple[torch.Tensor, torch.Tensor]:
        """

        Args:
            y: Tensor
                The random variable after flow
            h: list of object
                other conditional inputs
            init: bool
                perform initialization or not (default: False)
            init_scale: float
                initial scale (default: 1.0)

        Returns: x: Tensor, logdet: Tensor
            x, the random variable before flow
            logdet, the log determinant of :math:`\partial x / \partial y`
            Then the density :math:`\log(p(y)) = \log(p(x)) + logdet`

        """
        if self.inverse:
            if init:
                x, logdet = self.init(y, h, init_scale=init_scale)
            else:
                x, logdet = self.forward(y, h)
        else:
            if init:
                raise RuntimeError('forward flow should be initialzed with forward pass')
            else:
                x, logdet = self.backward(y, h)
        return x, logdet

    @classmethod
    def register(cls, name: str):
        Flow._registry[name] = cls

    @classmethod
    def by_name(cls, name: str):
        return Flow._registry[name]

    @classmethod
    def from_params(cls, params: Dict):
        raise NotImplementedError
