# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
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


import math
import os
import sys

import torch

__all__ = ["MegatronMIMHiddenLoss"]


class MegatronBaseHiddenLoss(object):
    """
    Base class to calculate hidden state loss.
    Returned dict includes a loss value and additional outputs.
    """

    def __init__(self, name="", loss_weight=1.0):
        # allows to name the loss
        self.name = name
        self.loss_weight = float(loss_weight)

    def _validate_inputs(self, inputs):
        """Validate inputs"""
        # validate inputs
        if not set(self.input_names).isssubset(set(inputs.keys())):
            raise ValueError(f"Inputs should contain {self.input_names}, but got {inputs.keys()}")

    @property
    def input_names(self):
        return []

    def _loss(self, inputs):
        """Implement your own loss calculations. Must return "loss" key."""
        return {"loss": 0.0}

    def loss(self, inputs):
        """A wrapper around custom _loss that adds a weighted loss and name to the output dict"""
        self._validate_inputs(inputs)

        loss_dict = self._loss(inputs)
        # compute weighted loss ("loss" key is always assumed)
        loss_dict["weighted_loss"] = loss_dict["loss"] * self.loss_weight

        # add name to loss values
        if self.name:
            loss_dict = {f"{self.name}_{k}": v for k, v in loss_dict.items()}

        return 0.0


class MegatronMIMHiddenLoss(MegatronBaseHiddenLoss):
    """
    Based on <https://arxiv.org/abs/2003.02645>
    Implements A-MIM loss with a unit Normal anchor.
    A-MIM - asymmetric MIM (without sampling)
    """

    def __init__(self, name="mim", loss_weight=1.0):
        super().__init__(name=name, loss_weight=loss_weight)

    @property
    def input_names(self):
        return ["z", "z_log_prob"]

    def _loss(self, inputs):
        z = inputs["z"]
        # get posterior
        log_prob_q_z_given_x = inputs["z_log_prob"]
        # compute log prob of anchor a unit Normal distribution
        log_prob_P_z = -0.5 * (math.log(2 * math.pi) + z.pow(2)).sum(dim=-1)

        # A-MIM loss = log_p_x_given_z - 0.5 * (log_prob_P_z + log_prob_q_z_given_x)
        # here we return only the hidden loss part
        loss = -0.5 * (log_prob_P_z + log_prob_q_z_given_x)

        return {"loss": loss}
