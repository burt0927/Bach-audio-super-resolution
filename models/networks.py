import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.nn.modules import Module
import numpy as np
from collections import OrderedDict
from . import densenet_efficient as dens
from . import time_frequence as tf
from . import bwe 
import torch.nn.init as init
###############################################################################
# Functions
###############################################################################


def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm2d') != -1 or classname.find(
            'InstanceNorm2d') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


def weights_init_v2(m):
    classname = m.__class__.__name__


def get_norm_layer(norm_type):
    if norm_type == 'batch':
        norm_layer = nn.BatchNorm2d
    elif norm_type == 'instance':
        norm_layer = nn.InstanceNorm2d
    else:
        print('normalization layer [%s] is not found' % norm_type)
    return norm_layer
    # return None


def define_G(opt):
    netG = None
    use_gpu = len(opt.gpu_ids) > 0

    if use_gpu:
        assert (torch.cuda.is_available())

    netG = AuFCNWrapper(opt)

    if len(opt.gpu_ids) > 0:
        netG.cuda(device_id=opt.gpu_ids[0])
#    netG.weight_init()
    return netG


def define_D(input_nc,
             ndf,
             which_model_netD,
             n_layers_D=3,
             norm='batch',
             use_sigmoid=False,
             gpu_ids=[]):
    netD = None
    use_gpu = len(gpu_ids) > 0
    norm_layer = get_norm_layer(norm_type=norm)

    if use_gpu:
        assert (torch.cuda.is_available())
    if which_model_netD == 'basic':
        netD = NLayerDiscriminator(
            input_nc,
            ndf,
            n_layers=3,
            norm_layer=norm_layer,
            use_sigmoid=use_sigmoid,
            gpu_ids=gpu_ids)
    elif which_model_netD == 'n_layers':
        netD = NLayerDiscriminator(
            input_nc,
            ndf,
            n_layers_D,
            norm_layer=norm_layer,
            use_sigmoid=use_sigmoid,
            gpu_ids=gpu_ids)
    else:
        print('Discriminator model name [%s] is not recognized' %
              which_model_netD)
    if use_gpu:
        netD.cuda(device_id=gpu_ids[0])
    netD.apply(weights_init)
    return netD


def print_network(net):
    num_params = 0
    for param in net.parameters():
        num_params += param.numel()
    print(net)
    print('Total number of parameters: %d' % num_params)


##############################################################################
# Classes
##############################################################################


# Defines the GAN loss which uses either LSGAN or the regular GAN.
# When LSGAN is used, it is basically same as MSELoss,
# but it abstracts away the need to create the target label tensor
# that has the same size as the input
class GANLoss(nn.Module):
    def __init__(self,
                 use_lsgan=True,
                 target_real_label=1.0,
                 target_fake_label=0.0,
                 tensor=torch.FloatTensor):
        super(GANLoss, self).__init__()
        self.real_label = target_real_label
        self.fake_label = target_fake_label
        self.real_label_var = None
        self.fake_label_var = None
        self.Tensor = tensor
        if use_lsgan:
            self.loss = nn.MSELoss()
        else:
            self.loss = nn.BCELoss()

    def get_target_tensor(self, input, target_is_real):
        target_tensor = None
        if target_is_real:
            create_label = ((self.real_label_var is None)
                            or (self.real_label_var.numel() != input.numel()))
            if create_label:
                real_tensor = self.Tensor(input.size()).fill_(self.real_label)
                self.real_label_var = Variable(
                    real_tensor, requires_grad=False)
            target_tensor = self.real_label_var
        else:
            create_label = ((self.fake_label_var is None)
                            or (self.fake_label_var.numel() != input.numel()))
            if create_label:
                fake_tensor = self.Tensor(input.size()).fill_(self.fake_label)
                self.fake_label_var = Variable(
                    fake_tensor, requires_grad=False)
            target_tensor = self.fake_label_var
        return target_tensor

    def __call__(self, input, target_is_real):
        target_tensor = self.get_target_tensor(input, target_is_real)
        return self.loss(input, target_tensor)


class AuFCNWrapper(nn.Module):
    def __init__(self, opt):
        super(AuFCNWrapper, self).__init__()
        self.gpu_ids = opt.gpu_ids
        self.model = AuFCN(opt)
        try:
            assert opt.scale == 2
        except Exception:
            print('scale!=2 not supported')

    def forward(self, input):
        if self.gpu_ids and isinstance(input.data,
                                       torch.cuda.FloatTensor) and False:
            output = nn.parallel.data_parallel(self.model, input, self.gpu_ids)
            print("network G output", output.size())
            return output
        else :
            return self.model(input)

    def test(self, sample):
        return self.model.test(sample)

    def weight_init(self):
        for name, para in self.named_parameters():
            if 'weight' in name:
                if 'orth' in name:
                    init.orthogonal(para.data)
                else:
                    init.normal(para.data, mean=0, std=1e-3)
            else:
                init.constant(para, 0)
    



# TODO robust
# TODO requires gradient
# TODO assert AC == 0
class AuFCN(nn.Module):
    def __init__(self, opt):
        super(AuFCN, self).__init__()
        self.hDict = torch.Tensor(opt.maxhSize, opt.nmfcc * 3)
        self.hdictIndex = 0
        self.lDict = torch.Tensor(opt.maxlSize, opt.nmfcc * 3)
        self.lDictIndex = 0

    def update(self, my_dict, sample, dict_index):
        new = permuted.view(-1, sample.shape[2])
        my_dict[dict_index:dict_index+new.shape[0], :] = new
        return dict_index + new.shape[0]

    def forward(self, sample):
        degraded = sample[0]
        clean = sample[1]
        self.hDict_index = self.update_h(self.hDict, clean, self.hDict_index)
        self.lDict_index = self.update_l(self.lDict, degraded, self.lDict_index)
    
    def test(self, sample):
        temp = torch.matmul(self.lDict, sample)
        maxindex = torch.argmax(temp, 0)
        return self.hDict[*maxindex]


class Tanh_rescale(Module):
    def forward(self, input):
        return torch.div(
            torch.add(torch.tanh(torch.mul(input, 2.0)), 1.0), 2.0)

    def __repr__(self):
        return self.__class__.__name__ + ' ()'
