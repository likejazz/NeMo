# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
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

from contextlib import nullcontext
from typing import Union

import torch
import torch.nn.functional as F

from nemo.collections.asr.parts.k2.graph_transducer import GraphRnntLoss, force_float32_context
from nemo.core.utils.k2_guard import k2
from nemo.utils.enum import PrettyStrEnum


class GraphWTransducerLoss(GraphRnntLoss):
    """
    W-Transducer loss: RNN-T loss modification for training RNN-T model for the case
    when some text at the beginning/end of the utterance is missing.
    The resulting model behaves like the RNN-T model (no modification for decoding is required).
    For details see "Powerful and Extensible WFST Framework for RNN-Transducer Losses" paper
        https://ieeexplore.ieee.org/document/10096679
    """

    class LastBlankMode(PrettyStrEnum):
        ALLOW_IGNORE = "allow_ignore"
        FORCE_LAST = "force_last"

    def __init__(
        self,
        blank: int,
        eps_weight: float = 0.0,
        last_blank_mode: Union[LastBlankMode, str] = LastBlankMode.FORCE_LAST,
        use_grid_implementation=True,
        connect_composed=False,
        double_scores=False,
        cast_to_float32=False,
    ):
        """
        Init method

        Args:
            blank: blank label index
            eps_weight: weight of epsilon transitions, 0 means no penalty (default)
            last_blank_mode: allow to skip last blank in the prediction (default) or force it
            use_grid_implementation: Whether to use the grid implementation (Grid-Transducer).
            connect_composed: Connect graph after composing unit and temporal schemes
                (only for Compose-Transducer). `connect` operation is slow, it is useful for visualization,
                but not necessary for loss computation.
            double_scores: Use calculation of loss in double precision (float64) in the lattice.
                Does not significantly affect memory usage since the lattice is ~V/2 times smaller than the joint tensor.
            cast_to_float32: Force cast joint tensor to float32 before log-softmax calculation.
        """
        super().__init__(
            blank=blank,
            use_grid_implementation=use_grid_implementation,
            connect_composed=connect_composed,
            double_scores=double_scores,
            cast_to_float32=cast_to_float32,
        )
        self.eps_weight = eps_weight
        self.last_blank_mode = self.LastBlankMode(last_blank_mode)

    def get_unit_scheme(self, units_tensor: torch.Tensor, num_labels: int) -> "k2.Fsa":
        """
        Get unit scheme (target text) graph for W-Transducer loss (Compose-Transducer).
        Forward arcs represent text labels.

        Example graph: text [1, 2], blank=0. Eps ids: 3, 4.
        Labels: <text_labels>:<text_labels>:<unit_position>.

        graph::

                3:3:0                  0:0:1                  0:0:2
              +-------+              +-------+              +-------+
              v       |              v       |              v       |
            +-----------+  1:1:0   +-----------+  2:2:1   +-----------+  -1:-1:2   #===#
            |     0     | -------> |     1     | -------> |     2     | ---------> H 3 H
            +-----------+          +-----------+          +-----------+            #===#
              ^ 0:0:0 |                                     ^ 4:4:2 |
              +-------+                                     +-------+

        Args:
            units_tensor: 1d tensor with text units
            num_labels: number of total labels (vocab size including blank)

        Returns:
            k2 graph, ilabels=olabels (text labels + blank), unit_positions - text label indices
        """

        blank_id = self.blank
        start_eps_id = num_labels
        end_eps_id = num_labels + 1
        device = units_tensor.device
        text_len = units_tensor.shape[0]

        # arcs: scr, dest, label, score
        arcs = torch.zeros(((text_len + 1) * 2 + 2, 4), dtype=torch.int32, device=device)
        text_indices = torch.arange(0, text_len + 1, dtype=torch.int32, device=device)
        # eps
        arcs[0, 2] = start_eps_id
        # blank labels
        arcs[1:-1:2, 0] = text_indices  # from state
        arcs[1:-1:2, 1] = text_indices  # to state
        arcs[1:-1:2, 2] = blank_id

        # text labels
        arcs[2:-1:2, 0] = text_indices  # from state
        arcs[2:-1:2, 1] = text_indices + 1  # to state
        arcs[2:-2:2, 2] = units_tensor  # labels: text

        arcs[-1] = arcs[-2]
        arcs[-2, 1] = text_len
        arcs[-2, 2] = end_eps_id
        arcs[-1, 2] = -1  # last transition to final state, ilabel=-1 (special for k2)
        olabels = arcs[:, 2].detach().clone()  # same as ilabels

        fsa_text = k2.Fsa(arcs, olabels)
        fsa_text.unit_positions = torch.zeros_like(olabels)
        fsa_text.unit_positions[1:-1] = text_indices.expand(2, -1).transpose(0, 1).flatten()
        fsa_text.unit_positions[-1] = fsa_text.unit_positions[-2]
        return fsa_text

    def get_temporal_scheme(self, sequence_length: int, num_labels: int, device: torch.device) -> "k2.Fsa":
        """
        Get temporal scheme graph for W-Transducer loss (Compose-Transducer).

        Example graph: blank=0, sequence_length=3, num_labels=3, last_blank_mode="force_last".
        Labels: <labels_id>:<sequence_position>. Eps ids: 3, 4.§

        graph::

                                                         4:0
                       +--------------------------------------------+
                       |                               4:1          |
                       |                     +--------------------+ |
                1:0    |              1:1    |              1:2   | |
              +-----+  |            +-----+  |            +-----+ | |
              v     |  |            v     |  |            v     | v v
            +--------------+  0:0  +------------+  0:1   +------------+  0:2   +---+  -1:3    #===#
            |    0         | ----> |    1       | -----> |    2       | -----> | 3 | -------> H 4 H
            +--------------+       +------------+        +------------+        +---+          #===#
              ^ 2:0 | | | |         ^ 2:1 |  ^            ^ 2:2 |  ^             ^
              +-----+ | | |         +-----+  |            +-----+  |             |
                      | | |     3:0          |                     |             |
                      | | +------------------+     3:0             |             |
                      | +------------------------------------------+    3:0      |
                      +----------------------------------------------------------+

        Args:
            sequence_length: length of the sequence (in frames)
            num_labels: number of labels (including blank)
            device: device for tensor to construct

        Returns:
            k2 graph, ilabels/olabels – text labels (+ blank)/sequence_position
        """
        blank_id = self.blank
        start_eps_id = num_labels
        end_eps_id = num_labels + 1
        num_eps = 2

        num_sequence_arcs = sequence_length * (num_labels + num_eps)
        fsa_temporal_arcs = torch.zeros((num_sequence_arcs, 4), dtype=torch.int32, device=device)
        sequence_states = torch.arange(0, sequence_length, dtype=torch.int32, device=device)
        sequence_states_next = sequence_states + 1
        # for every state - num_labels+1 arcs, [0, 1, ..., num_labels-1, eps, 0, 1, ..., num_labels-1, eps, ...]
        start_states = sequence_states.expand(num_labels + num_eps, sequence_length).transpose(0, 1).flatten()

        # self-loops - all, make forward arcs later
        fsa_temporal_arcs[:num_sequence_arcs, 0] = start_states  # from
        fsa_temporal_arcs[:num_sequence_arcs, 1] = start_states  # to
        fsa_temporal_arcs[:num_sequence_arcs, 2] = (
            torch.arange(0, num_labels + num_eps, dtype=torch.int32, device=device)
            .expand(sequence_length, num_labels + num_eps)
            .flatten()
        )
        # forward arcs
        fsa_temporal_arcs[blank_id : num_sequence_arcs : num_labels + num_eps, 1] = sequence_states_next  # blanks
        # eps arcs
        fsa_temporal_arcs[start_eps_id : num_sequence_arcs : num_labels + num_eps, 0] = 0
        fsa_temporal_arcs[start_eps_id : num_sequence_arcs : num_labels + num_eps, 1] = sequence_states + 1
        fsa_temporal_arcs[end_eps_id : num_sequence_arcs : num_labels + num_eps, 0] = sequence_states
        fsa_temporal_arcs[end_eps_id : num_sequence_arcs : num_labels + num_eps, 1] = (
            sequence_length - 1 if self.last_blank_mode == self.LastBlankMode.FORCE_LAST else sequence_length
        )

        # transition to last final state
        fsa_temporal_arcs[-1, :3] = torch.tensor(
            (sequence_length, sequence_length + 1, -1), dtype=torch.int32, device=device
        )

        # need to sort arcs
        _, indices = torch.sort(fsa_temporal_arcs[:, 0], dim=0)
        fsa_temporal_arcs = fsa_temporal_arcs[indices]

        # output symbols: position in the sequence, same as start states for arcs
        olabels = fsa_temporal_arcs[:, 0].detach().clone()

        fsa_temporal = k2.Fsa(fsa_temporal_arcs, olabels)
        fsa_temporal = k2.arc_sort(fsa_temporal)  # need for compose
        return fsa_temporal

    def get_grid(self, units_tensor: torch.Tensor, sequence_length: int, num_labels: int) -> "k2.Fsa":
        """
        Construct W-Transducer lattice directly (Grid-Transducer).

        Args:
            units_tensor: 1d tensor with text units
            sequence_length: length of the sequence (number of frames)
            num_labels: number of total labels (vocab size including blank)

        Returns:
            transducer lattice (k2.Fsa)
        """
        blank_id = self.blank
        eps_id = num_labels  # beyond vocabulary
        text_length = units_tensor.shape[0]
        device = units_tensor.device
        num_grid_states = sequence_length * (text_length + 1)
        num_forward_arcs_base = (sequence_length - 1) * (text_length + 1)
        num_forward_arcs_additional = (sequence_length - 1) * 2
        num_forward_arcs = num_forward_arcs_base + num_forward_arcs_additional
        num_text_arcs = text_length * sequence_length
        arcs = torch.zeros((num_forward_arcs + num_text_arcs + 2, 4), dtype=torch.int32, device=device)
        # blank transitions
        # i, i+<text_len + 1>, 0 <blank>, i / <text_len+1>, i % <text_len + 1>
        from_states = torch.arange(num_forward_arcs_base, device=device)
        to_states = from_states + (text_length + 1)
        arcs[:num_forward_arcs_base, 0] = from_states
        arcs[:num_forward_arcs_base, 1] = to_states
        arcs[:num_forward_arcs_base, 2] = blank_id

        from_states = torch.cat(
            [
                torch.arange(sequence_length - 1, device=device) * (text_length + 1),
                text_length + torch.arange(sequence_length - 1, device=device) * (text_length + 1),
            ]
        )
        to_states = from_states + (text_length + 1)
        arcs[num_forward_arcs_base : num_forward_arcs_base + (sequence_length - 1) * 2, 0] = from_states
        arcs[num_forward_arcs_base : num_forward_arcs_base + (sequence_length - 1) * 2, 1] = to_states
        arcs[num_forward_arcs_base : num_forward_arcs_base + (sequence_length - 1), 2] = eps_id
        arcs[num_forward_arcs_base + (sequence_length - 1) : num_forward_arcs_base + (sequence_length - 1) * 2, 2] = (
            eps_id + 1
        )

        arcs[num_forward_arcs_base : num_forward_arcs_base + (sequence_length - 1), 0] = 0
        arcs[num_forward_arcs_base + (sequence_length - 1) : num_forward_arcs_base + (sequence_length - 1) * 2, 1] = (
            num_grid_states - 1
        )  # if other mode - fix later
        # last eps ark - after relabel

        # text arcs
        from_states = (
            torch.arange(num_grid_states, dtype=torch.int32, device=device)
            .reshape(sequence_length, text_length + 1)[:, :-1]
            .flatten()
        )
        to_states = from_states + 1
        ilabels = units_tensor.expand(sequence_length, -1).flatten()
        arcs[num_forward_arcs:-2, 0] = from_states
        arcs[num_forward_arcs:-2, 1] = to_states
        arcs[num_forward_arcs:-2, 2] = ilabels

        # last 2 states
        arcs[-2, :3] = torch.tensor((num_grid_states - 1, num_grid_states, blank_id), dtype=torch.int32, device=device)
        arcs[-1, :3] = torch.tensor((num_grid_states, num_grid_states + 1, -1), dtype=torch.int32, device=device)

        # sequence indices, time indices
        olabels = torch.div(arcs[:, 0], (text_length + 1), rounding_mode="floor")  # arcs[:, 0] // (text_length + 1)
        unit_positions = arcs[:, 0] % (text_length + 1)
        # last state: final
        olabels[-1] = -1
        unit_positions[-1] = -1

        # relabel
        # instead of using top sort (extremely expensive) k2.top_sort(rnnt_graph)
        arcs[:-2, 0] = self.relabel_states(arcs[:-2, 0], text_length + 1, sequence_length)
        arcs[:-3, 1] = self.relabel_states(arcs[:-3, 1], text_length + 1, sequence_length)

        if self.last_blank_mode == self.LastBlankMode.ALLOW_IGNORE:
            arcs[
                num_forward_arcs_base + (sequence_length - 1) : num_forward_arcs_base + (sequence_length - 1) * 2, 1
            ] = num_grid_states

        # sort by start state - required in k2
        # TODO: maybe it is more optimal to avoid sort, construct arcs in ascending order
        _, indices = torch.sort(arcs[:, 0], dim=0)
        arcs = arcs[indices]
        olabels = olabels[indices]
        unit_positions = unit_positions[indices]

        rnnt_graph = k2.Fsa(arcs, olabels)
        rnnt_graph.unit_positions = unit_positions
        return rnnt_graph

    def forward(
        self, acts: torch.Tensor, labels: torch.Tensor, act_lens: torch.Tensor, label_lens: torch.Tensor,
    ):
        """
        Forward method is similar to RNN-T Graph-Transducer forward method,
        but we need to assign eps weight to eps-transitions.
        """
        # nemo: acts=log_probs, labels=targets, act_lens=input_lengths, label_lens=target_lengths
        logits, targets, logits_lengths, target_lengths = acts, labels, act_lens, label_lens

        # logits: B x Time x Text+1 x C
        num_labels = logits.shape[-1]
        target_fsas_vec = self.get_graphs_batched(logits_lengths, targets, target_lengths, num_labels)

        cast_context = force_float32_context() if self.cast_to_float32 else nullcontext()
        with cast_context:
            log_probs = F.log_softmax(logits, dim=-1)
            with torch.no_grad():
                indices = self.get_logits_indices(target_fsas_vec, logits.shape)
                indices[target_fsas_vec.labels == -1] = 0
                indices[target_fsas_vec.labels >= num_labels] = 0  # eps

            # NB: do not assign scores -> modify, k2 will not update all scores correctly (modify -> assign)
            scores = log_probs.flatten().index_select(-1, indices)
            scores[target_fsas_vec.labels == -1] = 0
            scores[target_fsas_vec.labels >= num_labels] = self.eps_weight  # eps

            target_fsas_vec.scores = scores
            scores = -1 * target_fsas_vec.get_tot_scores(use_double_scores=self.double_scores, log_semiring=True)
            return scores
