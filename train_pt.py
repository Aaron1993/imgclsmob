import argparse
import time
import logging
import os
import sys
import warnings
import random
import numpy as np

import torch.backends.cudnn as cudnn
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torchvision.models as models

from common.env_stats import get_env_stats
from common.train_log_param_saver import TrainLogParamSaver

from pytorch.models.mobilenet import *
from pytorch.models.shufflenet import *
from pytorch.models.menet import *
from pytorch.models.squeezenet import *


def parse_args():
    parser = argparse.ArgumentParser(description='Train a model for image classification (PyTorch)',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--data-dir',
        type=str,
        default='../imgclsmob_data/imagenet',
        help='training and validation pictures to use.')

    parser.add_argument(
        '--model',
        type=str,
        required=True,
        help='type of model to use. see vision_model for options.')
    parser.add_argument(
        '--use-pretrained',
        action='store_true',
        help='enable using pretrained model from gluon.')
    parser.add_argument(
        '--resume',
        type=str,
        default='',
        help='resume from previously saved parameters if not None')
    parser.add_argument(
        '-e',
        '--evaluate',
        dest='evaluate',
        action='store_true',
        help='only evaluate model on validation set')

    parser.add_argument(
        '--num-gpus',
        type=int,
        default=0,
        help='number of gpus to use.')
    parser.add_argument(
        '-j',
        '--num-data-workers',
        dest='num_workers',
        default=4,
        type=int,
        help='number of preprocessing workers')

    parser.add_argument(
        '--batch-size',
        type=int,
        default=32,
        help='training batch size per device (CPU/GPU).')
    parser.add_argument(
        '--num-epochs',
        type=int,
        default=3,
        help='number of training epochs.')
    parser.add_argument(
        '--start-epoch',
        type=int,
        default=1,
        help='starting epoch for resuming, default is 1 for new training')

    parser.add_argument(
        '--optimizer-name',
        type=str,
        default='nag',
        help='optimizer name')
    parser.add_argument(
        '--lr',
        type=float,
        default=0.1,
        help='learning rate. default is 0.1.')
    parser.add_argument(
        '--lr-mode',
        type=str,
        default='step',
        help='learning rate scheduler mode. options are step, poly and cosine.')
    parser.add_argument(
        '--lr-decay',
        type=float,
        default=0.1,
        help='decay rate of learning rate. default is 0.1.')
    parser.add_argument(
        '--lr-decay-period',
        type=int,
        default=0,
        help='interval for periodic learning rate decays. default is 0 to disable.')
    parser.add_argument(
        '--lr-decay-epoch',
        type=str,
        default='40,60',
        help='epoches at which learning rate decays. default is 40,60.')
    parser.add_argument(
        '--warmup-lr',
        type=float,
        default=0.0,
        help='starting warmup learning rate. default is 0.0.')
    parser.add_argument(
        '--warmup-epochs',
        type=int,
        default=0,
        help='number of warmup epochs.')
    parser.add_argument(
        '--momentum',
        type=float,
        default=0.9,
        help='momentum value for optimizer, default is 0.9.')
    parser.add_argument(
        '--wd',
        type=float,
        default=0.0001,
        help='weight decay rate. default is 0.0001.')

    parser.add_argument(
        '--log-interval',
        type=int,
        default=50,
        help='number of batches to wait before logging.')
    parser.add_argument(
        '--save-interval',
        type=int,
        default=4,
        help='saving parameters epoch interval, best model will always be saved')
    parser.add_argument(
        '--save-dir',
        type=str,
        default='',
        help='directory of saved models and log-files')
    parser.add_argument(
        '--logging-file-name',
        type=str,
        default='train.log',
        help='filename of training log')

    parser.add_argument(
        '--seed',
        type=int,
        default=-1,
        help='Random seed to be fixed')
    parser.add_argument(
        '--log-packages',
        type=str,
        default='torch, torchvision',
        help='list of python packages for logging')
    parser.add_argument(
        '--log-pip-packages',
        type=str,
        default='',
        help='list of pip packages for logging')
    args = parser.parse_args()
    return args


def prepare_logger(log_dir_path,
                   logging_file_name):
    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if log_dir_path is not None and log_dir_path:
        log_file_path = os.path.join(log_dir_path, logging_file_name)
        if not os.path.exists(log_dir_path):
            os.makedirs(log_dir_path)
            log_file_exist = False
        else:
            log_file_exist = (os.path.exists(log_file_path) and os.path.getsize(log_file_path) > 0)
        fh = logging.FileHandler(log_file_path)
        logger.addHandler(fh)
        if log_file_exist:
            logging.info('--------------------------------')
    return logger


def init_rand(seed):
    if seed <= 0:
        seed = np.random.randint(10000)
    else:
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    return seed


def prepare_pt_context(num_gpus,
                       batch_size):
    use_cuda = (num_gpus > 0)
    batch_size *= max(1, num_gpus)
    return use_cuda, batch_size


def get_data_loader(data_dir,
                    batch_size,
                    num_workers):
    valdir = os.path.join(data_dir, 'val')
    normalize = transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225])
    jitter_param = 0.4

    train_loader = torch.utils.data.DataLoader(
        dataset=datasets.ImageFolder(
            root=os.path.join(data_dir, 'train'),
            transform=transforms.Compose([
                transforms.RandomResizedCrop(224),
                transforms.RandomHorizontalFlip(),
                transforms.ColorJitter(
                    brightness=jitter_param,
                    contrast=jitter_param,
                    saturation=jitter_param),
                transforms.ToTensor(),
                normalize,
            ])),
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=True)

    val_loader = torch.utils.data.DataLoader(
        dataset=datasets.ImageFolder(
            root=valdir,
            transform=transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                normalize,
            ])),
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True)

    return train_loader, val_loader


def _get_model(name, **kwargs):
    slk_models = {
        'squeezenet1_0': squeezenet1_0,
        'squeezenet1_1': squeezenet1_1,
        'mobilenet1_0': mobilenet1_0,
        'mobilenet0_75': mobilenet0_75,
        'mobilenet0_5': mobilenet0_5,
        'mobilenet0_25': mobilenet0_25,
        'fd_mobilenet1_0': fd_mobilenet1_0,
        'fd_mobilenet0_75': fd_mobilenet0_75,
        'fd_mobilenet0_5': fd_mobilenet0_5,
        'fd_mobilenet0_25': fd_mobilenet0_25,
        'shufflenet1_0_g1': shufflenet1_0_g1,
        'shufflenet1_0_g2': shufflenet1_0_g2,
        'shufflenet1_0_g3': shufflenet1_0_g3,
        'shufflenet1_0_g4': shufflenet1_0_g4,
        'shufflenet1_0_g8': shufflenet1_0_g8,
        'shufflenet0_5_g1': shufflenet0_5_g1,
        'shufflenet0_5_g2': shufflenet0_5_g2,
        'shufflenet0_5_g3': shufflenet0_5_g3,
        'shufflenet0_5_g4': shufflenet0_5_g4,
        'shufflenet0_5_g8': shufflenet0_5_g8,
        'shufflenet0_25_g1': shufflenet0_25_g1,
        'shufflenet0_25_g2': shufflenet0_25_g2,
        'shufflenet0_25_g3': shufflenet0_25_g3,
        'shufflenet0_25_g4': shufflenet0_25_g4,
        'shufflenet0_25_g8': shufflenet0_25_g8,
        'menet108_8x1_g3': menet108_8x1_g3,
        }
    try:
        net = models.__dict__[name](**kwargs)
        return net
    except ValueError as e:
        upstream_supported = str(e)
    name = name.lower()
    if name not in models:
        raise ValueError('%s\n\t%s' % (upstream_supported, '\n\t'.join(sorted(models.keys()))))
    net = slk_models[name](**kwargs)
    return net


def prepare_model(model_name,
                  classes,
                  use_pretrained,
                  pretrained_model_file_path,
                  use_cuda):
    kwargs = {'pretrained': use_pretrained,
              'classes': classes}

    net = _get_model(model_name, **kwargs)

    if model_name.startswith('alexnet') or model_name.startswith('vgg'):
        net.features = torch.nn.DataParallel(net.features)
    else:
        net = torch.nn.DataParallel(net)

    if use_cuda:
        net = net.cuda()

    if pretrained_model_file_path:
        assert (os.path.isfile(pretrained_model_file_path))
        logging.info('Loading model: {}'.format(pretrained_model_file_path))
        checkpoint = torch.load(pretrained_model_file_path)
        net.load_state_dict(checkpoint['state_dict'])

    return net


def prepare_trainer(net,
                    optimizer_name,
                    wd,
                    momentum,
                    lr_mode,
                    lr,
                    lr_decay_period,
                    lr_decay_epoch,
                    lr_decay,
                    warmup_epochs,
                    batch_size,
                    num_epochs,
                    num_training_samples,
                    pretrained_model_file_path):

    if optimizer_name == 'sgd':
        optimizer = torch.optim.SGD(
            params=net.parameters(),
            lr=lr,
            momentum=momentum,
            weight_decay=wd)
    elif optimizer_name == 'nag':
        optimizer = torch.optim.SGD(
            params=net.parameters(),
            lr=lr,
            momentum=momentum,
            weight_decay=wd,
            nesterov=True)
    else:
        raise Exception()

    if pretrained_model_file_path:
        checkpoint = torch.load(pretrained_model_file_path)
        optimizer.load_state_dict(checkpoint['optimizer'])
        start_epoch = checkpoint['epoch']
        best_prec1 = checkpoint['best_prec1']

    cudnn.benchmark = True

    lr_scheduler = None

    return optimizer, lr_scheduler, start_epoch, best_prec1


def calc_net_weight_count(net):
    net_params = net.collect_params()
    weight_count = 0
    for param in net_params.values():
        if param.shape is None:
            continue
        weight_count += np.prod(param.shape)
    return weight_count


def save_params(file_path,
                net):
    net.save_parameters(file_path)


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


def validate(acc_top1,
             acc_top5,
             net,
             val_data,
             use_cuda):
    net.eval()
    acc_top1.reset()
    acc_top5.reset()
    with torch.no_grad():
        for input, target in val_data:
            if use_cuda:
                target = target.cuda(non_blocking=True)
            output = net(input)
            prec1, prec5 = accuracy(output, target, topk=(1, 5))
            acc_top1.update(prec1[0], input.size(0))
            acc_top5.update(prec5[0], input.size(0))
    top1 = acc_top1.avg
    top5 = acc_top5.avg
    return 1-top1, 1-top5


def test(net,
         val_data,
         use_cuda):
    acc_top1 = AverageMeter()
    acc_top5 = AverageMeter()

    tic = time.time()
    err_top1_val, err_top5_val = validate(
        acc_top1=acc_top1,
        acc_top5=acc_top5,
        net=net,
        val_data=val_data,
        use_cuda=use_cuda)
    logging.info('Test: err-top1={:.4f}\terr-top5={:.4f}'.format(
        err_top1_val, err_top5_val))
    logging.info('Time cost: {:.4f} sec'.format(
        time.time() - tic))


def train_epoch(epoch,
                acc_top1,
                net,
                train_data,
                use_cuda,
                L,
                optimizer,
                lr_scheduler,
                batch_size,
                log_interval):

    tic = time.time()
    net.train()
    acc_top1.reset()
    train_loss = 0.0

    btic = time.time()
    for i, (input, target) in enumerate(train_data):
        if use_cuda:
            input = input.cuda(non_blocking=True)
            target = target.cuda(non_blocking=True)
        output = net(input)
        loss = L(output, target)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if epoch == 0 and i == 0:
            weight_count = calc_net_weight_count(net)
            logging.info('Model: {} trainable parameters'.format(weight_count))
        train_loss += sum([l.mean().asscalar() for l in loss]) / len(loss)
        prec1 = accuracy(output, target, topk=(1, ))
        acc_top1.update(prec1[0], input.size(0))

        if log_interval and not (i + 1) % log_interval:
            _, top1 = acc_top1.get()
            err_top1_train = 1.0 - top1
            speed = batch_size * log_interval / (time.time() - btic)
            logging.info('Epoch[{}] Batch [{}]\tSpeed: {:.2f} samples/sec\ttop1-err={:.4f}\tlr={:.4f}'.format(
                epoch + 1, i, speed, err_top1_train, optimizer.learning_rate))
            btic = time.time()

    top1 = acc_top1.avg
    err_top1_train = 1.0 - top1
    train_loss /= (i + 1)
    throughput = int(batch_size * (i + 1) / (time.time() - tic))

    logging.info('[Epoch {}] training: err-top1={:.4f}\tloss={:.4f}'.format(
        epoch + 1, err_top1_train, train_loss))
    logging.info('[Epoch {}] speed: {:.2f} samples/sec\ttime cost: {:.2f} sec'.format(
        epoch + 1, throughput, time.time() - tic))

    return err_top1_train, train_loss


def train_net(batch_size,
              num_epochs,
              start_epoch1,
              train_data,
              val_data,
              net,
              optimizer,
              lr_scheduler,
              lp_saver,
              log_interval,
              use_cuda):
    acc_top1 = AverageMeter()
    acc_top5 = AverageMeter()

    L = nn.CrossEntropyLoss().cuda()

    assert (type(start_epoch1) == int)
    assert (start_epoch1 >= 1)
    if start_epoch1 > 1:
        logging.info('Start training from [Epoch {}]'.format(start_epoch1))
        err_top1_val, err_top5_val = validate(
            acc_top1=acc_top1,
            acc_top5=acc_top5,
            net=net,
            val_data=val_data,
            use_cuda=use_cuda)
        logging.info('[Epoch {}] validation: err-top1={:.4f}\terr-top5={:.4f}'.format(
            start_epoch1 - 1, err_top1_val, err_top5_val))

    gtic = time.time()
    for epoch in range(start_epoch1 - 1, num_epochs):
        err_top1_train, train_loss = train_epoch(
            epoch,
            acc_top1,
            net,
            train_data,
            use_cuda,
            L,
            optimizer,
            lr_scheduler,
            batch_size,
            log_interval)

        err_top1_val, err_top5_val = validate(
            acc_top1=acc_top1,
            acc_top5=acc_top5,
            net=net,
            val_data=val_data,
            use_cuda=use_cuda)

        logging.info('[Epoch {}] validation: err-top1={:.4f}\terr-top5={:.4f}'.format(
            epoch + 1, err_top1_val, err_top5_val))

        if lp_saver is not None:
            lp_saver_kwargs = {'net': net}
            lp_saver.epoch_test_end_callback(
                epoch1=(epoch + 1),
                params=[err_top1_val, err_top1_train, err_top5_val, train_loss],
                **lp_saver_kwargs)

    logging.info('Total time cost: {:.2f} sec'.format(time.time() - gtic))
    if lp_saver is not None:
        logging.info('Best err-top5: {:.4f} at {} epoch'.format(
            lp_saver.best_eval_metric_value, lp_saver.best_eval_metric_epoch))


def main():
    args = parse_args()
    args.seed = init_rand(seed=args.seed)
    prepare_logger(
        log_dir_path=args.save_dir,
        logging_file_name=args.logging_file_name)
    logging.info("Script command line:\n{}".format(" ".join(sys.argv)))
    logging.info("Script arguments:\n{}".format(args))
    logging.info("Env_stats:\n{}".format(get_env_stats(
        packages=args.log_packages.replace(' ', '').split(','),
        pip_packages=args.log_pip_packages.replace(' ', '').split(','))))

    use_cuda, batch_size = prepare_pt_context(
        num_gpus=args.num_gpus,
        batch_size=args.batch_size)

    if args.convert_to_mxnet:
        batch_size = 1

    classes = 1000
    net = prepare_model(
        model_name=args.model,
        classes=classes,
        use_pretrained=args.use_pretrained,
        pretrained_model_file_path=args.resume.strip(),
        use_cuda=use_cuda)

    train_data, val_data = get_data_loader(
        data_dir=args.data_dir,
        batch_size=batch_size,
        num_workers=args.num_workers)

    if args.evaluate:
        assert (args.use_pretrained or args.resume.strip())
        test(
            net=net,
            val_data=val_data,
            use_cuda=use_cuda)
    else:
        num_training_samples = 1281167
        optimizer, lr_scheduler, start_epoch, best_prec1 = prepare_trainer(
            net=net,
            optimizer_name=args.optimizer_name,
            wd=args.wd,
            momentum=args.momentum,
            lr_mode=args.lr_mode,
            lr=args.lr,
            lr_decay_period=args.lr_decay_period,
            lr_decay_epoch=args.lr_decay_epoch,
            lr_decay=args.lr_decay,
            warmup_epochs=args.warmup_epochs,
            batch_size=batch_size,
            num_epochs=args.num_epochs,
            num_training_samples=num_training_samples,
            pretrained_model_file_path=args.resume.strip())
        args.start_epoch = start_epoch

        if args.save_dir and args.save_interval:
            lp_saver = TrainLogParamSaver(
                checkpoint_file_name_prefix='imagenet_{}'.format(args.model),
                last_checkpoint_file_name_suffix="last",
                best_checkpoint_file_name_suffix=None,
                last_checkpoint_dir_path=args.save_dir,
                best_checkpoint_dir_path=None,
                last_checkpoint_file_count=2,
                best_checkpoint_file_count=2,
                checkpoint_file_save_callback=save_params,
                save_interval=args.save_interval,
                num_epochs=args.num_epochs,
                param_names=['Val.Top1', 'Train.Top1', 'Val.Top5', 'Train.Loss'],
                acc_ind=2,
                # bigger=[True],
                # mask=None,
                score_log_file_path=os.path.join(args.save_dir, 'score.log'),
                score_log_attempt_value=1,
                best_map_log_file_path=os.path.join(args.save_dir, 'best_map.log'))
        else:
            lp_saver = None

        train_net(
            batch_size=batch_size,
            num_epochs=args.num_epochs,
            start_epoch1=args.start_epoch,
            train_data=train_data,
            val_data=val_data,
            net=net,
            optimizer=optimizer,
            lr_scheduler=lr_scheduler,
            lp_saver=lp_saver,
            log_interval=args.log_interval,
            use_cuda=use_cuda)


if __name__ == '__main__':
    main()

