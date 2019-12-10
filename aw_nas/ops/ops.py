"""
NN operations.
"""
#pylint: disable=arguments-differ,useless-super-delegation,invalid-name

import torch
from torch import nn
import torch.nn.functional as F

import numpy as np

def avg_pool_3x3(C, C_out, stride, affine):
    assert C == C_out
    return nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False)

def max_pool_3x3(C, C_out, stride, affine):
    assert C == C_out
    return nn.MaxPool2d(3, stride=stride, padding=1)

def conv_7x1_1x7(C, C_out, stride, affine):
    assert C == C_out
    return nn.Sequential(
        # C_out is ignored
        nn.ReLU(inplace=False),
        nn.Conv2d(C, C, (1, 7), stride=(1, stride), padding=(0, 3), bias=False),
        nn.Conv2d(C, C, (7, 1), stride=(stride, 1), padding=(3, 0), bias=False),
        nn.BatchNorm2d(C, affine=affine)
    )

PRIMITVE_FACTORY = {
    "none" : lambda C, C_out, stride, affine: Zero(stride),
    "avg_pool_3x3" : avg_pool_3x3,
    "max_pool_3x3" : max_pool_3x3,
    "skip_connect" : lambda C, C_out, stride, affine: Identity() if stride == 1 \
      else FactorizedReduce(C, C_out, stride=stride, affine=affine),
    "res_reduce_block": lambda C, C_out, stride, affine: ResFactorizedReduceBlock(
        C, C_out, stride=stride, affine=affine),

    "sep_conv_3x3" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 3, stride, 1, affine=affine),
    "sep_conv_3x3_exp3" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 3, stride, 1, affine=affine, expansion=3),
    "sep_conv_3x3_exp6" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 3, stride, 1, affine=affine, expansion=6),
    "sep_conv_5x5" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 5, stride, 2, affine=affine),
    "sep_conv_5x5_exp3" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 5, stride, 2, affine=affine, expansion=6),
    "sep_conv_5x5_exp6" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 5, stride, 2, affine=affine, expansion=6),
    "sep_conv_7x7" : lambda C, C_out, stride, affine: SepConv(
        C, C_out, 7, stride, 3, affine=affine),
    "dil_conv_3x3" : lambda C, C_out, stride, affine: DilConv(
        C, C_out, 3, stride, 2, 2, affine=affine),
    "dil_conv_5x5" : lambda C, C_out, stride, affine: DilConv(
        C, C_out, 5, stride, 4, 2, affine=affine),
    "conv_7x1_1x7" : conv_7x1_1x7,

    "relu_conv_bn_1x1" : lambda C, C_out, stride, affine: ReLUConvBN(C, C_out,
                                                                     1, stride, 0, affine=affine),
    "relu_conv_bn_3x3" : lambda C, C_out, stride, affine: ReLUConvBN(C, C_out,
                                                                     3, stride, 1, affine=affine),
    "relu_conv_bn_5x5" : lambda C, C_out, stride, affine: ReLUConvBN(C, C_out,
                                                                     5, stride, 2, affine=affine),
    "conv_bn_relu_1x1" : lambda C, C_out, stride, affine: ConvBNReLU(C, C_out,
                                                                     1, stride, 0, affine=affine),
    "conv_bn_relu_3x3" : lambda C, C_out, stride, affine: ConvBNReLU(C, C_out,
                                                                     3, stride, 1, affine=affine),
    "conv_bn_3x3" : lambda C, C_out, stride, affine: ConvBNReLU(
        C, C_out, 3, stride, 1, affine=affine, relu=False),
    "conv_bn_relu_5x5" : lambda C, C_out, stride, affine: ConvBNReLU(C, C_out,
                                                                     5, stride, 2, affine=affine),
    "conv_1x1" : lambda C, C_out, stride, affine: nn.Conv2d(C, C_out, 1, stride, 0),
    "inspect_block" : lambda C, C_out, stride, affine: inspectBlock(C, C_out, stride,
                                                                    affine=affine),
    "conv_3x3" : lambda C, C_out, stride, affine: nn.Conv2d(C, C_out, 3, stride, 1),
    "bn_relu" : lambda C, C_out, stride, affine: BNReLU(C, C_out, affine),

    # imagenet stem
    "imagenet_stem0": lambda C, C_out, stride, affine: nn.Sequential(
        nn.Conv2d(3, C_out // 2, kernel_size=3, stride=2, padding=1, bias=False),
        nn.BatchNorm2d(C_out // 2),
        nn.ReLU(inplace=True),
        nn.Conv2d(C_out // 2, C_out, 3, stride=2, padding=1, bias=False),
        nn.BatchNorm2d(C_out)
    ),

    # activations
    "tanh": lambda **kwargs: nn.Tanh(),
    "relu": lambda **kwargs: nn.ReLU(),
    "sigmoid": lambda **kwargs: nn.Sigmoid(),
    "identity": lambda **kwargs: Identity()
}

def register_primitive(name, func, override=False):
    assert callable(func), "A primtive must be callable"
    assert not (name in PRIMITVE_FACTORY and not override),\
        "some func already registered as {};"\
        " to override, use `override=True` keyword arguments.".format(name)
    PRIMITVE_FACTORY[name] = func

def get_op(name):
    assert name in PRIMITVE_FACTORY, \
        "{} not registered, use `register_primitive` to register primitive op".format(name)
    return PRIMITVE_FACTORY[name]

class BNReLU(nn.Module):
    def __init__(self, C_in, C_out, affine=True):
        super(BNReLU, self).__init__()
        assert C_in == C_out
        self.bn = nn.BatchNorm2d(C_out, affine=affine)

    def forward(self, x):
        return F.relu(self.bn(x))

class FactorizedReduce(nn.Module):
    def __init__(self, C_in, C_out, stride, affine=True, kernel_size=1):
        super(FactorizedReduce, self).__init__()
        self.stride = stride
        group_dim = C_out // stride

        padding = int((kernel_size - 1) / 2)
        self.convs = [nn.Conv2d(C_in, group_dim, kernel_size=kernel_size,
                                stride=stride, padding=padding, bias=False)\
                      for _ in range(stride)]
        self.convs = nn.ModuleList(self.convs)

        self.relu = nn.ReLU(inplace=False)
        self.bn = nn.BatchNorm2d(C_out, affine=affine)

        # just specificy one conv module here, as only C_in, kernel_size, group is used
        # for inject prob calculation every output position, this will work even though
        # not so meaningful conceptually
        object.__setattr__(self, "last_conv_module", self.convs[-1])

    def forward(self, x):
        x = self.relu(x)
        mod = x.size(2) % self.stride
        if mod != 0:
            pad = self.stride - mod
            x = F.pad(x, (pad, 0, pad, 0), "constant", 0)
        out = torch.cat([conv(x[:, :, i:, i:]) for i, conv in enumerate(self.convs)], dim=1)
        out = self.bn(out)
        return out

class DoubleConnect(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True, relu=True):
        super(DoubleConnect, self).__init__()
        self.path1 = ReLUConvBN(C_in, C_out, kernel_size, stride=stride, padding=padding, affine=affine)
        self.path2 = ReLUConvBN(C_in, C_out, kernel_size, stride=stride, padding=padding, affine=affine)

    def forward(self, x):
        return self.path1(x) + self.path2(x)


class ConvBNReLU(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True, relu=True):
        super(ConvBNReLU, self).__init__()
        if relu:
            self.op = nn.Sequential(
                nn.Conv2d(C_in, C_out, kernel_size, stride=stride, padding=padding, bias=False),
                nn.BatchNorm2d(C_out, affine=affine),
                nn.ReLU(inplace=False)
            )
        else:
            self.op = nn.Sequential(
                nn.Conv2d(C_in, C_out, kernel_size, stride=stride, padding=padding, bias=False),
                nn.BatchNorm2d(C_out, affine=affine)
            )

    def forward(self, x):
        return self.op(x)

    def forward_one_step(self, context=None, inputs=None):
        return self.op.forward_one_step(context, inputs)


class ReLUConvBN(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True):
        super(ReLUConvBN, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_out, kernel_size, stride=stride, padding=padding, bias=False),
            nn.BatchNorm2d(C_out, affine=affine)
        )

    def forward(self, x):
        return self.op(x)

    def forward_one_step(self, context=None, inputs=None):
        return self.op.forward_one_step(context, inputs)


class DilConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, dilation, affine=True):
        super(DilConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=stride,
                      padding=padding, dilation=dilation, groups=C_in, bias=False),
            nn.Conv2d(C_in, C_out, kernel_size=1, padding=0, bias=False),
            nn.BatchNorm2d(C_out, affine=affine),
        )

    def forward(self, x):
        return self.op(x)

    def forward_one_step(self, context=None, inputs=None):
        return self.op.forward_one_step(context, inputs)

class SepConv(nn.Module):

    def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True, expansion=1):
        super(SepConv, self).__init__()
        self.op = nn.Sequential(
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=stride,
                      padding=padding, groups=C_in, bias=False),
            nn.Conv2d(C_in, C_in*expansion, kernel_size=1, padding=0, bias=False),
            nn.BatchNorm2d(C_in*expansion, affine=affine),
            nn.ReLU(inplace=False),
            nn.Conv2d(C_in*expansion, C_in*expansion, kernel_size=kernel_size, stride=1,
                      padding=padding, groups=C_in, bias=False),
            nn.Conv2d(C_in*expansion, C_out, kernel_size=1, padding=0, bias=False),
            nn.BatchNorm2d(C_out, affine=affine),
        )

    def forward(self, x):
        return self.op(x)

    def forward_one_step(self, context=None, inputs=None):
        return self.op.forward_one_step(context, inputs)

def forward_one_step(self, context=None, inputs=None):
    #pylint: disable=protected-access,too-many-branches
    assert not context is None

    if not hasattr(self, "_conv_mod_inds"):
        self._conv_mod_inds = []
        mods = list(self._modules.values())
        mod_num = len(mods)
        for i, mod in enumerate(mods):
            if isinstance(mod, nn.Conv2d):
                if i < mod_num - 1 and isinstance(mods[i+1], nn.BatchNorm2d):
                    self._conv_mod_inds.append(i+1)
                else:
                    self._conv_mod_inds.append(i)
        self._num_convs = len(self._conv_mod_inds)

    if not self._num_convs:
        return stub_forward_one_step(self, context, inputs)

    _, op_ind = context.next_op_index
    if inputs is None:
        inputs = context.current_op[-1]
    modules_num = len(list(self._modules.values()))
    if op_ind < self._num_convs:
        for mod_ind in range(self._conv_mod_inds[op_ind-1]+1 if op_ind > 0 else 0,
                             self._conv_mod_inds[op_ind]+1):
            # running from the last point(exclusive) to the #op_ind's point (inclusive)
            inputs = self._modules[str(mod_ind)](inputs)
        if op_ind == self._num_convs - 1 and self._conv_mod_inds[-1] + 1 == modules_num:
            # if the last calculated module is already the last module in the Sequence container
            context.previous_op.append(inputs)
            context.current_op = []
        else:
            context.current_op.append(inputs)
        last_mod = self._modules[str(self._conv_mod_inds[op_ind])]
        context.last_conv_module = last_mod if isinstance(last_mod, nn.Conv2d) \
            else self._modules[str(self._conv_mod_inds[op_ind]-1)]
    elif op_ind == self._num_convs:
        for mod_ind in range(self._conv_mod_inds[-1]+1, modules_num):
            inputs = self._modules[str(mod_ind)](inputs)
        context.previous_op.append(inputs)
        context.current_op = []
        context.flag_inject(False)
    else:
        assert "ERROR: wrong op index! should not reach here!"
    return inputs, context

def stub_forward_one_step(self, context=None, inputs=None):
    assert not inputs is None and not context is None
    state = self.forward(inputs)
    context.previous_op.append(state)
    if isinstance(self, nn.Conv2d):
        context.last_conv_module = self
    return state, context

nn.Sequential.forward_one_step = forward_one_step
nn.Module.forward_one_step = stub_forward_one_step

def get_last_conv_module(self):
    if hasattr(self, "last_conv_module"):
        return self.last_conv_module

    # in some cases, can auto induce the last conv module
    if isinstance(self, nn.Conv2d):
        return self
    if isinstance(self, nn.Sequential):
        for mod in reversed(self._modules.values()):
            if isinstance(mod, nn.Conv2d):
                return mod
        return None
    if not self._modules:
        return None
    if len(self._modules) == 1:
        only_sub_mod = list(self._modules.values())[0]
        return get_last_conv_module(only_sub_mod)
    raise Exception("Cannot auto induce the last conv module of mod {}, "
                    "Specificy `last_conv_module` attribute!`".format(self))

nn.Module.get_last_conv_module = get_last_conv_module

class Identity(nn.Module):

    def __init__(self):
        super(Identity, self).__init__()

    def forward(self, x):
        return x


class Zero(nn.Module):

    def __init__(self, stride):
        super(Zero, self).__init__()
        self.stride = stride

    def forward(self, x):
        if self.stride == 1:
            return x.mul(0.)
        return x[:, :, ::self.stride, ::self.stride].mul(0.)


class inspectBlock(torch.nn.Module):
    def __init__(self, C_in, C_out, stride, affine=True):
        super(inspectBlock, self).__init__()
        self.op1 = ReLUConvBN(C_in, C_out, kernel_size=3, stride=stride,
                              padding=1, affine=affine)
        self.op2 = ReLUConvBN(C_in, C_out, kernel_size=1, stride=stride,
                              padding=0, affine=affine)
        self.op3 = SepConv(C_in, C_out, kernel_size=3, stride=stride,
                           padding=1, affine=affine)

    def forward(self, x):
        rand_ = np.random.random()
        if rand_ < 1./3.:
            return self.op1(x)
        if rand_ < 2./3.:
            return self.op2(x)
        return self.op3(x)


# ---- added for rnn ----
# from https://github.com/carpedm20/ENAS-pytorch
class EmbeddingDropout(torch.nn.Embedding):
    """Class for dropping out embeddings by zero'ing out parameters in the
    embedding matrix.
    This is equivalent to dropping out particular words, e.g., in the sentence
    'the quick brown fox jumps over the lazy dog', dropping out 'the' would
    lead to the sentence '### quick brown fox jumps over ### lazy dog' (in the
    embedding vector space).
    See 'A Theoretically Grounded Application of Dropout in Recurrent Neural
    Networks', (Gal and Ghahramani, 2016).
    """
    def __init__(self,
                 num_embeddings,
                 embedding_dim,
                 max_norm=None,
                 norm_type=2,
                 scale_grad_by_freq=False,
                 sparse=False,
                 dropout=0.1,
                 scale=None):
        """Embedding constructor.
        Args:
            dropout: Dropout probability.
            scale: Used to scale parameters of embedding weight matrix that are
                not dropped out. Note that this is _in addition_ to the
                `1/(1 - dropout)` scaling.
        See `torch.nn.Embedding` for remaining arguments.
        """
        torch.nn.Embedding.__init__(self,
                                    num_embeddings=num_embeddings,
                                    embedding_dim=embedding_dim,
                                    max_norm=max_norm,
                                    norm_type=norm_type,
                                    scale_grad_by_freq=scale_grad_by_freq,
                                    sparse=sparse)
        self.dropout = dropout
        assert 1.0 > dropout >= 0.0, "Dropout must be >= 0.0 and < 1.0"
        self.scale = scale

    def forward(self, inputs):
        """Embeds `inputs` with the dropped out embedding weight matrix."""
        if self.training:
            dropout = self.dropout
        else:
            dropout = 0

        if dropout:
            mask = self.weight.data.new(self.weight.size(0), 1)
            mask.bernoulli_(1 - dropout)
            mask = mask.expand_as(self.weight)
            mask = mask / (1 - dropout)
            masked_weight = self.weight * mask
        else:
            masked_weight = self.weight
        if self.scale and self.scale != 1:
            masked_weight = masked_weight * self.scale

        return F.embedding(inputs,
                           masked_weight,
                           max_norm=self.max_norm,
                           norm_type=self.norm_type,
                           scale_grad_by_freq=self.scale_grad_by_freq,
                           sparse=self.sparse)

class LockedDropout(nn.Module):
    """
    Variational dropout: same dropout mask at each time step. Gal and Ghahramani (2015).

    Ref: https://github.com/salesforce/awd-lstm-lm/
    """

    def __init__(self):
        super(LockedDropout, self).__init__()

    def forward(self, x, dropout=0.5):
        if not self.training or not dropout:
            return x
        # batch_size, num_hidden
        m = x.data.new(1, x.size(1), x.size(2)).bernoulli_(1 - dropout)
        mask = m.div_(1 - dropout)
        mask = mask.expand_as(x)
        return mask * x

class ResFactorizedReduceBlock(nn.Module):
    def __init__(self, C, C_out, stride, affine):
        super(ResFactorizedReduceBlock, self).__init__()
        kernel_size = 1
        padding = int((kernel_size - 1) / 2)
        self.op_1 = ReLUConvBN(
            C, C_out, kernel_size, stride,
            padding, affine=affine) if stride == 1 \
            else FactorizedReduce(C, C_out, stride=stride, affine=affine)
        self.op_2 = ReLUConvBN(C_out, C_out,
                               kernel_size, 1, padding, affine=affine)
        self.skip_op = Identity() if stride == 1 and C == C_out else \
                       ConvBNReLU(C, C_out, 1, stride, 0, affine=affine)

    def forward(self, inputs):
        inner = self.op_1(inputs)
        out = self.op_2(inner)
        out_skip = self.skip_op(inputs)
        return out + out_skip

    def forward_one_step(self, context=None, inputs=None):
        raise NotImplementedError()

class ChannelConcat(nn.Module):
    @property
    def is_elementwise(self):
        return False

    def forward(self, states):
        return torch.cat(states, dim=1)

class ElementwiseAdd(nn.Module):
    @property
    def is_elementwise(self):
        return True

    def forward(self, states):
        return sum(states)

class ElementwiseMean(nn.Module):
    @property
    def is_elementwise(self):
        return True

    def forward(self, states):
        return sum(states) / len(states)

CONCAT_OPS = {
    "concat": ChannelConcat,
    "sum": ElementwiseAdd,
    "mean": ElementwiseMean
}

def get_concat_op(type_):
    return CONCAT_OPS[type_]()
