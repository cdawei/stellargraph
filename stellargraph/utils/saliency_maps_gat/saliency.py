# -*- coding: utf-8 -*-
#
# Copyright 2018 Data61, CSIRO
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import numpy as np
import keras.backend as K
import scipy.sparse as sp
from stellargraph.mapper.node_mappers import FullBatchNodeSequence


class GradientSaliency(object):
    """
    Class to compute the saliency maps based on the vanilla gradient w.r.t the adjacency and the feature matrix.


    Args:
        model (Keras model object): The differentiable graph model object.
            model.input should contain two tensors:
                - features (Numpy array): The placeholder of the feature matrix.
                - adj (Numpy array): The placeholder of the adjacency matrix.
            model.output (Keras tensor): The tensor of model prediction output.
                This is typically the logit or softmax output.
    """

    def __init__(self, model, generator):
        # The placeholders for features and adjacency matrix (model input):
        if not isinstance(generator, FullBatchNodeSequence):
            raise TypeError(
                "The generator supplied has to be an object of FullBatchNodeSequence."
            )
        self.model = model
        # Collect variables for IG
        self.deltas = []
        self.non_exist_edges = []
        for var in model.non_trainable_weights:
            if "ig_delta" in var.name:
                self.deltas.append(var)
            if "ig_non_exist_edge" in var.name:
                self.non_exist_edges.append(var)

        features_t, output_indices_t, adj_t = model.input
        # Placeholder for class prediction (model output):
        output = self.model.output
        self.A = generator.A_dense
        self.X = generator.features

        # The placeholder for the node index of interest. It is typically the index of the target test node.
        self.node_idx = K.placeholder(shape=(), dtype="int32")

        # The placeholder for the class of interest. One will generally use the winning class.
        self.class_of_interest = K.placeholder(shape=(), dtype="int32")

        # The input tensors for computing the node saliency map
        node_mask_tensors = model.input + [
            K.learning_phase(),  # placeholder for mode (train or test) tense
            self.class_of_interest,
        ]

        # The input tensors for computing the link saliency map
        link_mask_tensors = model.input + [K.learning_phase(), self.class_of_interest]

        # node gradients are the gradients of the output's component corresponding to the
        # class of interest, w.r.t. input features of all nodes in the graph
        self.node_gradients = model.optimizer.get_gradients(
            K.gather(output[0, 0], self.class_of_interest), features_t
        )
        self.is_sparse = K.is_sparse(adj_t)
        # link gradients are the gradients of the output's component corresponding to the
        # class of interest, w.r.t. all elements of the adjacency matrix
        if self.is_sparse:
            print("adjacency matrix tensor is sparse")
            self.link_gradients = model.optimizer.get_gradients(
                K.gather(K.gather(output, self.node_idx), self.class_of_interest),
                adj_t.values,
            )

        else:
            self.link_gradients = model.optimizer.get_gradients(
                K.gather(output[0, 0], self.class_of_interest), adj_t
            )

        self.compute_link_gradients = K.function(
            inputs=link_mask_tensors, outputs=self.link_gradients
        )
        self.compute_node_gradients = K.function(
            inputs=node_mask_tensors, outputs=self.node_gradients
        )

    def set_ig_values(self, delta_value, edge_value):
        """
        Set values of the integrated gradient parameters in all layers of the model.

        Args:
            delta_value: Value of the `delta` parameter
            edge_value: Value of the `non_exist_edges` parameter
        """
        for delta_var in self.deltas:
            K.set_value(delta_var, delta_value)
        for edge_var in self.non_exist_edges:
            K.set_value(edge_var, edge_value)

    def get_node_masks(self, node_idx, class_of_interest, X_val=None, A_val=None):
        """
        Args:
            X_val, A_val, node_idx, class_of_interest: The values to feed while computing the gradients.
        Returns:
            gradients (Numpy array): Returns a vanilla gradient mask for the nodes.
        """
        out_indices = np.array([[node_idx]])

        if X_val is None:
            X_val = self.X
        if A_val is None:
            A_val = self.A
        # Execute the function to compute the gradient
        self.set_ig_values(1.0, 0.0)
        if self.is_sparse and not sp.issparse(A_val):
            A_val = sp.lil_matrix(A_val)
        gradients = self.compute_node_gradients(
            [X_val, out_indices, A_val, 0, class_of_interest]
        )
        return gradients[0]

    def get_link_masks(
        self, alpha, node_idx, class_of_interest, non_exist_edge, X_val=None, A_val=None
    ):
        """
        Args:
            X_val, A_val, node_idx, class_of_interest: The values to feed while computing the gradients.
        Returns:
            gradients (Numpy array): Returns a vanilla gradient mask for the nodes.
        """
        out_indices = np.array([[node_idx]])

        if X_val is None:
            X_val = self.X
        if A_val is None:
            A_val = self.A
        # Execute the function to compute the gradient
        self.set_ig_values(alpha, non_exist_edge)
        if self.is_sparse and not sp.issparse(A_val):
            A_val = sp.lil_matrix(A_val)
        gradients = self.compute_link_gradients(
            [X_val, out_indices, A_val, 0, class_of_interest]
        )
        return gradients[0]

    def get_node_importance(
        self, alpha, node_idx, class_of_interest, X_val=None, A_val=None
    ):
        """
        For nodes, the saliency mask we get gives us the importance of each features. For visualization purpose, we may
        want to see a summary of the importance for the node. The importance of each node can be defined as the sum of
        all the partial gradients w.r.t its features.

        Args:
            X_val, A_val, node_idx, class_of_interest: The values to feed while computing the gradients.
        Returns:
            (Numpy array): Each element indicates the importance of a node.
        """
        if X_val is None:
            X_val = self.X
        if A_val is None:
            A_val = self.A
        gradients = self.get_node_masks(X_val, A_val, node_idx, class_of_interest)[0]
        return np.sum(gradients, axis=1)