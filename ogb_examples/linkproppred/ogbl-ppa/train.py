#   Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""listwise model
"""

import torch
import os
import re
import time
import logging
from random import random
from functools import reduce, partial

# For downloading ogb
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
# SSL

import numpy as np
import multiprocessing

import pgl
import paddle
import paddle.fluid as F
import paddle.fluid.layers as L

from args import parser
from utils.args import print_arguments, check_cuda
from utils.init import init_checkpoint, init_pretraining_params
from model import BaseGraph
from dataloader.ogbl_ppa_dataloader import PPADataGenerator
from monitor.train_monitor import train_and_evaluate, OgbEvaluator

log = logging.getLogger(__name__)


class Metric(object):
    """Metric"""

    def __init__(self, **args):
        self.args = args

    @property
    def vars(self):
        """ fetch metric vars"""
        values = [self.args[k] for k in self.args.keys()]
        return values

    def parse(self, fetch_list):
        """parse"""
        tup = list(zip(self.args.keys(), [float(v[0]) for v in fetch_list]))
        return dict(tup)


if __name__ == '__main__':
    args = parser.parse_args()
    print_arguments(args)
    evaluator = OgbEvaluator()

    train_prog = F.Program()
    startup_prog = F.Program()
    args.num_nodes = evaluator.num_nodes

    if args.use_cuda:
        dev_list = F.cuda_places()
        place = dev_list[0]
        dev_count = len(dev_list)
    else:
        place = F.CPUPlace()
        dev_count = int(os.environ.get('CPU_NUM', multiprocessing.cpu_count()))

    with F.program_guard(train_prog, startup_prog):
        with F.unique_name.guard():
            graph_model = BaseGraph(args)
            test_prog = train_prog.clone(for_test=True)
            opt = F.optimizer.Adam(learning_rate=args.learning_rate)
            opt.minimize(graph_model.loss)

    #test_prog = F.Program()
    #with F.program_guard(test_prog, startup_prog):
    #    with F.unique_name.guard():
    #        _graph_model = BaseGraph(args)

    train_ds = PPADataGenerator(
        phase="train",
        graph_wrapper=graph_model.graph_wrapper,
        num_workers=args.num_workers,
        batch_size=args.batch_size)

    valid_ds = PPADataGenerator(
        phase="valid",
        graph_wrapper=graph_model.graph_wrapper,
        num_workers=args.num_workers,
        batch_size=args.batch_size)

    test_ds = PPADataGenerator(
        phase="test",
        graph_wrapper=graph_model.graph_wrapper,
        num_workers=args.num_workers,
        batch_size=args.batch_size)

    exe = F.Executor(place)
    exe.run(startup_prog)

    if args.init_pretraining_params is not None:
        init_pretraining_params(
            exe, args.init_pretraining_params, main_program=startup_prog)

    metric = Metric(**graph_model.metrics)

    nccl2_num_trainers = 1
    nccl2_trainer_id = 0
    if dev_count > 1:

        exec_strategy = F.ExecutionStrategy()
        exec_strategy.num_threads = dev_count

        train_exe = F.ParallelExecutor(
            use_cuda=args.use_cuda,
            loss_name=graph_model.loss.name,
            exec_strategy=exec_strategy,
            main_program=train_prog,
            num_trainers=nccl2_num_trainers,
            trainer_id=nccl2_trainer_id)

        test_exe = exe
    else:
        train_exe, test_exe = exe, exe

    train_and_evaluate(
        exe=exe,
        train_exe=train_exe,
        valid_exe=test_exe,
        train_ds=train_ds,
        valid_ds=valid_ds,
        test_ds=test_ds,
        train_prog=train_prog,
        valid_prog=test_prog,
        train_log_step=5,
        output_path=args.output_path,
        dev_count=dev_count,
        model=graph_model,
        epoch=args.epoch,
        eval_step=1000000,
        evaluator=evaluator,
        metric=metric)
